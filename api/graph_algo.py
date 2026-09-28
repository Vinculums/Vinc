# -*- coding: utf-8 -*-
"""구조 진단의 그래프 알고리즘 — 약연결 성분과 Leiden 커뮤니티. 순수 함수다.

여태 이 두 계산은 엔진의 algo 확장(`CALL louvain`·`weakly_connected_components`)에
맡겼다. ladybug 0.20 의 algo 확장은 `libnetworkit.so`·`libarrow.so.2500` 을 동적으로
링크하는데 확장에도 휠에도 그 둘이 없어, 허브에서 `vinc_diagnose` 의 components·
communities·community_context 가 전부 extension_unavailable 로 꺼졌다. 상류가 자기
소스 트리 안의 networkit 을 빌드해 링크한 것이라 ABI 가 맞는 사본을 플랫폼마다 따로
구해야 한다. 그래서 계산을 여기로 옮겼다(2026-09-28 발주자 선택).
근거: [[vinc.ops.ladybug-upgrade-review-2026-09-24 §2.3]] (원인), 문헌 조사 §10 레버 1·2

얻은 것:
  - 확장·다운로드·동결 번들 없이 허브와 데스크톱(Windows 0.20.3 포함)이 같은 답을 낸다
  - 커뮤니티가 Louvain 에서 **Leiden** 으로 바뀐다. Louvain 은 커뮤니티의 최대 25%를
    약하게, 16%를 끊긴 채로 돌려줄 수 있고(Traag 2019), 커뮤니티가 답변 재료가 되는
    `community_context` 에서는 그 결함이 그대로 읽는 사람에게 보인다
  - 투영을 소유자로 거를 수 있다(엔진 투영은 전체 표만 됐다). 두 스페이스의 같은 슬러그가
    한 결과에 섞이던 일이 투영 단계에서 사라진다

규모: 개인 그래프가 노드 수천이다. 파이썬 딕셔너리로 1초 안쪽이다. 수십만이 되면 다시 잰다.

**결정론이다.** Leiden 의 순서는 시드를 고정한 난수로 섞고, 동률은 id 순으로 가른다. 같은
그래프는 언제나 같은 커뮤니티를 낸다 — 답변 재료가 호출마다 바뀌면 안 된다.
"""
from __future__ import annotations

import random
from collections import defaultdict

#: 고립(차수 0) 노드의 커뮤니티 값. 엔진 Louvain 과 같은 값을 쓴다 — 소비자
#: (`api.query.communities`·`community_context`)가 이 값을 "커뮤니티 아님"으로 읽는다.
ISOLATED = -1

#: 기본 해상도(모듈러리티의 γ). 1.0 이 표준 모듈러리티다.
DEFAULT_RESOLUTION = 1.0
#: 기본 시드. 바꾸면 결과가 바뀐다 — 테스트와 답변 재료의 안정성이 이 값에 묶인다.
DEFAULT_SEED = 20260928
#: 집약 단계 상한. 보통 몇 단계에서 멈춘다. 무한 루프 방어일 뿐이다.
MAX_LEVELS = 64


def _undirected(nodes, edges) -> tuple[list[str], dict[str, dict[str, float]]]:
    """노드 목록과 무향 가중 인접을 만든다. 자기 루프는 버리고 겹친 엣지는 가중치를 더한다.
    엣지의 끝점이 노드 목록에 없으면 노드로 더한다(투영이 둘을 따로 읽어도 어긋나지 않게)."""
    order: list[str] = []
    seen: set[str] = set()
    for n in nodes:
        if n and n not in seen:
            seen.add(n)
            order.append(n)
    adj: dict[str, dict[str, float]] = {n: {} for n in order}
    for e in edges:
        a, b = e[0], e[1]
        w = float(e[2]) if len(e) > 2 and e[2] is not None else 1.0
        if not a or not b or a == b or w <= 0:
            continue
        for x in (a, b):
            if x not in adj:
                adj[x] = {}
                order.append(x)
        adj[a][b] = adj[a].get(b, 0.0) + w
        adj[b][a] = adj[b].get(a, 0.0) + w
    # 이웃도 정렬해 둔다. 이웃 순서가 입력 엣지 순서를 따라가면 지역 이동의 큐 순서가
    # 바뀌어 같은 그래프가 다른 커뮤니티를 낸다 — 투영 조회는 순서를 약속하지 않는다.
    return sorted(order), {n: dict(sorted(nb.items())) for n, nb in adj.items()}


