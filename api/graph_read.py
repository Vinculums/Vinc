# -*- coding: utf-8 -*-
"""지도 읽기: View L1(소유자 전체·주제 렌즈)와 L2(seed ego) 그래프, 미리보기, 주제 사이 결합과 L0 수,
원장 계획. `graph()` 가 계획을 실행하고 셰이핑·배치·순위를 붙여 `vinc_graph` 와 화면이 받는 답을 만든다.

순서: 계획 함수(`graph_plan`·`subgraph_ids_plan`·축 계획들·`seed_lookup_plan`)가 Cypher 를 만들고,
`shape_graph` 가 행을 노드·엣지로 접고, `graph()` 가 `graph_layout`(좌표)과 `graph_rank`(ppr·상금
선택)를 부른다. 원장 계획 셋은 `api/query.py` 의 원장 분류(`_ledger_classify`)가 읽는다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.X` 와 `from api.query import X` 는 그대로다.
두 이름은 같은 객체다. 여기 함수가 부르는 이름(`execute`·`_order_graph_result`·계획 함수 등)은 이
모듈에서 찾으므로, `graph()` 안의 호출을 바꾸려면 `api.query` 가 아니라 이 모듈의 이름을 패치한다.

이 모듈은 `api.query` 를 import 하지 않는다. 바닥은 `api/query_base.py`, 좌표는 `api/graph_layout.py`,
순위는 `api/graph_rank.py` 에서 읽는다(`tests/core/test_graph_read.py` 가 둘을 본다).
"""
from __future__ import annotations

import json

from store import pkns
from store.embedded_schema import wiki_pairs_open
from store.querybase import _validity_cols, domain_subtree_where

from .graph_layout import _domain_path, _shared_prefix, layout_positions
from .graph_rank import PRIZE_REACH_CAP, _prize_pick, attach_ppr
from .query_base import (
    BASIS_CONTENT,
    BASIS_RECORDED,
    QueryPlan,
    _CONCEPT_READ_PATTERN,
    _GRAPH_READ_PAIRS,
    _SEMANTIC_PK,
    _WIKI_READ_PAIRS,
    _bridge_owner_pred,
    _props_to_obj,
    _seed_table,
    _topic_description_mirrors_by_domain,
    _track_cols,
    execute,
    near_nodes,
    normalize_domains,
)


def domains_plan(*, owner: str | None = None) -> QueryPlan:
    """Discover the learning domains present in the store — enumerated from the
    data, never a fixed list. Domains are whatever the learner chose to study; you
    find out *what* someone has learned by asking the graph, not a config enum.
    Optionally scoped to one ``owner``. Returns ``{domain, episodes}`` rows.

    Episode 전용 — View L0(:func:`view_l0`)가 Concept 축과 별도로 이 플랜을 그대로
    재사용하므로(§ concept_domains_plan 병행) 여기서는 손대지 않는다. 도메인 목록
    화면의 Episode 편향 해소는 :func:`domain_counts_plan`/:func:`domains` 가 맡는다."""
    where = "WHERE e.owner_id = $owner AND e.domain IS NOT NULL" if owner else "WHERE e.domain IS NOT NULL"
    params: dict = {"owner": owner} if owner else {}
    cypher = (
        f"MATCH (e:Episode) {where} "
        "RETURN e.domain AS domain, count(*) AS episodes "
        "ORDER BY episodes DESC"
    )
    return QueryPlan("domains", cypher, params, note="open-domain discovery (data-driven, not a fixed list)")


def graph_plan(*, owner: str, domain: str | None = None,
               domains: list[str] | None = None) -> QueryPlan:
    """The learning knowledge-graph for an owner: Concept nodes + their relations
    (a row per concept, plus a row per outgoing Concept→Concept edge). ``domain``
    (free/open) optionally narrows to one domain. Shaped into nodes/edges by
    :func:`shape_graph`."""
    where = ["c.owner_id = $owner"]
    params: dict = {"owner": owner}
    doms = normalize_domains(domain, domains)
    if doms:
        where.append(domain_subtree_where(["c.domain"], doms, params))
    clause = "WHERE " + " AND ".join(where)
    # 관계 타입은 표준 `type(r)` 로 쓴다 — 임베디드 어댑터가 `label(r)` 로 바꿔 보낸다
    # (`store.embedded_backend.to_engine_cypher`). ⚠ evidence/confidence 투영은 공통 CC 읽기 계약 안에서만 된다 —
    # CONNECTS/ATTACHES_TO/TYPED 가 후보인 축에 붙이면 Binder 오류다.
    # ⚠ owner 는 **먼 끝점에도** 건다 — 시작점만 스코프하면 남의 증거 문장이
    # 클라이언트까지 간다: [[record:an-unlabelled-hop-binds-only-tables-that-share-the-projected-columns]].
    cypher = (
        f"MATCH (c:Concept) {clause} "
        f"OPTIONAL MATCH (c)-[r:{_CONCEPT_READ_PATTERN}]->(d:Concept) WHERE d.owner_id = $owner "
        "RETURN c.concept_id AS id, c.title AS label, c.domain AS domain, "
        "c.props AS props, c.tags AS tags, "
        "coalesce(c.created, c.updated_at, '') AS ts, "
        # v7(D72) — 내용 시점을 **별도 열로** 나른다(ts 에 접지 않는다). 클라이언트가
        # 둘 다 받아 두면 축 전환에 재조회가 필요 없다. Concept 만 이 열을 가지므로
        # 다른 라벨의 플랜에 그대로 옮겨 붙이면 Binder 오류다(위 경고와 같은 규칙).
        "coalesce(c.content_time, '') AS content_ts, "
        # ⚠ `pinned` 를 여기 넣지 말 것. **Concept 테이블에 그 열이 없다**
        # (`store/embedded_schema.py` — pinned 는 Episode 의 열이다). 넣으면 Binder
        # 오류로 그래프 플랜 전체가 죽는다.
        # 레이아웃 정렬의 `pinned` 키가 무동작인 것은 이 때문이고, D32 의 "pin 이
        # 허브 승격 1순위"는 개념 축에서 **아직 구현된 적이 없다**(별건).
        "type(r) AS rel, r.evidence AS evidence, r.confidence AS confidence, "
        # v14 — 엣지 출생. 공유·동기화가 이 투영에서 값을 집어 나른다(안 나르면
        # 받는 쪽에서 그 엣지가 그 기기 시각으로 다시 태어난다). 화면은 아직 안 쓴다.
        "r.created AS edge_created, "
        "d.concept_id AS target, d.domain AS target_domain, "
        "d.title AS target_label, d.props AS target_props, d.tags AS target_tags, "
        "coalesce(d.created, d.updated_at, '') AS target_ts, "
        "coalesce(d.content_time, '') AS target_content_ts, "
        # R2 — 쓸 수는 있는데 어떤 읽기 도구로도 못 읽던 필드. **Concept 에만**
        # 있는 열이라(Decision·Record·Episode·Bridge 에는 없다) 여기 두 자리와
        # 축 플랜의 개념 팔에서만 뽑는다. 다른 라벨에 옮겨 붙이면 Binder 오류다.
        "coalesce(c.one_liner, '') AS one_liner, "
        "coalesce(d.one_liner, '') AS target_one_liner, "
        # W3 — 양 끝 다 싣는다. 한쪽만 실으면 target 으로만 등장한 노드가 영구히
        # 버전 없이 남고, 그건 "안 바뀌었다"와 구분되지 않는다.
        + _track_cols("c") + ", " + _track_cols("d", "target_")
    )
    return QueryPlan("graph", cypher, params, note="learning knowledge-graph (concepts + relations)")


