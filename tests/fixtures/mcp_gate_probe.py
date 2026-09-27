import json
import os
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, os.getcwd())
from vinc_mcp import http_server as H

TEAM = "0198c0de-1111-7222-8333-444455556666"
OTHER = "0198c0de-2222-7333-8444-555566667777"
state = {"role": "viewer", "scope": "full", "denial": None}

class Bridge:
    owner = "user:local"
    def request(self, method, path, params=None, body=None, **kw):
        return {"kind": path.strip("/"), "executed": True, "rows": [], "owner":
                (body or params or {}).get("owner")}

def verify(_tok, team=None):
    if team is None:
        return {"owner": "user:u1", "plan": "free", "scope": state["scope"]}
    if team == TEAM:
        return {"owner": "team:" + team, "plan": "team", "scope": state["scope"],
                "role": state["role"], "member": "user:u1"}
    return None

app = H.MCPHTTPApp(Bridge(), static_token="test", verify_key=verify)
app.quota_check = lambda owner, plan: state["denial"]
server = H.make_server(app, "127.0.0.1", 0)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:" + str(server.server_address[1])

def call(i, name, **args):
    return {"jsonrpc":"2.0","id":i,"method":"tools/call",
            "params":{"name":name,"arguments":args}}

def post(message):
    req = urllib.request.Request(base + "/mcp", data=json.dumps(message).encode(),
        method="POST", headers={"Authorization":"Bearer vinc_sk_test",
                                "Content-Type":"application/json",
                                "MCP-Protocol-Version":"2025-06-18"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.read()

out = []
def add(name, message):
    out.append(name.encode() + b"\t" + post(message))

try:
    add("invalid", call(1,"vinc_search",query="x",limit=999))
    add("space", call(2,"vinc_search",query="x",space=OTHER))
    add("role", call(3,"vinc_ingest",nodes=[],space=TEAM))
    state["scope"] = "read"
    add("readonly", call(4,"vinc_ingest",nodes=[]))
    state["scope"] = "full"
    state["denial"] = {"isError":True,"content":[{"type":"text","text":"daily limit"}]}
    add("quota", call(5,"vinc_search",query="x"))
    state["denial"] = None
    add("batch", [call(6,"vinc_domains"), call(7,"vinc_domains",space=TEAM)])
finally:
    server.shutdown()
    server.server_close()
Path(sys.argv[1]).write_bytes(b"\n".join(out)+b"\n")
