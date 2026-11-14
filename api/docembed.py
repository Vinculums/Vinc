# -*- coding: utf-8 -*-
"""문서 임베딩 상태와 모델 이행: 재임베딩 큐, 워커 일감, 벡터 적용, 저장 모델과 이행 상태.

`api.docstore` 가 이 이름들을 같은 객체로 다시 내보낸다(`docstore.embed_model_state` 등). 이
모듈은 `api.docstore` 를 최상위에서 import 하지 않는다(docstore 가 이 모듈을 최상위에서 읽는다).
docstore 의 도우미 넷은 아래 얇은 함수가 호출 때 docstore 의 공개 이름으로 부른다.
"""
from __future__ import annotations

import re
from typing import Any

from store.backend import flag
from store.embedded_backend import DDL_GUARD, keep_vector_index
from store.querybase import now_iso


def _age_s(iso_ts):
    from .docstore import age_s
    return age_s(iso_ts)


def _meta(store, doc_id, **kw):
    from .docstore import doc_meta
    return doc_meta(store, doc_id, **kw)


def _drop_vector_index_if_any(store):
    from .docstore import drop_vector_index_if_any
    return drop_vector_index_if_any(store)


def _remnant_refusal(store, **kw):
    from .docstore import remnant_refusal
    return remnant_refusal(store, **kw)


def reembed_doc(store, doc_id: str, *, owner: str | None) -> dict:
    """문서를 재임베딩 큐에 되돌린다(`needs_embed = 1`) — 벡터는 **여기서 만들지 않는다.**

    검색이 읽는 벡터(`ChunkVec`)는 워커의 apply_embeddings 만 쓴다. 그 스토어의 모델을
    아는 것도 워커뿐이다. 옛 벡터는 워커가 새로 넣을 때까지 그대로 검색에 답한다. SEC-1 가드."""
    base: dict[str, Any] = {"kind": "doc.reembed", "executed": False, "doc_id": doc_id}
    meta = _meta(store, doc_id)
    # 남의 문서는 없는 문서와 같게 답한다(SEC-1).
    if meta is None or (owner is not None and meta.get("owner_id") != owner):
        base["error"] = "unknown doc_id"
        return base
    store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.needs_embed = 1",
              {"id": doc_id})
    base.update(executed=True, queued=True)
    return base


def pending_embed_chunks(store, *, limit: int = 16) -> list[dict]:
    """임베딩이 필요한(needs_embed=1) 문서를 **doc 단위**(최대 ``limit`` 개 문서)로,
    각 문서의 전 청크 text 와 함께 낸다 — **허브 자체호스팅 임베딩 워커**용
    (D45-E 후속·B). 워커가 이 text 를 로컬 모델로 임베딩해 :func:`apply_embeddings`
    로 돌려보낸다(원문이 서버 밖으로 안 나감).

    doc 단위로 내는 이유: 문서의 전 청크가 한 배치에 함께 가야 apply 가 needs_embed
    를 깔끔히 내린다(청크 캡으로 쪼개면 큰 문서가 영영 안 끝난다). owner 스코프
    아님 — 허브 서버사이드 유지보수 잡이라 전 소유자 대상. **루프백 내부 표면**
    (공개·프록시 미노출)에서만 호출.

    행마다 그 문서의 ``content_hash`` 를 싣는다. 워커는 이 값을 apply 에 **그대로**
    돌려보내야 한다 — chunk_id 는 위치 기반이라, 인코딩하는 사이 re-put 된 문서에 옛 본문
    벡터가 박히는 것을 apply 가 이 값으로만 가려낸다.

    고르는 순서는 :func:`_fair_pending_docs` 가 정한다(owner 마다 돌아가며)."""
    docs = _fair_pending_docs(store, int(limit))
    out: list[dict] = []
    for d in docs:
        rows = store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d "
            "RETURN c.chunk_id AS chunk_id, c.text AS text ORDER BY c.chunk_index",
            {"d": d["doc_id"]}) or []
        for r in rows:
            out.append({"doc_id": d["doc_id"], "chunk_id": r["chunk_id"],
                        "text": r.get("text") or "",
                        "content_hash": d.get("content_hash") or ""})
    return out


