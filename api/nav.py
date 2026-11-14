# -*- coding: utf-8 -*-
"""보기 화면이 눌러 들어가는 읽기: 관계 선의 근거, 좌측 문서 트리, 기간 항목, 원장과 그 드릴다운.

흐름 뷰에서 선을 누르면 `edge_context` 가 그 관계의 근거 구절과 양끝 노드의 발췌를 읽는다. 좌측 트리는
`nav_tree` 가 주제마다 문서를 개념 계층 깊이순(`_domain_depths`)으로 세운다. 타임라인 기간 브러시는
`docs_in_range` 가, 원장 롤업과 구간 클릭은 `ledger`·`ledger_drilldown` 이 읽는다. 원장 두 읽기는 개념별 분류
`_ledger_classify` 하나를 같이 쓰고, 그 분류는 `graph_read` 의 원장 계획과 ego 순회로 읽는다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.nav_tree`·`q.X` 와 `from api.query import X` 는
그대로다. 두 이름은 같은 객체다. 여기 함수가 부르는 이름(`execute`·`open_store`·`_ledger_classify` 등)은 이
모듈에서 찾으므로, 그 호출을 바꾸려면 `api.query` 가 아니라 이 모듈의 이름을 패치한다.

이 모듈은 `api.query` 를 import 하지 않는다(`tests/core/test_nav_module.py` 가 본다).
"""
from __future__ import annotations

from store import pkns
from store.backend import open_store
from store.config import load_config
from store.querybase import QueryPlan, domain_subtree_where, execute
from vinc_rag import excerpt as _excerpt

from .graph_read import _ledger_ego_ids, ledger_link_plan, ledger_origin_plan
from .query_base import BASIS_CONTENT, BASIS_RECORDED, _GRAPH_READ_PAIRS, _SEMANTIC_PK, normalize_domains


#: 주제 아래 설 수 있는 종 **전부**. `_SEMANTIC_PK` 보다 넓다 — 좌측 트리의 종 레일이
#: 기록·사건까지 세우기 때문이다. Bridge 와 개방 타입(Entity)이 빠진 것은 취향이 아니라
#: 스키마다: `IN_DOMAIN` 이 이 넷에만 걸리고(store/embedded_schema.py), Bridge 는
#: `CROSSES` 로 Domain 에 붙어 source_domain/target_domain 을 따로 갖는다(그래서
#: fragment ingest 가 Bridge 의 domain 을 저장하지 않는다고 경고한다). 개방 타입은
#: domain 을 아예 보존하지 않는다. **없는 줄을 레일에 세우지 않으려면 이 목록이 정본이다.**
_TREE_PK: dict[str, str] = {
    "Concept": "concept_id", "Decision": "decision_id",
    "Record": "record_id", "Episode": "episode_id",
}
#: 화면이 보내는 종 이름 → 라벨. 소문자 단수형이 `NavNode.kind` 와 같은 값이다.
_TREE_KIND_LABEL: dict[str, str] = {lbl.lower(): lbl for lbl in _TREE_PK}


# edge_context 허용 관계 — 흐름 뷰가 선으로 그리는 5종. etype 은 Cypher 에
# 보간되므로 **화이트리스트 검증이 보안 경계다**(임의 라벨 주입 차단).
# ATTACHES_TO 도 v16 부터 evidence 열을 갖지만 여기 없는 것은 의도다 — 부착은
# 흐름 뷰의 선이 아니라 상세 패널 카드로 그려지고, 그 근거는 node_context 의
# links.items[].evidence_text 가 이미 나른다. 선으로 승격할 때 함께 열 것.
_EDGE_CTX_TYPES = ("PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM",
                   "RELATED_TO", "ABOUT")