def _relabel(assign: dict[str, object]) -> dict[str, int]:
    """그룹 값을 0 부터 다시 매긴다: 큰 그룹이 먼저, 같으면 가장 작은 구성원 id 순.
    호출마다 같은 번호가 나오게 하는 것이 목적이다(ISOLATED 는 그대로 둔다)."""
    members: dict[object, list[str]] = defaultdict(list)
    for n, g in assign.items():
        if g != ISOLATED:
            members[g].append(n)
    ranked = sorted(members.values(), key=lambda m: (-len(m), min(m)))
    out = {n: ISOLATED for n, g in assign.items() if g == ISOLATED}
    for i, m in enumerate(ranked):
        for n in m:
            out[n] = i
    return out


def weakly_connected_components(nodes, edges) -> dict[str, int]:
    """방향을 무시한 연결 성분. 반환 `{id: 성분 번호}` — 번호는 큰 성분부터 0.
    고립 노드도 제 성분(크기 1)을 가진다: 고아 목록이 그것으로 만들어진다."""
    order, adj = _undirected(nodes, edges)
    parent = {n: n for n in order}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in order:
        for b in adj[a]:
            ra, rb = find(a), find(b)
            if ra != rb:
                if rb < ra:
                    ra, rb = rb, ra
                parent[rb] = ra
    return _relabel({n: find(n) for n in order})


# ── Leiden (Traag, Waltman, van Eck 2019, arXiv 1810.08473) ─────────────────
# 품질 함수는 해상도 γ 의 모듈러리티다. 노드 v 를 공동체 D 로 옮기는 이득은
#     k_{v,D} - γ · k_v · K_D / (2m)
# 에 비례한다(k_{v,D}: v 와 D 사이 가중치, k_v: v 의 차수, K_D: D 의 차수 합, m: 전체
# 가중치). 집약 뒤에도 m 과 차수는 원래 그래프의 것을 쓴다 — 자기 루프(공동체 안쪽
# 가중치)는 옮김으로 바뀌지 않으므로 이득 식에서 빠진다.

class _Level:
    """한 집약 단계의 그래프: 노드 차수(원래 그래프 기준)와 자기 루프 없는 인접."""

    def __init__(self, nodes: list, adj: dict, deg: dict):
        self.nodes = nodes
        self.adj = adj
        self.deg = deg


def _gain(k_v_to: float, k_v: float, k_tot: float, two_m: float, gamma: float) -> float:
    return k_v_to - gamma * k_v * k_tot / two_m


def _move_nodes(g: _Level, comm: dict, two_m: float, gamma: float, rng: random.Random) -> bool:
    """빠른 지역 이동. 옮긴 것이 하나라도 있으면 True."""
    tot: dict = defaultdict(float)
    for v in g.nodes:
        tot[comm[v]] += g.deg[v]
    queue = list(g.nodes)
    rng.shuffle(queue)
    queued = set(queue)
    head = 0
    moved = False
    # 번호는 정수로만 쓴다. 부르는 쪽(`leiden`)이 단계마다 0 부터 다시 매겨 넘기므로
    # 새 번호는 지금 가장 큰 번호 다음이면 겹치지 않는다.
    fresh = max(comm.values(), default=-1) + 1
    while head < len(queue):
        v = queue[head]
        head += 1
        queued.discard(v)
        cur = comm[v]
        links: dict = defaultdict(float)
        for u, w in g.adj[v].items():
            links[comm[u]] += w
        kv = g.deg[v]
        tot[cur] -= kv
        best, best_gain = cur, _gain(links.get(cur, 0.0), kv, tot[cur], two_m, gamma)
        # 빈 공동체(자기 혼자)의 이득은 0 이다. 지금 자리보다 나쁠 때만 나간다.
        for c in sorted(links, key=str):
            if c == cur:
                continue
            gn = _gain(links[c], kv, tot[c], two_m, gamma)
            if gn > best_gain + 1e-12:
                best, best_gain = c, gn
        if best_gain < -1e-12:
            best, best_gain = fresh, 0.0
            fresh += 1
        tot[best] += kv
        if best != cur:
            comm[v] = best
            moved = True
            for u in g.adj[v]:
                if comm[u] != best and u not in queued:
                    queue.append(u)
                    queued.add(u)
    return moved