def _fair_pending_docs(store, limit: int) -> list[dict]:
    """임베딩을 기다리는 문서를 owner 마다 돌아가며 고른다.

    한 owner 의 대량 적재가 대기열을 차지하면 다른 owner 의 새 문서가 그 뒤로 통째로 밀려
    의미 검색에서 빠진다. 그래서 가장 오래 기다린 문서를 가진 owner 부터 한 문서씩 차례로
    담고, owner 안에서는 오래 기다린 문서(`updated_at` 이 단조 HLC 라 사전순이 곧 시간순)
    부터 낸다. 한 owner 뿐이면 그 owner 의 오래된 순서 그대로다. 묻는 owner 는 `limit`
    명까지라 쿼리는 많아야 `limit + 1` 번이다."""
    if limit <= 0:
        return []
    owners = store.run(
        "MATCH (d:_DocMeta) WHERE d.needs_embed = 1 "
        "WITH coalesce(d.owner_id, '') AS owner, min(coalesce(d.updated_at, '')) AS since "
        "RETURN owner, since ORDER BY since, owner LIMIT $lim", {"lim": limit}) or []
    queues = []
    for row in owners:
        # owner 마다 limit 개까지 받는다: 문서가 모자란 owner 의 빈자리를 남은 owner 가 채운다.
        queues.append(list(store.run(
            "MATCH (d:_DocMeta) WHERE d.needs_embed = 1 AND coalesce(d.owner_id, '') = $o "
            "RETURN d.doc_id AS doc_id, d.content_hash AS content_hash "
            "ORDER BY d.updated_at, d.doc_id LIMIT $lim",
            {"o": row["owner"], "lim": limit}) or []))
    out: list[dict] = []
    for turn in range(limit):
        for queue in queues:
            if turn < len(queue) and len(out) < limit:
                out.append(queue[turn])
    return out


def _vec_width(store) -> int | None:
    """`ChunkVec.embedding` 의 선언 폭(`FLOAT[n]`의 n). 못 읽으면 None."""
    try:
        for r in store.run("CALL TABLE_INFO('ChunkVec') RETURN *") or []:
            if r.get("name") == "embedding":
                m = re.search(r"\[(\d+)\]", str(r.get("type") or ""))
                return int(m.group(1)) if m else None
    except Exception:          # noqa: BLE001 — 폭을 모르면 호출부가 약한 대조로 물러난다
        return None
    return None


def apply_embeddings(store, doc_id: str, embeddings: dict[str, Any],
                     encoder_runtime: str = "", *, content_hash: str | None = None,
                     model: str | None = None) -> dict:
    """임베딩 적용 전체를 **배타로** 돈다(허브·CLI 공통 단일 지점).

    지금까지의 허브 크래시는 전부 이 적용 도중에 났다. 인덱스 드롭만이 아니라 ChunkVec
    삭제·삽입과 메타 갱신(커밋)까지 다른 커넥션의 스캔과 겹치지 않게 한다. 안의 드롭은
    같은 스레드 재진입이라 교착하지 않는다. 대가: 적용하는 동안 읽기가 기다린다.
    근거: [[record:a-crash-between-a-vector-index-drop-and-the-next-checkpoint-leaves-a-remnant-2026-09-25]]"""
    with DDL_GUARD.exclusive():
        return _apply_embeddings(store, doc_id, embeddings, encoder_runtime,
                                 content_hash=content_hash, model=model)


