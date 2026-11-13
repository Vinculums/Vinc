# -*- coding: utf-8 -*-
"""지도 배치 기하: 노드와 엣지를 받아 3D 좌표(x·y·z)와 평면 좌표(x2·y2)를 붙이는 순수 함수들.

`graph()` 와 `graph_preview()` 가 `layout_positions` 를 부르고, 도메인 경로 보조(`_domain_path`·
`_shared_prefix`)는 Bridge 도메인 추정과 도메인 뿌리 진단도 읽는다. 스토어를 열지 않고 난수·시간을
읽지 않으므로 같은 입력은 언제나 같은 좌표가 된다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.X` 와 `from api.query import X` 는 그대로다.
두 이름은 같은 객체다. 여기 함수가 부르는 이름은 이 모듈에서 찾으므로, 배치 안의 호출을 바꾸려면
`api.query` 가 아니라 이 모듈의 이름을 패치한다.

이 모듈은 `api.query` 를 import 하지 않는다. 방향 관계 표(`_DIRECTED_RELS`·`_REVERSED_RELS`)는
`api/query_base.py` 에서 읽는다(`tests/core/test_graph_layout.py` 가 둘을 본다).
"""
from __future__ import annotations

from .query_base import _DIRECTED_RELS, _REVERSED_RELS


# 사용자 선택 프리셋 정본 — **2종**(D34).
#   hub  : 연결이 많을수록 중심, 적을수록 바깥(동심 배치). "무엇이 중심인가".
#   rank : 전역 위상 순서를 x 에 인코딩. "무엇이 무엇보다 먼저인가".
# radial·circle·tree·organic·dendro·shell 은 이 둘의 변주라 프리셋이 아니다.
# 그 이름은 `_LAYOUT_ALIAS` 가 hub 로 접어 받는다(저장값·딥링크 무해).
LAYOUTS = ("hub", "rank")
_LAYOUT_ALIAS = {"radial": "hub", "circle": "hub", "tree": "hub",
                 "organic": "hub", "dendro": "hub", "shell": "hub"}


