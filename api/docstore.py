# -*- coding: utf-8 -*-
"""문서 정본(SSoT) 스토어 — Chunk=SSoT 코어 (D45, P1).

Vinc 의 사용자 원문 텍스트 **정본은 Chunk 다** — 별도 문서 blob·외부 파일에
의존하지 않는다(D45 순수형 결정). 이 모듈이 그 계약의 단일 쓰기/읽기 지점:

  put_doc    : raw 텍스트 → 무손실 경계 청킹 → Chunk MERGE(+ 스테일 청크 삭제)
               → _DocMeta 갱신 → MENTIONS 재링크. **파일 I/O 없음.**
  get_doc    : doc_id 의 Chunk 를 오프셋 연결로 원문 복원 + content_hash 검증.
  delete_doc : doc 단위 청크 전체 DETACH DELETE + 정체성 레지스트리 정리.
  list_docs  : owner 스코프 문서 목록(_DocMeta).

무손실 계약(D45 §복원): 청킹은 **경계 슬라이스로 구성**된다 — 경계 목록
b0=0 < b1 < … < bn=len(text) 에서 chunk_i = text[b_i:b_{i+1}]. 정의상
``"".join(chunks) == text`` 가 항상 성립하고(겹침·탈락 구조적으로 불가),
각 청크에 char_start/char_end 를 기록해 get_doc 이 연속성까지 검증한다.

정체성(D45 §정체성): 문서의 조인 키는 경로가 아니라 **doc_id**(내재 식별자,
_DocMeta 정본)다. source_file/origin 은 출처 **표시**로만 남는다(조인 금지).
chunk_id 는 ``{doc_id}#{index:05d}`` — 편집·재청킹에도 위치 안정(F4 해소).

변경감지(F5 해소): _DocMeta.content_hash(sha256) 비교 — 무변경 재적재는
스킵된다(파일명 기반이던 구 체크포인트와 달리 **내용**으로 판정).

쓰기 순서 = 자기치유: ① 구 청크 삭제 → ② 신 청크 기록 → ③ 메타(해시) 기록.
중간 크래시 시 해시가 갱신되지 않아 재-put 이 스킵되지 않고 전체를 재기록한다
(임베디드 단일 프로세스 — 다중 문 트랜잭션 없이도 수렴).

인덱스 수명주기: 벡터/FTS 인덱스가 걸린 열(text·embedding)은 수정 불가
(§5-4 M6) → 변이 전 drop. 재빌드는 검색 경로의 지연 보장
(``ensure_search_indexes``, T3b 자기복구)에 맡긴다 — 문서 1건 편집마다
전체 HNSW 재빌드로 수 초를 태우지 않기 위함.

벡터는 이 모듈의 쓰기가 만들지 않는다: put_doc 은 텍스트만 적재하고 needs_embed=1 을
세운다(FTS 는 즉시 답한다). 검색이 읽는 `ChunkVec` 을 채우는 길은 워커의
``pending_embed_chunks`` → ``apply_embeddings`` 하나뿐이다.
"""
from __future__ import annotations

import calendar
import hashlib
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

from engines import domain_hierarchy
from store import pkns
from store.backend import flag, operation
from store.embedded_backend import DDL_GUARD, keep_vector_index
from store.lexical import lex_text
from store.querybase import now_iso, validity_state
from store.topic_conversion_guard import guard_active
# 청킹은 순수 함수라 아래 층에 있다. 임베딩 워커와 리랭커(vinc_rag)도 같은 경계를 쓴다.
from store.chunking import (DEFAULT_MAX_CHARS, _HEADING_RE, _section_title,  # noqa: F401
                            chunk_doc, estimate_tokens, normalize_boundaries,
                            plan_boundaries)

from . import graphmd
from . import node_schema
from .authoring import document_retrieval
# 리더 도우미와 개념 추출은 두 모듈에 산다. 호출자와 테스트가 `docstore.X` 로 부르므로 여기서
# 같은 객체를 다시 내보낸다. 두 모듈은 이 모듈을 최상위에서 import 하지 않는다(순환 방지).
from .docextract import (  # noqa: F401
    _ATX_RE, _CLAUSE_OPENERS, _FENCE_RE, _FM_DOMAIN_RE, _HEAD_NUM_RE, _LIST_RE,
    _MAX_HEADING_LEN, _MAX_TITLE_WORDS, _MD_EMPH_RE, _MD_LINK_RE, _MD_PREFIX_RE,
    _ONE_LINER_MAX, _SENT_END_RE, _SLUG_RE, _STRUCT_SLUGS, _UNDERLINE_RE, _clean_heading,
    _concept_shaped, _doc_topic, _extract_body_doc, _front_domain, _headings, _one_liner,
    _slug, _strip_md, _title_shaped, extract_concepts, extract_doc)
from .docreader import (  # noqa: F401
    _HL_TOKEN_RE, _MENTION_TARGETS, _concept_terms, _editor_offsets, _heading_level,
    doc_context, doc_highlights, doc_sections, highlight_ranges)
# 관계 근거 백필은 `api.docevidence` 에 산다. 같은 규칙으로 다시 내보낸다.
from .docevidence import _EVIDENCE_MAX, _sentences, backfill_evidence  # noqa: F401
# 임베딩 상태·모델 이행은 `api.docembed` 에 산다. 같은 규칙으로 다시 내보낸다(`scripts/repair_hub_store.py`
# 가 `embed_model_state`·`_EMBED_MIGRATING_KEY` 를, 서버와 CLI 가 나머지를 `docstore.X` 로 부른다).
from .docembed import (  # noqa: F401
    _EMBED_APPLIED_KEY, _EMBED_MIGRATING_KEY, _EMBED_MODEL_KEY, _EMBED_STALL_DEFAULT_S,
    _MIGRATION_GATE_MIN, _MIGRATION_GATE_RATIO, _apply_embeddings, _fair_pending_docs, _vec_width,
    apply_embeddings, embed_model_state, pending_embed_chunks, reembed_doc, stale_docs,
    stored_embed_model, switch_embed_model)

