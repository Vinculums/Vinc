"""로컬 그래프의 결정론적 Markdown/CSV 내보내기."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path

from .cli_transport import attached_bridge, data_file

_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
#: 파일 이름 조각의 상한. 경로 전체가 Windows 의 260자 벽에 닿지 않게 둔다.
_STEM_MAX = 96


def _scoped(domain: str, selected: str | None) -> bool:
    selected = (selected or "").rstrip("/")
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
    from store.config import load_config

    cfg = load_config({"data_dir": data_file(args)})
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
    # sync 번들이 개념 one_liner 와 관계 근거를 직접 나른다. graph 읽기는 그 값을 싣지
    # 않는 옛 사이드카 번들과 다른 종류의 노드를 채우는 대체 경로라, 번들 값을 덮지 않는다.
    if not graph.get("executed"):
        bundle["executed"] = False
        bundle["error"] = graph.get("error") or graph.get("note") or "graph detail unavailable"
        return
    details = {n["id"]: n for n in graph.get("nodes", [])}
    for node in bundle.get("nodes", []):
        if node.get("one_liner") is None:
            node["one_liner"] = details.get(node["id"], {}).get("one_liner") or ""
    from .sync import _REL_TO_FRAGMENT
    evidence = {(e.get("source"), e.get("target"),
                 e.get("role") or _REL_TO_FRAGMENT.get(e.get("type"),
                                                       str(e.get("type") or "").lower())):
                e.get("evidence") for e in graph.get("edges", [])}
    for edge in bundle.get("edges", []):
        if not edge.get("evidence"):
            edge["evidence"] = evidence.get(
                (edge.get("from"), edge.get("to"), edge.get("type"))) or ""


def _safe_name(raw: str) -> str:
    """경로 조각 하나. 구분자·`..`·예약 장치 이름(`nul.txt` 처럼 점 앞 부분 포함)을 막는다."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(". -") or "item"
    if name.split(".", 1)[0].upper() in _RESERVED:
        name = "_" + name
    if len(name) > _STEM_MAX:
        name = name[:_STEM_MAX - 9].rstrip(". -") + "-" + hashlib.sha256(
            raw.encode("utf-8")).hexdigest()[:8]
    return name


def _safe_stem(raw: str, used: set[str]) -> str:
    """id 의 접두 뒤 부분으로 파일 이름을 짓는다. 대소문자만 다른 이름은 대소문자를 가리지
    않는 파일 시스템(Windows·macOS)에서 한 파일이 되므로, 뒤에 오는 쪽에 해시를 붙인다."""
    stem = _safe_name(raw.split(":", 1)[-1])
    if stem.casefold() in used:
        stem = _safe_name(stem + "-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8])
    used.add(stem.casefold())
    return stem


def _yaml(value) -> str:
    return json.dumps(value if value is not None else "", ensure_ascii=False, sort_keys=True)


def _front(keys: list[str], obj: dict) -> str:
    return "---\n" + "".join(f"{key}: {_yaml(obj.get(key))}\n" for key in keys) + "---\n"


def _cell(value, *, excel: bool):
    if isinstance(value, (dict, list)):
        value = _yaml(value)
    # Excel 은 =·+·-·@ 로 시작하는 칸을 수식으로 실행한다. --excel 일 때만 막는다 —
    # 기본 CSV 는 바이트 그대로가 계약이다.
    if excel and isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _csv_bytes(header: list[str], rows: list[dict], *, excel: bool) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(row.get(key, ""), excel=excel) for key in header])
    return ("\ufeff" if excel else "").encode("utf-8") + stream.getvalue().encode("utf-8")


#: 열과 앞머리 키. **뒤에만 붙인다** — 기존 열의 이름·순서는 소비자가 기대는 계약이다.
_NODE_KEYS = ["id", "type", "title", "domain", "tags", "valid_from", "valid_until",
              "one_liner", "props", "content_time", "created", "date", "summary"]
_NODE_FRONT = ["id", "kind", "title", "domain", "tags", "valid_from", "valid_until",
               "one_liner", "content_time", "created", "date", "summary"]
_EDGE_KEYS = ["from", "to", "type", "evidence", "confidence", "created"]
_DOC_TIME = ["content_time", "created_at", "valid_from", "valid_until", "superseded_by"]
_DOC_KEYS = ["doc_id", "title", "topic", "origin", "updated_at", "content_hash", "text",
             *_DOC_TIME]
_DOC_FRONT = ["doc_id", "title", "topic", "origin", "updated_at", "content_hash", *_DOC_TIME]


def _node_row(node: dict) -> dict:
    """번들 노드를 내보내기 행으로. 출생은 번들에서 `_created` 스탬프로 온다."""
    return {**node, "created": node.get("created") or node.get("_created") or ""}


def build_files(owner: str, fmt: str, node_bundle: dict, doc_bundle: dict,
                doc_list: dict, *, domain: str | None = None, excel: bool = False) -> dict[str, bytes]:
    nodes = sorted((_node_row(n) for n in node_bundle.get("nodes", [])
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
        files["nodes.csv"] = _csv_bytes(_NODE_KEYS, nodes, excel=excel)
        files["edges.csv"] = _csv_bytes(_EDGE_KEYS, edges, excel=excel)
        files["documents.csv"] = _csv_bytes(_DOC_KEYS, docs, excel=excel)
    else:
        used_nodes: dict[str, set[str]] = {}
        kind_dirs: dict[str, str] = {}
        for node in nodes:
            kind = node.get("type") or "node"
            # 개방 타입은 사용자가 짓는 문자열이다 — 디렉터리 이름으로 쓰기 전에 거른다.
            # 대소문자만 다른 타입은 먼저 나온 이름의 한 폴더를 같이 쓴다.
            folder = kind_dirs.setdefault(_safe_name(kind).casefold(), _safe_name(kind))
            stem = _safe_stem(node["id"], used_nodes.setdefault(folder.casefold(), set()))
            front = _front(_NODE_FRONT, {**node, "kind": kind})
            props = json.dumps(node.get("props") or {}, ensure_ascii=False,
                               indent=2, sort_keys=True)
            relations = "".join(
                f"- {e['type']} -> [[{e['to']}]]" +
                (f" — {e['evidence']}" if e.get("evidence") else "") + "\n"
                for e in edges if e["from"] == node["id"])
            files[f"nodes/{folder}/{stem}.md"] = (
                front + "\n```json\n" + props + "\n```\n\n" + relations).encode("utf-8")
        used_docs: set[str] = set()
        for doc in docs:
            stem = _safe_stem(doc["doc_id"], used_docs)
            front = _front(_DOC_FRONT, doc)
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
    base = root.resolve()
    for name, content in files.items():
        path = (root / name).resolve()
        if base not in path.parents:
            return {"kind": "export", "executed": False,
                    "error": f"refusing to write outside the output directory: {name}"}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    # 쓴 파일을 되세어 본다. 파일 시스템이 두 이름을 하나로 합쳤으면 여기서 드러난다.
    written = {p.relative_to(root).as_posix().casefold() for p in root.rglob("*") if p.is_file()}
    missing = sorted(n for n in files if n.casefold() not in written)
    if missing or len(written) < len(files):
        return {"kind": "export", "executed": False,
                "error": "some files were not written as named", "missing": missing[:20]}
    return {"kind": "export", "executed": True, "out": str(root),
            "format": args.file_format, "files": len(files),
            "sha256": hashlib.sha256(b"".join(
                name.encode("utf-8") + b"\0" + files[name]
                for name in sorted(files))).hexdigest()}
