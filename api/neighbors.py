# -*- coding: utf-8 -*-
"""한 홉 관측: 노드 하나의 1-hop 이웃(`neighbors`)과 저장된 엣지(`node_edges`).

`neighbors()` 는 의미층 이웃(`neighbors_plan`), Entity 의 TYPED 이웃(`entity_neighbors_plan`), 부착 축
(`node_attachments_plan`: Record·Episode·Bridge, 양방향), Record 끼리의 대체 축(`record_supersedes_plan`)을
합쳐 (rel, id) 순서로 낸다. `domain:` 좌표는 `_domain_neighbors` 가 같은 모양으로 낸다. 이웃이 없으면
`seed_lookup_plan` 으로 노드가 있는지 물어 "없는 노드"와 "이웃이 없는 노드"를 가른다.
`node_edges()` 는 배치 없이 저장된 방향·근거·신뢰도를 스키마의 관계 표에서 읽는다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.X` 와 `from api.query import X` 는 그대로다.
두 이름은 같은 객체다. 여기 함수가 부르는 이름(`execute`·`seed_lookup_plan`·`near_nodes`·계획 함수)은
이 모듈에서 찾으므로, `neighbors()` 안의 호출을 바꾸려면 `api.query` 가 아니라 이 모듈의 이름을 패치한다.

이 모듈은 `api.query` 를 import 하지 않는다. 바닥은 `api/query_base.py`, 존재 조회 계획은
`api/graph_read.py` 에서 읽는다(`tests/core/test_neighbors.py` 가 둘을 본다).
"""
from __future__ import annotations

from engines import domain_hierarchy
from store import pkns
from store.backend import open_store
from store.config import load_config
from store.querybase import _VALIDITY_EMPTY, _validity_cols

from .graph_read import seed_lookup_plan
from .query_base import (
    QueryPlan,
    _FRAGMENT_EDGE_TO_REL,
    _SEMANTIC_LABELS,
    _SEMANTIC_PK,
    _bridge_owner_pred,
    _seed_table,
    _track_cols,
    execute,
    near_nodes,
)


# ── 단일 홉 관측 창구 (E-G5) — SoG Observe-then-Navigate 경량 프리미티브 ────────
# 리서치 SoG(Search-on-Graph)의 "단일 홉 관측 창구"를 경량 MCP 표면으로 제공한다.
# 상류 모델이 depth-2 ego 전체(vinc_graph 최소 단위) 대신 한 홉씩 관측·항해하게 해
# 토큰을 아낀다(-69.9% 근거). 조회 전용 — 개념의 1-hop 관계망을 rel 타입별로 요약.

def neighbors_plan(node: str, *, owner: str | None = None) -> QueryPlan:
    """개념의 1-hop 이웃(무방향) — rel 타입·이웃 id/title/domain. `type(r)`(표준 함수,
    임베디드 어댑터가 `label(r)` 로 바꾼다). owner 지정 시 양 끝 스코프.

    ⚠ 이 플랜은 **의미층끼리만** 본다(개념·결정). 부착 축(Record·Episode·Bridge)은
    :func:`node_attachments_plan` 이 따로 낸다 — 아래 그 함수의 주석 참조.

    v12 — 시드 라벨은 id 접두사에서 꺼내고(`_seed_table`), 상대편은 개념·결정을 모두
    본다. 라벨을 문자열로 박아 두면 결정에서 출발한 관측이 **빈 결과**가 되고, 그건
    "이웃이 없다"와 구분되지 않는다."""
    params: dict = {"node": pkns.key(owner, node)}
    s_label, s_pk = _seed_table(node)
    if s_label not in _SEMANTIC_LABELS:
        s_label, s_pk = "Concept", "concept_id"   # 하위호환: 접두사 없는 id 는 개념
    n_where, m_where = f"n.{s_pk} = $node", ""
    if owner:
        n_where += " AND n.owner_id = $owner"
        m_where = " AND m.owner_id = $owner"
        params["owner"] = owner
    # R2 — `one_liner` 은 **Concept 에만** 있는 열이다. UNION 의 두 팔이 같은 모양을
    # 가져야 하므로 Decision 팔은 리터럴로 채운다: 열이 없는 라벨에 `m.one_liner` 를
    # 쓰면 Binder 가 죽고, 그러면 이웃 조회 전체가 죽는다(이 파일이 pinned·
    # content_time 에서 이미 두 번 겪은 자리).
    # F15 — 방향을 싣는다. 저장 방향마다 한 팔이고(out: 시드가 출발, in: 시드가 도착),
    # 같은 상대·같은 관계가 양쪽에 다 있으면 neighbors() 가 "both" 로 합친다.
    cypher = " UNION ALL ".join(
        f"MATCH {pat} WHERE {n_where}{m_where} "
        f"RETURN type(r) AS rel, '{d}' AS dir, m.{m_pk} AS id, m.title AS title, "
        "m.domain AS domain, "
        + (f"coalesce(m.one_liner, '') AS one_liner"
           if m_label == "Concept" else "'' AS one_liner")
        # W3 — 의미층 둘 다 추적 라벨이라 리터럴 맞춤 없이 그대로 뽑는다.
        + ", " + _track_cols("m")
        for m_label, m_pk in _SEMANTIC_PK.items()
        for pat, d in ((f"(n:{s_label})-[r]->(m:{m_label})", "out"),
                       (f"(n:{s_label})<-[r]-(m:{m_label})", "in")))
    return QueryPlan("neighbors", cypher, params, note="single-hop observation (SoG)")


