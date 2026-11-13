# -*- coding: utf-8 -*-
"""두 개념 사이의 최단 경로: 홉 최단(`shortest_path_plan`, 엔진 `ALL SHORTEST`)과 confidence 가중 최단
(`weighted_paths`, 파이썬 다익스트라), 그리고 끝점이 개념이 아니거나 없을 때의 거절 문장.

`shortest_path()` 가 입구다. `weight="confidence"` 면 `_weighted_shortest_path` 가 후보 집합
(`path_candidates_plan`)의 엣지를 `graph_read` 의 계획·셰이핑으로 받아 `weighted_paths` 에 넘긴다.
경로가 없으면 층(접두) → 존재(`seed_lookup_plan` 으로 PK 조회) → depth 순서로 이유를 가른다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.X` 와 `from api.query import X` 는 그대로다.
두 이름은 같은 객체다. 여기 함수가 부르는 이름(`execute`·`seed_lookup_plan`·`near_nodes`·계획 함수)은
이 모듈에서 찾으므로, `shortest_path()` 안의 호출을 바꾸려면 `api.query` 가 아니라 이 모듈의 이름을 패치한다.

이 모듈은 `api.query` 를 import 하지 않는다. 바닥은 `api/query_base.py`, 계획·셰이핑은
`api/graph_read.py` 에서 읽는다(`tests/core/test_paths_module.py` 가 둘을 본다).
"""
from __future__ import annotations

from store import pkns

from .graph_read import graph_rows_for_ids_plan, seed_lookup_plan, shape_graph
from .query_base import QueryPlan, _edge_cost, _seed_table, execute, near_nodes


# ── 최단 경로 증거 체인 (E-G7) — 분산 증거 단편화 해소 ─────────────────────────
# 리서치 최단 경로 — 두 개념 사이 물리적으로 연결된 가장 조밀한 엣지 체인을 인출해
# 상류 모델에 증거로 준다(추측 없는 인과 증빙). kuzu `* ALL SHORTEST` —
# 동률 최단 경로 전부. TRAIL ego 순회(목표 미지정)와 달리 **목표쌍 지정형**.

def shortest_path_plan(a_id: str, b_id: str, *, max_depth: int = 6,
                       owner: str | None = None) -> QueryPlan:
    """두 개념 간 최단 경로(들). GQL `ALL SHORTEST`(ladybug `* ALL SHORTEST 1..d`). 엔드포인트
    owner 스코프 — 교차소유자 엣지 부재라 중간 홉 스코프 불요. nodes(p) 시퀀스 + 길이 반환.
    ⚠ 두 끝점은 앞의 MATCH 에서 묶는다. Neo4j 는 선택자 뒤의 WHERE 를 경로를 고른 **뒤에**
    거르므로, 끝점 술어를 거기 두면 모든 개념 쌍의 최단 경로를 먼저 찾는다."""
    depth = max(1, min(int(max_depth), 8))
    params: dict = {"a": pkns.key(owner, a_id), "b": pkns.key(owner, b_id)}
    a_where, b_where = "a.concept_id = $a", "b.concept_id = $b"
    if owner:
        a_where += " AND a.owner_id = $owner"
        b_where += " AND b.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (a:Concept) WHERE {a_where} "
        f"MATCH (b:Concept) WHERE {b_where} "
        f"MATCH p = ALL SHORTEST (a)-[]-{{1,{depth}}}(b) "
        "RETURN nodes(p) AS ns, length(p) AS len ORDER BY len LIMIT 25"
    )
    return QueryPlan("shortest_path", cypher, params,
                     note=f"all-shortest concept path (≤{depth})")


def _node_seq(ns) -> list[dict]:
    """nodes(p) 원소(노드 dict)에서 {id,title} 시퀀스 추출(방언 키 방어)."""
    out = []
    for n in ns or []:
        if isinstance(n, dict):
            cid = n.get("concept_id") or n.get("id")
            if cid:
                out.append({"id": cid, "title": n.get("title") or cid})
    return out


