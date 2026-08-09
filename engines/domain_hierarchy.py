# -*- coding: utf-8 -*-
"""도메인 계층(E-1) — 이름 문자열에서 그래프 구조를 **파생**한다.

도메인은 여태 노드의 `domain` 문자열 열로만 있었다. `vinc/design` 의 `/` 가 계층을
뜻했지만 그건 이름 규약일 뿐이라 그래프로는 못 따라갔다. v11 의 `PARENT_OF` ·
`IN_DOMAIN` 이 그 규약을 실제 엣지로 편다.

**저작이 아니라 파생이다.** 사용자는 지금처럼 `domain` 필드에 문자열만 적는다.
`내프로젝트/하위` 라고 적으면 계층이 저절로 생긴다 — 엣지를 따로 그릴 필요가 없다.
그래서 GRAPH.md 의 저작 엣지 여섯 종은 그대로다(어휘를 늘리지 않는다).

## 소유 모델 — 도메인 노드는 공유 좌표, 소유는 엣지가 진다

`Domain` 테이블은 열이 `name` 하나뿐이라 **소유 데이터를 담지 않는다**. 그래서
전역 공유가 성립하고, PK(`name`)를 바꾸는 무거운 이관도 필요 없다.
`Bridge-[CROSSES]->Domain` 이 이미 그 모양으로 돌고 있다.

⚠ 그 대가로 지켜야 할 불변식이 하나 있다:

    **조상은 안전하고, 자손은 반드시 소유로 걸러야 한다.**

`vinc/design` 을 가진 사람에게 조상 `vinc` 를 보여주는 것은 아무것도 흘리지 않는다 —
자기 문자열에서 스스로 계산할 수 있는 값이다. 반대로 `vinc` 의 **자손**을 그냥
펼치면 남이 만든 `vinc/그사람의비밀과제` 가 딸려 나온다. `PARENT_OF` 만 타고
내려가는 질의를 절대 그대로 노출하지 말 것 — :func:`subtree_scoped_cypher` 처럼
소유 스코프 노드(`IN_DOMAIN` 의 출발점)에 **먼저** 앵커를 걸어야 한다.
"""
from __future__ import annotations

SEP = "/"

#: 전이 질의의 깊이 상한. 무한 확장(`*`)은 순환이 생기면 안 끝나고, 계층은
#: 이름 세그먼트 수라 실무상 이보다 깊어질 이유가 없다.
MAX_DEPTH = 8

#: `domain` 열을 가진 라벨 → PK 열. `IN_DOMAIN` 의 FROM 쌍과 일치해야 한다
#: (store/embedded_schema._REL_TABLES). Bridge 는 domain 열이 없다
#: (source_domain/target_domain + CROSSES) — 여기 들지 않는다.
DOMAIN_LABELS: dict[str, str] = {
    "Concept": "concept_id",
    "Record": "record_id",
    "Episode": "episode_id",
}


def normalize(domain: str | None) -> str:
    """도메인 문자열 정규화. 빈 값이면 ``''``.

    빈 세그먼트를 접고 앞뒤 공백을 턴다(``' vinc // design '`` → ``'vinc/design'``).
    **대소문자는 건드리지 않는다** — `domain` 열이 그 문자열의 정본이고 Domain 노드
    이름이 그것과 정확히 같아야 하는데, 접으면 어느 표기를 열에 남길지가 정해지지
    않아 열과 노드가 갈린다. 쓰기 경로는 이 함수의 결과를 **열에도** 넣는다.
    """
    if not domain:
        return ""
    parts = [p.strip() for p in str(domain).split(SEP)]
    return SEP.join(p for p in parts if p)


def parse_chain(domain: str | None) -> list[str]:
    """정규화된 도메인의 **조상 사슬**(루트부터 자신까지).

    ``'vinc/design/tokens'`` → ``['vinc', 'vinc/design', 'vinc/design/tokens']``
    빈 값이면 ``[]``.
    """
    norm = normalize(domain)
    if not norm:
        return []
    segs = norm.split(SEP)
    return [SEP.join(segs[: i + 1]) for i in range(len(segs))]