def _typed_neighbors_cypher(owner: str | None) -> str:
    """Entity 시드의 TYPED 이웃(양방향). 저작 타입은 `subtype` 으로 낸다. Entity 에는
    도메인·한줄 설명 열이 없어 리터럴로 맞춘다. 근거: 감사 F12."""
    n_scope = " AND n.owner_id = $owner" if owner else ""
    m_scope = " AND m.owner_id = $owner" if owner else ""
    return " UNION ALL ".join(
        f"MATCH {pat} WHERE n.entity_id = $node{n_scope}{m_scope} "
        f"RETURN 'TYPED' AS rel, r.type AS subtype, '{d}' AS dir, m.entity_id AS id, "
        "m.title AS title, '' AS domain, '' AS one_liner, " + _track_cols("m")
        for pat, d in (("(n:Entity)-[r:TYPED]->(m:Entity)", "out"),
                       ("(n:Entity)<-[r:TYPED]-(m:Entity)", "in")))


def entity_neighbors_plan(node: str, *, owner: str | None = None) -> QueryPlan:
    """Entity 시드의 1-hop(TYPED, 양방향). :func:`neighbors_plan` 은 시드를 개념으로
    접어 Entity 를 못 본다 — 이것이 없을 때 개방 타입 노드는 관계가 있어도 이웃 0개였다."""
    params: dict = {"node": pkns.key(owner, node)}
    if owner:
        params["owner"] = owner
    return QueryPlan("entity_neighbors", _typed_neighbors_cypher(owner), params,
                     note="entity TYPED neighbours (F12)")


#: 부착 축의 라벨 → (PK 열, 엣지 이름, `domain` 자리에 쓸 열).
#: Bridge 는 `domain` 열이 없어 `source_domain` 을 쓴다 — 라벨마다 열이 달라서,
#: 다른 축의 투영을 그대로 옮겨 붙이면 Binder 로 죽는다.
_ATTACH_AXIS: dict[str, tuple[str, str, str]] = {
    "Record": ("record_id", "ATTACHES_TO", "domain"),
    "Episode": ("episode_id", "ABOUT", "domain"),
    "Bridge": ("bridge_id", "CONNECTS", "source_domain"),
}