# ── 가중 최단 경로 (E-G8) — confidence 를 거리로 읽는 증거 체인 ────────────────
# 비용 = 1 - confidence 로 다익스트라. 미기입(0.0)이 홉 하나와 같은 값이라
# **증거 없는 그래프에서 홉 최단으로 정확히 퇴화한다**(하위호환·설명가능).
# -log(c) 형은 c=0 에서 발산해 미기입 엣지를 통행 불가로 만들므로 쓰지 않는다.
# ⚠ 엔진 WSHORTEST 로 옮기지 말 것(검토 후 기각) — 저장 컬럼만 받아 파생열 드리프트
# 의무가 생기고, 패턴의 모든 rel 테이블에 그 열이 없으면 크래시한다:
# [[record:weighted-path-is-computed-in-python-not-by-the-engine]].

PATH_TIE_EPS = 1e-9      #: 부동소수 동률 허용 폭(비용 비교 전용)
PATH_LIMIT = 25          #: 반환 경로 상한 — `shortest_path_plan` 의 LIMIT 25 와 동수


def weighted_paths(edges: list[dict], a_id: str, b_id: str, *, max_hops: int = 6,
                   limit: int = PATH_LIMIT) -> list[dict]:
    """confidence 가중 최단 경로(들) — 순수·결정론(난수·시간 무의존).

    ``edges``: :func:`shape_graph` 엣지 리스트(각 ``{source,target,[confidence]}``).
    무방향으로 읽고(관계의 증거 강도는 방향과 무관), 같은 쌍에 엣지가 여럿이면
    **가장 싼 것**(=가장 신뢰도 높은 것)만 남긴다. 반환 ``[{ids, cost, length}]`` —
    최소 비용 동률 전부, ``(cost, length, ids)`` 결정론 정렬, ``limit`` 상한.

    홉 상한이 있으므로 (노드, 홉수) 층 위에서 다익스트라를 돈다 — 홉을 무시하고
    최적화하면 "더 싸지만 상한을 넘는" 경로를 답으로 골라 상한 계약을 어긴다.
    경로는 **단순 경로**(노드 무반복)로 한정한다."""
    import heapq

    adj: dict[str, dict[str, float]] = {}
    for e in edges or []:
        s, t = e.get("source"), e.get("target")
        if not s or not t or s == t:
            continue
        w = _edge_cost(e)
        for u, v in ((s, t), (t, s)):
            m = adj.setdefault(u, {})
            if v not in m or w < m[v]:
                m[v] = w
    if a_id not in adj or b_id not in adj:
        return []
    hops_cap = max(1, min(int(max_hops), 8))
    INF = float("inf")
    best: dict[tuple[str, int], float] = {(a_id, 0): 0.0}
    pq: list[tuple[float, int, str]] = [(0.0, 0, a_id)]
    while pq:
        d, h, u = heapq.heappop(pq)
        if d > best.get((u, h), INF) + PATH_TIE_EPS or h >= hops_cap:
            continue
        for v in sorted(adj.get(u, {})):
            nd, nh = d + adj[u][v], h + 1
            if nd + PATH_TIE_EPS < best.get((v, nh), INF):
                best[(v, nh)] = nd
                heapq.heappush(pq, (nd, nh, v))
    reached = [c for (n, _h), c in best.items() if n == b_id]
    if not reached:
        return []
    opt = min(reached)
    # 최적 비용과 동률인 경로를 tight-edge 전방 DFS 로 열거한다(결정론 — 이웃 정렬
    # 순서 고정). `best` 층이 각 접두의 최적성을 증명하므로 가지치기가 정확하다.
    out: list[dict] = []
    seen_paths: set[tuple[str, ...]] = set()

    def _walk(u: str, h: int, c: float, path: list[str], visited: set[str]) -> None:
        if len(out) >= limit:
            return
        if u == b_id and abs(c - opt) <= PATH_TIE_EPS:
            key = tuple(path)
            if key not in seen_paths:
                seen_paths.add(key)
                out.append({"ids": list(path), "cost": round(c, 6), "length": len(path) - 1})
            return          # 단순 경로 — b 도달 후 연장하지 않는다
        if h >= hops_cap:
            return
        for v in sorted(adj.get(u, {})):
            if v in visited:
                continue
            nd, nh = c + adj[u][v], h + 1
            if abs(best.get((v, nh), INF) - nd) > PATH_TIE_EPS or nd > opt + PATH_TIE_EPS:
                continue    # 이 접두가 (v,nh) 의 최적이 아니거나 이미 최적 비용 초과
            visited.add(v); path.append(v)
            _walk(v, nh, nd, path, visited)
            path.pop(); visited.discard(v)

    _walk(a_id, 0, 0.0, [a_id], {a_id})
    out.sort(key=lambda p: (p["cost"], p["length"], p["ids"]))
    return out[:limit]