def _refine(g: _Level, comm: dict, two_m: float, gamma: float, rng: random.Random) -> dict:
    """정제: 각 공동체 안에서 단일 노드부터 **잘 이어진** 부분 집합으로만 합친다.
    이 단계가 Louvain 에 없는 것이고, 끊긴 공동체가 나오지 않는 보증이 여기서 온다."""
    members: dict = defaultdict(list)
    for v in g.nodes:
        members[comm[v]].append(v)
    ref = {v: v for v in g.nodes}          # 처음엔 전부 단일
    ref_tot = {v: g.deg[v] for v in g.nodes}
    for c in sorted(members, key=str):
        vs = members[c]
        in_c = set(vs)
        k_c = sum(g.deg[v] for v in vs)
        # v 와 C∖v 사이 가중치 — 잘 이어진 노드만 옮긴다.
        to_rest = {v: sum(w for u, w in g.adj[v].items() if u in in_c) for v in vs}
        order = sorted(vs)
        rng.shuffle(order)
        # 부분 집합 T 와 C∖T 사이 가중치(합쳐지는 대로 갱신한다).
        sub_out = dict(to_rest)
        for v in order:
            kv = g.deg[v]
            if ref[v] != v or ref_tot[v] != kv:
                continue                     # 이미 다른 부분 집합에 들어갔다(단일이 아니다)
            if to_rest[v] < gamma * kv * (k_c - kv) / two_m:
                continue                     # v 가 C 안에서 잘 이어지지 않았다
            links: dict = defaultdict(float)
            for u, w in g.adj[v].items():
                if u in in_c and ref[u] != v:
                    links[ref[u]] += w
            best, best_gain = None, 0.0
            for t in sorted(links, key=str):
                kt = ref_tot[t]
                if sub_out[t] < gamma * kt * (k_c - kt) / two_m:
                    continue                 # T 가 C 안에서 잘 이어지지 않았다
                gn = _gain(links[t], kv, kt, two_m, gamma)
                if gn > best_gain + 1e-12:
                    best, best_gain = t, gn
            if best is None:
                continue
            ref[v] = best
            ref_tot[best] += kv
            ref_tot[v] = 0.0
            # 합친 뒤 T 와 C∖T 사이: 둘의 바깥 합에서 서로 사이의 가중치를 두 번 뺀다.
            sub_out[best] = sub_out[best] + to_rest[v] - 2 * links[best]
    # 부분 집합 대표를 끝까지 따라간다(대표 자신은 자기를 가리킨다).
    return {v: ref[v] for v in g.nodes}


def _aggregate(g: _Level, ref: dict) -> _Level:
    nodes = sorted(set(ref.values()), key=str)
    deg: dict = defaultdict(float)
    for v in g.nodes:
        deg[ref[v]] += g.deg[v]
    adj: dict = {n: defaultdict(float) for n in nodes}
    for v in g.nodes:
        rv = ref[v]
        for u, w in g.adj[v].items():
            ru = ref[u]
            if ru != rv:
                adj[rv][ru] += w
    return _Level(nodes, {n: dict(sorted(a.items(), key=lambda kv: str(kv[0])))
                          for n, a in adj.items()}, dict(deg))


