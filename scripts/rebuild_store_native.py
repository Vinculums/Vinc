#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스토어를 엔진의 `EXPORT DATABASE` → `IMPORT DATABASE` 로 **새 파일에** 다시 짓는다.

왜: 크래시가 남긴 인덱스 손상(행이 없는 곳을 가리키는 `_PK` 항목 — 쓰기가
"Primary-key index contains an invalid local node offset" 로 거절된다)은 행에서
인덱스를 새로 세우는 것 말고 고칠 길이 없다. 행 단위 이관(`store.migrate`)은 벡터를
파이썬으로 나르느라 몇 시간이 걸리고, 네이티브 경로는 모든 행과 인덱스를 1분 남짓에
옮긴다(2026-09-25 허브 사본 실측 약 70초, 표 11개 행 수 일치, `_PK` 해시 전부 재생성).
근거: [[vinc.ops.container-apps-cost-rise-2026-09-25]]

원본은 **읽기만 한다.** 대상은 새 경로이고(`--out` 은 없어야 한다), 롤백은 원본을
그대로 쓰는 것이다. 판정이 하나라도 어긋나면 대상을 지우고 1로 끝난다:

  1. 표마다 행 수가 원본과 같다(노드·관계 전부)
  2. 원본의 인덱스(표·이름)가 대상에 전부 있다
  3. 쓰기 검사가 통과한다 — 대상의 **버릴 사본**에 문서 하나를 넣고 지운다.
     같은 검사를 원본의 버릴 사본에도 돌려 "전에는 어땠고 지금은 된다"를 나란히
     남긴다(원본 쪽 결과는 판정에 쓰지 않는다 — 손상이 그 문서의 키를 안 밟을 수 있다).
  4. 비ASCII 키가 전부 PK 등호 조회로 찾힌다. 0.20.4 이하가 지은 PK 색인을 0.21 이
     열면 꼬리(마지막 `길이 % 8` 바이트)에 0x80 이상 바이트가 있는 키를 놓친다. 스캔에는
     보이고, 같은 키로 MERGE 하면 둘째 행이 생긴다. 원본 쪽 수로는 거절하지 않는다.

허브와 같은 키 규약으로 돌 것: `VINC_PK_NAMESPACED=on` 이면 쓰기 검사가 물리 키로 쓴다.

    python scripts/rebuild_store_native.py --data work/db.x --out work/db.x.rebuilt --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

try:
    from scripts._root import ROOT  # noqa: F401
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT  # noqa: F401

from store.backend import open_store          # noqa: E402
from store.config import load_config          # noqa: E402

_EXTS = ("fts", "vector")
_PROBE_DOC = "probe.rebuild-write-check"


def _catalog(s) -> dict:
    """원본 카탈로그 그대로: 노드 표의 열(타입·기본값·PK 순서까지)과 관계 표의 열·끝점 쌍."""
    plan: dict = {"nodes": {}, "rels": {}}
    for name, kind in sorted(_tables(s).items()):
        info = s.run(f"CALL table_info('{name}') RETURN *")
        if kind == "NODE":
            plan["nodes"][name] = info
        elif kind == "REL":
            if any(r.get("storage_direction", "both") != "both" for r in info):
                raise RuntimeError(f"copy rebuild cannot preserve storage direction: {name}")
            plan["rels"][name] = {"cols": info,
                                  "pairs": s.run(f"CALL show_connection('{name}') RETURN *")}
        else:
            raise RuntimeError(f"copy rebuild cannot preserve table kind {kind}: {name}")
    return plan


def _column(r: dict) -> str:
    default = r.get("default expression")
    return f"{r['name']} {r['type']}" + ("" if default in (None, "NULL") else f" DEFAULT {default}")