def edge_context(*, owner: str, src: str, dst: str, etype: str,
                 cfg=None, store=None) -> dict:
    """엣지→정본 컨텍스트(D45-V 1단계) — 흐름 뷰에서 선(관계)을 눌렀을 때
    "왜 A 가 B 로 이어지는가"에 답한다. 조회 전용.

    반환: 관계의 evidence(원문 구절)·confidence + 양끝 노드 메타(title·one_liner/
    summary)와 대표 정본 발췌 1건씩(MENTIONS 첫 건 — 개념·결정; Episode 는
    summary가 곧 설명). RELATED_TO는 무방향 매치(저장 방향 무관)다.
    관계별 끝점 조합은 graph·node_context와 같은 스키마 파생 읽기 계약을 따른다.
    evidence 가 빈 관계도 executed=True 로 돌려준다 — UI 는 Empty 상태로 그린다
    (클릭 불가 대신 "기록된 근거 없음"을 말한다, 조용한 실패 금지)."""
    cfg = cfg or load_config()
    base: dict = {"kind": "edge-context", "executed": False,
                  "src": {"id": src}, "dst": {"id": dst}, "type": etype}
    if etype not in _EDGE_CTX_TYPES:
        base["error"] = f"unsupported edge type: {etype}"
        return base
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        arrow = "-" if etype == "RELATED_TO" else "->"
        arms = []
        # 스키마 파생 끝점 계약을 공유한다. Decision에는 one_liner 열이 없다.
        for (sl, sp, dl, dp), rels in _GRAPH_READ_PAIRS.items():
            if etype not in rels:
                continue
            def detail(alias: str, label: str) -> str:
                column = {"Concept": "one_liner", "Episode": "summary"}.get(label)
                return f"{alias}.{column}" if column else "''"
            arms.append(
                f"MATCH (a:{sl})-[r:{etype}]{arrow}(b:{dl}) "
                f"WHERE a.{sp} = $s AND b.{dp} = $d "
                "AND a.owner_id = $owner AND b.owner_id = $owner "
                "RETURN r.evidence AS ev, r.confidence AS cf, "
                f"a.title AS at, {detail('a', sl)} AS ad, "
                f"b.title AS bt, {detail('b', dl)} AS bd, "
                f"'{sl}' AS src_label, '{dl}' AS dst_label")
        rows = store.run(" UNION ALL ".join(arms),
                         {"s": pkns.key(owner, src), "d": pkns.key(owner, dst),
                          "owner": owner}) or []
        if not rows:
            base["note"] = "unknown edge (또는 소유 스코프 밖)"
            return base
        r = rows[0]
        def _mention_excerpt(cid: str, label: str, title: str, detail: str) -> dict | None:
            if label not in _SEMANTIC_PK:
                return None
            m = store.run(
                f"MATCH (ch:Chunk)-[m:MENTIONS]->(c:{label}) "
                f"WHERE c.{_SEMANTIC_PK[label]} = $id AND c.owner_id = $owner "
                "AND (ch.owner_id = $owner OR ch.owner_id = '') "
                "RETURN ch.doc_id AS doc_id, ch.text AS text "
                "ORDER BY CASE m.method WHEN 'title' THEN 0 WHEN 'stem' THEN 1 ELSE 2 END, "
                "ch.doc_id, ch.chunk_index LIMIT 1",
                {"id": pkns.key(owner, cid), "owner": owner}) or []
            if not m:
                return None
            return {"doc_id": m[0].get("doc_id") or "",
                    "excerpt": _excerpt.excerpt(m[0].get("text") or "", title, detail)}
        base.update(
            executed=True,
            evidence=r.get("ev") or "",
            confidence=float(r.get("cf") or 0.0),
            src={"id": src, "title": r.get("at") or src, "one_liner": r.get("ad") or "",
                 "excerpt": _mention_excerpt(src, r["src_label"], r.get("at") or "",
                                             r.get("ad") or "")},
            dst={"id": dst, "title": r.get("bt") or dst, "one_liner": r.get("bd") or "",
                 "excerpt": _mention_excerpt(dst, r["dst_label"], r.get("bt") or "",
                                             r.get("bd") or "")})
        return base
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
        return base
    finally:
        if owns:
            store.close()


# ── 좌측 문서 트리(D69) ──────────────────────────────────────────────────────
# 깊이 = "이 주제 안에서 얼마나 안쪽 개념인가". 방향 관계 2종만 쓴다:
#   PREREQUISITE_OF(a→b) : a 를 먼저 알아야 b — a 가 **얕다**(그대로 적재)
#   DERIVES_FROM(a→b)    : a 는 b 에서 파생 — b 가 **얕다**(뒤집어 적재)
# 뒤집기를 빠뜨리면 트리 전체가 조용히 뒤집힌다(잎이 위로 온다) — 테스트로 못박음.
# RELATED_TO 는 대칭이라 깊이에 관여하지 않는다.
_DEPTH_CAP = 64          # 사이클·병리 구조의 상한. 화면이 무한 들여쓰기 되지 않게.


def _domain_depths(concepts: dict[str, str],
                   edges: list[tuple[str, str]]) -> dict[str, int]:
    """도메인 내 개념 → 깊이. 순수 함수(스토어 무관·결정론).

    ``concepts``: {concept_id: domain} — 한 도메인 분만 넘긴다.
    ``edges``: (얕은 쪽, 깊은 쪽) 로 **이미 정규화된** 같은 도메인 엣지.

    루트 = 들어오는 엣지가 없는 개념(엣지가 하나도 없는 개념 포함). 거기서 BFS 로
    층을 매긴다. ⚠ Kahn 위상 정렬을 쓰지 않는 이유: 순수 사이클 성분은 진입차수가
    0 인 노드가 없어 **영원히 안 벗겨진다**. BFS 는 visited 집합이 있어 사이클이
    있어도 반드시 끝나고, 그러고도 안 닿은 노드(= 사이클에만 속한 개념)는 맨
    아래 층에 id 순으로 둔다. 없는 계층을 지어내지 않으면서 목록에서 빠지지도
    않게 하는 타협이다."""
    out: dict[str, int] = {}
    children: dict[str, list[str]] = {}
    indeg: dict[str, int] = {cid: 0 for cid in concepts}
    for shallow, deep in edges:
        if shallow not in concepts or deep not in concepts or shallow == deep:
            continue
        children.setdefault(shallow, []).append(deep)
        indeg[deep] = indeg.get(deep, 0) + 1
    frontier = sorted(cid for cid in concepts if indeg.get(cid, 0) == 0)
    depth = 0
    while frontier and depth < _DEPTH_CAP:
        nxt: set[str] = set()
        for cid in frontier:
            if cid in out:
                continue
            out[cid] = depth
            nxt.update(children.get(cid, ()))
        frontier = sorted(c for c in nxt if c not in out)
        depth += 1
    if len(out) < len(concepts):        # 사이클에만 속한 나머지 — 맨 아래 한 층에.
        floor = min(max(out.values()) + 1 if out else 0, _DEPTH_CAP)
        for cid in sorted(concepts):
            out.setdefault(cid, floor)
    return out