def path_candidates_plan(seed: str, *, depth: int = 6,
                         owner: str | None = None) -> QueryPlan:
    """가중 경로 후보 집합 — seed 에서 ``depth`` 홉 이내 Concept id(E-G8).

    :func:`subgraph_ids_plan` 과 같은 SHORTEST 도달집합 순회지만 상한이 다르다
    (경로 상한은 8까지 열려 있고 ego 뷰의 4 클램프와 무관하다). ``a`` 에서 ``depth``
    홉 이내에 없는 노드는 길이 ``depth`` 이하 경로에 **구조적으로** 못 오르므로
    이 집합이 다익스트라의 완전한 탐색 공간이다."""
    depth = max(1, min(int(depth), 8))
    params: dict = {"seed": pkns.key(owner, seed)}
    n_where, m_where = "n.concept_id = $seed", "m.concept_id <> $seed"   # 시드 제외: subgraph_ids_plan 과 같다
    if owner:
        n_where += " AND n.owner_id = $owner"
        m_where += " AND m.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (n:Concept) WHERE {n_where} "
        f"MATCH SHORTEST 1 (n)-[]-{{1,{depth}}}(m:Concept) WHERE {m_where} "
        "RETURN DISTINCT m.concept_id AS id"
    )
    return QueryPlan("path_candidates", cypher, params,
                     note=f"weighted-path candidate set (depth={depth})")


#: `weight` 인자 허용값 — hops = E-G7 홉 최단(기본), confidence = E-G8 가중 최단.
WEIGHT_HOPS = "hops"
WEIGHT_CONFIDENCE = "confidence"

#: 이 표면은 **개념 층만** 걷는다(`shortest_path_plan` 의 패턴이 `(:Concept)` 이다).
#: 그래서 record·decision·episode id 를 주면 노드가 멀쩡히 있어도 매치가 0이고,
#: 여태 그 답이 `found:false` 였다 — "안 이어져 있다"와 구분이 안 됐다. 실제로
#: 그 답을 연결 부재로 읽고 "노드들이 섬이다"라고 보고한 적이 있다.
#: 접두만 본다: 알려진 비-개념 접두일 때만 말하므로 정상 개념을 거짓으로 막지 않는다.
_NON_CONCEPT_PREFIXES = ("record:", "decision:", "episode:", "domain:", "bridge:")


