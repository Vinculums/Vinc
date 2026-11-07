# -*- coding: utf-8 -*-
"""E4 — MCP 도구 사용 평가용 **고정 픽스처 그래프**를 심는다.

왜 라이브 그래프가 아닌가. 계획서(vinc.plan.mcp-hardening §E4)는 "답이 그래프
상태에 따라 변할 수 있는 2문항은 스냅샷 고정 후 확정한다"고 적었다. 그런데 그건
2문항의 문제가 아니다 — 우리가 매 세션 그래프에 쓰고 있으므로 **경로 길이·이웃
수·고아 목록·도메인 카운트가 전부** 움직인다. 평가셋이 조용히 낡으면 그건
"측정하지 않는 측정"이 된다(E1 이 고친 것과 같은 함정).

그래서 코퍼스를 코드로 고정한다. 이 스크립트가 심는 그래프는 작지만 구조가
의도적으로 다양하다:

- 개념 삼각(view·flow·timeline)과 별개 컴포넌트(billing·invoice)
- **고아** 하나(concept:glossary) — diagnose 가 잡아야 한다
- 개념에 붙은 **Record 2·Episode 1·Bridge 1** — neighbors 가 개념만 보면 놓친다(F14)
- **모순 쌍**(decide 의 conflicts) — 도메인을 가로지르는 엣지
- **근접 중복 쌍**(concept:view-viewpoint / concept:view-viewpoints) — merge_candidates
- 문서 1건 — search → doc_get 왕복

멱등하다. 같은 스토어에 두 번 돌려도 결과가 같다(전부 MERGE).
"""
from __future__ import annotations

import argparse

try:
    from scripts._root import ROOT  # noqa: F401
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT  # noqa: F401

from store import pkns  # noqa: E402  (PK 네임스페이싱 — sys.path 세팅 뒤)

OWNER = "eval:tooluse"

#: (id, title, domain, tags)
CONCEPTS = [
    ("concept:view",       "View 뷰",            "product", ["core"]),
    ("concept:flow",        "흐름 관점",           "product", ["core"]),
    ("concept:timeline",    "타임라인 관점",       "product", ["core"]),
    ("concept:billing",     "청구",                "money",   ["core"]),
    ("concept:invoice",     "인보이스",            "money",   []),
    ("concept:glossary",    "용어집",              "docs",    []),        # 고아
    ("concept:view-viewpoint",  "View 뷰 단수형",     "product", []),        # 근접 중복 A
    ("concept:view-viewpoints", "View 뷰 복수형",     "product", []),        # 근접 중복 B
]

#: (from, to) — 전부 RELATED_TO. 삼각 + 별개 컴포넌트 + 도메인 횡단 하나.
EDGES = [
    ("concept:view", "concept:flow"),
    ("concept:flow", "concept:timeline"),
    ("concept:timeline", "concept:view"),
    ("concept:billing", "concept:invoice"),
    ("concept:view", "concept:billing"),      # 도메인 횡단 = decide 의 conflict
]

#: (id, title, domain, tags, note, 붙는 개념)
RECORDS = [
    ("record:fact-view-webgl", "View 는 WebGL 컨텍스트를 하나만 쓴다", "product",
     ["fact"], "슬라이더 드래그로 재마운트하면 컨텍스트가 샌다.", "concept:view"),
    ("record:pitfall-view-refetch", "옵션 8개 중 하나만 서버가 필요하다", "product",
     ["pitfall"], "전량 재조회라 드래그마다 왕복이 생긴다.", "concept:view"),
]

#: (id, title, date, domain, summary, 붙는 개념)
EPISODES = [
    ("episode:2026-01-15-view-ship", "View 3종 관점 출시", "2026-01-15", "product",
     "흐름·타임라인·합계 세 관점을 한 번에 냈다.", "concept:view"),
]

#: (id, title, source_domain, target_domain, role, 붙는 개념)
BRIDGES = [
    ("bridge:pricing-surface", "가격 표면", "product", "money", "pricing", "concept:view"),
]

DOC_ID = "eval.tooluse.view-notes"
DOC_TITLE = "View 구현 노트"
DOC_TEXT = """# View 구현 노트

## 렌더러
2D 는 sigma 로 독립 렌더링한다. 3D 와 코드를 공유하지 않는다.

## 컨텍스트 누수
show() 에 가드가 없어 슬라이더 드래그가 WebGL 컨텍스트를 흘린다.
브라우저 상한(보통 16개)에 닿으면 그래프가 검게 죽는다.

## 툴팁
자체 툴팁을 쓴다. WCAG 1.4.13 을 만족해야 하므로 호버 해제로 사라지지 않는다.
"""