def nav_tree(*, owner: str, nodes: bool = False, kind: str | None = None,
             domain: str | None = None, domains: list[str] | None = None,
             cfg=None, store=None) -> dict:
    """좌측 문서 트리(D69) — 주제 → 그 주제의 문서, **개념 계층 깊이순**.

    문서는 주제 열을 갖지 않으므로(`_DocMeta`, D45) 주제도 착지 노드도 전부
    언급(`MENTIONS`)에서 유도한다 — `docstore.mention_tally` 를 그대로 쓴다.
    `모든 문서` 목록과 트리가 같은 문서를 다른 주제에 걸지 않게 원천이 하나다.

    반환::

        {executed, domains: [{domain, concepts, docs: [
            {doc_id, title, node_id, node_title, depth, updated_at}]}],
         unfiled: [{doc_id, title, updated_at}], counts: {...}}

    - 문서 정렬: `(depth, title, doc_id)` — 얕은 개념의 문서가 위.
    - 개념은 있는데 문서가 없는 주제도 **빈 그룹으로 싣는다**(주제가 목록에서
      사라지면 이름을 바꾸거나 문서를 붙일 방법이 없어진다).
    - 언급이 하나도 없는 문서는 `unfiled` — 없는 주제를 지어내지 않는다(D45-V).

    ``nodes=True`` 면 주제마다 ``nodes: [{id, title, kind, depth}]`` 를 함께 싣는다
    (좌측 트리의 노드 갈래). **기본은 False 이고 그때 응답은 종전과 글자까지 같다** —
    제목은 문서보다 수가 훨씬 많고(개념 1400 대 문서 215 인 그래프가 실재한다) 이
    응답은 웹에서 허브를 거치므로, 안 쓰는 화면까지 그 무게를 지게 두지 않는다.

노드 모집단의 기본은 위 ``concepts`` 수를 세는 것과 **같은 집합**이다(`_SEMANTIC_PK`,
    즉 Concept 과 Decision). 주제 머리가 "개념 N개"라고 말하면 그 갈래에 정확히 N줄이
    있어야 한다 — 다른 집합을 세면 화면이 스스로를 반박한다.

    ``kind`` 를 주면 모집단이 **그 종 하나**가 된다(`concept|decision|record|episode`).
    그때 각 주제에 ``count`` 가 함께 실리고 그것이 그 갈래의 줄 수다 — 위 계약을 깨지
    않고 지키는 방법이 이것이다: 세는 집합과 그리는 집합이 여전히 같고, 무엇을 세는지가
    인자로 드러난다. ``concepts`` 는 뜻이 바뀌지 않는다(늘 Concept + Decision).

    **종을 통째로 싣는 인자는 없다.** 기록이 이 그래프에서 가장 큰 종이고 이 응답은
    웹에서 계정 프록시를 건너므로, 화면은 레일에서 고른 한 종만 가져간다. 레일이
    개수를 알기 위해 필요한 것은 목록이 아니라 집계뿐이라 그것은 ``counts.kinds``
    (종별 수)가 따로 준다 — 쿼리 하나이고 제목을 싣지 않는다.

    ⚠ 주제 아래 설 수 있는 종은 **넷뿐이다**(`_TREE_PK`). Bridge 와 개방 타입은
    스키마가 `IN_DOMAIN` 을 주지 않는다 — 레일에 그 줄을 세우면 영원히 빈 갈래가 된다.

    ``domain``/``domains`` 는 모집단을 그 **하위 트리**로 좁힌다(정확일치 OR `d + "/"`
    접두 — 판정은 `domain_subtree_where` 한 벌이다). 목록·개수·문서·미분류가 **같은
    술어**를 지난다: 세는 집합과 그리는 집합이 어긋나면 화면이 스스로를 반박한다.
    스코프가 걸리면 `unfiled`(주제 없는 문서)는 비는 것이 정답이다 — 주제 스코프에
    주제 없는 문서는 들지 않는다. 응답은 `counts.scope` 로 무엇으로 좁혔는지 에코한다.

    ⚠ 이 인자가 없던 동안 좌측 레일의 Agent 갈래는 **아무것도 거르지 못했다**:
    `lnb.ts` 가 `domain: "vinc/agent/roles"` 를 선언해도 실어 보낼 자리가 없어
    모든 Concept 이 그 갈래에 섰다(실측 2026-09-20, 예약 주제 밖 `vinc/agentic` 과
    무관한 `work` 까지 6/6 이 나왔다). 근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §3]].
    """
    from api import docstore as _ds

    cfg = cfg or load_config()
    base: dict = {"kind": "nav-tree", "executed": False, "domains": [], "unfiled": []}
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = "no reachable store"
        return base
    want = _TREE_KIND_LABEL.get((kind or "").strip().lower()) if kind else None
    if kind and not want:
        base["note"] = f"unknown kind: {kind}"
        return base
    # 주제 스코프 — 목록·개수·문서가 모두 이 한 벌을 지난다.
    scope = normalize_domains(domain, domains)
    dom_params: dict = {"owner": owner}
    dom_where = ""
    if scope:
        dom_where = " AND " + domain_subtree_where(["c.domain"], scope, dom_params)

    def _in_scope(dom: str) -> bool:
        """문서 주제가 스코프 안인가. Cypher 쪽 `domain_subtree_where` 와 같은 판정."""
        return not scope or any(dom == d or dom.startswith(d.rstrip("/") + "/")
                                for d in scope)
    try:
        # v12 — 결정도 주제 아래 선다. 빼면 좌측 트리에서 결정이 통째로 안 보이고,
        # 그 주제의 개수도 실제보다 작게 나온다(빠진 것이 있다는 표시조차 없다).
        # 제목은 노드 갈래를 요청했을 때만 싣는다 — 안 그러면 기본 호출이 그래프
        # 전체의 제목을 끌고 온다(이 응답이 웹에서는 허브를 건넌다).
        title_col = ", c.title AS title" if nodes else ""
        # 모집단 — 기본은 종전(Concept + Decision), `kind` 를 주면 그 하나뿐이다.
        # 넷을 한꺼번에 싣는 분기는 두지 않는다(위 계약: 목록은 한 종씩).
        pop = {want: _TREE_PK[want]} if want else dict(_SEMANTIC_PK)
        crows = store.run(
            " UNION ALL ".join(
                f"MATCH (c:{lbl}) WHERE c.owner_id = $owner AND c.domain IS NOT NULL "
                f"AND c.domain <> ''{dom_where} "
                f"RETURN c.{pk} AS id, c.domain AS domain{title_col}, "
                f"'{lbl.lower()}' AS kind"
                for lbl, pk in pop.items()),
            dom_params) or []
        # 레일의 개수 — 제목을 싣지 않는 집계 하나. 종을 고르는 손잡이가 "없는 것"과
        # "아직 안 가져온 것"을 구별할 수 있어야 하므로 0 도 키로 남긴다.
        kind_counts: dict[str, int] = {}
        for lbl, pk in _TREE_PK.items():
            rows = store.run(
                f"MATCH (c:{lbl}) WHERE c.owner_id = $owner AND c.domain IS NOT NULL "
                f"AND c.domain <> ''{dom_where} RETURN count(c.{pk}) AS n",
                dom_params) or []
            kind_counts[lbl.lower()] = int((rows[0] or {}).get("n") or 0) if rows else 0
        by_domain: dict[str, dict[str, str]] = {}
        cdom: dict[str, str] = {}
        cmeta: dict[str, tuple[str, str]] = {}     # id → (title, kind), nodes=True 일 때만
        for r in crows:
            cid, dom = r.get("id") or "", (r.get("domain") or "").strip()
            if not cid or not dom:
                continue
            cdom[cid] = dom
            by_domain.setdefault(dom, {})[cid] = dom
            if nodes:
                cmeta[cid] = (r.get("title") or "", r.get("kind") or "concept")

        # 방향 엣지 2종을 한 번에. 양 끝 owner 가드(SEC-1).
        erows = store.run(
            "MATCH (a:Concept)-[:PREREQUISITE_OF]->(b:Concept) "
            "WHERE a.owner_id = $owner AND b.owner_id = $owner "
            "RETURN a.concept_id AS shallow, b.concept_id AS deep "
            "UNION ALL "
            "MATCH (a:Concept)-[:DERIVES_FROM]->(b:Concept) "
            "WHERE a.owner_id = $owner AND b.owner_id = $owner "
            "RETURN b.concept_id AS shallow, a.concept_id AS deep",   # ← 뒤집기
            {"owner": owner}) or []
        edges_by_domain: dict[str, list[tuple[str, str]]] = {}
        for r in erows:
            s, d = r.get("shallow") or "", r.get("deep") or ""
            dom = cdom.get(s, "")
            if not s or not d or not dom or cdom.get(d) != dom:
                continue        # 교차 주제 엣지는 그 주제의 계층이 아니다
            edges_by_domain.setdefault(dom, []).append((s, d))

        depths: dict[str, int] = {}
        for dom, members in by_domain.items():
            depths.update(_domain_depths(members, edges_by_domain.get(dom, [])))

        docs = _ds.list_docs(store, owner=owner).get("docs", [])
        groups: dict[str, list[dict]] = {dom: [] for dom in by_domain}
        unfiled: list[dict] = []
        for d in docs:
            topic = d.get("topic") or ""
            node = d.get("primary_concept_id") or ""
            row = {"doc_id": d.get("doc_id") or "", "title": d.get("title") or "",
                   "updated_at": d.get("updated_at") or ""}
            # 유효기간이 닫힌 문서는 행에 그 날을 싣는다(없으면 키도 없다). 트리는 거르지 않는다:
            # 숨길지는 화면이 정한다(커뮤니티 스페이스는 내린 페이지를 숨긴다).
            if d.get("valid_until"):
                row["valid_until"] = d["valid_until"]
            if not topic or not node:
                # 스코프가 걸렸으면 주제 없는 문서는 그 스코프에 들지 않는다.
                if not scope:
                    unfiled.append(row)
                continue
            if not _in_scope(topic):
                continue
            groups.setdefault(topic, []).append(
                {**row, "node_id": node, "node_title": d.get("primary_concept_title") or "",
                 "depth": int(depths.get(node, 0))})

        out = []
        for dom in sorted(groups):
            rows = sorted(groups[dom],
                          key=lambda r: (r["depth"], r["title"], r["doc_id"]))
            g = {"domain": dom, "concepts": len(by_domain.get(dom, {})),
                 "docs": rows}
            # `kind` 로 물었을 때의 주제 머리 숫자. `concepts` 와 이름을 나눈 이유는
            # 그 열의 뜻(Concept + Decision)을 바꾸지 않으려는 것이다.
            if want:
                g["count"] = len(by_domain.get(dom, {}))
            if nodes:
                # 문서와 **같은 정렬 규칙**(깊이 → 제목 → id). 두 갈래를 오갈 때
                # 줄 순서의 뜻이 달라지면 토글이 목록을 뒤섞는 것으로 읽힌다.
                g["nodes"] = sorted(
                    ({"id": cid, "title": cmeta.get(cid, ("", ""))[0],
                      "kind": cmeta.get(cid, ("", "concept"))[1],
                      "depth": int(depths.get(cid, 0))}
                     for cid in by_domain.get(dom, {})),
                    key=lambda r: (r["depth"], r["title"], r["id"]))
            out.append(g)
        out.sort(key=lambda g: (-len(g["docs"]), -g["concepts"], g["domain"]))
        # 스코프가 걸리면 문서 수도 **실은 줄**만 센다 — 머리의 숫자와 갈래의 줄
        # 수가 어긋나면 화면이 스스로를 반박한다.
        counts = {"domains": len(out),
                  "docs": sum(len(g["docs"]) for g in out) if scope else len(docs),
                  "unfiled": len(unfiled), "kinds": kind_counts}
        if want:
            counts["kind"] = want.lower()
        if scope:
            counts["scope"] = list(scope)
        base.update(executed=True, domains=out, unfiled=unfiled, counts=counts)
        return base
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
        return base
    finally:
        if owns:
            store.close()


