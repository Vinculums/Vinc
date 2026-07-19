# -*- coding: utf-8 -*-
"""논리 스토어 이관(T4·T5) — 드라이버 무관 export/import via GraphStore.run().

**왜 논리 이관인가**: LadybugDB 0.18.x 는 kuzu 0.11.3 DB 파일을 열지 못한다(저장
포맷 비호환 — 2026-07-19 실측: "not a valid Lbug database file"). 그래서 드라이버
전환(D14 재평가 · G0 게이트)은 파일 복사가 아니라 **논리 덤프→재적재**여야 한다.
이 모듈이 그 레일이며, `backup.py`(파일 스냅샷 — 동일 드라이버 백업)와 상보한다.

**드라이버 공존 불가**(네이티브 타입 충돌 — embedded_backend §2-2): 한 프로세스가
kuzu·ladybug 를 동시에 import 하지 못한다. 따라서 실제 전환은 **두 프로세스**로:
  1) 구 드라이버(kuzu)로 `export_store` → JSON 파일.
  2) 새 드라이버(ladybug)로 `import_store` ← 그 JSON.
`vinc-migrate` CLI(아래 main)가 한쪽 방향을 수행한다. `MEMORY_EMBEDDED_DRIVER` 로
드라이버를 고정해 프로세스별로 부른다.

**무손실 계약(T5 리허설)**: 스키마 SSoT(embedded_schema.node_table_specs/
rel_table_specs)에서 열을 파생하므로, 열 추가(추적열·evidence 등)에 이관이 자동
추종한다. 추적열(updated_at/rev/sync_seq)·preference 신호(pinned/access_count)·
embedding 벡터까지 전량 보존한다. round-trip 왕복 테스트가 이를 상시 보증한다.
"""
from __future__ import annotations

import json

from . import embedded_schema as _schema

_DUMP_VERSION = 1


def export_store(store) -> dict:
    """스토어 전량을 드라이버 무관 dict 로 덤프. 노드(라벨별 행)·엣지(라벨별 끝점+
    props). 스키마 SSoT 의 열만 조회(투기 없음)."""
    node_specs = _schema.node_table_specs()
    nodes: dict[str, list[dict]] = {}
    for label, spec in node_specs.items():
        cols = spec["cols"]
        proj = ", ".join(f"n.{c} AS {c}" for c in cols)
        try:
            rows = store.run(f"MATCH (n:{label}) RETURN {proj}")
        except Exception:
            rows = []   # 미프로비저닝 라벨 — 빈 집합
        nodes[label] = rows or []
    edges: list[dict] = []
    for spec in _schema.rel_table_specs():
        label, src, dst, props = spec["label"], spec["from"], spec["to"], spec["props"]
        src_pk = node_specs[src]["pk"]
        dst_pk = node_specs[dst]["pk"]
        proj = [f"a.{src_pk} AS _from", f"b.{dst_pk} AS _to"]
        proj += [f"r.{p} AS {p}" for p in props]
        try:
            rows = store.run(
                f"MATCH (a:{src})-[r:{label}]->(b:{dst}) RETURN {', '.join(proj)}")
        except Exception:
            rows = []
        for r in rows or []:
            edges.append({"label": label, "from": r.pop("_from"),
                          "to": r.pop("_to"), "props": r})
    return {"dump_version": _DUMP_VERSION, "schema_version": _schema.SCHEMA_VERSION,
            "nodes": nodes, "edges": edges}


def _set_clause(cols: list[str], pk: str) -> str:
    """pk 제외 열의 SET 절(모두 파라미터). 빈 열이면 ''(pk-only 노드)."""
    setters = [f"n.{c} = ${c}" for c in cols if c != pk]
    return (" SET " + ", ".join(setters)) if setters else ""


def import_store(store, dump: dict, *, dim: int) -> dict:
    """덤프를 스토어에 재적재(멱등 MERGE). 먼저 스키마 provision 후 노드→엣지 순.
    반환 통계 {nodes, edges}. 노드 pk 로 MERGE, 나머지 열 SET; 엣지는 끝점 MATCH 후
    MERGE(+props SET). 벡터 인덱스는 호출부/embed 잡이 별도 관리(여기선 데이터만)."""
    store.provision(dim=dim)
    node_specs = _schema.node_table_specs()
    n_count = e_count = 0
    for label, rows in (dump.get("nodes") or {}).items():
        spec = node_specs.get(label)
        if not spec:
            continue
        pk, cols = spec["pk"], spec["cols"]
        setc = _set_clause(cols, pk)
        for row in rows:
            params = {c: row.get(c) for c in cols}
            store.run(f"MERGE (n:{label} {{{pk}: ${pk}}}){setc}", params)
            n_count += 1
    rel_by_label = {s["label"]: s for s in _schema.rel_table_specs()}
    for edge in dump.get("edges") or []:
        spec = rel_by_label.get(edge["label"])
        if not spec:
            continue
        src, dst = spec["from"], spec["to"]
        src_pk, dst_pk = node_specs[src]["pk"], node_specs[dst]["pk"]
        props = edge.get("props") or {}
        setr = (" SET " + ", ".join(f"r.{p} = $much_{p}" for p in props)) if props else ""
        params = {"_s": edge["from"], "_d": edge["to"]}
        params.update({f"much_{p}": v for p, v in props.items()})
        store.run(
            f"MATCH (a:{src}), (b:{dst}) "
            f"WHERE a.{src_pk} = $_s AND b.{dst_pk} = $_d "
            f"MERGE (a)-[r:{edge['label']}]->(b){setr}", params)
        e_count += 1
    return {"nodes": n_count, "edges": e_count}


def main() -> None:
    import argparse
    import sys
    from .backend import open_store
    from .config import load_config

    ap = argparse.ArgumentParser(
        description="Vinc 논리 스토어 이관(T4) — 드라이버 무관 export/import. "
                    "드라이버 전환 시 프로세스별로: (kuzu) export → (ladybug) import.")
    ap.add_argument("mode", choices=["export", "import"])
    ap.add_argument("--file", required=True, help="덤프 JSON 경로")
    args = ap.parse_args()

    cfg = load_config()
    store = open_store(cfg)
    if store is None:
        print("[ERROR] 스토어 연결 실패 (드라이버/자격 확인).", file=sys.stderr)
        sys.exit(1)
    try:
        if args.mode == "export":
            dump = export_store(store)
            with open(args.file, "w", encoding="utf-8") as f:
                json.dump(dump, f, ensure_ascii=False)
            nn = sum(len(v) for v in dump["nodes"].values())
            print(f"[export] 드라이버={store.driver_name} 노드 {nn} · 엣지 "
                  f"{len(dump['edges'])} → {args.file}")
        else:
            with open(args.file, encoding="utf-8") as f:
                dump = json.load(f)
            stats = import_store(store, dump, dim=cfg.embedding_dim)
            print(f"[import] 드라이버={store.driver_name} 노드 {stats['nodes']} · "
                  f"엣지 {stats['edges']} ← {args.file}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
