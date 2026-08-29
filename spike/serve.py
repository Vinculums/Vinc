# -*- coding: utf-8 -*-
"""W11: 실제 MCP HTTP 서버 + ui:// 오버레이. 포트 8788(8787 은 다른 세션과 겹친다)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vinc_mcp.bridge import Bridge
from vinc_mcp.http_server import MCPHTTPApp, make_server
from spike import apps

bridge = Bridge(base="http://127.0.0.1:8789", token="w11side")
app = MCPHTTPApp(bridge, static_token="w11spike")
apps.install(app._dispatcher)
httpd = make_server(app, "127.0.0.1", 8788)
print("listening 8788", flush=True)
httpd.serve_forever()