def docs_in_range(*, owner: str, start: str, end: str, domain: str | None = None,
                  limit: int = 200, basis: str = BASIS_RECORDED,
                  cfg=None, store=None) -> dict:
    """기간(날짜 범위) 항목 질의 — 타임라인 기간 브러시(D45-V2 4단계). 조회 전용.

    타임라인이 그리는 점은 **날짜를 가진 Episode·Concept** 다. 브러시로 [start,end]
    (ISO, 반개구간 [start, end])를 선택하면 그 구간 항목을 돌려준다.

    ⚠ 시간 기준은 **차트와 글자 그대로 같아야 한다**(`coalesce(date|created,
    updated_at)` — :func:`graph_plan` 의 ts 투영). 여기만 `date`/`created` 단독을
    보던 시절, 팩으로 들어온 개념은 `created` 가 비어 차트는 점 42개를 그리는데
    같은 밴드를 누르면 "0개"가 떴다(실측). 모집단이 어긋나면 화면이 자기를 반박한다
    — D52 가 원장에서 고친 것과 같은 병이다. `updated_at` 이 내용 시점이 아니라는
    캐비앳은 유효하지만, 그건 **양쪽 모두**에 적용되는 한계이지 한쪽만 엄격해야 할
    이유가 아니다. 응답에 ``basis`` 를 에코해 화면이 무엇을 셌는지 밝힐 수 있게 한다.

    ISO 문자열은 사전순 = 시간순이라 문자열 비교로 안전하게 거른다(타임라인
    ts.slice 규약과 동형). 빈 값은 ``'' >= $s`` 가 거짓이라 자동 제외된다.
    ``domain`` 렌즈 선택. SEC-1 owner 스코프.

    ``basis``(D72) — 차트가 어느 축을 그리고 있는지 그대로 받는다. `content` 축에서는
    **Episode 를 세지 않는다**: 에피소드에는 내용 시점 열이 없어(있지도 않은 값을
    빈 문자열로 취급하면 전부 구간 밖) 모집단에서 정직하게 빠진다. 대신 Record 가
    합류한다 — Concept 과 같은 열을 얻었기 때문이다.

    반환: {executed, start, end, basis, items:[{id, kind, title, ts, domain}], total}."""
    cfg = cfg or load_config()
    on_content = basis == BASIS_CONTENT
    base: dict = {"kind": "docs-in-range", "executed": False,
                  "start": start, "end": end, "items": [],
                  "basis": BASIS_CONTENT if on_content else "authored-or-updated"}
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        lim = max(1, min(int(limit), 1000))
        dclause = " AND e.domain = $domain" if domain else ""
        cclause = " AND c.domain = $domain" if domain else ""
        params = {"owner": owner, "s": start, "e": end}
        if domain:
            params["domain"] = domain
        rclause = " AND rc.domain = $domain" if domain else ""
        if on_content:
            # 내용 축 — 열을 가진 라벨만(Concept·Record). 빈 값은 `'' >= $s` 가 거짓이라
            # 자동으로 빠진다: 내용 시점을 안 적은 노드를 0 이나 오늘로 채우지 않는다.
            cypher = (
                "MATCH (c:Concept) WHERE c.owner_id = $owner "
                "AND coalesce(c.content_time, '') >= $s "
                f"AND coalesce(c.content_time, '') < $e{cclause} "
                "RETURN c.concept_id AS id, 'concept' AS kind, c.title AS title, "
                "coalesce(c.content_time, '') AS ts, c.domain AS domain "
                "UNION ALL "
                "MATCH (rc:Record) WHERE rc.owner_id = $owner "
                "AND coalesce(rc.content_time, '') >= $s "
                f"AND coalesce(rc.content_time, '') < $e{rclause} "
                "RETURN rc.record_id AS id, 'record' AS kind, rc.title AS title, "
                "coalesce(rc.content_time, '') AS ts, rc.domain AS domain")
        else:
            cypher = (
                "MATCH (e:Episode) WHERE e.owner_id = $owner "
                "AND coalesce(e.date, e.updated_at, '') >= $s "
                f"AND coalesce(e.date, e.updated_at, '') < $e{dclause} "
                "RETURN e.episode_id AS id, 'episode' AS kind, e.title AS title, "
                "coalesce(e.date, e.updated_at, '') AS ts, e.domain AS domain "
                "UNION ALL "
                "MATCH (c:Concept) WHERE c.owner_id = $owner "
                "AND coalesce(c.created, c.updated_at, '') >= $s "
                f"AND coalesce(c.created, c.updated_at, '') < $e{cclause} "
                "RETURN c.concept_id AS id, 'concept' AS kind, c.title AS title, "
                "coalesce(c.created, c.updated_at, '') AS ts, c.domain AS domain")
        rows = store.run(cypher, params) or []
        rows.sort(key=lambda r: (r.get("ts") or "", r.get("id") or ""))
        items = [{"id": r["id"], "kind": r.get("kind") or "", "title": r.get("title") or r["id"],
                  "ts": r.get("ts") or "", "domain": r.get("domain") or ""}
                 for r in rows[:lim]]
        base.update(executed=True, items=items, total=len(items),
                    truncated=len(rows) > lim)
        return base
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
        return base
    finally:
        if owns:
            store.close()