def node_attachments_plan(node: str, *, owner: str | None = None) -> QueryPlan:
    """부착 축의 1-hop — Record(ATTACHES_TO)·Episode(ABOUT)·Bridge(CONNECTS). F14.

    **양방향이다.** 시드가 개념·결정이면 거기 붙은 Record·Episode·Bridge 를 내고,
    시드가 그 셋 중 하나이면 그것이 붙어 있는 개념·결정을 낸다.

    이게 왜 따로 필요한가: :func:`neighbors_plan` 의 패턴은 양 끝이 의미층이라
    다른 라벨이 **필터가 아니라 매치 모양에서** 빠진다. 그래서 이 플랜만 쓰는 `vinc_neighbors` 는
    `concept:vinc-mcp` 의 1-hop 이웃 15개 중 7개(RELATED_TO)만 본다.
    빠진 8개가 하필 **사실이 사는 곳**이다 — Record(pitfall·fact) 6 · Episode 1 ·
    Bridge 1. 도구 설명은 "graph 대신 neighbors 로 한 홉씩 걸으라"고 권하므로,
    그 조언을 따른 모델은 Record 를 영영 못 본다. 무음 누락이다(F6 과 같은 계열).

    역방향이 이어서 필요한 이유: 부착 축을 한쪽으로만 풀면
    **Record·Episode 시드는 관계가 있는데도 이웃 0개**를 낸다 — 그러면 응답이
    "관계 없음"과 구분되지 않고, 그 hint("아직 관계가 없다")까지 붙는다.
    같은 무음 누락의 반대편이다(도구 계약은 [[vinc.canon.mcp-contract]]).

    UNION 은 열 타입이 같아야 하므로 rel·id·title·domain 에 W3 의 추적열 둘,
    W13 의 유효구간 둘을 더한 여덟 열만 투영한다 — 추적열은 셋 다 추적 라벨이라
    모양이 맞고, 유효구간은 Record 에만 있어 나머지 두 팔을 리터럴로 맞춘다
    (한 팔이라도 빠뜨리면 UNION 전체가 Binder 로 죽는다)."""
    params: dict = {"node": pkns.key(owner, node)}
    s_label, _s_pk = _seed_table(node)
    if owner:
        params["owner"] = owner
    if s_label in _ATTACH_AXIS:
        return QueryPlan("node_attachments", _attach_reverse_cypher(s_label, owner),
                         params, note="attachment axis, reverse (F14)")
    # v12 — 부착 **대상**도 의미층 둘 중 하나다(결정에도 기록·사건·다리가 붙는다).
    # 라벨을 박아 두면 결정의 부착이 통째로 안 보인다.
    c_label, c_pk = (s_label, _s_pk) if s_label in _SEMANTIC_LABELS else ("Concept", "concept_id")
    c_scope = " AND c.owner_id = $owner" if owner else ""
    parts = []
    for label, (pk, rel, dom) in _ATTACH_AXIS.items():
        # 부착 노드의 owner: Record·Episode 는 엄격, Bridge 는 과도기 술어(축 플랜 관례).
        a_scope = ""
        if owner:
            a_scope = (f" AND {_bridge_owner_pred('a')}" if label == "Bridge"
                       else " AND a.owner_id = $owner")
        # F15 — 부착 엣지는 부착 노드에서 시드로 온다. 시드 쪽에서 보면 in 이다.
        parts.append(f"MATCH (a:{label})-[:{rel}]->(c:{c_label}) "
                     f"WHERE c.{c_pk} = $node{c_scope}{a_scope} "
                     f"RETURN '{rel}' AS rel, 'in' AS dir, a.{pk} AS id, a.title AS title, "
                     f"a.{dom} AS domain, " + _track_cols("a") + ", "
                     # W13 — 유효구간은 Record 팔만 실열, 나머지는 자리 맞춤.
                     + (_validity_cols("a") if label == "Record" else _VALIDITY_EMPTY))
    return QueryPlan("node_attachments", " UNION ALL ".join(parts), params,
                     note="record/episode/bridge attachments (F14)")


def record_supersedes_plan(node: str, *, owner: str | None = None) -> QueryPlan:
    """Record 시드의 SUPERSEDES 이웃(양방향). 방향은 `vinc_edges` 가 준다."""
    params: dict = {"node": pkns.key(owner, node)}
    scope = ""
    if owner:
        params["owner"] = owner
        scope = " AND a.owner_id = $owner AND m.owner_id = $owner"
    arms = [f"MATCH {pat} WHERE a.record_id = $node{scope} "
            f"RETURN 'SUPERSEDES' AS rel, '{d}' AS dir, m.record_id AS id, m.title AS title, "
            "m.domain AS domain, '' AS one_liner, " + _track_cols("m") + ", "
            + _validity_cols("m")
            for pat, d in (("(a:Record)-[:SUPERSEDES]->(m:Record)", "out"),
                           ("(m:Record)-[:SUPERSEDES]->(a:Record)", "in"))]
    return QueryPlan("record_supersedes", " UNION ALL ".join(arms), params,
                     note="record supersession axis")