def _copies(plan: dict):
    """(파일, 원본 읽기 쿼리, 행 수 쿼리, 대상 표, COPY FROM 옵션). 노드 표가 관계보다 먼저 온다."""
    for name, cols in plan["nodes"].items():
        proj = ", ".join(f"n.{c['name']} AS {c['name']}" for c in cols)
        yield (name, f"MATCH (n:{name}) RETURN {proj}",
               f"MATCH (n:{name}) RETURN count(n)", name, "")
    for name, rel in plan["rels"].items():
        for p in rel["pairs"]:
            a, b = p["source table name"], p["destination table name"]
            match = f"MATCH (a:{a})-[r:{name}]->(b:{b})"
            proj = ", ".join([f"a.{p['source table primary key']} AS _from",
                              f"b.{p['destination table primary key']} AS _to"]
                             + [f"r.{c['name']} AS {c['name']}" for c in rel["cols"]])
            yield (f"{name}__{a}__{b}", f"{match} RETURN {proj}", f"{match} RETURN count(r)",
                   name, f" (from='{a}', to='{b}')")


def _scalar(s, query: str) -> int:
    return int(s._conn.execute(query).get_next()[0])


def _copy_out(s, plan: dict, dump: str) -> list[str]:
    """행이 있는 표·쌍마다 parquet 하나. 엔진이 흘려 쓰므로 행을 파이썬으로 올리지 않는다."""
    written = []
    for file, read, count, _, _ in _copies(plan):
        if _scalar(s, count):
            s._conn.execute(f"COPY ({read}) TO '{Path(dump, file + '.parquet').as_posix()}'")
            written.append(file)
    return written


def _copy_in(d, plan: dict, dump: str, written: list[str]) -> None:
    for name, cols in plan["nodes"].items():
        pk = next(c["name"] for c in cols if c["primary key"])
        d._conn.execute(f"CREATE NODE TABLE {name}({', '.join(_column(c) for c in cols)}, "
                        f"PRIMARY KEY({pk}))")
    for name, rel in plan["rels"].items():
        ends = ", ".join(f"FROM {p['source table name']} TO {p['destination table name']}"
                         for p in rel["pairs"])
        d._conn.execute(f"CREATE REL TABLE {name}({ends}"
                        + "".join(", " + _column(c) for c in rel["cols"]) + ")")
    for file, _, _, table, options in _copies(plan):
        if file in written:
            d._conn.execute(f"COPY {table} FROM '{Path(dump, file + '.parquet').as_posix()}'{options}")


def _rows_digest(s, plan: dict) -> dict[str, list]:
    """표·쌍마다 [행 수, 행 해시의 합]. 순서와 무관한 다중집합 대조라 정렬도 적재도 없다."""
    out = {}
    for file, read, _, _, _ in _copies(plan):
        result = s._conn.execute(read)
        n, acc = 0, 0
        while result.has_next():
            row = json.dumps(result.get_next(), ensure_ascii=False, default=str, separators=(",", ":"))
            acc = (acc + int.from_bytes(hashlib.sha256(row.encode("utf-8")).digest(), "big")) % (1 << 256)
            n += 1
        out[file] = [n, f"{acc:064x}"]
    return out


def _open(path: str):
    s = open_store(load_config({"data_dir": path}))
    if s is None:
        from store.embedded_backend import EmbeddedBackend
        raise RuntimeError(f"스토어를 못 연다: {path}: {EmbeddedBackend.last_open_error}")
    for ext in _EXTS:        # 확장 색인(FTS·벡터)은 확장이 실려 있어야 내보내고 들인다
        s._load_ext(ext)
    return s


def _tables(s) -> dict[str, str]:
    return {str(r["name"]): str(r["type"]).upper()
            for r in s.run("CALL show_tables() RETURN name, type")}


def _counts(s) -> dict[str, int]:
    out = {}
    for name, kind in sorted(_tables(s).items()):
        q = (f"MATCH (n:{name}) RETURN count(n) AS c" if kind == "NODE"
             else f"MATCH ()-[r:{name}]->() RETURN count(r) AS c")
        rows = s.run(q)
        out[name] = int(rows[0]["c"]) if rows else 0
    return out


def _indexes(s) -> list[str]:
    rows = s.run("CALL show_indexes() RETURN table_name AS t, index_name AS i")
    return sorted(f"{r['t']}.{r['i']}" for r in rows)


def _files(path: str) -> list[str]:
    return [p for p in (path, path + ".wal") if os.path.exists(p)]