def ledger(*, owner: str, domain: str | None = None, seed: str | None = None,
           depth: int = 2, by: str = "domain",
           domains: list[str] | None = None, cfg=None, store=None) -> dict:
    """원장(D43) — 도메인별 `{출처, 연결상태}` 롤업. 조회 전용.

    ``by="concept"``(D52) — **한 행이 개념 하나**가 되는 세밀 모드. 도메인을 눌러
    들어갔을 때 생키의 가운데 열이 그 도메인 하나로 쪼그라들던 문제를 푼다(스코프를
    좁혀도 알갱이가 그대로라 "더 깊이 안 들어가는" 것처럼 보였다). 행 값은 전부
    0/1 이고 라벨은 개념 제목(`label`), 키(`domain` 필드)는 개념 id 다 — 소비처가
    구간 클릭 대상을 그대로 쓸 수 있게 모양을 유지한다. 마크 판정은 드릴다운과 같은
    :func:`_concept_in_mark` 를 쓴다(수치 정합을 구조로 보장).

    반환 행: ``{domain, concepts, episodic, record, unsourced, connected,
    gateway, orphan}``, concepts 내림차순.

    스코프 우선순위 ``seed > domain > all``. ``seed`` 지정 시 ego 롤업 — 모집단은
    L2 서브그래프와 같은 순회로 얻은 시드 주변 개념(:func:`_ledger_ego_ids`)이고,
    구성원별 연결상태는 **전역**이다(엣지가 ego 를 떠나도 센다 —
    :func:`ledger_link_plan` ids 계약). 응답에 ``scope``("seed"|"domain"|"all")와
    ``population``(롤업에 실린 개념 총수)을 가산한다 — 구 소비처는 새 키를 무시하고,
    새 소비처는 scope 키 부재로 구 사이드카(seed 무시 → 주제 폴백)를 판별한다
    (버전 스큐 우아 강등). 빈 ego(미존재 시드·부착 0·entity 시드)는 DB 왕복 없이
    ``rows=[]``·population 0 으로 답한다 — ``IN $ids`` 에 빈 리스트를 바인딩하지
    않는다(방언 위험).

    출처 3분할(episodic/record/unsourced)과 연결 3분할(connected/gateway/orphan)은
    **각각 배타**라 두 합이 모두 정확히 concepts 다 — 소비처(Totals 누적 막대)가
    셋을 더해 행 합계로 쓴다. 한 개념이 에피소드와 레코드에 동시에 부착되면 우선순위
    ``episodic > record > unsourced`` 로 한 칸에만 센다(겹침을 그대로 흘리면 막대와
    총계가 부풀었다).

    도메인이 빈 개념은 롤업 키가 없어 제외한다(`domains_plan` 의
    ``domain IS NOT NULL`` 과 같은 태도).

    두 플랜 중 **하나라도 강등되면 응답 전체를 강등**한다 — 연결 축만 죽으면 차수가
    전부 0 이라 모든 개념이 고아로 보고되는데, ``executed: true`` 를 달고 나가면
    소비처가 그 거짓을 참으로 그린다. ego 순회 강등도 같은 태도다."""
    by_id, scope, degraded = _ledger_classify(
        owner=owner, domain=domain, domains=domains, seed=seed, depth=depth,
        cfg=cfg, store=store)
    if degraded is not None:
        return degraded
    if not by_id:      # 빈 ego(개념 구성원 없음) — DB 왕복 없이 빈 롤업
        plan = QueryPlan("ledger", "", {"owner": owner, "seed": seed or ""},
                         note="empty ego (no concept members)")
        return {"plan": plan.to_dict(), "executed": True, "rows": [],
                "scope": scope, "population": 0}

    if by == "concept":
        return _ledger_by_concept(by_id, owner=owner, scope=scope, cfg=cfg, store=store)
    agg: dict[str, dict] = {}
    for b in by_id.values():
        dom = b["domain"]
        if not dom:
            continue
        a = agg.setdefault(dom, {"domain": dom, "concepts": 0, "episodic": 0,
                                 "record": 0, "unsourced": 0, "connected": 0,
                                 "gateway": 0, "orphan": 0})
        a["concepts"] += 1
        if b["episodic"]:                       # 출처는 엄격 분할(episodic > record)
            a["episodic"] += 1
        elif b["record"]:
            a["record"] += 1
        else:
            a["unsourced"] += 1
        if b["degree"] == 0:
            a["orphan"] += 1
        if b["gateway"]:
            a["gateway"] += 1
        if b["degree"] > 0 and not b["gateway"]:
            a["connected"] += 1
    rows = sorted(agg.values(), key=lambda r: (-r["concepts"], r["domain"]))
    # D43 ego: 스코프 에코 + 모집단(행에 실린 개념 총수 — 행 합계와 항상 일치).
    return {"executed": True, "rows": rows, "scope": scope,
            "population": sum(r["concepts"] for r in rows)}


