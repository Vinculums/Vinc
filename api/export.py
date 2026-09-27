"""로컬 그래프의 결정론적 Markdown/CSV 내보내기."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path

from .cli_transport import attached_bridge


def _scoped(domain: str, selected: str | None) -> bool:
    return not selected or domain == selected or domain.startswith(selected + "/")


def _payload(args) -> tuple[dict, dict, dict]:
    bridge = attached_bridge(args)
    if bridge is not None:
        nodes = bridge.request("GET", "/sync/pull-delta",
                               params={"owner": args.owner, "since": 0, "all": "true"})
        docs = bridge.request("GET", "/sync/docs-export",
                              params={"owner": args.owner, "since": 0, "all": "true"})
        listed = bridge.request("GET", "/docs", params={"owner": args.owner,
                                                         "detail": "full"})
        graph = bridge.request("GET", "/graph", params={"owner": args.owner})
        _enrich(nodes, graph)
        return nodes, docs, listed

    from . import docstore, docsync, query, sync
    from .server import VincApp
    from store.config import load_config, resolve_data_dir

    cfg = load_config({"data_dir": resolve_data_dir()})
    app = VincApp.open(cfg)
    try:
        if app.store is None:
            return {"executed": False, "error": "no reachable store"}, {}, {}
        nodes = sync.export_delta(owner=args.owner, since=0, everything=True,
                                  cfg=cfg, store=app.store)
        _enrich(nodes, query.graph(owner=args.owner, store=app.store))
        return (nodes, docsync.export_docs(app.store, owner=args.owner, since=0,
                                           everything=True),
                docstore.list_docs(app.store, owner=args.owner, detail="full"))
    finally:
        app.close()


def _enrich(bundle: dict, graph: dict) -> None:
    # sync 번들은 내용 필드를 보존하지만 표시용 one_liner와 관계 근거는 뺀다.
    # 같은 소유자의 전체 graph 읽기 한 번으로 둘을 채운다.
    if not graph.get("executed"):
        bundle["executed"] = False
        bundle["error"] = graph.get("error") or graph.get("note") or "graph detail unavailable"
        return
    details = {n["id"]: n for n in graph.get("nodes", [])}
    for node in bundle.get("nodes", []):
        node["one_liner"] = details.get(node["id"], {}).get("one_liner") or ""
    from .sync import _REL_TO_FRAGMENT
    evidence = {(e.get("source"), e.get("target"),
                 e.get("role") or _REL_TO_FRAGMENT.get(e.get("type"),
                                                       str(e.get("type") or "").lower())):
                e.get("evidence") for e in graph.get("edges", [])}
    for edge in bundle.get("edges", []):
        edge["evidence"] = evidence.get((edge.get("from"), edge.get("to"), edge.get("type"))) or ""


def _safe_stem(raw: str, used: set[str]) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", raw.split(":", 1)[-1]).strip(". -") or "item"
    if stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                        *(f"LPT{i}" for i in range(1, 10))}:
        stem += "-item"
    key = stem.casefold()
    if key in used:
        stem += "-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        key = stem.casefold()
    used.add(key)
    return stem


def _yaml(value) -> str:
    return json.dumps(value if value is not None else "", ensure_ascii=False, sort_keys=True)


def _front(keys: list[str], obj: dict) -> str:
    return "---\n" + "".join(f"{key}: {_yaml(obj.get(key))}\n" for key in keys) + "---\n"


def _csv_bytes(header: list[str], rows: list[dict], *, excel: bool) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_yaml(row.get(key)) if isinstance(row.get(key), (dict, list))
                         else row.get(key, "") for key in header])
    return ("\ufeff" if excel else "").encode("utf-8") + stream.getvalue().encode("utf-8")


def build_files(owner: str, fmt: str, node_bundle: dict, doc_bundle: dict,
                doc_list: dict, *, domain: str | None = None, excel: bool = False) -> dict[str, bytes]:
    nodes = sorted((n for n in node_bundle.get("nodes", [])
                    if _scoped(n.get("domain") or "", domain)),
                   key=lambda n: n["id"].encode("utf-8"))
    ids = {n["id"] for n in nodes}
    edges = sorted((e for e in node_bundle.get("edges", [])
                    if e.get("from") in ids and e.get("to") in ids),
                   key=lambda e: (e.get("from", ""), e.get("type", ""), e.get("to", "")))
    topics = {d["doc_id"]: d.get("topic") or "" for d in doc_list.get("docs", [])}
    docs = sorted((dict(d, topic=topics.get(d["doc_id"], ""))
                   for d in doc_bundle.get("docs", [])
                   if _scoped(topics.get(d["doc_id"], ""), domain)),
                  key=lambda d: d["doc_id"].encode("utf-8"))
    files: dict[str, bytes] = {}
    if fmt == "csv":
        files["nodes.csv"] = _csv_bytes(
            ["id", "type", "title", "domain", "tags", "valid_from", "valid_until",
             "one_liner", "props"], nodes, excel=excel)
        files["edges.csv"] = _csv_bytes(
            ["from", "to", "type", "evidence"], edges, excel=excel)
        files["documents.csv"] = _csv_bytes(
            ["doc_id", "title", "topic", "origin", "updated_at", "content_hash", "text"],
            docs, excel=excel)
    else:
        used_nodes: dict[str, set[str]] = {}
        for node in nodes:
            kind = node.get("type") or "node"
            stem = _safe_stem(node["id"], used_nodes.setdefault(kind, set()))
            front = _front(["id", "kind", "title", "domain", "tags", "valid_from",
                            "valid_until", "one_liner"],
                           {**node, "kind": kind})
            props = json.dumps(node.get("props") or {}, ensure_ascii=False,
                               indent=2, sort_keys=True)
            relations = "".join(
                f"- {e['type']} -> [[{e['to']}]]" +
                (f" — {e['evidence']}" if e.get("evidence") else "") + "\n"
                for e in edges if e["from"] == node["id"])
            files[f"nodes/{kind}/{stem}.md"] = (
                front + "\n```json\n" + props + "\n```\n\n" + relations).encode("utf-8")
        used_docs: set[str] = set()
        for doc in docs:
            stem = _safe_stem(doc["doc_id"], used_docs)
            front = _front(["doc_id", "title", "topic", "origin", "updated_at",
                            "content_hash"], doc)
            files[f"documents/{stem}.md"] = (front + doc.get("text", "")).encode("utf-8")
        files["vinc-export.json"] = (json.dumps(
            {"format_version": 1, "owner": owner,
             "counts": {"nodes": len(nodes), "edges": len(edges), "documents": len(docs)}},
            ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return files


def export_store(args) -> dict:
    if args.excel and args.file_format != "csv":
        return {"kind": "export", "executed": False,
                "error": "--excel requires --format csv"}
    nodes, docs, listed = _payload(args)
    if not nodes.get("executed") or not listed.get("executed") or "docs" not in docs:
        return {"kind": "export", "executed": False,
                "error": nodes.get("error") or nodes.get("note") or docs.get("error") or
                         listed.get("error") or "export source unavailable"}
    files = build_files(args.owner, args.file_format, nodes, docs, listed,
                        domain=args.domain, excel=args.excel)
    root = Path(args.out)
    if root.exists() and not root.is_dir():
        return {"kind": "export", "executed": False, "error": "output is not a directory"}
    if root.exists():
        existing = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        if existing - set(files):
            return {"kind": "export", "executed": False,
                    "error": "output contains files outside this export; choose a fresh directory"}
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return {"kind": "export", "executed": True, "out": str(root),
            "format": args.file_format, "files": len(files),
            "sha256": hashlib.sha256(b"".join(
                name.encode("utf-8") + b"\0" + files[name]
                for name in sorted(files))).hexdigest()}
