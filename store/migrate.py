# -*- coding: utf-8 -*-
"""논리 스토어 이관(T4·T5) — 드라이버 무관 export/import via GraphStore.run().

**용도**: 데이터 디렉토리 간 논리 덤프→재적재(백업 복원·기기 이관·덤프 점검 등).
파일 스냅샷(`backup.py` — 동일 드라이버·바이너리 복사)과 상보하는 상위 레벨
경로다. (역사: 원래 kuzu→ladybug 드라이버 전환용 레일로 만들어졌으나, v0.5.3 에서
kuzu 를 제거하고 ladybug 단일로 단순화하며 그 용도는 종료 — 로직 자체는
드라이버 무관이라 범용 export/import 도구로 남긴다.)

**무손실 계약(T5 리허설)**: 스키마 SSoT(embedded_schema.node_table_specs/
meta_table_specs/rel_table_specs)에서 열을 파생하므로, 열 추가(추적열·evidence 등)에
이관이 자동 추종한다. 추적열(updated_at/rev/sync_seq)·preference 신호(pinned/
access_count)·embedding 벡터·메타 테이블(`_DocMeta` 등)까지 전량 보존한다. round-trip
왕복 테스트가 이를 상시 보증한다.

⚠ **메타 테이블은 v2 덤프부터 실린다**(2026-08-30). v1 덤프에는 `meta` 키가 없고,
그런 덤프를 적재하면 문서 정체성(`_DocMeta`)·삭제 표식·동기화 커서가 없는 스토어가
된다 — 오류 없이. 그 상태가 무슨 뜻인지는 `embedded_schema.meta_table_specs()` 에.
"""
from __future__ import annotations

import json
import re

from . import embedded_schema as _schema

#: v2 — 메타(`_`-접두) 테이블 동봉. v1 덤프도 그대로 적재된다(meta 키 부재 = 없음).
_DUMP_VERSION = 2


def _narrow_to_existing(store, table: str, wanted: list[str], probe: str) -> list[str] | None:
    """`table` 이 실제로 가진 열만 남긴다. None 은 "테이블 자체가 없다"는 뜻이다.

    전량 투영이 실패하는 이유는 둘인데 **결과가 정반대**다:

      · 테이블이 아직 프로비저닝되지 않았다 → 정당히 빈 집합이다
      · 테이블은 있는데 **열이 없다**(스토어가 코드보다 낮은 스키마다) → 그 라벨의
        **전 행이 조용히 사라진다**

    예전 코드는 둘을 `except Exception: rows = []` 로 똑같이 다뤘다. 2026-09-02 에
    실측으로 물렸다: schema_version 14 스토어를 v16 코드로 export 하니 Record 가
    뒤에 얻은 `valid_from`·`valid_until` 때문에 투영이 깨졌고, **Record 2행이 오류
    하나 없이 0행으로 덤프됐다.** 이 함수를 타는 곳이 백업 복원·기기 이관·재구축
    전부라, 낮은 스키마의 사본을 되살리면 라벨 하나가 통째로 빈다.

    `probe` 는 "이 테이블이 있는가"를 묻는 질의다 — 있는데 열이 모자란 경우와
    아예 없는 경우를 가르는 것이 이 함수의 유일한 일이다.
    """
    try:
        info = store.run(f"CALL table_info('{table}') RETURN *")
    except Exception:
        info = None
    if info:
        have = {str(r.get("name", "")) for r in info}
        return [c for c in wanted if c in have]
    # table_info 를 못 쓰는 드라이버일 수도 있다 — 그때는 존재 여부만 따로 묻는다.
    try:
        store.run(probe)
    except Exception:
        return None          # 테이블이 없다 = 정당히 빈 집합
    # 있는데 투영이 깨졌고 어느 열인지도 못 알아낸다 → **삼키면 손실이다.**
    raise RuntimeError(
        f"{table}: 전량 투영이 실패했는데 실제 열 목록을 알아낼 수 없다 — "
        "빈 집합으로 넘기면 이 라벨의 행이 조용히 사라진다")


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
            # 빠른 길이 깨졌다. 여기서 빈 집합으로 넘기는 것이 예전의 무음 손실이었다.
            have = _narrow_to_existing(store, label, cols,
                                       f"MATCH (n:{label}) RETURN count(n) AS c")
            if have is None:
                rows = []                      # 미프로비저닝 라벨 — 정당히 빈 집합
            elif spec["pk"] not in have:
                # pk 가 없으면 import 가 MERGE 할 키가 없다. 조용히 넘기면 안 된다.
                raise RuntimeError(f"{label}: pk {spec['pk']} 열이 없다 — 이관 불가")
            else:
                proj = ", ".join(f"n.{c} AS {c}" for c in have)
                rows = store.run(f"MATCH (n:{label}) RETURN {proj}")
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
            # 노드와 같은 이유로 같은 처방 — 관계 테이블도 열이 늘어난 적이 있다.
            have = _narrow_to_existing(
                store, label, list(props),
                f"MATCH ()-[r:{label}]->() RETURN count(r) AS c")
            if have is None:
                rows = []
            else:
                proj = [f"a.{src_pk} AS _from", f"b.{dst_pk} AS _to"]
                proj += [f"r.{p} AS {p}" for p in have]
                rows = store.run(
                    f"MATCH (a:{src})-[r:{label}]->(b:{dst}) RETURN {', '.join(proj)}")
        for r in rows or []:
            # 끝점 라벨을 함께 적는다 — 다중 FROM/TO 쌍 테이블(v11 IN_DOMAIN)에서는
            # pk 값만으로 어느 쌍인지 못 가른다. 구 덤프에는 이 열이 없어 import 가
            # 쌍 전수 시도로 물러선다.
            edges.append({"label": label, "from": r.pop("_from"),
                          "to": r.pop("_to"), "from_label": src, "to_label": dst,
                          "props": r})
    return {"dump_version": _DUMP_VERSION, "schema_version": _schema.SCHEMA_VERSION,
            "nodes": nodes, "edges": edges, "meta": _export_meta(store)}