def _weighted_shortest_path(a_id: str, b_id: str, *, max_depth: int, owner: str | None,
                            cfg, store) -> dict:
    """E-G8 가중 갈래 — 후보 집합 → 엣지 투영 → 파이썬 다익스트라.

    엣지는 :func:`graph_rows_for_ids_plan`(이미 evidence/confidence 를 투영한다)로
    받고 **후보 집합 안으로 여과**한다(ego 경계 유출 제거 — graph() 와 같은 정책)."""
    base = {"kind": "shortest_path", "executed": False, "found": False,
            "a": a_id, "b": b_id, "length": None, "paths": [], "count": 0,
            "weight": WEIGHT_CONFIDENCE, "cost": None}
    ids_res = execute(path_candidates_plan(a_id, depth=max_depth, owner=owner),
                      cfg, store=store)
    if not ids_res.get("executed"):
        base["note"] = ids_res.get("note", "")
        return base
    ids = sorted({a_id, *(r["id"] for r in ids_res.get("rows", []) if r.get("id"))})
    base["executed"] = True
    if b_id not in ids:
        return base          # depth 안에서 닿지 않는다 — found=False(경로 부재와 동일 계약)
    rows_res = execute(graph_rows_for_ids_plan(ids, owner=owner), cfg, store=store)
    if not rows_res.get("executed"):
        base["executed"] = False
        base["note"] = rows_res.get("note", "")
        return base
    shaped = shape_graph(rows_res.get("rows", []))
    keep = set(ids)
    edges = [e for e in shaped["edges"] if e["source"] in keep and e["target"] in keep]
    title_of = {n["id"]: n.get("label") or n["id"] for n in shaped["nodes"]}
    paths = weighted_paths(edges, a_id, b_id, max_hops=max_depth)
    if not paths:
        return base
    base.update(found=True, count=len(paths), cost=paths[0]["cost"],
                length=paths[0]["length"],
                paths=[{"ids": p["ids"], "titles": [title_of.get(i, i) for i in p["ids"]],
                        "length": p["length"], "cost": p["cost"]} for p in paths])
    return base


def _concept_only_refusal(base: dict, wrong: list[str]) -> dict:
    """개념이 아닌 끝점은 **연결 부재가 아니라 층 부재**다. 이걸 안 가르면 읽는 쪽이
    빈 답을 "안 이어져 있다"로 읽는다."""
    base["error"] = ("this tool walks the concept layer only: "
                     + ", ".join(wrong) + " is not a concept id")
    base["hint"] = ("Shortest paths are computed between `concept:` nodes. "
                    "A record, decision or episode is attached to concepts "
                    "rather than sitting on the path graph, so it can never "
                    "be an endpoint here even when it exists and is well "
                    "connected. Use vinc_neighbors on that id to see what it "
                    "attaches to, then path between those concepts.")
    return base


def _unknown_node_refusal(base: dict, missing: list[str], *, owner, store) -> dict:
    base["error"] = "unknown node: " + ", ".join(missing)
    cands = [c for x in missing for c in near_nodes(store, x, owner=owner)]
    base["hint"] = ("Node ids are exact and type-prefixed (e.g. `concept:view`). "
                    + ("Did you mean: "
                       + ", ".join(dict.fromkeys(c["id"] for c in cands)) + "? "
                       if cands else "")
                    + "Use vinc_search to find the id first.")
    if cands:
        base["candidates"] = cands[:3]
    return base


def _probe_node(x: str, *, owner, cfg, store) -> dict | None:
    """PK 로 노드 하나를 묻는다. 반환: 행(dict) · 없으면 None · 프로브가 못 돌았으면
    ``{"_unknown": True}``(모르는 것이지 없는 것이 아니다)."""
    probe = execute(seed_lookup_plan(x, owner=owner), cfg, store=store)
    if not probe.get("executed"):
        return {"_unknown": True, "note": probe.get("note", "")}
    return next((r for r in probe.get("rows", []) if r.get("id") == x), None)


