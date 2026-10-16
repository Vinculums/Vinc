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
@pytest.mark.parametrize("op", sorted(hs._TEAM_REFUSED_OPS))
def test_team_graph_refuses_every_community_agent_op(native, role, op):
    """팀 그래프에는 공유 Agent 를 가져올 수 없다. 역할이 무엇이든 허브가 막는다."""
    from api import query
    store, _, app = native
    args = {**snapshot(), "owner": OWNER, "mutation_id": str(uuid4())}
    token, raw = signed(args, op, typ="proxy-team", graph_owner=TEAM, role=role)
    with pytest.raises(PermissionError, match="not available in a team graph"):
        app.graph_owner_request(token, args, raw_body=raw)
    assert not query.node("concept:agent-safe", owner=TEAM, store=store)["found"]


def test_team_refused_ops_cover_every_community_agent_route():
    assert hs._TEAM_REFUSED_OPS == {op for op in hs._PROXY_OPS
        if op.startswith("community-agent-") or op == "community-pack-inspect"}
    assert {"community-agent-import", "community-agent-import-preview",
        "community-agent-export", "community-pack-inspect"} <= hs._TEAM_REFUSED_OPS
    assert not hs._TEAM_REFUSED_OPS & (hs._EDITOR_OPS | hs._ADMIN_OPS)
    for role in ("viewer", "editor", "admin"):
        assert not any(hs._role_allows(role, op) for op in hs._TEAM_REFUSED_OPS)


def test_personal_import_still_lands_in_the_signed_user_graph(native):
    from api import query
    store, _, app = native
    args = {**snapshot(), "owner": TEAM, "mutation_id": str(uuid4())}
    token, raw = signed(args, "community-agent-import")
    result = app.graph_owner_request(token, args, raw_body=raw)
    assert result["executed"] and result["status"] == "proposed", result
    assert query.node(result["node_id"], owner=OWNER, store=store)["found"]
    assert not query.node(result["node_id"], owner=TEAM, store=store)["found"]


def test_team_destination_is_a_validation_error_not_a_crash(native):
    from api import community_agents
    store, core, _ = native
    snap = snapshot()
    with pytest.raises(ValueError, match="invalid_agent_destination"):
        community_agents.preview(store, owner=TEAM, snapshot=snap["snapshot"], source=snap["source"])
    # 사이드카 라우트도 500 이 아니라 같은 검증 오류를 400 으로 돌려준다.
    body = {**snap, "owner": TEAM}
    assert core.preview_community_agent(body) == (400, {"executed": False, "error": "invalid_agent_destination"})
    code, res = core.import_community_agent({**body, "mutation_id": str(uuid4())})
    assert (code, res["error"]) == (400, "invalid_agent_destination")
    code, res = core.read_community_agent_result({"owner": TEAM, "mutation_id": str(uuid4())})
    assert (code, res["error"]) == (400, "invalid_agent_destination")
    from api import query
    assert not query.node("concept:agent-safe", owner=TEAM, store=store)["found"]


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
    with pytest.raises(PermissionError, match="team graph"):
        app.graph_owner_request(token, preview_args, raw_body=raw)
    assert not query.node("concept:agent-safe", owner=TEAM, store=store)["found"]
    token, raw = signed(preview_args, "community-agent-import-preview")
    assert app.graph_owner_request(token, preview_args, raw_body=raw)["target"] is None
    assert not query.node("concept:agent-safe", owner=OWNER, store=store)["found"]
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