def shape_graph(rows: list[dict]) -> dict:
    """Turn graph rows into ``{nodes, edges}`` (pure). Nodes de-duplicated by id
    with degree counted; edges mark ``cross_domain`` when endpoints differ in
    domain. Domain is a free/open string — never interpreted against a list.

    A1.9(A4): 노드에 ``props``(dict)·``tags``(list)를 실어 다리에서 요약이 유실되지
    않게 한다(예전엔 id/label/domain/kind/degree 만 반환).

    F2/F3: 행이 이종 kind(bridge/episode/record/entity)와 엣지 질화자
    (role)를 실어오면 그대로 셰이핑한다 — ``kind``/``target_kind`` 부재 시
    'concept'(하위호환), ``domain2``(브릿지 target_domain)는 존재 시에만 노드에
    부착. **degree 는 도착한 모든 엣지 행을 양 끝에 카운트** — 축 플랜(CONNECTS/
    ABOUT/ATTACHES_TO/TYPED)이 합류하는 것 자체가 F3(과소 카운트) 수리다(같은
    물리 엣지는 정확히 한 플랜에서만 오므로 이중 계상 없음). 노드 dedup 은 선착
    우선 — 호출부가 Concept 축을 먼저 넣는 고정 순서로 결정론을 보장한다.

    D43: 노드에 ``ts``(라벨별 시간열 — 플랜이 coalesce 로 단일화해
    ``ts``/``target_ts`` 로 투영), 엣지에 ``confidence``/``evidence``(E-G6 선택 열).
    ⚠ ``ts`` 계약: ``updated_at`` 은 ISO-ms UTC 라 사전식 정렬이 시간순과 일치하지만,
    ``created``/``date`` 는 자유형식이고 자주 ``''`` 다(``Record.created`` 는 오늘
    쓰는 코드가 없다). 따라서 ``ts`` 는 **정당하게 빈 문자열일 수 있고**, 소비처는
    가정하지 말고 강등해야 한다(날짜 없는 노드를 제외하거나 별도 표기).

    ⚠ dedup 예외 — ``ts`` 는 **빈 값일 때만 나중 행이 채운다**(그 외 필드는 선착
    우선 불변). 같은 노드가 여러 축에서 도착할 때 먼저 온 행이 시간열을 안 실었으면
    (또는 정말 비었으면) 뒤 행의 비지 않은 ``ts`` 로 승격한다 — 없으면 축 플랜으로만
    도착한 개념이 영구히 무날짜로 굳는다."""
    nodes: dict[str, dict] = {}

    def _node(nid, label, dom, props=None, tags=None, kind=None, domain2=None, ts=None,
              content_ts=None, one_liner=None, rev=None, updated_at=None,
              valid_from=None, valid_until=None):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": label or nid, "domain": dom or "",
                          "kind": kind or "concept", "degree": 0,
                          "props": _props_to_obj(props), "tags": list(tags or []),
                          "ts": ts or "", "content_ts": content_ts or "",
                          # R2 — 쓸 수는 있는데 어떤 읽기 도구로도 못 읽던 필드.
                          # 열은 **Concept 에만** 있으므로 나머지 종류에서는 빈
                          # 문자열이고, 그건 결손이 아니라 사실이다.
                          "one_liner": one_liner or "",
                          # W3: 낙관적 동시성의 읽기 절반. 쓰기는 이 값을
                          # `expected_rev` 로 실을 때만 대조한다(선택).
                          "rev": rev or 0, "updated_at": updated_at or "",
                          # W13 — Record 만 값을 갖는다(축 플랜 중 record 팔 전용 열).
                          # 다른 종류의 빈 문자열은 결손이 아니라 사실이다(R2 동형).
                          "valid_from": valid_from or "",
                          "valid_until": valid_until or ""}
            if domain2:
                nodes[nid]["domain2"] = domain2
        else:
            # 빈 시간열만 나중 행이 채운다 — 나머지 필드는 선착 우선 불변.
            # content_ts(D72)도 같은 규칙: 열이 없는 축 플랜(Bridge·Entity)에서
            # 먼저 도착한 노드가 영구히 무시점으로 굳지 않게 한다.
            if ts and not nodes[nid]["ts"]:
                nodes[nid]["ts"] = ts
            if content_ts and not nodes[nid].get("content_ts"):
                nodes[nid]["content_ts"] = content_ts
            # one_liner 도 같다: 개념이 축 플랜(열 없음)에서 먼저 도착하면 빈 채로
            # 들어오는데, 뒤이어 개념 플랜의 행이 값을 들고 온다.
            if one_liner and not nodes[nid].get("one_liner"):
                nodes[nid]["one_liner"] = one_liner
            # W3 — 같은 규칙. 투영 없는 행(있다면)이 먼저 닿아도 버전이 0/빈 채로
            # 굳지 않는다. 진짜 rev 0 은 채울 것이 없으므로 그대로 0 이다.
            if rev and not nodes[nid].get("rev"):
                nodes[nid]["rev"] = rev
            if updated_at and not nodes[nid].get("updated_at"):
                nodes[nid]["updated_at"] = updated_at
            # W13 — 같은 규칙. Record 는 개념 플랜의 target 으로 먼저 도착할 수
            # 있고(record→concept 의 relates_to 는 없지만 ids 모드 합류가 있다),
            # 그 경우 구간이 빈 채로 굳으면 무효화가 그 조회에서만 안 보인다.
            if valid_from and not nodes[nid].get("valid_from"):
                nodes[nid]["valid_from"] = valid_from
            if valid_until and not nodes[nid].get("valid_until"):
                nodes[nid]["valid_until"] = valid_until
        return nodes[nid]

    edges: list[dict] = []
    for r in rows:
        nid = r.get("id")
        if not nid:
            continue
        _node(nid, r.get("label"), r.get("domain"), r.get("props"), r.get("tags"),
              r.get("kind"), r.get("domain2"), r.get("ts"), r.get("content_ts"),
              r.get("one_liner"), r.get("rev"), r.get("updated_at"),
              # W13 — 유효구간은 record 축 행만 싣는다(매개자 자리라 source 측만).
              r.get("valid_from"), r.get("valid_until"))
        tgt = r.get("target")
        if tgt:
            # C5: target 은 target_label(=title)로 만든다 — 예전엔 id 를 label 로 넘겨
            # target-only 로 등장한 노드가 항상 id 노출로 폴백됐다(_node 의
            # `label or nid` 폴백은 title 이 정말 없을 때만 쓰여야 한다).
            _node(tgt, r.get("target_label"), r.get("target_domain"),
                  r.get("target_props"), r.get("target_tags"), r.get("target_kind"),
                  ts=r.get("target_ts"), content_ts=r.get("target_content_ts"),
                  one_liner=r.get("target_one_liner"), rev=r.get("target_rev"),
                  updated_at=r.get("target_updated_at"),
                  # 기록끼리의 SUPERSEDES 는 도착점도 Record 다. 대체된 기록의 구간이
                  # 빠지면 은퇴한 기록이 그래프에서 유효한 것처럼 보인다.
                  valid_from=r.get("target_valid_from"),
                  valid_until=r.get("target_valid_until"))
            src_dom, tgt_dom = r.get("domain"), r.get("target_domain")
            # cross_domain: 끝점 차이 규칙 유지 — 브릿지는 domain=source_domain 이라
            # 원측(source) 팔은 in-cluster, 반대측 팔만 cross(기하 정합, 특례 불요).
            cross = bool(src_dom and tgt_dom and src_dom != tgt_dom)
            edge = {"source": nid, "target": tgt,
                    "type": r.get("rel") or "RELATED_TO", "cross_domain": cross}
            if r.get("role"):
                edge["role"] = r["role"]
            # E-G6 — 관계 증거·신뢰도(선택 열). 상류 미저작 시 키를 붙이지 않는다(role 과 동형).
            if r.get("confidence") is not None:
                edge["confidence"] = float(r["confidence"])
            if r.get("evidence"):
                edge["evidence"] = r["evidence"]
            edges.append(edge)
            nodes[nid]["degree"] += 1
            nodes[tgt]["degree"] += 1
    return {"nodes": list(nodes.values()), "edges": edges}


def _derive_bridge_domains(shaped: dict) -> None:
    """Bridge 의 `domain` 은 **저작물이 아니라 파생값**이다 — CONNECTS 로 닿는 끝점
    도메인들의 가장 깊은 공통 조상(`vinc/security` + `vinc/engineering` → `vinc`).

    저장하지 않고 /graph 응답에서만 계산한다. 저장하면 연결이 바뀌는 순간 썩는데,
    브릿지의 소속은 정의상 그 연결이 결정하기 때문이다. 여태 노드 domain 은 선언
    필드 `source_domain` 이었고 라이브에서는 그게 **전부 비어 있어서**(v2 owner
    백필이 건너뛰어지며 domain 도 같이 남았다) 세 관점 모두가 브릿지 전량을
    "도메인 없음" 한 칸에 몰아넣었다. 근거: [[record:bridge-layout-clump-2026-08-22]].

    CONNECTS 가 하나도 없으면 미분류(`""`)로 남는다 — 지어낼 근거가 없다.
    도메인이 빈 끝점은 계산에서 뺀다(`_shared_prefix` 규약): 미분류 이웃 하나가
    나머지 끝점의 공통 조상을 뿌리까지 끌어내리면 안 된다.

    `domain2`(선언된 target_domain)는 상세 레일 계약이라 손대지 않는다."""
    nodes = shaped.get("nodes") or []
    doms = {n.get("id"): n.get("domain") or "" for n in nodes}
    bridges = {n.get("id") for n in nodes if n.get("kind") == "bridge"}
    if not bridges:
        return
    ends: dict[str, list[str]] = {}
    for e in shaped.get("edges") or []:
        if (e.get("type") or "") != "CONNECTS":
            continue
        s, t = e.get("source"), e.get("target")
        # 방향은 늘 Bridge→Concept 이지만 양쪽을 다 본다 — 어느 축 플랜이 실어 오든
        # 같은 답이 나오게(투영이 뒤집혀도 조용히 미분류가 되지 않게).
        if s in bridges and t in doms:
            ends.setdefault(s, []).append(t)
        if t in bridges and s in doms:
            ends.setdefault(t, []).append(s)
    for n in nodes:
        if n.get("id") in bridges:
            n["domain"] = "/".join(_shared_prefix(
                [_domain_path(doms.get(t, "")) for t in ends.get(n["id"], ())]))


def subgraph_ids_plan(seed: str, *, depth: int = 2, owner: str | None = None,
                      seed_label: str = "Concept") -> QueryPlan:
    """Seed+depth 순회 — View L2 노드 포커스와 MCP `vinc_graph` 의 공유 플랜(D16,
    MCP-SKELETON §10 공백 해소). seed 에서 depth 이내에 닿는 Concept id 를 수집한다.

    **G1(ladybug 0.18.0): 순회 의미론은 `TRAIL` 이 아니라 `SHORTEST` 다.**
    (아래 지연 수치는 0.18.0 의 것이다. 0.20.4 에서는 다시 재지 않았다.)
    이 플랜이 원하는 것은 *도달 집합*(DISTINCT id)이지 경로 열거가 아니다. TRAIL 은
    엣지 무재사용 경로를 **전부 펼친 뒤** DISTINCT 로 접으므로 조밀 ego 에서 그 일을
    두 번 한다. SHORTEST 는 (시작,끝) 쌍당 한 경로만 확장해 같은 집합을 더 싸게 낸다.
    잰 값(노드 200·평균차수 12, 같은 스토어, TRAIL→SHORTEST): depth2 9.6→3.8ms · depth3 5.5→4.1ms ·
    depth4 13.4→4.5ms. **결과 차집합은 시드 자신 하나뿐**이다(TRAIL 은 depth≥3 에서
    사이클로 시드에 되돌아와 시드를 행으로 낸다 — 차집합 `TRAIL-only=['n0']`).
    호출부 4곳(:func:`graph` · :func:`ego_ids` · :func:`decide` 토픽 · share 팩)이
    전부 `sorted({seed, *rows})` 로 시드를 명시 합류시키므로 **도달 집합은 동일**하고
    순수 비용 절감이다. M3(§6-2)이 막으려던 것은 기본 WALK 의 경로 폭증인데
    SHORTEST 는 애초에 폭증하지 않으므로 가드 목적도 그대로 유지된다.
    ⚠ `decide_plan`(경로 자체를 소비)은 TRAIL 을 유지한다 — 거긴 열거가 목적이다.

    depth 의미(F2/F4 이후): 무라벨 순회는 모든 rel 테이블을 지난다 — Bridge 매개
    (CONNECTS 2홉)에 더해 ABOUT/ATTACHES_TO 도 홉이 되므로, depth-2 에서 같은
    에피소드/레코드에 공동 부착된 개념들이 서로의 이웃이 된다(브릿지 형제와 동형).
    부착도 구조라는 의도된 의미 — rel 타입 제한(-[:A|B *]-)은 방언 위험 대비 이득이
    없어 하지 않는다.

    ⚠ owner 필수성(SEC-1): 이 플랜은 여태 owner 를 받지 않아 **호스티드 멀티테넌트에서
    남의 개념을 순회**했다(`vinc_graph` 는 seed 필수라 항상 이 경로). owner 를 주면
    시드와 도달 노드를 모두 그 소유자로 제한한다. 중간 홉은 필터하지 않지만, 쓰기가
    owner 스탬프를 강제하므로 교차 소유자 엣지가 생기지 않아 결과는 owner-clean 이다.
    owner=None(로컬 단일 사용자)이면 기존 동작.

    ``seed_label`` — **시드 한 칸만** 라벨을 바꾼다(기본 Concept. 기본값이면 플랜 문자열이
    인자를 주지 않은 것과 바이트로 같다). 도달 집합은 여전히 Concept 이다. 결정 시드가 여기서 0행을
    내서 `vinc_graph` 가 결정에서 퍼지지 않았다(감사 §5-6). 공유 팩·동기화·decide 는
    이 인자를 안 넘기므로 결과가 그대로다.
    근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S4"""
    depth = max(1, min(int(depth), 4))
    # PK 네임스페이싱 — `$seed` 는 노드 PK 바인딩이라 물리 키다(반환 id 는 읽기 훅이
    # 논리로 되돌린다). 근거: [[vinc.plan.node-pk-namespacing]] §7
    params: dict = {"seed": pkns.key(owner, seed)}
    seed_pk = _SEMANTIC_PK[seed_label]
    # 시드는 도달 집합에 넣지 않는다. ladybug SHORTEST 는 시드를 원래 안 내지만 GQL 의
    # `SHORTEST 1` 은 하한 1 이면 시드로 돌아오는 사이클을 고를 수 있어서 술어로 뺀다.
    n_where, m_where = f"n.{seed_pk} = $seed", "m.concept_id <> $seed"
    if owner:
        n_where += " AND n.owner_id = $owner"
        m_where += " AND m.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (n:{seed_label}) WHERE {n_where} "
        f"MATCH SHORTEST 1 (n)-[]-{{1,{depth}}}(m:Concept) WHERE {m_where} "
        "RETURN DISTINCT m.concept_id AS id"
    )
    return QueryPlan("subgraph_ids", cypher, params,
                     note=f"seed+depth reach set (depth={depth}, SHORTEST)")


