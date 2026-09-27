"""CLI에서 MCP 도구를 같은 로컬 라우트로 호출한다."""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager

from store.config import load_config, resolve_data_dir
from vinc_mcp import tools

from .cli_transport import attached_bridge


class _InProcessBridge:
    def __init__(self, app):
        self.app = app
        self.owner = "user:local"

    def request(self, method, path, params=None, body=None, **_kw):
        from .server import _Handler

        # HTTP 셸과 같은 디스패치 표를 쓴다. 소켓과 두 번째 스토어는 만들지 않는다.
        if method == "GET":
            _status, result = _Handler._route_get(self, path, params or {})
        elif method == "POST":
            self._POST_WITHOUT_STORE = _Handler._POST_WITHOUT_STORE
            _status, result = _Handler._route_post(self, path, body or {})
        else:
            raise ValueError(f"unsupported local method: {method}")
        return result


@contextmanager
def _bridge(args):
    attached = attached_bridge(args)
    if attached is not None:
        yield attached
        return
    from .server import VincApp

    cfg = load_config({"data_dir": resolve_data_dir(getattr(args, "data", None))})
    app = VincApp.open(cfg)
    try:
        yield _InProcessBridge(app)
    finally:
        app.close()


def _confirm(args, subject: str) -> bool:
    if getattr(args, "yes", False):
        return True
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{subject}? Type yes to continue: ").strip() == "yes"
    except EOFError:
        return False


def _json_file(path: str) -> dict:
    stream = sys.stdin if path == "-" else open(path, encoding="utf-8")
    try:
        value = json.load(stream)
    finally:
        if stream is not sys.stdin:
            stream.close()
    if not isinstance(value, dict):
        raise ValueError("tool arguments must be a JSON object")
    return value


def _tool_request(args) -> tuple[str, dict, str | None]:
    cmd = args.cmd
    owner = getattr(args, "owner", None)
    common = {"owner": owner} if owner else {}
    if cmd == "call":
        name = args.tool if args.tool.startswith("vinc_") else "vinc_" + args.tool
        if name not in {t["name"] for t in tools.TOOLS}:
            raise ValueError(f"unknown tool: {args.tool}")
        if args.args is not None:
            data = json.loads(args.args)
            if not isinstance(data, dict):
                raise ValueError("tool arguments must be a JSON object")
        else:
            data = _json_file(args.args_file or "-")
        destructive = name if name in {"vinc_delete", "vinc_delete_edge",
                                       "vinc_merge_nodes", "vinc_doc_delete"} else None
        return name, data, destructive
    if cmd == "whoami":
        return "vinc_whoami", common, None
    if cmd == "conventions":
        return "vinc_conventions", {**common, "lang": args.lang}, None
    if cmd == "node":
        if args.nodecmd == "get":
            name, data = "vinc_brief", {"query": args.id}
        elif args.nodecmd == "neighbors":
            name, data = "vinc_neighbors", {"node": args.id, "limit": args.limit}
        else:
            name, data = "vinc_graph", {"seed_id": args.id, "depth": args.depth}
        return name, {**common, **data}, None
    if cmd == "path":
        return "vinc_path", {**common, "a": args.a, "b": args.b,
                             "max_depth": args.max_depth}, None
    if cmd == "ties":
        return "vinc_suggest_ties", {**common, "node": args.id,
                                     "limit": args.limit}, None
    if cmd == "diagnose":
        return "vinc_diagnose", {**common, "kind": args.kind,
                                 "node": args.node}, None
    if cmd == "ingest":
        return "vinc_ingest", {**common, **_json_file(args.file)}, None
    if cmd == "delete":
        return "vinc_delete", {**common, "node_id": args.id}, args.id
    if cmd == "edge":
        return "vinc_delete_edge", {**common, "from_id": args.from_id,
                                    "to_id": args.to_id, "type": args.type}, "edge"
    if cmd == "merge":
        if args.mergecmd == "candidates":
            return "vinc_merge_candidates", {**common, "limit": args.limit}, None
        if args.mergecmd == "rejections":
            return "vinc_merge_rejections", common, None
        if args.mergecmd == "reject":
            return "vinc_merge_reject", {**common, "a": args.a, "b": args.b,
                                         "undo": args.undo, "note": args.note}, None
        return "vinc_merge_nodes", {**common, "keep_id": args.keep,
                                    "drop_id": args.drop}, args.drop
    if cmd == "doc" and args.doccmd == "extract":
        return "vinc_doc_extract", {**common, "doc_id": args.id,
                                    "apply": args.apply}, None
    raise ValueError(f"no tool mapping for {cmd}")


def run(args) -> tuple[dict | None, int | None]:
    try:
        name, data, destructive = _tool_request(args)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"vinc: {exc}", file=sys.stderr)
        return None, 2
    if destructive and not _confirm(args, destructive):
        print("vinc: confirmation required; pass --yes", file=sys.stderr)
        return None, 2
    try:
        tools.validate_args(name, data)
    except tools.InvalidArgument as exc:
        print(f"vinc: {exc}", file=sys.stderr)
        return None, 2
    with _bridge(args) as bridge:
        try:
            return tools.call(name, data, bridge), None
        except tools.InvalidArgument as exc:
            print(f"vinc: {exc}", file=sys.stderr)
            return None, 2