def leiden(nodes, edges, *, resolution: float = DEFAULT_RESOLUTION,
           seed: int = DEFAULT_SEED) -> dict[str, int]:
    """Leiden 커뮤니티. 반환 `{id: 커뮤니티 번호}` — 번호는 큰 커뮤니티부터 0, 차수 0 인
    노드는 :data:`ISOLATED`. 엣지는 `(a, b)` 또는 `(a, b, weight)` 이고 방향은 무시한다.

    보증(논문 정리 2): 돌려준 커뮤니티는 전부 연결돼 있다. `tests/test_graph_algo.py` 가
    무작위 그래프 여럿에서 이것을 확인한다."""
    order, adj = _undirected(nodes, edges)
    deg = {n: sum(adj[n].values()) for n in order}
    two_m = sum(deg.values())
    if two_m <= 0:
        return {n: ISOLATED for n in order}
    rng = random.Random(seed)
    g = _Level(list(order), adj, deg)
    # 원래 노드 → 현재 단계 노드. 처음엔 자기 자신.
    where = {n: n for n in order}
    comm = {n: i for i, n in enumerate(order)}
    for _ in range(MAX_LEVELS):
        comm = _dense(comm)
        _move_nodes(g, comm, two_m, resolution, rng)
        if len(set(comm.values())) == len(g.nodes):
            break                            # 모든 공동체가 단일 노드다 — 더 합칠 것이 없다
        ref = _refine(g, comm, two_m, resolution, rng)
        agg = _aggregate(g, ref)
        # 집약 그래프의 첫 분할은 정제 전 분할(comm)을 따른다 — 논문의 핵심 절차.
        comm = {ref_v: comm[v] for v, ref_v in ref.items()}
        where = {n: ref[w] for n, w in where.items()}
        if len(agg.nodes) == len(g.nodes):
            g = agg
            break                            # 정제가 아무것도 합치지 않았다
        g = agg
    final = {n: comm[where[n]] for n in order}
    for n in order:
        if deg[n] == 0:
            final[n] = ISOLATED
    return _relabel(_split_disconnected(final, adj))


def _dense(comm: dict) -> dict:
    """공동체 번호를 0 부터 촘촘히 다시 매긴다(첫 등장 순서는 노드 순서를 따른다)."""
    ids: dict = {}
    out = {}
    for v in sorted(comm, key=str):
        out[v] = ids.setdefault(comm[v], len(ids))
    return out


def _split_disconnected(assign: dict, adj: dict) -> dict:
    """끊긴 공동체를 연결 성분으로 나눈다. 정제가 끝까지 돌면 일어나지 않는 일이지만,
    정제가 아무것도 합치지 못해 멈춘 단계(`leiden` 의 두 번째 break)에서 지역 이동의
    분할이 그대로 나갈 수 있다. 끊긴 공동체는 답변 재료로 나가면 그대로 보이는 결함이라
    여기서 한 번 더 막는다."""
    out = dict(assign)
    by: dict = defaultdict(list)
    for n, c in assign.items():
        if c != ISOLATED:
            by[c].append(n)
    for c, ms in by.items():
        inside = set(ms)
        seen: set = set()
        part = 0
        for start in sorted(ms):
            if start in seen:
                continue
            stack = [start]
            seen.add(start)
            while stack:
                x = stack.pop()
                out[x] = (c, part)
                for u in adj.get(x, {}):
                    if u in inside and u not in seen:
                        seen.add(u)
                        stack.append(u)
            part += 1
    return out


def modularity(nodes, edges, assign: dict[str, int], *,
               resolution: float = DEFAULT_RESOLUTION) -> float:
    """분할의 모듈러리티. 테스트와 품질 표시가 쓴다. 고립 노드는 기여가 0 이다."""
    order, adj = _undirected(nodes, edges)
    deg = {n: sum(adj[n].values()) for n in order}
    two_m = sum(deg.values())
    if two_m <= 0:
        return 0.0
    inside: dict = defaultdict(float)
    tot: dict = defaultdict(float)
    for n in order:
        c = assign.get(n, ISOLATED)
        tot[c] += deg[n]
        for u, w in adj[n].items():
            if assign.get(u, ISOLATED) == c:
                inside[c] += w
    q = 0.0
    for c in tot:
        if c == ISOLATED:
            continue
        q += inside[c] / two_m - resolution * (tot[c] / two_m) ** 2
    return q
