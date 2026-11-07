# -*- coding: utf-8 -*-
"""허브 도구 호출 클라이언트 — 호스티드 어댑터(`mcp.vincs.io/mcp`)에 JSON-RPC
`tools/call` 을 한 번 왕복한다. 지금 부르는 쪽은 `api/refs.py`(인용 대조)와
`scripts/agents_sync.py` 이고, `proxy.py` 는 주소(`_endpoint`)만 빌린다. MCP 도구
디스패처(`tools.call`)는 이 모듈을 부르지 않는다.

- stdlib `urllib` 만(F5 오프라인·외부 의존 0 유지).
- 인증: env `VINC_REMOTE_MCP_TOKEN`(회원 발급 키 `vinc_sk_*`).
  URL 은 env `VINC_REMOTE_MCP_URL`(기본 `https://mcp.vincs.io/mcp`).
- **owner 는 전송하지 않는다** — 허브가 토큰으로 owner 를 정하는 게 권위다(멀티테넌트).
  로컬 owner 를 실어 보내면 남의 스코프를 넘볼 여지가 되므로 제거한다.
- **`target` 도 전송하지 않는다** — 허브는 그 인자를 받으면 거절한다. 호출부가 실수로
  실어도 막히지 않도록 여기서 뗀다.
- 허브의 `_dispatch` 는 `tools/call` 을 initialize 없이 처리한다(rpc.py) → 단건 POST.
"""
from __future__ import annotations

import json
import os

from . import hubhttp

DEFAULT_REMOTE_URL = "https://mcp.vincs.io/mcp"


def _endpoint() -> str:
    return (os.environ.get("VINC_REMOTE_MCP_URL") or DEFAULT_REMOTE_URL).rstrip("/")


def call(name: str, args: dict, *, timeout: float = 60) -> dict:
    """원격 도구 1건 호출. 실패는 예외 대신 `{error, isError}` 로 강등(호스트가 사용자에게
    친절히 전달하도록)."""
    token = os.environ.get("VINC_REMOTE_MCP_TOKEN") or ""
    if not token:
        return {"error": "the hub call requires a key — set VINC_REMOTE_MCP_TOKEN "
                         "(get one at https://vincs.io/account).", "isError": True}
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name,
                   "arguments": {k: v for k, v in (args or {}).items()
                                 if k not in ("owner", "target")}},
    }
    try:
        data = hubhttp.post_json(_endpoint(), token=token, payload=payload, timeout=timeout)
    except hubhttp.HubHTTPError as e:
        if e.status is None:
            return {"error": f"remote unreachable: {type(e.cause).__name__}: {e.cause}", "isError": True}
        hint = " (key invalid or expired — reconnect at vincs.io/account)" if e.status == 401 else ""
        return {"error": f"remote HTTP {e.status}{hint}", "isError": True}
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
