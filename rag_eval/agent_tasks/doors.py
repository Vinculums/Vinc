"""A2 의 다른 문 — 같은 로컬 스택·같은 읽기 키에 MCP 대신 REST v1 이나 CLI 로 붙는다.

| 비교군 | 모델이 받는 것 |
|---|---|
| A2 | MCP ``tools/list`` 그대로(:mod:`arms`) |
| A2-rest | ``vinc_http`` 도구 하나와 라우트 표(``vinc_mcp/rest_routes.py`` 에서 만든다) |
| A2-cli | ``vinc`` 도구 하나와 도구 동사의 도움말(제품 CLI 를 ``VINC_TARGET=account`` 로 돌린다) |

과제·모델·호스트·도구 호출 상한은 A2 와 같고 문만 다르다. 문의 결과는 MCP 봉투 꼴
(``content``·``isError``)로 돌려주어 :class:`hosts.ToolRunner` 와 분류·흔적이 그대로 쓴다.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

DOOR_ARMS = ("A2-rest", "A2-cli")
ROOT = pathlib.Path(__file__).resolve().parents[2]

HTTP_TOOL = {
    "name": "vinc_http",
    "description": "Send one request to the Vinc REST API (v1) and get its status and JSON body back. "
                   "The routes are listed in your instructions.",
    "parameters": {"type": "object", "required": ["method", "path"], "properties": {
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "path": {"type": "string", "description": "Starts with /v1/. Percent-encode path values."},
        "query": {"type": "object", "description": "Query string parameters."},
        "body": {"type": "object", "description": "JSON body for POST, PUT and PATCH."}}}}

CLI_TOOL = {
    "name": "vinc",
    "description": "Run one Vinc command line and get its output (JSON). Give the arguments after "
                   "`vinc`, for example: search --query \"release checklist\". The commands are "
                   "listed in your instructions; `<command> --help` shows a command's options.",
    "parameters": {"type": "object", "required": ["command"], "properties": {
        "command": {"type": "string", "description": "The arguments after `vinc`."}}}}

#: 자세한 사용법을 미리 실어 주는 동사. 나머지 도구 동사는 한 줄 설명만 싣는다.
CLI_DETAIL = (("search",), ("brief",), ("recall",), ("node", "get"), ("node", "neighbors"),
              ("edges",), ("doc", "get"), ("doc", "ls"), ("path",), ("decide",))
#: 모든 동사에 붙는 전역 선택지. 사용법을 줄일 때 뺀다.
_GLOBAL = re.compile(r"\s*\[(?:-h|--target \{local,account\}|--space SPACE|"
                     r"--format \{human,json,ndjson\}|--json|--debug)\]")
#: 문이 받지 않는 선택지: 대상 바꾸기와 이 기계의 파일 읽기, 확인 건너뛰기.
_REFUSED = ("--target", "--file", "--args-file", "--yes", "--data")


def _envelope(text: str, error: bool, **extra) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error, **extra}


def _query_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value) if isinstance(value, (dict, list)) else str(value)


def _param_line(where: str, name: str, schema: dict) -> str:
    kind = schema.get("type") or ("one of " + ", ".join(map(str, schema["enum"])) if schema.get("enum") else "")
    about = _first_sentence(schema.get("description") or "")
    return f"{where} {name}" + (f" ({kind})" if kind else "") + (f": {about}" if about else "")


def _first_sentence(text: str) -> str:
    head = (text or "").strip().split("\n", 1)[0]
    return re.split(r"(?<=[.!?])\s", head, maxsplit=1)[0]


class RestDoor:
    """``vinc_http`` 한 도구. 로컬 스택의 ``/v1`` 밖으로는 보내지 않는다."""

    arm = "A2-rest"

    def __init__(self, base_url: str, key: str, *, timeout: float = 120.0) -> None:
        self.base, self.key, self.timeout = base_url.rstrip("/"), key, timeout

    def tools(self) -> list[dict]:
        return [HTTP_TOOL]

    def manual(self, mcp_tools: list[dict]) -> str:
        """라우트 표. MCP 문이 받는 것과 같은 정보를 싣는다: 라우트가 부르는 도구의 설명 전부와
        인자마다 타입·설명 첫 문장. REST 의 몫은 인자가 어디로 가는지와 응답 꼴뿐이다.

        근거: 첫 문장만 실은 첫 판에서 REST 가 MCP 보다 유의하게 낮았다(Sonnet 탐색 회차, 2026-10-28).
        """
        from vinc_mcp import rest_routes   # noqa: PLC0415
        by_name = {t["name"]: t for t in mcp_tools}
        lines = ["Vinc REST API v1. Call it with the vinc_http tool. Each route runs the Vinc tool "
                 "named in its heading and answers with that tool's JSON. Arguments go in `query` "
                 "(GET, DELETE) or `body` (POST, PUT, PATCH); values in {braces} go in the path."]
        groups: dict[str, list] = {}
        for r in rest_routes.ROUTES:
            groups.setdefault(r.tool, []).append(r)
        for tool, routes in groups.items():
            spec = by_name.get(tool) or {}
            text = " ".join((spec.get("description") or "").split())
            lines.append(f"\n## {tool}" + (f": {text}" if text else ""))
            props = (spec.get("parameters") or {}).get("properties") or {}
            for r in routes:
                lines.append(f"- {r.method} {r.path}")
                for where, names in (("path", tuple(arg for arg, _ in r.path_args)),
                                     ("query", r.query), ("body", r.body)):
                    for name in names:
                        lines.append("  - " + _param_line(where, name, props.get(
                            dict(r.path_args).get(name, name), {})))
        return "\n".join(lines)

    def call(self, name: str, args: dict) -> dict:
        if name != HTTP_TOOL["name"]:
            return _envelope(f"unknown tool {name}; the only tool is vinc_http", True)
        method = str(args.get("method") or "GET").upper()
        path = str(args.get("path") or "")
        if method not in HTTP_TOOL["parameters"]["properties"]["method"]["enum"]:
            return _envelope(f"method must be one of GET, POST, PUT, PATCH, DELETE, not {method}", True)
        if not path.startswith("/v1/") or ".." in path.split("?", 1)[0].split("/"):
            return _envelope("path must start with /v1/", True)
        query = args.get("query") if isinstance(args.get("query"), dict) else {}
        url = self.base + path
        if query:
            url += ("&" if "?" in path else "?") + urllib.parse.urlencode(
                {k: _query_value(v) for k, v in query.items()})
        body = None
        if method in ("POST", "PUT", "PATCH"):
            body = json.dumps(args.get("body") if isinstance(args.get("body"), dict) else {}).encode()
        req = urllib.request.Request(url, data=body, method=method, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
            "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return _envelope(f"HTTP {resp.status}\n{resp.read().decode('utf-8', 'replace')}", False)
        except urllib.error.HTTPError as exc:
            return _envelope(f"HTTP {exc.code}\n{exc.read().decode('utf-8', 'replace')}", True)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return _envelope(f"transport error: {type(exc).__name__}", True, _transport_error=True)


class CliDoor:
    """``vinc`` 한 도구. 제품 CLI 를 이 하네스의 인터프리터로, 계정 대상(로컬 스택의 ``/v1``)으로 돌린다.

    도구 동사(``api/cli_target.py`` ``TOOL_CMDS``)만 받는다. 이 기계 전용 동사(serve, backup 등)와
    대상 바꾸기·파일 읽기 선택지는 거절한다. 홈은 빈 임시 폴더라 사용자 설정을 읽지 않는다.
    """

    arm = "A2-cli"

    def __init__(self, base_url: str, key: str, *, timeout: float = 120.0, python: str | None = None,
                 run=subprocess.run) -> None:
        self.base, self.key, self.timeout = base_url.rstrip("/"), key, timeout
        self.python = python or sys.executable
        self._run = run
        self.home = tempfile.mkdtemp(prefix="rag-eval-cli-")

    def tools(self) -> list[dict]:
        return [CLI_TOOL]

    def _exec(self, argv: list[str]) -> subprocess.CompletedProcess:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.home,
               "USERPROFILE": self.home, "VINC_TARGET": "account", "VINC_API_URL": self.base,
               "VINC_API_KEY": self.key, "VINC_FORMAT": "json", "MEMORY_DATA_DIR": self.home}
        if os.environ.get("SYSTEMROOT"):
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        code = ("import sys; sys.path.insert(0, sys.argv.pop(1)); sys.argv[0] = 'vinc'; "
                "from api.cli import main; raise SystemExit(main())")
        return self._run([self.python, "-X", "utf8", "-c", code, str(ROOT), *argv],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         cwd=self.home, env=env, timeout=self.timeout)

    def manual(self, mcp_tools: list[dict] | None = None) -> str:
        """도구 동사 목록과 자주 쓰는 동사의 사용법. 제품 CLI 의 도움말에서 만든다."""
        from api.cli_target import TOOL_CMDS   # noqa: PLC0415
        top = self._exec(["--help"]).stdout.split("\noptions:", 1)[0]
        verbs = []
        for m in re.finditer(r"^    ([a-z][a-z-]*)\s{2,}(.+?)(?=^    [a-z]|\Z)", top, re.M | re.S):
            if m.group(1) in TOOL_CMDS:
                verbs.append(f"- {m.group(1)}: {' '.join(m.group(2).split())}")
        lines = ["Vinc command line. Call it with the vinc tool: pass the arguments after `vinc`. "
                 "Output is JSON. Commands:", *verbs, "", "Usage of the common commands:"]
        for verb in CLI_DETAIL:
            text = self._exec([*verb, "--help"]).stdout
            usage = text.split("\n\n", 1)[0]
            lines.append(" ".join(_GLOBAL.sub("", usage).split()).replace("usage: ", ""))
        lines.append("Run `<command> --help` for the options of any other command.")
        return "\n".join(lines)

    def call(self, name: str, args: dict) -> dict:
        if name != CLI_TOOL["name"]:
            return _envelope(f"unknown tool {name}; the only tool is vinc", True)
        from api.cli_target import TOOL_CMDS   # noqa: PLC0415
        try:
            argv = shlex.split(str(args.get("command") or ""))
        except ValueError as exc:
            return _envelope(f"could not split the command: {exc}", True)
        if argv[:1] == ["vinc"]:
            argv = argv[1:]
        if not argv:
            return _envelope("give a command, for example: search --query \"...\"", True)
        if argv[0] not in TOOL_CMDS and argv[0] not in ("--help", "-h"):
            return _envelope(f"{argv[0]} is not available here; use one of the listed commands", True)
        if any(a.split("=", 1)[0] in _REFUSED for a in argv):
            return _envelope("this command line may not change its target or read local files", True)
        try:
            done = self._exec(argv)
        except subprocess.TimeoutExpired:
            return _envelope("the command timed out", True, _transport_error=True)
        out = done.stdout if done.stdout.strip() else done.stderr
        return _envelope(out.strip(), done.returncode != 0)


def build(arm: str, base_url: str, key: str):
    if arm == "A2-rest":
        return RestDoor(base_url, key)
    if arm == "A2-cli":
        return CliDoor(base_url, key)
    raise ValueError(f"no door for {arm}")


def describe(door, manual: str) -> dict:
    return {"tools": [t["name"] for t in door.tools()],
            "manual_chars": len(manual),
            "manual_sha256": hashlib.sha256(manual.encode("utf-8")).hexdigest()}
