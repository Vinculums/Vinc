# -*- coding: utf-8 -*-
"""검색·되부르기(recall)·RRF 융합·청크/노드/그래프 팔 — RAG 팀의 검색 코어.

`api/query.py` 에서 원문 그대로 옮겼다(VIN-243). 동작은 바꾸지 않았다. 바닥 보조 함수는
`store.querybase` 에서, 저장소 접근은 `store` 에서 가져온다. `api`·`vinc_mcp` 를 import
하지 않는다 — `scripts/gate_ownership.py` 의 [LAYER] 가 막는다. `api/query.py` 가 같은
이름을 다시 내보내므로 `q.search` 같은 기존 호출은 그대로다.
근거: [[vinc.plan.team-split]]
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from store.config import load_config
from store.backend import open_store
from store import pkns
from store import search_temporal as temporal
from store.embedded_backend import vector_generation
from store.querybase import (
    QueryPlan,
    _VALIDITY_EMPTY,
    _classify_chunk_error,
    _props_to_obj,
    _validity_cols,
    domain_subtree_where,
    execute,
    holds_during,
    is_conventions_chunk_id,
    normalize_query_tokens,
    resolve_as_of,
    validity_state,
)


# Default preference weights for rank="preference". Tunable per call.
PREF_WEIGHTS = {"w_pin": 3.0, "w_freq": 1.0, "w_tag": 2.0, "freq_k": 5.0}


def score_preference(rows: list[dict], *, weights: dict | None = None,
                     tags: list[str] | None = None, limit: int = 10) -> list[dict]:
    """소유자 선호 신호로 recall 후보를 랭크하는 **순수 함수**(백엔드 무의존, §4-2).

    점수 = w_pin·pin + w_freq·(access_count 포화항) + w_tag·(tags 교집합 수).
    각 행에 ``score`` 를 부여하고 점수 내림차순으로 상한 ``limit`` 까지 반환한다.
    입력 ``rows`` 는 recency(date DESC) 정렬 상태를 전제 — 파이썬 정렬이 안정적이라
    동점은 recency tie-break 로 유지된다."""
    w = {**PREF_WEIGHTS, **(weights or {})}
    tagset = set(tags or [])

    def _score(r: dict) -> float:
        pin = 1.0 if r.get("pinned") else 0.0
        ac = float(r.get("access_count") or 0)
        freq = ac / (ac + w["freq_k"]) if (ac + w["freq_k"]) else 0.0
        overlap = float(len(tagset & set(r.get("tags") or []))) if tagset else 0.0
        return w["w_pin"] * pin + w["w_freq"] * freq + w["w_tag"] * overlap

    scored = sorted(rows, key=_score, reverse=True)  # stable → recency tie-break 유지
    for r in scored:
        r["score"] = _score(r)
    return scored[:limit]


#: recall 의 정렬 키. **날짜가 있는 행이 먼저, 없는 행은 끝**이고 같은 날짜 안에서는
#: id 오름차순이다. 임베디드 엔진은 `ORDER BY e.date DESC` 에서 NULL 을 가장 큰 값으로
#: 두어서, 날짜 없는 에피소드가 "가장 최근"으로 맨 위에 섰다. 엔진이 `NULLS LAST` 를
#: 파싱하지 못해(실측, ladybug 0.18.0 · 0.20.4) 표현식 키로 뒤로 민다. 빈 문자열도 날짜가 아니다.
#: id 키는 커서 페이징이 오프셋을 쓸 수 있게 순서를 **완전히** 고정한다.
#: `score_preference` 는 이 순서를 동점 처리(recency tie-break)로 그대로 물려받는다.
#: 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S2.
RECALL_ORDER = ("ORDER BY (CASE WHEN e.date IS NULL OR e.date = '' THEN 1 ELSE 0 END), "
                "e.date DESC, e.episode_id")


def recall_plan(query: str = "", *, owner: str | None = None, tenant: str | None = None,
                domain: str | None = None, workspace: str | None = None,
                tags: list[str] | None = None, rank: str = "recency", limit: int = 10,
                weights: dict | None = None, offset: int = 0) -> QueryPlan:
    """Recall an individual's *learning* episodes, scoped by ownership and domain,
    ordered by recency or by owner-preference alignment.

    The memory is one person's cross-domain learning. ``owner`` is the primary
    (individual) scope — "my memory". ``domain`` is a **free, user-defined** string
    (physics, math, art, economics, finance, …) — never a fixed list; pass whatever
    the learner actually used, or omit to recall across all their domains. ``tenant``
    is the governance boundary; ``workspace`` narrows further. Each filter is
    optional and ANDed. Text filter matches title/summary.

    ``domain`` 은 **하위 트리**다: `vinc/agent` 는 `vinc/agent/*` 의 에피소드까지 담는다
    (:func:`domain_subtree_where`, 공유 팩과 같은 술어).

    ``rank``:
      - ``"recency"`` (default): dated rows newest first, undated rows last
        (:data:`RECALL_ORDER`). ``tags`` is a **filter** here: an episode is kept
        when it carries at least one of them.
      - ``"preference"``: score each Episode by the owner's signals — pin (strong),
        access frequency (saturating), and tag overlap with ``tags`` — then order
        by that score, recency as tie-break ("내 선호에 맞춤"). Here ``tags`` stays a
        **boost**, not a filter: 컨텍스트 팩(`api/activate.py`)이 이 모드로 선호를
        앞세워 내보내는데, 걸러 버리면 팩에서 에피소드가 빠진다."""
    where = []
    params: dict = {"limit": int(limit)}
    if owner:
        where.append("e.owner_id = $owner")
        params["owner"] = owner
    if domain:
        # 하위 트리 — 정확일치만 보면 `vinc/agent` 가 자식 주제의 에피소드를 0건으로
        # 답했다. 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S1.
        where.append(domain_subtree_where(["e.domain"], [domain], params))
    if tags and rank != "preference":
        # 태그 필터(하나라도 겹치면 통과). 여태 tags 는 preference 점수에만 쓰여서
        # recency 에서는 **조용히 무시**됐다 — 없는 태그를 줘도 전량이 나왔다.
        # ⚠ 엔진이 람다·`any()` 안의 파라미터를 못 푼다(실측: `any(t IN … WHERE t IN
        # $tags)` 가 파서 단언으로 죽는다). 그래서 태그마다 list_contains 를 OR 로 편다.
        ors = []
        for i, t in enumerate(dict.fromkeys(tags)):
            params[f"tag{i}"] = t
            ors.append(f"list_contains(coalesce(e.tags, []), $tag{i})")
        where.append("(" + " OR ".join(ors) + ")")
    if tenant:
        where.append("e.tenant_id = $tenant")
        params["tenant"] = tenant
    if workspace:
        where.append("e.workspace = $workspace")
        params["workspace"] = workspace
    if query:
        where.append("(toLower(e.title) CONTAINS toLower($q) OR toLower(e.summary) CONTAINS toLower($q))")
        params["q"] = query
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    # 투영에 preference 신호(pinned/tags/access_count/last_access)를 항상 포함한다 —
    # 이 신호는 런타임 축적분으로 원문 재수집으로 복원 불가하며 pack.json 이 유일한
    # 무손실 이관 통로다(설계 §10·D4). recency·preference 공통.
    projection = ("e.episode_id AS id, e.title AS title, e.date AS date, "
                  "e.owner_id AS owner, e.domain AS domain, e.tenant_id AS tenant, "
                  "e.workspace AS workspace, e.summary AS summary, "
                  "coalesce(e.pinned, false) AS pinned, coalesce(e.tags, []) AS tags, "
                  "coalesce(e.access_count, 0) AS access_count, e.last_access AS last_access")

    if rank == "preference":
        # 선호 점수는 **백엔드 밖 순수 함수**(:func:`score_preference`)로 매긴다 —
        # RRF 융합과 동형(§4-2). 임베디드(Kùzu 계보)는 toFloat·리스트 컴프리헨션·
        # 인덱스 스코어링 방언이 Neo4j 와 달라(§6), Cypher 내 점수식은 이식성이 낮다.
        # 스코프 필터된 후보 전량을 신호와 함께 가져와(단일 사용자 규모 — §12 R5)
        # 인-프로세스로 랭크·상한한다.
        # ⚠ kuzu 는 **미사용 파라미터를 거부**한다("Parameter … not found", §6 방언).
        # preference 플랜은 Cypher 에 $limit 를 쓰지 않으므로(파이썬에서 상한) 제거.
        params.pop("limit", None)
        cypher = (
            f"MATCH (e:Episode) {clause} "
            f"RETURN {projection} "
            f"{RECALL_ORDER}"             # 안정 정렬 기반(동점 시 recency tie-break)
        )
        return QueryPlan("recall", cypher, params,
                         note="episodic recall (preference — scored in-process)")

    skip = ""
    if offset and int(offset) > 0:
        # 커서 페이지(opt-in). 오프셋 0 이면 SKIP 이 붙지 않는다 — 커서를 안 쓰는
        # 호출자(activate·CLI·/plan/recall)의 플랜 모양은 그대로다.
        skip = "SKIP $skip "
        params["skip"] = int(offset)
    cypher = (
        f"MATCH (e:Episode) {clause} "
        f"RETURN {projection} "
        f"{RECALL_ORDER} {skip}LIMIT $limit"
    )
    return QueryPlan("recall", cypher, params, note="3-tier episodic memory (owner-scoped)")


def rrf_fuse(ranked_lists: list[list[dict]], *, k: int = 60,
             top_k: int | None = None, id_key: str = "id") -> list[dict]:
    """Reciprocal Rank Fusion — 하이브리드 검색의 순수 융합 함수(§4-2, 백엔드 밖).

    각 순위 리스트에서 항목의 기여 = 1/(k + rank). 여러 암(vector·BM25)의 기여를
    합산해 재정렬한다. 학습·점수 보정 불요(k≈60 기본). 각 행에 ``rrf_score`` 부여."""
    scores: dict = {}
    rows_by_id: dict = {}
    for rows in ranked_lists:
        for rank, row in enumerate(rows):
            rid = row.get(id_key)
            if rid is None:
                continue
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank + 1)
            rows_by_id.setdefault(rid, row)
    fused = sorted(rows_by_id.values(), key=lambda r: scores[r[id_key]], reverse=True)
    for r in fused:
        r["rrf_score"] = scores[r[id_key]]
    return fused[:top_k] if top_k else fused


#: 페이지네이션 후보 풀 상한(암별 LIMIT 이자 total_count 의 상한). 200을 넘는
#: 순회는 검색이 아니라 열람이다 — 그쪽은 vinc_doc_list/vinc_recall 이 담당한다.
SEARCH_POOL_CAP = 200


#: 청크 하이브리드 융합의 RRF 상수. `rrf_fuse` 의 기본은 60 이고 **여태 아무도 안
#: 넘겼다** — 즉 60 은 한 번도 측정된 적 없는 값이었다. 176문서 split 스토어에서
#: k 를 10·20·60·100 으로 쓸어 보니 작은 쪽이 단조로 낫다:
#:
#:     질의 셋        k=10              k=20              k=100
#:     캐논 118      +0.0169(0 포함)    +0.0109 [+0.001,+0.021]  -0.0071
#:     사람 75       +0.0931 [+0.048,+0.147]  +0.0613 [+0.035,+0.093]  -0.0128 [-0.027,-0.002]
#:
#: k=10 이 더 크지만 캐논 셋과 사람 셋의 한국어 축에서 구간이 0을 스친다. **두 셋
#: 모두에서 유의하고 어느 축도 나빠지지 않는 값은 20 하나다** — 한쪽에서 최고인 값이
#: 아니라 양쪽에서 버티는 값을 택한다.
#: 근거: [[record:rrf-k-and-weight-sweep-on-full176-2026-09-08]] ·
#: [[record:rrf-k-20-reproduces-on-the-human-control-2026-09-08]]
#:
#: ⚠ 노드 융합(`title_hits`+`graph_hits`)에는 **적용하지 않는다.** 그 팔들은 재 본
#: 적이 없고, 같은 상수를 쓴다고 같은 최적값을 갖지 않는다.
CHUNK_RRF_K = 20


def _query_fingerprint(query: str, owner: str | None, filters: dict | None,
                       detail: str, as_of: str | None = None, *, mode=None,
                       historical=False, known_at=None) -> str:
    basis = [query, owner or "", sorted((filters or {}).items()), detail]
    # 시점이 다른 요청의 커서로 이어 읽으면 두 시점의 결과가 한 목록에 섞인다. 기본값
    # (as_of 없음)에서는 지문이 예전과 같게 둔다 — 이미 나간 커서가 무효가 되지 않게.
    if as_of:
        basis.append("as_of:" + as_of.strip())
    if mode:
        basis.append("mode:" + mode)
        if mode == "current":
            basis.append("current_at:" + temporal.dt.datetime.now(temporal.dt.timezone.utc).date().isoformat())
    if historical:
        basis.append("historical:true")
    if known_at:
        basis.append("known_at:" + known_at.strip())
    basis = json.dumps(basis, ensure_ascii=False)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def cursor_encode(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def cursor_decode(s: str) -> dict:
    """무효 커서는 ValueError("invalid_cursor: …") — 호출부가 명시 오류로 표면화한다."""
    try:
        pad = "=" * (-len(s) % 4)
        data = json.loads(base64.urlsafe_b64decode((s + pad).encode("ascii")))
    except Exception:
        raise ValueError("invalid_cursor: not a vinc search cursor") from None
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ValueError("invalid_cursor: unknown cursor version")
    return data


#: 발췌 길이(문자). node_context 의 발췌 관례(§excerpts)와 같은 값이다.
SNIPPET_CHARS = 240


def _snippet_start(flat: str, query_tokens: set[str] | None) -> int:
    """발췌를 어디서 시작할까 — 질의 토큰이 **가장 여러 가지** 든 SNIPPET_CHARS 창의 시작.

    질의 토큰이 나오는 자리를 찾고(`find_tokens`), 그 자리마다 창을 열어 두 포인터로 서로 다른 토큰
    수를 센다(O(n)). 동률이면 앞의 것이다. 맞는 토큰이 없으면 0 — 오늘 발췌와 바이트까지 같다.
    시작은 앞 공백으로 물려 낱말을 자르지 않는다.

    왜: 청크 첫 240자는 한국어 방송 말뭉치에서 근거 구간을 18.6% 만 보여 줬다. 질의어 창은
    95.3%(그의 단어)·87.8%(검색어)·59.3%(바꾼 말). 에이전트는 발췌를 보고 열 문서를 고른다.
    근거: [[vinc.research.korean-retrieval-2026-10-01 §7]]"""
    if not query_tokens or len(flat) <= SNIPPET_CHARS:
        return 0
    from store.lexical import find_tokens
    hits = find_tokens(flat, query_tokens)
    if not hits:
        return 0
    best_n, best_start = 0, 0
    counts: dict[str, int] = {}
    j = 0
    for i, (start, _) in enumerate(hits):
        while j < len(hits) and hits[j][0] + len(hits[j][1]) <= start + SNIPPET_CHARS:
            counts[hits[j][1]] = counts.get(hits[j][1], 0) + 1
            j += 1
        n = sum(1 for c in counts.values() if c)
        if n > best_n:
            best_n, best_start = n, start
        tok = hits[i][1]
        if counts.get(tok):
            counts[tok] -= 1
    # 처음 창이 최고 창만큼 덮으면 처음을 보여 준다 — 오늘 발췌와 같고 `…` 도 안 붙는다.
    head_n = len({tok for pos, tok in hits if pos + len(tok) <= SNIPPET_CHARS})
    if best_n == 0 or best_start == 0 or head_n >= best_n:
        return 0
    # 시작이 낱말 한가운데면 앞 공백으로 물린다(최대 20자). 너무 멀면 그 자리 그대로.
    sp = flat.rfind(" ", max(0, best_start - 20), best_start)
    start = sp + 1 if sp >= 0 else best_start
    # 창 끝이 원문 끝을 넘지 않게 당긴다 — 마지막 문단에 모였으면 꽉 찬 창을 보여 준다.
    return max(0, min(start, len(flat) - SNIPPET_CHARS))


def _summary_row(r: dict, query_tokens: set[str] | None = None) -> dict:
    """청크 행의 요약형 — 전문(text) 대신 발췌(snippet)를 싣는다(GM-14 요약·상세 분리).
    발췌는 질의어가 가장 많이 든 자리다(:func:`_snippet_start`). 처음이 아니면 `…` 로 시작하고,
    맞는 게 없으면 처음 240자다. 공백 정규화는 node_context 의 기존 관례(§excerpts)와 같다.
    전문은 doc_id 로 vinc_doc_get 이 돌려준다(chunk id 규약 `{doc_id}#{n:05d}`)."""
    flat = " ".join(str(r.get("text") or "").split())
    out = {k: v for k, v in r.items() if k != "text"}
    start = _snippet_start(flat, query_tokens)
    out["snippet"] = ("…" if start else "") + flat[start:start + SNIPPET_CHARS]
    out["truncated"] = len(flat) > SNIPPET_CHARS
    rid = str(r.get("id") or "")
    out["doc_id"] = rid.rsplit("#", 1)[0] if "#" in rid else ""
    return out


def _attach_doc_titles(store, rows: list[dict], *, owner: str | None = None,
                       include_titles: bool = True) -> dict:
    """청크 행에 문서 제목(`doc_title`)을 단다 — 페이지의 doc_id 들을 한 번에 조회한다.

    행은 doc_id 만 들고 있어서, 제목이 필요한 소비자(허브 `search`)가 발췌 첫 줄을
    제목으로 썼다 — 그래서 "```mermaid …"·"--- doc_id: …" 가 제목으로 나갔다(감사 §5
    #19). node_context 의 `_DocMeta` 조인과 같은 모양이고, 비용은 코퍼스가 아니라
    페이지 크기에 묶인다. `_DocMeta.doc_id` 는 소유자로 네임스페이싱되지 않으므로
    owner 를 반드시 건다 — 같은 doc_id 를 가진 남의 문서 제목이 새면 안 된다.
    실패는 삼킨다: 제목은 부가 정보이고, 없으면 소비자가 doc_id 로 대신한다.

    v22 — 같은 조회에서 문서 유효구간도 읽어 `{doc_id: (valid_from, valid_until)}` 로
    돌려준다(:func:`_drop_retired_doc_rows`). 페이지당 조회는 여전히 한 번이다.
    근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S12"""
    ids = sorted({r.get("doc_id") for r in rows if r.get("doc_id")})
    if not ids:
        return {}
    where = "d.doc_id IN $ids" + (" AND d.owner_id = $owner" if owner else "")
    params: dict = {"ids": ids}
    if owner:
        params["owner"] = owner
    try:
        got = store.run(f"MATCH (d:_DocMeta) WHERE {where} "
                        "RETURN d.doc_id AS id, d.title AS t, "
                        "d.valid_from AS vf, d.valid_until AS vu, "
                        "d.created_at AS created_at, d.updated_at AS updated_at, "
                        "d.content_time AS content_time, d.superseded_by AS superseded_by", params) or []
    except Exception:
        raise
    meta = {str(g.get("id") or ""): g for g in got}
    for r in rows:
        g = meta.get(r.get("doc_id"), {})
        temporal.stamp(r, {**g, "valid_from": g.get("vf"), "valid_until": g.get("vu")})
    if include_titles:
        titles = {str(g.get("id") or ""): str(g.get("t") or "") for g in got}
        for r in rows:
            t = titles.get(r.get("doc_id") or "")
            if t:
                r["doc_title"] = t
    return {str(g.get("id") or ""): (str(g.get("vf") or ""), str(g.get("vu") or ""))
            for g in got}


def _drop_retired_doc_rows(base: dict, validity: dict, *, historical: bool, scope) -> None:
    """청크 행을 **문서의** 유효구간으로 거른다(v22). 기본은 은퇴한 문서를 감추고,
    `historical` 이면 그대로, `as_of` 기간이면 그 기간에 성립한 문서만 남긴다. 감춘 수는
    `validity_filtered` 에 더하고 `doc_validity_filtered` 로도 센다. 순위와 커서는 그대로라
    이 페이지가 `limit` 보다 짧아질 수 있다.
    근거: [[vinc.plan.temporal-edges-point-in-time-document-validity-2026-09-28]] §3"""
    if (historical and scope is None) or not validity:
        return
    kept, hidden = [], 0
    for r in base.get("rows") or []:
        vf, vu = validity.get(r.get("doc_id") or "", ("", ""))
        ok = (temporal.overlaps_scope(vf, vu, scope) if scope is not None
              else validity_state(vf, vu) != "historical")
        if ok:
            kept.append(r)
        else:
            hidden += 1
    if hidden:
        base["rows"] = kept
        base["validity_filtered"] = int(base.get("validity_filtered") or 0) + hidden
        base["doc_validity_filtered"] = hidden
        base.setdefault("validity_note", "hid chunks of documents whose validity ended; "
                                         "pass historical=true to include them")


def recall(query: str = "", *, owner: str | None = None, tenant: str | None = None,
           domain: str | None = None, workspace: str | None = None,
           tags: list[str] | None = None, rank: str = "recency", limit: int = 10,
           weights: dict | None = None, cfg=None, store=None,
           debug: bool = False, page: bool = False, cursor: str | None = None) -> dict:
    """에피소드 타임라인. ``page`` 나 ``cursor`` 를 주면 **커서 페이징**으로 답한다:
    응답에 ``has_more``·``next_cursor`` 가 실리고, ``cursor`` 는 그다음 페이지를 준다.
    둘 다 안 주면 응답 모양이 예전 그대로다(activate·CLI·웹 op 가 이 모양을 쓴다).

    커서는 검색 커서 코덱(:func:`cursor_encode`)을 쓴다: 오프셋 + 질의 지문(qh) + 기대
    id(eid). 다른 질의의 커서는 ``invalid_cursor``, 그새 타임라인이 바뀌었으면
    ``stale_cursor`` 로 **명시 거절**한다(조용히 첫 페이지로 돌아가지 않는다).
    근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S3."""
    if not (page or cursor):
        res = execute(
            recall_plan(query, owner=owner, tenant=tenant, domain=domain,
                        workspace=workspace, tags=tags, rank=rank, limit=limit, weights=weights),
            cfg, store=store, debug=debug,
        )
        # preference 랭킹은 백엔드 밖 순수 함수로(§4-2). 플랜은 후보 전량을 recency 순으로
        # 가져오고, 여기서 점수·상한을 적용한다.
        if rank == "preference" and res.get("executed"):
            res["rows"] = score_preference(res["rows"], weights=weights, tags=tags, limit=limit)
        return res

    limit = max(1, int(limit))
    qh = _recall_fingerprint(query, owner, domain=domain, tenant=tenant,
                             workspace=workspace, tags=tags, rank=rank)
    off, want_eid = 0, None
    if cursor:
        try:
            c = cursor_decode(cursor)
            if c.get("qh") != qh:
                raise ValueError(
                    "invalid_cursor: cursor was issued for a different recall query/scope")
            off = int(c.get("off") or 0)
            if off < 0:
                raise ValueError("invalid_cursor: negative offset")
            want_eid = c.get("eid")
        except (TypeError, ValueError) as exc:
            msg = str(exc) if str(exc).startswith("invalid_cursor") else f"invalid_cursor: {exc}"
            return _recall_refusal(msg)
    # 한 행을 더 읽어 다음 페이지가 있는지 안다(개수 질의를 따로 돌리지 않는다).
    if rank == "preference":
        plan = recall_plan(query, owner=owner, tenant=tenant, domain=domain,
                           workspace=workspace, tags=tags, rank=rank, weights=weights)
    else:
        plan = recall_plan(query, owner=owner, tenant=tenant, domain=domain,
                           workspace=workspace, tags=tags, rank=rank,
                           limit=limit + 1, offset=off)
    res = execute(plan, cfg, store=store, debug=debug)
    res.setdefault("has_more", False)
    res.setdefault("next_cursor", None)
    if not res.get("executed"):
        return res
    rows = res["rows"]
    if rank == "preference":
        rows = score_preference(rows, weights=weights, tags=tags,
                                limit=off + limit + 1)[off:]
    if want_eid is not None and (not rows or rows[0].get("id") != want_eid):
        return _recall_refusal("stale_cursor: the timeline changed since this cursor was "
                               "issued; start again without a cursor")
    has_more = len(rows) > limit
    res["rows"] = rows[:limit]
    res["has_more"] = has_more
    if has_more:
        res["next_cursor"] = cursor_encode(
            {"v": 1, "off": off + limit, "qh": qh, "eid": rows[limit].get("id")})
    return res


def _recall_fingerprint(query: str, owner: str | None, *, domain, tenant, workspace,
                        tags, rank) -> str:
    """recall 커서의 질의 지문. 검색 지문과 같은 함수를 쓰되 detail 자리에 `recall` 을
    넣는다 — 검색 커서가 recall 에서 통하지 않고 반대도 같다."""
    filters = {"domain": domain or "", "tenant": tenant or "", "workspace": workspace or "",
               "tags": ",".join(sorted(set(tags or []))), "rank": rank or "recency"}
    return _query_fingerprint(query or "", owner, filters, "recall")


def _recall_refusal(msg: str) -> dict:
    """무효·실효 커서의 답. 검색과 같이 최상위 ``error`` 로 말하고 행은 비운다."""
    return {"kind": "recall", "executed": False, "rows": [], "has_more": False,
            "next_cursor": None, "error": msg, "note": msg}


def _row_validity(r: dict) -> tuple[str, str]:
    """검색 행의 유효구간 — Record 는 열, Decision 은 props 의 임시 인코딩이다
    (`props.valid_from`/`props.valid_until`, 시간 정책 §8 항목 5). "읽기는 종류별로
    두 자리를 본다"는 그 조항이 이 함수다. 여기 없던 동안 71건에 채운 결정 구간은
    어떤 읽기도 보지 않았고, 폐기된 결정(d44)이 후속 결정과 동점으로 나왔다
    (라이브 재현 2026-09-11)."""
    if r.get("kind") == "decision":
        p = _props_to_obj(r.get("props_raw"))
        return str(p.get("valid_from") or ""), str(p.get("valid_until") or "")
    return str(r.get("valid_from") or ""), str(r.get("valid_until") or "")


def _visible(valid_from, valid_until, *, historical: bool,
             scope: tuple[str, str] | None) -> bool:
    """검색 두 팔(제목·의미)이 함께 쓰는 판정. 기간이 있으면 그 기간, 없으면 오늘 규칙."""
    if scope is not None:
        return temporal.overlaps_scope(valid_from, valid_until, scope)
    if historical:
        return True
    return validity_state(valid_from, valid_until) != "historical"


def _chunk_count(store) -> int:
    """Chunk 행 수(지연 빌드 여부 판단용). 조회 실패(미프로비저닝 등)는 0 으로 강등."""
    try:
        r = store.run("MATCH (c:Chunk) RETURN count(c) AS c")
        return int(r[0].get("c")) if r else 0
    except Exception:
        return 0


class _UnscopedChunkSearch(RuntimeError):
    """owner 스코프가 강제됐는데 백엔드가 청크 필터를 보장하지 못할 때.

    일반 예외와 구분하는 이유: 이건 인덱스 부재처럼 **재시도로 풀리는 실패가 아니다.**
    지연 빌드 후 다시 도는 경로로 새면 같은 무스코프 조회를 한 번 더 하게 된다."""


#: `_chunk_arms` 의 내부 팔 이름 → 응답 `arms` 의 표기.
_ARM_LABEL = {"keyword": "fts", "semantic": "vector"}


#: 벡터 인덱스가 없을 때 응답에 남기는 사유. 문구가 아니라 **이 필드의 존재**가
#: 신호다(서버가 `vector_note` 를 붙이는 자리와 같다 — 화면은 파싱하지 않는다).
_VECTOR_REBUILDING = ("vector index is being rebuilt after a write: "
                      "full-text results only for now")


#: 질의 벡터의 폭이 저장 벡터와 다를 때(임베딩 모델 전환). 엔진은 캐스팅 오류로만 알린다
#: (`... incorrect list entry to ARRAY. Expected: 4, Actual: 2.`). 재빌드 창이 아니라 실패다.
_VECTOR_WIDTH_MISMATCH = ("query vector width does not match the stored vectors, so the "
                          "semantic arm was skipped: full-text results only")


#: 벡터 색인이 **다시 서지 못하는** 스토어의 사유(잔해 — `VectorIndexRemnant`). 위와 달리
#: 실패다: `vector_rebuilding` 을 달지 않는다.
#: 스탬프 없는 옛 벡터가 있을 때의 경고. 의미 팔은 **돌았다** — 호환을 확인할 수 없을 뿐이다.
#: 평가 하네스(`rag_eval/evalkit/run_nodes._degraded`)가 이 문구를 강등에서 빼므로 상수로 둔다.
UNSTAMPED_RUNTIME_NOTE = "stored vectors have unknown encoder runtime; re-embed to verify compatibility"


_VECTOR_REMNANT = ("vector index unavailable: the store holds a remnant index that blocks "
                   "the rebuild until the sidecar restarts; full-text results only")


#: 같은 잔해가 FTS 색인(`Chunk`)에 있을 때. 청크 팔이 통째로 선다 — 청크 검색은 FTS 로 시작한다.
_FTS_REMNANT = ("full-text index unavailable: the store holds a remnant index that blocks "
                "the rebuild until the sidecar restarts; document search is down")


def _chunk_arms(store, query, query_embedding, top_k,
                filters) -> tuple[list[dict], str, list[str], bool, bool]:
    """Chunk FTS(+선택 벡터) 하이브리드 검색 코어 — 기존 §4-2 RRF 로직 그대로.
    반환 ``(rows, vector_note, arm_names, capped, keyword_gated)``. FTS·확장 부재는 예외로 전파(호출부가
    지연 빌드·error 표면화를 담당).

    ``capped`` 는 어느 팔이든 **거르기 전에** ``top_k`` 에 닿았는가다. 관례 청크를 빼면
    풀이 상한 아래로 줄어 호출부가 그 사실을 더는 못 본다.

    ``arm_names`` 는 **실제로 결과를 낸 팔**이다(``keyword`` · ``semantic``). 호출부의
    ``arms`` 는 이것에서만 나온다 — 검색 전에 세우면 이 함수가 던졌을 때 아무 팔도
    안 돌았는데 응답이 두 팔을 말한다.

    ⚠ **벡터 팔의 실패는 예외로 올리지 않는다**(사유는 ``vector_note``). 가장 흔한 것이
    인덱스 부재다. 문서를 쓰면 쓰기 경로가
    벡터 인덱스를 드롭하므로(HNSW 삽입 세그폴트 방어 — `drop_vector_index`), 쓰기
    직후의 검색은 이 자리를 반드시 밟는다. 여기서 통째로 던지면 **멀쩡한 키워드
    결과까지 버리고** 호출부가 HNSW 재빌드를 요청 스레드에서 기다렸다(실측: 문서
    쓰기 뒤 검색이 타임아웃). 키워드로 답하고 사유를 남긴다."""
    kw = store.fulltext_search(query, top_k, filters)           # BM25 암
    arms = [kw]
    arm_names = ["keyword"]
    vnote = ""
    if query_embedding is not None:
        try:
            arms.append(store.vector_search(query_embedding, top_k, filters))
            arm_names.append("semantic")
        except Exception as exc:
            # 벡터 팔이 **어떻게** 실패하든 이미 구한 키워드 결과는 지킨다(팔 하나의 고장이
            # 검색 전체의 고장이 되면 안 된다). 사유는 `vector_note` 로 가른다: 색인 재빌드
            # 창만 양성이고, 폭 불일치(모델 전환)와 나머지는 실패다.
            if _classify_chunk_error(exc) == "index_missing":
                vnote = _VECTOR_REBUILDING
            elif "incorrect list entry to array" in str(exc).lower():
                vnote = f"{_VECTOR_WIDTH_MISMATCH} ({exc})"
            else:
                vnote = f"vector arm failed: {type(exc).__name__}: {exc}"
    # 매치 표기(D24): 융합 전 각 arm 멤버십을 행에 주석 — UI 의 ≡/≈ 표기 데이터.
    membership: dict = {}
    for name, rows_ in zip(arm_names, arms):
        for r in rows_:
            membership.setdefault(r.get("id"), []).append(name)
    # 관례(GRAPH.md) 청크는 검색 결과에서 뺀다 — 사용자가 찾는 것은 자기 자료지
    # 그 자료를 어떻게 넣기로 했는지의 규약이 아니다. chunk_id 가 `{doc_id}#{n}` 이라
    # 추가 질의 없이 가르고, **융합 전에** 걸러야 상위 k 를 관례가 잠식하지 않는다.
    capped = any(len(a) >= top_k for a in arms)
    for a in arms:
        a[:] = [r for r in a if not is_conventions_chunk_id(r.get("id"))]
    gated = _keyword_gate(query, arms)
    if gated:
        arms[0] = []
        membership = {}
        for name, rows_ in zip(arm_names, arms):
            for r in rows_:
                membership.setdefault(r.get("id"), []).append(name)
    rows = (rrf_fuse(arms, k=CHUNK_RRF_K, top_k=top_k) if len(arms) > 1 else arms[0][:top_k])
    for r in rows:
        r.pop("lex", None)               # 관문용 열 — 응답에 나가지 않는다
        r["arms"] = membership.get(r.get("id"), [])
    return rows, vnote, arm_names, capped, gated


#: 키워드 팔 관문의 문턱과 보는 행 수. 의미 팔이 행을 냈을 때, 키워드 상위 GATE_TOP 행 가운데
#: 질의 토큰을 가장 많이 덮는 행조차 KEYWORD_GATE 에 못 미치면 키워드 목록을 융합에서 뺀다.
#: 한국어 질의로 영어 문서를 찾을 때 두 글자 묶음 팔은 늘 엉뚱한 한국어 행 200개를 내고, RRF 가
#: 그것으로 의미 팔의 정답을 밀어낸다(한→영 -0.058). 관문을 붙이면 같은 셋이 +0.148,
#: 사람 한→영 +0.264 이고 다섯 셋 어느 것도 나빠지지 않았다. 0.5 는 176문서 스토어에서 골랐고
#: 방송 말뭉치에서도 같은 자리에 선다(바꾼 말 65% · 그의 단어 1.7% 에서만 끈다).
#: 근거: [[vinc.research.korean-retrieval-2026-10-01 §4]]
KEYWORD_GATE = 0.5
GATE_TOP = 20


def _keyword_gate(query: str, arms: list[list[dict]]) -> bool:
    """키워드 목록을 융합에서 뺄 것인가. **의미 팔이 행을 냈을 때만** 판단한다 — 인코더가 없는
    데스크톱 사이드카나 의미 팔이 실패한 검색에서 키워드를 빼면 빈 페이지가 남는다.
    행에 `lex` 가 없으면(`migrate_lex_index` 를 아직 안 돌려 색인이 `text` 위인 스토어) 판단하지
    않는다: 관문은 두 글자 묶음 색인과 함께 잰 것이고, 이관 전 스토어는 관문 없이 답한다."""
    if len(arms) < 2 or not arms[1] or not arms[0]:
        return False
    head = arms[0][:GATE_TOP]
    if not all("lex" in r for r in head):
        return False
    from store.lexical import coverage, lex_tokens
    qt = lex_tokens(query)
    if not qt:
        return False
    best = max(coverage(qt, r.get("lex") or "") for r in head)
    return best < KEYWORD_GATE


def _schedule_vector_index(store, dim: int | None) -> None:
    """벡터(HNSW) 인덱스 재빌드를 **요청 스레드 밖으로** 보낸다.

    이 재빌드는 조회 요청 안에서 기다릴 일이 아니다: 잡 큐로 되돌려 보내는 경로
    (:meth:`store.embedded_backend._ReaderView.ensure_search_indexes`)가 상한 60초로
    **동기 대기**했고, 앞에 적재 잡이 있으면 검색이 그만큼 매달렸다. 예약만 하고
    이번 응답은 FTS 로 답한다(사유는 `vector_note`).

    ``schedule_search_indexes`` 가 없는 스토어(CLI·테스트가 직접 여는 백엔드)에서는
    기다릴 요청 스레드가 애초에 없으므로 그 자리에서 만든다 — 다음 검색부터 벡터
    팔이 산다. 실패는 삼킨다: 예약 실패는 검색 실패가 아니고, 다음 검색이 다시 건다."""
    fn = (getattr(store, "schedule_search_indexes", None)
          or getattr(store, "ensure_search_indexes", None))
    if fn is None or dim is None:
        return
    try:
        fn(dim=dim)
    except Exception:
        pass


#: 매치 필드 강도 — 같은 토큰 수를 맞췄으면 제목이 이긴다.
_FIELD_RANK = {"title": 0, "one_liner": 1, "props": 2, "id": 3}


#: id 매치에서 무시할 토큰 — 노드 종류 접두사(`concept:` 등)는 모든 id 에 들어 있어
#: 그대로 세면 질의 하나가 전 노드를 같은 점수로 맞춘다. 접두사는 신호가 아니다.
_ID_PREFIX_TOKENS = frozenset(("concept", "episode", "record", "bridge", "entity",
                               "decision"))   # v12


#: 어휘 순위로 선택한 후보의 상한. 저장소 반환 순서로 강한 후보를 먼저 버리지 않는다.
NODE_SCAN_CAP = 2000


def _id_token(t: str) -> bool:
    """id 매치에 쓰는 토큰인가 — 종류 접두와 한 글자는 거의 모든 id 에 들어 있다."""
    return len(t) > 1 and t not in _ID_PREFIX_TOKENS


def _node_fallback_search(store, query: str, *, limit: int = 20,
                          owner: str | None = None, historical: bool = False,
                          stats: dict | None = None,
                          scope: tuple[str, str] | None = None) -> list[dict]:
    """Concept·Record 타이틀/요약/props 폴백 검색(T3b C1 **핵심**) — FTS·벡터 결과와
    **무관하게 항상** 수행한다. Concept/Record 는 Chunk 인덱스에 안 들어가(제목·요약을
    가진 노드가 여태 검색 자체가 불가) Chunk 0개 스토어에선 검색이 통째로 비었다 —
    실사용 사고의 직접 원인. 이 폴백이 그 갈래를 해소한다.

    **A1.10 부분 매치 + 점수 랭킹**(자연어 질문 0건 해소, 2026-08-03). 구 A1.9 는
    **토큰 AND** 였다 — 모든 토큰이 어느 필드엔가 있어야 매치. 이름을 그대로 댄 질의만
    통과하고, 조사·의문사가 섞인 자연어 질문("낙관적 동시성 제어가 뭐야?")은 '제어가'·
    '뭐야' 때문에 **자기 제목조차 못 찾아** 항상 0건이었다. 이제:

      ① :func:`normalize_query_tokens` 로 구두점·조사·의문사를 걷어내고,
      ② **토큰 OR** 로 후보를 모은 뒤,
      ③ **파이썬에서 점수를 매긴다**(§4-2 — Cypher 안에 점수식을 두지 않는다):
         맞은 토큰 수(주) → 제목에서 맞은 토큰 수 → 필드 강도 → id.

    반환 행: {id, title, kind('concept'|'record'|'decision'|'episode'), domain, matched_in('title'|
    'one_liner'|'props'), hit_tokens, title_tokens, token_total} — 뒤 세 개가 신설이다.
    호출부(묻기 등)가 "얼마나 확실한 매치인가"를 **임의 임계값 없이** 판정하려면 맞은
    토큰 수가 필요하다. 구 소비처는 새 키를 무시하면 그만이다(가산 변경).

    OR 후보를 기존 어휘 순위로 좁힌 뒤 :data:`NODE_SCAN_CAP` 상한을 적용한다.
    잘린 후보가 있으면 통계에 남기며 저장소 반환 순서에 따라 강한 후보를 버리지 않는다.
    빈 쿼리·토큰 없음은 모든 노드를 삼키므로 스킵([])."""
    toks = normalize_query_tokens(query)
    if not toks:
        return []
    params: dict = {f"q{i}": t for i, t in enumerate(toks)}
    idx = range(len(toks))
    # SEC-1: 소유자 스코프. 없으면(로컬 단일 사용자) 기존 전역 검색.
    own_c = own_r = own_d = own_e = ""
    if owner:
        own_c, own_r = " AND c.owner_id = $owner", " AND r.owner_id = $owner"
        own_d, own_e = " AND d.owner_id = $owner", " AND e.owner_id = $owner"
        params["owner"] = owner

    # PK 네임스페이싱 — id 열은 물리 키(`owner슬러그`)라, **owner 문자열의
    # 부분열인 토큰이 그 소유자의 모든 행에 걸린다**(owner 가 `user:019f…` 면 토큰
    # "user" 하나로 전량 매치). 아래 파이썬 재필터는 읽기 훅이 되돌린 **논리** id 로
    # 다시 재므로 결과가 틀리지는 않지만, NODE_SCAN_CAP 을 그 쓰레기로 채우면 진짜
    # 매치가 잘린다. 그래서 **id 열에서만** 그런 토큰을 뺀다.
    # 근거: [[vinc.plan.node-pk-namespacing]] §7
    own_low = (owner or "").lower() if pkns.enabled() else ""
    # 종류 접두(`concept` 등)와 한 글자 토큰도 id 열에서 뺀다 — 모든 id 에 들어 있어서
    # 스캔 상한을 쓰레기로 채운다(점수 단계도 같은 규칙, :func:`_id_token`).
    id_idx = [i for i in idx
              if _id_token(toks[i]) and not (own_low and toks[i] in own_low)]

    def _any(fields: tuple[str, ...]) -> str:   # 토큰 하나라도, 필드 하나라도
        parts = [f"lower(coalesce({f},'')) CONTAINS $q{i}"
                 for f in fields
                 for i in (id_idx if f.endswith("_id") else idx)]
        return "(" + " OR ".join(parts) + ")" if parts else "(1 = 0)"

    # id(슬러그)도 검색한다: GRAPH.md 관례가 `concept:optimistic-locking` 처럼 **뜻이
    # 담긴 영어 슬러그**를 강제하므로, 제목이 한국어인 개념을 영어 용어로 찾는 유일한
    # 경로다(실측: "optimistic locking" 이 자기 개념을 못 찾고 0건이었다).
    c_fields = ("c.title", "c.one_liner", "c.props", "c.concept_id")
    r_fields = ("r.title", "r.props", "r.record_id")
    # v12 — Decision 을 같은 폴백에 넣는다. 빠뜨리면 **결정이 검색으로 도달 불가**가
    # 된다(D57: 검색이 제1 진입점). 그리고 이 함수는 `near_nodes` 도 쓰므로, 여기
    # 없으면 `vinc_neighbors` 가 실재하는 결정을 "unknown node" 라고 답한다 — 실제로
    # 라이브에서 그렇게 나왔다(2026-08-09 이관 직전 탐침).
    d_fields = ("d.title", "d.props", "d.decision_id")
    # Episode 도 같은 폴백에 넣는다(2026-09-09). 여기 없던 동안 **사건은 어떤 팔로도
    # 후보가 될 수 없었다** — 제목 팔의 UNION 은 셋뿐이고 그래프 팔은 Concept 만
    # 승격하므로, 스토어에 160건이 있어도 `brief` 가 이름을 댈 방법이 0이었다. Decision
    # 이 v12 전까지 있던 자리와 같고, 그때 남긴 이유("빠뜨리면 검색으로 도달 불가")도 같다.
    #
    # 실측으로 골랐다: 정답 60문항에서 R@1 0.133 → 0.183, R@5 0.283 → 0.367 이고
    # **다른 종류는 한 문항도 잃지 않는다**(Episode 만 0/10 → 3/10). 같이 재 본 "맞은
    # 토큰 수를 제목 길이로 나눈다"는 정반대였다(R@1 0.033) — 짧은 제목이 토큰 하나로
    # 다 이겨서, 그 가설은 기각한다.
    #
    # ⚠ **`summary` 는 검색 필드에 넣지 않는다.** 처음엔 넣었다가 되돌렸다(2026-09-09).
    # 사건의 요약은 문단이라 토큰 표면이 넓고, 그건 Record 제목이 남의 질문을 가져가는
    # 것과 **같은 기전**이다. 실측: 요약을 포함하면 답 불가 질문 6건 중 1건이 사건으로
    # **확정**됐다(정직성 0.0 → 0.1667). 빼면 이득은 그대로다 — R@1 0.1667 유지, 후보
    # 진입 0.3333 → 0.3500, 확정 오답 0.6167 → 0.6000, 답 불가 확정 0.0 복구. R@3 만
    # 0.2000 → 0.1833(한 문항)이 값이다.
    #
    # 확정된 뒤의 재료는 그대로다 — `node_context` 가 `summary` 를 one_liner 자리로
    # 올리므로 서술 문단은 답변 재료에 실린다. 여기서 빼는 것은 **찾는 표면**뿐이다.
    # 근거: [[record:episode-can-never-be-a-brief-candidate-2026-09-09]] ·
    # [[record:adding-episode-to-the-title-arm-is-a-pure-gain-2026-09-09]] ·
    # [[record:the-episode-summary-is-too-wide-a-search-surface-2026-09-09]]
    e_fields = ("e.title", "e.episode_id")
    cypher = (
        f"MATCH (c:Concept) WHERE {_any(c_fields)}{own_c} "
        "RETURN c.concept_id AS id, c.title AS title, 'concept' AS kind, "
        f"c.domain AS domain, c.one_liner AS one_liner, c.props AS props_raw, "
        f"{_VALIDITY_EMPTY} "
        "UNION ALL "
        f"MATCH (r:Record) WHERE {_any(r_fields)}{own_r} "
        "RETURN r.record_id AS id, r.title AS title, 'record' AS kind, "
        f"r.domain AS domain, '' AS one_liner, r.props AS props_raw, {_validity_cols('r')} "
        "UNION ALL "
        f"MATCH (d:Decision) WHERE {_any(d_fields)}{own_d} "
        "RETURN d.decision_id AS id, d.title AS title, 'decision' AS kind, "
        f"d.domain AS domain, '' AS one_liner, d.props AS props_raw, {_VALIDITY_EMPTY} "
        "UNION ALL "
        f"MATCH (e:Episode) WHERE {_any(e_fields)}{own_e} "
        "RETURN e.episode_id AS id, e.title AS title, 'episode' AS kind, "
        f"e.domain AS domain, '' AS one_liner, '' AS props_raw, {_VALIDITY_EMPTY}"
    )
    rows = store.run(cypher, params)
    scored: list[dict] = []
    dropped: set = set()       # 지운 id — 의미 팔이 같은 노드를 지워도 한 번만 센다
    best_dropped = (0, 0)      # 지운 행 중 가장 센 (hit_tokens, title_tokens)
    for r in rows:
        # 유효구간 기본 필터(채택된 시간 정책 §4.2). **미기재는 current 라서 안 걸린다** —
        # 그 절이 빠지면 낡은 것이 아니라 코퍼스의 7할이 사라진다(`validity_state` 참조).
        # 🔴 **거르는 것은 `historical` 뿐이다.** `!= "current"` 로 적으면 `future` 가
        # 함께 떨어지는데, 그 상태는 `valid_until` 이 아니라 `valid_from` 에서 나온다 —
        # 감출 수 있는 것은 명시된 `valid_until` 뿐이라는 것이 `validity_state` 의 정책이고
        # 도구 계약도 같은 말을 한다. UTC 시계가 KST 저작본을 매일 아침 아홉 시간 동안
        # `future` 로 떨어뜨려 이 모순이 날마다 발화했다:
        # [[record:validity-state-compares-a-local-dated-claim-against-a-utc-clock-2026-09-14]].
        # 지운 수는 세어서 호출부에 돌려준다: 조용히 줄어든 결과는 "그래프에 이것뿐"으로
        # 읽히고, 그건 이 정책이 막으려는 고장과 같은 종이다.
        # `scope`(as_of 기간)가 있으면 그 기간에 성립했는지로 가른다(`_visible`).
        if scope is not None or not historical:
            if not _visible(*_row_validity(r), historical=historical, scope=scope):
                dropped.add(r.get("id"))
                # 지운 행의 점수도 기억한다. 지우기가 **동점을 깨면** 남은 하나가
                # 단독 승자가 되어, 원래 "모르겠다"였을 질문이 확신 있는 답이 된다.
                # 부르는 쪽이 그것을 알아야 판단할 수 있다.
                _dt_ = sum(1 for t in toks if t in str(r.get("title") or "").lower())
                _dh_ = sum(1 for t in toks
                           if t in str(r.get("title") or "").lower()
                           or t in str(r.get("one_liner") or "").lower()
                           or t in str(r.get("props_raw") or "").lower())
                if (_dh_, _dt_) > best_dropped:
                    best_dropped = (_dh_, _dt_)
                continue
        title = str(r.get("title") or "").lower()
        one = str(r.get("one_liner") or "").lower()
        props = str(r.get("props_raw") or "").lower()
        nid = str(r.get("id") or "").lower()
        n_title = sum(1 for t in toks if t in title)
        n_one = sum(1 for t in toks if t in one)
        n_props = sum(1 for t in toks if t in props)
        n_id = sum(1 for t in toks if _id_token(t) and t in nid)
        hit = sum(1 for t in toks
                  if t in title or t in one or t in props
                  or (_id_token(t) and t in nid))
        if not hit:
            continue
        where = ("title" if n_title else "one_liner" if n_one
                 else "props" if n_props else "id")
        scored.append({"id": r.get("id"), "title": r.get("title"), "kind": r.get("kind"),
                       "domain": r.get("domain") or "", "matched_in": where,
                       "hit_tokens": hit, "title_tokens": n_title,
                       "token_total": len(toks)})
    # 결정론 랭킹: 맞은 토큰 수 ↓ → 제목 토큰 수 ↓ → 필드 강도 ↑ → id ↑.
    if stats is not None:
        stats["candidate_count"] = len(scored)
        stats["candidate_count_capped"] = len(scored) > NODE_SCAN_CAP
        stats["dropped_ids"] = dropped
        stats["dropped_not_current"] = len(dropped)
        stats["best_dropped_score"] = best_dropped
    from heapq import nsmallest
    return nsmallest(min(NODE_SCAN_CAP, max(1, int(limit))), scored,
                     key=lambda r: (-r["hit_tokens"], -r["title_tokens"],
                                    _FIELD_RANK.get(r["matched_in"], 9), str(r["id"])))


def _graph_arm_search(store, chunk_ids: list[str], *, owner: str | None = None,
                      limit: int = 20) -> list[dict]:
    """E-G1 그래프 암 — 상위 Chunk 가 :MENTIONS 하는 Concept + 그 1-hop Concept
    이웃을 후보로 승격한다(리서치 ① 다중 홉 가교 — 이부 그래프 확산의 결정론 번역).

    ``chunk_ids`` 는 **랭크 순서**(상위 청크 우선). 직접 언급 개념이 이웃보다 앞서고
    (더 강한 신호), 같은 층 내에서는 그 개념을 언급한 최상위 청크의 랭크로 정렬한다.
    반환 행은 node_hits 동형 {id,title,kind:'concept',domain,matched_in:'graph'} —
    :func:`search` 가 title 폴백과 RRF 융합한다. MENTIONS/Chunk 부재 시 [](하위호환).

    owner(SEC-1): Concept·이웃 모두 owner 스코프. `-[r]-` 무방향 1-hop(방언 중립)."""
    if not chunk_ids:
        return []
    params: dict = {"ids": list(chunk_ids)}
    own_c = own_nb = ""
    if owner:
        own_c = " AND c.owner_id = $owner"
        own_nb = " AND nb.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) "
        f"WHERE ch.chunk_id IN $ids{own_c} "
        "OPTIONAL MATCH (c)-[r]-(nb:Concept) "
        f"WHERE nb.concept_id <> c.concept_id{own_nb} "
        "RETURN ch.chunk_id AS chunk_id, c.concept_id AS id, c.title AS title, "
        "c.domain AS domain, nb.concept_id AS nb_id, nb.title AS nb_title, "
        "nb.domain AS nb_domain"
    )
    rows = store.run(cypher, params)
    rank = {cid: i for i, cid in enumerate(chunk_ids)}
    direct: dict[str, dict] = {}   # concept_id → best (min chunk rank)
    neigh: dict[str, dict] = {}    # 1-hop 이웃(직접 언급이 아닌 것만)
    for r in rows:
        cid = r.get("id")
        if not cid:
            continue
        cr = rank.get(r.get("chunk_id"), 10 ** 9)
        cur = direct.get(cid)
        if cur is None or cr < cur["_r"]:
            direct[cid] = {"id": cid, "title": r.get("title"),
                           "domain": r.get("domain"), "_r": cr}
        nb = r.get("nb_id")
        if nb and nb != cid:
            # 이웃도 자기를 불러온 최상위 청크의 랭크로 줄 선다(직접 언급과 같은 규칙).
            cur = neigh.get(nb)
            if cur is None or cr < cur["_r"]:
                neigh[nb] = {"id": nb, "title": r.get("nb_title"),
                             "domain": r.get("nb_domain"), "_r": cr}
    for cid in direct:            # 직접 언급이면 이웃 목록에서 제외(중복 방지)
        neigh.pop(cid, None)
    ordered = (sorted(direct.values(), key=lambda x: (x["_r"], x["id"]))
               + sorted(neigh.values(), key=lambda x: (x["_r"], x["id"])))
    # `hit_tokens`/`title_tokens` 를 0 으로 명시한다(A1.10): 그래프 암은 **질문 토큰이
    # 아니라 청크 인접으로** 올라온 후보다. 소비처가 두 암을 한 줄로 정렬할 때 키가
    # 없어 `undefined` 로 비교되면 조용히 뒤섞인다 — 0 은 "토큰으로는 안 맞았다"는
    # 명시적 진술이고, 그래서 토큰 매치가 하나라도 있는 후보가 항상 앞선다.
    out = [{"id": x["id"], "title": x.get("title") or x["id"], "kind": "concept",
            "domain": x.get("domain") or "", "matched_in": "graph",
            "hit_tokens": 0, "title_tokens": 0}
           for x in ordered]
    return out[:max(1, int(limit))]


#: 의미 팔이 낸 id 들의 도메인. **미러는 그 열을 안 담는다** — `NodeVec` 은 찾는 표면과
#: 라벨만 들고 있고 도메인은 노드 표에 있다.
#:
#: 🔴 여기까지 빈 문자열을 채워 보내면 받는 쪽이 **"무주제"로 읽는다.** 2026-09-10 에
#: 고아 조사를 하다가 의미 팔이 낸 `domain: ""` 을 보고 "무주제 노드가 있다, 그런데
#: 센서스는 0을 답한다 → 센서스가 틀렸다"까지 갔다. 센서스는 옳았고 이 필드가 거짓이었다.
#: 같은 노드가 그래프 팔로는 제 도메인을, 의미 팔로는 빈 문자열을 내는 것이 증거다.
#: **없는 것과 비어 있는 것은 다르다**, 그리고 이 팔은 이제 기본으로 켜져 있다.
def _domains_for_ids(store, ids: list[str], *, owner: str | None = None) -> dict:
    """논리 id → domain. 네 라벨을 각각 한 번씩 훑는다(반환은 top_k 규모라 작다)."""
    if not ids:
        return {}
    out: dict[str, str] = {}
    for label, pk in (("Concept", "concept_id"), ("Record", "record_id"),
                      ("Decision", "decision_id"), ("Episode", "episode_id")):
        where = f"WHERE n.{pk} IN $ids"
        # PK 네임스페이싱이 켜진 스토어에서 PK 열은 물리 키(owner\x1fid)다 — 논리
        # 슬러그로 바인딩하면 한 건도 안 맞고 domain 이 통째로 빈다(PKNS 잡에서만
        # 붉었다). 되돌리기는 읽기 훅이 한다. 근거: [[vinc.plan.node-pk-namespacing]] §7
        params: dict = {"ids": pkns.keys(owner, list(ids))}
        if owner:
            where += " AND n.owner_id = $o"
            params["o"] = owner
        try:
            rows = store.run(f"MATCH (n:{label}) {where} "
                             f"RETURN n.{pk} AS id, n.domain AS domain", params) or []
        except Exception:
            continue        # 한 라벨이 막혀도 나머지는 채운다 — 이 값은 증강이다
        for r in rows:
            if r.get("id"):
                out[r["id"]] = r.get("domain") or ""
    return out


def _validity_for_ids(store, ids: list[str], *, owner: str | None = None) -> dict:
    """논리 id → (valid_from, valid_until). Record 는 열, Decision 은 props 의 임시 인코딩
    (시간 정책 §8 항목 5). 미러(NodeVec)에는 구간이 없어 의미 팔이 올린 후보는 여기서
    되찾아 본다. Concept·Episode 는 구간이 없다 — 목록에 없으면 호출부가 ('', '') 로
    읽고 그것은 current 다. 조회가 막히면 예외를 그대로 올린다: 호출부(의미 팔)의
    except 가 `node_error` 를 남기고 그 팔을 비운다 — 조용히 통과시키는 쪽이 이 필터가
    막으려는 고장이다."""
    if not ids:
        return {}
    out: dict[str, tuple[str, str]] = {}
    params: dict = {"ids": pkns.keys(owner, list(ids))}
    own = ""
    if owner:
        own = " AND n.owner_id = $o"
        params["o"] = owner
    for r in store.run("MATCH (n:Record) WHERE n.record_id IN $ids" + own
                       + " RETURN n.record_id AS id, " + _validity_cols("n"), params) or []:
        if r.get("id"):
            out[r["id"]] = (r.get("valid_from") or "", r.get("valid_until") or "")
    for r in store.run("MATCH (n:Decision) WHERE n.decision_id IN $ids" + own
                       + " RETURN n.decision_id AS id, n.props AS props_raw", params) or []:
        if r.get("id"):
            p = _props_to_obj(r.get("props_raw"))
            out[r["id"]] = (str(p.get("valid_from") or ""), str(p.get("valid_until") or ""))
    return out


def _node_semantic_arm_on() -> bool:
    """의미 암을 켤 것인가. **기본은 켜짐**이고 `VINC_NODE_SEMANTIC_ARM=off` 가 비상구다.

    ⚠ 2026-09-10 에 뒤집혔다. 전에는 꺼져 있었고 근거는 "팔이 후보만 올리고 해상은 안
    바꾼다"였다 — 후보 진입은 21/60 → 35/60 인데 확정된 46건의 **선택이 하나도 안
    바뀌었다**. 그 전제가 거짓이 됐다: 팔 자신의 1-2위 거리 격차를 순서 키에 넣자
    노출 R@1 이 0.2833 → **0.4833**, 확신 오답 7 → 3 이 됐고 답 불가 확정은 그대로다.
    한국어 축이 0.100 → 0.500 으로 가장 크게 움직인다.

    **비용은 질의가 아니라 릴리스가 낸다.** 이 팔은 `NodeVec` 미러를 읽는데 미러는
    전량 재구축만 있고(HNSW 증분 삽입이 세그폴트를 낸다) 그 재구축은 배포 경계에서
    한 번 돈다(`api/server.py::write_node_mirror_rebuild`). 미러가 비어 있으면 이 팔은
    **빈 목록**이지 예외가 아니다 — 그래서 켜 두는 것이 안전하다.
    근거: [[decision:the-semantic-arm-ships-on-and-the-mirror-rebuilds-at-the-release-boundary]]"""
    return str(os.environ.get("VINC_NODE_SEMANTIC_ARM", "")).strip().lower() not in (
        "0", "off", "false", "no")


def _node_sem_rank_on() -> bool:
    """의미 팔의 단호함을 순서에 반영할 것인가. **기본은 켜짐**(`VINC_NODE_SEM_RANK=off`).

    팔과 **한 몸이다** — 위의 0.4833 은 둘 다 켠 값이고, 팔만 켜면 0.2833 으로 돌아간다.
    따로 끌 수 있게 둔 이유는 격차 문턱(`_SEM_GAP`)을 다시 잴 때 순서 기여만 떼어
    보기 위해서다."""
    return str(os.environ.get("VINC_NODE_SEM_RANK", "")).strip().lower() not in (
        "0", "off", "false", "no")


#: 의미 팔이 **자기 기준으로 단호하다**고 볼 1-2위 거리 격차.
#:
#: 절대 거리는 못 쓴다: 완전 일치가 0.077 인데 아무 말이나 던져도 0.170 이라 범위가
#: 좁고 크게 겹친다(실측 2026-09-10). 가르는 것은 **1위가 2위보다 뚜렷하게 가까운가**이고,
#: 그 격차의 중앙값은 맞혔을 때 0.0215 · 틀렸을 때 0.0047 로 4.6배 갈린다. 어휘 층이
#: "1위가 2위를 2토큰 차로 이겨야 한다"고 요구하는 것과 같은 모양이다.
#:
#: 문턱별 의미 팔 1위 정밀도(답 가능 60): 0.000 → 28/60(0.47) · 0.010 → 26/31(0.84)
#: · 0.025 → 12/13(0.92) · 0.040 → 9/9(1.00).
#: ⚠ 이 단호함은 **순서만** 바꾼다. 확신은 여전히 어휘 여유를 요구한다 — 답 불가
#: 21문항 중 9건도 0.010 격차를 통과하므로 이것으로 확정을 주면 정직성이 무너진다.
#: 근거: [[record:the-semantic-arm-decides-by-its-own-margin-not-its-distance-2026-09-10]]
_SEM_GAP = 0.010


def _fuse_node_hits(title_hits: list[dict], graph_hits: list[dict], *,
                    top_k: int, vec_hits: list[dict] | None = None) -> list[dict]:
    """title 폴백 + 그래프 암 (+ v17 의미 암)을 개념-공간 RRF 융합(§4-2, D24 매치 표기).
    각 행에 ``arms``(title/graph/semantic 멤버십)를 부여한다. 다른 팔이 다 비면
    **융합하지 않고** title 순서를 그대로 유지(하위호환 — 기존 Chunk 0 스토어 동작 불변).

    의미 암은 **어휘가 하나도 안 겹쳐도 닿는 유일한 팔**이다. 실측(2026-09-09): 후보
    진입을 21/60 에서 35/60 으로 올리고, 그 이득은 제목이 안 겹치는 축에 몰린다
    (제목 재사용 ≤0.34 인 한국어 질문 1/8 → 5/8). 다만 **답이 바뀌지는 않는다** —
    기본이 꺼진 이유는 :func:`_node_semantic_arm_on` 에 있다.
    근거: [[record:the-node-semantic-arm-lifts-candidacy-not-answers-2026-09-09]]"""
    membership: dict = {}
    arms_in = [("title", title_hits), ("graph", graph_hits),
               ("semantic", vec_hits or [])]
    for name, rows_ in arms_in:
        for r in rows_:
            membership.setdefault(r.get("id"), set()).add(name)
    lists = [rows_ for _, rows_ in arms_in if rows_]
    if len(lists) < 2:
        # 융합할 것이 없으면 그 한 팔의 순서를 그대로 낸다. `title_hits` 로 못 박으면
        # **제목 팔이 빈 채 다른 팔만 찬 경우가 통째로 버려진다**(융합 전 동작은 그때
        # 그 팔의 행을 냈다).
        out = lists[0] if lists else []
        for r in out:
            r["arms"] = sorted(membership.get(r.get("id"), []))
        return out
    fused = rrf_fuse(lists, top_k=top_k)   # title 우선(행 유지)
    for r in fused:
        r["arms"] = sorted(membership.get(r.get("id"), []))
    return fused


#: 스토어 하나가 기억하는 소유자 수의 상한. 넘치면 통째로 비운다(다음 검색이 다시 훑는다).
_RUNTIME_STAMP_OWNERS = 256


def _stored_runtimes(store, owner: str | None) -> frozenset[str]:
    """요청 소유자의 저장 벡터 도장 집합. 벡터 쓰기 세대가 그대로면 두 표를 다시 훑지 않는다.

    캐시는 백엔드에 달려 있다(`EmbeddedBackend.runtime_stamps` — 리더 뷰는 `_b` 로 닿는다).
    없는 스토어(테스트 더블)는 매번 훑는다. 세대는 훑기 **전에** 읽는다: 그래야 훑는 도중
    끝난 쓰기가 옛 세대로 적혀 다음 검색이 다시 훑는다(:func:`store.embedded_backend.vector_generation`).
    잠금은 없다. 스레드가 겹쳐도 dict 연산 하나하나는 원자적이고, 잃는 것은 캐시 적중뿐이다."""
    gen = vector_generation()
    cache = getattr(getattr(store, "_b", store), "runtime_stamps", None)
    key = owner or ""
    hit = cache.get(key) if isinstance(cache, dict) else None
    if hit is not None and hit[0] == gen:
        return hit[1]
    stored: set[str] = set()
    for table in ("ChunkVec", "NodeVec"):
        where = " WHERE v.owner_id = $owner" if owner else ""
        rows = store.run(
            f"MATCH (v:{table}){where} RETURN DISTINCT v.encoder_runtime AS runtime",
            {"owner": owner} if owner else {}) or []
        stored.update(str(row.get("runtime") or "") for row in rows)
    stamps = frozenset(stored)
    if isinstance(cache, dict):
        if len(cache) >= _RUNTIME_STAMP_OWNERS:
            cache.clear()
        cache[key] = (gen, stamps)
    return stamps


def _encoder_runtime_warning(store, owner: str | None, runtime: str) -> str:
    """구 벡터·혼재·불일치를 경고한다. 부분 재임베딩은 한 스토어에 두 판을 남길 수 있다.

    벡터마다 찍힌 지문을 요청 소유자 안에서만 대조한다.
    """
    try:
        stored = _stored_runtimes(store, owner)
    except Exception as exc:
        return f"encoder runtime check failed: {type(exc).__name__}"
    if not stored:
        return ""
    known = stored - {""}
    if known - {runtime}:
        return ("query/stored encoder runtime mismatch: "
                f"query={runtime}; stored={','.join(sorted(known))}"
                + ("; some stored vectors are unknown" if "" in stored else ""))
    if "" in stored:
        return UNSTAMPED_RUNTIME_NOTE
    return ""


def _search(query: str, *, query_embedding: list[float] | None = None,
           top_k: int = 5, filters: dict | None = None, owner: str | None = None,
           cursor: str | None = None, detail: str = "summary",
           historical: bool = False, as_of: str | None = None,
           mode: str | None = None, known_at: str | None = None,
           query_runtime: str | None = None,
           relevance_profile: dict | None = None,
           relevance_corpus_revision: str | None = None,
           cfg=None, store=None) -> dict:
    """하이브리드 검색 **재설계**(T3b) — BM25(FTS)+(선택)벡터 RRF(§4-2) 위에 3개
    실사용 사고 대응을 얹는다:

      1. **실패 침묵 제거** — Chunk 검색 실행 실패 시 최상위 ``error`` 로 표면화한다
         (``executed:False`` 유지). '인덱스 부재'와 '확장 미가용'을 접두로 구분
         (:func:`_classify_chunk_error`). 이전엔 실패가 `executed:false`+빈 rows 로
         침묵해 호출자가 '결과 없음'으로 오판했다.
      2. **지연 인덱스 자동 빌드** — FTS 가 '인덱스 부재'로 실패하면 Chunk 행이 있을
         때 인덱스를 1회 빌드(멱등)하고 재검색한다. Chunk 0개면 빌드 없이 조용히 폴백.
      3. **Concept/Record 타이틀 폴백(핵심)** — FTS/벡터 결과와 무관하게 항상 노드
         폴백 검색을 수행해 신규 필드 ``node_hits`` 에 담는다. ``rows`` 는 Chunk 결과
         전용(하위호환). Chunk 0개 스토어에서도 개념이 검색된다.

    벡터 암은 **쿼리 임베딩 주입형**(§3-1) — 미주입 시 BM25 단독.
    ``store`` 주입(사이드카 §11 P2): read 뷰를 넘기면 재사용(열지도 닫지도 않음).

    **커서 페이지네이션(GM-14)**: ``rows`` 는 안정 정렬(점수 내림차순 + id 오름차순)
    후보 풀(상한 :data:`SEARCH_POOL_CAP`)의 ``top_k`` 페이지다. ``total_count`` ·
    ``has_more`` · ``next_cursor`` 가 함께 실리고, 무효/실효 커서는 조용한 폴백 없이
    최상위 ``error``(invalid_cursor/stale_cursor) 로 답한다. 기본 ``detail="summary"``
    는 청크 전문 대신 발췌(snippet)를 싣는다 — 전문은 vinc_doc_get(doc_id)."""
    cfg = cfg or load_config()
    # 운영 프로필 활성화는 보정·승인 뒤의 별도 설정이다. 기본값에는 수치 문턱이 없다.
    # VIN-343의 2026-10-05 사용자 결정: 빈 정상 결과와 별도 약한 후보 영역.
    # 보정 검증 계약: [[vinc.manual.rag]]
    profile_error = ""
    from vinc_rag.search_relevance import load_profile, validate_profile, profile_state, classify_rows
    try:
        profile_path = os.environ.get("VINC_SEARCH_RELEVANCE_PROFILE", "").strip()
        if relevance_profile is not None:
            relevance_profile = validate_profile(relevance_profile)
        elif profile_path:
            relevance_profile = load_profile(profile_path)
            # A calibrated personal corpus must not set floors for every tenant.
            if not relevance_profile.get('owner_id'):
                raise ValueError('operating profile requires an owner')
            # This is the operator-declared, independently approved calibration
            # revision. Never take it from the profile itself to bypass the gate.
            if relevance_corpus_revision is None:
                relevance_corpus_revision = os.environ.get('VINC_SEARCH_CORPUS_REVISION', '').strip() or None
    except (ValueError, OSError) as exc:
        relevance_profile = None
        profile_error = type(exc).__name__
    detail = "full" if (detail or "").strip().lower() == "full" else "summary"
    # 어느 팔이 돌았는지를 **결과에 남긴다**. 임베딩이 오래 write-only 였는데(호출부가
    # query_embedding 을 안 넘겨 vector_search 가 한 번도 실행되지 않았다) 에러가 없어
    # 아무도 몰랐다. 이 필드가 그 침묵을 깬다 — CLI·HTTP 양쪽이 같은 값을 본다
    # (HTTP 계층에서만 붙이면 §11 게이트① 의 CLI↔HTTP 동등성이 깨진다).
    # ⚠ **아직 아무 팔도 안 돌았으므로 "none" 에서 시작한다.** 여기서 임베딩 유무로
    # "fts+vector" 를 미리 세우면, 청크 팔이 통째로 죽었을 때(확장 미가용·미프로비저닝)
    # 응답이 **돌지도 않은 팔의 이름을 계속 달고 나간다** — 침묵보다 나쁜 거짓말이다.
    base = {"kind": "search", "executed": False, "rows": [], "node_hits": [],
            "arms": "none",
            "total_count": 0, "has_more": False, "next_cursor": None}
    if profile_error:
        base["relevance_policy"] = {"state": "invalid_profile", "error": profile_error}
    # 커서 검증은 스토어를 열기 전에 — 무효 커서에 조회 비용을 쓰지 않는다.
    qh = _query_fingerprint(query, owner, filters, detail, as_of, mode=mode,
                            historical=historical, known_at=known_at)
    # 시점 인자도 스토어를 열기 전에 검사한다. 틀린 값이 조용히 오늘로 떨어지면 부르는
    # 쪽은 과거를 읽은 줄 안다.
    try:
        historical, _scope, _scope_info = temporal.mode_scope(mode, historical, as_of, query)
    except ValueError as exc:
        base["note"] = base["error"] = f"invalid_temporal_mode: {exc}" if mode else f"invalid_as_of: {exc}"
        return base
    if mode:
        base["mode"] = mode
        base["claim_assessment_scope"] = "retrieved_candidates"
    if known_at and not temporal.time_scope(known_at):
        base["error"] = f"invalid_time_scope: invalid known_at {known_at!r}"
        return base
    off, want_eid, want_arms = 0, None, None
    if cursor:
        try:
            c = cursor_decode(cursor)
            if c.get("qh") != qh:
                raise ValueError(
                    "invalid_cursor: cursor was issued for a different query/scope")
            off = max(0, int(c.get("off") or 0))
            want_eid = c.get("eid")
            want_arms = c.get("arms")    # 1페이지를 낸 팔. 이 키가 없던 커서는 대조하지 않는다
        except (TypeError, ValueError) as exc:
            base["note"] = base["error"] = str(exc)
            return base
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base
    # 🔴 **node_hits 는 첫 페이지에만 싣는다.** 커서는 청크 풀 위의 오프셋이고
    # (off·eid·has_more 전부 rows 의 좌표다), 노드 팔들은 그 풀과 무관한 자기 top_k
    # 목록이다 — 그래서 여태 모든 페이지가 같은 node_hits 를 되풀이했다(감사 §5 #17:
    # `query=design limit=5` 세 페이지 모두에 concept:design-hub). 노드 쪽에 오프셋을
    # 주지 않는 이유: 그래프 팔은 **그 페이지의** 청크에서 이웃을 뽑으므로 노드 목록이
    # 페이지마다 다른 풀이 되고, 안정된 순서도 두 번째 has_more 도 없다. 이어 읽기
    # 페이지는 비운 node_hits 와 `node_hits_omitted` 를 싣는다 — 첫 페이지는 한 바이트도
    # 안 바뀐다(brief·Ask·검색창은 커서를 안 쓴다).
    # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S11
    continuation = bool(cursor)
    try:
        # (3) 노드 폴백(title) — FTS/벡터와 무관하게 항상. 실패해도 Chunk 경로 무영향.
        # E-G1: 여기서는 title 폴백만 담고, Chunk 하이브리드 뒤 그래프 암과 융합한다.
        title_hits: list[dict] = []
        _vstats: dict = {}
        # 시점 읽기(as_of). 기간이 없으면 오늘 규칙 그대로다 — 기본값에서는 한 바이트도
        # 안 바뀐다. 무엇으로 읽었는지는 응답에 싣는다(`time_scope`): 과거를 읽은 답이
        # 오늘의 답처럼 읽히면 이 기능이 막으려는 고장과 같은 종이다.
        if _scope_info:
            base["time_scope"] = _scope_info
        _vnote = (f"hid nodes whose claim did not hold during {_scope_info['as_of']}; "
                  "drop as_of for today's view"
                  if _scope is not None else
                  "hid nodes whose claim is no longer current; "
                  "pass historical=true to include them")
        try:
            if not continuation:
                title_hits = _node_fallback_search(store, query, limit=NODE_SCAN_CAP if mode == "current" else top_k, owner=owner,
                                                   historical=True if mode == "current" else historical, stats=_vstats,
                                                   scope=None if mode == "current" else _scope)
                if mode == "current":
                    temporal.finish_nodes(store, title_hits, owner=owner, scope=_scope, current=True, known_at=known_at, stats=_vstats)
                    if _vstats.get("current_hidden"):
                        base["validity_filtered"] = len(_vstats["current_hidden"])
                    if known_at:
                        base["known_at_filtered"] = len(_vstats.get("known_hidden", ()))
                        base["known_at_unknown"] = len(_vstats.get("known_unknown", ()))
        except Exception as exc:
            base["node_error"] = f"{type(exc).__name__}: {exc}"
        # 걸러 냈으면 **말한다**. 조용히 줄어든 결과는 "그래프에 이것뿐"으로 읽히고,
        # 그건 이 필터가 막으려는 고장과 같은 종이다(`vector_note` 와 같은 태도).
        if _vstats.get("dropped_not_current"):
            base["validity_filtered"] = _vstats["dropped_not_current"] + len(_vstats.get("current_hidden", ()))
            base["validity_best_dropped"] = list(_vstats.get("best_dropped_score") or (0, 0))
            base["validity_note"] = _vnote
        base["node_hits"] = title_hits   # 그래프 암 실패해도 최소 title 폴백은 보장

        # (1)(2) Chunk 하이브리드 — 실패는 error 로 표면화, 인덱스 부재는 지연 빌드 후 재시도.
        # GM-14: top_k 가 아니라 **후보 풀**(SEARCH_POOL_CAP)을 가져온다 — 페이지는
        # 아래에서 안정 정렬 위의 오프셋으로 자른다.
        # 🔴 **SEC-1 청크 팔 owner 스코프.** owner 를 여기서 빼면 호스티드에서 `rows[]`
        # 가 남의 문서 본문 발췌를 싣는다 — node_hits 가 막혀 있어 눈에 잘 안 띄고
        # 회귀 테스트도 노드 폴백만 봤다([[record:the-search-chunk-arm-is-owner-scoped-and-fails-closed]]).
        chunk_filters = dict(filters or {})
        base["node_window"] = {"pagination": "first_page_only", "total_count_scope": "chunks",
                               "limit": max(1, int(top_k)),
                               "lexical_candidates": _vstats.get("candidate_count"),
                               "lexical_candidates_capped": bool(_vstats.get("candidate_count_capped")),
                               "candidate_limit": NODE_SCAN_CAP}
        scoped_ok = True
        if owner:
            chunk_filters["owner_id"] = owner
            # **fail-closed.** 스코프가 강제된 요청인데 백엔드가 필터를 실제로 적용하는지
            # 알 수 없으면 청크 팔을 아예 돌리지 않는다. 필터를 무시하는 구현(구 백엔드·
            # 테스트 더블)에 owner 를 넘기면 "스코프를 걸었다고 믿는 무스코프 조회"가
            # 되는데, 그건 지금 고치는 결함과 정확히 같은 것이다. 조용히 넓게 답하느니
            # 답을 줄이고 사유를 남긴다.
            scoped_ok = bool(getattr(store, "scoped_chunk_search", False))
        pool: list[dict] = []
        vnote = ""
        ran: list[str] = []          # 실제로 돈 팔 — 예외로 새면 빈 채로 남는다
        arm_capped = False           # 거르기 전의 팔이 상한에 닿았나(_chunk_arms)
        kw_gated = False             # 키워드 목록이 관문에 걸려 융합에서 빠졌나(_keyword_gate)
        try:
            if not scoped_ok:
                raise _UnscopedChunkSearch(
                    "owner scope requested but this store does not support "
                    "owner-filtered chunk search")
            pool, vnote, ran, arm_capped, kw_gated = _chunk_arms(
                store, query, query_embedding, SEARCH_POOL_CAP, chunk_filters or None)
            base["executed"] = True
        except _UnscopedChunkSearch as exc:
            # 폴백하지 않는다 — node_hits 는 이미 owner 로 걸러져 있으므로 그것만 답한다.
            base["error"] = f"owner_scope_unsupported: {exc}"
            base["note"] = base["error"]
        except Exception as exc:
            if _classify_chunk_error(exc) != "index_missing":
                base["error"] = f"{_classify_chunk_error(exc)}: {type(exc).__name__}: {exc}"
                base["note"] = base["error"]
            else:
                n = _chunk_count(store)
                builder = getattr(store, "ensure_search_indexes", None)
                if n == 0:
                    # Chunk 0 — 빌드 시도 없이 조용히 폴백(node_hits 가 답, 무 에러).
                    base["executed"] = True
                    base["note"] = "no chunks indexed — node_hits only"
                elif getattr(store, "fts_index_remnant", False):
                    # 다시 서지 않는 색인이다(잔해). 빌드를 걸면 검색마다 실패할 잡이 돈다.
                    # 근거: [[record:an-fts-remnant-on-chunk-stops-every-document-write-and-the-heal-moves-chunk-with-its-edges-2026-09-25]]
                    base["error"] = base["note"] = f"fts_index_remnant: {_FTS_REMNANT}"
                elif builder is None:
                    base["error"] = f"index_missing: {type(exc).__name__}: {exc}"
                    base["note"] = base["error"]
                else:
                    try:
                        # **벡터 인덱스는 여기서 만들지 않는다.** FTS 만 세워 답하고
                        # (`dim=None`), HNSW 는 아래에서 예약한다 — 요청 스레드가
                        # 기다릴 일이 아니다(:func:`_schedule_vector_index`).
                        builder(dim=None)
                        pool, vnote, ran, arm_capped, kw_gated = _chunk_arms(
                            store, query, query_embedding, SEARCH_POOL_CAP,
                            chunk_filters or None)
                        base["executed"] = True
                        base["note"] = "search index built on demand"
                    except Exception as exc2:
                        base["error"] = (f"index_build_failed: "
                                         f"{type(exc2).__name__}: {exc2}")
                        base["note"] = base["error"]
        # 여기서 처음으로 `arms` 를 세운다 — **돈 팔의 이름만** 붙는다.
        base["arms"] = "+".join(_ARM_LABEL[a] for a in ran) or "none"
        # 키워드 팔은 돌았지만 질의를 못 덮어 융합에서 뺐다 — 걸렸을 때만 싣는다(`vector_note`
        # 처럼). `arms` 는 그대로 두 팔이다: 평가 하네스의 강등 판정이 바뀌지 않게.
        if kw_gated:
            base["keyword_gated"] = True
        # 벡터 팔을 **요청했는데 안 돌았고 그 이유가 실패**면 사유를 구조화 필드로
        # 남긴다. 여태 이 자리는 `error` 만 세웠는데, 화면은 영문 문구를 파싱할 수
        # 없으므로(금지) 반쪽·빈 답을 완전한 답으로 보여 줬다. 청크 팔이 통째로
        # 죽는 경로(확장 미가용·미프로비저닝·스코프 미지원·빌드 실패)가 전부 여기다.
        # 청크가 0개라 아무것도 못 찾은 경우는 실패가 아니므로(`error` 없음) 안 선다.
        if query_embedding is not None and "semantic" not in ran and base.get("error"):
            base.setdefault("vector_note", base["error"])
        if vnote and getattr(store, "vector_index_remnant", False):
            # 재빌드 창이 아니다 — 다시 서지 않는 색인이다. 양성 표식(`vector_rebuilding`)을
            # 달면 "곧 돌아온다"는 거짓이 되므로 실패로 싣고, 빌드도 걸지 않는다.
            # 근거: [[record:a-crash-between-a-vector-index-drop-and-the-next-checkpoint-leaves-a-remnant-2026-09-25]]
            base["vector_note"] = _VECTOR_REMNANT
        elif vnote and vnote != _VECTOR_REBUILDING:
            base["vector_note"] = vnote      # 벡터 팔 실패(폭 불일치 등) — 재빌드 창이 아니다
        elif vnote:
            # 조용한 강등 금지 — 어느 팔이 돌았는지(`arms`)와 왜 강등됐는지
            # (`vector_note`)를 둘 다 남긴다.
            base["vector_note"] = vnote
            # 재빌드 창은 **양성 계약**이다(쓰기가 HNSW 를 드롭 → 배경에서 다시 선다).
            # 화면이 이것을 진짜 실패와 같은 톤으로 그리면 거짓 경보가 되므로 구조화
            # 표식으로 가른다 — 문구 파싱은 금지다(_VECTOR_REBUILDING 주석과 같은 규칙).
            base["vector_rebuilding"] = True
            _schedule_vector_index(store, cfg.embedding_dim)

        # GM-14 페이지네이션 — 안정 정렬(점수 내림차순, 동점은 id 오름차순). 페이지
        # 사이에 결과가 흔들리면 안 되므로 정렬 키에 id 를 반드시 묶는다.
        pool.sort(key=lambda r: (-(float(r.get("rrf_score") or r.get("score") or 0.0)),
                                 str(r.get("id") or "")))
        if mode == "current":
            for r in pool:
                rid = str(r.get("id") or "")
                r["doc_id"] = r.get("doc_id") or (rid.rsplit("#", 1)[0] if "#" in rid else "")
            try:
                meta = temporal.doc_sources(store, [r.get("doc_id") for r in pool], owner=owner)
            except Exception as exc:
                meta = {}
                base["partial"] = True
                base["temporal_error"] = f"{type(exc).__name__}: {exc}"
            for r in pool:
                temporal.stamp(r, meta.get(r.get("doc_id"), {}), scope=_scope)
            visible = [r for r in pool if r["temporal"]["validity"] not in ("historical", "future")]
            base["validity_filtered"] = int(base.get("validity_filtered") or 0) + len(pool) - len(visible)
            base["doc_validity_filtered"] = len(pool) - len(visible)
            pool = visible
            before_lineage = len(pool)
            try:
                temporal.finish_docs(store, pool, owner=owner, scope=_scope, current=True, known_at=known_at)
                base["validity_filtered"] += before_lineage - len(pool)
                base["doc_validity_filtered"] += before_lineage - len(pool)
            except Exception as exc:
                base["partial"] = True
                base["temporal_error"] = f"{type(exc).__name__}: {exc}"
            temporal.current_order(pool)
            # Keep the assessed cohort until the public wrapper reconstructs page rows.
            # A display limit must not erase a conflict already observed in retrieval.
            base["_current_doc_temporal"] = {r["doc_id"]: r["temporal"] for r in pool}
        base["total_count"] = len(pool)
        # 풀은 SEARCH_POOL_CAP 에서 잘린다 — 그 수에 닿았으면 total_count 는 "전부"가
        # 아니라 "적어도"다. 여태 거의 모든 질의가 200·199 를 읽혀 전체 수처럼 보였다
        # (감사 §5 #18). 필드 이름은 안 바꾸고, 닿았을 때만 표식을 단다.
        # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S11
        if arm_capped or len(pool) >= SEARCH_POOL_CAP:
            base["total_count_capped"] = True
        # 청크 팔이 실패했으면 풀이 비어 모든 커서가 낡아 보인다 — 원래 오류를 그대로 둔다.
        # 1페이지와 다른 팔이 돌았으면(벡터 색인이 사이에 드롭) 순서가 다른 풀이다 — eid 가
        # 우연히 맞아도 중복·누락이 난다.
        if cursor and not base.get("error") and (
                off >= len(pool)
                or (want_arms is not None and want_arms != base["arms"])
                or (want_eid is not None
                    and str(pool[off].get("id") or "") != str(want_eid))):
            # 결과가 커서 발급 시점과 달라졌다 — 조용히 어긋난 페이지를 주지 않는다.
            base["note"] = base["error"] = (
                "stale_cursor: results changed since this cursor was issued; "
                "restart from the first page")
            base["executed"] = False
            return base
        page = pool[off:off + max(1, int(top_k))]
        next_off = off + len(page)
        base["has_more"] = next_off < len(pool)
        if base["has_more"]:
            base["next_cursor"] = cursor_encode(
                {"v": 1, "off": next_off, "qh": qh,
                 "eid": str(pool[next_off].get("id") or ""), "tc": len(pool),
                 "arms": base["arms"]})
        if detail == "full":
            # 전문도 문서 유효구간으로 거른다. 원본 풀의 순위·커서 좌표는 보존한다.
            base["rows"] = [dict(r) for r in page]
            for r in base["rows"]:
                rid = str(r.get("id") or "")
                r["doc_id"] = rid.rsplit("#", 1)[0] if "#" in rid else ""
        else:
            from store.lexical import lex_tokens
            qtok = lex_tokens(query)              # 페이지에 한 번 — 행마다 다시 자르지 않는다
            base["rows"] = [_summary_row(r, qtok) for r in page]
        try:
            _drop_retired_doc_rows(base, _attach_doc_titles(
                store, base["rows"], owner=owner, include_titles=detail != "full"),
                historical=True if mode == "current" else historical,
                scope=None if mode == "current" else _scope)
        except Exception as exc:
            base["partial"] = True
            base["temporal_error"] = f"{type(exc).__name__}: {exc}"
        # MCP 봉투의 크기 안전망(shrinker)이 이어받을 좌표 — 꼬리 행을 덜어낸 뒤
        # 같은 지문으로 next_cursor 를 다시 쓸 수 있게 한다.
        base["page"] = {"offset": off, "limit": max(1, int(top_k)), "fingerprint": qh}
        if base.get("doc_validity_filtered") and base["rows"]:
            # 축소기는 필터 뒤 행 수가 아니라 덜어낸 행의 원본 풀 위치로 이어 읽는다.
            visible_ids = {str(r.get("id") or "") for r in base["rows"]}
            base["page"]["row_offsets"] = {
                str(r.get("id") or ""): off + i for i, r in enumerate(page)
                if str(r.get("id") or "") in visible_ids}

        # (4-a) v17 의미 암 — 노드 미러(NodeVec)를 질의 벡터로 찾는다. **어휘가 하나도
        # 안 겹쳐도 닿는 유일한 팔**이다: 나머지 두 팔은 토큰 겹침(title)이거나 청크
        # 순위에 얹혀 있어(graph), 패러프레이즈를 못 건넌다.
        #
        # 기본은 **켜져 있다**(`VINC_NODE_SEMANTIC_ARM=off` 가 비상구). 미러가 비면
        # 빈 목록이라 켜 둬도 안 깨지고, 미러는 배포 경계에서 전량 재구축된다.
        # 뒤집힌 경위와 수치는 `_node_semantic_arm_on` 에 있다.
        vec_hits: list[dict] = []
        _sem_decisive: str | None = None
        _sem_lead: tuple | None = None       # (의미 1위 id, 2위와의 거리 차)
        if query_embedding is not None and not continuation and not _node_semantic_arm_on():
            base["node_semantic"] = {"state": "disabled", "freshness": "not_checked",
                                     "refresh": "release_boundary", "scope": "returned_candidates"}
        if query_embedding is not None and not continuation and _node_semantic_arm_on():
            base["node_semantic"] = {"state": "complete", "freshness": "not_checked",
                                     "refresh": "release_boundary", "scope": "returned_candidates"}
            try:
                # 라벨은 **제목**이다. 미러의 `text` 는 찾는 표면이라 한줄설명이 붙어
                # 있고(1365 중 225 행), 그것을 title 자리에 넣으면 그대로 화면에 나간다.
                _raw = store.node_vector_search(
                    query_embedding, SEARCH_POOL_CAP if mode == "current" else top_k, owner=owner) or []
                validator = getattr(store, "validate_node_vector_hits", None)
                if callable(validator):
                    _raw, state = validator(_raw, owner=owner)
                    base["node_semantic"].update(state)
                    if state.get("stale_candidates") or state.get("missing_candidates"):
                        base["node_semantic"]["state"] = "partial"
                if not _raw and not any(base["node_semantic"].get(k) for k in ("stale_candidates", "missing_candidates")):
                    counter = getattr(store, "node_vector_count", None)
                    if callable(counter):
                        count = counter(owner=owner)
                        base["node_semantic"].update(stored_vectors=count,
                            stored_vectors_scope="owner" if owner else "local_unscoped")
                        if count == 0:
                            base["node_semantic"].update(state="empty_mirror", freshness="not_checked")
                # 유효구간 기본 필터(§4.2) — 이 팔은 미러를 읽어 구간 열이 없으므로 id 로
                # 되찾아 본다. 제목 팔만 걸러 내면 폐기된 노드가 **이 팔로 되돌아온다**
                # (라이브 재현 2026-09-11: 제목 팔이 걸러 낸 d44 가 semantic 표로 다시
                # 올라왔다). 단호함 판정(_SEM_GAP)보다 앞에 두어 폐기된 1위가 팔을
                # 단호하게 만들지 못하게 한다. 지운 수는 제목 팔과 같은 자리에 더하고,
                # **판정도 제목 팔과 같다** — `historical` 만 거른다(사유는 그쪽 주석).
                if (_scope is not None or not historical) and _raw:
                    _vf = _validity_for_ids(
                        store, [h.get("id") for h in _raw if h.get("id")], owner=owner)
                    _keep = []
                    for h in _raw:
                        if _visible(*_vf.get(h.get("id"), ("", "")),
                                    historical=historical, scope=_scope):
                            _keep.append(h)
                        else:
                            _gone = _vstats.setdefault("dropped_ids", set())
                            _gone.add(h.get("id"))
                            _vstats["dropped_not_current"] = len(_gone)
                    _raw = _keep
                vec_hits = [{"id": h.get("id"),
                             "title": h.get("title") or h.get("text") or "",
                             "kind": h.get("kind") or "", "domain": "",
                             "matched_in": "semantic", "hit_tokens": 0,
                             "title_tokens": 0, "token_total": 0,
                             "distance": h.get("distance")}
                            for h in _raw]
                # 도메인은 미러에 없다. 빈 문자열로 두면 "무주제"로 읽히므로 채운다
                # (사유는 `_domains_for_ids` 주석).
                _doms = _domains_for_ids(
                    store, [h["id"] for h in vec_hits if h.get("id")], owner=owner)
                for _h in vec_hits:
                    _h["domain"] = _doms.get(_h.get("id"), "")
                # 1위가 2위보다 `_SEM_GAP` 이상 가까우면 **이 팔이 자기 기준으로
                # 단호하다**. 절대 거리가 아니라 격차인 이유는 그 상수 주석에 있다.
                # 목록이 거리 오름차순이라 이 격차가 곧 1위가 **나머지 전체**보다 앞선
                # 정도(의미 우위)이고, brief 의 승격(`_SEM_PROMOTE`)이 같은 값을 읽는다.
                # 융합이 top_k 로 잘라 2위 행을 떨굴 수 있으므로 node_hits 가 아니라
                # 여기(원시 목록)서 잰다. 반올림은 X3 모의와 같은 자리수다.
                if len(_raw) > 1:
                    try:
                        _gap = float(_raw[1]["distance"]) - float(_raw[0]["distance"])
                        _sem_lead = (_raw[0].get("id"), round(_gap, 6))
                        if _node_sem_rank_on() and _gap >= _SEM_GAP:
                            _sem_decisive = _raw[0].get("id")
                    except (TypeError, ValueError, KeyError):
                        pass
            except Exception as exc:
                base.setdefault("node_error", f"{type(exc).__name__}: {exc}")
                code = str(exc) if str(exc) in ("node_vector_extension_unavailable",
                    "node_vector_query_failed", "node_vector_validation_failed",
                    "node_vector_status_unavailable") else "node_semantic_failed"
                base["node_semantic"].update(state="degraded", error=code)
        # 의미 팔이 더 걸러 냈으면 (3) 에서 적은 공개를 갱신한다 — 셈은 두 팔이 지운 id 의
        # 합집합이다(두 팔이 같은 노드를 지우면 한 번).
        if _vstats.get("dropped_not_current"):
            base["validity_filtered"] = (_vstats["dropped_not_current"]
                                         + int(base.get("doc_validity_filtered") or 0)
                                         + len(_vstats.get("current_hidden", ())))
            base.setdefault("validity_best_dropped",
                            list(_vstats.get("best_dropped_score") or (0, 0)))
            base.setdefault("validity_note", _vnote)

        # 이어 읽기에서도 벡터 팔은 다시 돈다. 첫 페이지와 같은 런타임 경고를
        # 반환하기 전에 계산해 두어야 한다.
        if query_embedding is not None and query_runtime is not None:
            runtime_note = _encoder_runtime_warning(store, owner, query_runtime)
            if runtime_note:
                old_note = base.get("vector_note")
                base["vector_note"] = (f"{old_note}; {runtime_note}" if old_note
                                       else runtime_note)
                base["encoder_runtime_warning"] = True

        if relevance_profile is not None:
            state = profile_state(relevance_profile, encoder_model=cfg.embedding_model,
                                  query_runtime=query_runtime,
                                  corpus_revision=relevance_corpus_revision,
                                  runtime_warning=bool(base.get("encoder_runtime_warning")),
                                  owner=owner)
            base["relevance_policy"] = state
            if state["state"] == "active":
                base["rows"] = classify_rows(base["rows"], relevance_profile, kind="chunk")

        # (4-b) E-G1 그래프 암 — 상위 Chunk 의 :MENTIONS 이웃 개념을 title 폴백과 융합.
        # 실패는 검색을 막지 않는다(그래프 암은 증강 — 부재 시 title 만, 하위호환).
        if continuation:
            # 이어 읽기 페이지 — 노드 팔을 돌리지 않았다(위 `continuation` 주석).
            base["node_hits"] = []
            base["node_hits_omitted"] = "first_page_only"
            return base
        try:
            chunk_ids = [r.get("id") for r in base.get("rows", []) if r.get("id")
                         and r.get("relevance", {}).get("level") != "weak"]
            graph_hits = _graph_arm_search(store, chunk_ids, owner=owner, limit=top_k)
            base["node_hits"] = _fuse_node_hits(title_hits, graph_hits, top_k=NODE_SCAN_CAP if mode == "current" else top_k,
                                                vec_hits=vec_hits)
            # 융합은 id 당 행 하나만 남기므로(제목 행이 이기면 의미 행의 표가 사라진다)
            # 융합 **뒤에** id 로 찾아 단다. 거리도 같다 — 안 달면 제목 팔도 찾은 노드는
            # 의미 거리를 잃는다. 다는 값은 순위에 쓰이지 않는다.
            # 근거: [[record:node-hits-lose-the-semantic-distance-when-the-title-arm-also-found-the-node-2026-09-29]]
            _dist = {h["id"]: h.get("distance") for h in vec_hits if h.get("id")}
            for _r in base["node_hits"]:
                _rid = _r.get("id")
                if _rid in _dist:
                    _r["distance"] = _dist[_rid]
                if _sem_lead and _rid == _sem_lead[0]:
                    _r["sem_lead"] = _sem_lead[1]
                if _sem_decisive and _rid == _sem_decisive:
                    _r["sem_decisive"] = True
        except Exception as exc:
            base.setdefault("node_error", f"{type(exc).__name__}: {exc}")
            base["node_hits"] = _fuse_node_hits(title_hits, [], top_k=NODE_SCAN_CAP if mode == "current" else top_k,
                                                vec_hits=vec_hits)
    finally:
        if owns:
            store.close()
    if relevance_profile is not None and base.get("relevance_policy", {}).get("state") == "active":
        base["node_hits"] = classify_rows(base["node_hits"], relevance_profile, kind="node")
    return base


def search(query: str, *, mode: str | None = None, known_at: str | None = None, **kwargs) -> dict:
    """모든 결과 경로에 시간 메타를 붙인다. 기본 순위와 저장 데이터는 보존한다."""
    try:
        temporal.mode_scope(mode, kwargs.get("historical", False), kwargs.get("as_of"), query)
        if known_at and not temporal.time_scope(known_at):
            raise ValueError("invalid known_at")
    except ValueError as exc:
        prefix = "invalid_time_scope" if "known_at" in str(exc) else "invalid_temporal_mode" if mode else "invalid_as_of"
        return {"kind": "search", "executed": False, "rows": [], "node_hits": [], "arms": "none", "total_count": 0, "has_more": False, "next_cursor": None, "error": f"{prefix}: {exc}"}
    store = kwargs.get("store")
    owns = store is None
    if owns:
        store = open_store(kwargs.get("cfg") or load_config())
    if store is None:
        return _search(query, mode=mode, known_at=known_at, **kwargs)
    try:
        res = _search(query, mode=mode, known_at=known_at, **{**kwargs, "store": store})
        doc_states = res.pop("_current_doc_temporal", {})
        if not res.get("error", "").startswith(("invalid_", "stale_cursor")):
            try:
                _, scope, _ = temporal.mode_scope(mode, kwargs.get("historical", False), kwargs.get("as_of"), query)
                stats = {}
                temporal.finish_nodes(store, res.get("node_hits") or [], owner=kwargs.get("owner"), scope=scope, current=mode == "current", known_at=known_at, stats=stats)
                hidden = len(stats.get("current_hidden", ()))
                if hidden:
                    res["validity_filtered"] = int(res.get("validity_filtered") or 0) + hidden
                if mode == "current":
                    res["node_hits"] = res.get("node_hits", [])[:kwargs.get("top_k", 5)]
                if res.get("validity_filtered"):
                    res.setdefault("validity_note", "some claims are outside the selected time scope; use historical mode to include them")
                for row in res.get("rows") or []:
                    if "temporal" not in row:
                        temporal.stamp(row, {})
                if mode == "current":
                    for row in res.get("rows") or []:
                        if row.get("doc_id") in doc_states:
                            row["temporal"] = dict(doc_states[row["doc_id"]])
                else:
                    temporal.finish_docs(store, res.get("rows") or [], owner=kwargs.get("owner"),
                                         scope=scope, known_at=known_at)
                if known_at:
                    # 생성시각 컷오프일 뿐, 과거 본문 revision을 복원하지 않는다.
                    until = temporal.instant(temporal.time_scope(known_at)[1])
                    hidden = int(res.get("known_at_filtered") or 0) + len(stats.get("known_hidden", ()))
                    unknown = set(stats.get("known_unknown", ()))
                    for key in ("rows", "node_hits"):
                        kept = []
                        for row in res.get(key) or []:
                            created = temporal.instant(row["temporal"]["created_at"])
                            if created is None:
                                unknown.add(row.get("doc_id") or row.get("id"))
                            if created is not None and created >= until:
                                hidden += 1
                            else:
                                kept.append(row)
                        res[key] = kept
                    res["known_at_filtered"], res["known_at_unknown"] = hidden, len(unknown)
                    res.setdefault("time_scope", {})["known_at"] = {"value": known_at,
                        "from": temporal.time_scope(known_at)[0], "until": temporal.time_scope(known_at)[1]}
            except Exception as exc:
                res["partial"] = True
                res["temporal_error"] = f"{type(exc).__name__}: {exc}"
                for row in (res.get("node_hits") or []) + (res.get("rows") or []):
                    temporal.stamp(row, {})
        if "node_window" in res:
            res["node_window"]["returned"] = len(res.get("node_hits") or [])
        return res
    finally:
        if owns:
            store.close()