#: `concept:doc.<doc_id>` — 문서의 개념 그래프 쪽 자리(미러). 그 `domain` 은 **사람이
#: 적은 주제**이고, 본문 매칭에서 나오는 파생 주제보다 우선한다.
#: ⚠ `api.query._DOC_CONCEPT_PREFIX` 와 같은 문자열이어야 한다 — 두 모듈이 서로를
#: 모듈 수준에서 임포트하지 않으므로 사본이 둘이고, 테스트가 바이트로 고정한다
#: (`_ALLOWED_PAIRS_HINT` 와 같은 처방).
_DOC_MIRROR_PREFIX = "concept:doc."


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def age_s(iso_ts: str) -> float | None:
    """`now_iso()` 문자열 → 지금까지 경과 초. 비어 있거나 못 읽으면 None(모른다).

    UTC 로 찍은 문자열이라 되돌릴 때도 지역시간 가정이 없는
    `calendar.timegm` 을 쓴다(`time.mktime` 은 로컬 타임존을 가정해 틀린다)."""
    if not iso_ts:
        return None
    try:
        return max(0.0, time.time() - calendar.timegm(time.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ")))
    except Exception:
        return None


# 사설 별칭. 같은 함수이고, 이 이름으로 부르는 호출부·테스트가 그대로 돈다(`api.docembed` 는 공개 이름을 쓴다).
_age_s = age_s


def make_doc_id(owner: str, content_hash: str, title: str = "", origin: str = "") -> str:
    """결정론 doc_id — (owner, 최초 내용 해시, title, origin) 파생. 같은 문서의
    중복 최초-적재가 자연 dedupe 되고(동일 입력 = 동일 id = 멱등 MERGE), 기기 간
    오프라인 재적재도 같은 정체성으로 수렴한다. **편집 후에도 id 는 불변** —
    put_doc 에 doc_id 를 넘겨 갱신하므로 내용이 바뀌어도 정체성은 유지된다."""
    basis = f"{owner}|{title}|{origin}|{content_hash}"
    return "d" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def resolve_conventions_doc_id(store, owner: str | None) -> str:
    """이 owner 의 관례 문서가 **실제로 앉아 있는** doc_id — 읽기와 쓰기가 같이 쓴다.

    구 고정 id(`graph.md`)는 전 시스템에 행이 하나뿐이라 먼저 쓴 owner 가 점유한다.
    그 행을 owner 파생 id 로 **옮기지 않는다**: 옮김은 마이그레이션이고, 여기서 얻을
    것이 id 의 생김새뿐인데 잃을 것은 되돌릴 수 없는 쓰기다. 대신 점유자는 제자리를
    계속 쓰고, 그 밖의 전원은 owner 파생 id 를 쓴다 — 그것만으로 충돌이 닫힌다.
    근거: [[vinc.ops.risk-audit-2026-08-05 §3.4]]

    ⚠ 이 판정을 건너뛰고 `graphmd.conventions_doc_id` 를 직접 쓰면, 점유자의 기존
    관례가 안 보이고 둘째 사본이 생긴다."""
    if owner:
        legacy = _meta(store, graphmd.LEGACY_CONVENTIONS_DOC_ID)
        if legacy is not None and legacy.get("owner_id") == owner:
            return graphmd.LEGACY_CONVENTIONS_DOC_ID
        # 원문 이동으로 소유자 파생 id의 원소유자와 현소유자가 달라질 수 있다.
        owned = store.run("MATCH (d:_DocMeta) WHERE d.owner_id=$owner AND d.doc_id STARTS WITH $prefix "
                          "RETURN d.doc_id AS doc_id", {"owner": owner, "prefix": "graph.md~"})
        reserved = [r["doc_id"] for r in owned if graphmd.is_conventions_doc_id(r["doc_id"])]
        if len(reserved) > 1:
            raise ValueError("ambiguous_owner_conventions")
        if reserved:
            return reserved[0]
    return graphmd.conventions_doc_id(owner)


def chunk_id_of(doc_id: str, index: int) -> str:
    """위치 안정 chunk_id(F4) — 내용이 아니라 (doc, 순번) 에 종속. 편집 시 같은
    자리의 청크가 같은 id 로 MERGE 되고, 줄어든 꼬리만 삭제된다."""
    return f"{doc_id}#{index:05d}"


# ── DB 경계 ──────────────────────────────────────────────────────────────────

def doc_meta(store, doc_id: str, *, strict=False) -> dict | None:
    rows = store.run(
        "MATCH (d:_DocMeta) WHERE d.doc_id = $id RETURN d.doc_id AS doc_id, "
        "d.owner_id AS owner_id, d.title AS title, d.origin AS origin, "
        "d.content_hash AS content_hash, d.chunk_count AS chunk_count, "
        "d.updated_at AS updated_at, d.needs_embed AS needs_embed, "
        # v7(D72) — put_doc 이 created_at 을 보존하려면 먼저 읽어야 한다.
        "d.created_at AS created_at, d.content_time AS content_time, "
        # v22 — 문서 유효구간. put_doc 이 3값(미지정 보존)을 지키려면 먼저 읽어야 한다.
        "d.valid_from AS valid_from, d.valid_until AS valid_until, "
        "d.superseded_by AS superseded_by", {"id": doc_id})
    from .operation_receipts import in_transaction
    if (strict or in_transaction.get()) and rows is None:
        raise RuntimeError("document_metadata_unavailable")
    return rows[0] if rows else None


# 사설 별칭. 같은 함수이고, 이 이름으로 부르는 호출부·테스트가 그대로 돈다(`api.docembed` 는 공개 이름을 쓴다).
_meta = doc_meta


def _live_chunks(store, doc_id: str, *, strict=False) -> dict[str, int]:
    """doc_id 로 살아 있는 Chunk 행 수를 청크의 owner_id 별로 센다('' = 무소유)."""
    rows = store.run("MATCH (c:Chunk) WHERE c.doc_id = $d "
                     "RETURN c.owner_id AS o, count(c) AS n", {"d": doc_id})
    from .operation_receipts import in_transaction
    if (strict or in_transaction.get()) and rows is None:
        raise RuntimeError("document_chunks_unavailable")
    rows = rows or []
    return {str(r.get("o") or ""): int(r.get("n") or 0) for r in rows}


def chunks_intact(store, doc_id: str) -> bool:
    """메타가 있고 살아 있는 청크 행 수가 메타의 `chunk_count` 와 같은가.

    무변경 스킵은 이 조건 위에서만 안전하다. 안 맞는 문서(쓰기 창 표식 -1, 꼬리 청크,
    빠진 청크)는 get_doc 이 거절하므로 같은 본문 re-put 이 곧 치유다."""
    meta = _meta(store, doc_id)
    return meta is not None and \
        sum(_live_chunks(store, doc_id).values()) == int(meta.get("chunk_count") or 0)


# ── 내용 시점(D72) ───────────────────────────────────────────────────────────
# 형식 검사기의 정본은 `node_schema.normalize_time` 이다. 사설 이름은 같은 함수를 가리키며
# 이 모듈 안의 호출과 그 이름을 읽는 테스트가 쓴다.
_norm_content_time = node_schema.normalize_time


#: 본문 첫머리 프런트매터가 스스로 밝히는 doc_id. **그 값은 스토어의 id 가 아니다** —
#: 선언일 뿐이고, 실제 id 는 `put_doc` 에 준 것(또는 생략 시 합성된 해시)이다.
_FM_DOC_ID_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
_FM_ID_LINE_RE = re.compile(r"^doc_id\s*:\s*(\S+)\s*$", re.M)


def _declared_doc_id(text: str) -> str:
    """본문이 선언한 doc_id(없으면 ''). **읽기만 한다 — 본문은 절대 안 고친다.**

    text 는 Chunk=SSoT 의 정본이고 `get_doc` 의 `verified` 는 이 바이트의 sha256 이다.
    여기서 한 글자만 고쳐도 그 검증이 거짓이 된다."""
    m = _FM_DOC_ID_RE.match(text or "")
    if not m:
        return ""
    line = _FM_ID_LINE_RE.search(m.group(1))
    return (line.group(1).strip() if line else "").strip("\"'")


def _doc_id_warnings(text: str, doc_id: str) -> list[str]:
    """본문이 선언한 id 와 실제로 쓰는 id 가 다르면 말한다.

    2026-08-25 에 이 침묵이 사본을 하나 만들었다. `d4ab94561078498e3` 의 본문은
    `doc_id: vinc.ops.graph-claim-decay` 라고 선언하는데 그건 그 문서의 id 가 아니다.
    그 값을 읽어 `put_doc` 에 넘겼더니 고쳐지지 않고 **제목이 같은 문서가 하나 더**
    생겼고, 신호는 성공 응답 안의 `replaced: 0` 뿐이었다.

    거부하지 않는 이유: 동기화(`api/docsync`)와 팩 가져오기가 같은 깔때기를 쓰고,
    거기 오는 문서들은 프런트매터에 정당하게 자기 이름을 적고 있다. 막으면 복제가
    선다. 침묵을 고치는 데 필요한 것은 거부가 아니라 말이다."""
    declared = _declared_doc_id(text)
    if declared and doc_id and declared != doc_id:
        return [f"the body declares doc_id '{declared}' but this write targets "
                f"'{doc_id}' — the body's declaration is not the store id, and "
                f"using it as one creates a second document"]
    return []


# 프런트매터의 관계 줄. 본문 텍스트일 뿐이라 저장이 엣지로 바꾸지 않는다.
_FM_RELATION_KEYS = ("inherits_from", "derives_from", "related", "supersedes")


def _no_mirror_warnings(store, text: str, doc_id: str, *, owner: str) -> list[str]:
    """쓰기가 끝났는데 `concept:doc.<id>` 미러가 없고, 프런트매터가 주제나 관계를
    적어 뒀으면 말한다. 그 둘은 미러가 있어야 붙는데 아무것도 적용되지 않았다.

    미러는 `topic` 을 받을 때만 생긴다(`_set_authored_topic`). 이 경고가 없으면 topic 없는
    쓰기가 성공만 답하고, 그 문서는 토픽도 관계도 없이 남는다. 근거 기록의 id 는
    `tests/core/test_doc_put_warns_without_mirror.py` 머리에 있다.

    프런트매터가 아무것도 적지 않은 문서는 조용하다. 동기화·팩 가져오기·주제 설명이
    같은 깔때기로 topic 없이 쓰고, 그 경우까지 말하면 경고가 소음이 된다."""
    m = _FM_DOC_ID_RE.match(text or "")
    if not m:
        return []
    block = m.group(1)
    dm = _FM_DOMAIN_RE.search(block)
    declared = dm.group(1).strip() if dm else ""
    rels = [k for k in _FM_RELATION_KEYS if re.search(rf"^{k}\s*:", block, re.M)]
    if not declared and not rels:
        return []
    mid = pkns.key(owner, _DOC_MIRROR_PREFIX + doc_id)
    if store.run("MATCH (n:Concept) WHERE n.concept_id = $id RETURN n.concept_id AS id",
                 {"id": mid}):
        return []
    parts = [f"concept:doc.{doc_id} does not exist, so this document has no topic and "
             f"no relations: pass topic to create it"]
    if declared:
        parts.append(f"the front matter's domain: {declared} is text and was not applied")
    if rels:
        parts.append(f"the front matter's {', '.join(rels)} lines are text, not edges; "
                     f"send them with vinc_ingest from concept:doc.{doc_id}")
    return ["; ".join(parts)]


def _stamp_updated_at(store) -> str:
    """정본 갱신 시각 — 스토어 내 단조 HLC(revstamp). 벽시계 스큐에도 동기화
    LWW 비교가 성립하도록. revstamp 미가용(경량 스토어)이면 벽시계로 강등."""
    try:
        from . import revstamp
        return revstamp.hlc_now(store)
    except Exception:
        return now_iso()


def _next_doc_seq(store, owner: str) -> int:
    """owner별 단조 doc sync_seq(P3 delta 커서). revstamp 카운터 재사용 —
    노드 sync 와 같은 지평의 커서를 공유하지 않도록 'doc:' 네임스페이스로 분리."""
    try:
        from . import revstamp
        return revstamp.next_seq(store, f"doc:{owner}")
    except Exception:
        return 0


def _node_stamp(store, owner: str, rev: int = 0) -> dict:
    """노드 추적열 {updated_at, rev, sync_seq} — 이 모듈이 **생 Cypher 로** 쓰는
    Concept 에 붙인다(위 `_stamp_updated_at` 의 노드판).

    왜 필요한가(실측 2026-08-17): `extract_doc` 은 개념을 fragment 를 거치지 않고
    직접 MERGE 하면서 이 셋을 안 찍었다. `CREATE NODE TABLE` 은 열 기본값을 안 내므로
    값은 0 이 아니라 **NULL** 이 되고, delta 술어 `c.sync_seq > $since` 는 NULL 을
    배제한다 — 그래서 최초 전량 push(`since=0`) 이후에 문서가 만든 개념은 **어느 기기
    에도 영영 가지 않았다**(오류도 경고도 없이 노드 통째로). 재현: 전량 push 로 커서를
    1 로 올린 뒤 추출로 개념을 만들고 `since=1` 을 뽑으면 그 개념만 빠진다.

    fragment 로 라우팅해 재사용하는 편이 깔끔하지만 그쪽은 `status`·`stem`·
    `source_file` 을 못 써서(이 경로가 쓰는 열들) 못 옮긴다. 그래서 열 셋만 여기서 붙인다.

    revstamp 미가용(경량 스토어)이면 `_stamp_updated_at` 과 같은 태도로 강등한다."""
    try:
        from . import revstamp
        return revstamp.make_stamp(store, {"owner": owner, "rev": rev})
    except Exception:
        return {"updated_at": now_iso(), "rev": int(rev or 0) + 1, "sync_seq": 0}


def doc_id_for_origin(owner: str, origin: str) -> str:
    """origin(파일 경로 등 **안정 외부 식별자**) 기반 결정론 doc_id(P2). 같은
    파일의 재적재가 같은 doc 로 수렴한다(내용 해시 기반 make_doc_id 와 달리 편집에도
    id 불변) — 배치 파일 이관의 정체성 원천."""
    return "d" + hashlib.sha256(f"{owner}|origin|{origin}".encode("utf-8")).hexdigest()[:16]


def doc_hash(store, doc_id: str) -> str | None:
    """doc 의 현재 content_hash(없으면 None) — 배치 증분이 임베딩·청킹 **전에**
    무변경을 판정해 스킵하기 위한 값(F5 대체: 파일명 아닌 내용 기반)."""
    m = _meta(store, doc_id)
    return m.get("content_hash") if m else None


def _clear_doc_tombstone(store, doc_id: str) -> None:
    try:
        store.run("MATCH (t:_DocTombstone) WHERE t.doc_id = $id DELETE t", {"id": doc_id})
    except Exception:
        pass


def drop_vector_index_if_any(store) -> None:
    """**벡터가 새로 생기는 쓰기** 직전에만 부른다(멱등). 재빌드는 검색 경로의
    ensure_search_indexes 지연 보장(T3b)이 담당 — 여기서 재빌드하지 않는다.

    ⚠ 이름과 범위가 2026-07-31 에 좁아졌다. 예전엔 `_drop_indexes_if_any` 로 **FTS 까지**
    드롭했고 문서 저장·삭제·임베딩 적용 네 경로가 전부 그걸 불렀다. 실측하니 그 설계가
    **임베딩이 도는 동안 키워드 검색을 100% 실패시키고 있었다**(60라운드 중 FTS 성공 0회).
    지금 유일하게 동작하는 검색이 FTS 인데 배경 작업이 주기적으로 그걸 끄고 있었다.

    벡터만 드롭해야 하는 이유와, 그마저도 모든 경로에서 필요하지는 않은 이유는
    :meth:`store.embedded_backend.EmbeddedBackend.drop_vector_index` 주석에 실측과 함께 있다.
    요약: **"벡터가 없던 자리에 벡터가 생기는 것"만 위험하다**(HNSW 삽입 → 수백 건에서
    세그폴트). 벡터 없는 청크 삽입·대량 삭제·기존 벡터 값 변경은 전부 안전하다(실측).

    확장 미가용 환경에서는 로드가 실패하는데 그런 스토어엔 인덱스가 존재할 수도 없다 —
    **정본 쓰기를 막을 이유가 아니다**(SSoT 는 검색 확장 없이도 성립해야 한다).
    구 백엔드 호환: `drop_vector_index` 가 없으면 넓은 쪽으로 물러난다."""
    fn = operation(store, "drop_vector_index") or operation(store, "drop_search_indexes")
    if fn is None:
        return
    try:
        fn()
    except Exception as exc:
        if "확장 미가용" in str(exc):   # `_load_ext` 의 강등 문구: 위 단락의 경우만 삼킨다
            return
        # 그 밖의 실패(DDL 가드 시간 초과·다른 커넥션의 쓰기 트랜잭션 등) = 인덱스가 **아직
        # 걸려 있다.** 삼키고 벡터를 넣으면 그게 세그폴트 경로다. 쓰기를 실패시킨다(잡이
        # 재시도한다). '색인 없음'은 백엔드 드롭(`_drop_index`)이 이미 흡수했다.
        # 근거: [[record:a-crash-between-a-vector-index-drop-and-the-next-checkpoint-leaves-a-remnant-2026-09-25]]
        raise


# 사설 별칭. 같은 함수이고, 이 이름으로 부르는 호출부·테스트가 그대로 돈다(`api.docembed` 는 공개 이름을 쓴다).
_drop_vector_index_if_any = drop_vector_index_if_any


def remnant_refusal(store, *, chunks: bool = True, vectors: bool = True) -> str | None:
    """이 쓰기가 건드릴 표(`Chunk`·`ChunkVec`)에 잔해 색인이 확인됐으면 그 사유(`error` 값).

    잔해가 있는 표의 쓰기는 예외가 아니라 **세그폴트**다(삽입·삭제·아무 열의 SET 전부 —
    실측). 백엔드도 그 문장을 거절하지만(`EmbeddedBackend.run`) 그건 쓰기 **도중**에
    던지므로 앞 문장들만 들어간 반쪽 쓰기가 남는다. 여기서 쓰기 전에 멈춘다. 수리는
    재기동이 하고(`heal_fts_index`·`heal_vector_index`), 그 뒤 같은 쓰기를 다시 하면 된다.
    근거: [[record:a-remnant-segfaults-every-write-to-its-table-not-only-inserts-2026-09-25]]"""
    if chunks and flag(store, "fts_index_remnant"):
        return "fts_index_remnant"
    if vectors and flag(store, "vector_index_remnant"):
        return "vector_index_remnant"
    return None


# 사설 별칭. 같은 함수이고, 이 이름으로 부르는 호출부·테스트가 그대로 돈다(`api.docembed` 는 공개 이름을 쓴다).
_remnant_refusal = remnant_refusal


def _with_fts_repair(store, fn: Callable[[], Any]) -> Any:
    """FTS 색인 불일치로 실패하면 색인을 재빌드하고 **1회만** 재시도한다.

    왜 필요한가(2026-08-09 실측). ladybug 0.18.0 의 FTS 증분 유지가 시간이 지나며
    존재하는 행의 문서를 잃는다. 그 행을 지우려는 순간 엔진이 이렇게 던진다:
    (0.20.4 의 FTS 확장도 같은 문구를 그대로 싣고, 같은 계열의 `term '...' is missing
    during delete` 문구가 하나 더 있다 — 둘 다 판별 표지에 걸린다. 0.20.0 이 FTS 질의와
    커밋 경합의 힙 손상을 고쳤지만 이 표류의 원인인지는 입증되지 않아 수리는 그대로 둔다.)

        FTS index 'chunk_ft' is inconsistent: document for node offset 992 is
        missing during delete. Drop and recreate the FTS index.

    처방을 오류 문구가 직접 말해 주고, 백엔드에는 `rebuild_fts_index()` 가 있는데,
    **라이브 허브에서 그걸 부를 길이 없었다.** 그래서 6초짜리 조작이 세 번 다 하드다운
    + 파일 수술이 됐다(08-04 · 08-07 · 08-09). 이 함수가 그 간극이다.

    설계 의도 두 가지:

    - **새 표면을 만들지 않는다.** 관리용 엔드포인트나 MCP 도구를 늘리면 프록시
      허용목록을 세 곳에 등재해야 하고(웹 패리티), 어긋나면 그 기능만 조용히 죽는다.
      이건 쓰기 경로 안에서 끝난다.
    - **조건을 좁게 잡는다.** 엔진이 스스로 "inconsistent" 라고 말한 경우에만 돈다
      (:meth:`EmbeddedBackend.is_fts_inconsistent`). 아무 예외에나 재빌드를 걸면
      진짜 결함을 5.8초짜리 재시도로 덮어 버린다.

    재시도는 1회다. 재빌드 후에도 같은 오류면 그건 다른 문제이므로 그대로 올린다.

    이 패턴은 새로 만든 게 아니다 — `ensure_search_indexes`(색인 **부재**를 검색
    경로에서 지연 복구, T3b)의 형제 조건이다. 그쪽은 없는 색인을 만들고 이쪽은
    어긋난 색인을 다시 만든다."""
    try:
        return fn()
    except Exception as exc:
        checker = operation(store, "is_fts_inconsistent")
        rebuild = operation(store, "rebuild_fts_index")
        if rebuild is None or checker is None or not checker(exc):
            raise
        # 조용히 고치지 않는다 — 재빌드가 돌았다는 사실이 로그에 남아야 이 결함이
        # 얼마나 자주 나는지 셀 수 있다. 무음 자기복구는 "안 나는 것"처럼 보인다.
        print(f"[WARN] FTS 색인 불일치 — 재빌드 후 1회 재시도: "
              f"{str(exc).splitlines()[0][:200]}", file=sys.stderr)
        # 소요 시간을 남긴다 — 로컬 실측(5.8초)이 허브 코퍼스에서도 같은지는
        # 추정이 아니라 로그로 알아야 한다. 이 호출이 도는 동안 /health 가 굶을
        # 수 있다는 것이 이 재구축의 실제 위험이므로(entrypoint.sh 의
        # sidecar_healthy 주석 참고) 소요 시간이 그대로 진단이다.
        # 근거: [[vinc.manual.core §3]] · [[vinc.plan.korean-retrieval-p1-p2-2026-10-02]]
        _t0 = time.monotonic()
        rebuild()
        print(f"[WARN] FTS 재빌드 완료 — {time.monotonic() - _t0:.1f}s", file=sys.stderr)
        return fn()


#: 문서 유효구간 형식(Record 와 같은 계약).
_VALIDITY_FORMAT = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def put_doc(store, **kwargs) -> dict:
    """:func:`_put_doc_once` 를 트랜잭션 하나로 쓴다(`store.write_batch`). 청크·미러·링크를 문장마다
    커밋하지 않는다. 안의 문장이 실패하면(FTS 수리 포함) 묶음을 버리고
    한 문장씩 다시 쓴다(`store.write_batch.run_batched`)."""
    from store import write_batch
    result, _first = write_batch.run_batched(store, lambda: _put_doc_once(store, **kwargs),
                                             what="doc.put")
    return result


def _put_doc_once(store, *, owner: str, text: str, doc_id: str | None = None,
                  title: str | None = None, origin: str | None = None, layer: str = "ssot",
                  max_chars: int = DEFAULT_MAX_CHARS,
                  boundaries: list[int] | None = None,
                  link: bool = True, updated_at: str | None = None,
                  created_at: str | None = None, content_time: str | None = None,
                  topic: str | None = None, valid_from: str | None = None,
                  valid_until: str | None = None, superseded_by: str | None = None,
                  expected_content_hash: str | None = None, create_only: bool = False) -> dict:
    """raw 텍스트를 doc 단위로 적재/갱신(파일 I/O 없음) — Chunk=SSoT 의 쓰기 정본.

    - 신규: doc_id 미지정 시 결정론 파생(make_doc_id). 반환 doc_id 로 이후 편집.
    - 갱신: 기존 doc_id + 변경 텍스트 → 구 청크 삭제 후 재청킹(고아 0, F3 해소).
    - 무변경: content_hash 동일 → skipped(F5 — 내용 기반 변경감지).
    - SEC-1: 기존 문서의 owner 불일치 → 거부(남의 정본을 재-put 으로 탈취 불가).

    ``boundaries`` (P2): 외부 청커(chonkie)의 시작 오프셋 후보 — 무손실 정규화 후
    검색 청크로 사용(없으면 내장 경계). ``layer``: 검색 pre-filter 태그(파일 배치
    이관 시 원 레이어 보존). ``updated_at`` (P3): 동기화 적용 시 원격 HLC 를 그대로
    박기 위한 주입점(로컬 쓰기는 None → 스토어 HLC 스탬프).

    세 단계를 차례로 부른다: 준비(:func:`_put_prepare`), 청크 다시 쓰기
    (:func:`_put_rewrite_chunks`), 미러와 링크(:func:`_put_mirror_and_links`). 본문이 그대로면
    가운데 단계 대신 :func:`_put_unchanged_body` 가 답한다. 모두 이 호출 안에서 돌아
    `put_doc` 의 쓰기 묶음 하나에 든다."""
    # 어느 소유자로 썼는지 밝힌다(fragment.merge_fragment 의 같은 주석 참조 —
    # 문서 정본도 owner 스코프이고 오배치가 조용하기는 마찬가지다).
    base: dict[str, Any] = {"kind": "doc.put", "executed": False, "owner": owner}
    plan = _put_prepare(store, base, owner=owner, text=text, doc_id=doc_id, title=title,
                        origin=origin, content_time=content_time, topic=topic,
                        valid_from=valid_from, valid_until=valid_until,
                        superseded_by=superseded_by,
                        expected_content_hash=expected_content_hash, create_only=create_only)
    if isinstance(plan, dict):
        return plan
    if plan.unchanged:
        return _put_unchanged_body(store, base, plan, text=text, origin=origin, link=link,
                                   updated_at=updated_at, topic=topic)
    prev, n_live = plan.prev, plan.n_live

    # 청크는 언제나 쓰고, 지울 옛 행이 있으면 자식 벡터도 지운다 — 그 표에 잔해가 있으면 멈춘다.
    wipe = prev is not None or n_live > 0
    refusal = _remnant_refusal(store, vectors=wipe)
    if refusal:
        base["error"] = refusal
        return base

    chunks, stamp, seq, removed, needs_embed = _put_rewrite_chunks(
        store, plan, text=text, layer=layer, max_chars=max_chars, boundaries=boundaries,
        updated_at=updated_at, created_at=created_at, wipe=wipe)
    return _put_mirror_and_links(store, base, plan, text=text, title=title, topic=topic,
                                 link=link, chunks=chunks, stamp=stamp, seq=seq,
                                 removed=removed, needs_embed=needs_embed)


@dataclass
class _PutPlan:
    """준비 단계(:func:`_put_prepare`)가 읽고 정한 값. 뒤 단계는 스토어를 다시 읽지 않고 이것을 쓴다."""
    owner: str
    doc_id: str
    content_hash: str
    prev: dict | None
    n_live: int
    kept_title: str
    kept_origin: str
    validity: dict
    validity_changed: bool
    ctime: str
    ctime_changed: bool
    ctime_warnings: list
    unchanged: bool


def _put_prepare(store, base: dict, *, owner, text, doc_id, title, origin, content_time, topic,
                 valid_from, valid_until, superseded_by, expected_content_hash,
                 create_only) -> "_PutPlan | dict":
    """준비: 입구 검사, doc_id 확정, 저장된 메타·청크 읽기, 3값 필드(제목·출처·유효구간·내용 시점)
    해석, 소유와 조건부 쓰기 검사. 거절이면 그 응답(dict)을, 아니면 계획을 돌려준다.
    ``base`` 에 `doc_id` 를 적는다(거절 응답이 그 값을 싣는다)."""
    if not owner or not isinstance(owner, str):
        base["error"] = "owner is required — 정본(SSoT)은 무소유로 두지 않는다(SEC-1)"
        return base
    if not isinstance(text, str):
        base["error"] = "text must be a string"
        return base
    # PK 네임스페이싱(design A) 판별자 U+001F 가 id·문자열 필드에 섞이면 안 된다
    # (strip 이 이 바이트로 owner 를 잘라내므로 물리 키로 오인된다). text 는 원문
    # 정본(D45)이라 제외 — 검열이 아니라 키 재료가 되는 필드만 본다.
    # 근거: [[vinc.plan.node-pk-namespacing]] §7
    if pkns.has_sep((owner, doc_id, title or "", origin or "")):
        base["error"] = "owner/doc_id/title/origin must not contain U+001F (reserved for PK namespacing)"
        return base

    conditional = expected_content_hash is not None or create_only is not False
    content_hash = _sha(text)
    # 옛 고정 id(`graph.md`)를 그대로 지목한 쓰기(MCP `vinc_doc_put` · API)가 **남이 점유한**
    # 그 행을 겨누면 이 owner 의 파생 id 로 돌린다. 안 돌리면 점유자 아닌 전원이
    # `owner mismatch` 로 거절된다(Vinc #342). 점유자 자신과 빈 자리는 그대로 둔다.
    if doc_id == graphmd.LEGACY_CONVENTIONS_DOC_ID:
        try:
            seat = _meta(store, doc_id, strict=True) if conditional else _meta(store, doc_id)
        except Exception:
            return {**base, "error": "write_precondition_unavailable", "status": "query_failed"}
        if seat is not None and seat.get("owner_id") != owner:
            if conditional:
                return {**base, "error": "document_unavailable", "status": "unavailable"}
            doc_id = graphmd.conventions_doc_id(owner)
    doc_id = doc_id or make_doc_id(owner, content_hash, title or "", origin or "")
    if guard_active(store):
        from .topic_conversion import check_write
        check_write(store, owner, {"id": doc_id, "topic": topic})
    base["doc_id"] = doc_id

    try:
        prev = _meta(store, doc_id, strict=True) if conditional else _meta(store, doc_id)
        live = _live_chunks(store, doc_id, strict=True) if conditional else _live_chunks(store, doc_id)
    except Exception:
        return {**base, "error": "write_precondition_unavailable", "status": "query_failed"}
    n_live = sum(live.values())
    # title 은 `content_time` 과 같은 3값이다: **키 부재(None)=기존 보존 · ""=비움 ·
    # 값=설정.** 여태 무조건 SET 이라 본문만 재전송한 호출이 제목을 빈 문자열로 덮었고,
    # 그 덮임이 아래 skip 비교("해시와 제목이 둘 다 같을 때만 skip")를 어긋내
    # **청크 전량 삭제·재작성**까지 끌고 갔다 — 무해해야 할 재전송이 파괴적 쓰기가 된다.
    # 노드 쪽은 `fragment._KEPT_FIELDS` 로 같은 수술이 끝나 있었고 문서 쪽만 남아 있었다.
    kept_title = title if title is not None else str((prev or {}).get("title") or "")
    kept_origin = origin if origin is not None else str((prev or {}).get("origin") or "")
    # v22 — 문서 유효구간도 같은 3값이다(키 부재=보존 · ""=비움 · 값=설정). 형식은
    # Record 와 같은 `YYYY[-MM[-DD]]` 이고 어긋나면 **쓰지 않고 거절한다**: 조용히 비우면
    # 은퇴시키려던 문서가 현재로 남는다.
    validity = {}
    for key, val in (("valid_from", valid_from), ("valid_until", valid_until)):
        if val is None:
            validity[key] = str((prev or {}).get(key) or "")
        elif val == "" or _VALIDITY_FORMAT.match(val.strip()):
            validity[key] = val.strip()
        else:
            base["error"] = f"invalid {key} {val!r}: use YYYY, YYYY-MM or YYYY-MM-DD"
            return base
    validity["superseded_by"] = (superseded_by.strip() if superseded_by is not None
                                 else str((prev or {}).get("superseded_by") or ""))
    if validity["superseded_by"] == doc_id:
        base["error"] = "superseded_by must name a different document"
        return base
    validity_changed = prev is not None and any(
        validity[k] != str(prev.get(k) or "") for k in validity)
    # 내용 시점도 같은 3값이다(키 부재=보존 · ""=비움 · 값=설정). 형식이 틀리면 쓰지 않고
    # 저장된 값을 남기며 말한다 — 노드 content_time 과 같은 규칙이고, 조용히 비우면
    # 오타 한 번이 저장된 시점을 지운다.
    stored_ctime = str((prev or {}).get("content_time") or "")
    ctime_warnings: list[str] = []
    if content_time is None:
        ctime = stored_ctime
    else:
        ctime = _norm_content_time(content_time)
        if not ctime and str(content_time).strip():
            ctime = stored_ctime
            ctime_warnings.append(
                f"content_time '{content_time}' is not YYYY[-MM[-DD]] — kept the "
                f"stored value")
    ctime_changed = prev is not None and ctime != stored_ctime
    # 소유의 정본은 메타다. 메타 없이 남은 청크(첫 쓰기가 ③ 전에 끊긴 잔해)는 청크의
    # owner_id 가 유일한 근거라, 하나라도 남의 것(무소유 포함)이면 지우지도 덮지도
    # 않는다 — 아래 MERGE 가 남의 청크 owner_id 를 덮어쓰던 길이다(RV-12).
    seat_owner = (prev.get("owner_id") or "") if prev is not None else None
    if (seat_owner is not None and seat_owner != owner) or \
            (seat_owner is None and any(o != owner for o in live)):
        # 거절은 그대로다(덮어쓰지도 만들지도 않는다). 문구만 소유자를 밝히지
        # 않는다 — doc_id 는 허브 전체가 한 이름공간이라 이 id 는 쓸 수 없다는
        # 것까지만 말한다(vinc_ingest 의 id_conflicts 와 같은 대접)(SEC-1).
        # 빈 owner(레거시 무소유)도 남의 것으로 본다 — 열어 두면 재-put 한 번으로
        # 본문 교체와 소유 획득이 된다(VIN-226). 근거: [[vinc.canon.chunk-ssot §4.2]]
        base["error"] = ("doc_id unavailable: this id cannot be written from this "
                         "account; choose a different doc_id")
        return base
    from .mutation_contract import document_guard, MutationConflict
    try:
        document_guard(prev, owner=owner, create_only=create_only,
                       expected_content_hash=expected_content_hash)
    except MutationConflict as exc:
        return {**base, **exc.result}
    # 무변경 스킵은 살아 있는 행 수가 `chunk_count` 와 같을 때만이다. 쓰기 창 표식
    # (-1, 아래 ⓪)·꼬리 청크·빠진 청크가 남은 문서는 get_doc 이 거절하는데, 스킵하면
    # 같은 본문 re-put 이 성공을 답한 채 그 문서가 영영 안 읽힌다(RV-12).
    unchanged = prev is not None and (prev.get("content_hash") == content_hash and prev.get("title") == kept_title
                                      and n_live == int(prev.get("chunk_count") or 0))
    return _PutPlan(owner=owner, doc_id=doc_id, content_hash=content_hash, prev=prev,
                    n_live=n_live, kept_title=kept_title, kept_origin=kept_origin,
                    validity=validity, validity_changed=validity_changed, ctime=ctime,
                    ctime_changed=ctime_changed, ctime_warnings=ctime_warnings,
                    unchanged=unchanged)


def _put_unchanged_body(store, base: dict, plan: _PutPlan, *, text, origin, link, updated_at,
                        topic) -> dict:
    """본문·제목·청크 수가 저장된 그대로인 re-put. 출처·유효구간·내용 시점만 바뀌었으면 메타만
    고치고, 아무것도 안 바뀌었으면 건너뛴다(`skipped`). 청크와 임베딩은 다시 만들지 않는다."""
    owner, doc_id, prev, content_hash = plan.owner, plan.doc_id, plan.prev, plan.content_hash
    kept_title, kept_origin, validity = plan.kept_title, plan.kept_origin, plan.validity
    validity_changed, ctime, ctime_changed = plan.validity_changed, plan.ctime, plan.ctime_changed
    ctime_warnings = plan.ctime_warnings
    origin_changed = origin is not None and origin != str(prev.get("origin") or "")
    if origin_changed or validity_changed or ctime_changed:
        # 출처·유효구간·내용 시점만 바뀌면 원문 청크와 임베딩을 다시 만들 필요가 없다.
        # 쓰기 창 표식은 청크·메타 사이에서 끊겨도 다음 re-put 이 복구하게 한다.
        stamp = updated_at or _stamp_updated_at(store)
        seq = _next_doc_seq(store, owner)
        count = int(prev.get("chunk_count") or 0)
        store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id "
                  "SET d.chunk_count = -1", {"id": doc_id})
        linked = 0
        if origin_changed:
            store.run("MATCH (c:Chunk) WHERE c.doc_id = $id "
                      "SET c.source_file = $origin",
                      {"id": doc_id, "origin": kept_origin})
            # source_file 은 MENTIONS 의 source 매칭에도 쓰인다. 옛 링크를
            # 지우고 같은 링커로 다시 만들되 청크 원문은 보존한다.
            from store.embedded_schema import body_mentions_ready
            keep_body = ("AND (m.method IS NULL OR m.method <> 'body') "
                         if body_mentions_ready(store) else "")
            store.run("MATCH (c:Chunk)-[m:MENTIONS]->() WHERE c.doc_id = $id "
                      + keep_body + "DELETE m", {"id": doc_id})
            linked = _relink_mentions(store, doc_id, owner=owner,
                                      origin=kept_origin) if link else 0
        store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id "
                  "SET d.origin = $origin, d.updated_at = $ts, "
                  "d.sync_seq = $seq, d.chunk_count = $count, "
                  "d.valid_from = $vf, d.valid_until = $vu, d.superseded_by = $sb, "
                  "d.content_time = $ctime",
                  {"id": doc_id, "origin": kept_origin, "ts": stamp,
                   "seq": seq, "count": count, "vf": validity["valid_from"],
                   "vu": validity["valid_until"], "sb": validity["superseded_by"],
                   "ctime": ctime})
        _clear_doc_tombstone(store, doc_id)
        base.update(executed=True, skipped=False, created=False,
                    chunks=count, replaced=0, title=kept_title,
                    content_hash=content_hash, mentions=linked,
                    updated_at=stamp, sync_seq=seq, **validity,
                    warnings=_doc_id_warnings(text, doc_id) + ctime_warnings)
        if topic:
            base["topic"] = _set_authored_topic(
                store, doc_id, topic, owner=owner, title=kept_title)
        base["warnings"] += _no_mirror_warnings(store, text, doc_id, owner=owner)
        base["needs_embed"] = None if prev.get("needs_embed") is None else bool(prev["needs_embed"])
        base["retrieval"] = document_retrieval(prev.get("needs_embed"))
        return base
    base.update(executed=True, skipped=True, created=False,
                chunks=int(prev.get("chunk_count") or 0), title=kept_title,
                content_hash=content_hash,
                warnings=_doc_id_warnings(text, doc_id) + ctime_warnings)
    # ⚠ 주제는 본문과 **독립**이다. 본문이 그대로라고 "이 문서를 X 밑에 둬라"를
    # 버리면 그 지시가 조용히 사라진다 — 부르는 쪽에서 가장 흔한 형태가
    # "본문은 그대로 두고 주제만 적는다" 인데 그것이 정확히 안 먹혔다
    # (실측 2026-09-06, 라이브에서 skipped=True 이고 topic=None 이 돌아왔다).
    if topic:
        base["topic"] = _set_authored_topic(store, doc_id, topic, owner=owner,
                                            title=kept_title)
    base["warnings"] += _no_mirror_warnings(store, text, doc_id, owner=owner)
    base.update(**validity, needs_embed=None if prev.get("needs_embed") is None else bool(prev["needs_embed"]))
    base["retrieval"] = document_retrieval(prev.get("needs_embed"))
    return base