def _same_endpoint_path(x: str, *, weight: str, owner, cfg, store) -> dict:
    """a == b — 길이 0 경로 하나(노드 자신)로 답한다. hops·confidence 두 갈래 공통.

    ⚠ 엔진 패턴이 `1..d` 라 자기 자신까지의 경로는 매치가 0이고, 여태 이 입력이
    `no path within max_depth` 로 답했다 — 노드가 멀쩡히 있는데 "안 이어져 있다"고
    말한 셈이다. 거절은 그대로 둔다: 개념이 아니거나 없는 id 는 a != b 일 때와 같은
    문장으로 거절한다(같은 id 두 번이 거절을 우회하는 길이 되면 안 된다).
    근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]]"""
    base = {"kind": "shortest_path", "executed": True, "found": False,
            "a": x, "b": x, "length": None, "paths": [], "count": 0, "weight": weight}
    if weight == WEIGHT_CONFIDENCE:
        base["cost"] = None
    if x.lower().startswith(_NON_CONCEPT_PREFIXES):
        return _concept_only_refusal(base, [x])
    row = _probe_node(x, owner=owner, cfg=cfg, store=store)
    if row is not None and row.get("_unknown"):
        base["executed"] = False
        base["note"] = row.get("note", "")
        return base
    if row is None:
        return _unknown_node_refusal(base, [x], owner=owner, store=store)
    if _seed_table(x)[0] != "Concept":
        # 개방층(Entity) 노드 — 있지만 개념이 아니다.
        return _concept_only_refusal(base, [x])
    path = {"ids": [x], "titles": [row.get("title") or x], "length": 0}
    if weight == WEIGHT_CONFIDENCE:
        path["cost"] = 0.0
        base["cost"] = 0.0
    base.update(found=True, length=0, paths=[path], count=1)
    return base