def _apply_embeddings(store, doc_id: str, embeddings: dict[str, Any],
                      encoder_runtime: str = "", *, content_hash: str | None = None,
                      model: str | None = None) -> dict:
    """워커가 계산한 청크 임베딩을 저장한다(chunk_id 키 — 순서 무관, 편집 재정렬에 강건).

    **값은 벡터 하나 또는 벡터 목록**이다(v8 — 부모-자식). 청크는 2000자 단위인데 모델
    입력 상한은 512토큰이라 한 벡터로는 앞부분만 담긴다(실측: 한국어 42.6%·영어 12.3%
    유실). 그래서 워커가 청크를 ≤500토큰 조각으로 나눠 조각별 벡터를 보내고, 여기서
    `ChunkVec` 자식 행으로 편다. 청크 자체는 **손대지 않는다** — 재청킹이 아니므로
    MENTIONS·발췌·주제 유도가 전부 기준선 그대로다.

    구 형식(벡터 하나)도 받는다 — 자식 1개짜리로 정규화한다(하위호환).
    문서의 모든 청크가 채워지면 needs_embed=0. 벡터가 새로 생기므로 인덱스 드롭 필수.

    ``content_hash``·``model`` 은 그 벡터를 **만든 바탕**이다(워커가 pending 의 해시를
    그대로 돌려주고, 자기 모델을 싣는다). 지금 문서·스토어와 다르면 아무것도 안 쓰고
    거절한다(`content_changed` · `model_changed`) — needs_embed 는 1 로 남아 다음 라운드가
    새 본문으로 다시 한다. None 이면 대조하지 않는다(직접 부르는 CLI·스크립트).
    근거: [[record:reembed-only-requeues-and-apply-checks-what-a-vector-was-built-from-2026-09-30]]"""
    base: dict[str, Any] = {"kind": "doc.embed.apply", "executed": False, "doc_id": doc_id}
    if not embeddings:
        base["error"] = "no embeddings"
        return base
    # 잔해 색인이 있는 `ChunkVec` 에 행을 넣으면 프로세스가 **세그폴트**로 죽는다(예외가
    # 아니다). 거절하면 needs_embed 가 남아 수리(다음 기동) 뒤에 워커가 다시 넣는다.
    # 근거: [[record:a-crash-between-a-vector-index-drop-and-the-next-checkpoint-leaves-a-remnant-2026-09-25]]
    if flag(store, "vector_index_remnant"):
        base["error"] = "vector_index_remnant"
        return base
    rows = store.run("MATCH (c:Chunk) WHERE c.doc_id = $d "
                     "RETURN c.chunk_id AS id, c.owner_id AS owner", {"d": doc_id}) or []
    all_ids = {r["id"] for r in rows}
    owner_of = {r["id"]: (r.get("owner") or "") for r in rows}
    if not all_ids:
        base["error"] = "unknown doc_id"
        return base
    # 이 대조가 경합 없이 성립하는 것은 apply 와 put_doc · switch_embed_model 이 같은
    # 단일 쓰기 큐에서 돌기 때문이다 — 대조와 쓰기 사이에 본문이 바뀔 틈이 없다.
    current = str((_meta(store, doc_id) or {}).get("content_hash") or "")
    if content_hash is not None and current != content_hash:
        base["error"] = "content_changed"
        return base
    if model is not None and stored_embed_model(store) != model:
        base["error"] = "model_changed"
        return base
    # 하나든 여럿이든 목록으로 정규화(첫 원소가 스칼라면 단일 벡터).
    norm = {cid: (val if (val and isinstance(val[0], (list, tuple))) else [val])
            for cid, val in embeddings.items() if cid in all_ids}
    # 폭은 **지우기 전에** 전부 대조한다. 엔진은 틀린 폭을 삽입하는 순간에야 던지므로,
    # 그 뒤라면 옛 자식은 이미 지워졌고 여러 청크 문서는 반쪽만 들어간다.
    # 선언 폭을 못 읽으면(대역 스토어) 적어도 한 폭으로 고르고 비지 않았는지만 본다.
    width = _vec_width(store)
    widths = {len(vec) for many in norm.values() for vec in many}
    ok = widths == {width} if width else (len(widths) == 1 and 0 not in widths)
    if norm and not ok:
        base.update(error="vector_width_mismatch", expected=width, got=sorted(widths))
        return base
    # 워커가 채운 벡터를 박는 자리 — 없던 벡터가 생기므로 드롭이 **필수**다(위와 동종).
    # FTS 는 안 건드리므로 임베딩이 도는 동안에도 키워드 검색이 계속 답한다.
    # 예외는 `keep_vector_index()` 하나다: 측정한 엔진에서 벡터 질의를 배타로 돌리는 동안은
    # 색인을 두고 증분으로 넣는다(이 적용 전체가 이미 배타다).
    # 근거: [[decision:keep-the-chunk-vector-index-behind-a-flag-and-run-each-vector-query-alone-2026-10-03]]
    if not keep_vector_index():
        _drop_vector_index_if_any(store)
    applied = vecs = 0
    for cid, many in norm.items():
        # ⚠ 먼저 **기존 자식을 지운다**. 안 그러면 재임베딩 때 옛 조각이 남아
        # 구·신 벡터가 한 색인에 섞인다 — 에러 없이 검색만 나빠지는 그 사고다.
        store.run("MATCH (v:ChunkVec) WHERE v.chunk_id = $id DETACH DELETE v", {"id": cid})
        for seq, vec in enumerate(many):
            store.run(
                "MERGE (v:ChunkVec {vec_id: $vid}) SET v.chunk_id = $cid, v.doc_id = $doc, "
                "v.owner_id = $own, v.seq = $seq, v.embedding = $emb, "
                "v.encoder_runtime = $runtime",
                {"vid": f"{cid}#{seq:03d}", "cid": cid, "doc": doc_id,
                 "own": owner_of.get(cid, ""), "seq": seq, "emb": list(vec),
                 "runtime": encoder_runtime})
            vecs += 1
        applied += 1
    base["vectors"] = vecs
    # 전 청크가 채워졌을 때만 needs_embed 내림(부분 배치는 다음 라운드가 마저).
    if all_ids <= set(embeddings.keys()):
        store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.needs_embed = 0",
                  {"id": doc_id})
        base["cleared"] = True
        # 이행 표식 내리기 — 백로그가 0 이 된 라운드에서. 여기(워커 apply, writer
        # 경로)가 내리는 이유: `embed_model_state` 는 리더 경로의 진단 전용이라
        # 쓰면 안 된다. 드레인 직후 크래시로 표식이 남는 창은 다음 편집 →
        # 다음 apply 라운드가 같은 분기로 치유한다(그동안 게이트가 한 번 더
        # 내려가 있을 뿐, 방향은 안전하다).
        left = store.run(
            "MATCH (d:_DocMeta) WHERE d.needs_embed = 1 RETURN count(d) AS c") or []
        if not (left and int(left[0].get("c") or 0)):
            store.run("MATCH (m:_SchemaMeta) WHERE m.key = $k DELETE m",
                      {"k": _EMBED_MIGRATING_KEY})
    # 🔴 **워커가 마지막으로 실제 일한 시각**. pending 이 있는데 이 값이 오래됐으면 그
    # 자체가 알람이다 — 진행 지표만으로는 정지를 못 본다
    # ([[record:worker-heartbeat-separates-backlog-from-death]]).
    store.run("MERGE (m:_SchemaMeta {key: $k}) SET m.value = $v",
              {"k": _EMBED_APPLIED_KEY, "v": now_iso()})
    base.update(executed=True, embedded=applied)
    return base