def subgraph_hops_plan(seed: str, *, depth: int = 2, owner: str | None = None,
                       seed_label: str = "Concept") -> QueryPlan:
    """:func:`subgraph_ids_plan` 과 같은 도달 집합에 **홉 수**를 붙인다.

    노드 예산(`max_nodes`)을 받은 :func:`graph` 만 쓴다. 예산을 넘으면 깊이를 층
    단위로 줄이려면 각 노드가 몇 홉에 있는지 알아야 해서다. SHORTEST 는 (시드, m)
    쌍마다 경로 하나만 펴므로 행도 m 마다 하나다. 예산 없는 호출(View L2·공유 팩·
    동기화)은 이 플랜을 타지 않는다 — 그 결과는 바이트 단위로 그대로다.

    ⚠ 무라벨 순회라 IN_DOMAIN·PARENT_OF 도 홉이다. 그래서 depth 2 는 시드와 **같은
    주제의 개념 전부**에 닿고(Domain 노드 경유), depth 4 면 주제 트리를 타고 형제
    주제의 개념까지 번진다(임베디드 스토어). 허브의 depth 2+ 가 무거운 까닭 하나이고, 예산이 층 단위로 깊이를
    줄이는 것이 그 비용을 끊는다(순회 의미 자체는 공유 플랜이라 여기서 안 바꾼다).
    근거는 :func:`graph` 독스트링의 노드 예산 인용과 같다."""
    depth = max(1, min(int(depth), 4))
    params: dict = {"seed": pkns.key(owner, seed)}
    seed_pk = _SEMANTIC_PK[seed_label]
    # 시드는 도달 집합에 넣지 않는다. ladybug SHORTEST 는 시드를 원래 안 내지만 GQL 의
    # `SHORTEST 1` 은 하한 1 이면 시드로 돌아오는 사이클을 고를 수 있어서 술어로 뺀다.
    n_where, m_where = f"n.{seed_pk} = $seed", "m.concept_id <> $seed"
    if owner:
        n_where += " AND n.owner_id = $owner"
        m_where += " AND m.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (n:{seed_label}) WHERE {n_where} "
        f"MATCH p = SHORTEST 1 (n)-[]-{{1,{depth}}}(m:Concept) WHERE {m_where} "
        "RETURN m.concept_id AS id, min(length(p)) AS hops"
    )
    return QueryPlan("subgraph_hops", cypher, params,
                     note=f"seed+depth reach set with hop counts (depth={depth})")


def decision_seed_rows_plan(seed: str, *, owner: str | None = None,
                            other: str = "Concept", outgoing: bool = True,
                            rels: str = "PREREQUISITE_OF|DERIVES_FROM|INHERITS_FROM"
                            ) -> QueryPlan:
    """결정 시드 자신의 의미층 엣지(PREREQUISITE_OF·DERIVES_FROM·INHERITS_FROM) 한 방향·
    한 상대 라벨. :func:`decide`가 결정 시드의 로컬 관계를 읽는 플랜이다.
    :func:`graph`는 공통 읽기 계약의 decision_relation_rows_plan을 사용한다.

    ``rels`` 는 :func:`decide` 만 바꾼다 — 거기엔 결정 축 플랜이 안 돌아서 ABOUT 도
    이 플랜으로 싣는다(결정 축 플랜은 owner 필수라 로컬 무소유 호출에서 못 쓴다).

    행 모양은 graph_rows 와 같다(id=출발, target=도착). Decision 에는 one_liner·
    content_time 열이 없어 그 자리는 리터럴이다 — 열 없는 라벨에 옮겨 붙이면 Binder
    가 조회 전체를 죽인다(decision_rows_plan 경고와 같은 자리).
    근거는 :func:`subgraph_ids_plan` 의 ``seed_label`` 인용과 같다."""
    params: dict = {"seed": pkns.key(owner, seed)}
    s_where, o_where = "s.decision_id = $seed", ""
    if owner:
        s_where += " AND s.owner_id = $owner"
        o_where = " WHERE o.owner_id = $owner"
        params["owner"] = owner
    o_pk = _SEMANTIC_PK[other]
    o_kind = other.lower()
    o_ol = "coalesce(o.one_liner, '')" if other == "Concept" else "''"
    o_ct = "coalesce(o.content_time, '')" if other == "Concept" else "''"
    s_cols = ("s.decision_id AS {i}, s.title AS {p}label, s.domain AS {p}domain, "
              "s.props AS {p}props, coalesce(s.tags, []) AS {p}tags, "
              "'decision' AS {p}kind, coalesce(s.created, s.updated_at, '') AS {p}ts, "
              "'' AS {p}content_ts, '' AS {p}one_liner, ")
    o_cols = (f"o.{o_pk} AS {{i}}, o.title AS {{p}}label, o.domain AS {{p}}domain, "
              "o.props AS {p}props, coalesce(o.tags, []) AS {p}tags, "
              f"'{o_kind}' AS {{p}}kind, coalesce(o.created, o.updated_at, '') AS {{p}}ts, "
              f"{o_ct} AS {{p}}content_ts, {o_ol} AS {{p}}one_liner, ")
    src, dst = (s_cols, o_cols) if outgoing else (o_cols, s_cols)
    src_a, dst_a = ("s", "o") if outgoing else ("o", "s")
    if outgoing:
        pattern = f"(s)-[r:{rels}]->(o:{other})"
    else:
        pattern = f"(o:{other})-[r:{rels}]->(s)"
    cypher = (
        f"MATCH (s:Decision) WHERE {s_where} "
        f"MATCH {pattern}{o_where} "
        "RETURN " + src.format(p="", i="id") + dst.format(p="target_", i="target")
        + "type(r) AS rel, r.evidence AS evidence, r.confidence AS confidence, "
        + _track_cols(src_a) + ", " + _track_cols(dst_a, "target_")
    )
    return QueryPlan("graph_decision_seed", cypher, params,
                     note=f"decision seed edges ({'out' if outgoing else 'in'} {other})")


def graph_rows_for_ids_plan(ids: list[str], *, owner: str | None = None) -> QueryPlan:
    """id 집합의 Concept 행 + 나가는 엣지(§4-2 — 엣지의 서브그래프 외부 여과는
    파이썬 shape 단계에서). `graph_plan` 과 동일 투영.

    ⚠ owner 필수성(SEC-1): id 집합이 이미 owner-scoped 여도 **엣지 대상 d 는 별개**다 —
    필터가 없으면 남의 노드가 target_label/target_props 로 노출된다. 소유자를 주면
    행(c)과 엣지 대상(d)을 모두 제한한다."""
    params: dict = {"ids": pkns.keys(owner, ids)}
    c_where, d_where = "c.concept_id IN $ids", ""
    if owner:
        c_where += " AND c.owner_id = $owner"
        d_where = " WHERE d.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (c:Concept) WHERE {c_where} "
        f"OPTIONAL MATCH (c)-[r:{_CONCEPT_READ_PATTERN}]->(d:Concept){d_where} "
        "RETURN c.concept_id AS id, c.title AS label, c.domain AS domain, "
        "c.props AS props, c.tags AS tags, "
        "coalesce(c.created, c.updated_at, '') AS ts, "
        # v7(D72) — 내용 시점을 **별도 열로** 나른다(ts 에 접지 않는다). 클라이언트가
        # 둘 다 받아 두면 축 전환에 재조회가 필요 없다. Concept 만 이 열을 가지므로
        # 다른 라벨의 플랜에 그대로 옮겨 붙이면 Binder 오류다(위 경고와 같은 규칙).
        "coalesce(c.content_time, '') AS content_ts, "
        "type(r) AS rel, r.evidence AS evidence, r.confidence AS confidence, "
        # v14 — 엣지 출생. 공유·동기화가 이 투영에서 값을 집어 나른다(안 나르면
        # 받는 쪽에서 그 엣지가 그 기기 시각으로 다시 태어난다). 화면은 아직 안 쓴다.
        "r.created AS edge_created, "
        "d.concept_id AS target, d.domain AS target_domain, "
        "d.title AS target_label, d.props AS target_props, d.tags AS target_tags, "
        "coalesce(d.created, d.updated_at, '') AS target_ts, "
        "coalesce(d.content_time, '') AS target_content_ts, "
        # R2 — 쓸 수는 있는데 어떤 읽기 도구로도 못 읽던 필드. **Concept 에만**
        # 있는 열이라(Decision·Record·Episode·Bridge 에는 없다) 여기 두 자리와
        # 축 플랜의 개념 팔에서만 뽑는다. 다른 라벨에 옮겨 붙이면 Binder 오류다.
        "coalesce(c.one_liner, '') AS one_liner, "
        "coalesce(d.one_liner, '') AS target_one_liner, "
        # W3 — 양 끝 다 싣는다. 한쪽만 실으면 target 으로만 등장한 노드가 영구히
        # 버전 없이 남고, 그건 "안 바뀌었다"와 구분되지 않는다.
        + _track_cols("c") + ", " + _track_cols("d", "target_")
    )
    return QueryPlan("graph", cypher, params, note="subgraph rows for id set")


# ── View 축 플랜(F2 — Concept 단일 축 해소) ─────────────────────────────────
# 부착 축 3종(Bridge·Episode·Record)을 별도 순수 플랜으로 두고 파이썬 셰이핑에서
# 합류한다(UNION ALL 메가플랜 대신 — 이종 투영의 NULL 패딩 회피 + 플랜별 강등:
# 미마이그레이션 스토어에서 새 축만 조용히 빈 행이 되고 Concept 축은 산다).
# 공통 계약: **비-OPTIONAL 매치** — 부착 없는 항목은 렌더 대상이 아니다(View =
# 연결 뷰; 미부착 Episode/Record 의 표면은 Records/L0 집계). domain(L1 렌즈)과
# ids(L2 ego 후조회)는 상호 배제. ids 모드는 **어느 한쪽 끝점**이 집합에 들면
# 매치 — concept 시드에선 팔이 kept 개념에 닿을 때(접착제), episode:/record:/
# bridge: 시드에선 자기 부착이 나온다(고립 폴백 대신 depth-1 이웃 렌더).