def _size(path: str) -> int:
    return sum(os.path.getsize(p) for p in _files(path))


def _pk_misses(s) -> dict[str, dict[str, int]]:
    """노드 표마다 비ASCII PK 수와 그중 등호 조회가 놓친 수. 비ASCII 키가 없는 표는 뺀다."""
    out = {}
    for name, kind in sorted(_tables(s).items()):
        if kind != "NODE":
            continue
        pk = next((r["name"] for r in s.run(f"CALL table_info('{name}') RETURN *")
                   if r.get("primary key")), None)
        if pk is None:
            continue
        keys = [r["k"] for r in s.run(f"MATCH (n:{name}) RETURN n.{pk} AS k", raw=True)
                if isinstance(r.get("k"), str) and not r["k"].isascii()]
        if not keys:
            continue
        missed = sum(1 for k in keys if not s.run(
            f"MATCH (n:{name}) WHERE n.{pk} = $k RETURN count(n) AS c", {"k": k}, raw=True)[0]["c"])
        out[name] = {"keys": len(keys), "missed": missed}
    return out


def _write_probe(path: str) -> dict:
    """``path`` 의 **버릴 사본**에 문서 하나를 넣고 지운다. 원본은 건드리지 않는다."""
    from api import docstore
    work = tempfile.mkdtemp(prefix="vinc-probe-")
    copy = os.path.join(work, "db")
    for f in _files(path):
        shutil.copy2(f, copy + f[len(path):])
    s = None
    try:
        s = _open(copy)
        rows = s.run("MATCH (m:_DocMeta) RETURN m.owner_id AS o, count(m) AS c "
                     "ORDER BY c DESC LIMIT 1")
        owner = rows[0]["o"] if rows and rows[0].get("o") else "user:probe"
        put = docstore.put_doc(s, owner=owner, doc_id=_PROBE_DOC, title="Rebuild write check",
                               text="A document written to check that the rebuilt store "
                                    "accepts writes.\n\n" + "Body line. " * 30,
                               topic="vinc/infra/hub")
        if not put.get("executed"):
            return {"ok": False, "stage": "put", "error": str(put)[:500]}
        gone = docstore.delete_doc(s, _PROBE_DOC, owner=owner)
        if not gone.get("executed", True):
            return {"ok": False, "stage": "delete", "error": str(gone)[:500]}
        return {"ok": True}
    except Exception as exc:                      # noqa: BLE001 — 결과로 보고한다
        return {"ok": False, "stage": "open/put", "error": f"{type(exc).__name__}: {exc}"[:500]}
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:                     # noqa: BLE001
                pass
        shutil.rmtree(work, ignore_errors=True)