#: 벡터를 만든 모델의 식별자를 적어 두는 자리(`_SchemaMeta`).
_EMBED_MODEL_KEY = "embed_model"


def stored_embed_model(store) -> str | None:
    """기록된 임베딩 모델 하나만 읽는다(리더로 부를 수 있다). 못 읽으면 ``None`` —
    그때 호출부는 모른다고 보고 예전처럼 전환 잡을 돌린다(보수적인 쪽)."""
    try:
        rows = store.run("MATCH (m:_SchemaMeta) WHERE m.key = $k RETURN m.value AS v",
                         {"k": _EMBED_MODEL_KEY}) or []
    except Exception:
        return None
    return (rows[0].get("v") or "") if rows else ""
#: 워커가 마지막으로 `apply_embeddings` 를 성공시킨 시각(ISO). 백로그가 **줄고 있는지**
#: 를 밖에서 판정하는 유일한 신호다 — `/health` 가 이 값을 싣는다.
_EMBED_APPLIED_KEY = "embed_last_applied_at"
#: **모델 이행이 진행 중**이라는 표식(값 = 이행 대상 모델). `switch_embed_model` 이
#: 전량 재큐와 함께 세우고, `apply_embeddings` 가 백로그를 다 비운 라운드에 내린다.
#: 이것이 있어야 "pending 의 출처"가 갈린다 — 모델 이행(전량, 반쪽 커버리지가 순위를
#: 기울임 → 벡터 팔 차단이 발주자 결정)과 일상 문서 편집(한두 건, 그 문서만 의미
#: 검색에서 빠질 뿐 나머지 커버리지는 온전) 이 같은 needs_embed 신호를 쓰기 때문에,
#: 표식 없이는 편집 1건이 의미 검색 전체를 끈다(실측 2026-09-02: "1 docs pending"
#: 창마다 전역 FTS-only 강등).
_EMBED_MIGRATING_KEY = "embed_migration_model"
#: 이행 중에도 벡터 팔은 **남은 문서가 max(이 수, 전체의 이 비율)을 넘을 때만** 끈다.
#: `pending > 0` 이던 게이트는 계속 실패하는 문서 하나가 격리를 되풀이하는 동안 모든
#: 검색을 키워드로만 답하게 했다. 그 정도 섞임은 감수한다는 것이 결정이다.
#: 근거: [[decision:rag-code-review-fixes-run-in-full-in-the-local-rag-session-2026-09-29]]
_MIGRATION_GATE_MIN = 5
_MIGRATION_GATE_RATIO = 0.02


