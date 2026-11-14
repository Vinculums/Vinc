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
from vinc_mcp import toolcall, tools
from vinc_mcp.bridge import DEFAULT_OWNER

from .cli_transport import attached_bridge, data_file
from .cli_output import usage_error


class _InProcessBridge:
    def __init__(self, app):
        self.app = app
        # 붙은 사이드카의 Bridge 와 같은 규칙 — 앱이 떠 있든 아니든 같은 owner 로 쓴다.
        self.owner = os.environ.get("VINC_OWNER") or DEFAULT_OWNER

    def request(self, method, path, params=None, body=None, **_kw):
        from . import routes

        # HTTP 셸과 같은 디스패치(`api.routes`)를 쓴다. 소켓과 두 번째 스토어는 만들지 않는다.
        started = time.perf_counter()
        if method == "GET":
            _status, result = routes.dispatch(self.app, method, path, params or {})
        elif method == "POST":
            _status, result = routes.dispatch(self.app, method, path, body or {})
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


def confirm(args, subject: str) -> bool:
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


def _text_file(path: str) -> str:
    """검사할 산출물 원문. `-` 는 stdin 이고 바이트를 고치지 않는다."""
    if path == "-":
        return sys.stdin.read()
    try:
        with open(path, encoding="utf-8") as stream:
            return stream.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"--output: {exc}") from None


