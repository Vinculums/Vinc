# -*- coding: utf-8 -*-
"""노드 읽기: 검색 파사드(`search`)와 노드 상세(`node_context`)와 그 관계 목록.

`search()` 는 `vinc_rag.retrieval.search` 의 순위·페이지를 그대로 두고 인용 id(`cite_id`)를 붙이며, as_of 기간
뒤에 시작한 사건을 뺀다(`_drop_unstarted_episodes`). `node_context()` 는 `_node_context` 의 답(설명·발췌·문서
목록·관계 창)에 검색과 같은 시간 메타를 붙인다. 관계 창은 종류마다 `_node_links`·`_record_links`·
`_episode_links`·`_entity_links` 가 만들고, `domain:` 좌표는 `_domain_context` 가 `neighbors` 의
`_domain_neighbors` 로 답한다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.X` 와 `from api.query import X` 는 그대로다.
두 이름은 같은 객체다. 여기 함수가 부르는 이름(`execute`·`open_store`·`_retrieval` 등)은 이 모듈에서
찾으므로, `search()`·`node_context()` 안의 호출을 바꾸려면 `api.query` 가 아니라 이 모듈의 이름을 패치한다.

이 모듈은 `api.query` 를 import 하지 않는다. 바닥은 `api/query_base.py`, 한 홉 관측은 `api/neighbors.py`
에서 읽는다(`tests/core/test_node_read.py` 가 둘을 본다).
"""
from __future__ import annotations

from engines import domain_hierarchy
from store import pkns
from store.backend import flag, open_store, operation
from store.config import load_config
from store.querybase import _VALIDITY_EMPTY, _classify_chunk_error, _validity_cols, holds_during
from vinc_rag import excerpt as _excerpt
from vinc_rag import retrieval as _retrieval
from vinc_rag.retrieval import _FTS_REMNANT, _chunk_count

from .neighbors import _domain_neighbors, _typed_neighbors_cypher
from .query_base import (
    _GRAPH_READ_PAIRS,
    _SEMANTIC_LABELS,
    _SEMANTIC_PK,
    _bridge_owner_pred,
    _props_to_obj,
    _seed_table,
    _stamp_validity,
    _topic_description_mirrors_by_domain,
    _track_cols,
    execute,
    node,
    node_plan,
)


def _drop_unstarted_episodes(result: dict, query: str, *, as_of, mode, historical) -> None:
    """as_of 기간 뒤에 일어난 사건을 node_hits 에서 뺀다(감사 F14).

    사건의 날짜는 그 시작이고 끝나지 않는다 — `api.temporal_read` 가 neighbors·graph·
    recall 을 그렇게 거른다. 검색 코어는 사건을 구간 없음으로 읽어 늘 남겼고, 그래서 같은
    as_of 요청에서 검색만 그 사건을 냈다. 날짜는 검색이 이미 단 시간 메타(content_time)에
    있어 스토어를 다시 읽지 않는다. 날짜가 없으면 감추지 않는다.
    감춘 행은 다른 구간 필터와 같은 자리에 센다(validity_filtered·validity_best_dropped)
    — brief 의 동점 가드가 같은 규칙으로 본다."""
    if mode == "current" or not (as_of or "").strip() or str(result.get("error") or "").startswith(
            ("invalid_", "stale_cursor")):
        return
    try:
        from store.search_temporal import mode_scope
        _, scope, info = mode_scope(mode, historical, as_of, query)
    except ValueError:
        return
    if scope is None:
        return
    kept, best = [], tuple(result.get("validity_best_dropped") or (0, 0))
    hidden = 0
    for hit in result.get("node_hits") or []:
        date = str((hit.get("temporal") or {}).get("content_time") or "")
        if (hit.get("kind") == "episode" or str(hit.get("id") or "").startswith("episode:")) \
                and date and not holds_during(date, "", scope):
            hidden += 1
            # 축별 최댓값으로 모은다(검색 코어의 제목 팔과 같은 규칙, 동점 가드가 읽는다).
            best = (max(best[0], int(hit.get("hit_tokens") or 0)),
                    max(best[1], int(hit.get("title_tokens") or 0)))
            continue
        kept.append(hit)
    if not hidden:
        return
    result["node_hits"] = kept
    result["validity_filtered"] = int(result.get("validity_filtered") or 0) + hidden
    result["validity_best_dropped"] = list(best)
    result.setdefault("validity_note",
                      f"hid nodes whose claim did not hold during {info['as_of']}; "
                      "drop as_of for today's view")
    if isinstance(result.get("node_window"), dict):
        result["node_window"]["returned"] = len(kept)


def _doc_cite_ids(store, owner: str | None, doc_ids) -> dict[str, str]:
    """문서 키와 인용할 노드 id는 다르다. 읽을 수 있는 미러만 인용한다."""
    ids = {f"concept:doc.{doc_id}" for doc_id in doc_ids if doc_id}
    if not ids or store is None:
        return {}
    where = "c.concept_id IN $ids"
    params = {"ids": pkns.keys(owner, sorted(ids))}
    if owner:
        where += " AND c.owner_id = $owner"
        params["owner"] = owner
    rows = store.run(f"MATCH (c:Concept) WHERE {where} "
                     "RETURN c.concept_id AS id", params) or []
    return {nid[len("concept:doc."):]: nid for row in rows
            if (nid := pkns.strip(str(row.get("id") or ""))) in ids}


def _cite_doc_rows(rows: list[dict], cites: dict[str, str]) -> list[dict]:
    # 행은 보존한다. 미러가 없을 때 doc 키를 노드 id로 꾸미지 않는다.
    return [{**row, "cite_id": cites.get(str(row.get("doc_id") or "")),
             **({"cite_unavailable": "document mirror missing or unreadable"}
                if str(row.get("doc_id") or "") not in cites else {})}
            for row in rows]