#: pending 이 있는데 이보다 오래 진행(=성공한 apply)이 없으면 정체(stall)로 본다.
#: 기본 30분(워커 기본 주기 5분, `vinc_rag/embed/worker.py` `--interval`의 6배):
#: 콜드스타트(모델 다운로드·양자화, 수 분 걸릴 수 있다)·일시적 부하는 견디면서
#: 죽거나 멈춘 워커는 한 시간 안에 걸린다. 이 모듈은 순수 계산이라 env 를 안
#: 읽는다. 조정하려면 :func:`embed_model_state` 호출부(`api/server.py`)에서
#: `stall_after_s` 를 넘긴다(W14 범위 밖, 현재 호출부는 기본값을 쓴다).
_EMBED_STALL_DEFAULT_S = 1800.0


def embed_model_state(store, *, stall_after_s: float = _EMBED_STALL_DEFAULT_S) -> dict:
    """현재 색인이 **어느 모델로 만들어졌는가**, 그리고 이행 중인가.

    반환 `{"stored", "migrating", "migration_model", "pending", "docs",
    "last_applied_at", "age_s", "stalled"}`. `stored` 가 빈 문자열이면 아직
    아무것도 기록되지 않은 상태다(구 설치·신규 설치).

    `migrating` 은 **모델 이행 중**(전량 재큐, `_EMBED_MIGRATING_KEY` 표식 보유)이고
    남은 문서가 `_MIGRATION_GATE_MIN`·`_MIGRATION_GATE_RATIO` 문턱을 넘을 때만 True 다
    — 일상 문서 편집의 needs_embed 는 표식을 안 세우므로 여기 안 걸리고, 이행 끝의
    안 풀리는 문서 몇 건도 게이트를 붙잡지 못한다. 검색의 벡터 팔 게이트가 이 값을 본다: 편집 1건이 의미 검색 전체를
    끄던 과잉을 이 구분이 걷어낸다(pending 자체는 그대로 보고되고, `stalled` 도
    표식과 무관하게 pending 기준이라 워커 정체 알람은 안 무뎌진다).
    ⚠ 이 배포 전에 시작된 이행은 표식이 없어 `migrating=False` 로 읽힌다 —
    라이브에 진행 중 이행이 없음을 확인하고 들어간 변경이다(2026-09-02).

    `age_s` 는 `last_applied_at`(워커가 마지막으로 청크 임베딩을 실제로 저장에
    성공한 시각, `apply_embeddings` 가 찍는다) 이후 경과 초, 한 번도 성공한 적
    없으면 None. **이 값은 store 에 영속되므로 워커 프로세스가 몇 번을 죽었다
    재기동해도 사라지지 않는다**: "떴다 죽는 프로세스는 확인 순간마다 살아
    있어서 실패 카운터가 매번 리셋된다"는 함정(허브 워치독이 2026-08-28 에 실제로
    물린 것과 같은 모양)이 여기서는 성립하지 않는다. `pid` 가 아니라 **진행**을
    재는 것도 같은 이유다: 살아 있음과 건강함은 다르다
    ([[record:worker-heartbeat-separates-backlog-from-death]]).

    `stalled` 는 청크가 있는 pending 이 남았고 `age_s >= stall_after_s` 일 때 True. `age_s`
    가 None(한 번도 성공한 적 없음)이면 `stalled=False` 로 둔다: 모델 전환 직후
    콜드스타트와 "태어날 때부터 멈춤"을 이 신호 하나로는 구분 못 하기 때문이다
    (ponytail: 필요해지면 `switch_embed_model` 에 requeued_at 도 찍어 그때부터
    잰다. 지금은 오탐이 알람 신뢰를 깎는 쪽이 더 나쁘다고 판단해 보류).

    이 함수는 **진단만** 한다: 정체를 발견해도 워커를 죽이거나 재기동하지
    않는다. 멈춘 프로세스가 단일 writer 락을 쥔 채일 수 있어, 그걸 죽이는 설계는
    이 리포가 이미 여러 번 다친 자리다(`docker-entrypoint.sh` 사이드카 워치독
    주석 참조). 알람은 `/health` 로 노출되고, 끄는 판단은 사람이 한다.

    ⚠ **예외를 삼키고 기본값을 돌려준다.** 그 관용이 :func:`switch_embed_model` 과
    묶이면 위험하다 — 읽기가 조용히 실패하면 `stored` 가 영원히 빈 문자열이고, 그러면
    전환이 매 라운드 "바뀌었다"고 판정해 전 문서를 다시 큐에 넣는다. 그래서 실패
    사유를 버리지 않고 `error` 로 싣는다(`/health` 에 그대로 나간다)."""
    out: dict[str, Any] = {"stored": "", "migrating": False, "migration_model": "",
                           "pending": 0, "pending_without_chunks": 0, "docs": 0, "last_applied_at": "",
                           "age_s": None, "stalled": False}
    try:
        rows = store.run("MATCH (m:_SchemaMeta) WHERE m.key = $k RETURN m.value AS v",
                         {"k": _EMBED_MODEL_KEY}) or []
        out["stored"] = (rows[0].get("v") or "") if rows else ""
        mig = store.run("MATCH (m:_SchemaMeta) WHERE m.key = $k RETURN m.value AS v",
                        {"k": _EMBED_MIGRATING_KEY}) or []
        out["migration_model"] = (mig[0].get("v") or "") if mig else ""
        at = store.run("MATCH (m:_SchemaMeta) WHERE m.key = $k RETURN m.value AS v",
                       {"k": _EMBED_APPLIED_KEY}) or []
        out["last_applied_at"] = (at[0].get("v") or "") if at else ""
        cnt = store.run("MATCH (d:_DocMeta) WHERE d.needs_embed = 1 RETURN count(d) AS c") or []
        out["pending"] = int(cnt[0].get("c")) if cnt else 0
        tot = store.run("MATCH (d:_DocMeta) RETURN count(d) AS c") or []
        out["docs"] = int(tot[0].get("c")) if tot else 0
    except Exception as exc:      # noqa: BLE001 — 진단이 호출부를 죽이지 않게
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    # pending 중 **청크가 하나도 없는** 문서. 워커는 청크 단위로 일감을 받으므로(`pending_embed_
    # chunks`) 이 문서들을 영영 못 보고, needs_embed 도 영영 안 내려간다. 고치는 길은 소유자의
    # 전문 re-put 이나 삭제뿐이다. 이것까지 `stalled` 에 세면 워커가 멀쩡히 놀고 있어도 정체
    # 알람이 켜진다 — 2026-10-02 허브가 하루 종일 pending=2 · stalled 였는데, 그 둘은 put_doc
    # 가 청크를 지운 뒤 끊긴 쓰기 창 문서였다. `pending` 자체는 그대로 둔다(이행 게이트가 본다).
    # 근거: [[vinc.ops.incident-2026-10-02-node-mirror-oom §4]]
    # 따로 감싼다: 이 부가 수치가 실패해도 위의 pending·이행 판정은 그대로 나가야 한다
    # (같은 try 에 두면 이행 게이트가 열린다 — test_server 가 잡았다). 실패하면 0 으로,
    # 즉 예전처럼 pending 전부를 정체 판정에 센다.
    if out["pending"]:
        try:
            ids = [r.get("d") for r in store.run(
                "MATCH (d:_DocMeta) WHERE d.needs_embed = 1 RETURN d.doc_id AS d") or []]
            seen = {r.get("d") for r in store.run(
                "MATCH (c:Chunk) WHERE c.doc_id IN $ids RETURN DISTINCT c.doc_id AS d",
                {"ids": ids}) or []}
            out["pending_without_chunks"] = len(set(ids) - seen)
        except Exception as exc:  # noqa: BLE001
            out["pending_without_chunks_error"] = f"{type(exc).__name__}: {exc}"
    out["migrating"] = bool(out["migration_model"]) and out["pending"] > max(
        _MIGRATION_GATE_MIN, out["docs"] * _MIGRATION_GATE_RATIO)
    age = _age_s(out["last_applied_at"])
    out["age_s"] = None if age is None else round(age)
    workable = out["pending"] - out["pending_without_chunks"]
    out["stalled"] = bool(age is not None and workable > 0 and age >= stall_after_s)
    # `pending` 만 보면 "곧 줄어드는 큐"로 읽힌다. 그런데 이 스토어에 벡터를 만들어
    # 넣는 워커가 **아예 붙지 않은 설치**에서는 그 수가 영원히 안 줄어든다 — 데스크탑
    # 번들은 인코더 스택을 이름으로 제외한다(`packaging/sidecar.spec` 의 excludes).
    # 그 경우 정체는 결함이 아니라 설계이고, `stalled` 알람도 age_s 가 None 이라 안
    # 뜬다. 즉 밖에서 보는 사람에게는 **아무 신호 없이 124 라는 수만** 남는다.
    # 여기서 둘을 가려 주지는 못한다(사이드카는 워커의 존재를 모른다). 대신 두 읽기를
    # 나란히 말한다 — 어느 쪽인지는 워커가 붙었는지로 사람이 가른다.
    if not out["stored"] and not out["last_applied_at"] and out["pending"] > 0:
        out["note"] = ("이 스토어는 벡터를 한 번도 받은 적이 없다. 임베딩 워커가 붙은 "
                       "설치라면 첫 실행 전이고, 안 붙은 설치(데스크탑 기본)라면 이 수는 "
                       "줄지 않는다 — 그때 검색은 키워드 팔 하나로 답한다.")
    return out