def bridge_rows_plan(*, owner: str, domain: str | None = None, domains: list[str] | None = None,
                     ids: list[str] | None = None) -> QueryPlan:
    """Bridge 노드 + CONNECTS 팔(F2). 노드 domain = source_domain(단일 결정값),
    target_domain 은 domain2 로 병기(상세 레일용). L1 소속은 선언 필드(source/
    target_domain)를 신뢰 — 실 CONNECTS 끝점과 어긋난 브릿지는 렌즈에서 빠질 수
    있다(L2 에선 보임, v1 수용). owner: Bridge 과도기 술어 + 도착점 엄격.

    도착점은 의미층 라벨마다 한 팔이다(개념·결정). 개념만 보면 결정에 걸린 다리가
    vinc_neighbors 에는 보이고 그래프에는 안 보인다(감사 F2)."""
    doms = normalize_domains(domain, domains)
    assert not (doms and ids), "domain and ids are mutually exclusive"
    where = [_bridge_owner_pred("b"), "c.owner_id = $owner"]
    params: dict = {"owner": owner}
    if doms:
        where.append(domain_subtree_where(
            ["b.source_domain", "b.target_domain"], doms, params))
    if ids:
        params["ids"] = pkns.keys(owner, ids)
    arms = []
    for t_label, t_pk in _SEMANTIC_PK.items():
        arm_where = list(where)
        if ids:
            arm_where.append(f"(c.{t_pk} IN $ids OR b.bridge_id IN $ids)")
        # one_liner·content_time 은 Concept 에만 있는 열이라 다른 라벨은 리터럴로 맞춘다.
        concept = t_label == "Concept"
        arms.append(
            f"MATCH (b:Bridge)-[r:CONNECTS]->(c:{t_label}) "
            f"WHERE {' AND '.join(arm_where)} "
            "RETURN b.bridge_id AS id, b.title AS label, b.source_domain AS domain, "
            "b.target_domain AS domain2, b.props AS props, 'bridge' AS kind, "
            "coalesce(b.created, b.updated_at, '') AS ts, "
            "'CONNECTS' AS rel, r.role AS role, "
            f"c.{t_pk} AS target, c.domain AS target_domain, c.title AS target_label, "
            f"c.props AS target_props, c.tags AS target_tags, '{t_label.lower()}' AS target_kind, "
            "coalesce(c.created, c.updated_at, '') AS target_ts, "
            # F10 — 개념의 내용 시점을 이 축에서도 싣는다. 빠지면 다리로만 닿은 개념이
            # 내용 시점 없이 굳는다.
            + ("coalesce(c.content_time, '') AS target_content_ts, "
               "coalesce(c.one_liner, '') AS target_one_liner, " if concept else
               "'' AS target_content_ts, '' AS target_one_liner, ")
            # W3 — 매개자(b)도 vinc_graph 의 노드로 나가므로 함께 싣는다.
            # one_liner 와 달리 이 두 열은 추적 라벨 전부에 있어 리터럴 맞춤이 필요 없다.
            + _track_cols("b") + ", " + _track_cols("c", "target_"))
    return QueryPlan("graph_axis_bridge", " UNION ALL ".join(arms), params,
                     note="bridge axis rows (F2)")


def episode_rows_plan(*, owner: str, domain: str | None = None, domains: list[str] | None = None,
                      ids: list[str] | None = None) -> QueryPlan:
    """Episode 노드 + ABOUT 팔(F2/F4). L1 소속은 (자기 domain OR 부착 개념 domain)
    — 렌즈 밖 에피소드가 렌즈 안 개념에 부착된 경우가 정확히 F2 가 숨기던 연결이라
    OR 가 의도다."""
    doms = normalize_domains(domain, domains)
    assert not (doms and ids), "domain and ids are mutually exclusive"
    where = ["e.owner_id = $owner", "c.owner_id = $owner"]
    params: dict = {"owner": owner}
    if doms:
        where.append(domain_subtree_where(["e.domain", "c.domain"], doms, params))
    if ids:
        where.append("(c.concept_id IN $ids OR e.episode_id IN $ids)")
        params["ids"] = pkns.keys(owner, ids)
    cypher = (
        "MATCH (e:Episode)-[r:ABOUT]->(c:Concept) "
        f"WHERE {' AND '.join(where)} "
        "RETURN e.episode_id AS id, e.title AS label, e.domain AS domain, "
        "e.props AS props, coalesce(e.tags, []) AS tags, 'episode' AS kind, "
        "coalesce(e.date, e.updated_at, '') AS ts, "
        # ABOUT 은 E-G6 열을 가진 rel 테이블이라 evidence/confidence 투영이 안전하다.
        "'ABOUT' AS rel, r.evidence AS evidence, r.confidence AS confidence, "
        "c.concept_id AS target, c.domain AS target_domain, c.title AS target_label, "
        "c.props AS target_props, c.tags AS target_tags, 'concept' AS target_kind, "
        # D43: 부착 개념의 시간열도 투영한다 — 축 플랜으로만 도착하는 개념
        # (episode:/record:/bridge: 시드 L2)이 영구 무날짜가 되던 결함(Timeline 이
        # 전량을 '날짜 없음'으로 강등)의 절반. 나머지 절반은 shape_graph 의 ts 승격.
        "coalesce(c.created, c.updated_at, '') AS target_ts, "
        # F10 — 내용 시점도 같은 이유로 싣는다(도착점이 개념이라 열이 있다).
        "coalesce(c.content_time, '') AS target_content_ts, "
        # R2 — 같은 이유로 개념 팔의 one_liner 도 싣는다(Episode 쪽에는 열이 없다).
        "coalesce(c.one_liner, '') AS target_one_liner, "
        # W3 — 매개자(e)도 vinc_graph 의 노드로 나가므로 함께 싣는다.
        # one_liner 와 달리 이 두 열은 추적 라벨 전부에 있어 리터럴 맞춤이 필요 없다.
        + _track_cols("e") + ", " + _track_cols("c", "target_")
    )
    return QueryPlan("graph_axis_episode", cypher, params, note="episode axis rows (F2/F4)")


def record_rows_plan(*, owner: str, domain: str | None = None, domains: list[str] | None = None,
                     ids: list[str] | None = None) -> QueryPlan:
    """Record 노드 + ATTACHES_TO 팔(F2). 엣지 질화자 r.type 은 role 슬롯으로 투영
    (CONNECTS.role 과 단일 선택 열 공유 — shape_graph 계약).

    도착점은 의미층 라벨마다 한 팔이다(개념·결정). 근거·신뢰도(v16 실열)도 싣는다 —
    빠지면 vinc_graph 와 그것을 쓰는 내보내기가 부착의 근거를 잃는다(감사 F2·F2b)."""
    doms = normalize_domains(domain, domains)
    assert not (doms and ids), "domain and ids are mutually exclusive"
    where = ["rc.owner_id = $owner", "c.owner_id = $owner"]
    params: dict = {"owner": owner}
    if doms:
        where.append(domain_subtree_where(["rc.domain", "c.domain"], doms, params))
    if ids:
        params["ids"] = pkns.keys(owner, ids)
    arms = []
    for t_label, t_pk in _SEMANTIC_PK.items():
        arm_where = list(where)
        if ids:
            arm_where.append(f"(c.{t_pk} IN $ids OR rc.record_id IN $ids)")
        # one_liner·content_time 은 Concept 에만 있는 열이라 다른 라벨은 리터럴로 맞춘다.
        concept = t_label == "Concept"
        arms.append(
            f"MATCH (rc:Record)-[r:ATTACHES_TO]->(c:{t_label}) "
            f"WHERE {' AND '.join(arm_where)} "
            "RETURN rc.record_id AS id, rc.title AS label, rc.domain AS domain, "
            "rc.props AS props, coalesce(rc.tags, []) AS tags, 'record' AS kind, "
            "coalesce(rc.created, rc.updated_at, '') AS ts, "
            # v7(D72) — Record 도 content_time 을 얻었다(Concept 과 같은 축). Bridge·Entity
            # 축 플랜에는 열이 없으므로 절대 옮겨 붙이지 않는다 — 붙이면 모든 레벨의
            # /graph 가 Binder 로 죽는다.
            "coalesce(rc.content_time, '') AS content_ts, "
            "'ATTACHES_TO' AS rel, r.type AS role, "
            "r.evidence AS evidence, r.confidence AS confidence, "
            f"c.{t_pk} AS target, c.domain AS target_domain, c.title AS target_label, "
            f"c.props AS target_props, c.tags AS target_tags, '{t_label.lower()}' AS target_kind, "
            "coalesce(c.created, c.updated_at, '') AS target_ts, "
            # R2 — 개념 팔에서만 뽑는다. 매개자(Bridge·Episode·Record·Decision)에는
            # 이 열이 없어 shape_graph 가 빈 문자열로 둔다(결손이 아니라 사실).
            + ("coalesce(c.content_time, '') AS target_content_ts, "
               "coalesce(c.one_liner, '') AS target_one_liner, " if concept else
               "'' AS target_content_ts, '' AS target_one_liner, ")
            # W3 — 매개자(rc)도 vinc_graph 의 노드로 나가므로 함께 싣는다.
            # one_liner 와 달리 이 두 열은 추적 라벨 전부에 있어 리터럴 맞춤이 필요 없다.
            + _track_cols("rc") + ", " + _track_cols("c", "target_") + ", "
            # W13 — 유효구간은 **Record 팔에서만** 뽑는다(도착점에는 열이 없다).
            + _validity_cols("rc"))
    return QueryPlan("graph_axis_record", " UNION ALL ".join(arms), params,
                     note="record axis rows (F2)")


def decision_rows_plan(*, owner: str, domain: str | None = None, domains: list[str] | None = None,
                       ids: list[str] | None = None) -> QueryPlan:
    """Decision 노드 + ABOUT 팔(v12). 결정이 **무엇을 두고** 내려졌는지가 그 팔이다.

    Episode 축과 같은 형태다(둘 다 ABOUT 으로 개념에 붙는다). L1 소속도 같은 이유로
    OR — 렌즈 밖 결정이 렌즈 안 개념을 두고 내려진 경우가 정확히 F2 가 숨기던 연결이다.

    ⚠ `content_ts` 를 **투영하지 않는다**. Decision 에는 그 열이 없다(라이브 데이터에서
    68/68 이 비어 있어 열을 두지 않는다). 열 없는 라벨에 옮겨 붙이면 모든 레벨의 /graph
    가 Binder 로 죽는다 — Record 축 주석이 경고하는 바로 그 자리다. 도착점(Concept)
    쪽은 열이 있으므로 target_content_ts 는 싣는다."""
    doms = normalize_domains(domain, domains)
    assert not (doms and ids), "domain and ids are mutually exclusive"
    where = ["d.owner_id = $owner", "c.owner_id = $owner"]
    params: dict = {"owner": owner}
    if doms:
        where.append(domain_subtree_where(["d.domain", "c.domain"], doms, params))
    if ids:
        where.append("(c.concept_id IN $ids OR d.decision_id IN $ids)")
        params["ids"] = pkns.keys(owner, ids)
    cypher = (
        "MATCH (d:Decision)-[r:ABOUT]->(c:Concept) "
        f"WHERE {' AND '.join(where)} "
        "RETURN d.decision_id AS id, d.title AS label, d.domain AS domain, "
        "d.props AS props, coalesce(d.tags, []) AS tags, 'decision' AS kind, "
        "coalesce(d.created, d.updated_at, '') AS ts, "
        "'ABOUT' AS rel, r.evidence AS evidence, r.confidence AS confidence, "
        "c.concept_id AS target, c.domain AS target_domain, c.title AS target_label, "
        "c.props AS target_props, c.tags AS target_tags, 'concept' AS target_kind, "
        "coalesce(c.created, c.updated_at, '') AS target_ts, "
        "coalesce(c.content_time, '') AS target_content_ts, "
        # R2 — 개념 팔에서만 뽑는다. 매개자(Bridge·Episode·Record·Decision)에는
        # 이 열이 없어 shape_graph 가 빈 문자열로 둔다(결손이 아니라 사실).
        "coalesce(c.one_liner, '') AS target_one_liner, "
        # W3 — 매개자(d)도 vinc_graph 의 노드로 나가므로 함께 싣는다.
        # one_liner 와 달리 이 두 열은 추적 라벨 전부에 있어 리터럴 맞춤이 필요 없다.
        + _track_cols("d") + ", " + _track_cols("c", "target_")
    )
    return QueryPlan("graph_axis_decision", cypher, params, note="decision axis rows (v12)")