def _ledger_by_concept(by_id: dict, *, owner: str, scope: str, cfg, store) -> dict:
    """개념 단위 원장(D52) — 한 행 = 개념 하나. 값은 0/1 이고 세 열의 합은 여전히
    보존된다(출처 3분할·연결 3분할이 각각 배타이므로 행마다 각 축에서 정확히 하나가
    1 이다). 제목은 한 번의 왕복으로 채운다 — 없으면 id 를 그대로 라벨로 쓴다."""
    ids = [cid for cid, b in by_id.items() if b.get("domain")]
    if not ids:
        return {"executed": True, "rows": [], "scope": scope, "population": 0,
                "by": "concept"}
    owns = store is None
    if owns:
        store = open_store(cfg or load_config())
    try:
        got = store.run(
            "MATCH (c:Concept) WHERE c.concept_id IN $ids AND c.owner_id = $owner "
            "RETURN c.concept_id AS id, c.title AS title",
            {"ids": pkns.keys(owner, ids), "owner": owner}) or []
        title = {r["id"]: (r.get("title") or r["id"]) for r in got}
    finally:
        if owns and store is not None:
            store.close()
    rows = []
    for cid in ids:
        b = by_id[cid]
        row = {"domain": cid, "label": title.get(cid, cid), "topic": b["domain"],
               "concepts": 1}
        for mark in _LEDGER_MARKS:
            row[mark] = 1 if _concept_in_mark(b, mark) else 0
        rows.append(row)
    rows.sort(key=lambda r: (str(r["label"]).lower(), str(r["domain"])))
    return {"executed": True, "rows": rows, "scope": scope,
            "population": len(rows), "by": "concept"}