def _put_rewrite_chunks(store, plan: _PutPlan, *, text, layer, max_chars, boundaries,
                        updated_at, created_at, wipe):
    """청크 다시 쓰기: 저장된 청크·자식 벡터 지우기, 새 청크, 메타(해시), 문서 삭제 표식 상쇄.
    반환은 `(청크, 스탬프, 순번, 지운 행 수, needs_embed)` 다."""
    owner, doc_id, prev, n_live = plan.owner, plan.doc_id, plan.prev, plan.n_live
    kept_title, kept_origin, content_hash = plan.kept_title, plan.kept_origin, plan.content_hash
    validity, ctime = plan.validity, plan.ctime
    chunks = chunk_doc(text, max_chars=max_chars, boundaries=boundaries)
    stamp = updated_at or _stamp_updated_at(store)
    seq = _next_doc_seq(store, owner)

    # 여기는 벡터를 쓰지 않는다 — 검색이 읽는 벡터(`ChunkVec`)는 워커의 apply_embeddings
    # 만 쓴다. 벡터 없는 삽입은 HNSW 를 안 건드리므로 인덱스도 안 드롭한다
    # ([[vinc.ops.fts-index-drift]] §3).
    # ① 구 청크 삭제(재청킹으로 개수가 줄어도 고아가 남지 않게 — 부착 MENTIONS 도
    #    DETACH 로 함께 소멸). ② 신 청크 기록. ③ 메타(해시) 기록 — 이 순서가
    # 중간 크래시 자기치유의 근거다(모듈 독스트링).
    # ① 은 메타 유무와 무관하다: 메타 없이 남은 청크(첫 쓰기가 ③ 전에 끊긴 잔해)를
    # 안 지우면 더 짧은 re-put 이 꼬리 청크를 남긴다(RV-12). 남의 행은 위에서 거절됐다.
    removed = n_live
    if prev is not None:
        # ⓪ 창 표식. ①②를 도는 동안 메타는 아직 **옛** chunk_count 를 들고 있어서,
        #    청크가 **늘어나는** re-put 은 살아 있는 행 수가 0→새 수 를 훑으며 옛 수와
        #    정확히 한 번 일치한다 — get_doc 의 행 수 비교가 그 한 순간을 성공으로
        #    답했다(실측: 1→2청크 re-put 중 3,600자 문서가 2,000자를 깨끗한 산문으로
        #    돌려줬다). 어떤 행 수와도 안 맞는 값을 찍어 루프 **전체**를 덮는다.
        #    ③ 의 메타 MERGE 가 진짜 수로 되돌린다 — 치유는 그 한 줄이 전부다.
        # ⚠ 크래시가 이 표식을 디스크에 남기면 그 문서는 **다음 re-put 까지 계속 거절**
        #    된다. 무청크 잔해(모듈 독스트링 "쓰기 순서 = 자기치유")와 같은 갈래다:
        #    조용히 산문으로 읽히는 일이 없고, 되살리는 길도 전문 re-put 하나로 같다.
        #    그래서 위 무변경 skip 이 음수 표식 앞에서 물러선다 — 안 그러면 같은 본문
        #    re-put 이 표식을 남긴 채 성공을 답해 그 문서가 영영 안 읽힌다.
        store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.chunk_count = -1",
                  {"id": doc_id})
    if wipe:
        # 자식 벡터(v8)도 함께 — 재청킹으로 청크가 줄면 옛 자식이 없는 부모를 가리켜
        # 고아가 된다. 새 벡터는 워커가 apply_embeddings 로 다시 만든다.
        store.run("MATCH (v:ChunkVec) WHERE v.doc_id = $d DETACH DELETE v", {"d": doc_id})
        _with_fts_repair(store, lambda: store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d DETACH DELETE c", {"d": doc_id}))

    for i, c in enumerate(chunks):
        # ⚠ 파라미터명 주의: end 는 Cypher 예약어라 $end 를 파서가 거부한다(실측,
        # ladybug 0.18 · 0.20.4 — 0.20.4 는 $start 는 받는다). $cs/$ce 로 우회.
        params = {
            "id": chunk_id_of(doc_id, c["index"]), "doc": doc_id, "owner": owner,
            "origin": kept_origin, "sec": c["section_title"], "idx": c["index"],
            "cs": c["start"], "ce": c["end"], "cc": len(c["text"]), "text": c["text"],
        }
        params["lyr"] = layer
        # v21 — 색인이 담는 말을 **같은 MERGE 에서** 쓴다. 따로 SET 하면 FTS 가 걸린 기존 행
        # 쓰기라 프로세스가 죽는 자리고, 표지 전이어도 미리 채워 두면 이관이 이 행을 건너뛴다.
        params["lex"] = lex_text(c["text"])
        sets = ("c.doc_id = $doc, c.owner_id = $owner, c.source_file = $origin, "
                "c.section_title = $sec, c.chunk_index = $idx, c.layer = $lyr, "
                "c.char_start = $cs, c.char_end = $ce, c.char_count = $cc, "
                "c.token_count = 0, c.text = $text, c.lex = $lex")
        store.run(f"MERGE (c:Chunk {{chunk_id: $id}}) SET {sets}", params)

    # needs_embed: 새로 쓴 문서는 언제나 1 — 워커가 `ChunkVec` 을 채우고 내린다.
    # ⚠ 여기서 벡터를 만들어 `Chunk.embedding` 에 넣던 길(embed=)은 지웠다. v8 이후 검색은
    # 그 열을 안 읽는데 needs_embed 만 0 으로 내려, 그 문서가 의미 검색에서 영구히 빠졌다.
    needs_embed = 1
    # v7(D72) — created_at 은 **최초 1회**만 찍고 편집이 보존한다. 여태 문서에 남는
    # 시각이 updated_at 하나뿐이라, 고칠 때마다 "언제 만든 글인가"가 지워졌다.
    # 레거시 행은 원천이 없으므로 '' 로 남긴다(오늘로 채우면 그게 곧 거짓 시각이다).
    if prev is None:
        born = created_at or stamp          # 신규 — 지금이 곧 최초 시각
    else:
        born = str(prev.get("created_at") or "")   # 편집 — 있으면 보존, 없으면 ''
    store.run(
        "MERGE (d:_DocMeta {doc_id: $id}) SET d.owner_id = $owner, d.title = $title, "
        "d.origin = $origin, d.content_hash = $hash, d.chunk_count = $n, "
        "d.updated_at = $ts, d.sync_seq = $seq, d.needs_embed = $ne, "
        "d.created_at = $born, d.content_time = $ctime, "
        "d.valid_from = $vf, d.valid_until = $vu, d.superseded_by = $sb",
        {"id": doc_id, "owner": owner, "title": kept_title, "origin": kept_origin,
         "hash": content_hash, "n": len(chunks), "ts": stamp, "seq": seq,
         "ne": needs_embed, "born": born, "ctime": ctime,
         "vf": validity["valid_from"], "vu": validity["valid_until"],
         "sb": validity["superseded_by"]})
    # 재생성 = 로컬 doc 툼스톤 상쇄(낡은 삭제가 동기화에서 재전파되지 않게, P3).
    _clear_doc_tombstone(store, doc_id)
    return chunks, stamp, seq, removed, needs_embed


def _put_mirror_and_links(store, base: dict, plan: _PutPlan, *, text, title, topic, link, chunks,
                          stamp, seq, removed, needs_embed) -> dict:
    """미러와 링크: 제목 따라가기, 주제 적기, 언급 다시 잇기, 응답 조립(경고 순서 포함)."""
    owner, doc_id, prev = plan.owner, plan.doc_id, plan.prev
    kept_title, kept_origin, content_hash = plan.kept_title, plan.kept_origin, plan.content_hash
    validity, ctime_warnings = plan.validity, plan.ctime_warnings
    # 제목이 바뀌었으면 미러가 **물려받은 제목 그대로일 때만** 따라간다(아래 함수).
    # 주제 적기와 같은 이유로 링커보다 먼저다.
    if prev is not None and title is not None:
        _follow_title(store, doc_id, owner=owner,
                      old=str(prev.get("title") or ""), new=kept_title)
    # 주제를 **적는다**(선택). 링커보다 **먼저** 해야 미러 제목이 이번 본문 스캔의
    # 대상이 된다.
    authored = _set_authored_topic(store, doc_id, topic, owner=owner,
                                   title=kept_title) if topic else ""

    linked = 0
    if link:
        linked = _relink_mentions(store, doc_id, owner=owner, origin=kept_origin)

    base.update(executed=True, skipped=False, created=prev is None,
                chunks=len(chunks), replaced=removed, title=kept_title,
                content_hash=content_hash, mentions=linked, updated_at=stamp,
                sync_seq=seq, needs_embed=bool(needs_embed),
                warnings=_doc_id_warnings(text, doc_id) + ctime_warnings,
                note="search indexes dropped — 검색 경로가 지연 재빌드(T3b)")
    if topic:
        base["topic"] = authored
    base["warnings"] += _no_mirror_warnings(store, text, doc_id, owner=owner)
    base.update(**validity)
    base["retrieval"] = document_retrieval(bool(needs_embed))
    return base
def _set_authored_topic(store, doc_id: str, topic: str, *, owner: str,
                        title: str) -> str:
    """문서의 주제를 **적는다** — `concept:doc.<doc_id>` 미러의 `domain` 에.

    파생 주제(본문 매칭 → 개념 → 그 개념의 domain)는 아무도 단언한 적 없는 값이라
    문서와 어긋날 수 있다. 실측 2026-09-06: 미러가 있는 117건 중 39건이 파생과 달랐고
    배포 런북이 `vinc/engineering/account` 로 걸려 있었다. 근거:
    [[record:the-document-subject-relation-is-inferred-not-authored-2026-09-06]]

    이미 있는 미러는 **domain 만** 갱신한다. 제목·one_liner 를 덮지 않는 것은
    `extract_doc` 과 같은 태도다(다듬어 둔 값을 쓰기가 지우면 안 된다). 없으면 만들고,
    그때만 문서 제목을 물려받는다 — 그 물림이 Ask 에서 주제 개념과 겹쳐 보이는 위험은
    [[record:risk-doc-title-collides-with-topic]] 에 적혀 있다.

    빈 문자열·공백만이면 아무것도 하지 않는다(주제를 **지우는** 것은 이 인자의 일이
    아니다 — 지우려면 미러를 지우거나 domain 을 직접 고친다)."""
    # 열에도 정규화된 값을 넣는다 — `IN_DOMAIN` 이 가리키는 Domain 이름과 글자까지
    # 같아야 열 집계와 엣지 조회가 같은 답을 준다(`fragment._merge_node` 와 같은 규약).
    dom = domain_hierarchy.normalize(topic)
    if not dom:
        return ""
    # 물리 키로 바인딩한다(`extract_doc` 과 같은 규약) — 논리 id 로 쓰면 네임스페이싱이
    # 켜진 스토어에서 남의 스코프에 걸리거나 조회에서 안 잡힌다.
    mid = pkns.key(owner, _DOC_MIRROR_PREFIX + doc_id)
    st = _node_stamp(store, owner)
    rows = store.run("MATCH (n:Concept) WHERE n.concept_id = $id RETURN n.concept_id AS id",
                     {"id": mid}) or []
    if rows:
        store.run("MATCH (n:Concept) WHERE n.concept_id = $id SET n.domain = $dom, "
                  "n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq",
                  {"id": mid, "dom": dom, "ua": st["updated_at"], "rev": st["rev"],
                   "seq": st["sync_seq"]})
    else:
        store.run(
            "MERGE (n:Concept {concept_id: $id}) SET n.title = $t, n.one_liner = '', "
            "n.owner_id = $owner, n.domain = $dom, n.created = $now, "
            "n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq",
            {"id": mid, "t": title or doc_id, "owner": owner, "dom": dom,
             "now": st["updated_at"], "ua": st["updated_at"], "rev": st["rev"],
             "seq": st["sync_seq"]})
    _link_domain(store, mid, dom)
    return dom


def _follow_title(store, doc_id: str, *, owner: str, old: str, new: str) -> bool:
    """문서 제목이 바뀌면 미러(`concept:doc.<doc_id>`)의 제목을 따라 바꾼다 — **미러가 아직
    물려받은 제목(옛 문서 제목, 제목이 없었으면 doc_id)을 들고 있을 때만.**

    미러는 만들어질 때 문서 제목을 물려받고 그 뒤로는 제목을 덮지 않는다(`_set_authored_topic`
    주석: 다듬어 둔 값을 쓰기가 지우면 안 된다). 그래서 앱에서 새 문서를 만든 뒤 제목을 고치면
    트리와 View 에 옛 제목이 남았다. 사람이 미러 제목을 따로 고쳤다면 그 값은 옛 문서 제목과
    다르므로 여기서 건드리지 않는다 — 두 규칙이 한 조건으로 함께 선다.
    반환 = 바꿨는가. 실패는 삼킨다(제목 따라가기는 문서 쓰기를 실패시킬 이유가 아니다)."""
    new = (new or "").strip()
    if not new or new == old:
        return False
    inherited = [t for t in {old, doc_id} if t]
    mid = pkns.key(owner, _DOC_MIRROR_PREFIX + doc_id)
    try:
        rows = store.run(
            "MATCH (n:Concept) WHERE n.concept_id = $id RETURN n.title AS title",
            {"id": mid}) or []
        if not rows or str(rows[0].get("title") or "") not in inherited:
            return False
        st = _node_stamp(store, owner)
        store.run("MATCH (n:Concept) WHERE n.concept_id = $id SET n.title = $t, "
                  "n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq",
                  {"id": mid, "t": new, "ua": st["updated_at"], "rev": st["rev"],
                   "seq": st["sync_seq"]})
        return True
    except Exception as exc:
        print(f"[docstore] mirror title not updated ({doc_id}): {exc}", file=sys.stderr)
        return False


def _link_domain(store, key: str, dom: str) -> None:
    """Concept 의 `IN_DOMAIN` 을 방금 쓴 `domain` 열에 맞춘다(옛 엣지는 `link_node` 가 지운다).

    `n.domain` 을 쓰는 곳은 이 엣지도 같이 옮겨야 한다. 안 그러면 `vinc_domains`(열
    집계)는 노드를 세는데 `vinc_neighbors domain:…`(엣지 조회)는 못 찾는다 — 다음
    기동 백필까지 둘이 갈린다. 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S9.

    실패는 삼킨다: 계층은 열에서 언제든 다시 파생되는 파생물이고(`scripts/
    relink_domains.py`), 여기서 죽으면 문서 쓰기가 통째로 실패한다. `fragment` 와 같은 태도다.
    ``key`` 는 **물리 키**다(`link_node` 가 PK 로 바인딩한다)."""
    try:
        domain_hierarchy.link_node(store, "Concept", key, dom)
    except Exception as exc:
        print(f"[docstore] domain hierarchy not updated ({key}): {exc}", file=sys.stderr)


def _relink_mentions(store, doc_id: str, *, owner: str, origin: str) -> int:
    """이 doc 의 청크만 대상으로 :MENTIONS 재생성. 링크 규칙·우선순위는 E-G1
    링커(engines.mentions_link)를 그대로 재사용한다 — 규칙 분기 금지(SSoT 하나).
    구 링크는 put_doc 의 DETACH DELETE 가 이미 지웠다. 실패는 적재를 무효화하지
    않는다(증강)."""
    try:
        from engines.mentions_link import compute_links, merge_links, _fetch_concepts
        rows = store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS chunk_id, "
            "c.text AS text", {"d": doc_id})
        chunks = [{"chunk_id": r["chunk_id"], "source_file": origin,
                   "text": r.get("text") or ""} for r in rows or []]
        concepts = _fetch_concepts(store, owner=owner)
        links = compute_links(chunks, concepts)
        # 쓰기도 링커의 것을 쓴다 — 여기 MERGE 를 복제하고 있었고, 그 사본에
        # `(co:Concept)` 이 박혀 있어 결정으로 향하는 링크가 조용히 0건이 됐다.
        return merge_links(store, links, concepts, owner)
    except Exception:
        return 0


