# -*- coding: utf-8 -*-
"""원격 MCP 프록시(A1.9 A2) — `target="remote"` 도구 호출을 호스티드 어댑터
(`mcp.vincs.io/mcp`)로 JSON-RPC `tools/call` 왕복한다.

- stdlib `urllib` 만(F5 오프라인·외부 의존 0 유지).
- 인증: env `VINC_REMOTE_MCP_TOKEN`(v0.1.9 수동; v0.2.0 회원 발급 키 `vinc_sk_*`).
  URL 은 env `VINC_REMOTE_MCP_URL`(기본 `https://mcp.vincs.io/mcp`).
- **owner 는 전송하지 않는다** — 원격이 토큰으로 owner 를 정하는 게 권위(멀티테넌트,
  v0.2.0). 로컬 owner 를 실어 보내면 남의 스코프를 넘볼 여지가 되므로 제거한다.
- **`target` 도 전송하지 않는다** — 그것은 "어느 스토어로 갈까"라는 **호출부의 질문**이고,
  이 함수를 부른 시점에 이미 답이 났다. 허브에는 갈 곳이 하나뿐이라 인자를 받으면
  거절한다(`target is not available on this server: this endpoint IS the remote graph`).
  tools.py 는 부르기 전에 pop 해서 멀쩡했지만 스크립트 셋은 그대로 실어 보내 전부
  막혀 있었다 — 그래서 호출부마다가 아니라 **여기서** 뗀다.
- 원격 서버의 `_dispatch` 는 `tools/call` 을 initialize 없이 처리한다(rpc.py) → 단건 POST.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_REMOTE_URL = "https://mcp.vincs.io/mcp"


def _endpoint() -> str:
    return (os.environ.get("VINC_REMOTE_MCP_URL") or DEFAULT_REMOTE_URL).rstrip("/")


def call(name: str, args: dict, *, timeout: float = 60) -> dict:
    """원격 도구 1건 호출. 실패는 예외 대신 `{error, isError}` 로 강등(호스트가 사용자에게
    친절히 전달하도록)."""
    token = os.environ.get("VINC_REMOTE_MCP_TOKEN") or ""
    if not token:
        return {"error": "remote target requires a key — set VINC_REMOTE_MCP_TOKEN "
                         "(get one at https://vincs.io/account).", "isError": True}
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name,
                   "arguments": {k: v for k, v in (args or {}).items()
                                 if k not in ("owner", "target")}},
    }
    req = urllib.request.Request(_endpoint(), data=json.dumps(payload).encode("utf-8"),
                                 method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        hint = " (key invalid or expired — reconnect at vincs.io/account)" if e.code == 401 else ""
        return {"error": f"remote HTTP {e.code}{hint}", "isError": True}
    except Exception as e:  # noqa: BLE001 — 네트워크/파싱 실패 전부 강등
        return {"error": f"remote unreachable: {type(e).__name__}: {e}", "isError": True}
    if data.get("error"):
        err = data["error"]
        return {"error": err.get("message", str(err)) if isinstance(err, dict) else str(err),
                "isError": True}
    result = data.get("result") or {}
    # MCP tools/call 결과 = {content:[{type:text, text:<json>}], isError}. 텍스트를
    # 역직렬화해 로컬 도구와 동일한 dict 형태로 정규화한다.
    content = result.get("content") or []
    if content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except Exception:
            return {"text": content[0].get("text", ""), "isError": result.get("isError", False)}
    return result