def _export_meta(store) -> dict[str, list[dict]]:
    """메타(`_`-접두) 테이블 행. 없는 테이블은 빈 집합(구 스토어 호환)."""
    out: dict[str, list[dict]] = {}
    for label, spec in _schema.meta_table_specs().items():
        proj = ", ".join(f"n.{c} AS {c}" for c in spec["cols"])
        try:
            out[label] = store.run(f"MATCH (n:{label}) RETURN {proj}") or []
        except Exception:
            out[label] = []
    return out


#: 적재하지 않는 메타 행. 스키마 버전은 **대상 스토어 자신의 사실**이다 — 덤프의 값을
#: 덮어쓰면 구 덤프가 신 스토어의 버전을 되돌려 다음 부팅이 마이그레이션을 다시 돈다.
#: (멱등이라 낫기는 하지만, 낫는다고 틀린 값을 쓸 이유는 없다.)
_META_SKIP = {("_SchemaMeta", "schema_version")}


def _import_meta(store, meta: dict[str, list[dict]]) -> int:
    """메타 행 재적재(pk MERGE). 반환 = 실제로 쓴 행 수."""
    n = 0
    for label, spec in _schema.meta_table_specs().items():
        pk, cols = spec["pk"], spec["cols"]
        setc = _set_clause(cols, pk)
        for row in meta.get(label) or []:
            if (label, row.get(pk)) in _META_SKIP:
                continue
            store.run(f"MERGE (n:{label} {{{pk}: ${pk}}}){setc}",
                      {c: row.get(c) for c in cols})
            n += 1
    return n


def _set_clause(cols: list[str], pk: str) -> str:
    """pk 제외 열의 SET 절(모두 파라미터). 빈 열이면 ''(pk-only 노드)."""
    setters = [f"n.{c} = ${c}" for c in cols if c != pk]
    return (" SET " + ", ".join(setters)) if setters else ""