def tool_request(args) -> tuple[str, dict, str | None]:
    cmd = args.cmd
    owner = getattr(args, "owner", None)
    common = {"owner": owner} if owner else {}
    if cmd in {"ingest", "delete", "edge", "merge", "doc"}:
        common.update({key: getattr(args, key) for key in ("operation_id", "dry_run", "preview_digest",
            "create_only", "expected_content_hash") if getattr(args, key, None) is not None})
    if cmd == "operation":
        return "vinc_operation_status", {**common, "operation_id": args.operation_id}, None
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
        # 되돌릴 수 없는 도구(admin 등급)는 확인을 받는다. 목록을 따로 두지 않고 MCP 호스트가 확인을 거는
        # 기준(`destructiveHint` 에서 유도한 `tool_tier`)을 그대로 쓴다.
        destructive = name if tools.tool_tier(name) == "admin" else None
        return name, data, destructive
    # 구 동사 9개의 출력도 도구 결과 모양으로 통일한다(C6, D7).
    temporal = {key: getattr(args, key) for key in ("mode", "historical", "as_of", "known_at")
                if getattr(args, key, None) is not None} if cmd in ("search", "brief") else {}
    if cmd == "search":
        return "vinc_search", {**common, "query": args.query,
                               "limit": args.top_k, "cursor": args.cursor,
                               "detail": args.detail, **temporal}, None
    if cmd == "recall":
        if legacy_recall(args):
            raise ValueError("--query, --tenant, --workspace and --rank preference work on "
                             "the local target only; use search for relevance queries")
        tags = [part.strip() for part in (args.tags or "").split(",") if part.strip()]
        return "vinc_recall", {**common, "domain": args.domain,
                               "tags": tags or None, "limit": args.limit}, None
    if cmd == "brief":
        return "vinc_brief", {**common, "query": args.query,
                              "max_excerpts": args.max_excerpts, **temporal}, None
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
            # 생략(None)은 보존, 빈 문자열은 지우기다(`vinc_doc_put` 계약). 파일 이름 기본값은
            # 만들 때만 쓴다: `--id` 로 고치는 문서의 제목·origin 을 파일 이름으로 덮으면 안 된다.
            origin, title = args.origin, args.title
            if args.file:
                from . import textextract
                import os
                got = textextract.read_file(args.file)
                if not got.get("executed"):
                    raise ValueError(str(got.get("error") or "cannot read document file"))
                text = got["text"]
                if not args.id or getattr(args, "create_only", None):
                    name = os.path.basename(args.file)
                    origin = name if origin is None else origin
                    title = os.path.splitext(name)[0] if title is None else title
            else:
                text = sys.stdin.read()
            return "vinc_doc_put", {"text": text, "doc_id": args.id,
                                    "title": title, "origin": origin, **common,
                                    **{key: getattr(args, key) for key in (
                                        "topic", "valid_from", "valid_until", "superseded_by")
                                       if getattr(args, key, None) is not None}}, None
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
            if getattr(args, "review_mirror", False):
                data["review_mirror"] = True
        return name, {**common, **data}, None
    if cmd == "edges":
        # 단건(위치 인자)과 묶음(--nodes)은 도구 계약처럼 둘 중 하나만이다. 둘 다·둘 다 없음은
        # validate_args 가 같은 문장으로 거절한다.
        data = {"node": args.id, "nodes": args.nodes, "type": args.type,
                "dir": args.dir, "limit": args.limit}
        return "vinc_edges", {**common, **{k: v for k, v in data.items() if v is not None}}, None
    if cmd == "role":
        if args.rolecmd == "export":
            return "vinc_role_export", {**common, "role": args.id, "format": args.format}, None
        if args.rolecmd == "get":
            return "vinc_role", {**common, "role": args.id,
                                 **({"compact": True} if getattr(args, "compact", None) else {})}, None
        data = {**common, "role": args.id, "output": _text_file(args.output)}
        if args.person_only_requests is not None:
            try:
                requests = json.loads(args.person_only_requests)
            except json.JSONDecodeError as exc:
                raise ValueError(f"--person-only-requests: {exc}") from None
            if not isinstance(requests, list):
                raise ValueError("--person-only-requests: expected a JSON array")
            data["person_only_requests"] = requests
        if args.self_authored:
            data["self_authored"] = True
        return "vinc_role_check", data, None
    if cmd == "events":
        data = {"since": args.since, "role": args.role, "kinds": args.kind, "limit": args.limit}
        return "vinc_events", {**common, **{k: v for k, v in data.items()
                                             if v not in (None, "", [])}}, None
    if cmd == "run":
        if args.runcmd == "start":
            data = {"role": args.role, "task": args.task, "subject": args.subject,
                    "run_id": args.run_id, "host": args.host}
            return "vinc_run_start", {**common, **{k: v for k, v in data.items()
                                                    if v not in (None, "")}}, None
        if args.runcmd == "submit":
            data = {"run": args.run, "answer": args.answer, "cited_ids": args.cited,
                    "verdict": getattr(args, "verdict", None),
                    "verdict_note": getattr(args, "verdict_note", None)}
            if args.abandon:
                data["abandon"] = True
            if args.output is not None:
                data["output"] = _text_file(args.output)
            for flag, key in (("person_only_requests", "person_only_requests"), ("usage", "usage")):
                raw = getattr(args, flag)
                if raw is not None:
                    try:
                        data[key] = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"--{flag.replace('_', '-')}: {exc}") from None
            return "vinc_run_submit", {**common, **{k: v for k, v in data.items()
                                                     if v not in (None, "", [])}}, None
        if args.runcmd == "show":
            return "vinc_runs", {**common, "run": args.run}, None
        data = {"cited": args.cited, "role": args.role, "limit": args.limit}
        return "vinc_runs", {**common, **{k: v for k, v in data.items()
                                           if v not in (None, "")}}, None
    if cmd == "eval":
        data = {"role": args.role, "limit": args.limit}
        return "vinc_eval_cards", {**common, **{k: v for k, v in data.items()
                                                 if v not in (None, "")}}, None
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
        data = {**common, **_json_file(args.file)}
        if getattr(args, "review_claims", False):
            data["review_claims"] = True
        return "vinc_ingest", data, None
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


# 사설 별칭. 같은 함수이고, 이 이름으로 부르는 호출부가 그대로 돈다.
_confirm = confirm
_tool_request = tool_request


def run(args) -> tuple[dict | None, int | None]:
    if legacy_recall(args):
        # 옛 인자를 쓰는 스크립트는 옛 모양(사이드카 `/recall`)을 그대로 받는다.
        params = {k: getattr(args, k) for k in
                  ("query", "owner", "domain", "tenant", "workspace", "rank", "tags", "limit")}
        with _bridge(args) as bridge:
            return bridge.request("GET", "/recall", params={k: v for k, v in params.items()
                                                            if v not in (None, "")}), None
    try:
        name, data, destructive = tool_request(args)
    except (ValueError, json.JSONDecodeError) as exc:
        return None, usage_error(str(exc))
    if destructive and not data.get("dry_run") and not confirm(args, destructive):
        return None, usage_error("confirmation required; pass --yes")
    # 검증은 `toolcall.call` 이 브리지를 열기 전에 한다. 거절되는 인자로는 스토어를 열지 않는다.
    try:
        return toolcall.call(name, data, open_bridge=lambda: _bridge(args)), None
    except tools.InvalidArgument as exc:
        return None, usage_error(str(exc))
