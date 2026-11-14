# -*- coding: utf-8 -*-
"""잇기 제안(`vinc_suggest_ties`): 연결이 적은 개념에 이어 볼 만한 개념을 점수와 근거로 내는 결정론 읽기.

`suggest_ties` 는 소유자의 개념 메타(`concept_meta_plan`)를 읽어 태그 희소성(`_tag_weights`), 같은 주제, 공통 이웃 수로
점수를 매기고, 산식(`TIE_SCORE_FORMULA`)과 항별 값(`signals`)을 함께 낸다. 이미 이어진 개념은 `graph_read` 의
`graph_plan` 행과 브리지로 이어진 이웃(`bridge_neighbors_plan`)으로 걸러 낸다. 개념이 아닌 끝점은 `paths` 의
`_NON_CONCEPT_PREFIXES` 로 알아보고, 모르는 id 에는 `near_nodes` 의 후보를 붙인다. 조회 전용이다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.suggest_ties`·`q.X` 와 `from api.query import X` 는 그대로다.
두 이름은 같은 객체다. 여기 함수가 부르는 이름(`execute`·`graph_plan`·`near_nodes` 등)은 이 모듈에서 찾으므로, 그
호출을 바꾸려면 `api.query` 가 아니라 이 모듈의 이름을 패치한다.

이 모듈은 `api.query` 를 import 하지 않는다(`tests/core/test_ties_module.py` 가 본다).
"""
from __future__ import annotations

from store import pkns
from store.querybase import QueryPlan, execute

from .graph_read import graph_plan
from .paths import _NON_CONCEPT_PREFIXES
from .query_base import _bridge_owner_pred, near_nodes


def bridge_neighbors_plan(node: str, *, owner: str) -> QueryPlan:
    """잇기 제안의 접착제 인지(F3) — ``node`` 와 Bridge 매개로 이미 이어진 개념
    (CONNECTS 양팔 반대편). Bridge 는 과도기 술어, 개념은 엄격 owner."""
    cypher = (
        "MATCH (x:Concept)<-[:CONNECTS]-(b:Bridge)-[:CONNECTS]->(y:Concept) "
        f"WHERE x.concept_id = $node AND {_bridge_owner_pred('b')} "
        "AND y.owner_id = $owner "
        "RETURN DISTINCT y.concept_id AS id"
    )
    return QueryPlan("bridge_neighbors", cypher,
                     {"node": pkns.key(owner, node), "owner": owner},
                     note="bridge-mediated neighbors (tie-suggest exclusion)")


def concept_meta_plan(*, owner: str) -> QueryPlan:
    """잇기 제안용 Concept 메타(id·title·domain·tags) — 순수 조회."""
    cypher = (
        "MATCH (c:Concept) WHERE c.owner_id = $owner "
        "RETURN c.concept_id AS id, c.title AS title, c.domain AS domain, "
        "coalesce(c.tags, []) AS tags ORDER BY id"
    )
    return QueryPlan("concept_meta", cypher, {"owner": owner},
                     note="concept metadata for tie suggestions")


# ── 잇기 제안 점수 산식 (F11) ────────────────────────────────────────────────
# 세 신호를 쓰고 각 항의 근거를 `signals` 로 함께 돌려준다:
#   태그   Σ 2·ln(N/df)/ln(N)  — 표준 IDF. 나만 가진 태그 ≈2, 전부가 가진 태그 0.
#   도메인 +1.0                — 약한 신호라 약한 가중.
#   이웃   1.5·min(공통 이웃, 4) — 후보는 직접 연결이 이미 제외돼 있으므로 공통
#          이웃 = 2홉 근접이다. **동률을 실제로 깨는 것은 이 항이다.**
# ⚠ 태그를 희소성 없이 세는 옛 산식으로 되돌리지 말 것 — 점수가 포화돼 limit 이
# 사실상 id 사전순으로 자른다: [[record:tie-suggestions-were-tied-not-ranked]].
_TIE_TAG_MAX = 2.0
_TIE_DOMAIN_W = 1.0
_TIE_LINK_W = 1.5
_TIE_LINK_CAP = 4
TIE_SCORE_FORMULA = ("score = sum(2·ln(N/df) / ln(N) for shared tags) "
                     "+ 1.0 (same domain) + 1.5·min(common neighbors, 4)")


def _tag_weights(rows: list[dict]) -> dict[str, float]:
    """태그별 희소성 가중치(IDF). ``rows`` 는 owner 의 전체 개념 메타."""
    import math
    n = len(rows)
    df: dict[str, int] = {}
    for r in rows:
        for t in set(r.get("tags") or []):
            df[t] = df.get(t, 0) + 1
    if n <= 1:
        return {t: _TIE_TAG_MAX for t in df}
    den = math.log(n)
    out = {}
    for t, d in df.items():
        # d ≥ n(전 개념이 가진 태그)이면 0 — 정보가 없다. 음수로 내려가진 않는다.
        out[t] = max(0.0, _TIE_TAG_MAX * math.log(n / max(1, d)) / den)
    return out


