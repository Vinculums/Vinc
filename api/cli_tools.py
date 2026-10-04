"""CLI에서 MCP 도구를 같은 로컬 라우트로 호출한다."""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import urlencode
from store import diagnostics
from contextlib import contextmanager

from store.config import load_config
from vinc_mcp import tools
from vinc_mcp.bridge import DEFAULT_OWNER

from .cli_transport import attached_bridge, data_file
from .cli_output import usage_error


class _InProcessBridge:
    def __init__(self, app):
        self.app = app
        # 붙은 사이드카의 Bridge 와 같은 규칙 — 앱이 떠 있든 아니든 같은 owner 로 쓴다.
        self.owner = os.environ.get("VINC_OWNER") or DEFAULT_OWNER

    def request(self, method, path, params=None, body=None, **_kw):
        from .server import _Handler

        # HTTP 셸과 같은 디스패치 표를 쓴다. 소켓과 두 번째 스토어는 만들지 않는다.
        started = time.perf_counter()
        if method == "GET":
            _status, result = _Handler._route_get(self, path, params or {})
        elif method == "POST":
            self._POST_WITHOUT_STORE = _Handler._POST_WITHOUT_STORE
            _status, result = _Handler._route_post(self, path, body or {})
        else:
            raise ValueError(f"unsupported local method: {method}")
        route = path + ("?" + urlencode(params) if params else "")
        diagnostics.trace(method, route, _status, started, transport="local")
        return result


@contextmanager
def _bridge(args):
    attached = attached_bridge(args)
    if attached is not None:
        yield attached
        return
    from .server import VincApp

    # 붙기 판정과 같은 파일을 연다(cli_transport.data_file).
    cfg = load_config({"data_dir": data_file(args)})
    app = VincApp.open(cfg, quiet_boot=not diagnostics.enabled())
    try:
        yield _InProcessBridge(app)
    finally:
        app.close()


def _confirm(args, subject: str) -> bool:
    """사람이 터미널 앞에 있을 때만 묻는다. 묻는 말은 stderr 로 — stdout 은 결과 자리다.
    Windows 의 NUL 은 isatty() 가 참이라 stdin 만으로는 판정하지 않는다."""
    if getattr(args, "yes", False):
        return True
    if not (sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()):
        return False
    try:
        sys.stderr.write(f"{subject}? Type yes to continue: ")
        sys.stderr.flush()
        return sys.stdin.readline().strip() == "yes"
    except (EOFError, OSError):
        return False


def legacy_recall(args) -> bool:
    """`recall` 의 옛 관련도 인자. 도구(vinc_recall)에는 없어서 사이드카 `/recall` 이 답한다."""
    return args.cmd == "recall" and bool(
        args.query or args.tenant or args.workspace or args.rank != "recency")


def _json_file(path: str, *, source: str = "JSON input") -> dict:
    stream = sys.stdin if path == "-" else open(path, encoding="utf-8")
    try:
        try:
            value = json.load(stream)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source}: {exc}") from None
    finally:
        if stream is not sys.stdin:
            stream.close()
    if not isinstance(value, dict):
        raise ValueError(f"{source}: tool arguments must be a JSON object")
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
            try:
                data = json.loads(args.args)
            except json.JSONDecodeError as exc:
                raise ValueError(f"--args: {exc}") from None
            if not isinstance(data, dict):
                raise ValueError("--args: tool arguments must be a JSON object")
        elif args.args_file is None and sys.stdin.isatty():
            # 터미널에서 인자 없이 부르면 stdin 을 기다리며 멈춘 것처럼 보인다.
            raise ValueError("pass --args JSON, --args-file PATH, or pipe JSON on stdin")
        else:
            data = _json_file(args.args_file or "-",
                              source="--args-file" if args.args_file else "stdin")
        destructive = name if name in {"vinc_delete", "vinc_delete_edge",
                                       "vinc_merge_nodes", "vinc_doc_delete"} else None
        return name, data, destructive
    # 구 동사 9개의 출력도 도구 결과 모양으로 통일한다(C6, D7).
    if cmd == "search":
        return "vinc_search", {**common, "query": args.query,
                               "limit": args.top_k, "cursor": args.cursor,
                               "detail": args.detail}, None
    if cmd == "recall":
        if legacy_recall(args):
            raise ValueError("--query, --tenant, --workspace and --rank preference work on "
                             "the local target only; use search for relevance queries")
        tags = [part.strip() for part in (args.tags or "").split(",") if part.strip()]
        return "vinc_recall", {**common, "domain": args.domain,
                               "tags": tags or None, "limit": args.limit}, None
    if cmd == "brief":
        return "vinc_brief", {**common, "query": args.query,
                              "max_excerpts": args.max_excerpts}, None
    if cmd == "decide":
        return "vinc_decide", {**common, "topic": args.topic,
                               "depth": args.depth}, None
    if cmd == "domains":
        return "vinc_domains", common, None
    if cmd == "doc":
        if args.doccmd == "get":
            return "vinc_doc_get", {"doc_id": args.id, **common}, None
        if args.doccmd == "ls":
            return "vinc_doc_list", common, None
        if args.doccmd == "rm":
            return "vinc_doc_delete", {"doc_id": args.id, **common}, args.id
        if args.doccmd == "put":
            if args.file:
                from . import textextract
                import os
                got = textextract.read_file(args.file)
                if not got.get("executed"):
                    raise ValueError(str(got.get("error") or "cannot read document file"))
                text = got["text"]
                origin = args.origin or os.path.basename(args.file)
                title = args.title or os.path.splitext(os.path.basename(args.file))[0]
            else:
                text = sys.stdin.read()
                origin, title = args.origin, args.title
            return "vinc_doc_put", {"text": text, "doc_id": args.id,
                                    "title": title, "origin": origin, **common}, None
    if cmd == "whoami":
        return "vinc_whoami", common, None
    if cmd == "conventions":
        return "vinc_conventions", {**common, "lang": args.lang}, None
    if cmd == "node":
        if args.nodecmd == "get":
            name, data = "vinc_node", {"node_id": args.id}
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
    if legacy_recall(args):
        # 옛 인자를 쓰는 스크립트는 옛 모양(사이드카 `/recall`)을 그대로 받는다.
        params = {k: getattr(args, k) for k in
                  ("query", "owner", "domain", "tenant", "workspace", "rank", "tags", "limit")}
        with _bridge(args) as bridge:
            return bridge.request("GET", "/recall", params={k: v for k, v in params.items()
                                                            if v not in (None, "")}), None
    try:
        name, data, destructive = _tool_request(args)
    except (ValueError, json.JSONDecodeError) as exc:
        return None, usage_error(str(exc))
    if destructive and not _confirm(args, destructive):
        return None, usage_error("confirmation required; pass --yes")
    try:
        tools.validate_args(name, data)
    except tools.InvalidArgument as exc:
        return None, usage_error(str(exc))
    with _bridge(args) as bridge:
        try:
            return tools.call(name, data, bridge), None
        except tools.InvalidArgument as exc:
            return None, usage_error(str(exc))
