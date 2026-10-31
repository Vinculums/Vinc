# -*- coding: utf-8 -*-
"""계정 모드의 stdio 세션을 호스티드 MCP 로 통째로 넘긴다(VIN-132 K4).

데스크톱의 로컬 등록은 호스트마다 `vinc mcp serve` 하나다. 앱이 계정 모드면(`~/.vinc/
config.json` 의 `mode: "account"`) 이 서버는 사이드카를 띄우지 않고, 호스트가 보낸
JSON-RPC 한 줄을 **그대로** `mcp.vincs.io/mcp` 로 보내고 답을 그대로 돌려준다. 그래서
호스트는 호스티드 도구 목록(`space`·`search`·`fetch` 포함)과 호스티드 `instructions` 를
본다 — 앱 화면이 보는 그래프와 같다.

- 키: 환경 `VINC_REMOTE_MCP_TOKEN`. 데스크톱에서는 런처가 키체인의 기기별 MCP 키를
  넣는다(K3). 키가 없으면 `initialize` 는 로그인하라는 안내로 답하고, 나머지 요청은 같은
  오류 하나로 답한다: 서버가 죽은 것처럼 보이지 않고, 사람에게 이유가 보이게.
- 세션: 호스티드가 준 `Mcp-Session-Id` 와 협상된 `MCP-Protocol-Version` 을 다음 요청에
  싣는다. 알림(id 없음)은 보내기만 하고 아무것도 쓰지 않는다.
- stdout 은 프로토콜 전용이다. 로그는 stderr.

근거: [[vinc.plan.mcp-follows-the-app-mode-vin-132-2026-09-29]]
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from typing import IO, Any

from . import __version__, hubhttp, modern, run_protocol
from .remote import _endpoint
from .rpc import LATEST_PROTOCOL_VERSION, PROTOCOL_VERSIONS, error_reply

#: 호스티드의 무거운 연산은 계정 쪽에서 45초까지 허용된다. 그보다 넉넉히 기다린다.
TIMEOUT_S = 90.0
#: JSON-RPC 서버 정의 오류 코드(-32000~-32099). 호스트는 message 를 사람에게 보인다.
ERR_ACCOUNT = -32001

SIGN_IN = ("Vinc is in account mode, but this computer has no MCP key. "
           "Sign in to the Vinc app, then restart this AI host.")
REFUSED = ("Your Vinc account refused this computer's MCP key (HTTP {code}). "
           "Sign in to the Vinc app again, then restart this AI host.")
UNREACHABLE = "Cannot reach your Vinc account right now ({why}). Try again in a moment."


def wants_account_mode(*, base: str | None, token: str | None, owner: str | None,
                       env: Mapping[str, str] | None = None,
                       config: dict | None = None) -> bool:
    """`mcp serve` 가 호스티드로 넘길 것인가.

    - 사이드카를 **명시로** 가리키면(`--base`·`--token`·`--owner`) 로컬이다: 그렇게
      부르는 것은 개발·시험·허브 쪽이고, 앱 모드와 무관하다
    - `VINC_MCP_MODE=account|local` 이 있으면 그것을 따른다(시험·수동 전환용)
    - 그 밖에는 앱 설정의 `mode` 가 `"account"` 일 때만 계정이다. **설정 파일이 없으면
      로컬이다**: 데스크톱 없이 CLI 만 쓰는 사람과 개발 환경은 지금처럼 로컬 스토어를
      본다. 데스크톱은 로그인 뒤 모드를 정하면서(`decideDataMode`) 설정을 적는다.
    """
    if base or token or owner:
        return False
    envmap: Mapping[str, str] = os.environ if env is None else env
    forced = str(envmap.get("VINC_MCP_MODE") or "").strip().lower()
    if forced in ("account", "local"):
        return forced == "account"
    if config is None:
        from store.config import read_local_config
        config = read_local_config()
    return str((config or {}).get("mode") or "") == "account"


def _fail(msg_id: Any, message: str) -> list[dict]:
    """계정 쪽 실패를 답 목록으로. 봉투는 `rpc.error_reply` 한 벌이고, 알림이면 빈 목록이다."""
    reply = error_reply(msg_id, ERR_ACCOUNT, message)
    return [reply] if reply else []


def _is_request(msg: Any) -> bool:
    """응답을 기다리는 요청인가. 알림(`id` 없음)에는 아무것도 돌려주지 않는다."""
    return isinstance(msg, dict) and "id" in msg and "method" in msg


class Proxy:
    """호스티드 MCP 로 가는 세션 하나. `handle` 은 stdout 에 쓸 메시지 목록을 준다."""

    def __init__(self, token: str, url: str | None = None, *, timeout: float = TIMEOUT_S):
        self.token = (token or "").strip()
        self.url = (url or _endpoint()).rstrip("/")
        self.timeout = timeout
        self.session_id: str | None = None
        self.protocol: str | None = None
        #: 이 프로세스에 붙은 클라이언트(initialize 의 clientInfo). 허브는 세션을 추적하지 않아
        #: 모르므로 run_start 의 host 를 여기서 채워 넘긴다.
        self.client = ""

    # ── 키가 없을 때 ────────────────────────────────────────────────────────
    def _signed_out(self, msg: Any) -> list[dict]:
        if not _is_request(msg):
            return []
        method = msg.get("method")
        if method == "initialize":
            want = (msg.get("params") or {}).get("protocolVersion")
            negotiated = want if want in PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
            return [{"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "vinc", "version": __version__},
                "instructions": SIGN_IN,
            }}]
        if method == "ping":
            return [{"jsonrpc": "2.0", "id": msg["id"], "result": {}}]
        return _fail(msg["id"], SIGN_IN)

    # ── 넘기기 ──────────────────────────────────────────────────────────────
    def handle(self, msg: Any) -> list[dict]:
        if not self.token:
            return self._signed_out(msg)
        msg = self._stamp_host(msg)
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        # 모던 요청은 자기 `_meta` 버전과 메서드·이름을 헤더로 비춘다(호스티드가 대조한다).
        # 세션도 협상도 없는 세대라 아래 두 헤더는 싣지 않는다.
        extra = modern.request_headers(msg) if isinstance(msg, dict) else {}
        if not extra:
            if self.session_id:
                extra["Mcp-Session-Id"] = self.session_id
            if self.protocol:
                extra["MCP-Protocol-Version"] = self.protocol
        msg_id = msg.get("id") if isinstance(msg, dict) else None
        try:
            resp = hubhttp.request(self.url, token=self.token, data=body, timeout=self.timeout,
                                   accept="application/json, text/event-stream", headers=extra)
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            ctype = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.text
        except Exception as e:  # noqa: BLE001 — 네트워크·시간 초과는 전부 요청의 오류로
            if isinstance(e, hubhttp.HubHTTPError):
                if e.status is not None:
                    return self._http_error(msg, msg_id, e)
                e = e.cause
            print(f"vinc-mcp: hosted MCP unreachable: {type(e).__name__}: {e}", file=sys.stderr)
            return _fail(msg_id, UNREACHABLE.format(why=type(e).__name__)) if _is_request(msg) else []
        replies = _parse(raw, ctype)
        if isinstance(msg, dict) and msg.get("method") == "initialize":
            for r in replies:
                pv = ((r.get("result") or {}).get("protocolVersion")
                      if isinstance(r, dict) else None)
                if pv:
                    self.protocol = pv
        return replies

    def _stamp_host(self, msg: Any) -> Any:
        if not isinstance(msg, dict):
            return msg
        params = msg.get("params")
        if not isinstance(params, dict):
            return msg
        if msg.get("method") == "initialize":
            self.client = run_protocol.client_label(params.get("clientInfo"))
            return msg
        if msg.get("method") != "tools/call" or not self.client:
            return msg
        args = run_protocol.with_host(params.get("name") or "", params.get("arguments"), self.client)
        if args is params.get("arguments"):
            return msg
        return {**msg, "params": {**params, "arguments": args}}

    def _http_error(self, msg: Any, msg_id: Any, e: hubhttp.HubHTTPError) -> list[dict]:
        raw = e.body
        # 호스티드가 JSON-RPC 오류 본문을 실어 보냈으면 그것이 가장 정확한 말이다.
        passed = [r for r in _parse(raw, ((e.headers or {}).get("Content-Type") or "").lower())
                  if isinstance(r, dict) and r.get("jsonrpc") == "2.0"]
        if passed:
            return passed if _is_request(msg) else []
        if not _is_request(msg):
            return []
        if e.status in (401, 403):
            return _fail(msg_id, REFUSED.format(code=e.status))
        return _fail(msg_id, UNREACHABLE.format(why=f"HTTP {e.status}"))


def _parse(raw: str, ctype: str) -> list[dict]:
    """응답 본문 → 메시지 목록. 빈 본문(202·204, 알림의 답)은 빈 목록이다."""
    if not raw.strip():
        return []
    if "text/event-stream" in ctype:
        out: list[dict] = []
        for event in raw.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in event.split("\n")
                             if line.startswith("data:"))
            if data.strip():
                try:
                    out.extend(_as_list(json.loads(data)))
                except ValueError:
                    print("vinc-mcp: skipping a non-JSON event from the hosted MCP", file=sys.stderr)
        return out
    try:
        return _as_list(json.loads(raw))
    except ValueError:
        print("vinc-mcp: the hosted MCP answered with a non-JSON body", file=sys.stderr)
        return []


def _as_list(x: Any) -> list[dict]:
    return [m for m in (x if isinstance(x, list) else [x]) if isinstance(m, dict)]


def serve_remote(token: str, *, url: str | None = None,
                 stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> int:
    """stdio 루프. `stdio.serve` 와 같은 줄 규약(한 줄에 JSON 하나)."""
    proxy = Proxy(token, url)
    src = stdin or sys.stdin
    dst = stdout or sys.stdout
    if not proxy.token:
        print("vinc-mcp: account mode without a key; answering with a sign-in message",
              file=sys.stderr)
    for line in src:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print("vinc-mcp: skipping non-JSON line", file=sys.stderr)
            continue
        for reply in proxy.handle(msg):
            dst.write(json.dumps(reply, ensure_ascii=False) + "\n")
            dst.flush()
    return 0