def _attach_reverse_cypher(s_label: str, owner: str | None) -> str:
    """부착 노드(Record·Episode·Bridge) 시드 → 그것이 붙어 있는 개념·결정.

    시드 쪽 owner 술어는 라벨 규칙 그대로(Bridge 만 과도기), 반대쪽은 엄격이다."""
    s_pk, rel, _dom = _ATTACH_AXIS[s_label]
    s_scope = ""
    if owner:
        s_scope = (f" AND {_bridge_owner_pred('a')}" if s_label == "Bridge"
                   else " AND a.owner_id = $owner")
    m_scope = " AND m.owner_id = $owner" if owner else ""
    return " UNION ALL ".join(
        f"MATCH (a:{s_label})-[:{rel}]->(m:{m_label}) "
        f"WHERE a.{s_pk} = $node{s_scope}{m_scope} "
        f"RETURN '{rel}' AS rel, 'out' AS dir, m.{m_pk} AS id, m.title AS title, "
        "m.domain AS domain, "
        + ("coalesce(m.one_liner, '') AS one_liner"
           if m_label == "Concept" else "'' AS one_liner")
        + ", " + _track_cols("m")            # W3
        for m_label, m_pk in _SEMANTIC_PK.items())


def _domain_neighbors(node: str, *, owner: str | None, limit: int | None,
                      cfg=None, store=None) -> dict:
    """`domain:` 좌표의 1-hop (E-1). :func:`neighbors` 와 **같은 반환 모양**이라
    상류가 분기 없이 그대로 걸어 내려갈 수 있다.

    ⚠ ``owner`` 없이는 **실행하지 않는다**(fail-closed). 도메인 노드에는 소유가
    없어서, 소유 술어가 빠지면 자식 목록이 남의 하위 도메인 이름을 그대로 흘린다.
    다른 조회는 owner=None 이 "로컬 단일 사용자"라 무해하지만 여기는 아니다."""
    domain = domain_hierarchy.parse_id(node)
    base = {"kind": "neighbors", "executed": False, "node": node,
            "neighbors": [], "by_rel": {}, "count": 0}
    if not owner:
        base.update(executed=True, found=False,
                    error="owner required — 도메인 좌표는 무소유 공유 구조라 "
                          "스코프 없이 펼치면 남의 하위 도메인이 노출된다")
        return base
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base
    try:
        params = {"domain": domain, "owner": owner}
        members = store.run(domain_hierarchy.members_cypher(), params) or []
        kids = store.run(domain_hierarchy.child_domains_cypher(), params) or []
        # W3 — 여기 행에는 `rev`/`updated_at` 이 없다. 도메인은 추적 라벨이 아니고
        # (`_TRACK_COLS` 밖), 멤버 행은 `engines/domain_hierarchy.py` 의 질의에서
        # 오므로 이 패키지의 투영 범위(api/) 밖이다. 없는 것은 결손이지
        # 사실이 아니므로 0 으로 채우지 않는다 — 지어낸 버전이 제일 나쁘다.
        # F15 — 방향: 자식 주제는 이 좌표에서 나가고(PARENT_OF), 멤버는 들어온다(IN_DOMAIN).
        rows = [{"rel": "PARENT_OF", "dir": "out", "id": domain_hierarchy.make_id(c),
                 "title": c, "domain": domain}
                for c in sorted({r["child"] for r in kids if r.get("child")})]
        rows += [{"rel": "IN_DOMAIN", "dir": "in", "id": r.get("id"),
                  "title": r.get("title") or r.get("id"),
                  "domain": r.get("domain") or ""}
                 for r in members if r.get("id")]
        rows.sort(key=lambda r: (str(r["rel"]), str(r["id"])))
        by_rel: dict[str, int] = {}
        for r in rows:
            by_rel[r["rel"]] = by_rel.get(r["rel"], 0) + 1
        out = {"kind": "neighbors", "executed": True, "found": bool(rows),
               "node": node, "neighbors": rows if limit is None else rows[:max(1, int(limit))],
               "by_rel": by_rel, "count": len(rows)}
        if not rows:
            out["hint"] = (f"No nodes of yours are under {domain!r}. Domains come "
                           "from the `domain` field on your nodes — set it (e.g. "
                           f"'{domain}/subtopic') and the hierarchy follows.")
        return out
    finally:
        if owns:
            store.close()