def ensure_chain(store, domain: str | None) -> list[str]:
    """도메인 노드와 `PARENT_OF` 사슬을 멱등 MERGE. 반환은 사슬(루트→자신).

    ``store`` 는 ``run(cypher, params)`` 만 쓰므로 GraphStore·세션 어느 쪽이든 된다."""
    chain = parse_chain(domain)
    for name in chain:
        store.run("MERGE (:Domain {name: $n})", {"n": name})
    for parent, child in zip(chain, chain[1:]):
        store.run("MATCH (p:Domain {name: $p}), (c:Domain {name: $c}) "
                  "MERGE (p)-[:PARENT_OF]->(c)", {"p": parent, "c": child})
    return chain


def link_node(store, label: str, node_id: str, domain: str | None) -> str:
    """노드의 `IN_DOMAIN` 을 ``domain`` **하나로** 맞춘다. 반환은 정규화된 도메인.

    `domain` 열이 단일값이므로 엣지도 단일값이어야 한다. 그래서 기존 엣지를 먼저
    지운다 — 안 지우면 도메인을 옮긴 노드가 옛 도메인과 새 도메인 **양쪽**에
    나타나고, 그건 조회에서 조용히 틀린 답이 된다.

    도메인이 비면 엣지만 지운다(도메인 해제). 알 수 없는 라벨은 무동작."""
    pk = DOMAIN_LABELS.get(label)
    if not pk:
        return ""
    store.run(f"MATCH (n:{label} {{{pk}: $id}})-[r:IN_DOMAIN]->(:Domain) DELETE r",
              {"id": node_id})
    norm = normalize(domain)
    if not norm:
        return ""
    ensure_chain(store, norm)
    store.run(f"MATCH (n:{label} {{{pk}: $id}}), (d:Domain {{name: $d}}) "
              "MERGE (n)-[:IN_DOMAIN]->(d)", {"id": node_id, "d": norm})
    return norm


#: 도메인을 **주소**로 잡는 id 접두. 노드 id 가 아니라 좌표 이름이라 접두가 필요하다
#: (`vinc` 라는 개념 노드와 `vinc` 라는 도메인은 다른 것이다).
ID_PREFIX = "domain:"


def parse_id(node_id: str | None) -> str | None:
    """``'domain:vinc/design'`` → ``'vinc/design'``. 도메인 주소가 아니면 ``None``.

    빈 이름(``'domain:'``)도 ``None`` — 주소가 아니다."""
    if not node_id or not str(node_id).startswith(ID_PREFIX):
        return None
    return normalize(str(node_id)[len(ID_PREFIX):]) or None


def make_id(domain: str) -> str:
    return ID_PREFIX + normalize(domain)


def _union_all(parts: list[str]) -> str:
    return " UNION ALL ".join(parts)


def members_cypher() -> str:
    """``$domain`` 에 **직접** 속한 ``$owner`` 의 노드(세 라벨 합집합)."""
    return _union_all([
        f"MATCH (:Domain {{name: $domain}})<-[:IN_DOMAIN]-(n:{lbl}) "
        f"WHERE n.owner_id = $owner "
        f"RETURN 'IN_DOMAIN' AS rel, n.{pk} AS id, n.title AS title, n.domain AS domain"
        for lbl, pk in DOMAIN_LABELS.items()])


def child_domains_cypher(*, depth: int = MAX_DEPTH) -> str:
    """``$domain`` 의 **직계 자식** 중 ``$owner`` 의 노드를 하위에 가진 것만.

    ⚠ 소유 조건이 이 질의의 존재 이유다. 자식을 그냥 나열하면 남이 만든
    `vinc/그사람의과제` 가 딸려 나온다(모듈 독스트링의 불변식). 자식의 **하위 전체**를
    보는 이유는 노드가 손자 도메인에만 있어도 그 자식은 보여야 하기 때문이다."""
    return _union_all([
        f"MATCH (:Domain {{name: $domain}})-[:PARENT_OF]->(c:Domain)"
        f"-[:PARENT_OF*0..{depth}]->(:Domain)<-[:IN_DOMAIN]-(n:{lbl}) "
        f"WHERE n.owner_id = $owner RETURN c.name AS child"
        for lbl in DOMAIN_LABELS])