def import_store(store, dump: dict, *, dim: int) -> dict:
    """덤프를 스토어에 재적재(멱등 MERGE). 먼저 스키마 provision 후 노드→엣지→메타 순.
    반환 통계 {nodes, edges, meta}. 노드 pk 로 MERGE, 나머지 열 SET; 엣지는 끝점 MATCH 후
    MERGE(+props SET). 검색 인덱스는 만들지 않는다 — 데이터만 넣는다.

    ⚠ **인덱스를 안 만든다는 것이 이 함수의 계약이지만, 호출부가 그걸 안다는 보장은
    없다.** 새 스토어에 적재만 하고 두면 FTS 색인이 없어 검색이 통째로 죽는다
    (`Table Chunk doesn't have an index with name chunk_ft`). 재구축 경로는
    :func:`rebuild_store` 를 쓸 것 — 거기서 색인까지 세운다."""
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
    # label → 쌍 **리스트**. dict 로 접으면 다중 FROM/TO 쌍 테이블(v11 IN_DOMAIN)에서
    # 마지막 쌍만 남아 나머지 쌍의 엣지가 0건 MATCH 로 조용히 유실된다.
    rel_by_label: dict[str, list[dict]] = {}
    for s in _schema.rel_table_specs():
        rel_by_label.setdefault(s["label"], []).append(s)
    for edge in dump.get("edges") or []:
        specs = rel_by_label.get(edge["label"])
        if not specs:
            continue
        # 덤프가 끝점 라벨을 실었으면 그 쌍만(정확). 구 덤프는 열이 없어 쌍 전수
        # 시도로 물러선다 — 안 맞는 쌍은 MATCH 가 비어 no-op 이다.
        want = (edge.get("from_label"), edge.get("to_label"))
        if all(want):
            specs = [s for s in specs if (s["from"], s["to"]) == want] or specs
        props = edge.get("props") or {}
        setr = (" SET " + ", ".join(f"r.{p} = $much_{p}" for p in props)) if props else ""
        params = {"_s": edge["from"], "_d": edge["to"]}
        params.update({f"much_{p}": v for p, v in props.items()})
        for spec in specs:
            src, dst = spec["from"], spec["to"]
            src_pk, dst_pk = node_specs[src]["pk"], node_specs[dst]["pk"]
            store.run(
                f"MATCH (a:{src}), (b:{dst}) "
                f"WHERE a.{src_pk} = $_s AND b.{dst_pk} = $_d "
                f"MERGE (a)-[r:{edge['label']}]->(b){setr}", params)
        e_count += 1
    return {"nodes": n_count, "edges": e_count,
            "meta": _import_meta(store, dump.get("meta") or {})}


def rebuild_store(src, dst, *, model: str, dim: int) -> dict:
    """**차원이 바뀌는 임베딩 모델 교체**의 재구축 경로 — 이 리포의 정본이다.

    `switch_embed_model` 만으로는 안 된다. 그 함수는 옛 벡터를 지우고 전 문서를 큐에
    넣지만 **벡터 열의 차원은 못 바꾼다**: 차원은 `CREATE NODE TABLE ChunkVec(…
    embedding FLOAT[768])` 로 박혀 있고, `provision(dim=1024)` 은 'already exists' 를
    삼켜 **조용히 no-op** 이다(실측: 열이 FLOAT[768] 그대로). 그 뒤 1024 벡터를 쓰면
    그때서야 ``Unsupported casting LIST with incorrect list entry to ARRAY`` 가
    난다 — **스토어를 이미 비운 다음에.**

    그래서 제자리가 아니라 export→새 스토어→import 다. 이유 셋:
      ① `ALTER TABLE … ALTER COLUMN` 으로 배열 길이를 못 바꾼다(엔진 미지원).
      ② 위의 provision no-op.
      ③ 제자리로 하려면 ChunkVec 을 DROP/재생성해야 하는데 실패하면 되돌릴 원본이
         없다. 새 스토어로 만들면 실패해도 원본이 그대로 남는다(= 롤백 수단).

    순서가 계약이다. 각 단계가 왜 그 자리인지:

    1. **덤프에서 ChunkVec 을 뺀다.** export 는 옛 768 벡터를 그대로 싣는데 import 는
       1024 열에 넣으려 하므로 행마다 캐스팅 예외가 난다. 어차피 전량 재임베딩할
       벡터라 버리는 것이 맞다 — 대가는 재임베딩이 끝날 때까지 **벡터 검색이 빈다**.
    2. **메타를 포함해 적재한다**(`import_store` 가 한다). `_DocMeta` 가 없으면 4번의
       재큐가 0건이 되고 재임베딩이 영영 안 일어난다 — 오류 하나 없이.
    3. **색인을 여기서 세운다. 최적화가 아니라 필수 단계다.** `import_store` 는 데이터만
       넣으므로 새 스토어에는 FTS 가 없고, 그러면 재충전 창 동안 검색이 "벡터만 비는"
       게 아니라 **통째로 죽는다**(실측: `Table Chunk doesn't have an index with name
       chunk_ft`). 재임베딩 **전에** 세워야 그 창이 설계된 모양(벡터 팔 off · 텍스트 팔
       on)이 된다. `switch_embed_model` 주석의 "FTS 는 그대로 살아 있다"는 제자리
       전환을 전제한 말이라 이 경로에는 해당되지 않는다.
    4. **전량 재큐.** 모델 식별자를 새 값으로 적고 전 문서 `needs_embed=1`.

    반환에 `requeued` 와 `docs` 를 함께 싣는다 — 둘이 어긋나면 그것이 곧 2번 실패의
    신호다(호출부가 볼 수 있어야 한다).
    """
    # api 를 함수 안에서 import 한다. `store/` 는 `api/` 를 모르는 층인데(모듈 최상단에
    # 두면 그 방향이 뒤집힌다), 재큐를 호출부에 미루면 "재구축했는데 재큐를 안 한"
    # 스토어를 만들 수 있다 — 그것이 정확히 이 함수가 막으려는 무음 실패다.
    from api.docstore import switch_embed_model

    dump = export_store(src)
    dropped = len(dump["nodes"].pop("ChunkVec", None) or [])
    # 노드 미러(NodeVec, v17)도 같은 이유로 버린다: 옛 차원의 벡터라 새 열에 안 들어가고,
    # 어차피 컨테이너 기동이 전량 재구축한다(`docker-entrypoint.sh` 의 미러 재구축).
    # 리허설(2026-08-30) 때는 이 표가 없어서 빠져 있었다.
    node_dropped = len(dump["nodes"].pop("NodeVec", None) or [])
    stats = import_store(dst, dump, dim=dim)
    dst.ensure_search_indexes(dim=dim)
    sw = switch_embed_model(dst, model)
    docs = dst.run("MATCH (d:_DocMeta) RETURN count(d) AS c") or []
    return {"model": model, "dim": dim, "vectors_dropped": dropped,
            "node_vectors_dropped": node_dropped,
            "nodes": stats["nodes"], "edges": stats["edges"], "meta": stats["meta"],
            "docs": int(docs[0]["c"]) if docs else 0,
            "requeued": sw.get("requeued", 0)}