def shortest_path(a_id: str, b_id: str, *, max_depth: int = 6,
                  owner: str | None = None, weight: str = WEIGHT_HOPS,
                  cfg=None, store=None) -> dict:
    """E-G7/E-G8 — 두 개념 간 최단 경로 증거 체인(조회 전용). 반환 {executed, found,
    a, b, length, paths:[{ids,titles}], count, weight}. 경로 부재 시 found=False.

    ``weight``: ``"hops"``(기본, E-G7 — 엔진 ALL SHORTEST, 동률 전부) 또는
    ``"confidence"``(E-G8 — 파이썬 다익스트라, 비용 = 1-confidence). 가중 갈래는
    ``cost``(경로 비용 합)를 추가로 싣는다. 미지 값은 조용히 ``hops`` 로 강등하고
    응답의 ``weight`` 가 **실제로 적용한 축**을 에코한다(`_apply_basis` 와 같은 태도).

    ``a == b`` 는 길이 0 경로 하나(노드 자신)로 답한다(:func:`_same_endpoint_path`)."""
    confidence = (weight or "").strip().lower() == WEIGHT_CONFIDENCE
    if a_id == b_id:
        return _same_endpoint_path(a_id, weight=WEIGHT_CONFIDENCE if confidence
                                   else WEIGHT_HOPS, owner=owner, cfg=cfg, store=store)
    if confidence:
        return _weighted_shortest_path(a_id, b_id, max_depth=max_depth, owner=owner,
                                       cfg=cfg, store=store)
    res = execute(shortest_path_plan(a_id, b_id, max_depth=max_depth, owner=owner),
                  cfg, store=store)
    base = {"kind": "shortest_path", "executed": res.get("executed", False),
            "found": False, "a": a_id, "b": b_id, "length": None,
            "paths": [], "count": 0, "weight": WEIGHT_HOPS}
    if not res.get("executed"):
        base["note"] = res.get("note", "")
        return base
    seqs = []
    for r in res.get("rows", []):
        ns = r.get("ns") or []
        # 엔진 패턴은 모든 rel 테이블을 걷는다 — 경로가 Chunk(MENTIONS)나 다른 비-개념
        # 노드를 지날 수 있고, _node_seq 가 그 노드를 말없이 떨궈 길이가 줄면 직접
        # 엣지처럼 읽힌다(두 노드가 같은 청크에만 함께 언급돼도 length:1 로 나왔다).
        # 전원 개념인 경로만 남긴다 — 모든 노드가 개념이면 모든 홉이 개념↔개념 저작
        # 엣지다(MENTIONS·IN_DOMAIN 은 개념 둘을 잇지 못한다). 더 긴 저작 경로는 가중
        # 팔(confidence)이 개념 엣지 집합에서 완전 탐색한다. 근거: [[vinc.manual.core]]
        if not ns or any(not (isinstance(n, dict) and n.get("concept_id")) for n in ns):
            continue
        seq = _node_seq(ns)
        if len(seq) >= 2:
            seqs.append({"ids": [s["id"] for s in seq],
                         "titles": [s["title"] for s in seq],
                         "length": len(seq) - 1})
    # 결정론 정렬(길이→노드 시퀀스) + dedup
    uniq: dict = {}
    for s in sorted(seqs, key=lambda s: (s["length"], s["ids"])):
        uniq.setdefault(tuple(s["ids"]), s)
    paths = list(uniq.values())
    # 최단만 남긴다. 설명이 "ALL shortest paths (ties included)"를 약속하는데 행 안에
    # 더 긴 경로가 섞여 나온 적이 있다 — 계약은 여기서 지킨다(엔진 의미론에 기대지 않는다).
    # 근거는 :func:`_same_endpoint_path` 독스트링의 인용과 같다.
    if paths:
        paths = [p for p in paths if p["length"] == paths[0]["length"]]
    base.update(found=bool(paths), paths=paths, count=len(paths),
                length=paths[0]["length"] if paths else None)
    if not paths:
        # F7 — 여태 `found:false` 뿐이라 **끝점이 없는 것**과 **길이 없는 것**이
        # 구분되지 않았다. 둘은 다음 행동이 완전히 다르다: 앞은 id 를 고쳐야 하고
        # 뒤는 depth 를 늘리거나 엣지를 이어야 한다. 어느 끝이 문제인지 짚어 준다.
        #
        # 순서: **층(접두) → 존재(PK) → depth**. 층 검사가 먼저다 — 개념 층 밖 id 는
        # 있든 없든 여기서 끝점이 될 수 없으므로, 없는 `record:x` 에 "unknown node"
        # 라고 답하면 id 만 고치면 될 것처럼 읽힌다. 문서화된 거절은 개념 전용이다.
        # 근거는 :func:`_same_endpoint_path` 독스트링의 인용과 같다.
        wrong = [x for x in (a_id, b_id) if x.lower().startswith(_NON_CONCEPT_PREFIXES)]
        if wrong:
            return _concept_only_refusal(base, wrong)
        # ⚠ **존재는 PK 로 묻는다.** 이 검사가 `near_nodes`(제목·props **유사도** 검색)
        # 였고, 유사도는 존재의 증거가 아니다: 슬러그 id 는 제 산문 제목 안에 글자
        # 그대로 안 나타나는 일이 흔해서 후보가 0이 되고, 그러면 **실재하는 노드가
        # `unknown node` 로 보고된다.** 예: `concept:hybrid-search`
        # 는 이웃 28개를 가진 멀쩡한 개념인데 이 표면만 "unknown node" 라고 답했다.
        # `neighbors()` 가 정확히 같은 결함을 `api/neighbors.py` 에서 이미 고쳤는데(그 주석이
        # 이 처방을 적어 놓았다) **형제 함수만 안 받았다.**
        # 근거: [[record:path-called-an-existing-concept-unknown-2026-09-08]]
        # 프로브가 못 돌았으면 **모르는 것**이지 없는 것이 아니다 — 조회 실패를
        # 부재로 단언하지 않는다(`neighbors()` 와 같은 태도).
        missing = [x for x in (a_id, b_id)
                   if _probe_node(x, owner=owner, cfg=cfg, store=store) is None]
        if missing:
            return _unknown_node_refusal(base, missing, owner=owner, store=store)
        base["error"] = f"no path within max_depth={max_depth}"
        base["hint"] = ("Both nodes exist but nothing connects them within this "
                        "depth. Raise `max_depth`, or use vinc_neighbors on each "
                        "end to see what they do connect to.")
    return base