def rebuild(src: str, dst: str) -> dict:
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    if _files(dst):
        raise FileExistsError(f"--out 은 없는 경로여야 한다: {dst}")
    report: dict = {"src": src, "dst": dst, "src_bytes": _size(src)}
    dump = tempfile.mkdtemp(prefix="vinc-export-")
    source_work = tempfile.mkdtemp(prefix="vinc-rebuild-source-")
    source_copy = os.path.join(source_work, "db")
    plan = written = None
    try:
        # 엔진은 읽기 쿼리만 실행해도 닫을 때 체크포인트를 쓸 수 있다. 원본과 WAL은
        # 열지 않고 사본에서 내보낸다. 호출자는 닫힌 스토어나 고정된 백업을 넘긴다.
        for f in _files(src):
            shutil.copy2(f, source_copy + f[len(src):])
        t = time.monotonic()
        s = _open(source_copy)
        try:
            report["src_counts"] = _counts(s)
            report["src_indexes"] = _indexes(s)
            report["src_pk_misses"] = _pk_misses(s)
            shutil.rmtree(dump)                   # EXPORT 는 없는 디렉터리를 만든다
            try:
                s._conn.execute(f"EXPORT DATABASE '{Path(dump).as_posix()}' (format='parquet')")
                report["export_method"] = "native"
            except RuntimeError as exc:
                # 0.21.2는 다중 끝점 관계의 네이티브 내보내기를 거절한다. 다른 실패를
                # 삼키지 않고, 이 경우만 원본 카탈로그대로 표마다 엔진 COPY 로 옮긴다.
                # 행을 파이썬으로 올리는 이관 경로는 허브 크기에서 러너 메모리를 넘었다.
                message = str(exc)
                if not ("Export database: rel table " in message and
                        "connects multiple node pairs, which is not supported for "
                        "icebug-disk CSR export yet." in message):
                    raise
                os.makedirs(dump, exist_ok=True)
                plan = _catalog(s)
                written = _copy_out(s, plan, dump)
                report["export_method"] = "copy"
                report["native_export_error"] = message
                report["copied_files"] = len(written)
                report["src_rows_digest"] = _rows_digest(s, plan)
        finally:
            s.close()
        report["export_s"] = round(time.monotonic() - t, 1)

        t = time.monotonic()
        d = _open(dst)
        try:
            if plan is None:
                d._conn.execute(f"IMPORT DATABASE '{Path(dump).as_posix()}'")
            else:
                _copy_in(d, plan, dump, written)
            report["import_s"] = round(time.monotonic() - t, 1)
            t = time.monotonic()
            # 원본에 없던 검색 색인(예: 기동 수리 전 사본의 chunk_vec)도 세운다 — 대상은
            # 온전한 스토어여야 한다. 폭은 설정이 아니라 들여온 열에서 읽는다.
            d.ensure_search_indexes(dim=d._stored_vec_dim(1024))
            # 일반 검색 보장은 Chunk 색인만 만든다. 원본에 있던 노드 미러 색인은
            # 모든 벡터를 들인 뒤 따로 복원하고, 생성 실패는 그대로 거절한다.
            if ("NodeVec.node_vec" in report["src_indexes"] and
                    "NodeVec.node_vec" not in _indexes(d)):
                error = d.create_node_vector_index()
                if error:
                    raise RuntimeError(f"NodeVec index rebuild failed: {error}")
            d.run("CHECKPOINT")
            report["index_s"] = round(time.monotonic() - t, 1)
            report["dst_counts"] = _counts(d)
            report["dst_indexes"] = _indexes(d)
            report["dst_pk_misses"] = _pk_misses(d)
            if plan is not None:
                report["dst_rows_digest"] = _rows_digest(d, plan)
        finally:
            d.close()
    finally:
        shutil.rmtree(dump, ignore_errors=True)
        shutil.rmtree(source_work, ignore_errors=True)
    report["dst_bytes"] = _size(dst)

    problems = [f"{name}: {n} -> {report['dst_counts'].get(name)}"
                for name, n in report["src_counts"].items()
                if report["dst_counts"].get(name) != n]
    problems += [f"index missing: {i}" for i in report["src_indexes"]
                 if i not in report["dst_indexes"]]
    problems += [f"{name}: {v['missed']} of {v['keys']} non-ASCII keys miss the primary key index"
                 for name, v in report["dst_pk_misses"].items() if v["missed"]]
    if plan is not None:
        problems += [f"{file}: rows changed in the copy rebuild"
                     for file, digest in report["src_rows_digest"].items()
                     if report["dst_rows_digest"].get(file) != digest]
    report["probe_before"] = _write_probe(src)
    report["probe_after"] = _write_probe(dst)
    if not report["probe_after"]["ok"]:
        problems.append(f"write probe failed on the rebuilt store: {report['probe_after']}")
    report["problems"] = problems
    report["ok"] = not problems
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True, help="원본 DB 파일(.wal 은 옆에서 찾는다)")
    ap.add_argument("--out", required=True, help="새로 지을 DB 파일 경로(없어야 한다)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    existed = bool(_files(a.out))                 # 이미 있던 파일은 이 스크립트의 것이 아니다
    try:
        report = rebuild(a.data, a.out)
    except Exception as exc:                      # noqa: BLE001
        report = {"ok": False, "problems": [f"{type(exc).__name__}: {exc}"]}
    if not report["ok"] and not existed:
        for f in _files(a.out):                   # 반쯤 지은 것은 남기지 않는다
            os.remove(f)
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for k, v in report.items():
            print(f"{k}: {v}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