def vector_dim(store, table: str = "ChunkVec") -> int | None:
    """`table` 의 embedding 열이 실제로 가진 차원. 표가 없으면 None.

    메타에 적힌 모델 이름으로 추정하지 않고 열을 직접 읽는다 — 이 값이 곧 "첫 쓰기가
    캐스팅 예외를 내는가"를 정하는 사실이고, 메타는 비어 있거나 낡을 수 있다."""
    try:
        rows = store.run(f"CALL table_info('{table}') RETURN *") or []
    except Exception:
        return None
    for r in rows:
        if str(r.get("name", "")) == "embedding":
            m = re.search(r"(\d+)", str(r.get("type", "")))
            return int(m.group(1)) if m else None
    return None


def migrate_dim_if_needed(data_dir: str, *, model: str, target_dim: int) -> dict:
    """기동 시 벡터 열의 차원이 이 배포의 모델과 다르면 **재구축해서 갈아 끼운다.**

    허브 컨테이너의 엔트리포인트가 사이드카를 띄우기 **전에** 부른다(`api/cli.py
    migrate-dim`). 그 자리여야 하는 이유 둘: 스토어는 단일 writer 라 사이드카가 연 뒤에는
    아무도 두 번째로 못 열고, 사이드카 기동 대기(300초)가 재구축 시간을 먹으면 컨테이너가
    재시작 루프에 든다.

    안전한 방향으로만 움직인다:
      · 차원이 같으면 아무것도 하지 않는다(멱등 — 매 기동 불려도 좋다).
      · 재구축은 **새 파일**에 만들고 원본은 읽기만 한다(:func:`rebuild_store` 의 계약).
        `docs != requeued` 면 원본을 그대로 두고 예외를 낸다 — 반쯤 된 스토어를 자리에
        넣지 않는다.
      · 성공하면 원본을 ``<data_dir>.pre-<옛차원>`` 으로 물리고 새 것을 자리에 놓는다.
        이 사본이 이 리비전 안의 되돌림 수단이고, 영속 스냅샷의 ``.prev`` 세대가 한
        주기 더 그 뒤를 받친다. 되돌림 자체는 반대 방향의 같은 재구축이다.
    벡터는 재구축 뒤 비어 있다. 워커가 새 모델로 다시 채우는 동안 검색은 텍스트 팔로
    답하고 `arms`·`vector_note` 가 그 상태를 말한다 — 창의 길이는 코퍼스 × 인코더 속도다
    (2026-09-11 산정: 소유자 그래프 하나에 약 1시간 45분).
    근거: [[decision:bge-m3-adoption]] · [[record:embed-vector-dim-fixed-at-768-2026-08-30]]
    """
    import os
    import time

    from . import persistence
    from .backend import open_store
    from .config import load_config

    out: dict = {"kind": "embed.dim.migrate", "data": data_dir, "model": model,
                 "target_dim": target_dim, "changed": False}
    if not os.path.exists(data_dir):
        out["note"] = "no store yet — provision at boot creates it at the target width"
        return out
    t0 = time.perf_counter()
    src = open_store(load_config({"data_dir": data_dir}))
    if src is None:
        raise RuntimeError("store open failed (driver)")
    out_dir = f"{data_dir}.rebuild-{target_dim}"
    try:
        cur = vector_dim(src, "ChunkVec")
        out["current_dim"] = cur
        if cur is None:
            out["note"] = "ChunkVec not provisioned — nothing to migrate"
            return out
        if cur == target_dim:
            return out
        for part, _req in persistence._parts(out_dir):
            persistence._rm(part)                      # 지난 실패의 잔해
        dst = open_store(load_config({"data_dir": out_dir, "embedding_model": model}))
        if dst is None:
            raise RuntimeError("rebuild target open failed (driver)")
        try:
            dst.provision(dim=target_dim)
            res = rebuild_store(src, dst, model=model, dim=target_dim)
        finally:
            dst.close()
    finally:
        src.close()
    if res["docs"] != res["requeued"]:
        for part, _req in persistence._parts(out_dir):
            persistence._rm(part)
        raise RuntimeError(f"rebuild requeued {res['requeued']} of {res['docs']} documents — "
                           "the new store is discarded and the old one stays in place")
    keep = f"{data_dir}.pre-{cur}"
    for part, _req in persistence._parts(keep):
        persistence._rm(part)
    for part, _req in persistence._parts(data_dir):
        if os.path.exists(part):
            os.replace(part, keep + part[len(data_dir):])
    for part, _req in persistence._parts(out_dir):
        if os.path.exists(part):
            os.replace(part, data_dir + part[len(out_dir):])
    out.update(res)
    out.update(changed=True, previous_dim=cur, kept=keep,
               wall_s=round(time.perf_counter() - t0, 2))
    return out