def decision_relation_rows_plan(*, owner: str, domain: str | None = None,
                                domains: list[str] | None = None,
                                ids: list[str] | None = None) -> QueryPlan:
    """기존 축이 읽지 않는 관계를 전체/주제/시드 그래프에 보충한다.

    이름과 달리 Decision 관계만이 아니다. 개념끼리(graph_plan)와 ABOUT→Concept
    (사건·결정 축)를 뺀 공통 읽기 계약의 나머지 전부다 — 결정의 의미 관계, 사건→결정
    ABOUT, 그리고 기록끼리의 SUPERSEDES(감사 F2). 이름을 그대로 둔 것은
    호출부와 테스트가 이 이름으로 부르기 때문이다.

    끝점 조합은 스키마 파생 계약을 사용한다. 렌즈 소속은 기존 부착 축처럼 어느 한
    끝점의 domain으로 가린다. ids 모드는 어느 한 끝점이 기존 도달 집합에 속한
    관계만 한 번 합류한다. 이웃을 다시 순회하지 않으므로 Concept 도달 집합과 예산은
    그대로다.
    """
    doms = normalize_domains(domain, domains)
    params: dict = {"owner": owner}
    pairs: dict[tuple, list[str]] = {}
    for pair, rels in _GRAPH_READ_PAIRS.items():
        sl, _sp, dl, _dp = pair
        if (sl, dl) == ("Concept", "Concept"):
            continue
        selected = [rel for rel in rels if not (rel == "ABOUT" and dl == "Concept")]
        if selected:
            pairs[pair] = selected
    if wiki_pairs_open(owner):
        # 공개 위키는 사건·기록 쪽끼리도 잇는다(v23). 개인·팀 조회는 갈래가 늘지 않는다.
        for pair, rels in _WIKI_READ_PAIRS.items():
            pairs.setdefault(pair, []).extend(rels)
    if ids is not None:
        params["ids"] = pkns.keys(owner, ids)

    def cols(alias: str, label: str, pk: str, prefix: str = "") -> str:
        nid = "target" if prefix else "id"
        time_col = "date" if label == "Episode" else "created"
        ol = f"coalesce({alias}.one_liner, '')" if label == "Concept" else "''"
        ct = (f"coalesce({alias}.content_time, '')" if label in ("Concept", "Record")
              else "''")
        # 유효구간은 Record 열이다. 다른 라벨은 리터럴로 맞춘다(UNION 모양).
        vf, vu = ((f"coalesce({alias}.valid_from, '')", f"coalesce({alias}.valid_until, '')")
                  if label == "Record" else ("''", "''"))
        return (
            f"{alias}.{pk} AS {nid}, {alias}.title AS {prefix}label, "
            f"{alias}.domain AS {prefix}domain, {alias}.props AS {prefix}props, "
            f"coalesce({alias}.tags, []) AS {prefix}tags, '{label.lower()}' AS {prefix}kind, "
            f"coalesce({alias}.{time_col}, {alias}.updated_at, '') AS {prefix}ts, "
            f"{ct} AS {prefix}content_ts, {ol} AS {prefix}one_liner, "
            f"{vf} AS {prefix}valid_from, {vu} AS {prefix}valid_until, "
            + _track_cols(alias, prefix)
        )

    arms = []
    for (sl, sp, dl, dp), rels in pairs.items():
        where = ["a.owner_id = $owner", "b.owner_id = $owner"]
        if ids is not None:
            where.append(f"(a.{sp} IN $ids OR b.{dp} IN $ids)")
        elif doms:
            where.append(domain_subtree_where(["a.domain", "b.domain"], doms, params))
        arms.append(
            f"MATCH (a:{sl})-[r:{'|'.join(rels)}]->(b:{dl}) "
            f"WHERE {' AND '.join(where)} RETURN "
            + cols("a", sl, sp) + ", " + cols("b", dl, dp, "target_") + ", "
            "type(r) AS rel, r.evidence AS evidence, r.confidence AS confidence"
        )
    return QueryPlan("graph_decision_relations", " UNION ALL ".join(arms), params,
                     note="decision relations outside the existing attachment axes")


_GRAPH_AXIS_PLANS = (bridge_rows_plan, episode_rows_plan, record_rows_plan,
                     decision_rows_plan)
_GRAPH_READ_PLANS = _GRAPH_AXIS_PLANS + (decision_relation_rows_plan,)


# ── entity-시드 L2(F1) — TYPED 는 Entity↔Entity 폐쇄 성분 ────────────────────
# Entity 는 domain 열이 없고(테마 귀속) 어떤 기존 rel 테이블에도 닿지 않으므로
# L1/개념시드 L2 에는 **구조적으로** 등장하지 않는다. 가시성은 entity: 시드 전용.

def entity_subgraph_ids_plan(seed: str, *, depth: int = 2,
                             owner: str | None = None) -> QueryPlan:
    """entity-시드 순회 — depth 1..4 클램프, 엣지 무재사용 순회(어댑터가 ladybug `* TRAIL` 로 보낸다).
    시드로 돌아오는 사이클이 있으면 시드도 낸다(TRAIL 의미 그대로).
    Entity 는 소유 없는 '' 행이 없는 테이블이라 엄격 owner."""
    depth = max(1, min(int(depth), 4))
    params: dict = {"seed": pkns.key(owner, seed)}
    n_where, m_where = "n.entity_id = $seed", ""
    if owner:
        n_where += " AND n.owner_id = $owner"
        m_where = " WHERE m.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (n:Entity) WHERE {n_where} "
        f"MATCH (n)-[]-{{1,{depth}}}(m:Entity){m_where} "
        "RETURN DISTINCT m.entity_id AS id"
    )
    return QueryPlan("entity_subgraph_ids", cypher, params,
                     note=f"entity seed traversal (depth={depth})")


def entity_rows_for_ids_plan(ids: list[str], *, owner: str | None = None) -> QueryPlan:
    """Entity id 집합 행 + 나가는 TYPED 엣지. rel 은 type(r)="TYPED"(무의미한 캐리어
    이름)가 아니라 **r.type(사용자 저작 타입)** 을 투영 — 그것이 의미다. domain 은
    ''(Entity 무도메인 — entity-시드 L2 는 단일 중앙 클러스터로 레이아웃)."""
    params: dict = {"ids": pkns.keys(owner, ids)}
    e_where, d_where = "e.entity_id IN $ids", ""
    if owner:
        e_where += " AND e.owner_id = $owner"
        d_where = " WHERE d.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (e:Entity) WHERE {e_where} "
        f"OPTIONAL MATCH (e)-[r:TYPED]->(d:Entity){d_where} "
        "RETURN e.entity_id AS id, e.title AS label, '' AS domain, "
        "coalesce(e.tags, []) AS tags, 'entity' AS kind, "
        # Entity 는 created/date 열이 없다 — updated_at 만이 시간 원천.
        "coalesce(e.updated_at, '') AS ts, "
        "r.type AS rel, d.entity_id AS target, '' AS target_domain, "
        "d.title AS target_label, coalesce(d.tags, []) AS target_tags, "
        "'entity' AS target_kind, "
        # W3 — Entity 도 추적 라벨이다(`_TRACKED_LABELS`). 개방층 노드만 버전이
        # 안 보이면 그 축에서만 쓰기 확인이 안 된다.
        + _track_cols("e") + ", " + _track_cols("d", "target_")
    )
    return QueryPlan("entity_graph", cypher, params, note="entity rows for id set (TYPED)")


# 고립 시드가 실어 오는 열 — 라벨마다 **있는 것만** 고른다. 없는 열을 RETURN 하면
# Binder 예외로 조회가 통째로 죽는다(Bridge 에 `domain` 이 없는 것과 같은 자리).
# ts 표현은 각 축 플랜의 coalesce 와 같은 것을 쓴다 — 같은 노드가 연결 유무에 따라
# 다른 시간을 말하면 안 된다.
_SEED_FIELDS: dict[str, dict[str, str]] = {
    "Concept": {"domain": "n.domain", "tags": "n.tags", "props": "n.props",
                "ts": "coalesce(n.created, n.updated_at, '')",
                "content_ts": "coalesce(n.content_time, '')"},
    "Record": {"domain": "n.domain", "tags": "n.tags", "props": "n.props",
               "ts": "coalesce(n.created, n.updated_at, '')",
               "content_ts": "coalesce(n.content_time, '')"},
    "Decision": {"domain": "n.domain", "tags": "n.tags", "props": "n.props",
                 "ts": "coalesce(n.created, n.updated_at, '')"},
    "Episode": {"domain": "n.domain", "tags": "n.tags", "props": "n.props",
                "ts": "coalesce(n.date, n.updated_at, '')"},
    "Bridge": {"props": "n.props", "ts": "coalesce(n.created, n.updated_at, '')"},
    "Entity": {"tags": "n.tags", "ts": "coalesce(n.updated_at, '')"},
}


def seed_lookup_plan(seed: str, *, owner: str | None = None) -> QueryPlan:
    """seed 노드 존재 선조회(C4) — '노드 없음'과 '노드는 있으나 연결 0(고립)'을
    구분하기 위한 조회. id 접두사(`concept:`·`episode:`·
    `record:`·`bridge:`·`entity:`)로 테이블을 판별하고, 접두사가 없거나 미지의
    접두사면 Concept 로 시도한다. `graph()` 는 순회 결과가 비었을 때만 이 플랜을
    쓴다 — 순회가 이미 노드를 찾았다면(=시드가 확실히 존재) 굳이 다시 묻지 않는다.

    owner(SEC-1): Concept/Episode/Record/Entity 는 엄격 owner 로 제한한다. Bridge 는
    v2(F5)에서 owner_id 를 얻어 과도기 술어(:func:`_bridge_owner_pred`)로 필터한다
    — 구 '필터 불가' 예외(존재 여부 노출)는 해소됐다. 미지 접두사는 Entity 로
    프로브한다(:func:`_seed_table` — 개방층 id 는 임의 접두사)."""
    label, pk = _seed_table(seed)
    params: dict = {"seed": pkns.key(owner, seed)}
    where = f"n.{pk} = $seed"
    if owner:
        where += (f" AND {_bridge_owner_pred('n')}" if label == "Bridge"
                  else " AND n.owner_id = $owner")
        params["owner"] = owner
    # ⚠ id·title 만 싣던 시절, 고립 노드는 `vinc_graph` 에서 **domain·tags·props·ts 가
    # 전부 빈** 모습으로 답했다 — 아래 graph() 가 없는 값을 빈 값으로 지어냈기 때문이다.
    # 값이 실제로 저장돼 있어도 그렇게 보이므로, 방금 쓴 노드를 읽어 확인하는 절차가
    # (그래프에 근거를 옮길 때 쓰는 그 절차가) "쓰기가 실패했다"는 오진을 낳았다.
    # 그래서 있는 열은 실어 온다.
    extra = _SEED_FIELDS.get(label, {})
    cols = ", ".join([f"n.{pk} AS id", "n.title AS title"]
                     + [f"{expr} AS {alias}" for alias, expr in extra.items()])
    cypher = f"MATCH (n:{label}) WHERE {where} RETURN {cols}"
    return QueryPlan("seed_lookup", cypher, params, note=f"seed existence probe ({label})")


