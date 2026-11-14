# -*- coding: utf-8 -*-
"""관계 근거 백필: 정본 청크에서 두 개념이 같은 문장에 나온 자리를 근거로 엣지에 채운다.

`api.docstore` 가 이 이름들을 같은 객체로 다시 내보낸다(`docstore.backfill_evidence`). 이
모듈은 `api.docstore` 를 최상위에서 import 하지 않는다(docstore 가 이 모듈을 최상위에서 읽는다).
"""
from __future__ import annotations

from typing import Any

from store import pkns


# 근거 문장 상한 — 툴팁·패널 한 문단 분량. 더 길면 읽히지 않고 저장만 부푼다.
_EVIDENCE_MAX = 300


def _sentences(text: str) -> list[str]:
    """문장 분해 — 마침표·물음표·느낌표·줄바꿈 기준의 결정론 분해. NLP 없음.
    CJK 종결부호(。！？)는 뒤에 공백이 없어도 끊는다(그 언어는 띄어 쓰지 않는다)."""
    import re as _re
    parts = _re.split(r"(?<=[.!?])\s+|(?<=[。！？])\s*|\n+", text or "")
    return [p.strip() for p in parts if p.strip()]


def backfill_evidence(store, *, owner: str, doc_id: str | None = None,
                      limit: int = 400, cursor: str | None = None) -> dict:
    """관계의 **근거 문장**을 정본에서 결정론으로 채운다(E-G6 생산자, U2).

    여태 스키마에는 `evidence`/`confidence` 열이 있는데 **채우는 곳이 하나도
    없었다** — 팩도 추출도 저작하지 않아, 관계 패널이 언제나 "근거 없음"이었다
    (실측: 방향 엣지 80개 중 보유 0). 여기서 그 공백을 메운다.

    규칙(LLM 없음·오프라인·재현 가능) — 문장 판정 2단, 정확한 쪽 우선:
      ① 한 청크가 두 개념을 **함께 언급**하면(MENTIONS 두 줄), 그 청크에서 두 제목이
        **같은 문장** 안에 verbatim 으로 나타나는 문장을 근거로 삼는다.
      ② ①이 그 문장에서 안 잡히면, 두 제목의 **유의 토큰**(각 ≤6, `engines.
        mentions_link` 와 같은 어휘 — 규칙 분기 금지)이 모두 그 문장에 있을 때
        근거로 삼는다. 첫 owner 전체 실행이 scanned 202 · filled 0 이었던 이유가
        ①뿐이어서다 — 이 코퍼스의 주장형 긴 제목은 verbatim 공기가 물리적으로
        없다. 완화는 [[decision:relax-the-evidence-producer-rule]] 로 비준됐다.
      같은 문장이 없으면 근거로 치지 않는다 — 같은 문서에 있었다는 사실만으로는
      관계의 근거가 아니다.

    confidence 는 **출처 장부를 겸한다**(비준 조건 — backfill 산은 저작분과 구별
    가능해야 한다): ①은 두 제목의 거리로 0.5~0.9(가까울수록 높다), ②는 고정 0.4.
    0.5 미만이면 토큰 완화 백필이라는 뜻이고, 저작 evidence 는 저자가 준 값(또는
    없음)을 그대로 가진다. 이미 근거가 있는 엣지는 건드리지 않는다(사용자·상류
    저작 보호).

    ``doc_id`` 를 주면 그 문서가 언급하는 개념들 사이 엣지만 훑는다(편집 직후
    증분). 없으면 owner 전체. 반환 {executed, filled, scanned, has_more, next_cursor}.

    후보는 (a, b, 관계) 순으로 훑는다. 채우지 못한 엣지는 근거가 빈 채 남으므로, 순서
    없이 LIMIT 만 두면 그것들이 매번 같은 창을 차지해 뒤의 엣지가 영영 안 보였다.
    뒤가 남았으면 ``next_cursor`` 를 돌려주고, 그 값을 ``cursor`` 로 다시 주면 마지막으로
    본 엣지 다음부터 훑는다(키셋 — 그사이 채워진 엣지가 빠져도 건너뛰지 않는다). 다른
    owner·문서의 커서나 깨진 커서는 ``invalid_cursor`` 로 거절한다(검색 커서와 같은 규약)."""
    out: dict[str, Any] = {"kind": "evidence.backfill", "executed": False,
                           "filled": 0, "scanned": 0, "has_more": False, "next_cursor": None}
    # 순환 임포트 회피 — query 가 이 모듈을 쓴다. 커서 코덱은 검색과 한 벌이다.
    from .query import _query_fingerprint, cursor_decode, cursor_encode
    qh = _query_fingerprint("evidence.backfill", owner, {"doc": doc_id or ""}, "")
    lim = max(1, int(limit))
    try:
        # ① 후보 엣지 — 근거가 비어 있는 개념 간 관계(양 끝 owner 스코프, SEC-1).
        scope = ""
        params: dict[str, Any] = {"owner": owner}
        if doc_id:
            scope = (" AND EXISTS { MATCH (ch:Chunk)-[:MENTIONS]->(a) "
                     "WHERE ch.doc_id = $doc } ")
            params["doc"] = doc_id
        if cursor:
            data = cursor_decode(cursor)            # 무효면 ValueError("invalid_cursor: …")
            after = data.get("after")
            if data.get("qh") != qh:
                raise ValueError("invalid_cursor: cursor was issued for a different owner or document")
            if not (isinstance(after, list) and len(after) == 3
                    and all(isinstance(x, str) for x in after)):
                raise ValueError("invalid_cursor: cursor has no edge position")
            scope += (" AND (a.concept_id > $a0 OR (a.concept_id = $a0 AND (b.concept_id > $b0 "
                      "OR (b.concept_id = $b0 AND type(r) > $r0)))) ")
            # 커서는 읽은 행의 **논리** id 를 담는다(백엔드가 반환값의 물리 키를 벗긴다).
            # 비교 상대 a.concept_id 는 물리 키이므로 바인딩할 때 합성한다. 양 끝이 같은
            # owner 라 물리 순서와 논리 순서가 같다. 근거: [[vinc.plan.node-pk-namespacing]] §7
            params.update(a0=pkns.key(owner, after[0]), b0=pkns.key(owner, after[1]),
                          r0=after[2])
        rows = store.run(
            "MATCH (a:Concept)-[r]->(b:Concept) "
            "WHERE a.owner_id = $owner AND b.owner_id = $owner "
            f"AND (r.evidence IS NULL OR r.evidence = ''){scope}"
            "RETURN a.concept_id AS aid, a.title AS at, "
            "b.concept_id AS bid, b.title AS bt, type(r) AS rel "
            f"ORDER BY aid, bid, rel LIMIT {lim + 1}", params) or []
    except Exception as exc:
        msg = str(exc) if str(exc).startswith("invalid_cursor") else f"{type(exc).__name__}: {exc}"
        out["error"] = out["note"] = msg
        return out
    rows, more = rows[:lim], len(rows) > lim      # 한 행 더 읽어 뒤가 남았는지 정확히 안다
    out.update(executed=True, scanned=len(rows), has_more=more)
    if more:
        out["next_cursor"] = cursor_encode(
            {"v": 1, "qh": qh, "after": [rows[-1]["aid"], rows[-1]["bid"], rows[-1]["rel"]]})
    for row in rows:
        at, bt = (row.get("at") or "").strip(), (row.get("bt") or "").strip()
        if len(at) < 2 or len(bt) < 2 or at.lower() == bt.lower():
            continue
        # 행의 aid·bid 는 논리 id 다. 아래 두 쿼리의 PK 바인딩은 물리 키여야 맞는다 —
        # 논리 id 그대로면 네임스페이싱 스토어(허브)에서 아무 엣지도 채우지 못한다.
        ends = {"aid": pkns.key(owner, row["aid"]), "bid": pkns.key(owner, row["bid"])}
        try:
            chunks = store.run(
                "MATCH (ch:Chunk)-[:MENTIONS]->(a:Concept), (ch)-[:MENTIONS]->(b:Concept) "
                "WHERE a.concept_id = $aid AND b.concept_id = $bid "
                "AND (ch.owner_id = $owner OR ch.owner_id = '') "
                "RETURN ch.text AS text ORDER BY ch.chunk_id LIMIT 6",
                {**ends, "owner": owner}) or []
        except Exception:
            continue
        # ② 완화 판정용 제목 토큰 — mentions_link 의 어휘·상한을 그대로 재사용한다
        #    (규칙 분기 금지). 토큰셋이 같으면 아무 문장이나 잡히므로 완화는 건너뛴다.
        from engines.mentions_link import _MAX_TITLE_TOKENS, _tokens as _ml_tokens
        ta = set(_ml_tokens(at)[:_MAX_TITLE_TOKENS])
        tb = set(_ml_tokens(bt)[:_MAX_TITLE_TOKENS])
        relaxable = bool(ta) and bool(tb) and ta != tb
        found: tuple[str, float] | None = None
        for ch in chunks:
            for sent in _sentences(ch.get("text") or ""):
                low = sent.lower()
                ia, ib = low.find(at.lower()), low.find(bt.lower())
                if ia >= 0 and ib >= 0:
                    gap = abs(ia - ib)
                    # 가까울수록 높게, 멀어지면 0.5 로 수렴(상한 0.9 — 결정론 근사이지
                    # 사람이 확인한 근거가 아니라는 뜻을 수치가 스스로 말한다).
                    conf = round(max(0.5, 0.9 - gap / 400.0), 3)
                    found = (" ".join(sent.split())[:_EVIDENCE_MAX], conf)
                    break
                if relaxable:
                    stoks = set(_ml_tokens(sent))
                    if ta <= stoks and tb <= stoks:
                        # 고정 0.4 는 값이자 표식이다 — 0.5 미만 = 토큰 완화 백필.
                        # 비준 조건(저작분과 구별 가능)이 이 한 값으로 성립한다.
                        found = (" ".join(sent.split())[:_EVIDENCE_MAX], 0.4)
                        break
            if found:
                break
        if not found:
            continue
        try:
            store.run(
                f"MATCH (a:Concept)-[r:{row['rel']}]->(b:Concept) "
                "WHERE a.concept_id = $aid AND b.concept_id = $bid "
                "AND a.owner_id = $owner AND b.owner_id = $owner "
                "SET r.evidence = $ev, r.confidence = $cf",
                {**ends, "owner": owner, "ev": found[0], "cf": found[1]})
            out["filled"] += 1
        except Exception:
            continue
    return out