def neighbors(node: str, *, owner: str | None = None, limit: int = 50,
              cfg=None, store=None) -> dict:
    """E-G5 — 개념의 1-hop 관계망 요약(조회 전용). 반환 {executed, node,
    neighbors:[{rel,dir,id,title,domain,one_liner,rev,updated_at,valid_from,valid_until}],
    by_rel:{rel:count}, count}. 결정론 순서
    (rel, id). 상류 SoG 항해의 관측 표면.

    ``dir`` 은 저장된 방향이다: out(시드가 출발), in(시드가 도착), both(같은 관계가
    양쪽에 다 있다 — 한 줄로 센다). Entity(개방 타입) 시드는 TYPED 관계를 내고,
    그 행의 ``subtype`` 이 저작된 타입이다(감사 F12·F15).

    ``node`` 가 ``domain:`` 접두면 **도메인 좌표**를 잡는다(E-1) — 자식 도메인
    (`PARENT_OF`)과 거기 직접 속한 노드(`IN_DOMAIN`)를 같은 모양으로 낸다. 그래서
    `domain:vinc` 에서 시작해 한 홉씩 아래로 걸어 내려갈 수 있다."""
    if domain_hierarchy.parse_id(node):
        return _domain_neighbors(node, owner=owner, limit=limit, cfg=cfg, store=store)
    entity_seed = _seed_table(node)[0] == "Entity"
    # F12 — Entity 시드는 TYPED 만 갖는다. 아래 두 플랜은 시드를 개념으로 접어 0행을 낸다.
    res = execute(entity_neighbors_plan(node, owner=owner) if entity_seed
                  else neighbors_plan(node, owner=owner), cfg, store=store)
    if not res.get("executed"):
        return {"kind": "neighbors", "executed": False, "node": node,
                "neighbors": [], "by_rel": {}, "count": 0, "note": res.get("note", "")}
    rows = list(res.get("rows", []))
    if not entity_seed:
        # F14 — 부착 축(Record·Episode·Bridge)을 합류시킨다. 이게 없을 때 이 도구는
        # 개념끼리의 RELATED_TO 만 보여 줬고, 사실이 사는 Record 는 통째로 안 보였다.
        # 부착 축이 못 뜨더라도(구 스토어·확장 부재) 개념 축 결과는 그대로 낸다.
        att = execute(node_attachments_plan(node, owner=owner), cfg, store=store)
        rows += list(att.get("rows", [])) if att.get("executed") else []
    if node.startswith("record:"):
        # 대체 축 — Record 끼리의 SUPERSEDES. 위 두 플랜은 양 끝 중 하나가 의미층이라
        # Record→Record 를 모양에서 놓친다. 이게 없으면 대체된 기록에서 후속을 못 걷는다.
        sup = execute(record_supersedes_plan(node, owner=owner), cfg, store=store)
        if sup.get("executed"):
            rows += list(sup.get("rows", []))
    seen: dict = {}
    out: list[dict] = []
    by_rel: dict[str, int] = {}
    for r in sorted(rows, key=lambda r: (str(r.get("rel")), str(r.get("id")),
                                         str(r.get("subtype") or ""), str(r.get("dir") or ""))):
        # 같은 관계·같은 상대는 한 줄이다(count 의 뜻은 그대로). 양쪽 방향이 다 있으면
        # 그 줄의 dir 이 both 가 된다. TYPED 는 저작 타입이 다르면 다른 관계다.
        key = (r.get("rel"), r.get("id"), r.get("subtype") or "")
        if not r.get("id"):
            continue
        if key in seen:
            row = seen[key]
            if r.get("dir") and row.get("dir") != r.get("dir"):
                row["dir"] = "both"
            continue
        row = {"rel": r.get("rel"), "dir": r.get("dir") or "", "id": r.get("id"),
               "title": r.get("title") or r.get("id"), "domain": r.get("domain") or "",
               # R2 — Concept 만 이 열을 갖는다. 부착 축(Record·Episode·Bridge)은
               # 키 자체가 없고, 그 경우 빈 문자열은 결손이 아니라 사실이다.
               "one_liner": r.get("one_liner") or "",
               # W3 — 걸어가다 만난 노드의 버전. `domain:` 좌표 응답만 이 키가
               # 없다(도메인은 추적 라벨이 아니다 — 위 _domain_neighbors).
               "rev": r.get("rev") or 0,
               "updated_at": r.get("updated_at") or "",
               # W13 — Record 부착만 값을 갖는다(열이 Record 전용 — 부착 축
               # 정방향 팔만 싣고, 의미층·역방향 행은 키가 없어 '' 로 남는다.
               # 그 빈 문자열은 결손이 아니라 사실이다).
               "valid_from": r.get("valid_from") or "",
               "valid_until": r.get("valid_until") or "",
               **({"subtype": r["subtype"]} if r.get("subtype") else {})}
        seen[key] = row
        out.append(row)
        by_rel[r.get("rel")] = by_rel.get(r.get("rel"), 0) + 1
    if not out:
        # F7 — 여태 여기가 `{executed: true, count: 0}` 뿐이었다. **없는 노드**와
        # **이웃이 없는 노드**가 응답으로 구분되지 않았다. 둘을 가른다.
        #
        # ⚠ 그 구분을 `near_nodes`(제목·props 유사도 검색)로 했었다. 유사도는 존재의
        # 증거가 아니다: 슬러그 id 는 제 산문 제목 안에 글자 그대로 나타나는 일이 드물어
        # (`episode:2026-08-07-mcp-hardening-f1-f2` 같은 것) 후보가 0이 되고, 그러면
        # **실재하는 노드가 `unknown node` 로 보고됐다.** 부착 엣지가 하나라도 있으면
        # 위 축 플랜이 잡아 주므로 증상은 **엣지 0인 Episode·Record·Bridge** 에만 났고,
        # 그래서 오래 안 보였다. 정본: [[record:neighbors-unknown-node-for-edgeless-attachments-2026-08-28]].
        #
        # 존재는 PK 로 묻는다. `seed_lookup_plan` 이 정확히 그 조회이고 — 여섯 테이블을
        # 접두사로 가르고, PK 네임스페이싱과 owner 스코프(Bridge 과도기 술어 포함)를
        # 이미 처리한다 — `graph()` 는 같은 갈래에서 진작 그것을 쓰고 있었다(1895행).
        # `neighbors()` 만 안 받았다. `near_nodes` 는 "혹시 이것을 찾으셨나요"에만 남긴다.
        probe = execute(seed_lookup_plan(node, owner=owner), cfg, store=store)
        if probe.get("executed"):
            exact = any(r.get("id") == node for r in probe.get("rows", []))
            # 없을 때만 후보를 뽑는다 — 있으면 쓸 데가 없고, 유사도 검색은 공짜가 아니다.
            cands = [] if exact else near_nodes(store, node, owner=owner)
        else:
            # 존재 조회 실패는 유사도 후보 유무로 대체할 수 없다.
            return {"kind": "neighbors", "executed": False, "node": node,
                    "neighbors": [], "by_rel": {}, "count": 0,
                    "note": probe.get("note") or "existence lookup unavailable"}
        return {"kind": "neighbors", "executed": True, "found": exact, "node": node,
                "neighbors": [], "by_rel": {}, "count": 0,
                "error": None if exact else f"unknown node: {node}",
                "hint": ("This node exists but has no relations yet — "
                         "vinc_suggest_ties proposes candidates, vinc_ingest adds edges."
                         if exact else
                         "Node ids are exact and type-prefixed (e.g. `concept:view`). "
                         + ("Did you mean: " + ", ".join(c["id"] for c in cands) + "? "
                            if cands else "")
                         + "Use vinc_search to find the id first."),
                **({"candidates": cands} if cands and not exact else {})}
    _lim = max(1, int(limit))
    # F6 — `count` 가 전체 수라 비교로 알아낼 수는 있었지만, 그건 받는 쪽이 **먼저
    # 의심해야** 성립한다. 잘렸다는 사실은 명시로 말한다.
    return {"kind": "neighbors", "executed": True, "found": True, "node": node,
            "neighbors": out[:_lim], "by_rel": by_rel, "count": len(out),
            "neighbors_truncated": len(out) > _lim}