def _apply_basis(shaped: dict, basis: str) -> str:
    """`content` 축이면 노드의 `ts` 를 `content_ts` 로 갈아 끼운다.

    **Cypher 를 분기하지 않는 이유**: 시간열을 조건절에 넣는 순간 플랜 수만큼 Binder
    함정(§4)이 늘어난다. 투영은 두 열을 항상 싣고, 어느 쪽을 쓸지는 여기서 정한다.
    클라이언트도 두 열을 다 받으므로 축 전환에 재조회가 필요 없다.

    돌려주는 값은 **실제로 적용한 축**이다 — 미지 값은 조용히 recorded 로 강등하고
    그 사실을 응답이 에코한다(화면이 무엇을 셌는지 밝힐 수 있게)."""
    if basis != BASIS_CONTENT:
        return BASIS_RECORDED
    for n in shaped.get("nodes", []):
        n["ts"] = n.get("content_ts") or ""
    return BASIS_CONTENT


def _order_graph_result(shaped: dict) -> None:
    """계산된 값은 그대로 두고 그래프 응답의 배열 순서만 고정한다."""
    shaped["nodes"].sort(key=lambda n: n["id"])
    # 같은 끝점·타입의 관계도 role/evidence/confidence 등 전체 값으로 동률을 푼다.
    shaped["edges"].sort(key=lambda e: (
        e["source"], e["target"], e["type"],
        json.dumps(e, sort_keys=True, ensure_ascii=False, separators=(",", ":"))))


def graph(*, owner: str, domain: str | None = None, seed: str | None = None,
          depth: int = 2, layout: str = "radial", basis: str = BASIS_RECORDED,
          domains: list[str] | None = None, cone: float = 1.0,
          max_nodes: int | None = None, select: str = "layers",
          cfg=None, store=None) -> dict:
    """지식 그래프 — 기본은 owner(±domain) 스코프 전체(View L1), ``seed`` 지정 시
    seed+depth ego 서브그래프(View L2 = MCP `vinc_graph` 동일 경로). 노드에는
    결정론 레이아웃 좌표(x,y,z)가 포함된다(D17) — ``layout`` 프리셋은 D34
    (radial·circle·tree·organic·rank·dendro, 미지 값 radial 강등).

    F2: Concept 단일 축 해소 — 축 플랜 3종(Bridge·Episode·Record)이
    합류해 부착 노드·엣지가 렌더된다. L2 에선 매개자(브릿지 등)가 keep 집합에
    편입돼 '보이지 않는 접착제'(kept 개념 둘이 엣지 없이 떠 있던 문제)가 사라진다.
    entity: 시드는 전용 순회(TYPED 폐쇄 성분 — :func:`entity_subgraph_ids_plan`).

    decision: 시드는 시드 칸만 Decision 으로 바꿔 같은 도달 집합 플랜을 탄다(개념
    시드와 같은 모양으로 퍼진다). 의미 관계는 :func:`decision_relation_rows_plan`이
    기존 도달 집합에 한 번 합류한다(새 이웃을 다시 순회하지 않는다).

    ``max_nodes`` — 노드 예산(개념 도달 집합 기준). **주는 호출만** 영향을 받는다:
    도달 집합이 예산을 넘으면 깊이를 층 단위로 줄여(최소 1) 그 층까지만 싣고
    ``depth_budget`` 으로 무엇을 줄였는지 말한다. 층 중간을 자르지 않는 이유는 "몇
    홉까지 다 봤다"는 말이 참으로 남아야 해서다. 주지 않으면(View L2·공유 팩·동기화)
    플랜도 결과도 예산 없는 읽기 그대로다. 예산 값은 MCP 핸들러가 정한다.
    ``select="prize"`` 면 같은 경우에 깊이를 두고 개념을 ppr 로 고른다(:func:`_prize_pick`,
    단위는 같다 — 시드 + 개념 ``max_nodes``). 무엇을 했는지는 ``selection`` 이 말하고,
    노드의 ``ppr_recent`` 도 이때만 붙는다.
    근거: [[decision:vinc-graph-node-budget-lives-in-the-mcp-handler-2026-09-24]]"""
    if seed:
        # SEC-1: seed 갈래도 owner 스코프를 지킨다. 여태 owner 를 받고도 쓰지 않아
        # 호스티드에서 남의 서브그래프가 그대로 보였다(vinc_graph 는 seed 필수).
        # entity 인지는 접두사 라우팅(_seed_table)이 가린다 — 개방층 id 는 임의 접두사(F1).
        seed_label = _seed_table(seed)[0]
        entity_seed = seed_label == "Entity"
        decision_seed = seed_label == "Decision"
        budget: dict | None = None
        if entity_seed:
            ids_res = execute(entity_subgraph_ids_plan(seed, depth=depth, owner=owner),
                              cfg, store=store)
        elif max_nodes and int(depth) > 1:
            ids_res = execute(subgraph_hops_plan(
                seed, depth=depth, owner=owner,
                seed_label="Decision" if decision_seed else "Concept"), cfg, store=store)
        elif decision_seed:
            ids_res = execute(subgraph_ids_plan(seed, depth=depth, owner=owner,
                                                seed_label="Decision"), cfg, store=store)
        else:
            ids_res = execute(subgraph_ids_plan(seed, depth=depth, owner=owner),
                              cfg, store=store)
        if not ids_res.get("executed"):
            return {"kind": "graph", "executed": False, "seed": seed, "depth": depth,
                    "nodes": [], "edges": [], "note": ids_res.get("note", "")}
        reach_rows = [r for r in ids_res.get("rows", []) if r.get("id")]
        # `select="prize"`: 층 자르기가 깊이를 줄일 바로 그 경우(시드 뺀 개념 도달 집합이
        # 예산을 넘을 때)에, 깊이는 두고 개념을 상금(ppr)으로 고른다. 단위는 층 자르기와 같다
        # (시드 + 개념 `max_nodes`). 고른 뒤에는 아래 행 조회·축 합류가 층 자르기와 똑같이
        # 고른 개념에만 돈다 — 부착(레코드·에피소드·브릿지·결정)은 예산 밖이다.
        # 결정 시드는 층 자르기로 간다: 결정↔개념 엣지는 순위 그래프(개념↔개념)에 없고, 시드
        # 자신의 엣지가 고리 전체를 예산 밖에서 다시 싣는다(아래 의미 관계 합류).
        # 개념으로 이어진 후보가 예산 안일 때도 층 자르기로 간다(:func:`_prize_pick`).
        selection: dict | None = None
        prized = False
        if (select == "prize" and max_nodes and int(depth) > 1 and not entity_seed
                and len(reach_rows) > int(max_nodes)):
            picked: list[str] | None = None
            if decision_seed:
                why = "prize selection ranks from concept seeds only"
            elif len(reach_rows) > PRIZE_REACH_CAP:
                why = (f"reach {len(reach_rows)} is over the prize-selection cap of "
                       f"{PRIZE_REACH_CAP}")
            else:
                picked, why = _prize_pick(seed, reach_rows, int(max_nodes), owner=owner,
                                          cfg=cfg, store=store)
            if picked:
                keep_ids = set(picked)
                selection = {"method": "prize", "max_nodes": int(max_nodes),
                             "reach": len(reach_rows), "kept": len(keep_ids - {seed}),
                             "note": why}
                reach_rows = [r for r in reach_rows if r["id"] in keep_ids]
                prized = True
            else:
                selection = {"method": "layers", "requested": "prize",
                             "note": f"{why}, so depth was cut by layer"}
        if max_nodes and int(depth) > 1 and not entity_seed and not prized:
            want = max(1, min(int(depth), 4))
            hops = {r["id"]: int(r.get("hops") or 1) for r in reach_rows}
            served = 1
            for k in range(1, want + 1):
                if sum(1 for h in hops.values() if h <= k) <= int(max_nodes):
                    served = k
                else:
                    break
            if served < want:
                budget = {"requested_depth": want, "served_depth": served,
                          "reach_at_requested": len(hops),
                          "reach_served": sum(1 for h in hops.values() if h <= served),
                          "max_nodes": int(max_nodes)}
                reach_rows = [r for r in reach_rows if hops[r["id"]] <= served]
                depth = served
        ids = sorted({seed, *(r["id"] for r in reach_rows)})
        res = execute(
            entity_rows_for_ids_plan(ids, owner=owner) if entity_seed
            else graph_rows_for_ids_plan(ids, owner=owner), cfg, store=store)
        rows = list(res.get("rows", []))
        keep = set(ids)   # 엣지의 depth+1 유출 대상 제거(ego 경계)
        if not entity_seed:
            # F2 — 접착제 후조회: 축 플랜을 ids 모드로 실행해 매개자(브릿지·에피소드·
            # 레코드)와 그 팔을 합류시키고, 양 끝점을 keep 에 편입한다(매개자 렌더 +
            # episode:/record:/bridge: 시드의 depth-1 이웃). 플랜별 강등이 미마이그레이션
            # 스토어를 자가 처리한다(빈 행 — Concept 축은 산다).
            for plan_fn in _GRAPH_READ_PLANS:
                ax = execute(plan_fn(owner=owner, ids=ids), cfg, store=store)
                if not ax.get("executed"):
                    continue
                ax_rows = ax.get("rows", [])
                rows += ax_rows
                for r in ax_rows:
                    if r.get("id"):
                        keep.add(r["id"])
                    if r.get("target"):
                        keep.add(r["target"])
        shaped = shape_graph(rows)
        shaped["edges"] = [e for e in shaped["edges"] if e["target"] in keep and e["source"] in keep]
        shaped["nodes"] = [n for n in shaped["nodes"] if n["id"] in keep]
        executed = res.get("executed", False)
        found = True
        if not shaped["nodes"]:
            # C4: 순회·행 조회가 둘 다 비었다 — '시드 자체가 없음'과 '시드는 있으나
            # 연결이 0(고립)'이 여태 구분 불가했다(실사용에서 실재 episode 시드가
            # 빈 응답을 받아 '데이터 없음'으로 오판됨). 이 갈래에서만 선조회한다 —
            # Concept/Entity 시드가 실재하면 위 rows 플랜이 이미 자기 자신을 한 행
            # (고립 시 rel/target=NULL)으로 돌려주므로 shaped["nodes"] 가 비지 않고,
            # 부착 있는 episode/record/bridge 시드는 축 플랜(ids 모드)이 잡는다.
            # 여기 도달하는 건 시드가 아예 없거나, Concept 밖 타입이 **부착도 0**
            # 인(진짜 고립) 경우뿐이다.
            seed_res = execute(seed_lookup_plan(seed, owner=owner), cfg, store=store)
            if not seed_res.get("executed"):
                return {"kind": "graph", "executed": False, "seed": seed, "depth": depth,
                        "nodes": [], "edges": [], "note": seed_res.get("note", "")}
            seed_row = next((r for r in seed_res.get("rows", []) if r.get("id") == seed), None)
            found = seed_row is not None
            if found:
                kind = seed.split(":", 1)[0] if ":" in seed else "concept"
                shaped["nodes"] = [{"id": seed, "label": seed_row.get("title") or seed,
                                    "domain": seed_row.get("domain") or "",
                                    "kind": kind, "degree": 0,
                                    "props": _props_to_obj(seed_row.get("props")),
                                    "tags": list(seed_row.get("tags") or []),
                                    "ts": seed_row.get("ts") or "",
                                    "content_ts": seed_row.get("content_ts") or ""}]
        # E-G2 — 시드 관점 개인화 랭킹(ppr). layout 전에 부여(순수 메타, 좌표 무관).
        # `ppr_recent` 는 `prize` 를 요청했을 때만 싣는다(기본 출력은 그대로).
        attach_ppr(shaped, [seed], recent=select == "prize")
        # 브릿지 소속은 파생값이다 — 배치보다 **먼저** 확정해야 hub·rank 가 같은
        # 도메인을 읽는다(_derive_bridge_domains 주석).
        _derive_bridge_domains(shaped)
        _mark_topic_descriptions(shaped, owner, cfg, store)
        # 씨앗이 있는 화면(L2, 한 노드의 이웃)에는 주제 마크를 안 낸다. 거기서의
        # 주어는 그 노드이고 담는 관계는 지도 **전체**를 볼 때의 뼈대라, 초점 화면에
        # 끼우면 ego 이웃이 아닌 것이 이웃인 척한다(테스트 넷이 이걸 잡았다).
        shaped["nodes"] = layout_positions(shaped["nodes"], shaped["edges"], layout,
                                           cone=cone)
        used = _apply_basis(shaped, basis)
        _order_graph_result(shaped)
        if not found:
            cands = near_nodes(store, seed, owner=owner)
            return {"kind": "graph", "executed": executed, "seed": seed, "depth": depth,
                    "found": False, "basis": used,
                    "error": f"unknown seed_id: {seed}",
                    "hint": ("seed_id is an exact, type-prefixed node id "
                             "(e.g. `concept:view`). "
                             + ("Did you mean: " + ", ".join(c["id"] for c in cands)
                                + "? " if cands else "")
                             + "Use vinc_search to find the id first."),
                    **({"candidates": cands} if cands else {}), **shaped}
        return {"kind": "graph", "executed": executed, "seed": seed, "depth": depth,
                "found": found, "basis": used,
                **({"depth_budget": budget} if budget else {}),
                **({"selection": selection} if selection else {}), **shaped}
    doms = normalize_domains(domain, domains)
    res = execute(graph_plan(owner=owner, domains=doms), cfg, store=store)
    rows = list(res.get("rows", []))
    # F2 — 부착 축 합류(L1/전체 뷰). 고정 순서 concat: Concept 축 선행 = 노드 dedup
    # 의 선착 우선이 Concept 투영을 이기게(결정론).
    for plan_fn in _GRAPH_READ_PLANS:
        ax = execute(plan_fn(owner=owner, domains=doms), cfg, store=store)
        if ax.get("executed"):
            rows += ax.get("rows", [])
    shaped = shape_graph(rows)
    _derive_bridge_domains(shaped)
    _mark_topic_descriptions(shaped, owner, cfg, store)
    shaped["nodes"] = layout_positions(shaped["nodes"], shaped["edges"], layout,
                                       topic_marks=True, cone=cone)
    used = _apply_basis(shaped, basis)
    _order_graph_result(shaped)
    return {"kind": "graph", "executed": res.get("executed", False),
            "basis": used, **shaped}