def get_doc(store, doc_id: str, *, owner: str | None = None) -> dict:
    """doc_id 의 원문을 청크 오프셋 연결로 복원 + 무손실 검증.

    verified = (연속성: b0=0 · char_start_i == char_end_{i-1}) AND
               (sha256(복원문) == _DocMeta.content_hash).
    레거시 청크(오프셋 -1)는 복원 불가로 보고한다(검색만 가능).
    살아 있는 청크 수가 메타의 `chunk_count` 와 어긋나면(쓰기 창 · 삭제 창 · 크래시
    잔해) 본문 자리를 주지 않고 `executed=False` 로 거절한다. 쓰기 창은 put_doc 이
    `chunk_count = -1` 로 표식하므로 루프 **전체**가 덮인다 — 아래 주석 참조."""
    base: dict[str, Any] = {"kind": "doc.get", "executed": False, "doc_id": doc_id}
    meta = _meta(store, doc_id)
    # 남의 문서는 **없는 문서와 똑같이** 답한다(SEC-1). "owner mismatch" 는 그 id 가
    # 이 허브에 있다는 사실을 남에게 알려 주었다. 후보(near_docs)는 부르는 쪽 소유로만
    # 좁혀지므로 남의 id 가 거기로 새지 않는다.
    # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]]
    if meta is None or (owner is not None and meta.get("owner_id") != owner):
        return _unknown_doc(base, store, doc_id, owner)
    rows = store.run(
        "MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_index AS idx, "
        "c.char_start AS cs, c.char_end AS ce, c.text AS text "
        "ORDER BY c.chunk_index", {"d": doc_id})
    rows = rows or []

    # 살아 있는 청크 행 수가 메타의 `chunk_count` 와 다르면 이 문서는 **지금 교체되는
    # 중이거나 중간에 끊긴 것**이다. put_doc 은 구 청크를 지우고 하나씩 다시 MERGE 한 뒤
    # 마지막에 메타를 갱신하고, delete_doc 은 청크를 지운 뒤 메타를 지운다. 두 창과
    # 크래시 잔해가 여기서 같은 모양으로 잡힌다 — 그래서 판정을 두 쓰기 경로가 아니라
    # **둘이 만나는 이 자리**에 둔다(한쪽에만 두면 절반만 고친다).
    # 안 하면 창 안의 읽기가 빈 본문이나 **앞부분만**을 executed:true 로 답한다(실측
    # 2026-09-06: 27청크 문서가 chunks:0 · 3,802자 문서가 1,802자). `verified` 로는 못
    # 가른다 — 오프셋 잃은 레거시 문서도 false 이고 그쪽은 행 수가 맞아 여기 안 걸린다.
    # ⚠ 기다렸다 다시 읽지 않고 **답을 거절한다**: 이 상태는 크래시가 남기면 다음 re-put
    # 까지 지속되므로(모듈 독스트링 "쓰기 순서 = 자기치유") 기다림으로는 낫지 않는다.
    # ⚠ 행 수 비교 **하나만으로는 창을 다 못 덮는다** — 청크가 늘어나는 re-put 은 메타가
    # 아직 옛 수를 들고 있어서 행 수가 그 값을 스치고 지나간다. 그 한 순간을 위해
    # put_doc 이 창 진입 때 `chunk_count = -1`(음수 = 표식)을 찍고, 그 값이 여기서
    # 어떤 행 수와도 안 맞아 루프 전체가 덮인다. delete_doc 은 표식이 필요 없다:
    # 청크 삭제가 **한 문**이라 행 수가 n→0 으로 한 번에 떨어지고, 그 0 은 메타의 n 과
    # 항상 어긋난다(중간 값이 없다).
    # 계약: [[vinc.canon.chunk-ssot]]
    expected = int(meta.get("chunk_count") or 0)
    if expected and len(rows) != expected:
        base["chunks"], base["chunk_count"] = len(rows), expected
        base["error"] = ("document is being rewritten: its chunks are mid-replacement"
                         if expected < 0 else
                         "document is incomplete: %d of %d chunks are present"
                         % (len(rows), expected))
        base["hint"] = ("A write replaces chunks one at a time and a delete removes "
                        "them before the metadata, so a read landing in either window "
                        "sees this. Read it again in a moment. If it stays this way "
                        "the last write was interrupted: re-put the full text to heal "
                        "it. This is not an empty document, and not a missing one.")
        return base

    def _off(v) -> int:   # ⚠ `v or -1` 금지 — 정당한 오프셋 0 이 falsy 로 강등된다
        return -1 if v is None else int(v)

    contiguous = bool(rows) and _off(rows[0].get("cs")) == 0
    prev_end = 0
    parts: list[str] = []
    for r in rows:
        s, e = _off(r.get("cs")), _off(r.get("ce"))
        if s != prev_end or e < s:
            contiguous = False
        prev_end = e
        parts.append(r.get("text") or "")
    text = "".join(parts)
    verified = contiguous and _sha(text) == meta.get("content_hash")
    base.update(executed=True, text=text, title=meta.get("title") or "",
                origin=meta.get("origin") or "", owner_id=meta.get("owner_id"),
                content_hash=meta.get("content_hash"), chunks=len(rows),
                updated_at=meta.get("updated_at"), verified=verified,
                needs_embed=bool(int(meta.get("needs_embed") or 0)),
                # v7(D72) — 편집기가 내용 시점을 편집하려면 지금 값을 알아야 한다.
                created_at=meta.get("created_at") or "",
                content_time=meta.get("content_time") or "",
                # v22 — 문서 유효구간과 파생 상태. 상태는 저장하지 않고 읽을 때 낸다.
                valid_from=meta.get("valid_from") or "",
                valid_until=meta.get("valid_until") or "",
                superseded_by=meta.get("superseded_by") or "",
                validity=validity_state(meta.get("valid_from"), meta.get("valid_until")))
    from store.search_temporal import stamp, finish_docs
    stamp(base)
    # 기존 visibility 값과 명시적 유효구간의 판정을 구분한다.
    base["validity_basis"] = "legacy_visibility"
    base["retrieval"] = document_retrieval(base["needs_embed"])
    try:
        finish_docs(store, [base], owner=owner)
    except Exception as exc:
        base["partial"] = True
        base["temporal_error"] = f"{type(exc).__name__}: {exc}"
    base["claim_validity"] = base["temporal"]["validity"]
    if not verified:
        base["note"] = ("복원 검증 실패 — 레거시(오프셋 미기록) 또는 부분 기록. "
                        "re-put 으로 치유 가능")
    return base