def _ledger_classify(*, owner: str, domain: str | None, seed: str | None,
                     depth: int, cfg, store, domains: list[str] | None = None):
    """원장 개념별 분류(by_id) — :func:`ledger` 집계와 :func:`ledger_drilldown`
    드릴다운이 **공유**한다(둘의 수치 정합을 구조로 보장 — 보고서 정정 2 대응:
    드릴다운 항목 수 == 원장 셀 값). 반환 ``(by_id, scope, degraded)``:
    degraded 가 not None 이면 그대로 반환할 강등 응답(두-플랜 규칙). by_id 는
    concept_id → {domain, ts, episodic, record, degree, gateway}. 빈 dict = 빈 ego."""
    scope = "domain" if domain else "all"
    ids: list[str] | None = None
    if seed:
        ego = _ledger_ego_ids(seed, owner=owner, depth=depth, cfg=cfg, store=store)
        if not ego.get("executed"):
            return None, scope, ego
        scope, ids, domain = "seed", ego["ids"], None
        if not ids:
            return {}, "seed", None
    o = execute(ledger_origin_plan(owner=owner, domains=normalize_domains(domain, domains),
                                   ids=ids), cfg, store=store)
    if not o.get("executed"):
        return None, scope, o
    lk = execute(ledger_link_plan(owner=owner, domains=normalize_domains(domain, domains),
                                  ids=ids), cfg, store=store)
    if not lk.get("executed"):
        return None, scope, lk
    by_id: dict[str, dict] = {}
    for row in o.get("rows", []):
        nid = row.get("id")
        if not nid:
            continue
        b = by_id.setdefault(nid, {"domain": "", "ts": "", "episodic": False,
                                   "record": False, "degree": 0, "gateway": False})
        mark = row.get("mark")
        if mark == "all":                       # 권위 도메인·시간은 개념 자신에게서
            b["domain"] = row.get("domain") or ""
            b["ts"] = row.get("ts") or ""
        elif mark == "episodic":
            b["episodic"] = True
        elif mark == "record":
            b["record"] = True
    for row in lk.get("rows", []):
        b = by_id.get(row.get("id"))
        if b is None:      # 출처 축에 없는 id = 스코프 밖 — 세지 않는다
            continue
        b["degree"] += 1
        src, other = row.get("domain") or "", row.get("other") or ""
        if src and other and src != other:
            b["gateway"] = True
    return by_id, scope, None