def switch_embed_model(store, model: str) -> dict:
    """임베딩 모델이 바뀌었으면 **전량 재임베딩을 건다**(멱등).

    🔴 이게 없으면 모델을 바꿔도 아무 일도 일어나지 않는다. `needs_embed` 는 문서를
    저장할 때만 켜지므로, 모델만 갈아끼우면 옛 모델로 만든 벡터가 그대로 남아 새 쿼리
    벡터와 **다른 공간에서** 비교된다 — 에러 없이 검색만 나빠지는, 이 리포가 반복해서
    당해 온 실패 양식이다. (같은 뿌리의 다른 사례: 청킹 정책을 바꿔도 `content_hash`
    가 그대로라 재청킹이 아예 일어나지 않는다.)

    **기록이 없을 때(`stored == ""`)도 바뀐 것으로 친다.** 그 벡터가 어느 모델로
    만들어졌는지 알 수 없기 때문이다 — 지금 배포가 정확히 그 경우다(옛 bge 벡터 +
    기록 없음). 모르는 것을 맞다고 가정하느니 지우고 다시 만든다. 신규 설치는 문서가
    없어 no-op 이고, 재기동은 기록이 남아 있어 두 번 지우지 않는다.

    바뀌었을 때 하는 일 셋:
      1. **옛 자식 벡터를 전부 지운다.** 남겨 두고 새것을 섞으면 그게 곧 혼재다.
         지우면 그 구간의 벡터 검색은 비지만, 비는 편이 틀린 편보다 낫다(발주자 결정:
         "안전한 방향"). FTS 는 그대로 살아 있어 검색 자체가 죽지는 않는다.
      2. 전 문서 `needs_embed = 1` — 워커가 순차로 다시 채운다.
      3. 새 모델 식별자를 기록 — 다음 기동에서 같은 값이면 아무 일도 하지 않는다(멱등).
    """
    base: dict[str, Any] = {"kind": "embed.model.switch", "executed": False, "model": model}
    if not model:
        base["error"] = "model is required"
        return base
    st = embed_model_state(store)
    base["previous"] = st["stored"]
    if st["stored"] == model:
        base.update(executed=True, changed=False)
        return base
    # 아래 전량 삭제가 `ChunkVec` 을 친다. 거절하면 모델 기록이 안 바뀌므로 수리 뒤 기동이
    # 다시 건다.
    refusal = _remnant_refusal(store, chunks=False)
    if refusal:
        base["error"] = refusal
        return base
    # 인덱스가 걸린 채로 대량 삭제해도 안전하다(실측 2,000청크) — 다만 곧이어 워커가
    # 벡터를 새로 만들 것이므로 어차피 드롭이 필요하다. 여기서 한 번에 처리한다.
    _drop_vector_index_if_any(store)
    store.run("MATCH (v:ChunkVec) DETACH DELETE v")
    store.run("MATCH (d:_DocMeta) SET d.needs_embed = 1")
    store.run("MERGE (m:_SchemaMeta {key: $k}) SET m.value = $v",
              {"k": _EMBED_MODEL_KEY, "v": model})
    # 이행 표식 — 이 pending 은 **전량 재큐**다. 검색 게이트는 이 표식이 있을 때만
    # 벡터 팔을 내린다(발주자 결정 "재임베딩 기간 FTS만" 은 이 경우를 말한 것이고,
    # 일상 편집의 needs_embed 는 이 표식을 안 세우므로 팔이 산다). 내리는 쪽은
    # `apply_embeddings`(백로그 0 이 된 라운드).
    store.run("MERGE (m:_SchemaMeta {key: $k}) SET m.value = $v",
              {"k": _EMBED_MIGRATING_KEY, "v": model})
    cnt = store.run("MATCH (d:_DocMeta) RETURN count(d) AS c") or []
    base.update(executed=True, changed=True, requeued=int(cnt[0].get("c")) if cnt else 0)
    return base


def stale_docs(store, *, owner: str | None = None, limit: int = 50) -> list[str]:
    """재임베딩이 필요한 문서 id(needs_embed=1) — 배경 잡이 순회할 대상."""
    where = "d.needs_embed = 1"
    if owner:
        where += " AND d.owner_id = $o"
    rows = store.run(
        f"MATCH (d:_DocMeta) WHERE {where} RETURN d.doc_id AS id "
        f"ORDER BY d.updated_at LIMIT {max(1, min(int(limit), 500))}",
        {"o": owner} if owner else None) or []
    return [r["id"] for r in rows]