def suggest_ties(*, owner: str, node: str, limit: int = 5, cfg=None, store=None) -> dict:
    """고아(저연결) 노드의 잇기 후보 — D25(임베딩 유사도)의 **전 단계** 결정론 근사
    (D32). 점수는 태그 희소성 + 도메인 일치 + 그래프 거리 2홉이고, 산식은
    :data:`TIE_SCORE_FORMULA` 로 응답에 함께 공개한다(F11). 각 후보는 산출 근거를
    ``reason``(지배 신호 이름)·``signals``(항별 값)로 싣는다 — merge_candidates 의
    ``reason`` 과 같은 방식이다. 이미 연결된 노드는 제외. 조회 전용 — 확정(엣지 생성)은
    fragment MERGE(POST /ingest/fragment) 경유로 단일 쓰기 표면(R4)을 유지한다."""
    meta = execute(concept_meta_plan(owner=owner), cfg, store=store)
    if not meta.get("executed"):
        return {"kind": "suggest_ties", "executed": False, "node": node,
                "suggestions": [], "total": 0, "truncated": False,
                "formula": TIE_SCORE_FORMULA, "note": meta.get("note", "")}
    rows = meta.get("rows", [])
    me = next((r for r in rows if r.get("id") == node), None)
    if me is None:
        # 비-개념 끝점은 연결 부재가 아니라 층 부재다(path 의 개념 전용 거절과 같은
        # 태도). suggest_ties 는 개념 메타로만 점수를 매기므로 결정·기록·사건은 여기서
        # 후보를 받을 수 없다 — "unknown concept" 로 답하면 id 만 고치면 될 것처럼
        # 읽힌다. 근거: [[vinc.manual.core]]
        if node.lower().startswith(_NON_CONCEPT_PREFIXES):
            return {"kind": "suggest_ties", "executed": True, "found": False,
                    "node": node, "suggestions": [], "total": 0, "truncated": False,
                    "formula": TIE_SCORE_FORMULA,
                    "error": f"suggest_ties walks the concept layer only: {node} is not a concept id",
                    "hint": ("Tie scores are computed between `concept:` nodes (tag overlap, "
                             "domain, shared neighbours). A record, decision or episode attaches "
                             "to concepts rather than being tied to them, so it is never a tie "
                             "endpoint here. Use vinc_neighbors on that id to see its attachments.")}
        # 후보에서 질문한 노드 자신은 뺀다 — 자기 자신을 잇자고 제안하지 않는다.
        cands = [c for c in near_nodes(store, node, owner=owner) if c.get("id") != node]
        # F4 봉투 통일 — total·truncated·formula 를 **결과가 있을 때만** 싣던 것을
        # 고친다. 계약에 적힌 필드가 상황에 따라 사라지면 호스트는 그것을 옵셔널로
        # 취급할 수밖에 없고, 그러면 truncated 같은 안전 표식이 무의미해진다.
        return {"kind": "suggest_ties", "executed": True, "found": False,
                "node": node, "suggestions": [], "total": 0, "truncated": False,
                "formula": TIE_SCORE_FORMULA,
                "note": f"unknown concept: {node}",
                "error": f"unknown concept: {node}",
                "hint": ("Node ids are exact and type-prefixed (e.g. `concept:view`). "
                         + ("Did you mean: " + ", ".join(c["id"] for c in cands) + "? "
                            if cands else "")
                         + "Use vinc_search to find the id first."),
                **({"candidates": cands} if cands else {})}
    g = execute(graph_plan(owner=owner), cfg, store=store)
    linked = {node}
    # F11 — 인접표를 **전 노드**로 만든다(예전엔 seed 의 이웃만 모았다). 후보의
    # 이웃까지 알아야 "공통 이웃 수"(2홉 근접)를 잴 수 있다. 무방향으로 둔다 —
    # 잇기 제안에 방향은 의미가 없다.
    adj: dict[str, set[str]] = {}
    for r in g.get("rows", []):
        a, b = r.get("id"), r.get("target")
        if not a or not b:
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
        if a == node:
            linked.add(b)
        elif b == node:
            linked.add(a)
    # F3 — 접착제 인지: 브릿지 매개로 이미 이어진 개념도 linked 에 합류시켜 기존
    # 글루를 중복 제안하지 않는다(graph_plan 은 CC 직접 엣지만 본다).
    bn = execute(bridge_neighbors_plan(node, owner=owner), cfg, store=store)
    if bn.get("executed"):
        linked.update(r["id"] for r in bn.get("rows", []) if r.get("id"))
    my_tags = set(me.get("tags") or [])
    my_domain = me.get("domain") or ""
    my_nbrs = adj.get(node, set())
    weights = _tag_weights(rows)
    out = []
    for r in rows:
        cid = r.get("id")
        if not cid or cid in linked:
            continue
        shared = sorted(my_tags & set(r.get("tags") or []))
        tag_s = sum((weights.get(t, _TIE_TAG_MAX) for t in shared), 0.0)
        dom_s = _TIE_DOMAIN_W if my_domain and r.get("domain") == my_domain else 0.0
        common = sorted(my_nbrs & adj.get(cid, set()))
        link_s = _TIE_LINK_W * min(len(common), _TIE_LINK_CAP)
        score = tag_s + dom_s + link_s
        if score <= 0:
            continue
        # 지배 신호 = reason. 동점이면 정보량이 큰 쪽부터(이웃 > 태그 > 도메인).
        reason = max((("shared_neighbors", link_s), ("shared_tags", tag_s),
                      ("same_domain", dom_s)), key=lambda kv: kv[1])[0]
        out.append({"id": cid, "title": r.get("title") or cid,
                    "domain": r.get("domain") or "", "shared_tags": shared,
                    "score": round(score, 4), "reason": reason,
                    "signals": {"tags": round(tag_s, 4), "domain": dom_s,
                                "neighbors": round(link_s, 4),
                                "common_neighbors": len(common)},
                    "shared_neighbors": common[:3]})
    out.sort(key=lambda s: (-s["score"], s["id"]))   # 결정론 순서
    n = max(1, int(limit))
    return {"kind": "suggest_ties", "executed": True, "node": node,
            "suggestions": out[:n], "total": len(out),
            # 잘렸다는 사실을 말하지 않으면 호스트는 "후보가 이게 전부"로 읽는다.
            "truncated": len(out) > n, "formula": TIE_SCORE_FORMULA}