def seed(store) -> dict:
    """픽스처를 심고 심은 개수를 돌려준다. 전부 MERGE 라 멱등하다.

    ⚠ 이 시더는 쓰기 경로(`merge_fragment`)를 우회해 스토어에 직접 심는다. PK
    네임스페이싱(design A)이 켜지면 읽기 플랜이 owner 로 합성한 물리 키로 묻기
    때문에, 노드 PK 는 여기서도 `_k` 로 합성해야 한다 — 안 하면 평가셋이 통째로
    빈손이 되고, 그 실패는 "도구가 답을 못 준다"로 오독된다.
    근거: [[vinc.plan.node-pk-namespacing]] §7"""
    def _k(nid: str) -> str:
        return pkns.key(OWNER, nid)

    with store.session() as s:
        for cid, title, domain, tags in CONCEPTS:
            s.run("MERGE (c:Concept {concept_id:$id}) SET c.owner_id=$o, c.title=$t, "
                  "c.domain=$d, c.tags=$g", id=_k(cid), o=OWNER, t=title, d=domain, g=tags)
        for a, b in EDGES:
            s.run("MATCH (x:Concept{concept_id:$a}),(y:Concept{concept_id:$b}) "
                  "MERGE (x)-[:RELATED_TO]->(y)", a=_k(a), b=_k(b))
        for rid, title, domain, tags, note, target in RECORDS:
            s.run("MERGE (r:Record {record_id:$id}) SET r.owner_id=$o, r.title=$t, "
                  "r.domain=$d, r.tags=$g, r.props=$p",
                  id=_k(rid), o=OWNER, t=title, d=domain, g=tags, p='{"note": "%s"}' % note)
            s.run("MATCH (r:Record{record_id:$id}),(c:Concept{concept_id:$c}) "
                  "MERGE (r)-[:ATTACHES_TO {type:'relates_to'}]->(c)", id=_k(rid), c=_k(target))
        for eid, title, date, domain, summary, target in EPISODES:
            s.run("MERGE (e:Episode {episode_id:$id}) SET e.owner_id=$o, e.title=$t, "
                  "e.date=$dt, e.domain=$d, e.summary=$s",
                  id=_k(eid), o=OWNER, t=title, dt=date, d=domain, s=summary)
            s.run("MATCH (e:Episode{episode_id:$id}),(c:Concept{concept_id:$c}) "
                  "MERGE (e)-[:ABOUT]->(c)", id=_k(eid), c=_k(target))
        for bid, title, sd, td, role, target in BRIDGES:
            s.run("MERGE (b:Bridge {bridge_id:$id}) SET b.owner_id=$o, b.title=$t, "
                  "b.source_domain=$sd, b.target_domain=$td",
                  id=_k(bid), o=OWNER, t=title, sd=sd, td=td)
            s.run("MATCH (b:Bridge{bridge_id:$id}),(c:Concept{concept_id:$c}) "
                  "MERGE (b)-[:CONNECTS {role:$r}]->(c)", id=_k(bid), c=_k(target), r=role)
    return {"concepts": len(CONCEPTS), "edges": len(EDGES), "records": len(RECORDS),
            "episodes": len(EPISODES), "bridges": len(BRIDGES)}


def seed_doc(store) -> str:
    """문서 정본 1건(search → doc_get 왕복용). docstore 경유라 청킹 규약을 탄다."""
    from api import docstore
    docstore.put_doc(doc_id=DOC_ID, title=DOC_TITLE, text=DOC_TEXT,
                     owner=OWNER, origin="scripts/seed_tooluse.py", store=store)
    return DOC_ID


def main() -> int:
    ap = argparse.ArgumentParser(description="E4 도구 사용 평가용 픽스처 그래프를 심는다")
    ap.add_argument("--with-doc", action="store_true", help="문서 정본도 함께 심는다")
    args = ap.parse_args()

    from store.config import load_config
    from store.backend import open_store
    cfg = load_config()
    store = open_store(cfg)
    store.provision(dim=cfg.embedding_dim)
    counts = seed(store)
    if args.with_doc:
        counts["doc"] = seed_doc(store)
    store.close()
    print(f"owner={OWNER} · {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
