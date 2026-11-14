"""belief 판정의 쓰기: 이전 유효구간을 닫고 현재 확률을 엣지에 미러한다.

계약(검증·읽기 주석)은 `api.belief_contract` 에 있다. 여기는 그 계약이 받아들인 판정만 쓴다.
노드 쓰기와 끝점 스탬프는 `api.fragment` 의 공개 함수(`merge_node`·`stamp_edge_endpoints`)로
하므로, 소유자 스탬프·역할 경고·쓰기 묶음 규칙이 일반 저작 경로와 같다.
"""
from __future__ import annotations

from store import pkns

from . import fragment
from .belief_contract import ENDS, RELATIONS, belief_key, belief_subject, current, edge_key, load


def apply(added, edges, *, store, owner, warnings):
    """새 판정을 보관한 뒤 이전 구간을 닫고 현재 확률을 미러한다. 재시도 가능하다."""
    rows = load(store, owner, attached=True)
    # 재시도에도 전체 이력을 맞춘다. 같은 id는 자기 자신을 닫지 않는다.
    subjects = {belief_subject(r) for r in rows if any(e.get("from") == r["id"] for e in edges)}
    subjects.update(belief_subject(r) for r in added)
    for subject in subjects:
        chain = sorted([r for r in rows if belief_subject(r) == subject],
                       key=lambda r: (r["valid_from"], r.get("created") or "", r["id"]))
        for prior, following in zip(chain, chain[1:]):
            end = following["valid_from"]
            if not prior["valid_until"] or prior["valid_until"] > end:
                fragment.merge_node(store, {"id": prior["id"], "type": "record", "valid_until": end},
                                    owner, warnings, owner_explicit=True)
                prior["valid_until"] = end
    latest = {belief_key(r): r for r in current(rows)}
    touched = {edge_key(e.get("from"), RELATIONS.get(e.get("type"), ""), e.get("to")) for e in edges}
    touched.update(belief_key(r) for r in added)
    touched.update(belief_key(r) for r in rows if belief_subject(r) in subjects)
    for key in touched & latest.keys():
        src, rel, dst = key
        belief = latest[key]
        sl, sp = ENDS[src.split(":", 1)[0]]
        dl, dp = ENDS[dst.split(":", 1)[0]]
        params = {"s": pkns.key(owner, src), "d": pkns.key(owner, dst),
                  "owner": owner, "p": belief["props"]["p"]}
        patterns = [f"(a:{sl})-[r:{rel}]->(b:{dl})"]
        if rel == "RELATED_TO":
            patterns.append(f"(b:{dl})-[r:{rel}]->(a:{sl})")
        changed = False
        for pattern in patterns:
            # 무방향 SET 대신 저장된 방향별로 쓴다. 같은 엣지를 다시 만들지 않는다.
            match = (f"MATCH {pattern} WHERE a.{sp} = $s AND b.{dp} = $d "
                     "AND a.owner_id = $owner AND b.owner_id = $owner ")
            found = store.run(match + "RETURN r.confidence AS confidence", params)
            if any(r["confidence"] != params["p"] for r in found):
                store.run(match + "SET r.confidence = $p", params)
                changed = True
        if changed:
            fragment.stamp_edge_endpoints(store, rel, ((sl, sp), (dl, dp)), params["s"], params["d"],
                                          owner, osc=True, warnings=warnings)