def _main_rebuild(args, open_store, load_config) -> int:
    """`rebuild` 서브커맨드 — 원본은 **읽기만** 하고 결과는 새 디렉터리에 만든다.

    ⚠ 원본을 지우지 않는다. 새 스토어가 멀쩡한지 확인한 뒤 사람이 바꿔 끼우는 것이
    롤백 수단이다(재구축 실패는 조용하지 않아야 하고, 되돌릴 것이 있어야 한다)."""
    import os
    import sys

    if not (args.out and args.model):
        print("[ERROR] rebuild 에는 --out 과 --model 이 필요하다", file=sys.stderr)
        return 2
    if os.path.exists(args.out):
        print(f"[ERROR] 대상이 이미 있다: {args.out}", file=sys.stderr)
        return 2
    src = open_store(load_config())
    dst_cfg = load_config({"data_dir": args.out, "embedding_model": args.model})
    dst = open_store(dst_cfg)
    if src is None or dst is None:
        print("[ERROR] 스토어 연결 실패 (드라이버/자격 확인).", file=sys.stderr)
        return 1
    try:
        out = rebuild_store(src, dst, model=args.model, dim=dst_cfg.embedding_dim)
    finally:
        src.close()
        dst.close()
    print(f"[rebuild] → {args.out} · dim={out['dim']} 노드 {out['nodes']} · 엣지 "
          f"{out['edges']} · 메타 {out['meta']} · 문서 {out['docs']} · 재큐 "
          f"{out['requeued']} · 버린 벡터 {out['vectors_dropped']}")
    if out["docs"] != out["requeued"]:
        # 여기가 무음 실패의 자리였다 — 소리를 내고 실패로 끝낸다.
        print(f"[ERROR] 재큐가 문서 수와 다르다({out['requeued']} != {out['docs']}) "
              "— 재임베딩이 일부/전부 일어나지 않는다", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    import argparse
    import sys
    from .backend import open_store
    from .config import load_config

    ap = argparse.ArgumentParser(
        description="Vinc 논리 스토어 이관(T4) — 데이터 디렉토리 간 export/import.")
    ap.add_argument("mode", choices=["export", "import", "rebuild"])
    ap.add_argument("--file", help="덤프 JSON 경로(export/import 필수)")
    ap.add_argument("--out", help="rebuild 대상 데이터 디렉토리(비어 있어야 한다)")
    ap.add_argument("--model", help="rebuild 후 임베딩 모델(HF id)")
    args = ap.parse_args()

    if args.mode == "rebuild":
        sys.exit(_main_rebuild(args, open_store, load_config))
    if not args.file:
        ap.error("--file 이 필요하다")

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