def graph_preview(*, owner, domains=None, layout="hub", cone=1.0, basis="recorded", cfg=None, store=None):
    """A bounded real Concept subgraph, read inside the continuation's transaction.

    This is explicitly partial: no full-population metrics or fabricated edges.
    Other axes arrive with the unchanged final graph response.
    """
    where, params = ["c.owner_id = $owner"], {"owner": owner}
    doms = normalize_domains(None, domains)
    if doms:
        where.append(domain_subtree_where(["c.domain"], doms, params))
    rows = execute(QueryPlan("graph_preview_nodes",
        "MATCH (c:Concept) WHERE " + " AND ".join(where) +
        " RETURN c.concept_id AS id, c.title AS label, c.domain AS domain, "
        "coalesce(c.created, c.updated_at, '') AS ts, "
        "coalesce(c.content_time, '') AS content_ts ORDER BY c.concept_id LIMIT 128",
        params), cfg, store=store)
    if not rows.get("executed"):
        return {"executed": False}
    nodes = list(rows.get("rows", []))
    if not nodes:
        return {"executed": True, "nodes": [], "edges": []}
    ids = pkns.keys(owner, [n["id"] for n in nodes])
    edges = execute(QueryPlan("graph_preview_edges",
        "MATCH (c:Concept)-[r]->(d:Concept) WHERE c.owner_id = $owner AND d.owner_id = $owner "
        "AND c.concept_id IN $ids AND d.concept_id IN $ids "
        "RETURN c.concept_id AS id, d.concept_id AS target, type(r) AS rel "
        "ORDER BY c.concept_id, d.concept_id, type(r) LIMIT 512",
        {"owner": owner, "ids": ids}), cfg, store=store)
    if not edges.get("executed"):
        return {"executed": False}
    shaped = shape_graph(nodes + list(edges.get("rows", [])))
    shaped["nodes"] = layout_positions(shaped["nodes"], shaped["edges"], layout,
                                       topic_marks=False, cone=cone)
    used = _apply_basis(shaped, basis)
    _order_graph_result(shaped)
    return {"kind": "graph_preview", "executed": True, "partial": True, "basis": used, **shaped}


def domain_ties_plan(*, owner: str) -> QueryPlan:
    """View L0 결합 행렬(D16) — 도메인이 다른 Concept 간 직접 엣지를 도메인 쌍으로 집계."""
    cypher = (
        "MATCH (a:Concept)-[r]->(b:Concept) "
        "WHERE a.owner_id = $owner AND b.owner_id = $owner AND a.domain <> b.domain "
        "RETURN a.domain AS source, b.domain AS target, count(*) AS ties "
        "ORDER BY ties DESC"
    )
    return QueryPlan("domain_ties", cypher, {"owner": owner}, note="cross-domain tie matrix (L0)")


def bridge_ties_plan(*, owner: str | None = None) -> QueryPlan:
    """L0 결합 행렬의 Bridge 축 — 실데이터(weg-9000 허브)에서 개념 간 엣지는 전부
    도메인 내부이고 크로스도메인은 :Bridge 가 1급으로 담는다.
    v2(F5)에서 Bridge 가 owner_id 를 얻어 owner 지정 시 과도기 술어로 스코프한다
    (구 '소유 스탬프 없음 → 필터 불가' 제약 해소 — 호스티드 교차테넌트 집계 차단)."""
    where = "b.source_domain IS NOT NULL AND b.target_domain IS NOT NULL"
    params: dict = {}
    if owner:
        where += f" AND {_bridge_owner_pred('b')}"
        params["owner"] = owner
    cypher = (
        "MATCH (b:Bridge) "
        f"WHERE {where} "
        "RETURN b.source_domain AS source, b.target_domain AS target, count(*) AS ties "
        "ORDER BY ties DESC"
    )
    return QueryPlan("bridge_ties", cypher, params, note="bridge tie matrix (L0)")


def direct_coupling_plan(*, owner: str, a: str, b: str) -> QueryPlan:
    """Compare 뷰(D22) — 두 도메인 사이 **개념 대 개념** 직접 크로스도메인 엣지."""
    cypher = (
        "MATCH (x:Concept)-[r]->(y:Concept) "
        "WHERE x.owner_id = $owner AND y.owner_id = $owner "
        "AND ((x.domain = $a AND y.domain = $b) OR (x.domain = $b AND y.domain = $a)) "
        "RETURN x.concept_id AS a_id, x.title AS a_title, x.domain AS a_domain, "
        "y.concept_id AS b_id, y.title AS b_title, type(r) AS rel"
    )
    return QueryPlan("coupling_direct", cypher, {"owner": owner, "a": a, "b": b},
                     note="concept-level direct cross-domain edges")


def bridge_coupling_plan(*, a: str, b: str, owner: str | None = None) -> QueryPlan:
    """Compare 뷰(D22) — Bridge 매개 개념쌍: (cA)←CONNECTS─(Bridge)─CONNECTS→(cB),
    cA.domain=$a · cB.domain=$b. weg-9000 실데이터에서 직접 엣지는 0 이고 결합은
    Bridge 가 담당 — 매개쌍이 주 신호다. owner 지정 시(F5) Concept 양끝은
    엄격, Bridge 는 과도기 술어(:func:`_bridge_owner_pred`)로 스코프한다."""
    where = "x.domain = $a AND y.domain = $b"
    params: dict = {"a": a, "b": b}
    if owner:
        where += (" AND x.owner_id = $owner AND y.owner_id = $owner "
                  f"AND {_bridge_owner_pred('br')}")
        params["owner"] = owner
    cypher = (
        "MATCH (x:Concept)<-[:CONNECTS]-(br:Bridge)-[:CONNECTS]->(y:Concept) "
        f"WHERE {where} "
        "RETURN x.concept_id AS a_id, x.title AS a_title, "
        "y.concept_id AS b_id, y.title AS b_title, "
        "br.bridge_id AS via, br.title AS via_title"
    )
    return QueryPlan("coupling_bridge", cypher, params,
                     note="bridge-mediated concept pairs")


def coupling(*, owner: str, a: str, b: str, cfg=None, store=None) -> dict:
    """두 도메인의 개념 결합쌍(D22) — 직접 엣지 ∪ Bridge 매개. 쌍 단위 dedup,
    직접 결합 우선 정렬."""
    d = execute(direct_coupling_plan(owner=owner, a=a, b=b), cfg, store=store)
    br = execute(bridge_coupling_plan(a=a, b=b, owner=owner), cfg, store=store)
    pairs: dict[tuple[str, str], dict] = {}
    for r in d.get("rows", []):
        # 방향 정규화: a 도메인 쪽을 왼쪽에.
        left_first = r.get("a_domain") == a
        ai, at = (r["a_id"], r["a_title"]) if left_first else (r["b_id"], r["b_title"])
        bi, bt = (r["b_id"], r["b_title"]) if left_first else (r["a_id"], r["a_title"])
        pairs[(ai, bi)] = {"a_id": ai, "a_title": at or ai, "b_id": bi,
                           "b_title": bt or bi, "kind": "direct", "rel": r.get("rel")}
    for r in br.get("rows", []):
        key = (r["a_id"], r["b_id"])
        if key not in pairs:   # 직접 결합이 매개보다 강한 신호 — 우선
            pairs[key] = {"a_id": r["a_id"], "a_title": r.get("a_title") or r["a_id"],
                          "b_id": r["b_id"], "b_title": r.get("b_title") or r["b_id"],
                          "kind": "bridge", "via": r.get("via"),
                          "via_title": r.get("via_title")}
    out = sorted(pairs.values(), key=lambda p: (p["kind"] != "direct", p["a_title"]))
    return {"executed": bool(d.get("executed") or br.get("executed")),
            "a": a, "b": b, "pairs": out}


def concept_domains_plan(*, owner: str) -> QueryPlan:
    """L0 도메인 집계의 Concept 축 — Episode 없는 코퍼스(개념 위주 허브, 예:
    weg-9000 적재분)에서도 L0 가 그려져야 한다."""
    cypher = (
        "MATCH (c:Concept) WHERE c.owner_id = $owner AND c.domain IS NOT NULL "
        "RETURN c.domain AS domain, count(*) AS records ORDER BY records DESC"
    )
    return QueryPlan("concept_domains", cypher, {"owner": owner}, note="concept counts per domain (L0)")