def _unknown_doc(base: dict, store, doc_id: str, owner: str | None) -> dict:
    """없는 doc_id 의 읽기 응답 — get_doc 과 남의 문서 거절이 **같은 꼴**을 내도록
    한 자리로 모은 것. F7: 오타 하나에 다음 수가 없던 한 줄 오류를 후보로 보강한다."""
    base["error"] = "unknown doc_id"
    cands = near_docs(store, doc_id, owner=owner)
    base["hint"] = ("doc_id must match exactly. "
                    + ("Did you mean: " + ", ".join(cands) + "? " if cands else "")
                    + "Use vinc_doc_list to browse, or vinc_search to find a "
                      "document by its content.")
    if cands:
        base["candidates"] = cands
    return base


def _nothing_deleted(base: dict, store, doc_id: str, owner: str | None) -> dict:
    """지울 것이 없던 삭제의 응답 — 없는 문서와 남의 문서가 같은 꼴을 낸다."""
    base.update(executed=True, deleted_chunks=0, deleted_meta=False, found=False)
    # F7 — 여태 없는 문서를 지워도 응답이 성공과 **똑같았다**. 멱등이라 오류는
    # 아니지만, 오타로 지운 줄 알고 넘어가는 것이 실제 피해다.
    base["error"] = "unknown doc_id — nothing was deleted"
    cands = near_docs(store, doc_id, owner=owner)
    base["hint"] = ("The delete is idempotent, so this is not an error — but "
                    "nothing matched. "
                    + ("Did you mean: " + ", ".join(cands) + "? " if cands else "")
                    + "Use vinc_doc_list to check the id.")
    if cands:
        base["candidates"] = cands
    return base


