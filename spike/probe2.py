import json
from vinc_mcp.rpc import Dispatcher
from spike import apps

d = Dispatcher(bridge=None)
apps.install(d)

def rpc(m, p=None):
    return d.handle({"jsonrpc": "2.0", "id": 1, "method": m, "params": p or {}})

out = {}
out["initialize.capabilities"] = rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]["capabilities"]
out["resources/list"] = rpc("resources/list").get("result", rpc("resources/list").get("error"))
r = rpc("resources/read", {"uri": apps.UI_URI})
c = r["result"]["contents"][0]
out["resources/read"] = {"uri": c["uri"], "mimeType": c["mimeType"], "html_chars": len(c["text"])}
out["resources/read(bad uri)"] = rpc("resources/read", {"uri": "ui://nope"}).get("error")
tl = rpc("tools/list")["result"]["tools"]
out["tool_with_meta"] = [{"name": t["name"], "_meta": t["_meta"]} for t in tl if "_meta" in t]
out["tools_count"] = len(tl)
print(json.dumps(out, ensure_ascii=False, indent=2))
