# -*- coding: utf-8 -*-
"""W11 스파이크 프로브 — MCP Apps 계약을 이 서버가 어디까지 답하는가.

핸드셰이크 3종(initialize / tools/list / resources/*)은 브리지를 건드리지 않으므로
사이드카 없이 돈다. tools/call 만 사이드카가 필요하다(라운드트립 하네스에서 별도).
"""
import json, sys
from vinc_mcp.rpc import Dispatcher

d = Dispatcher(bridge=None)  # 핸드셰이크는 bridge 를 안 쓴다

def rpc(method, params=None, _id=1):
    return d.handle({"jsonrpc": "2.0", "id": _id, "method": method,
                     "params": params or {}})

out = {}
for want in ("2026-07-28", "2025-11-25", "2025-06-18"):
    r = rpc("initialize", {"protocolVersion": want,
                           "capabilities": {}, "clientInfo": {"name": "w11", "version": "0"}})
    out[f"initialize({want})"] = r["result"]

for m in ("resources/list", "resources/templates/list", "resources/read",
          "resources/subscribe", "prompts/list"):
    r = rpc(m, {"uri": "ui://vinc/trace"})
    out[m] = r.get("error", r.get("result"))

tl = rpc("tools/list")["result"]["tools"]
out["tools_count"] = len(tl)
out["tool_has_meta"] = sorted({k for t in tl for k in t if k.startswith("_")})
out["tool_keys_sample"] = sorted(tl[0].keys())
print(json.dumps(out, ensure_ascii=False, indent=2))