def near_docs(store, needle: str, *, owner: str | None = None,
              limit: int = 3) -> list[str]:
    """놓친 doc_id 에 가까운 문서 후보(F7). id 와 제목 양쪽을 본다 — 사용자는 둘 중
    아무거나 기억하고 있다. stdlib `difflib` 뿐이라 새 색인이 필요 없다."""
    import difflib
    try:
        where = "WHERE d.owner_id = $o " if owner else ""
        rows = store.run(
            f"MATCH (d:_DocMeta) {where}RETURN d.doc_id AS doc_id, d.title AS title",
            {"o": owner} if owner else None) or []
    except Exception:                                             # noqa: BLE001
        return []                      # 힌트를 만들다 본 응답을 죽이지 않는다
    ids = [r.get("doc_id") for r in rows if r.get("doc_id")]
    hits = difflib.get_close_matches(needle, ids, n=limit, cutoff=0.5)
    if len(hits) < limit:              # id 로 못 찾으면 제목으로 한 번 더
        by_title = {(r.get("title") or ""): r.get("doc_id") for r in rows}
        for t in difflib.get_close_matches(needle, list(by_title), n=limit, cutoff=0.5):
            if by_title[t] not in hits:
                hits.append(by_title[t])
    return hits[:limit]


def delete_doc(store, doc_id: str, *, owner: str | None = None,
               tombstone: bool = True) -> dict:
    """doc 단위 완전 삭제 — 청크(부착 MENTIONS 포함 DETACH)와 정체성 레지스트리.
    owner 지정 시 소유 가드(SEC-1). 멱등.

    ``tombstone`` (P3): 삭제 표식을 남겨 동기화가 상대 기기에도 삭제를 전파하게
    한다(하드삭제만으론 다음 delta 에서 부활). 동기화 apply 의 하드삭제는 False.

    ⚠ **미러 개념(`concept:doc.<doc_id>`)은 일부러 안 지운다.** 그래서 지운 문서의
    미러가 그래프에 남고 그 주제의 공유 팩마다 실린다(`documents: 0` 인 껍데기).
    아는 천장이다 — 여기서 지우려면 `graph_edit.delete_node` 급의 파괴적 삭제여야 하고
    (사용자가 그 노드에 그린 엣지와 다듬은 제목이 함께 사라지며, `_Tombstone` 으로
    다른 기기에도 전파된다) 그것은 P11c(공유 시트 제외) 범위 밖의 제품 판정이다.
    당장의 피해는 팩 쪽에서 막혀 있다: `share._docs_for_concepts` 가 부재 doc_id 를
    걸러 빈 문서를 안 싣는다(`tests/core/test_share.py`
    `test_a_mirror_whose_document_is_gone_ships_no_document` 가 그 상태를 고정한다)."""
    base: dict[str, Any] = {"kind": "doc.delete", "executed": False, "doc_id": doc_id}
    meta = _meta(store, doc_id)
    if meta is not None and owner is not None and meta.get("owner_id") != owner:
        # 거절은 그대로, 답은 없는 문서와 같게(SEC-1) — 아무것도 지우지 않는다.
        return _nothing_deleted(base, store, doc_id, owner)
    # 메타가 없으면 소유의 근거는 청크의 owner_id 뿐이다 — owner 를 준 삭제는 자기 청크만
    # 지운다. 안 그러면 남이 첫 쓰기에서 끊긴 잔해를 id 하나로 지울 수 있다(RV-12).
    scoped = meta is None and owner is not None
    params = {"d": doc_id, "o": owner} if scoped else {"d": doc_id}
    cwhere = "c.doc_id = $d" + (" AND c.owner_id = $o" if scoped else "")
    vwhere = "v.doc_id = $d" + (" AND v.owner_id = $o" if scoped else "")
    cnt = store.run(f"MATCH (c:Chunk) WHERE {cwhere} RETURN count(c) AS c", params)
    n = int(cnt[0].get("c")) if cnt else 0
    if n:
        refusal = _remnant_refusal(store)       # 아래 두 삭제가 잔해 표를 친다
        if refusal:
            base["error"] = refusal
            return base
        # 인덱스를 드롭하지 않는다 — 문서 하나 지우자고 끊으면 그동안 검색이 죽는다.
        # 대신 삭제를 `_with_fts_repair` 로 감싼다: 오래 증분 유지된 FTS 색인은 존재하는
        # 행의 문서를 잃고, 그 행을 지우면 엔진이 불일치로 던진다([[vinc.ops.fts-index-drift]]).
        # ⚠ 자식 벡터(v8)를 **먼저** 지운다 — 남으면 조용히 사라지는 게 아니라 인덱스에
        # 남아 상위 k 자리를 잡아먹는다.
        # put_doc 과 달리 여기엔 창 표식이 필요 없다: 청크 삭제가 **한 문**이라 행 수가
        # n→0 으로 한 번에 떨어지고, 중간 값이 없으니 메타의 n 과 항상 어긋난다
        # (get_doc 이 그 어긋남으로 거절한다). 이 줄을 청크별 루프로 바꾸면 그 보장이
        # 깨지므로, 그때는 put_doc 처럼 표식을 찍어야 한다.
        store.run(f"MATCH (v:ChunkVec) WHERE {vwhere} DETACH DELETE v", params)
        _with_fts_repair(store, lambda: store.run(
            f"MATCH (c:Chunk) WHERE {cwhere} DETACH DELETE c", params))
    owner_id = (meta or {}).get("owner_id") or owner or ""
    if meta is not None:
        store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id DELETE d", {"id": doc_id})
    if tombstone and (meta is not None or n):
        store.run(
            "MERGE (t:_DocTombstone {doc_id: $id}) SET t.owner_id = $o, "
            "t.deleted_at = $ts, t.sync_seq = $seq",
            {"id": doc_id, "o": owner_id, "ts": _stamp_updated_at(store),
             "seq": _next_doc_seq(store, owner_id)})
    base.update(executed=True, deleted_chunks=n, deleted_meta=meta is not None)
    if meta is None and not n:
        _nothing_deleted(base, store, doc_id, owner)
    else:
        base["found"] = True
    return base