def backfill(store, *, owner: str | None = None, rename: dict | None = None) -> dict:
    """기존 노드의 `domain` 열에서 계층을 **한 번에** 파생한다(v11 이행).

    v11 이전에 쓰인 노드는 열만 있고 엣지가 없다. 쓰기 경로가 앞으로는 유지하지만
    이미 있는 것은 여기서 채운다. **멱등**이라 몇 번 돌려도 같다.

    ``rename``(선택): ``{옛 도메인: 새 도메인}``. 열과 엣지를 같이 옮긴다. 평면
    도메인을 계층 아래로 넣을 때 쓴다(`design` → `vinc/design`). revstamp 는 찍지
    **않는다** — 이행은 내용 변경이 아니라 표현 보정이고, 366 노드에 스탬프를 찍으면
    모든 기기가 전량을 다시 받는다. 사용자 주도 개명은 `query.rename_domain` 이다.

    반환 ``{scanned, linked, renamed, domains, edges, by_label}``.

    ⚠ **도메인 단위 집합 연산**이지 노드 단위 순회가 아니다. 부팅 경로에서 도는
    코드라 비용이 노드 수에 비례하면 큰 그래프가 기동 프로브를 넘긴다(366 노드 기준
    노드 단위 2.58s → 집합 단위 0.1s 대, 실측 2026-08-09). 질의 수는 노드가 아니라
    **서로 다른 도메인 값**에 비례한다."""
    rename = rename or {}
    out: dict = {"scanned": 0, "linked": 0, "renamed": 0, "by_label": {}}
    for label, pk in DOMAIN_LABELS.items():
        where = "WHERE n.domain IS NOT NULL AND n.domain <> ''"
        params: dict = {}
        if owner:
            where += " AND n.owner_id = $owner"
            params["owner"] = owner
        groups = store.run(f"MATCH (n:{label}) {where} "
                           "RETURN n.domain AS domain, count(n) AS n", params) or []
        n_link = 0
        for g in groups:
            cur = g.get("domain") or ""
            cnt = int(g.get("n") or 0)
            out["scanned"] += cnt
            target = normalize(rename.get(cur, cur))
            if not target:
                continue
            scope = " AND n.owner_id = $owner" if owner else ""
            if target != cur:
                store.run(f"MATCH (n:{label}) WHERE n.domain = $old{scope} "
                          "SET n.domain = $new",
                          {**params, "old": cur, "new": target})
                out["renamed"] += cnt
            ensure_chain(store, target)
            # 옛 엣지 제거 후 재연결 — 개명한 경우 옛 좌표를 가리키는 엣지가 남는다.
            store.run(f"MATCH (n:{label})-[r:IN_DOMAIN]->(:Domain) "
                      f"WHERE n.domain = $d{scope} DELETE r", {**params, "d": target})
            store.run(f"MATCH (n:{label}), (d:Domain {{name: $d}}) "
                      f"WHERE n.domain = $d{scope} MERGE (n)-[:IN_DOMAIN]->(d)",
                      {**params, "d": target})
            n_link += cnt
        if n_link:
            out["by_label"][label] = n_link
        out["linked"] += n_link
    edges = store.run("MATCH ()-[r:IN_DOMAIN]->() RETURN count(r) AS c") or []
    out["edges"] = int(edges[0]["c"]) if edges else 0
    out["domains"] = len(store.run("MATCH (d:Domain)<-[:IN_DOMAIN]-() "
                                   "RETURN DISTINCT d.name AS name") or [])
    return out


def subtree_scoped_cypher(label: str, *, depth: int = MAX_DEPTH) -> str:
    """``$domain`` 하위 전체에서 ``$owner`` 의 ``label`` 노드를 모으는 Cypher.

    소유 스코프를 **노드 쪽**에 건다(위 불변식). 도메인 노드에는 소유가 없으므로
    도메인에서 출발해 내려가면 남의 하위 도메인이 딸려 온다."""
    pk = DOMAIN_LABELS[label]
    return (f"MATCH (root:Domain {{name: $domain}})-[:PARENT_OF*0..{depth}]->(d:Domain)"
            f"<-[:IN_DOMAIN]-(n:{label}) WHERE n.owner_id = $owner "
            f"RETURN n.{pk} AS id, n.title AS title, d.name AS domain")
