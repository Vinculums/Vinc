# -*- coding: utf-8 -*-
"""최신 계정 정책: TEAM 공유 예산과 개인 예산의 HTTP 경계 검증."""
import json
import threading
import urllib.request
import urllib.error

from vinc_mcp.bridge import Bridge
from vinc_mcp.http_server import MCPHTTPApp, make_server
from vinc_mcp.store import Store


TEAM = "12345678-1234-1234-1234-123456789abc"
CFG = {"free_limit": 2, "team_limit": 3, "window_s": 86400, "lock_s": 43200}


class StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="local", owner="user:local")

    def request(self, *args, **kwargs):
        return {"executed": True}


def make_app(cfg=None):
    def verify(raw, space=None):
        member = "user:a" if raw == "vinc_sk_a" else "user:b"
        if space:
            return {"owner": f"team:{space}", "member": member,
                    "role": "admin", "scope": "full", "plan": "team"}
        return {"owner": member, "scope": "full", "plan": "free"}
    return MCPHTTPApp(StubBridge(), verify_key=verify, store=Store(":memory:"),
                      quota_config=cfg or CFG)


def test_two_members_share_team_budget_but_personal_is_separate():
    app = make_app()
    server = make_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def call(token, team=True):
        path = f"/t/{TEAM}/mcp" if team else "/mcp"
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}{path}",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "vinc_domains", "arguments": {}}}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())["result"]
    try:
        assert not call("vinc_sk_a").get("isError")
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/v1/domains?space={TEAM}",
            headers={"Authorization": "Bearer vinc_sk_b"})
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
        assert not call("vinc_sk_a").get("isError")
        assert call("vinc_sk_b")["isError"]
        try:
            urllib.request.urlopen(request, timeout=5)
            raise AssertionError("REST bypassed exhausted shared budget")
        except urllib.error.HTTPError as response:
            assert response.code == 429
        assert not call("vinc_sk_a", team=False).get("isError")
        assert not call("vinc_sk_b", team=False).get("isError")
        counts = {row["owner"]: row["count"] for row in app.store.usage_rows()}
        assert counts == {f"team:{TEAM}": 3, "user:a": 1, "user:b": 1}
        before = app.store.usage_rows()
        for _ in range(3):
            result = app.quota_snapshot(f"team:{TEAM}", "team")
            assert result["count"] == 3 and result["remaining"] == 0
            assert result["locked_until"] is not None
        assert app.store.usage_rows() == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_internal_reader_handles_missing_disabled_and_exempt_without_writes():
    app = make_app({**CFG, "exempt_owners": {"team:exempt"}})
    assert app.quota_snapshot("user:new", "free")["remaining"] == 2
    assert app.quota_snapshot("team:new", "team")["remaining"] == 3
    assert app.quota_snapshot("team:exempt", "team") == {
        "count": 0, "remaining": None, "exempt": True}
    assert app.store.usage_rows() == []
    app.quota_config = None
    assert app.quota_snapshot("user:new", "free") is None
