"""공유 Agent의 서명·권한과 MCP/REST 읽기 연결은 실제 코어 경로로 검증한다."""
import base64
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from uuid import uuid4

import pytest

from vinc_mcp import community, edverify, http_server as hs, tools
from api.cli_tools import _InProcessBridge
from api.server import VincApp
from tests.core.test_decision_type import _fresh_store, _restore

SEED = bytes(range(32))
ACTOR = "0191f0aa-1111-7abc-8def-0123456789ab"
OWNER = "user:" + ACTOR
TEAM = "team:0191f0aa-2222-7abc-8def-0123456789ab"
SPACE = "community:0191f0aa-3333-7abc-8def-0123456789ab"


def signed(args, op, **claims):
    raw = json.dumps(args).encode()
    now = int(time.time())
    payload = {"typ": "proxy", "iss": "account", "aud": "mcp", "sub": ACTOR,
        "op": op, "scope": "full", "iat": now, "exp": now + 60, "jti": uuid4().hex,
        "bh": base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode(), **claims}
    return edverify.sign_assertion(SEED, payload), raw


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    monkeypatch.setenv("VINC_DURABLE_ACK", "off")
    monkeypatch.setenv("VINC_TOPIC_CONVERSION", "0")
    store, keep = _fresh_store()
    core = VincApp(store=store)
    app = hs.MCPHTTPApp(_InProcessBridge(core), static_token="t", account_public=edverify.secret_to_public(SEED))
    try:
        yield store, core, app
    finally:
        core.jobs.shutdown()
        store.close()
        _restore(keep)


def snapshot():
    from api.community_agents import digest
    snap = {"role": {"id": "concept:agent-safe", "name": "agent-safe", "label": "Safe role", "domain": "agent",
        "tags": ["agent", "execute", "role"], "props": {"mission": "Read the selected version.", "tier": "execute",
        "rung": "fast", "never": [], "person_only": []}}, "prompt": "Pinned", "tools": [], "references": [], "license": "MIT"}
    share = str(uuid4())
    return {"snapshot": snap, "source": {"share_id": share, "version": 1, "hash": digest(snap),
        "url": f"https://vincs.io/community/a/{share}/v/1", "author": {"handle": "safe", "deleted": False}}}


@pytest.mark.parametrize("role", ["viewer", "editor", "admin"])
def test_signed_team_import_editor_only_and_owner_forced(native, role):
    from api import query
    store, _, app = native
    args = {**snapshot(), "owner": OWNER, "mutation_id": str(uuid4())}
    token, raw = signed(args, "community-agent-import", typ="proxy-team", graph_owner=TEAM, role=role)
    if role == "viewer":
        with pytest.raises(PermissionError):
            app.graph_owner_request(token, args, raw_body=raw)
    else:
        result = app.graph_owner_request(token, args, raw_body=raw)
        assert result["executed"] and result["status"] == "proposed", result
        assert query.node(result["node_id"], owner=TEAM, store=store)["found"]
        assert not query.node(result["node_id"], owner=OWNER, store=store)["found"]


@pytest.mark.parametrize("scope", [None, "read", "FULL", True])
def test_account_assertion_requires_full_import_scope(native, scope):
    _, _, app = native
    args = {**snapshot(), "mutation_id": str(uuid4())}
    token, raw = signed(args, "community-agent-import", scope=scope)
    with pytest.raises(PermissionError, match="full-access"):
        app.graph_owner_request(token, args, raw_body=raw)


def test_signed_pure_post_reads_and_body_replay_boundary(native):
    from api import query
    store, _, app = native
    args = {**snapshot(), "format": "vincpack"}
    token, raw = signed(args, "community-agent-export", typ="proxy-community", graph_owner=SPACE,
        role="reader", visibility="public")
    result = app.graph_owner_request(token, args, raw_body=raw)
    assert result["format"] == "vincpack" and result["encoding"] == "base64"
    with pytest.raises(PermissionError, match="already used"):
        app.graph_owner_request(token, args, raw_body=raw)
    preview_args = snapshot()
    token, raw = signed(preview_args, "community-agent-import-preview", typ="proxy-team", graph_owner=TEAM, role="viewer")
    assert app.graph_owner_request(token, preview_args, raw_body=raw)["target"] is None
    assert not query.node("concept:agent-safe", owner=TEAM, store=store)["found"]
    token, raw = signed(preview_args, "community-agent-import-preview")
    with pytest.raises(PermissionError, match="body"):
        app.graph_owner_request(token, preview_args, raw_body=raw + b" ")


def test_metadata_removal_is_projector_only_and_document_owner_bound(native):
    _, _, app = native
    for ident in ["agent." + SPACE[10:] + "." + ACTOR + ".1", "talk." + ACTOR]:
        args = {"id": ident}
        token, raw = signed(args, "doc-delete", typ="proxy-community", graph_owner=SPACE,
            role="reader", visibility="public")
        with pytest.raises(PermissionError):
            app.graph_owner_request(token, args, raw_body=raw)
    args = {"id": "agent." + ACTOR + "." + ACTOR + ".1"}
    token, raw = signed(args, "doc-delete", typ="proxy-community", graph_owner=SPACE,
        role="projector", sub=hs._COMMUNITY_PROJECTOR_SUB, visibility="public")
    with pytest.raises(PermissionError, match="owned Agent"):
        app.graph_owner_request(token, args, raw_body=raw)


def test_actual_mcp_rest_read_only_callback_scope_quota_and_error(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    calls, quota = [], []
    class Bridge:
        owner = OWNER
        def request(self, *args, **kwargs):
            raise AssertionError("Canonical share reads cannot use raw graph projection")
    def verify(key, team=None):
        return {"owner": OWNER, "scope": "read", "plan": "free"} if key == "vinc_ro_fixture" and not team else None
    def read(auth, args):
        calls.append((auth, args))
        return {"executed": False, "status": 403, "error": "share_withdrawn"} if args["payload"].get("withdrawn") else {"executed": True, "version": 1, "data_only": True}
    app = hs.MCPHTTPApp(Bridge(), verify_key=verify, verify_community=lambda sub, space: {
        "owner": space, "member": OWNER, "role": "reader", "visibility": "public"}, community_read=read)
    app.quota_check = lambda owner, plan: quota.append((owner, plan)) or None
    server = hs.make_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(path, value):
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}" + path,
            data=json.dumps(value).encode(), headers={"Authorization": "Bearer vinc_ro_fixture", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
    try:
        args = {"space": SPACE, "action": "agent_detail", "payload": {"shareId": ACTOR, "version": 1}}
        status, body = request("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": community.READ_TOOL, "arguments": args}})
        assert status == 200 and not body["result"].get("isError"), body
        status, body = request("/v1/community/read?space=" + SPACE, {"action": "agent_export", "payload": {"withdrawn": True}})
        assert status == 403 and body["error"]["code"] == "role_forbidden", body
        assert len(calls) == 2 and all(c[0]["member"] == OWNER and c[0]["scope"] == "read" for c in calls)
        assert quota == [(OWNER, "free"), (OWNER, "free")]
        assert tools.tool_tier(community.READ_TOOL) == "viewer"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_large_immutable_export_refused_without_truncation_and_points_to_complete_route():
    from vinc_mcp import rpc
    text = json.dumps({"version": 1, "content": "x" * rpc.MAX_RESULT_CHARS})
    result = rpc._result_text(text, community.READ_TOOL, {})
    assert result["isError"] is True
    assert "/v1/community/read" in result["content"][0]["text"]
    assert "x" * 100 not in result["content"][0]["text"]
