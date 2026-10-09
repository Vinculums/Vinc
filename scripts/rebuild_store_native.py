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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store.backend import open_store          # noqa: E402
from store.config import load_config          # noqa: E402
from store.migrate import export_store, import_store  # noqa: E402
from store import embedded_schema             # noqa: E402

_EXTS = ("fts", "vector")
_PROBE_DOC = "probe.rebuild-write-check"


def _payload_digest(dump: dict) -> str:
    """물리 키·벡터·관계 속성·메타를 행 순서와 무관하게 대조한다."""
    payload = {}
    for section in ("nodes", "meta"):
        payload[section] = {
            label: sorted(json.dumps(row, sort_keys=True, ensure_ascii=False)
                          for row in rows)
            for label, rows in dump[section].items()
        }
    payload["edges"] = sorted(json.dumps(row, sort_keys=True, ensure_ascii=False)
                              for row in dump["edges"])
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


def _logical_export(s) -> dict:
    """정본에 없는 표나 열을 묵시적으로 버리지 않는다."""
    columns = {name: set(spec["cols"]) for name, spec in
               {**embedded_schema.node_table_specs(),
                **embedded_schema.meta_table_specs()}.items()}
    for spec in embedded_schema.rel_table_specs():
        columns.setdefault(spec["label"], set()).update(spec["props"])
    for name in _tables(s):
        if name not in columns:
            raise RuntimeError(f"logical rebuild cannot preserve unknown table: {name}")
        present = {row["name"] for row in s.run(f"CALL table_info('{name}') RETURN *")}
        extra = present - columns[name]
        if extra:
            raise RuntimeError(f"logical rebuild cannot preserve unknown columns: {name}: {sorted(extra)}")
    return export_store(s, raw=True)


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
    logical = None
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
            shutil.rmtree(dump)                   # EXPORT 는 없는 디렉터리를 만든다
            try:
                s._conn.execute(f"EXPORT DATABASE '{Path(dump).as_posix()}' (format='parquet')")
                report["export_method"] = "native"
            except RuntimeError as exc:
                # 0.21.2는 다중 끝점 관계의 네이티브 내보내기를 거절한다. 다른 실패를
                # 삼키지 않고, 이 경우만 기존 이관 경로로 벡터까지 그대로 옮긴다.
                message = str(exc)
                if not ("Export database: rel table " in message and
                        "connects multiple node pairs, which is not supported for "
                        "icebug-disk CSR export yet." in message):
                    raise
                logical = _logical_export(s)
                report["export_method"] = "logical"
                report["native_export_error"] = message
                report["vector_dim"] = s._stored_vec_dim(1024)
                report["src_payload_sha256"] = _payload_digest(logical)
        finally:
            s.close()
        report["export_s"] = round(time.monotonic() - t, 1)

        t = time.monotonic()
        d = _open(dst)
        try:
            if logical is None:
                d._conn.execute(f"IMPORT DATABASE '{Path(dump).as_posix()}'")
            else:
                import_store(d, logical, dim=report["vector_dim"])
            report["import_s"] = round(time.monotonic() - t, 1)
            t = time.monotonic()
            # 원본에 없던 검색 색인(예: 기동 수리 전 사본의 chunk_vec)도 세운다 — 대상은
            # 온전한 스토어여야 한다. 폭은 설정이 아니라 들여온 열에서 읽는다.
            d.ensure_search_indexes(dim=d._stored_vec_dim(1024))
            d.run("CHECKPOINT")
            report["index_s"] = round(time.monotonic() - t, 1)
            report["dst_counts"] = _counts(d)
            report["dst_indexes"] = _indexes(d)
            if logical is not None:
                report["dst_payload_sha256"] = _payload_digest(export_store(d, raw=True))
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
    if logical is not None and report["src_payload_sha256"] != report["dst_payload_sha256"]:
        problems.append("logical rebuild changed node, edge, vector, or metadata values")
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