class MentionTally:
    """문서 → 언급 개념 집계. `list_docs` 와 `nav.nav_tree` 의 **공유 원천**.

    두 표면(모든 문서 목록·좌측 문서 트리)이 같은 문서를 다른 주제/다른 노드에
    걸면 사용자에게는 그냥 버그다. 그래서 집계를 한 군데서만 만든다.

    - `dom[doc_id][domain]`  = 그 주제 개념들의 언급 횟수 합 → 대표 주제(`topic`)
    - `con[doc_id][cid]`     = 개념별 언급 횟수 → 대표 개념(`primary_concept_id`)
    - `cdom[cid]` · `ctitle[cid]` = 개념의 주제·제목

    **대표 개념은 대표 주제 안에서 고른다**(전역 최다가 아니라). 문서가 주제 T 밑에
    걸려 있는데 눌렀더니 다른 주제의 노드로 날아가면 트리가 자기를 반박한다."""

    __slots__ = ("dom", "con", "cdom", "ctitle", "authored")

    def __init__(self) -> None:
        self.dom: dict[str, dict[str, int]] = {}
        self.con: dict[str, dict[str, int]] = {}
        self.cdom: dict[str, str] = {}
        self.ctitle: dict[str, str] = {}
        #: doc_id → (domain, mirror_cid, mirror_title) — 사람이 **적은** 주제.
        self.authored: dict[str, tuple[str, str, str]] = {}

    def topics(self, doc_id: str) -> list[str]:
        """걸린 주제 전부, 많은 순(동률은 이름순 — 결정론).

        ⚠ **적힌 주제가 있으면 그것이 맨 앞이다.** 나머지는 매칭이 찾아낸 것이라
        발견으로서 유효하지만 대표는 아니다. 근거·실측:
        [[record:the-document-subject-relation-is-inferred-not-authored-2026-09-06]]
        (미러 domain 이 있는 문서 117건 중 39건에서 파생 주제와 어긋났고, 그중에는
        배포 런북이 `vinc/engineering/account` 로 걸린 것도 있었다)."""
        t = self.dom.get(doc_id) or {}
        auth = (self.authored.get(doc_id) or ("", "", ""))[0]
        from store.document_topic import ranked_topics
        return ranked_topics(t, auth)

    def primary(self, doc_id: str, topic: str) -> tuple[str, str]:
        """`topic` 안에서 가장 많이 언급된 개념 `(id, title)`. 없으면 `("", "")`.

        동률은 개념 id 순(결정론). `topic` 이 대표 주제인 한 후보는 반드시 하나
        이상 존재한다 — 그 주제가 뽑힌 근거가 곧 그 주제 개념의 언급이므로."""
        c = self.con.get(doc_id) or {}
        best = sorted(((cid, n) for cid, n in c.items() if self.cdom.get(cid, "") == topic),
                      key=lambda kv: (-kv[1], kv[0]))
        if best:
            return (best[0][0], self.ctitle.get(best[0][0], ""))
        # 적힌 주제로 왔는데 그 주제에 언급된 개념이 없을 수 있다(주제를 적기만 하고
        # 본문은 개념 제목을 안 담은 문서). 그때는 **그 문서 자신의 미러**가 답이다 —
        # 빈 값을 주면 트리에서 눌러도 갈 곳이 없다.
        dom, cid, ctitle = self.authored.get(doc_id) or ("", "", "")
        return (cid, ctitle) if dom and dom == topic and cid else ("", "")