def search(query: str, *, query_embedding: list[float] | None = None,
           top_k: int = 5, filters: dict | None = None, owner: str | None = None,
           cursor: str | None = None, detail: str = "summary",
           historical: bool = False, as_of: str | None = None,
           mode: str | None = None, known_at: str | None = None,
           query_runtime: str | None = None, query_model: str | None = None,
           query_colbert: str | None = None, kinds=None,
           cfg=None, store=None) -> dict:
    """검색 순위와 페이지는 그대로 두고 공개 응답에 정확한 인용 id를 붙인다.

    as_of 기간이 있으면 그 기간이 끝난 뒤에 시작한 사건(Episode)을 node_hits 에서 뺀다
    (:func:`_drop_unstarted_episodes`). 다른 종류의 구간 검사는 검색 코어가 이미 한다."""
    result = _retrieval.search(
        query, query_embedding=query_embedding, top_k=top_k, filters=filters,
        owner=owner, cursor=cursor, detail=detail, historical=historical,
        as_of=as_of, mode=mode, known_at=known_at, query_runtime=query_runtime,
        query_model=query_model, query_colbert=query_colbert, cfg=cfg, store=store,
        **({"kinds": kinds} if kinds else {}))
    result["node_hits"] = [{**hit, "cite_id": hit.get("id") or None}
                           for hit in result.get("node_hits") or []]
    _drop_unstarted_episodes(result, query, as_of=as_of, mode=mode, historical=historical)
    rows = result.get("rows") or []
    if not rows:
        return result
    owns = store is None
    try:
        if owns:
            store = open_store(cfg or load_config())
        cites = _doc_cite_ids(store, owner, [row.get("doc_id") for row in rows])
        result["rows"] = _cite_doc_rows(rows, cites)
    except Exception as exc:
        result["rows"] = _cite_doc_rows(rows, {})
        result["citation_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns and store is not None:
            try:
                store.close()
            except Exception as exc:
                result.setdefault("citation_error", f"{type(exc).__name__}: {exc}")
    return result


#: 문서 목록의 응답별 상한. sources_offset 으로 다음 창을 읽는다.
_NODE_SOURCES_CAP = 50


def node_context(**kwargs) -> dict:
    """상세·요약도 검색과 같은 시간 메타를 보존한다."""
    from store import search_temporal as temporal
    store = kwargs.get("store")
    owns = store is None
    if owns:
        store = open_store(kwargs.get("cfg") or load_config())
    if store is None:
        return _node_context(**kwargs)
    try:
        res = _node_context(**{**kwargs, "store": store})
        if res.get("executed") and res.get("node_kind") != "domain":
            try:
                owner, nid = kwargs.get("owner"), kwargs["node"]
                source = temporal.node_sources(store, [nid], owner=owner).get(nid, res)
                temporal.stamp(res, source)
                candidates = [{"id": nid, "kind": source.get("kind", res.get("node_kind"))}]
                temporal.finish_nodes(store, candidates, owner=owner)
                res["temporal"] = candidates[0]["temporal"]
                excerpts = res.get("excerpts") or []
                docs = temporal.doc_sources(store, [r.get("doc_id") for r in excerpts], owner=owner)
                for row in excerpts:
                    temporal.stamp(row, docs.get(row.get("doc_id"), {}))
                temporal.finish_docs(store, excerpts, owner=owner)
                items = (res.get("links") or {}).get("items") or []
                temporal.finish_nodes(store, items, owner=owner)
                for item in items:
                    # F14 — 결정의 구간은 props 에 있어 관계 행의 두 칸이 비어 나갔다.
                    # 시간 메타가 이미 읽은 값으로 채운다(Record 는 열에서 이미 채웠다).
                    if str(item.get("id") or "").startswith("decision:"):
                        t = item.get("temporal") or {}
                        item["valid_from"] = str(t.get("valid_from") or "")
                        item["valid_until"] = str(t.get("valid_until") or "")
            except Exception as exc:
                temporal.stamp(res, res)
                res["partial"] = True
                res["temporal_error"] = f"{type(exc).__name__}: {exc}"
            res["validity_basis"] = "legacy_visibility"
            res["claim_validity"] = res["temporal"]["validity"]
        return res
    finally:
        if owns:
            store.close()


def _node_context(*, owner: str, node: str, limit: int = 4,
                 with_evidence: bool = False, links_offset: int = 0,
                 sources_offset: int = 0, cfg=None, store=None) -> dict:
    """노드→정본 컨텍스트(D45-V) — 뷰에서 노드를 눌렀을 때 보여줄 "이건 무엇이고,
    어느 정본이 근거인가". 조회 전용.

    1) 노드 자체 설명: Concept 이면 one_liner, Episode 면 summary.
    2) 근거 발췌: (ch:Chunk)-[:MENTIONS]->(c) 역참조 — 이 개념을 언급하는 정본
       청크의 발췌 + 문서 제목(_DocMeta 조인). 청크 owner 는 과도기 술어
       (owner OR '' — 소유가 빈 파일 파생 청크도 받는다, Bridge F5 전례와 동일).
    3) MENTIONS 가 비면 제목 FTS 폴백(arm='fts') — 링크가 아직 없어도 "관련
       정본"을 준다. 확장 미가용이면 조용히 빈 발췌(arm='none', 실패 아님 —
       발췌는 증강이지 상세 패널의 성립 조건이 아니다).

    4) 연결 요약 + 관계 목록(``links``) — 상세 레일이 쓰는 **수치 정본**. 여태
       프론트가 "마지막으로 그린 그래프의 엣지 집합"(스테일 잔상)에서 세고, 드릴다운
       경로로 열면 degree 를 0 으로 박아 "연결 0개 · → 3 나감"처럼 자기모순을 냈다.
       세는 자리를 노드 자신으로 옮겨 어느 동선으로 열어도 같은 숫자가 나온다.

    반환: {executed, node, node_kind, title, domain, one_liner, rev, updated_at, props,
           valid_from, valid_until, validity('current'|'historical'|'future'), superseded_by,
           excerpts: [{doc_id, doc_title, section, excerpt, method}], arm,
           links: {degree(=out+in), out, in, attached(=degree 에 안 든 나머지),
                   total(=degree+attached · vinc_neighbors 의 count 와 같은 연결수),
                   offset, truncated, next_offset(더 있을 때만),
                   items: [{id, title, domain, type, dir, evidence, confidence, note,
                            valid_from, valid_until}]}}
           ``items`` 는 `links_offset` 부터 최대 `_NODE_LINKS_CAP` 줄이다. `truncated`
           가 참이면 `next_offset` 이 **이어 읽을 자리**를 준다 — 그것을 따라가면
           이웃 전체에 닿는다(`_window`). `total` 은 창과 무관하게 언제나
           이웃 전체 수다.

           sources: {total, offset, truncated, next_offset(더 있을 때만),
                     items: [{doc_id, title, mentions}]}
           sources_offset 부터 최대 50개씩 읽으며 next_offset 으로 이어 간다.
           이 노드에 딸린 **문서 전부**. ``excerpts`` 와 다른 계약이다 — 저쪽은 청크를
           `limit` 개로 자르므로 거기 보이는 문서는 "이 노드의 문서"가 아니라 "상위 몇
           청크가 우연히 속한 문서"다(예: 문서 여섯 중 발췌에 뜬 것은 넷). 소스
           선택기는 이 목록을 써야 한다. MENTIONS 대상은 Concept/Decision 이며
           그 밖의 캐리어는 현재 스키마상 소스 목록이 비어 있다.
           ``note`` 는 부착(Record ATTACHES_TO)에서만 채워진다(카드 본문). 개념
           관계(out/in)와 Bridge/Episode 부착은 항상 빈 문자열.
           ``valid_from``/``valid_until`` 도 같은 신분(W13) — Record 부착만 값을
           갖고, 값이 있으면 그 기록의 유효구간이다(반개구간·YYYY[-MM[-DD]]).

    ``props`` 는 노드의 자유 속성(dict)이다. Episode 도 이 열을 갖고 여기서 읽는다
    (감사 F11). Entity 는 열이 없어 {} 다.

    ``concept:doc.*`` 미러의 유효구간은 Concept 열이 아니라 그 문서(`_DocMeta`)의
    것이다(감사 F13). Concept 에는 구간 열이 없어서, 은퇴한 문서의 미러가 current 로
    읽혔다. :func:`node_plan` 이 같은 자리를 이미 그렇게 읽는다.

    Entity(개방 타입)는 제목·태그와 TYPED 관계만 낸다(감사 F12, node_kind="entity").
    발췌 FTS 폴백이 실패하면 ``excerpt_error`` 에 사유를 싣는다(조용히 삼키지 않는다).

    ``rev``/``updated_at`` 은 그 노드의 현재 버전이다(W3). 고쳐 쓰기 전에 무엇을 읽었는지
    붙들어 두라고 낸다. 쓰기가 이 값을 ``expected_rev`` 로 실으면 어긋날 때 거절된다(선택).
    근거: [[vinc.plan.mcp-agent-surface §6]]."""
    cfg = cfg or load_config()
    base: dict = {"kind": "node-context", "executed": False, "node": node,
                  "one_liner": "", "domain": "", "props": {}, "excerpts": [],
                  "arm": "none",
                  # 이 노드에 딸린 문서 전부(발췌 캡과 다른 계약). 못 읽었을 때도
                  # 키는 있다 — 없는 키와 0 은 다른 말이다.
                  "sources": {"total": 0, **_window([], sources_offset, _NODE_SOURCES_CAP)},
                  # W3 — 못 읽었을 때도 키는 있다. 없는 키와 0 은 다른 말이다.
                  "rev": 0, "updated_at": "",
                  # 노드 자신의 유효구간(부착 목록의 것과 다르다). 못 읽었으면 빈 값.
                  "valid_from": "", "valid_until": "", "validity": "",
                  "superseded_by": ""}
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        lim = max(1, min(int(limit), 12))
        if node.startswith(domain_hierarchy.ID_PREFIX):
            return _domain_context(store, node, owner=owner, offset=links_offset, cfg=cfg)
        if _seed_table(node)[0] == "Entity":
            return _entity_context(store, node, base, owner=owner,
                                   with_evidence=with_evidence, offset=links_offset)
        if _seed_table(node)[0] not in (*_SEMANTIC_LABELS, "Record", "Episode"):
            exact = execute(node_plan(node, owner=owner), cfg, store=store)
            if not exact.get("executed"):
                return {**base, "status": "query_failed", "error": exact.get("error") or exact.get("note")}
            found = bool(exact.get("rows"))
            return {**base, "status": "unsupported_kind" if found else "unavailable",
                    "found": found, "note": "node context unavailable for this kind"}
        # ① 노드 메타 — 의미층(개념·결정) 우선, 없으면 Episode(타임라인 클릭 대상).
        #
        # v12 — 라벨을 id 접두사에서 꺼낸다. 여기 Concept 이 박혀 있으면 결정을
        # 클릭했을 때 `executed:False` + "unknown node" 가 나온다(라이브 탐침에서
        # 실제로 그랬다). Decision 에는 one_liner 열이 없어 그 자리는 '' 로 둔다 —
        # props 폴백은 아래 주석대로 호출부가 한다.
        meta_label, meta_pk = _seed_table(node)
        if meta_label not in _SEMANTIC_LABELS:
            meta_label, meta_pk = "Concept", "concept_id"
        ol_expr = "c.one_liner" if meta_label == "Concept" else "''"
        rows = store.run(
            f"MATCH (c:{meta_label}) WHERE c.{meta_pk} = $id AND c.owner_id = $owner "
            f"RETURN c.title AS title, {ol_expr} AS ol, c.props AS props, "
            "c.domain AS domain, "
            + _track_cols("c"),
            # PK 네임스페이싱 — 노드 PK 바인딩은 물리 키다. 응답의 `node` 는 논리
            # 그대로다(사용자가 준 값). 근거: [[vinc.plan.node-pk-namespacing]] §7
            {"id": pkns.key(owner, node), "owner": owner})
        if rows:
            # `props` 를 함께 싣는다(D73). `one_liner` 는 **쓰기 경로가 없는 열**이라
            # (`api/fragment.py` 가 SET 하지 않는다 — MCP 적재분 전량이 빈 값) 이것만
            # 돌려주면 상세 패널·묻기 답변이 통째로 빈다. 사람이 쓴 한두 문장 요약은
            # 실제로는 props 에 들어와 있고(검색은 이미 본다 — matched_in:'props'),
            # 조회만 그것을 안 주고 있었다. 호출부가 one_liner → props 순으로 고른다.
            base.update(executed=True,
                        node_kind="decision" if meta_label == "Decision" else "concept",
                        title=rows[0].get("title") or node,
                        domain=rows[0].get("domain") or "",
                        one_liner=rows[0].get("ol") or "",
                        rev=rows[0].get("rev") or 0,
                        updated_at=rows[0].get("updated_at") or "",
                        props=_props_to_obj(rows[0].get("props")))
            # Decision 의 구간은 props 에 있다(§8 항목 5 임시 인코딩). Concept 은
            # 구간이 없고, 미기재는 current 다.
            _p = base["props"] if meta_label == "Decision" else {}
            _stamp_validity(base, _p.get("valid_from"), _p.get("valid_until"))
            if meta_label == "Concept" and node.startswith("concept:doc."):
                # 문서 미러의 구간은 문서의 것이다(F13). 문서가 없으면 개념 규칙 그대로다.
                # 구간 열이 없는 v22 이전 스토어에서는 이 조회만 실패하고 개념 규칙으로
                # 남는다 — 발췌·관계까지 함께 잃으면 안 된다. 사유는 싣는다.
                try:
                    dm = store.run(
                        "MATCH (d:_DocMeta) WHERE d.doc_id = $doc AND d.owner_id = $owner "
                        "RETURN d.valid_from AS vf, d.valid_until AS vu, d.superseded_by AS sb",
                        {"doc": node[len("concept:doc."):], "owner": owner}) or []
                except Exception as exc:
                    dm = []
                    base["doc_validity_error"] = f"{type(exc).__name__}: {exc}"
                if dm:
                    _stamp_validity(base, dm[0].get("vf"), dm[0].get("vu"))
                    base["superseded_by"] = str(dm[0].get("sb") or "")
        else:
            ep = store.run(
                "MATCH (e:Episode) WHERE e.episode_id = $id AND e.owner_id = $owner "
                "RETURN e.title AS title, e.summary AS s, e.domain AS domain, "
                "e.props AS props, "
                + _track_cols("e"),
                {"id": pkns.key(owner, node), "owner": owner})
            if not ep:
                # Record — 의미층도 Episode 도 아닌 세 번째 실체. 위 ① 이 라벨을
                # Concept 으로 접어 버려서 `record:` id 는 여기까지 흘러와
                # "unknown node" 로 답했다. 존재하는 노드를 없다고 말하는 것이라
                # 조인 읽기(brief)가 Record 를 통째로 못 열었다 —
                # [[record:brief-returns-none-when-its-winner-is-a-record-2026-08-30]]
                # 이 그래프의 사실형 제목은 대부분 Record 에 있어서, 가장 싸게
                # 얻을 수 있는 답을 정확히 못 얻고 있었다.
                if _seed_table(node)[0] == "Record":
                    rc = store.run(
                        "MATCH (r:Record) WHERE r.record_id = $id "
                        "AND r.owner_id = $owner "
                        "RETURN r.title AS title, r.props AS props, "
                        "r.domain AS domain, "
                        + _track_cols("r") + ", " + _validity_cols("r"),
                        {"id": pkns.key(owner, node), "owner": owner})
                    if rc:
                        # one_liner 열이 없다 — 카드 본문은 props 다(부착 목록이
                        # `note` 를 뽑아 쓰는 그 자리와 같은 값). 호출부의
                        # one_liner → props 폴백이 그것을 집는다.
                        base.update(executed=True, node_kind="record",
                                    title=rc[0].get("title") or node,
                                    domain=rc[0].get("domain") or "",
                                    rev=rc[0].get("rev") or 0,
                                    updated_at=rc[0].get("updated_at") or "",
                                    props=_props_to_obj(rc[0].get("props")))
                        _stamp_validity(base, rc[0].get("valid_from"),
                                        rc[0].get("valid_until"))
                        # MENTIONS 는 Chunk→Concept 이라 Record 는 대상이 아니다.
                        # 관계는 있다(ATTACHES_TO). 여기서 links 를 비워 두면
                        # vinc_neighbors 가 양쪽 끝에서 보여 주는 부착을 이 표면만
                        # "연결 0"이라 답해, 두 도구가 같은 그래프를 두고 어긋난다.
                        base["links"] = _node_links(store, node, owner=owner,
                                                    with_evidence=with_evidence,
                                                    offset=links_offset)
                        return base
                base["status"] = "unavailable"
                base["found"] = False
                base["note"] = f"unknown node: {node}"
                return base
            base.update(executed=True, node_kind="episode",
                        title=ep[0].get("title") or node,
                        domain=ep[0].get("domain") or "",
                        one_liner=ep[0].get("s") or "",
                        rev=ep[0].get("rev") or 0,
                        updated_at=ep[0].get("updated_at") or "",
                        # F11 — 사건도 props 열이 있다. 안 고르면 저장된 값이 안 보인다.
                        props=_props_to_obj(ep[0].get("props")))
            _stamp_validity(base, "", "")   # 사건은 구간이 없다 — 꼴만 맞춘다
            # Episode 는 MENTIONS 대상이 아니다(발췌 없음 — 요약이 곧 설명). 그렇다고
            # **관계까지 없는 것은 아니다**: ABOUT 이 사건을 개념·결정에 잇는다. 여기서
            # 일찍 빠져나가면서 그 둘을 함께 건너뛰고 있었고, 사건이 해소 후보가 되면서
            # 확정된 사건만 관계 없이 답하게 됐다. 바로 위 Record 갈래가
            # 같은 사유로 이미 링크를 채운다.
            base["arm"] = "none"
            base["excerpts"] = []
            base["links"] = _episode_links(store, node, owner=owner,
                                           with_evidence=with_evidence,
                                           offset=links_offset)
            return base
        # ② MENTIONS 역참조 — 정본 발췌.
        ment = store.run(
            f"MATCH (ch:Chunk)-[m:MENTIONS]->(c:{meta_label}) "
            f"WHERE c.{meta_pk} = $id AND c.owner_id = $owner "
            "AND (ch.owner_id = $owner OR ch.owner_id = '') "
            "RETURN ch.chunk_id AS cid, ch.doc_id AS doc_id, "
            "ch.section_title AS section, ch.text AS text, m.method AS method "
            # 링크 방법이 강한 순(title > stem > source)으로 먼저 정렬한다. source 는
            # 파일 제공처(provenance)라 그 파일의 **모든** 청크가 걸리므로, 문서 순서만으로
            # 자르면 창이 앞 문서 머리말로 채워지고 정작 이 노드를 말하는 청크가 밀려난다.
            # doc_id·chunk_index 는 동순위 타이브레이크로 남겨 결과를 결정론으로 유지할 것.
            # 이 순서는 `tests/core/test_excerpt_rank.py` 가 고정하고, 근거 인용도 그 머리말에 있다.
            "ORDER BY CASE m.method WHEN 'title' THEN 0 WHEN 'stem' THEN 1 ELSE 2 END, "
            f"ch.doc_id, ch.chunk_index LIMIT {lim}",
            {"id": pkns.key(owner, node), "owner": owner}) or []
        arm = "mentions" if ment else "none"
        # ③ FTS 폴백 — 링크 없는 개념도 제목으로 관련 정본을 찾는다(증강).
        # B7 — 전문 검색을 **소유자로 좁혀** 묻는다. 전역 상위 몇 개를 받아 남의 행을
        # 버리면 공유 허브에서 남의 청크가 창을 채우고 내 발췌가 빈다.
        if not ment and base["title"]:
            ids, fts_error = _owner_fts_ids(store, base["title"], lim, owner=owner)
            if fts_error:
                base["excerpt_error"] = fts_error
            if ids:
                ment = store.run(
                    "MATCH (ch:Chunk) WHERE ch.chunk_id IN $ids "
                    "AND (ch.owner_id = $owner OR ch.owner_id = '') "
                    "RETURN ch.chunk_id AS cid, ch.doc_id AS doc_id, "
                    "ch.section_title AS section, ch.text AS text, "
                    "'fts' AS method", {"ids": ids, "owner": owner}) or []
                # `IN $ids` 는 BM25 순위를 지키지 않는다 — 되돌리지 않으면 같은 노드의 발췌
                # 순서가 호출마다(REST 와 MCP 사이에서도) 달라진다.
                ment = _excerpt.by_rank(ment, ids)
                arm = "fts" if ment else "none"
        # ④-0 이 노드에 딸린 **문서 전부**. 발췌와 다른 계약이다: 발췌는 청크를
        # `lim` 개로 자르므로 그 넷이 한 문서에서 나오면 **문서도 하나로 보인다** —
        # 노드가 열 개 문서에 언급돼 있어도 그렇다. 소스 선택기가 그것을 전체 목록으로
        # 읽으면 사용자는 있는 줄도 모르는 문서를 고를 수 없다.
        # 근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §4]].
        srows = store.run(
            f"MATCH (ch:Chunk)-[:MENTIONS]->(c:{meta_label}) "
            f"WHERE c.{meta_pk} = $id AND c.owner_id = $owner "
            "AND (ch.owner_id = $owner OR ch.owner_id = '') "
            "RETURN ch.doc_id AS doc_id, count(ch.chunk_id) AS n",
            {"id": pkns.key(owner, node), "owner": owner}) or []
        # ④ 문서 제목 조인(_DocMeta) + 발췌 정리(공백 접기, 240자).
        # 제목은 발췌에 뜬 문서와 소스 목록 **양쪽**에 필요하므로 한 번에 받는다.
        # `_DocMeta.doc_id` 는 소유자로 네임스페이싱되지 않으므로 청크와 같은 과도기 술어
        # (owner OR '')를 건다 — 빠지면 같은 doc_id 를 가진 남의 문서 제목이 샌다.
        doc_ids = sorted({r["doc_id"] for r in ment if r.get("doc_id")})
        all_ids = sorted({str(r.get("doc_id") or "") for r in srows
                          if r.get("doc_id")} | set(doc_ids))
        titles: dict[str, str] = {}
        if all_ids:
            for r in store.run(
                    "MATCH (d:_DocMeta) WHERE d.doc_id IN $ids "
                    "AND (d.owner_id = $owner OR d.owner_id = '') "
                    "RETURN d.doc_id AS id, d.title AS t",
                    {"ids": all_ids, "owner": owner}) or []:
                titles[r["id"]] = r.get("t") or ""
        # 제목 → id 순서로 창을 자른다. 발췌 상한과 독립적으로 끝까지 읽을 수 있다.
        _src = sorted(({"doc_id": str(r.get("doc_id") or ""),
                        "title": titles.get(str(r.get("doc_id") or ""), "")
                        or str(r.get("doc_id") or ""),
                        "mentions": int(r.get("n") or 0)} for r in srows
                       if r.get("doc_id")),
                      key=lambda s: (s["title"], s["doc_id"]))
        base["sources"] = {"total": len(_src),
                           **_window(_src, sources_offset, _NODE_SOURCES_CAP)}
        base["excerpts"] = [{
            "doc_id": r.get("doc_id") or "",
            "doc_title": titles.get(r.get("doc_id") or "", "") or (r.get("doc_id") or ""),
            "section": r.get("section") or "",
            # 노드 토큰이 모인 자리의 240자(`VINC_EXCERPT_WINDOW=head` 면 맨 앞 240자).
            "excerpt": _excerpt.excerpt(r.get("text") or "", base["title"],
                                        base.get("one_liner") or ""),
            "method": r.get("method") or "",
        } for r in ment]
        try:
            cites = _doc_cite_ids(store, owner, all_ids)
        except Exception as exc:
            cites = {}
            base["citation_error"] = f"{type(exc).__name__}: {exc}"
        base["excerpts"] = _cite_doc_rows(base["excerpts"], cites)
        base["sources"]["items"] = _cite_doc_rows(base["sources"]["items"], cites)
        base["arm"] = arm
        base["links"] = _node_links(store, node, owner=owner,
                                    with_evidence=with_evidence,
                                    offset=links_offset)
        return base
    except Exception as exc:
        base["status"] = "query_failed"
        base["note"] = base["error"] = f"{type(exc).__name__}: {exc}"
        return base
    finally:
        if owns:
            store.close()


# 상세 레일 관계 목록 상한 — 허브(차수 30+)에서 레일이 끝없이 길어지지 않게.
# 잘렸는지는 `truncated` 로 밝힌다(조용한 축소 금지).
_NODE_LINKS_CAP = 24


def _domain_context(store, node_id: str, *, owner: str, offset: int, cfg=None) -> dict:
    """공유 좌표의 존재는 정확한 소유 범위 조회로, 구조는 기존 이웃 조회로 읽는다."""
    base = {"kind": "node-context", "executed": False, "node": node_id,
            "node_kind": "domain"}
    domain = domain_hierarchy.parse_id(node_id)
    if not domain or not owner:
        return {**base, "status": "unavailable", "found": False}
    exact = node(domain_hierarchy.make_id(domain), owner=owner, cfg=cfg, store=store)
    if not exact.get("executed"):
        return {**base, "status": "query_failed", "error": exact.get("error") or exact.get("note")}
    if not exact.get("found"):
        # 남의 빈 공유 좌표와 없는 좌표는 구분하지 않는다.
        return {**base, "status": "unavailable", "found": False}
    ring = _domain_neighbors(node_id, owner=owner, limit=None, cfg=cfg, store=store)
    if not ring.get("executed") or ring.get("error"):
        return {**base, "status": "query_failed", "error": ring.get("error") or ring.get("note")}
    # 주제 설명의 미러는 그 주제의 노드가 아니다(설명은 아래 `description` 이 따로 싣는다).
    # 목록과 수에서 같이 빼야 열과 엣지의 대조(membership_drift)가 어긋나지 않는다
    mirrors = _topic_description_mirrors_by_domain(owner, cfg, store) or {}
    rows = [r for r in ring["neighbors"] if not (r["rel"] == "IN_DOMAIN" and r["id"] in mirrors)]
    children = [r for r in rows if r["rel"] == "PARENT_OF"]
    members = [r for r in rows if r["rel"] == "IN_DOMAIN"]
    counts = store.run(domain_hierarchy.scoped_counts_cypher(),
                       {"owner": owner, "domain": domain, "prefix": domain + "/"}) or []
    direct = sum(int(r.get("total") or 0) for r in counts if r.get("domain") == domain) \
        - sum(1 for d in mirrors.values() if d == domain)
    subtree = sum(int(r.get("total") or 0) for r in counts) \
        - sum(1 for d in mirrors.values() if d == domain or d.startswith(domain + "/"))
    expected_children = {domain + "/" + r["domain"][len(domain) + 1:].split("/")[0]
                         for r in counts if r.get("domain", "").startswith(domain + "/")}
    parent_path = domain.rpartition("/")[0]
    parent = ({"id": domain_hierarchy.make_id(parent_path), "title": parent_path,
               "domain": parent_path} if parent_path else None)
    topic = {"path": domain, "parent": parent,
             "child_topics": len(children), "direct_nodes": len(members),
             "subtree_nodes": subtree, "descendant_nodes": subtree - direct,
             "structure": {"total": len(rows), **_window(rows, offset)}}
    from api import topic_description
    topic["description"] = topic_description.read(store, owner=owner, domain=domain)
    # 열은 분류의 정본, 엣지는 파생물이다. 불일치는 자동 수리하지 않는다.
    if direct != len(members) or expected_children != {r["title"] for r in children}:
        topic["consistency"] = {"status": "membership_drift", "column_direct_nodes": direct,
                                "column_child_topics": len(expected_children)}
    return {**base, "executed": True, "found": True, "status": "ok",
            "title": domain.split("/")[-1], "domain": domain, "topic": topic}


def _window(items: list[dict], offset: int, cap: int = _NODE_LINKS_CAP) -> dict:
    """목록의 **한 창**과 이어 읽을 자리. 관계와 소스가 같은 말을 하게 묶는다.

    `truncated` 는 "이 창 뒤에 더 있나"다. `offset` 이 0 이면 오프셋 없는 뜻(전체가 상한보다
    큼)과 값이 같으므로 이미 이 응답을 읽던 화면은 바뀌지 않는다.

    왜 커서가 아니라 오프셋인가: 이 목록의 정렬은 결정론이고(제목·id) 규모가 수백이다.
    커서는 인코딩·만료·무효화를 다 지어야 하는데 여기서는 그 대가로 얻는 것이 없다.
    (점수로 줄 세우고 페이지가 많은 `vinc_search` 는 사정이 달라서 커서를 쓴다.)

    ⚠ 상한만 있고 이어 읽을 자리가 없으면 **보이는 것이 전부인지 알 길이 없다** —
    25개 중 24개를 내면서 `total:25` 만 말하던 자리가 그랬다.
    근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §6]]."""
    off = max(0, int(offset or 0))
    page = items[off:off + cap]
    more = len(items) > off + len(page)
    out: dict = {"items": page, "truncated": more, "offset": off}
    if more:
        out["next_offset"] = off + len(page)
    return out


def _owner_fts_ids(store, text: str, limit: int, *, owner: str | None
                   ) -> tuple[list[str], str]:
    """발췌 폴백의 전문 검색 — 소유자로 좁힌 청크 id 와 실패 사유(없으면 "").

    검색의 청크 팔과 같은 규칙이다: 소유자를 걸 수 없는 스토어에는 묻지 않고(fail-closed),
    색인이 없으면 청크가 있을 때 한 번 세우고 다시 묻는다. 그 밖의 실패는 사유를 돌려준다
    — 조용히 삼키면 "발췌 없음"과 "못 읽음"이 같아 보인다."""
    filters = {"owner_id": owner} if owner else None
    if owner and not flag(store, "scoped_chunk_search"):
        return [], ("owner_scope_unsupported: this store cannot filter full-text "
                    "search by owner")
    try:
        hits = store.fulltext_search(text, limit, filters) or []
    except Exception as exc:
        kind = _classify_chunk_error(exc)
        if kind != "index_missing":
            return [], f"{kind}: {type(exc).__name__}: {exc}"
        if flag(store, "fts_index_remnant"):
            return [], f"fts_index_remnant: {_FTS_REMNANT}"
        builder = operation(store, "ensure_search_indexes")
        if builder is None:
            return [], f"index_missing: {type(exc).__name__}: {exc}"
        if _chunk_count(store) == 0:
            return [], ""          # 청크가 없으면 찾을 발췌도 없다. 실패가 아니다
        try:
            builder(dim=None)
            hits = store.fulltext_search(text, limit, filters) or []
        except Exception as exc2:
            return [], f"index_build_failed: {type(exc2).__name__}: {exc2}"
    return [h.get("id") for h in hits if h.get("id")], ""


def _entity_links(store, node: str, *, owner: str, with_evidence: bool = False,
                  offset: int = 0) -> dict:
    """Entity 시드의 TYPED 관계(양방향). `subtype` 이 저작된 타입이다. TYPED 에는
    근거·신뢰도 열이 없어 evidence 는 False, confidence 는 None 이다."""
    empty = {"degree": 0, "out": 0, "in": 0, "attached": 0, "total": 0, "items": [],
             "truncated": False, "executed": False}
    try:
        rows = store.run(_typed_neighbors_cypher(owner), {"node": pkns.key(owner, node),
                                                          "owner": owner}) or []
    except Exception:
        return empty
    order = {"out": 0, "in": 1}
    items = [{
        "id": r.get("id") or "", "title": r.get("title") or (r.get("id") or ""),
        "domain": "", "type": "TYPED", "subtype": r.get("subtype") or "",
        "dir": r.get("dir") or "", "evidence": False, "confidence": None, "note": "",
        "valid_from": "", "valid_until": "",
        **({"evidence_text": ""} if with_evidence else {}),
    } for r in sorted(rows, key=lambda r: (order.get(r.get("dir") or "", 9),
                                           str(r.get("subtype") or ""),
                                           str(r.get("title") or ""),
                                           str(r.get("id") or "")))]
    out_n = sum(1 for i in items if i["dir"] == "out")
    return {"degree": len(items), "out": out_n, "in": len(items) - out_n, "attached": 0,
            "total": len(items), "executed": True, **_window(items, offset)}


def _entity_context(store, node: str, base: dict, *, owner: str,
                    with_evidence: bool = False, offset: int = 0) -> dict:
    """Entity(개방 타입) 노드의 상세. 열이 제목·타입·태그뿐이라 그것과 TYPED 관계만 낸다.
    이것이 없을 때 brief 는 Entity 를 열지 못했다(감사 F12)."""
    rows = store.run(
        "MATCH (n:Entity) WHERE n.entity_id = $id AND n.owner_id = $owner "
        "RETURN n.title AS title, n.type AS type, coalesce(n.tags, []) AS tags, "
        + _track_cols("n"), {"id": pkns.key(owner, node), "owner": owner}) or []
    if not rows:
        return {**base, "status": "unavailable", "found": False,
                "note": f"unknown node: {node}"}
    out = {**base, "executed": True, "node_kind": "entity",
           "title": rows[0].get("title") or node, "entity_type": rows[0].get("type") or "",
           "tags": list(rows[0].get("tags") or []),
           "rev": rows[0].get("rev") or 0, "updated_at": rows[0].get("updated_at") or ""}
    _stamp_validity(out, "", "")   # 개방 타입은 구간이 없다 — 꼴만 맞춘다
    out["links"] = _entity_links(store, node, owner=owner, with_evidence=with_evidence,
                                 offset=offset)
    return out


def _conf(value) -> float | None:
    """관계 신뢰도. 저장되지 않은 값은 None 으로 둔다 — 0.0 은 저작된 값이다(감사 F8)."""
    return None if value is None else float(value)


def _record_links(store, node: str, *, owner: str,
                  with_evidence: bool = False, offset: int = 0) -> dict:
    """Record 시드의 관계 목록 — 그 기록이 **붙어 있는** 개념·결정(ATTACHES_TO)과
    기록끼리의 대체(SUPERSEDES, 양방향).

    :func:`_node_links` 의 거울상이다. 저쪽은 "무엇이 이 노드에 붙어 있나"를 묻고
    여기서는 "이 노드가 무엇에 붙어 있나"를 묻는다. 방향이 반대라 같은 쿼리를 재사용할
    수 없다. 부착의 `dir` 은 'out' 으로 낸다 — 엣지가 Record 에서 나가기 때문이고, 화면의
    "나감" 줄과 뜻이 같다. SUPERSEDES 는 저장된 방향 그대로 out(이 기록이 대체한다)·
    in(이 기록이 대체됐다)이다. 빠지면 total 이 vinc_neighbors 의 count 보다 작다(감사 F2).

    evidence/confidence 는 v16 부터 ATTACHES_TO 실열이다 — v16 앞의 스키마에는 열이 없어
    항상 False 로 냈고 그것이 결측이 아니라 사실이었다
    ([[record:attaches-to-carries-evidence-since-v16]]).
    실패는 :func:`_node_links` 와 같은 태도로 빈 요약 강등."""
    empty = {"degree": 0, "out": 0, "in": 0, "attached": 0, "total": 0, "items": [],
             "truncated": False, "executed": False}
    seed = "rc.record_id = $id AND rc.owner_id = $owner AND o.owner_id = $owner "
    params = {"id": pkns.key(owner, node), "owner": owner}
    try:
        rows = store.run(" UNION ALL ".join(
            f"MATCH (rc:Record)-[r:ATTACHES_TO]->(o:{ol}) WHERE {seed}"
            f"RETURN o.{opk} AS id, o.title AS title, o.domain AS domain, "
            "'ATTACHES_TO' AS type, 'out' AS dir, "
            "r.evidence AS ev, r.confidence AS conf, " + _VALIDITY_EMPTY
            for ol, opk in _SEMANTIC_PK.items()), params) or []
    except Exception:
        return empty
    # 대체 상대도 Record 라 그 구간을 함께 싣는다(W13 과 같은 신분). 따로 묻는 이유:
    # SUPERSEDES 표가 없는 이전 판 스토어에서 이 팔이 Binder 로 죽어도 부착은 남아야 한다
    # (neighbors 의 record_supersedes_plan 도 따로 돈다). 못 읽었으면 표식을 단다.
    sup_failed = False
    try:
        rows += store.run(" UNION ALL ".join(
            f"MATCH {pat} WHERE {seed}"
            "RETURN o.record_id AS id, o.title AS title, o.domain AS domain, "
            f"'SUPERSEDES' AS type, '{d}' AS dir, "
            "r.evidence AS ev, r.confidence AS conf, " + _validity_cols("o")
            for pat, d in (("(rc:Record)-[r:SUPERSEDES]->(o:Record)", "out"),
                           ("(o:Record)-[r:SUPERSEDES]->(rc:Record)", "in"))), params) or []
    except Exception:
        sup_failed = True
    order = {"out": 0, "in": 1}
    items = [{
        "id": r.get("id") or "", "title": r.get("title") or (r.get("id") or ""),
        "domain": r.get("domain") or "", "type": r.get("type") or "",
        "dir": r.get("dir") or "",
        "evidence": bool((r.get("ev") or "").strip()),
        "confidence": _conf(r.get("conf")), "note": "",
        "valid_from": r.get("valid_from") or "", "valid_until": r.get("valid_until") or "",
        **({"evidence_text": (r.get("ev") or "").strip()} if with_evidence else {}),
    } for r in sorted(rows, key=lambda r: (order.get(r.get("dir") or "", 9),
                                           str(r.get("type") or ""),
                                           str(r.get("title") or ""),
                                           str(r.get("id") or "")))]
    out_n = sum(1 for i in items if i["dir"] == "out")
    return {"degree": len(items), "out": out_n, "in": len(items) - out_n, "attached": 0,
            "total": len(items), "executed": True,
            **({"supersession_unavailable": True} if sup_failed else {}),
            **_window(items, offset)}


def _episode_links(store, node: str, *, owner: str,
                   with_evidence: bool = False, offset: int = 0) -> dict:
    """Episode 시드의 관계 목록 — 그 사건이 **무엇에 대한 것인가**(ABOUT).

    :func:`_record_links` 와 같은 모양이다. 부착 엣지가 시드에서 **나가므로**
    "무엇이 이 노드에 붙어 있나"를 묻는 :func:`_node_links` 의 팔들은 구조적으로 0행을
    낸다. 게다가 Episode 는 `_SEMANTIC_LABELS` 가 아니라 그쪽에서 Concept 으로 접히는데,
    그러면 **다른 테이블을 조회하고 조용히 빈 목록**을 낸다.

    이 함수가 없던 동안 `node_context` 의 Episode 갈래는 링크를 아예 안 실었다. 사건이
    해소 후보가 아니던 시절에는 눈에 띄지 않았지만, 사건을 후보로 올리면서
    **확정된 사건만 관계 없이 답하는** 상태가 됐다. 한 스토어에서 잰 값: 사건 160건에 ABOUT
    256개(개념 226 · 결정 30)이고 관계가 0인 사건은 둘뿐이다.
    근거: [[record:episode-can-never-be-a-brief-candidate-2026-09-09]]"""
    empty = {"degree": 0, "out": 0, "in": 0, "attached": 0, "total": 0, "items": [],
             "truncated": False, "executed": False}
    try:
        rows = store.run(" UNION ALL ".join(
            f"MATCH (e:Episode)-[r:ABOUT]->(o:{ol}) "
            "WHERE e.episode_id = $id AND e.owner_id = $owner "
            "AND o.owner_id = $owner "
            f"RETURN o.{opk} AS id, o.title AS title, o.domain AS domain, "
            "r.evidence AS ev, r.confidence AS conf "
            for ol, opk in _SEMANTIC_PK.items()),
            {"id": pkns.key(owner, node), "owner": owner}) or []
    except Exception:
        return empty
    items = [{
        "id": r.get("id") or "", "title": r.get("title") or (r.get("id") or ""),
        "domain": r.get("domain") or "", "type": "ABOUT", "dir": "out",
        "evidence": bool((r.get("ev") or "").strip()),
        "confidence": _conf(r.get("conf")), "note": "",
        "valid_from": "", "valid_until": "",
        **({"evidence_text": (r.get("ev") or "").strip()} if with_evidence else {}),
    } for r in sorted(rows, key=lambda r: (str(r.get("title") or ""),
                                           str(r.get("id") or "")))]
    return {"degree": len(items), "out": len(items), "in": 0, "attached": 0,
            "total": len(items), "executed": True, **_window(items, offset)}


def _node_links(store, node: str, *, owner: str,
                with_evidence: bool = False, offset: int = 0) -> dict:
    """개념의 연결 요약 + 관계 목록(:func:`node_context` ④). 조회 전용.

    개념·결정 사이 공통 읽기 계약의 관계는 양방향으로 세고, 부착(Bridge 매개·Episode
    ABOUT·Record ATTACHES_TO)은 따로 센다 — 화면이 "연결 N · → 나감 · ← 들어옴"과
    "부착 N"을 다른 줄에 싣기 때문이다.

    그래서 ``degree`` 는 **out + in 뿐**이고 ``attached`` 는 거기 안 든 나머지다.
    ``total`` 이 그 둘을 합친 수 — 그 노드의 연결수이고 vinc_neighbors 의 ``count``
    와 같다. 이 셋은 세 갈래(:func:`_record_links`·:func:`_episode_links` 포함)에서
    같은 뜻이다: 기록·사건은 부착 축이 구조적으로 0이라 total 이 degree 와 같다.

    SEC-1: 양 끝을 owner 로 건다. Bridge 만 과도기 술어(:func:`_bridge_owner_pred`).
    실패는 조용히 빈 요약으로 강등한다 — 관계 목록은 증강이고, 미마이그레이션
    스토어(E-G6 열 부재)에서 상세 패널 자체가 죽으면 안 된다."""
    empty = {"degree": 0, "out": 0, "in": 0, "attached": 0, "total": 0, "items": [],
             "truncated": False, "executed": False}
    # v12 — 시드 라벨을 접두사에서 꺼낸다. 상대편(o)은 의미층 둘을 다 본다.
    # 박아 두면 결정의 상세 패널이 "연결 0"으로 뜬다(관계는 있는데 안 보인다).
    _sl, _spk = _seed_table(node)
    if _sl == "Record":
        # Record 시드는 반대 방향이다 — 부착은 Record→(Concept|Decision) 이라
        # 아래 팔들(무엇이 이 노드에 붙어 있나)이 구조적으로 0행을 낸다. 그대로
        # 두면 부착이 있는 기록이 "연결 0"으로 답해 vinc_neighbors 와 어긋난다
        # (그쪽은 "모든 관계는 양쪽 끝에서 보인다"를 계약으로 말한다).
        return _record_links(store, node, owner=owner, with_evidence=with_evidence,
                             offset=offset)
    if _sl not in _SEMANTIC_LABELS:
        _sl, _spk = "Concept", "concept_id"
    rows: list[dict] = []
    try:
        arms = []
        for (sl, sp, dl, dp), rels in _GRAPH_READ_PAIRS.items():
            if sl not in _SEMANTIC_LABELS or dl not in _SEMANTIC_LABELS:
                continue
            for outgoing in (True, False):
                if (sl if outgoing else dl) != _sl:
                    continue
                ol, opk = (dl, dp) if outgoing else (sl, sp)
                pattern = (f"(c:{_sl})-[r:{'|'.join(rels)}]->(o:{ol})" if outgoing
                           else f"(o:{ol})-[r:{'|'.join(rels)}]->(c:{_sl})")
                arms.append(
                    f"MATCH {pattern} "
                    f"WHERE c.{_spk} = $id AND c.owner_id = $owner AND o.owner_id = $owner "
                    f"RETURN o.{opk} AS id, o.title AS title, o.domain AS domain, "
                    f"type(r) AS type, '{'out' if outgoing else 'in'}' AS dir, "
                    "r.evidence AS ev, r.confidence AS conf")
        cc = " UNION ALL ".join(arms)
        rows = store.run(cc, {"id": pkns.key(owner, node), "owner": owner}) or []
    except Exception:
        return empty

    attached: list[dict] = []
    try:
        attached = store.run(
            # 부착 3종 — 각 rel 테이블의 열이 달라 evidence/confidence 는 ABOUT ·
            # ATTACHES_TO(v16)만 실열 투영하고 CONNECTS 는 상수로 맞춘다(UNION ALL 은
            # 컬럼 형태가 같아야 한다).
            # raw_props: Record 카드의 본문(props.note 또는 props.one_liner)이 여기
            # 있다 — 여태 id/title/domain 만 주고 그 문장은 버렸다(no-llm-chat 연구
            # §3.3, I6 미개통 원인). 나머지 둘은 빈 문자열로 맞춘다.
            # W13 — 유효구간 둘은 Record 팔만 실열(열이 Record 전용). 다른 두 팔은
            # 리터럴 자리 맞춤 — 한 팔이라도 빠지면 UNION 전체가 Binder 로 죽는다.
            f"MATCH (b:Bridge)-[:CONNECTS]->(c:{_sl}) "
            f"WHERE c.{_spk} = $id AND c.owner_id = $owner AND {_bridge_owner_pred('b')} "
            # CONNECTS 에는 신뢰도 열이 없다. 0.0 리터럴은 저작된 값으로 읽히므로 NULL 이다.
            "RETURN b.bridge_id AS id, b.title AS title, b.source_domain AS domain, "
            "'CONNECTS' AS type, 'att' AS dir, '' AS ev, CAST(NULL AS DOUBLE) AS conf, "
            "'' AS raw_props, "
            + _VALIDITY_EMPTY + " "
            "UNION ALL "
            f"MATCH (e:Episode)-[r:ABOUT]->(c:{_sl}) "
            f"WHERE c.{_spk} = $id AND c.owner_id = $owner AND e.owner_id = $owner "
            "RETURN e.episode_id AS id, e.title AS title, e.domain AS domain, "
            "'ABOUT' AS type, 'att' AS dir, r.evidence AS ev, r.confidence AS conf, "
            "'' AS raw_props, " + _VALIDITY_EMPTY + " "
            "UNION ALL "
            f"MATCH (rc:Record)-[r2:ATTACHES_TO]->(c:{_sl}) "
            f"WHERE c.{_spk} = $id AND c.owner_id = $owner AND rc.owner_id = $owner "
            "RETURN rc.record_id AS id, rc.title AS title, rc.domain AS domain, "
            "'ATTACHES_TO' AS type, 'att' AS dir, r2.evidence AS ev, "
            "r2.confidence AS conf, rc.props AS raw_props, " + _validity_cols("rc"),
            {"id": pkns.key(owner, node), "owner": owner}) or []
    except Exception:
        attached = []   # 부착 축만 강등 — 개념 관계는 이미 얻었다
    out_n = sum(1 for r in rows if r.get("dir") == "out")
    in_n = len(rows) - out_n
    items = []
    # 나감 먼저, 그다음 들어옴, 마지막 부착 — 화면이 읽는 순서 그대로 결정론 정렬.
    order = {"out": 0, "in": 1, "att": 2}
    for r in sorted(rows + attached,
                    key=lambda r: (order.get(r.get("dir") or "", 9),
                                   str(r.get("type") or ""), str(r.get("title") or ""),
                                   str(r.get("id") or ""))):
        # Record 카드 본문 — note 우선, 없으면 one_liner(두 이름이 실제로 쓰인다).
        props = _props_to_obj(r.get("raw_props")) if r.get("raw_props") else {}
        item = {
            "id": r.get("id") or "", "title": r.get("title") or (r.get("id") or ""),
            "domain": r.get("domain") or "", "type": r.get("type") or "",
            "dir": r.get("dir") or "",
            # 근거는 **있는지 없는지**가 화면의 관심사다(문장 전문은 edge-context 가 준다).
            "evidence": bool((r.get("ev") or "").strip()),
            # 저장되지 않은 신뢰도는 None 이다(감사 F8). 정렬은 brief 가 맨 뒤로 보낸다.
            "confidence": _conf(r.get("conf")),
            "note": props.get("note") or props.get("one_liner") or "",
            # W13 — Record 카드만 값을 갖는다(note 와 같은 신분). 무효화된 기록이
            # 카드에서 유효한 것과 똑같이 보이면 이 열은 없는 것과 같다.
            "valid_from": r.get("valid_from") or "",
            "valid_until": r.get("valid_until") or "",
        }
        if with_evidence:
            # W1 — 에이전트는 "근거가 있다"가 아니라 **그 문장**을 인용해야 한다.
            # 화면은 불리언만 쓰므로 키 자체를 그 경우에만 단다: 기본 호출의 페이로드는
            # 한 바이트도 안 변한다. 근거: [[vinc.plan.mcp-agent-surface §4]]
            item["evidence_text"] = (r.get("ev") or "").strip()
        items.append(item)
    # `degree` 는 out + in 이고 `attached` 는 **거기 안 든 나머지**다. 둘이 한 dict 에
    # 나란히 나가면서 어느 쪽도 그 사실을 말하지 않아, 받는 쪽이 degree 를 그 노드의
    # 연결수로 읽었다. 예: concept:node-resolution-accuracy 가
    # degree 9 · attached 50 으로 답하는 동안 vinc_neighbors 는 count 59 를 냈고(85%
    # 과소), 그 수를 합산한 연결성 결론이 나갔다가 철회됐다. 그래서 총수를 **직접
    # 싣는다** — 받는 쪽이 더해서 복원할 것을 남겨 두지 않는다.
    # 근거: [[episode:2026-09-14-brief-carries-the-connectivity-instead-of-leaving-it-to-be-summed]]
    return {"degree": len(rows), "out": out_n, "in": in_n, "attached": len(attached),
            "total": len(rows) + len(attached), "executed": True,
            **_window(items, offset)}