# 드릴다운 mark → by_id 분류 판정(원장 집계와 같은 배타 규칙). 출처 3분할·연결 3분할.
_LEDGER_MARKS = ("episodic", "record", "unsourced", "connected", "gateway", "orphan")


def _concept_in_mark(b: dict, mark: str) -> bool:
    if mark == "episodic":
        return b["episodic"]
    if mark == "record":
        return b["record"] and not b["episodic"]        # 엄격 분할(episodic > record)
    if mark == "unsourced":
        return not b["episodic"] and not b["record"]
    if mark == "orphan":
        return b["degree"] == 0
    if mark == "gateway":
        return b["gateway"]
    if mark == "connected":
        return b["degree"] > 0 and not b["gateway"]
    return False


def ledger_drilldown(*, owner: str, domain: str, mark: str, seed: str | None = None,
                     depth: int = 2, cfg=None, store=None) -> dict:
    """합계(Totals) 생키 구간 클릭 → 그 구간에 기여한 **개념 목록**(D45-V2 3단계).

    보고서 정정 2: 원장 롤업 단위는 문서가 아니라 개념이다 — 드릴다운 1홉은 개념
    목록이고, 문서는 개념→node_context 로 한 홉 더 간다. 항목 수는 원장의 해당
    셀 값과 **항상 일치**한다(_ledger_classify 공유). 조회 전용.

    반환: {executed, domain, mark, items:[{concept_id, title, one_liner}], total}."""
    cfg = cfg or load_config()
    base: dict = {"kind": "ledger-drilldown", "executed": False,
                  "domain": domain, "mark": mark, "items": []}
    if mark not in _LEDGER_MARKS:
        base["error"] = f"unknown mark: {mark}"
        return base
    by_id, _scope, degraded = _ledger_classify(
        owner=owner, domain=domain if not seed else None, seed=seed, depth=depth,
        cfg=cfg, store=store)
    if degraded is not None:
        degraded.setdefault("kind", "ledger-drilldown")
        return degraded
    ids = [cid for cid, b in (by_id or {}).items()
           if b["domain"] == domain and _concept_in_mark(b, mark)]
    if not ids:
        base["executed"] = True
        base["total"] = 0
        return base
    owns = store is None
    if owns:
        store = open_store(cfg)
    try:
        rows = store.run(
            "MATCH (c:Concept) WHERE c.concept_id IN $ids AND c.owner_id = $owner "
            "RETURN c.concept_id AS id, c.title AS title, c.one_liner AS ol",
            {"ids": pkns.keys(owner, ids), "owner": owner}) or []
        title = {r["id"]: {"concept_id": r["id"], "title": r.get("title") or r["id"],
                           "one_liner": r.get("ol") or ""} for r in rows}
        items = [title.get(cid, {"concept_id": cid, "title": cid, "one_liner": ""})
                 for cid in sorted(ids)]
        base.update(executed=True, items=items, total=len(items))
        return base
    finally:
        if owns and store is not None:
            store.close()