def view_l0(*, owner: str, cfg=None, store=None) -> dict:
    """View L0 Domain View 데이터(D16) — 도메인별 레코드 수(Episode ∪ Concept
    합산) + 도메인 간 결합 행렬(Concept 직접 엣지 + Bridge 합산). UI 는 이 응답만으로
    L0 를 그린다(그래프 로직 UI 반입 금지)."""
    d = execute(domains_plan(owner=owner), cfg, store=store)
    c = execute(concept_domains_plan(owner=owner), cfg, store=store)
    counts: dict[str, int] = {}
    for row in d.get("rows", []):
        counts[row["domain"]] = counts.get(row["domain"], 0) + int(row.get("episodes") or 0)
    for row in c.get("rows", []):
        counts[row["domain"]] = counts.get(row["domain"], 0) + int(row.get("records") or 0)
    domains = [{"domain": k, "episodes": v} for k, v in
               sorted(counts.items(), key=lambda kv: -kv[1])]
    t = execute(domain_ties_plan(owner=owner), cfg, store=store)
    b = execute(bridge_ties_plan(owner=owner), cfg, store=store)
    merged: dict[tuple[str, str], int] = {}
    for row in [*t.get("rows", []), *b.get("rows", [])]:
        key = (row.get("source") or "", row.get("target") or "")
        if key[0] and key[1]:
            merged[key] = merged.get(key, 0) + int(row.get("ties") or 0)
    ties = [{"source": s, "target": tg, "ties": n}
            for (s, tg), n in sorted(merged.items(), key=lambda kv: -kv[1])]
    return {"executed": bool(d.get("executed") or c.get("executed")),
            "domains": domains, "ties": ties}


# ── 원장 롤업(D43) — 출처 × 주제 × 연결상태 ──────────────────────────────────
# View Totals 관점의 데이터 원천. 두 플랜(출처 축·연결 축)을 각각 UNION ALL 로
# 가져와 **순수 파이썬에서 접는다**(`domain_counts_plan` → `domains` 와 동형) —
# Cypher 안에서 EXISTS{}·서브쿼리로 영리하게 굴지 않는다(임베디드 방언 이식성).
# 고아 여부를 `component_diagnosis` 에 맡기지 않는 것도 의도다 — 진단은 스토어를
# 두 번 더 읽고 강등 계약을 갖는데, 원장은 항상 그려져야 한다.

def ledger_origin_plan(*, owner: str, domain: str | None = None, domains: list[str] | None = None,
                       ids: list[str] | None = None) -> QueryPlan:
    """원장 출처 축 — 개념 전량('all') + 에피소드 부착('episodic') + 문서 부착
    ('record': 구 Record ATTACHES_TO **또는** D45 문서의 Chunk MENTIONS) 표식 행. 한 개념이 여러 에피소드에 부착되면 같은 표식이 여러 번
    오지만 접기(:func:`ledger`)가 불리언이라 무해하다.

    owner 규칙(SEC-1): **엣지 패턴의 양 끝을 모두** 필터한다 — 소유자 스코프 id
    집합만으론 반대편 끝이 안전해지지 않는다. Episode/Record/Concept 는 엄격
    ``= $owner``. 모든 UNION 분기가 ``$owner`` 를 바인딩한다(Kùzu 는 미사용
    파라미터를 거부 — §6 방언). ``domain``/``ids`` 는 절을 낼 때만 params 에 넣고,
    같은 이유로 낸 절은 **모든 분기에** 붙는다.

    ``ids``(D43 ego 원장): 개념 c 를 시드 주변 집합으로 제한한다 — ``domain`` 렌즈와
    상호 배제(축 플랜 관례). 부착 주체(Episode/Record)는 집합으로 제한하지 않는다 —
    부착은 전역 사실이라 ego 밖 에피소드가 ego 개념에 부착돼도 출처다."""
    doms = normalize_domains(domain, domains)
    assert not (doms and ids), "domain and ids are mutually exclusive"
    params: dict = {"owner": owner}
    and_scope = ""
    if doms:
        # 원장은 지도와 **같은 모집단**을 세야 한다 — 지표바가 지도와 다른 수를
        # 말하면 화면이 스스로를 반박한다. 그래서 같은 하위 트리 조건을 쓴다.
        and_scope = " AND " + domain_subtree_where(["c.domain"], doms, params)
    if ids:
        and_scope = " AND c.concept_id IN $ids"
        params["ids"] = pkns.keys(owner, ids)
    cypher = (
        f"MATCH (c:Concept) WHERE c.owner_id = $owner{and_scope} "
        "RETURN c.concept_id AS id, c.domain AS domain, "
        "coalesce(c.created, c.updated_at, '') AS ts, 'all' AS mark "
        "UNION ALL "
        "MATCH (e:Episode)-[:ABOUT]->(c:Concept) "
        f"WHERE e.owner_id = $owner AND c.owner_id = $owner{and_scope} "
        "RETURN c.concept_id AS id, c.domain AS domain, '' AS ts, 'episodic' AS mark "
        "UNION ALL "
        "MATCH (rc:Record)-[:ATTACHES_TO]->(c:Concept) "
        f"WHERE rc.owner_id = $owner AND c.owner_id = $owner{and_scope} "
        "RETURN c.concept_id AS id, c.domain AS domain, '' AS ts, 'record' AS mark "
        # D54 — D45 문서(Chunk=SSoT)도 '파일에서'로 센다. 문서를 넣는 경로가 둘인데
        # (구 Record + 신 _DocMeta/Chunk) 원장이 구 축만 보고 있어, 앱에서 문서를
        # 넣거나 팩에 documents 를 실어 와도 출처가 "없음"으로 남는다(개념 74개가
        # 전부 unsourced 로 보였다). 같은 'record' 칸에 접는다 — 화면 문구가 "파일에서"라
        # 두 물리 표현이 같은 뜻이고, 새 칸을 만들면 카피·범례가 함께 늘어난다.
        "UNION ALL "
        "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) "
        f"WHERE ch.owner_id = $owner AND c.owner_id = $owner{and_scope} "
        "RETURN c.concept_id AS id, c.domain AS domain, '' AS ts, 'record' AS mark"
    )
    return QueryPlan("ledger", cypher, params, note="ledger origin axis (concept × source)")


def ledger_link_plan(*, owner: str, domain: str | None = None, domains: list[str] | None = None,
                     ids: list[str] | None = None) -> QueryPlan:
    """원장 연결 축 — 개념 대 개념 직접 엣지('cc') + Bridge 매개 팔('bridge') 행.
    각 행이 차수 1 을 뜻하고, ``domain``/``other`` 가 다르면 그 개념은 관문
    (gateway)이다. 무방향 ``-[r]-`` 라 한 엣지가 양쪽 끝에서 각각 한 번 세어진다.

    owner 규칙: Concept 양끝 엄격. **Bridge 만 예외** — 과도기 술어
    (:func:`_bridge_owner_pred`, ``owner OR ''``)를 쓴다. 엄격 등호는 소유
    없는 행(v2 이전)을 가진 기존 로컬 사용자의 결합을 조용히 소거하는 회귀다.
    owner_id 열이 없는 Domain/Chunk/SkillExecution 은 롤업에 합류시키지 않는다.

    ``ids``(D43 ego 원장): 제한은 **c 쪽에만** 건다 — 먼 끝점 o 는 owner 스코프만
    유지하고 ego 로 좁히지 않는다(전역 차수·관문: 엣지가 ego 를 떠나도 그 구성원의
    연결이다). domain 렌즈가 o 를 안 건드리는 기존 태도와 동형이라 SEC-1 양끝
    스코프는 그대로다. ``domain`` 과 상호 배제, 모든 UNION 분기에 절을 붙인다."""
    doms = normalize_domains(domain, domains)
    assert not (doms and ids), "domain and ids are mutually exclusive"
    params: dict = {"owner": owner}
    and_scope = ""
    if doms:
        # 원장은 지도와 **같은 모집단**을 세야 한다 — 지표바가 지도와 다른 수를
        # 말하면 화면이 스스로를 반박한다. 그래서 같은 하위 트리 조건을 쓴다.
        and_scope = " AND " + domain_subtree_where(["c.domain"], doms, params)
    if ids:
        and_scope = " AND c.concept_id IN $ids"
        params["ids"] = pkns.keys(owner, ids)
    cypher = (
        "MATCH (c:Concept)-[r]-(o:Concept) "
        "WHERE c.owner_id = $owner AND o.owner_id = $owner "
        f"AND o.concept_id <> c.concept_id{and_scope} "
        "RETURN c.concept_id AS id, c.domain AS domain, o.domain AS other, 'cc' AS mark "
        "UNION ALL "
        "MATCH (b:Bridge)-[:CONNECTS]->(c:Concept) "
        f"WHERE {_bridge_owner_pred('b')} AND c.owner_id = $owner{and_scope} "
        "RETURN c.concept_id AS id, b.source_domain AS domain, "
        "b.target_domain AS other, 'bridge' AS mark"
    )
    return QueryPlan("ledger", cypher, params, note="ledger link axis (concept × coupling)")


def _ledger_ego_ids(seed: str, *, owner: str, depth: int = 2,
                    cfg=None, store=None) -> dict:
    """ego 원장(D43)의 모집단 — 시드 주변 Concept id 집합. L2 서브그래프와 **같은
    순회 기계**를 재사용해 두 관점의 모집단이 일치한다(:func:`graph` 시드 갈래와
    동형):

    - Concept 시드: :func:`subgraph_ids_plan`(depth 1..4 클램프, TRAIL 가드) 도달
      개념 ∪ 시드 자신(graph() 997 의 시드 합류와 동일).
    - episode:/record:/bridge: 시드: 순회가 :Concept 미매치라 0행 — 축 플랜(ids
      모드)의 부착 끝점(target = Concept id)만 모은다. L2 keep 집합의 Concept
      부분집합과 동일. 플랜별 강등은 graph() 와 같은 태도로 건너뛴다(미마이그레이션
      스토어 자가 처리 — 순회가 이미 스토어 도달을 증명했다).
    - entity: 시드(미지 접두사 포함): TYPED 폐쇄 성분이라 Concept 층에 구조적으로
      닿지 않는다 — 항상 빈 ego.

    성공 시 ``{"executed": True, "ids": [...]}``, 순회 강등 시 execute 딕셔너리를
    그대로 돌려준다(호출부가 응답 전체를 강등)."""
    kind = _seed_table(seed)[0]
    if kind == "Entity":
        return {"executed": True, "ids": []}
    res = execute(subgraph_ids_plan(seed, depth=depth, owner=owner), cfg, store=store)
    if not res.get("executed"):
        return res
    if kind == "Concept":
        return {"executed": True,
                "ids": sorted({seed, *(r["id"] for r in res.get("rows", []) if r.get("id"))})}
    members: set[str] = set()
    for plan_fn in _GRAPH_AXIS_PLANS:
        ax = execute(plan_fn(owner=owner, ids=[seed]), cfg, store=store)
        if not ax.get("executed"):
            continue
        # 부착 축은 결정에도 닿는다(F2). 원장 모집단은 개념이라 개념 끝점만 모은다.
        members.update(r["target"] for r in ax.get("rows", [])
                       if r.get("target") and r.get("target_kind", "concept") == "concept")
    return {"executed": True, "ids": sorted(members)}


def _mark_topic_descriptions(shaped: dict, owner: str, cfg=None, store=None) -> None:
    """주제 설명 미러에 ``topic_description: true`` 를 단다. 화면은 이 표시로 그것을 지도·트리·수에서
    뺀다(설명 자체는 주제 상세가 보여 준다). 표시만 달고 노드를 빼지 않는다: 문서 읽기와 다른
    소비자는 그대로 받는다."""
    if not shaped.get("nodes") or not owner:
        return
    ids = _topic_description_mirrors_by_domain(owner, cfg, store)
    if not ids:
        return
    for n in shaped["nodes"]:
        if n.get("id") in ids:
            n["topic_description"] = True