def node_edges(node: str | None = None, *, nodes: list[str] | None = None,
               owner: str | None = None, type: str | None = None,
               dir: str = "both", limit: int = 500, cfg=None, store=None) -> dict:
    """그래프 배치 없이 저장된 방향·근거·신뢰도를 읽는다.

    끝점 키와 관계 열은 스키마를 따른다. 양쪽 저작 노드의 소유자를 검사하고,
    전역 Domain은 그 소유자의 노드와 조상에 연결된 범위만 보여준다.
    조회가 없는 노드나 엣지를 만들어 내지 않는다.
    근거: [[vinc.spec.dataplane §5]] · [[vinc.canon.mcp-contract]].
    """
    from store.embedded_schema import node_table_specs, rel_table_specs

    base = {"kind": "edges", "executed": False, "edges": [], "count": 0}
    ids = [node] if node is not None else nodes
    if (not owner or (node is not None and nodes is not None)
            or not isinstance(ids, list) or not 1 <= len(ids) <= 100
            or any(not isinstance(n, str) or not n.strip() or "\x1f" in n for n in ids)
            or dir not in ("out", "in", "both")
            or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000
            or (type is not None and (not isinstance(type, str) or not type.strip()))):
        return {**base, "error": "invalid argument: provide owner, either node or 1..100 nodes, "
                "dir out/in/both, and limit 1..5000"}
    ids = list(dict.fromkeys(ids))
    # `type` 은 물리 rel 라벨(대문자, `PREREQUISITE_OF`)과 같아야 라벨 필터로 걸린다.
    # 사용자는 문서화된 소문자 이름(`prerequisite_of`)을 넣으므로 그대로는 어느 라벨과도
    # 같지 않아 0건이 된다(`relates_to` 는 `.upper()` 로도 `RELATED_TO` 가 안 되므로
    # delete_edge 와 같은 맵으로 정규화한다). 맵에 없는 이름은 대문자가 폴백이라
    # `connects`·`attaches_to`·`typed` 도 라벨로 맞는다. 저작 서브타입(ATTACHES_TO.type·
    # TYPED.type)은 아래 `r.type = $type` 가 원문 그대로 거르므로 이 정규화는 라벨
    # 비교에만 쓴다. 근거: [[vinc.manual.core]]
    rel_name = (_FRAGMENT_EDGE_TO_REL.get(type.strip().lower(), type.strip().upper())
                if type else None)
    specs, rels = node_table_specs(), rel_table_specs()
    grouped: dict[str, list[str]] = {}
    for nid in ids:
        domain = domain_hierarchy.parse_id(nid)
        label, _ = ("Domain", "name") if domain else _seed_table(nid)
        grouped.setdefault(label, []).append(domain if domain else pkns.key(owner, nid))
    params = {"owner": owner, **{label: values for label, values in grouped.items()}}
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        return {**base, "note": "no reachable store"}

    def read(cypher, args):
        return execute(QueryPlan("edges", cypher, args), cfg, store=store)

    try:
        visible: set[str] = set()
        if "Domain" in grouped:
            # Domain은 무소유 좌표다. 소유한 노드를 기준으로 범위를 좁혀
            # 전역 루트 아래 다른 소유자의 하위 주제를 노출하지 않는다.
            arms = [f"MATCH (n:{r['from']})-[:{r['label']}]->(d:Domain) "
                    "WHERE n.owner_id = $owner RETURN d.name AS domain"
                    for r in rels if r["to"] == "Domain"
                    and "owner_id" in specs[r["from"]]["cols"]]
            res = read(" UNION ALL ".join(arms), {"owner": owner})
            if not res["executed"]:
                return {**base, "error": res.get("error"), "note": res.get("note", "")}
            for row in res["rows"]:
                parts = str(row["domain"]).split("/")
                visible.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        params["visible"] = sorted(visible)
        probes = []
        for label in grouped:
            pk = specs[label]["pk"]
            scope = "n.name IN $visible" if label == "Domain" else "n.owner_id = $owner"
            probes.append(f"MATCH (n:{label}) WHERE n.{pk} IN ${label} AND {scope} "
                          f"RETURN n.{pk} AS id, '{label}' AS label")
        res = read(" UNION ALL ".join(probes), params)
        if not res["executed"]:
            return {**base, "error": res.get("error"), "note": res.get("note", "")}
        found = {"domain:" + r["id"] if r["label"] == "Domain" else pkns.strip(r["id"])
                 for r in res["rows"]}
        missing = [nid for nid in ids if nid not in found]
        arms = []
        for rel in rels:
            src, dst, label = rel["from"], rel["to"], rel["label"]
            # type은 물리 관계 표, subtype은 TYPED·ATTACHES_TO에 저장된 저작 타입이다.
            if type and rel_name != label and "type" not in rel["props"]:
                continue
            incident = []
            if dir in ("out", "both") and src in grouped:
                incident.append(f"a.{specs[src]['pk']} IN ${src}")
            if dir in ("in", "both") and dst in grouped:
                incident.append(f"b.{specs[dst]['pk']} IN ${dst}")
            if not incident:
                continue
            guards = ["(" + " OR ".join(incident) + ")"]
            for alias, endpoint in (("a", src), ("b", dst)):
                if "owner_id" in specs[endpoint]["cols"]:
                    guards.append(f"{alias}.owner_id = $owner")
                elif endpoint == "Domain" and src == dst == "Domain":
                    guards.append(f"{alias}.name IN $visible")
            if type and rel_name != label:
                guards.append("r.type = $type")
                params["type"] = type
            evidence = "r.evidence" if "evidence" in rel["props"] else "CAST(NULL AS STRING)"
            confidence = "r.confidence" if "confidence" in rel["props"] else "CAST(NULL AS DOUBLE)"
            subtype = "r.type" if "type" in rel["props"] else "CAST(NULL AS STRING)"
            # F15 — 엣지 출생 시각과 CONNECTS 의 역할. 어느 읽기 도구도 내지 않던 값이다.
            created = "r.created" if "created" in rel["props"] else "CAST(NULL AS STRING)"
            role = "r.role" if "role" in rel["props"] else "CAST(NULL AS STRING)"
            arms.append(f"MATCH (a:{src})-[r:{label}]->(b:{dst}) WHERE "
                        + " AND ".join(guards)
                        + f" RETURN a.{specs[src]['pk']} AS source, b.{specs[dst]['pk']} AS target, "
                        f"'{src}' AS source_label, '{dst}' AS target_label, '{label}' AS type, "
                        f"{evidence} AS evidence, {confidence} AS confidence, {subtype} AS subtype, "
                        f"{created} AS created, {role} AS role")
        res = read(" UNION ALL ".join(arms), params) if arms else {"executed": True, "rows": []}
        if not res["executed"]:
            return {**base, "error": res.get("error"), "note": res.get("note", "")}
        edges = [{"from": "domain:" + r["source"] if r["source_label"] == "Domain"
                  else pkns.strip(r["source"]),
                  "to": "domain:" + r["target"] if r["target_label"] == "Domain"
                  else pkns.strip(r["target"]), "type": r["type"],
                  "evidence": r["evidence"], "confidence": r["confidence"],
                  **({"subtype": r["subtype"]} if r["subtype"] else {}),
                  **({"role": r["role"]} if r["role"] else {}),
                  # 출생 시각은 저장된 엣지에만 있다. 파생 엣지(IN_DOMAIN 등)는 열이 없어
                  # 키를 달지 않는다(subtype·role 과 같은 규칙).
                  **({"created": r["created"]} if r["created"] else {}),
                  "derived": r["type"] in ("IN_DOMAIN", "PARENT_OF", "MENTIONS")}
                 for r in res["rows"]]
        edges.sort(key=lambda e: (e["from"], e["to"], e["type"],
                                  str(e.get("subtype", "")), str(e["evidence"]),
                                  str(e["confidence"])))
        from .belief_contract import annotate
        annotate(edges[:limit], store=store, owner=owner)
        return {**base, "executed": True, "nodes": ids, "missing_nodes": missing,
                **({"node": node, "found": node in found} if node is not None else {}),
                "edges": edges[:limit], "count": len(edges),
                "edges_truncated": len(edges) > limit}
    finally:
        if owns:
            store.close()