def mention_tally(store, *, owner: str | None = None) -> MentionTally:
    """`(Chunk)-[:MENTIONS]->(Concept)` 를 **한 번의 집계 쿼리로** 전 문서 분 수집.

    문서마다 조회하지 않는다. MENTIONS 미구축·구스토어에서는 빈 집계로 강등한다
    (실패가 아니다 — 주제 없는 목록은 여전히 유효하다)."""
    tally = MentionTally()
    # ⚠ try 는 **쿼리에만** 건다. 집계 루프까지 감싸면 프로그래밍 오류가 "구스토어
    # 강등"으로 위장돼 조용히 사라진다(실제로 그렇게 한 번 당했다 — 아래 주석 참조).
    try:
        mrows = store.run(
            "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) "
            + ("WHERE c.owner_id = $o AND (ch.owner_id = $o OR ch.owner_id = '') " if owner else "")
            + "RETURN ch.doc_id AS doc_id, c.concept_id AS cid, c.title AS title, "
              "c.domain AS domain, count(*) AS n",
            {"o": owner} if owner else None) or []
    except Exception:
        mrows = []   # MENTIONS 미구축·구스토어 — 주제 없이 목록만(강등, 실패 아님)
    for r in mrows:
        did = r.get("doc_id") or ""
        cid = r.get("cid") or ""
        if not did or not cid:
            continue
        n = int(r.get("n") or 0)
        dom = (r.get("domain") or "").strip()
        tally.cdom[cid] = dom
        tally.ctitle[cid] = r.get("title") or ""
        # ⚠ `t.setdefault(d, {})[k] = t[d].get(k, 0) + n` 로 쓰지 말 것: 파이썬은
        # 대입문의 **오른쪽을 먼저** 평가하므로 첫 등장에서 KeyError 가 난다.
        cbucket = tally.con.setdefault(did, {})
        cbucket[cid] = cbucket.get(cid, 0) + n
        if dom:   # 주제 집계는 주제 있는 개념만(무주제 개념이 대표 주제를 못 만든다)
            dbucket = tally.dom.setdefault(did, {})
            dbucket[dom] = dbucket.get(dom, 0) + n
    _load_authored_topics(store, tally, owner=owner)
    return tally


def _load_authored_topics(store, tally: MentionTally, *, owner: str | None) -> None:
    """`concept:doc.<doc_id>` 미러의 domain 을 그 문서의 **적힌 주제**로 싣는다.

    미러는 문서의 개념 그래프 쪽 자리이고 그 domain 은 사람이 고른 값이다. 파생
    주제는 본문 문자열 매칭에서 나오므로 둘은 다를 수 있고, 실제로 어긋난다. 미러가
    없거나 domain 이 비면 종전대로 파생만 쓴다(하위호환 — 이 함수는 더하기만 한다).

    실패는 강등한다: 미러 조회가 안 되는 구스토어에서도 목록은 나와야 한다."""
    # ⚠ `STARTS WITH 'concept:doc.'` 로 거르지 않는다. PK 네임스페이싱(design A)이
    # 켜지면 물리 키가 `owner논리id` 라 그 접두가 안 맞고, 접두를 합성해 넣으면
    # 이번엔 꺼진 스토어에서 안 맞는다. 주제 있는 개념 전부를 받아 파이썬에서
    # `pkns.strip`(SEP 없으면 항등) 후에 거르면 **두 모드에서 같은 코드가 돈다**.
    # 행 수는 MENTIONS 집계(문서×개념)보다 작으므로 대가가 없다.
    try:
        rows = store.run(
            "MATCH (c:Concept) "
            + ("WHERE c.owner_id = $o AND " if owner else "WHERE ")
            + "c.domain IS NOT NULL AND c.domain <> '' "
              "RETURN c.concept_id AS cid, c.title AS title, c.domain AS domain",
            {"o": owner} if owner else None) or []
    except Exception:
        return
    for r in rows:
        cid = r.get("cid") or ""
        logical = pkns.strip(cid)
        if not logical.startswith(_DOC_MIRROR_PREFIX):
            continue
        did = logical[len(_DOC_MIRROR_PREFIX):]
        if did:
            tally.authored[did] = ((r.get("domain") or "").strip(), cid,
                                   r.get("title") or "")


def list_docs(store, *, owner: str | None = None, limit: int | None = None,
              cursor: str | None = None, detail: str = "full",
              title: str | None = None) -> dict:
    """문서 목록(_DocMeta) + **주제·대표 개념 파생**. owner 지정 시 소유 스코프.

    ⚠ **기본값은 종전 그대로다**(`detail="full"` · 전량). `nav_tree` 가 이 함수를
    그대로 쓰면서 `topic`·`primary_concept_id` 를 요구하기 때문이다 — 기본을 요약으로
    바꾸면 좌측 문서 트리가 조용히 무너진다(F5 를 도구 쪽에서만 바꾸는 이유).

    F5 — MCP 표면은 `detail="summary"` · `limit=30` 으로 부른다. 실측 51,537B
    (약 12,900토큰)이 문서 120건에서 나왔고, 200건이 되면 쓸 수 없는 수준이 된다.
    요약형은 `mention_tally` 를 **아예 계산하지 않는다** — 크기뿐 아니라 왕복도 준다.
    커서·has_more 규약은 `vinc_search`(GM-14)와 같은 형태다.

    ⚠ 문서 자체에는 주제(domain) 열이 없다 — `_DocMeta` 는 정체성·변경감지 메타만
    갖는다(D45). 그래서 주제는 **문서가 언급하는 개념에서 유도**한다:
    `(Chunk)-[:MENTIONS]->(Concept)` 를 타고 그 개념들의 domain 을 세어, 가장 많이
    걸린 주제를 그 문서의 주제로 삼는다. 링크가 아직 없는 문서는 주제가 없다(빈 문자열)
    — 억지로 배정하지 않는다(D45-V "없으면 없다고 말한다").

    반환 doc 에 싣는 것:
    - `topic`(대표 주제) · `topics`(걸린 주제 전부, 많은 순)
    - `primary_concept_id`·`primary_concept_title`(D69) — 그 문서를 눌렀을 때
      **착지할 그래프 노드**. 문서 목록·좌측 트리의 클릭이 여기로 간다. 언급이
      없으면 빈 문자열이고, 그 문서는 착지 대상이 없다(편집기로만 열린다)."""
    clauses = ["d.owner_id = $o"] if owner else []
    params = {"o": owner} if owner else {}
    if title is not None:
        # 제목은 본문 FTS/벡터를 기다리지 않는다. 첫 저장 직후에도 Find에서 열린다.
        clauses.append("lower(d.title) = lower($title)")
        params["title"] = title.strip()
    where = "WHERE " + " AND ".join(clauses) + " " if clauses else ""
    rows = store.run(
        f"MATCH (d:_DocMeta) {where}RETURN d.doc_id AS doc_id, d.title AS title, "
        "d.origin AS origin, d.chunk_count AS chunks, d.updated_at AS updated_at, "
        "d.needs_embed AS needs_embed, "
        "d.created_at AS created_at, d.content_time AS content_time, "
        "d.valid_from AS valid_from, d.valid_until AS valid_until, d.superseded_by AS superseded_by "   # v7(D72)
        "ORDER BY d.updated_at DESC", params or None)
    # 관례(GRAPH.md)는 자료가 아니라 **자료에 대한 규약**이다. 문서 목록·좌측 트리
    # (nav_tree 가 이 함수를 그대로 쓴다)에서 빼서 사용자의 실제 자료 사이에 끼지 않게
    # 한다. 전용 자리는 앱의 관례 시트이고, 어시스턴트는 vinc_conventions 로 읽는다.
    # 정확한 id 로는 여전히 읽고 지울 수 있다(get_doc/delete_doc 은 그대로).
    docs = [{**r, "needs_embed": bool(int(r.get("needs_embed") or 0))}
            for r in (rows or []) if not graphmd.is_conventions_doc_id(r.get("doc_id") or "")]

    total = len(docs)
    off = 0
    # 순환 임포트 회피 — query 가 이 모듈을 이미 쓴다(nav_tree). 지연 임포트로
    # 커서 규약을 **한 벌만** 유지한다(검색과 같은 코덱·같은 지문).
    from .query import _query_fingerprint, cursor_decode, cursor_encode
    # 커서에 **어느 목록의 것인가**를 싣는다(검색 커서의 qh 와 같은 방식). 여태 오프셋
    # 하나뿐이라 남의 owner·다른 detail 로 발급된 커서, 음수 오프셋(`off:-5` 를 0 으로
    # 조용히 당겼다)이 전부 통과했다. 이제 셋 다 ValueError("invalid_cursor: …").
    # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] S10.
    qh = _query_fingerprint("doc.list", owner,
                            {"title": title.strip().lower()} if title is not None else None,
                            "summary" if detail == "summary" else "full")
    if cursor:
        data = cursor_decode(cursor)        # 무효면 ValueError — 호출부가 표면화
        if data.get("qh") != qh:
            raise ValueError(
                "invalid_cursor: cursor was issued for a different query/scope")
        raw_off = data.get("off", 0)
        if not isinstance(raw_off, int) or isinstance(raw_off, bool) or raw_off < 0:
            raise ValueError("invalid_cursor: offset is not a non-negative integer")
        off = raw_off
    page = docs[off:off + limit] if limit else docs[off:]

    from store import search_temporal as temporal
    if detail != "summary" or title is not None:
        for doc in page:
            temporal.stamp(doc)
        temporal.finish_docs(store, page, owner=owner)

    if detail == "summary":
        # 요약형은 언급 집계를 **건너뛴다.** topics·primary_concept 는 전 문서의
        # MENTIONS 를 훑어야 나오는 파생값이라, 목록을 보려고 그것까지 계산하면
        # 크기와 시간을 함께 낸다.
        page = [{"doc_id": d["doc_id"], "title": d.get("title") or "",
                 "updated_at": d.get("updated_at") or "", "chunks": d.get("chunks"),
                 **({"temporal": d["temporal"]} if title is not None else {})}
                for d in page]
    else:
        tally = mention_tally(store, owner=owner)
        for d in page:
            ordered = tally.topics(d["doc_id"])
            d["topics"] = ordered
            d["topic"] = ordered[0] if ordered else ""
            cid, ctitle = tally.primary(d["doc_id"], d["topic"])
            d["primary_concept_id"] = cid
            d["primary_concept_title"] = ctitle

    out = {"kind": "doc.list", "executed": True, "docs": page, "count": len(page)}
    if limit or cursor:
        # 페이지를 자를 때만 항해 정보를 싣는다 — 전량 호출(nav_tree·CLI)의 응답
        # 형태를 바꾸지 않기 위해서다.
        nxt = off + len(page)
        out["total_count"] = total
        out["has_more"] = nxt < total
        out["next_cursor"] = (cursor_encode({"v": 1, "off": nxt, "tc": total, "qh": qh})
                              if nxt < total else None)
    return out