def _global_rank(nodes: list[dict], directed: dict[str, set], math) -> None:
    """Rank(D50 + **D74 위계**) — 전역 위상 순서를 좌표에 인코딩한다.
    x = 랭크(선행 → 후행) · y = 열 안 정렬 · z = 도메인 슬랩(3D 전용).

    **위계를 어떻게 실었는가**:
    도메인으로 **클러스터를 만들지는 않는다** — 원 주석의 사유("계보는 도메인을
    가로지르므로 클러스터 안에서 랭크를 매기면 흐름이 끊긴다")는 지금도 맞다.
    대신 층(x)은 손대지 않고 **열 안 순서**만 도메인으로 묶는다. 층 읽기는 그대로
    두고 같은 도메인이 열을 가로질러 비슷한 높이에 서므로, 도메인 안 계보는 짧은
    가로선이 되고 도메인을 넘는 계보는 **사선으로 드러난다** — 그게 이 프리셋에서
    보고 싶은 것이다. 3D 는 거기에 z 를 더해 도메인마다 자기 깊이의 판을 준다
    (hub 의 원뿔이 여기서는 나란한 슬랩이 된다). 2D 는 z 를 안 읽으므로 같은
    그림에서 깊이만 빠진다.

    **왜 필요한가**: 2D 관점의 좌표는 여태 아무 의미도 인코딩하지 않았다(도메인을
    원 둘레에 배치했을 뿐). 흐름 관점은 좌표에 의미가 있지만 방향 엣지 3종만 그려
    나머지 관계가 안 보인다. 이 프리셋은 둘을 합친다 — 좌표는 흐름의 뜻을 갖고,
    렌더러(sigma)는 모든 엣지를 그린다.

    층화는 최장경로(Kahn 위상정렬). 사이클에 갇혀 큐에 못 든 노드는 확정된 상류의
    다음 층으로 민다(첫 방문 승리) — `mountFlow` 의 층화와 같은 규칙이라 흐름 뷰와
    2D 가 같은 랭크를 보여준다. 순수 함수(난수·시간 무의존, D17)."""
    ids = [n["id"] for n in nodes]
    inset = set(ids)
    out: dict[str, list[str]] = {}
    indeg = {i: 0 for i in ids}
    for s, targets in directed.items():
        if s not in inset:
            continue
        for t in sorted(targets):
            if t in inset:
                out.setdefault(s, []).append(t)
                indeg[t] += 1
    layer = {i: 0 for i in ids}
    queue = [i for i in ids if indeg[i] == 0]
    settled = set(queue)
    qi = 0
    while qi < len(queue):
        u = queue[qi]; qi += 1
        for v in out.get(u, ()):
            layer[v] = max(layer[v], layer[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                settled.add(v); queue.append(v)
    for i in ids:                     # 사이클 잔여 — 확정 상류 다음 층으로
        if i in settled:
            continue
        mx = -1
        for s, targets in directed.items():
            if i in targets and s in settled:
                mx = max(mx, layer[s])
        layer[i] = mx + 1
        settled.add(i)
    rows: dict[int, list[dict]] = {}
    for n in nodes:
        rows.setdefault(layer[n["id"]], []).append(n)
    cols = max(len(rows), 1)
    # 종횡비 — 열은 고정 간격으로 넓게, 행은 **가장 붐비는 열이 정해진 높이에 맞게**
    # 적응한다. 뿌리 열은 대개 수십 개라, 행 간격을 고정하면 세로로만 길어져 캔버스가
    # 세로 스크롤이 된다. 폭 = 0.5×열, 높이 = 3.0 고정 → 열이 늘수록 가로로 눕는다.
    tallest = max((len(r) for r in rows.values()), default=1)
    y_step = 3.0 / max(tallest - 1, 1)
    band = _domain_order(nodes)
    # 도메인 슬랩 간격 — 판이 하나뿐이면 0(평면으로 남는다), 여럿이면 [-0.6, 0.6].
    slabs = max(len(set(band.values())), 1)
    z_step = 0.0 if slabs < 2 else 1.2 / (slabs - 1)
    for lv, row in rows.items():
        # 열 안 정렬 = (도메인 띠, pinned, degree, id). 도메인이 하나뿐이면 첫 키가
        # 상수라 정렬은 예전 그대로(허브 우선, D32)로 접힌다 — 하위호환.
        row.sort(key=lambda n: (band.get(n.get("domain") or "", 0),
                                -int(bool(n.get("pinned"))),
                                -int(n.get("degree") or 0), str(n.get("id"))))
        k = len(row)
        x = (lv - (cols - 1) / 2) * 0.5
        for j, n in enumerate(row):
            n["x"] = round(x, 6)
            # ⚠ y 는 **위로 갈수록 크다**. 두 렌더러가 같은 규약이다:
            # three.js 는 +y 가 위, sigma 는 클립 y 를 `(1 - y) * h / 2` 로 뒤집어
            # 역시 +y 가 위다. 그래서 `j - (k-1)/2` 로 두면 1순위(j=0)가 **맨 아래**로
            # 가서 "허브 우선"이 화면에서 뒤집혀 읽힌다(같은 열에서 degree
            # 16 이 최하단, 14 가 최상단). 부호를 뒤집어 1순위를 맨 위에 세운다.
            n["y"] = round(((k - 1) / 2 - j) * y_step, 6)
            n["z"] = round((band.get(n.get("domain") or "", 0)
                            - (slabs - 1) / 2) * z_step, 6)
            # 평면 정본은 x·y 그대로 — rank 는 애초에 평면 문법이라 접을 것이 없다.
            n["x2"], n["y2"] = n["x"], n["y"]


def layout_positions(nodes: list[dict], edges: list[dict],
                     layout: str = "radial", topic_marks: bool = False,
                     cone: float = 1.0) -> list[dict]:
    """결정론 배치(D17 + D34 프리셋 + **D74 위계 폭발**) — 순수 함수. 난수·시간
    무의존이라 같은 그래프는 언제 그려도 같은 좌표다(플랜 순수성 계약과 동형).

    **좌표를 두 벌 낸다**(D74):
      ``x``/``y``/``z`` = 3D 정본 · ``x2``/``y2`` = 2D 정본.
    2D 는 3D 를 **투영한 것이 아니다**. z 를 눌러 평탄화하면 z 로만 갈라져 있던
    노드들이 평면에서 같은 점이 되는데(원뿔 배치에서는 흔하다), 사후 보정으로
    떼어내면 위치의 뜻이 흐려진다. 그래서 같은 트리를 **평면 문법으로 다시 배치**
    한다(원뿔 → 부채꼴). 겹침은 고쳐지는 것이 아니라 **생기지 않는다** —
    부채꼴은 서로 겹치지 않고 부채꼴 안에서는 각도 슬롯이 전부 다르다.
    (그래도 exact 중복이 남을 수 있는 경계는 `_dedupe_planar` 가 마지막에 막는다.)

    내부 정렬은 (pinned DESC, degree DESC, id) — pin 이 허브 승격 1순위(D32).
    ⚠ Concept 에는 `pinned` 열이 없어(embedded_schema) 개념 축에서는 무동작이다.

    ``topic_marks``: **주제 자신을 마크로 낸다**(hub 전용). 기본은 꺼짐
    이고 그것이 요지다 — 이 함수의 계약은 "준 노드를 배치한다"이지 "노드를 만든다"
    가 아니라서, 켜는 것은 지도 payload 를 조립하는 호출부의 결정이어야 한다.
    (테스트가 이 경계를 잡아 줬다: 기본으로 켰더니 배치 계약을 검사하는 네 건이
    입력보다 많은 노드를 받고 깨졌다.)"""
    import math
    GOLDEN = 2.399963229728653
    layout = _LAYOUT_ALIAS.get(layout, layout)
    if layout not in LAYOUTS:
        layout = "hub"
    adj: dict[str, set] = {}
    directed: dict[str, set] = {}
    #: Bridge 좌석의 재료(item 12) — CONNECTS 로 닿는 반대편. 방향은 늘
    #: Bridge→Concept 이지만 양쪽에 실어 둔다(어느 투영으로 와도 같은 답).
    connects: dict[str, set] = {}
    for e in edges or []:
        s, t = e.get("source"), e.get("target")
        if not s or not t:
            continue
        adj.setdefault(s, set()).add(t)
        adj.setdefault(t, set()).add(s)
        rel = e.get("type") or ""
        if rel == "CONNECTS":
            connects.setdefault(s, set()).add(t)
            connects.setdefault(t, set()).add(s)
        if rel in _DIRECTED_RELS:
            # 상류 → 하류로 정규화한다(`query_base._REVERSED_RELS` 주석 참조).
            if rel in _REVERSED_RELS:
                directed.setdefault(t, set()).add(s)
            else:
                directed.setdefault(s, set()).add(t)
    if layout == "rank":
        # 순서 프리셋에는 주제 마크를 안 낸다. rank 의 좌표는 "무엇이 무엇보다
        # 먼저인가"를 말하는데 주제에는 그 자리가 없다 — 억지로 앉히면 없는 뜻을
        # 지어내는 것이 된다. 담는 관계는 hub 가 답한다.
        _global_rank(nodes, directed, math)
    else:
        # 원뿔 반각 = 기본값 × 사용자 배율. 상한이 π/2 인 이유: 반각이 90°를 넘으면
        # 원뿔이 부모의 **뒤쪽**까지 열려 자식이 부모보다 중심에 가까워진다. 그러면
        # "이 방향으로 계속 뻗어 나간다"는 이 배치의 문장 자체가 깨진다.
        cone0 = min(_CONE0 * max(_CONE_MIN, min(_CONE_MAX, cone)), math.pi / 2)
        _global_hub(nodes, math, GOLDEN, topic_marks, cone0,
                    connects=connects, adj=adj)
    _dedupe_planar(nodes)
    return nodes


# ── 도메인 위계(D74) ──────────────────────────────────────────────────────────
# `domain` 은 문자열이지만 **경로**다: "vinc/architecture" 는 vinc 아래 architecture.
# 여태 코드 어디에도 이 문자열을 가르는 곳이 없어 도메인이 전부 평평한 이름이었고,
# 그래서 "Vinc 아래 design" 과 "다른 프로젝트의 design" 이 같은 칸을 썼다.
# 배치는 이 경로를 읽는 **첫 소비처**다(트리·범례·필터는 이 함수를 재사용한다).
def _domain_path(dom: str) -> tuple[str, ...]:
    """도메인 문자열 → 경로 조각. 빈 값·앞뒤 슬래시·중복 슬래시를 모두 흡수한다."""
    return tuple(p for p in (dom or "").split("/") if p)


def _common_prefix(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    """모든 도메인이 공유하는 접두 = 이 그래프의 **뿌리**.

    전부 `vinc/*` 면 뿌리는 `vinc` 이고 가지는 architecture·design·… 이 된다.
    접두를 안 벗기면 뿌리에 자식이 하나뿐이라 "한 점에서 한 점으로" 가는 헛가지가
    생긴다(폭발이 아니라 막대). 도메인이 없는 노드는 이 계산에서 빼고(그것들은
    어차피 뿌리에 직접 붙는다) 남은 것이 없으면 접두도 없다."""
    real = [p for p in paths if p]
    if not real:
        return ()
    out = list(_shared_prefix(paths))
    # 접두가 경로 전체와 같아지면(도메인이 하나뿐) 마지막 조각은 가지로 남긴다 —
    # 안 그러면 가지가 0개가 되어 전부 뿌리에 뭉친다.
    if len(out) == min(len(p) for p in real):
        out = out[:-1]
    return tuple(out)


def _shared_prefix(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    """경로들이 공유하는 접두 = **가장 깊은 공통 조상**, 손대지 않은 그대로.

    `_common_prefix` 와 갈라 둔 이유: 저쪽은 배치용이라 "접두가 경로 전체와 같으면
    마지막 조각을 되돌려준다"는 가지 보존 규칙이 붙는다. 조상을 묻는 쪽
    (`_derive_bridge_domains`)에는 그 규칙이 틀린 답을 준다 — `vinc/security` 하나만
    있으면 조상은 `vinc/security` 자신이지 `vinc` 가 아니다.

    빈 경로(도메인 없는 노드)는 계산에서 뺀다 — `_common_prefix` 와 같은 규약이다."""
    real = [p for p in paths if p]
    if not real:
        return ()
    out: list[str] = []
    for i in range(min(len(p) for p in real)):
        seg = real[0][i]
        if not all(p[i] == seg for p in real):
            break
        out.append(seg)
    return tuple(out)


class _Branch:
    """도메인 경로 트리의 마디. 자식(하위 도메인) + 자기 몫의 노드를 든다."""
    __slots__ = ("seg", "kids", "items", "weight")

    def __init__(self, seg: str):
        self.seg = seg
        self.kids: dict[str, "_Branch"] = {}
        self.items: list[dict] = []
        self.weight = 0

    def child(self, seg: str) -> "_Branch":
        b = self.kids.get(seg)
        if b is None:
            b = self.kids[seg] = _Branch(seg)
        return b


def _build_tree(nodes: list[dict]) -> _Branch:
    """노드들을 도메인 경로 트리에 담는다. 가지 무게 = 그 아래 노드 총수."""
    paths = [_domain_path(n.get("domain") or "") for n in nodes]
    base = _common_prefix(paths)
    root = _Branch("")
    for n, p in zip(nodes, paths):
        cur = root
        for seg in p[len(base):]:
            cur = cur.child(seg)
        cur.items.append(n)

    def weigh(b: _Branch) -> int:
        b.weight = len(b.items) + sum(weigh(k) for k in b.kids.values())
        return b.weight
    weigh(root)
    return root


def _ordered_children(b: _Branch) -> list:
    """가지 안 순서 — **가지가 이름순으로 먼저, 그다음 연결 많은 개념**.

    앞에 오는 것이 원뿔/부채꼴에서 축에 가까운 자리를 받는다. 개념은 degree
    내림차순이라 가지 안에서 "중심순"이 그대로 산다(D32).
    반환 원소는 (weight, kind, key, payload) — kind 로 재귀/배치를 가른다."""
    #: 가지 순서는 **이름**이다. 무게로 세우면 노드 하나가 들어와 순위가 뒤집힐 때
    #: 그 아래 전부가 자리를 옮긴다 — 이것 하나가 순번 띠를 통째로 무효화한다.
    subs = sorted(b.kids.values(), key=lambda k: k.seg)
    items = sorted(b.items, key=lambda n: (-int(bool(n.get("pinned"))),
                                           -int(n.get("degree") or 0), str(n.get("id"))))
    return ([(k.weight, "branch", k.seg, k) for k in subs]
            + [(1, "node", str(n.get("id")), n) for n in items])


def _domain_order(nodes: list[dict]) -> dict[str, int]:
    """도메인 문자열 → 띠 번호. **hub 의 가지 순서와 같은 순서**다.

    두 프리셋이 도메인을 다른 순서로 세우면 관점을 바꿀 때마다 "무엇이 먼저인가"가
    달라 보인다. 순서의 원천을 트리 한 곳에 두고 둘 다 여기서 읽는다."""
    root = _build_tree(nodes)
    order: dict[str, int] = {}

    def walk(b: _Branch) -> None:
        for _w, kind, _key, payload in _ordered_children(b):
            if kind == "branch":
                walk(payload)
            else:
                dom = payload.get("domain") or ""
                if dom not in order:
                    order[dom] = len(order)
    walk(root)
    return order


# 반경 계단 — 가지 깊이마다 한 겹씩 밖으로. 잎은 자기 가지 껍질 바깥에 흩어진다.
_R_BRANCH = (0.0, 0.52, 0.82, 1.02)     # 뿌리 · 도메인 · 하위 · 그 아래
_R_LEAF = 0.5                            # 잎이 가지 껍질 밖으로 더 나가는 폭
_CONE0 = 1.15                            # 뿌리에서 도메인으로 퍼지는 반각(rad)
#: 사용자 배율의 허용 범위. 1.0 이 기본 그림이다. 아래로는 0.3 —
#: 그보다 좁으면 가지가 한 줄기로 뭉쳐 위계가 안 읽힌다. 위로는 1.4 인데 실효 상한은
#: π/2 라(위 주석) 1.37 부터는 더 열려도 그대로다. 그 둘을 굳이 맞추지 않는 이유는
#: 상한의 근거가 다르기 때문이다: 하나는 취향의 끝이고 하나는 기하의 끝이다.
_CONE_MIN, _CONE_MAX = 0.3, 1.4


def _mean_angle(angles: list[float], math):
    """각의 평균은 **단위벡터의 평균**이다 — 0 과 2π 가 같은 각이라 산술평균은
    0.1 과 6.2 의 중간을 3.15(정반대)로 답한다. 합이 사실상 0 이면(정확히 갈라선
    이웃들) 선호가 없다는 뜻이라 None 을 돌려준다."""
    if not angles:
        return None
    cx = sum(math.cos(a) for a in angles)
    cy = sum(math.sin(a) for a in angles)
    if cx * cx + cy * cy < 1e-12:
        return None
    return math.atan2(cy, cx)


def _arc_gap(a: float, b: float, math) -> float:
    """두 각 사이의 원둘레 거리(0..π). 감싸기를 넘어서도 짧은 쪽을 잰다."""
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


def _cluster(items: list[dict], radius: float, math, center_w: float = 0.0,
             adj: dict | None = None) -> list:
    """무리 안 배치 — **규칙적 동심 고리 + 크기에 비례한 간격**. (dx, dy) 목록을 돌려준다.

    두 가지를 함께 푼다.
      ① 황금각 필로택시스는 고르지만 **규칙적으로 보이지 않는다**. 눈이
         잡을 격자가 없어 잘 흩어진 무작위로 읽힌다. 동심 고리는 고르기를 유지하면서
         "고리"와 "등각"이라는 두 규칙을 눈에 보이게 만든다.
      ② 등각만으로는 **큰 노드가 뭉쳐 보인다**. 마크 반지름은 √연결수에
         비례하는데(D32) 자리는 균등하니, 연결 많은 것끼리 이웃하면 그 둘만 붙는다.

    ②의 처방이 이 함수의 요지다. 절대 px 는 서버가 모르지만 **상대 비율**은 화면과
    같으므로(둘 다 √연결수), 이웃 두 마크의 평균 크기에 비례해 각 간격을 나눈다.
    큰 것 옆은 더 벌어지고 작은 것끼리는 촘촘해진다. 고리 반경도 같은 요구량으로
    정한다 — 큰 것이 많이 앉은 고리는 그만큼 바깥으로 밀린다.

    ⚠ **중심 자리는 비운다.** 거기에는 이미 그 주제의 마크가 서 있다(D86) — 잎을
    하나 앉히면 마크와 정확히 겹치고, 하필 연결이 가장 많은 것이 거기 가므로 가장
    큰 마크 둘이 포개진다. 무리가 커도 중심은 쓰지 않는다. 대신 마크의
    크기(`center_w`)를 첫 고리의 요구량에 더해, 마크가 클수록
    첫 고리가 바깥으로 밀리게 한다.

    ``adj``(item 13): 고리의 **슬롯 기하는 그대로 두고 누가 어디 앉는지만**
    고른다. degree 순으로 앉히다가, 이미 앉은 그래프 이웃이 있으면 그 이웃들의 평균
    각에 가장 가까운 **빈 슬롯**을 집는다. 이웃이 없으면 순서대로 앞 슬롯이므로
    **내부 엣지가 없는 무리는 좌표가 한 톨도 안 바뀐다**. 고리 반경과 크기 비례
    간격(위 ①·②)은 이 앞에서 이미 정해져 있어 손대지 않는다.

    순수 함수라 결정론이다(D17)."""
    n = len(items)
    if n == 0:
        return []
    if n == 1:
        return [(0.0, 0.0)]
    #: 화면 크기 축과 같은 것 — 반지름 ∝ √연결수. 1.0 은 고립 노드의 바닥 크기.
    w = [1.0 + math.sqrt(max(0.0, float(it.get("degree") or 0))) for it in items]
    out: list = [(0.0, 0.0)] * n
    rest = n
    rings = 1
    while 3 * rings * (rings + 1) < rest:
        rings += 1
    tot = rings * (rings + 1) // 2
    # 1단계 — 고리 멤버와 각 고리의 **요구량**(간격 합). 반경은 이걸로 정한다.
    plan, start, demand = [], 0, []
    for k in range(1, rings + 1):
        cnt = rest * (k * (k + 1) // 2) // tot - start
        if cnt <= 0:
            continue
        mem = list(range(start, start + cnt))
        gaps = [(w[mem[j]] + w[mem[(j + 1) % cnt]]) / 2 for j in range(cnt)]
        plan.append((mem, gaps))
        # 첫 고리만 마크 크기를 함께 진다(위 ⚠ 주석).
        demand.append((sum(gaps) or 1.0) + (1.0 + math.sqrt(max(0.0, center_w))
                                            if not plan[:-1] else 0.0))
        start += cnt
    # 2단계 — 고리 반경은 **두 요구 중 큰 쪽**이다.
    #   · 각(angular): 그 고리의 간격 합을 둘레로 담으려면 최소 이만큼 필요하다
    #   · 지름(radial): 앞 고리와 마크 크기만큼은 떨어져야 한다
    # 누적 비율만 쓰면 여기서 틀린다: 큰 것이 앉은 안쪽 고리가 짧아,
    # 가장 큰 마크들이 오히려 가장 좁게 붙는다. 그래서 묶는 것은 **고리 사이 거리**다.
    radii, prev_r, prev_w = [], 0.0, 1.0 + math.sqrt(max(0.0, center_w))
    for mem, gaps in plan:
        mw = max(w[m] for m in mem)
        angular = (sum(gaps) or 1.0) / (2 * math.pi)
        radial = prev_r + 0.55 * (prev_w + mw) / 2
        rr = max(angular, radial)
        radii.append(rr)
        prev_r, prev_w = rr, mw
    scale = radius / (radii[-1] or 1.0)
    idx = {str(it.get("id")): i for i, it in enumerate(items)}
    seated: dict[int, float] = {}          # 이미 앉은 항목 → 중심 기준 각
    for ring_i, ((mem, gaps), rr0) in enumerate(zip(plan, radii)):
        rr = rr0 * scale
        gsum = sum(gaps) or 1.0
        # 고리마다 반 칸 돌린다. 안 돌리면 모든 고리의 첫 자리가 θ=0 에 서서
        # 중심을 지나는 **한 줄**이 생긴다(한 덩어리의 마크 여럿이 같은 y 에 선다).
        off = math.pi / len(mem) if len(mem) > 1 else 0.0
        slots, acc = [], 0.0
        for g in gaps:
            slots.append(off * (ring_i % 2) + 2 * math.pi * acc / gsum)
            acc += g
        free = list(range(len(slots)))
        for m in mem:
            pick = 0                       # 이웃이 없으면 앞 슬롯 = 예전 그대로
            if adj:
                # 이웃 id 를 정렬해 읽는다 — adj 값은 set 이라 순회 순서가
                # 프로세스마다 다르고, 평균의 덧셈 순서까지 흔들리면 순수성이 깨진다.
                aim = _mean_angle(
                    [seated[idx[nb]] for nb in sorted(adj.get(str(items[m].get("id")), ()))
                     if nb in idx and idx[nb] in seated], math)
                if aim is not None:
                    pick = min(range(len(free)),
                               key=lambda k: (round(_arc_gap(slots[free[k]], aim, math), 9), k))
            th = slots[free.pop(pick)]
            seated[m] = th
            out[m] = (rr * math.cos(th), rr * math.sin(th))
    return out


def _shell(depth: int) -> float:
    if depth < len(_R_BRANCH):
        return _R_BRANCH[depth]
    return _R_BRANCH[-1] + 0.16 * (depth - len(_R_BRANCH) + 1)


def _global_hub(nodes: list[dict], math, GOLDEN, topic_marks: bool = False,
                cone0: float = _CONE0, connects: dict | None = None,
                adj: dict | None = None) -> None:
    """Hub — **중심에서 터져 나가는 위계**(D74, 구 동심 배치 개정).

    뿌리(이 그래프의 공통 도메인 = 지금은 `vinc`)가 원점이고, 도메인이 사방으로
    뻗고, 그 도메인이 향한 **그 방향으로** 하위 개념이 다시 퍼진다. 폭발의 잔해가
    한 방향으로 계속 날아가는 그림이다.

    degree 하나로 동심원을 그리면 위계가 사라진다. 도메인은 평평한 이름이 아니라
    `vinc/architecture` 처럼 경로다 — 좌표가 그 구조를 안 실으면
    "무엇이 무엇 아래인가"를 화면이 답하지 못한다. 중심성은 버리지 않는다: 가지
    **안에서** degree 순으로 축에 가까운 자리를 준다(D32 는 이제 가지 단위로 산다).

    3D: 자식 방향 = 부모 방향 주위 **원뿔** 안. 극각 θ ∝ √(형제 순번 비율)이고
    방위각은 황금각이라 어느 개수에서도 겹치지 않는다. 순번은 degree 내림차순이라
    연결 많은 것이 축 가까이 앉는다(D32).

    2D: 같은 트리를 **부채꼴**로 — 자식마다 같은 폭의 각도 구간을 나눠 갖고 그
    구간 안에서 다시 나눈다. 원뿔의 평면 대응물이라 3D 와 같은 이야기를 한다.
    투영이 아니므로 z 평탄화에 따른 겹침이 **원리적으로** 생기지 않는다.

    ⚠ 띠도 부채꼴도 **무게가 아니라 순번**으로 나눈다. 무게로 나누면 어디에 노드
    하나가 들어와도 형제 전부의 좌표가 바뀌어 지도가 매번 다시 깔린다 — 결정론이긴
    해도 삽입에 안정적이지 않다. 대가는 D74 의 무게 비례 인코딩이고(가지 크기는
    주제 마크 크기로 남는다), 얻는 것은 삽입이 그 가지 안에서 끝나는 것이다.
    근거·기각안: [[decision:the-hub-layout-gives-up-weight-proportional-spread-so-that-adding-a-node-moves-nothing]]
    실측: [[record:the-layout-becomes-insertion-stable-when-sibling-bands-come-from-rank-and-branches-are-ordered-by-name-2026-09-19]]

    Bridge 는 이 트리의 잎이 **아니다**(item 12) — 소속이 저작되지 않고 연결로
    정해지므로 끝점 가지들의 평균 방향에 따로 앉힌다(`seat_bridges`). 트리에서
    빼는 이유: 잎으로 두면 가지 무게에 셈해져 다른 노드 자리를 밀어 놓고 정작 그
    자리는 쓰지 않는다. ``connects`` 가 없으면(엣지 없는 호출) 예전 그대로다.

    순수 함수(난수·시간 무의존, D17)."""
    #: Bridge id → CONNECTS 반대편 중 **이 배치에 실제로 있는 것**. 비면 미분류라
    #: 보통 잎처럼 뿌리에 남는다(지어낼 방향이 없다).
    ends: dict[str, list[str]] = {}
    if connects:
        present = {n.get("id") for n in nodes}
        for n in nodes:
            if n.get("kind") != "bridge":
                continue
            arms = sorted(t for t in connects.get(n.get("id")) or () if t in present)
            if arms:
                ends[str(n.get("id"))] = arms
    tree_nodes = [n for n in nodes if str(n.get("id")) not in ends]
    root = _build_tree(tree_nodes)
    base = _common_prefix([_domain_path(n.get("domain") or "") for n in tree_nodes])
    #: 가지 기하 — 경로 → (3D 단위 방향, 껍질 반경, 2D 각). Bridge 좌석의 재료다.
    seats: dict[tuple, tuple] = {}
    #: 이번 배치에서 낼 주제 마크. 다 돌고 나서 한 번에 붙인다 — 도는 중에 넣으면
    #: 트리를 만든 그 목록이 순회 중에 자란다.
    marks: list[dict] = []

    def basis(d):
        """d 에 수직인 정규직교 두 축 — 원뿔 안 방위각을 세울 좌표계."""
        ax, ay, az = d
        # d 와 가장 안 나란한 축을 골라 외적 — 축이 나란하면 0 벡터가 나온다.
        hx, hy, hz = (1.0, 0.0, 0.0) if abs(ax) < 0.9 else (0.0, 1.0, 0.0)
        ux, uy, uz = ay * hz - az * hy, az * hx - ax * hz, ax * hy - ay * hx
        ul = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
        ux, uy, uz = ux / ul, uy / ul, uz / ul
        vx, vy, vz = ay * uz - az * uy, az * ux - ax * uz, ax * uy - ay * ux
        return (ux, uy, uz), (vx, vy, vz)

    def spray(d, theta: float, phi: float):
        """부모 방향 d 를 축으로 극각 θ · 방위각 φ 만큼 튼 단위 벡터."""
        (ux, uy, uz), (vx, vy, vz) = basis(d)
        ct, st = math.cos(theta), math.sin(theta)
        cp, sp = math.cos(phi), math.sin(phi)
        return (d[0] * ct + (ux * cp + vx * sp) * st,
                d[1] * ct + (uy * cp + vy * sp) * st,
                d[2] * ct + (uz * cp + vz * sp) * st)

    def sphere(mid: float, i: int):
        """무게 비례 피보나치 구면점 — 뿌리에서만 쓴다.

        뿌리의 자식(도메인)은 **사방으로** 나가야 폭발이 된다. 원뿔로 뿌리면 한쪽
        반구에만 몰려 "부채"가 되지 한 점에서 터진 그림이 아니다. z 를 무게 비례로
        띠 나누고 방위각을 황금각으로 돌리면 개수와 무관하게 고르게 덮인다."""
        z = 1.0 - 2.0 * mid
        rr = math.sqrt(max(0.0, 1.0 - z * z))
        ang = i * GOLDEN
        return (math.cos(ang) * rr, math.sin(ang) * rr, z)

    def place(b: _Branch, depth: int, d3, a0: float, a1: float, cone: float,
              path: tuple = ()) -> None:
        seats[path] = (d3, _shell(depth), (a0 + a1) / 2, a1 - a0)
        # 주제 자신을 **마크로 낸다**. 가지를 좌표 계산에만 쓰고 화면에 잎만 내면
        # "vinc 아래 design" 이라는 구조가 지도에서
        # 위치로만 암시되고 짚을 것이 없다. id 는 저장소의 진짜 주제 노드와 같은
        # `domain:<경로>` 라 클릭·추적이 그대로 해석된다(GM-12 · D16 의 L0→L1).
        # 반경은 이 가지 자신의 껍질이고 뿌리는 _shell(0)=0 이라 정확히 원점이다.
        if path and topic_marks:
            mark = {
                "id": "domain:" + "/".join(path),
                "label": path[-1],
                "domain": "/".join(path[:-1]),
                "kind": "domain",
                "degree": b.weight,      # 아래에 달린 총수 = 크기 인코딩(√연결수와 같은 축)
                "props": {}, "tags": [], "ts": "",
            }
            rb = _shell(depth)
            mark["x"] = round(d3[0] * rb, 6)
            mark["y"] = round(d3[1] * rb, 6)
            mark["z"] = round(d3[2] * rb, 6)
            mark["x2"] = round(math.cos((a0 + a1) / 2) * rb, 6)
            mark["y2"] = round(math.sin((a0 + a1) / 2) * rb, 6)
            marks.append(mark)
        kids = _ordered_children(b)
        r_here = _shell(depth)
        span = a1 - a0
        # 2D — 이 가지 자신의 자리. 잎은 **그 점 둘레에 뭉친다**(아래 주석).
        bx2 = math.cos((a0 + a1) / 2) * r_here
        by2 = math.sin((a0 + a1) / 2) * r_here
        # 이 가지의 잎을 **한 번에** 배치한다 — 간격이 이웃 크기에 달려 있어
        # (_cluster) 하나씩 독립으로는 정할 수 없다.
        leafnodes = [p2 for _w, k, _k2, p2 in kids if k == "node"]
        disc = min(_R_LEAF * 0.85, 0.28 * max(r_here, 0.3) * span)
        spots = _cluster(leafnodes, disc, math, float(b.weight), adj=adj)
        leaf_i = 0
        for i, (_w, kind, _key, payload) in enumerate(kids):
            mid = (i + 0.5) / len(kids)            # 순번 비율(0~1) — 띠·부채꼴·반경 램프
            # 3D — 뿌리는 구면 전체로, 그 아래는 부모 방향 주위 원뿔 안으로.
            # (원뿔에서는 순번이 앞쪽 = 축 근처일수록 θ 가 작다 = 연결 많은 것이 안쪽.)
            nd3 = (sphere(mid, i) if depth == 0
                   else spray(d3, cone * math.sqrt(mid), i * GOLDEN))
            # 2D — 부채꼴: 형제 수로 등분한다.
            b0 = a0 + span * (i / len(kids))
            b1 = a0 + span * ((i + 1) / len(kids))
            if kind == "branch":
                place(payload, depth + 1, nd3, b0, b1, cone * 0.58, path + (payload.seg,))
                continue
            n = payload
            # 잎 — 자기 가지 껍질 **바깥**으로. 순번이 뒤일수록 더 멀리(중심순 유지).
            rr = r_here + _R_LEAF * (0.30 + 0.70 * math.sqrt(mid))
            n["x"] = round(nd3[0] * rr, 6)
            n["y"] = round(nd3[1] * rr, 6)
            n["z"] = round(nd3[2] * rr, 6)
            # 2D 는 잎을 자기 가지 둘레에 **뭉친다**(3D 는 깊이가 이미 갈라 주므로
            # 안 건드린다). 배치는 황금각 필로택시스 + √ 반경 램프라 개수와 무관하게
            # 고르고 결정론이며, 중심순은 그대로다.
            # ⚠ 원반 크기는 **가지의 각도 폭에 맞춘다**(상수 금지) — 그리고 계수는
            # 넉넉해야 한다: 간신히 안 닿는 값은 화면에서 한 덩어리로 읽힌다.
            # 근거: [[record:planar-leaves-cluster-because-arms-read-as-a-vortex]].
            dx, dy = spots[leaf_i]
            leaf_i += 1
            n["x2"] = round(bx2 + dx, 6)
            n["y2"] = round(by2 + dy, 6)

    def seat_bridges() -> None:
        """Bridge 좌석(item 12) — 끝점이 **속한 가지**들의 평균 방향, 반경은 그
        가지 껍질들의 평균. `vinc/security` 와 `vinc/engineering` 을 잇는 브릿지는
        두 가지 사이를 가리키며 그 껍질 높이에 선다.

        같은 가지 짝을 잇는 브릿지가 여럿이면 그 좌석 둘레에서 `_cluster` 의 고리
        규칙으로 벌린다 — 겹친 둘은 화면에서 하나가 사라진 것과 같다. 짝 안 순서는
        잎과 같은 (degree 내림, id)라 결정론이다(D32).

        ⚠ 끝점이 **한 가지 안**에만 있는 브릿지는 좌석이 그 가지의 주제 마크와
        같은 점이 된다(마크도 `d3 × _shell(depth)` 에 선다). 평면 쪽은
        `_dedupe_planar` 가 마지막에 밀고, 3D 는 그대로 겹친다 — 계획이 정한 반경을
        지키는 편을 택했다. 근거: [[record:bridge-layout-clump-2026-08-22]].

        입력이 그래프뿐이라 순수 함수다(D17)."""
        if not ends:
            return
        dom_of = {str(n.get("id")): n.get("domain") or "" for n in nodes}

        def key_of(dom: str) -> tuple:
            """도메인 → 실재하는 가지 경로. 트리에 없는 깊이는 위로 접어 올린다."""
            p = _domain_path(dom)
            while p and p not in seats:
                p = p[:-1]
            return p if p in seats else base

        groups: dict[tuple, list[dict]] = {}
        for n in nodes:
            arms = ends.get(str(n.get("id")))
            if arms:
                groups.setdefault(
                    tuple(sorted({key_of(dom_of.get(t, "")) for t in arms})), []).append(n)
        for k in sorted(groups):
            grp = sorted(groups[k], key=lambda n: (-int(n.get("degree") or 0),
                                                   str(n.get("id"))))
            arms3 = [seats[p] for p in k]
            sx, sy, sz = (sum(s[0][i] for s in arms3) for i in range(3))
            L = math.sqrt(sx * sx + sy * sy + sz * sz)
            # 정반대로 갈라진 가지들은 합이 0 이다 — 그런 짝에는 '사이'라는 방향이
            # 실제로 없으므로 첫 가지 방향을 쓴다(결정론이 목적).
            d3 = (sx / L, sy / L, sz / L) if L > 1e-9 else arms3[0][0]
            rr = sum(s[1] for s in arms3) / len(arms3)
            cx = sum(math.cos(s[2]) for s in arms3)
            cy = sum(math.sin(s[2]) for s in arms3)
            # 2D 각도 평균도 **단위벡터의 평균**이다(0 과 2π 가 같은 각이라 산술평균은 틀린다).
            ang = math.atan2(cy, cx) if cx * cx + cy * cy > 1e-18 else arms3[0][2]
            (ux, uy, uz), (vx, vy, vz) = basis(d3)
            # 벌림 폭은 **잎 원반과 같은 규칙**이다(`place` 의 disc) — 반경은 이
            # 좌석의 것을, 각도 폭은 끝점 가지들의 평균을 쓴다. 상한을 그대로
            # 쓰면(0.85) 가지 껍질만 한 폭이 되어 브릿지가 가지 밖으로 튄다.
            disc = min(_R_LEAF * 0.85,
                       0.28 * max(rr, 0.3) * (sum(s[3] for s in arms3) / len(arms3)))
            for n, (ox, oy) in zip(grp, _cluster(grp, disc, math)):
                n["x"] = round(d3[0] * rr + ux * ox + vx * oy, 6)
                n["y"] = round(d3[1] * rr + uy * ox + vy * oy, 6)
                n["z"] = round(d3[2] * rr + uz * ox + vz * oy, 6)
                n["x2"] = round(math.cos(ang) * rr + ox, 6)
                n["y2"] = round(math.sin(ang) * rr + oy, 6)

    # 뿌리의 3D 기준 방향은 +z 로 잡고 첫 원뿔을 거의 반구까지 연다(_CONE0).
    # 2D 는 뿌리에서 한 바퀴(2π)를 통째로 나눠 준다.
    #
    # 뿌리의 경로는 벗겨 낸 **공통 접두**다(_common_prefix). 전부 `vinc/*` 면 `vinc`
    # 하나가 원점에 서고, 주제가 둘 이상 갈리면(예: `vinc/*` 와 `neo4j/*`) 접두가
    # 비어 뿌리 마크가 없다 — 이름 붙일 하나의 뿌리가 실제로 없기 때문이다.
    place(root, 0, (0.0, 0.0, 1.0), 0.0, 2 * math.pi, cone0, base)
    seat_bridges()           # 가지 기하(seats)가 다 찬 뒤라야 평균을 낼 수 있다
    nodes.extend(marks)      # 배치가 끝난 뒤 한 번에(marks 선언부 주석)


def _dedupe_planar(nodes: list[dict]) -> None:
    """평면 좌표의 **exact 중복**을 마지막에 막는다(D74 요구: 시스템이 회피할 것).

    부채꼴 배치는 원리적으로 겹치지 않지만, 경계 조건이 남는다: 잎이 하나뿐인
    가지가 여럿이면 각도 구간이 달라도 반경 램프가 같아 좌표가 근접하고, rank 처럼
    격자에 앉히는 프리셋은 애초에 같은 칸이 나올 수 있다. 여기서는 **똑같은 점**만
    본다 — 가까운 것은 정보(밀집)이지만 똑같은 것은 노드 하나가 사라진 것이다.

    떼어내기는 결정론이다: 같은 좌표를 받은 것들을 id 순으로 세우고 황금각 사다리로
    미세하게 민다(원점이면 반경으로, 아니면 그 자리의 접선 방향으로). 크기가 아주
    작아 배치의 뜻을 바꾸지 않는다."""
    import math
    GOLDEN = 2.399963229728653
    seen: dict[tuple, list[dict]] = {}
    for n in nodes:
        x = n.get("x2", n.get("x", 0.0)) or 0.0
        y = n.get("y2", n.get("y", 0.0)) or 0.0
        seen.setdefault((round(x, 6), round(y, 6)), []).append(n)
    for (x, y), group in seen.items():
        if len(group) < 2:
            continue
        for k, n in enumerate(sorted(group, key=lambda m: str(m.get("id")))[1:]):
            eps = 0.012 * (1 + k // 8)
            ang = (k + 1) * GOLDEN
            n["x2"] = round(x + math.cos(ang) * eps, 6)
            n["y2"] = round(y + math.sin(ang) * eps, 6)
