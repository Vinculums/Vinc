"""커뮤니티 MCP의 실제 HTTP·서명 경로. 계정 DB 정책 자체는 계정 패키지에서 검증한다."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vinc_mcp import community, edverify, entitlement, http_server as H, tools
from vinc_mcp.bridge import Bridge
from vinc_mcp.oauth import OAuthGate
from vinc_mcp.store import Store

A = "community:0191f0aa-1111-7abc-8def-0123456789ab"
B = "community:0191f0aa-2222-7abc-8def-0123456789ab"
USER = "0191f0aa-3333-7abc-8def-0123456789ab"
OPERATOR = "0191f0aa-4444-7abc-8def-0123456789ab"
SEED = bytes(range(32))
WRITE = community.WRITE_TOOL


class MemoryBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:private")
        self.calls = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        assert method == "GET", "community mutations must never reach raw graph bridge"
        return {"executed": True, "owner": params["owner"], "text": params["owner"], "verified": True}


@pytest.fixture
def service(monkeypatch):
    """테스트 계정 서비스: 서명과 고정된 정책 결과를 검증하고 판 정보를 기록한다."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    monkeypatch.setattr(entitlement, "_seed", lambda: SEED)
    state = {"requests": [], "revisions": [], "nonces": set(), "quota": [], "bridge": MemoryBridge()}
    lock = threading.Lock()

    class AccountHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data):
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            data = json.loads(raw)
            claims = edverify.verify_assertion(edverify.secret_to_public(SEED), self.headers.get("X-Vinc-Assertion"), max_age=120)
            if not claims or claims.get("iss") != "mcp" or claims.get("aud") != "account":
                return self.reply(401, {"error": "invalid_service"})
            with lock:
                state["requests"].append((self.path, data, claims))
            if self.path == "/internal/mcp/spaces":
                return self.reply(200, {"spaces": [], "communities": [{"id": A, "name": "Joined A", "role": "reader"}]})
            digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
            if claims.get("bh") != digest or not claims.get("jti") or claims.get("sub") != data.get("sub"):
                return self.reply(401, {"error": "body_or_subject_mismatch"})
            with lock:
                if claims["jti"] in state["nonces"]:
                    return self.reply(401, {"error": "replay"})
                state["nonces"].add(claims["jti"])
            if self.path == "/internal/mcp/community" and data.get("action") == "community_create":
                if claims.get("typ") != "community-write" or data.get("community") is not None:
                    return self.reply(403, {"error": "wrong_creation_scope"})
                return self.reply(200, {"created": True, "id": B})
            owner = "community:" + str(data.get("community"))
            if owner not in (A, B):
                return self.reply(404, {"error": "community_not_found"})
            if self.path == "/internal/mcp/community-resolve" and claims.get("typ") == "community-read":
                return self.reply(200, {"graph_owner": owner, "visibility": "public",
                                       "role": "operator" if data["sub"] == OPERATOR else "reader"})
            if self.path != "/internal/mcp/community" or claims.get("typ") != "community-write":
                return self.reply(401, {"error": "wrong_purpose"})
            if data.get("channel") != "mcp" or data.get("scope") != "full":
                return self.reply(403, {"error": "forbidden"})
            if data.get("payload", {}).get("slug") == "rules" and data["sub"] != OPERATOR:
                return self.reply(403, {"error": "protected_rules"})
            with lock:
                record = {"community": owner, "editor": data["sub"], "channel": "mcp",
                          "client_name": data["client_name"], "body": data["payload"].get("body"),
                          "revision": len(state["revisions"]) + 1}
                state["revisions"].append(record)
            return self.reply(200, {"saved": True, "revision": record["revision"], "graph": "pending"})

    account = ThreadingHTTPServer(("127.0.0.1", 0), AccountHandler)
    threading.Thread(target=account.serve_forever, daemon=True).start()
    monkeypatch.setenv("VINC_ACCOUNT_INTERNAL_URL", "http://127.0.0.1:" + str(account.server_port))
    def verify_key(token, team=None):
        if team or token not in ("vinc_sk_user", "vinc_ro_user", "vinc_sk_operator"):
            return None
        return {"owner": "user:" + (OPERATOR if token == "vinc_sk_operator" else USER),
                "scope": "read" if token == "vinc_ro_user" else "full", "plan": "free"}

    app = H.MCPHTTPApp(state["bridge"], static_token="S", verify_key=verify_key,
                      verify_community=community.resolve, community_write=community.write)
    def quota(owner, plan):
        state["quota"].append((owner, plan))
        return state.get("quota_denial")
    app.quota_check = quota
    hub = H.make_server(app, "127.0.0.1", 0)
    threading.Thread(target=hub.serve_forever, daemon=True).start()
    state["app"], state["hub"] = app, hub
    yield state
    hub.shutdown(); hub.server_close()
    account.shutdown(); account.server_close()


def rpc(service, name, args=None, *, token="vinc_sk_user", method="tools/call"):
    params = {"name": name, "arguments": args or {}} if method == "tools/call" else args or {}
    req = urllib.request.Request("http://127.0.0.1:" + str(service["hub"].server_port) + "/mcp",
                                 data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        envelope = json.loads(response.read())
    result = envelope.get("result") or {}
    if result.get("content") and not result.get("isError"):
        result = json.loads(result["content"][0]["text"])
    return envelope, result


def write_args(space=A, **over):
    return {"space": space, "action": "page_save", "client_name": "Codex",
            "payload": {"slug": "same", "title": "Same", "body": "Text", "summary": "Edit", "baseRevision": None}, **over}


def test_public_unjoined_reads_are_isolated_and_bill_the_actor(service):
    for owner in (A, B):
        _, result = rpc(service, "vinc_doc_get", {"space": owner, "doc_id": "page.same", "owner": "user:private"}, token="vinc_ro_user")
        assert result["owner"] == owner and result["text"] == owner
    assert service["quota"] == [("user:" + USER, "free"), ("user:" + USER, "free")]
    assert not service["revisions"]


def test_a_community_citation_roundtrips_to_the_same_owner_without_explicit_space(service):
    url = tools._cite_url("node", "concept:energy", B)
    _, result = rpc(service, "vinc_graph", {"url": url}, token="vinc_ro_user")
    assert result["owner"] == B
    assert service["bridge"].calls[-1][2]["owner"] == B
    assert service["quota"] == [("user:" + USER, "free")]


def test_write_reaches_account_revision_channel_not_raw_graph(service):
    _, result = rpc(service, WRITE, write_args(payload={"slug": "same", "body": "exact", "channel": "web", "editor": OPERATOR}))
    assert result == {"saved": True, "revision": 1, "graph": "pending"}
    assert service["revisions"] == [{"community": A, "editor": USER, "channel": "mcp", "client_name": "Codex", "body": "exact", "revision": 1}]
    assert service["bridge"].calls == []


@pytest.mark.parametrize("name", ["vinc_ingest", "vinc_doc_put", "vinc_delete", "vinc_doc_extract"])
def test_generic_write_refused_before_quota_or_bridge(service, name):
    _, result = rpc(service, name, {"space": A})
    assert result["isError"] and "community" in result["content"][0]["text"].lower()
    assert service["quota"] == service["revisions"] == service["bridge"].calls == []


def test_readonly_key_cannot_call_account_mutation(service):
    _, result = rpc(service, WRITE, write_args(), token="vinc_ro_user")
    assert result["isError"] and "read-only" in result["content"][0]["text"]
    assert not service["quota"] and not service["revisions"]


def test_protected_rules_still_use_account_policy(service):
    _, denied = rpc(service, WRITE, write_args(payload={"slug": "rules", "role": "operator"}))
    assert denied["isError"] and not service["revisions"]
    _, allowed = rpc(service, WRITE, write_args(payload={"slug": "rules", "body": "Authorized"}), token="vinc_sk_operator")
    assert allowed["saved"] and service["revisions"][0]["editor"] == OPERATOR


@pytest.mark.parametrize("method", ["prompts/list", "prompts/get"])
def test_community_roles_never_become_prompts(service, method):
    envelope, _ = rpc(service, "", {"space": A, "name": "agent-community"}, method=method)
    assert "error" in envelope and "data" in envelope["error"]["message"]
    assert not service["bridge"].calls


def test_concurrent_requests_do_not_share_authentication_context(service):
    inputs = [(A, "vinc_sk_user", USER), (B, "vinc_sk_operator", OPERATOR)] * 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda item: rpc(service, WRITE, write_args(item[0]), token=item[1])[1], inputs))
    assert all(r["saved"] for r in results)
    assert len(service["revisions"]) == 8
    assert all((r["community"], r["editor"]) in ((A, USER), (B, OPERATOR)) for r in service["revisions"])
    assert len({req[2]["jti"] for req in service["requests"]}) == len(service["requests"])


@pytest.mark.parametrize("over", [{"client_name": ""}, {"client_name": "x\nsecret"}, {"client_name": "x"*121}, {"payload": []}, {"action": "share_agent"}, {"payload": {"number": float("nan")}}])
def test_invalid_tool_arguments_consume_no_quota(service, over):
    _, result = rpc(service, WRITE, write_args(**over))
    assert result["isError"] and not service["quota"] and not service["revisions"]


def test_missing_or_private_space_never_falls_back_to_personal(service):
    for space in ("personal", "community:not-a-uuid", "community:0191f0aa-5555-7abc-8def-0123456789ab"):
        _, result = rpc(service, WRITE, write_args(space))
        assert result["isError"]
    assert not service["bridge"].calls and not service["revisions"]


def test_quota_denial_precedes_account_mutation(service):
    service["quota_denial"] = {"isError": True, "content": [{"type": "text", "text": "quota"}]}
    _, result = rpc(service, WRITE, write_args())
    assert result["isError"] and not service["revisions"]


def test_oauth_uses_public_community_resolution_without_team_membership(service):
    gate = OAuthGate("http://local", admin_key=None, store=Store(":memory:"))
    gate.store.add_token("token", "client", "user:" + USER, "free", exp=9e9)
    service["app"].oauth = gate
    service["app"].verify_member = lambda *args: pytest.fail("joining/membership is not a public read gate")
    service["app"]._fresh_plan = lambda _owner, _plan: "free"
    _, result = rpc(service, "vinc_doc_get", {"space": B, "doc_id": "page.same"}, token="token")
    assert result["owner"] == B
    gate.store.close()


def test_whoami_separates_joined_communities_from_team_spaces(service):
    entitlement._spaces_cache.clear(); entitlement._communities_cache.clear()
    _, result = rpc(service, "vinc_whoami")
    assert result["spaces"] == [{"id": "personal", "name": "Personal", "role": "admin"}]
    assert result["communities"] == [{"id": A, "name": "Joined A", "role": "reader"}]
    assert result["communities_source"] == "account"


def test_account_write_timeout_is_explicitly_unconfirmed(monkeypatch):
    monkeypatch.setattr(entitlement, "_seed", lambda: SEED)
    def timeout(*args, **kwargs):
        raise TimeoutError()
    monkeypatch.setattr(community.hubhttp, "urlopen", timeout)
    with pytest.raises(community.WriteUnconfirmed, match="read history"):
        community.write({"owner": A, "member": "user:"+USER, "scope": "full"}, write_args())


def test_creation_uses_personal_identity_and_never_a_client_owner(service):
    args = write_args("personal", action="community_create", payload={"slug": "new-topic", "owner": "user:other"})
    args.pop("space")
    _, result = rpc(service, WRITE, args)
    assert result["created"]
    path, body, claims = service["requests"][-1]
    assert path == "/internal/mcp/community" and body["sub"] == claims["sub"] == USER
    assert body["community"] is None and not service["bridge"].calls
    assert service["quota"] == [("user:"+USER, "free")]


def test_creation_cannot_be_misdirected_from_a_community_or_static_identity(service):
    for token in ("vinc_sk_user", "S"):
        _, result = rpc(service, WRITE, write_args(A, action="community_create"), token=token)
        assert result["isError"]
    assert not service["revisions"] and not service["bridge"].calls


@pytest.mark.parametrize("action", community.ACTIONS)
def test_complete_phase1_action_enum_is_routed_without_generic_graph_writes(service, action):
    args = write_args(A, action=action)
    if action == "community_create":
        args.pop("space")
    # 합친 공간의 동작은 허브가 payload 형식을 본다. 그 동작에는 계정이 받는 모양을 준다.
    shaped = {"post_reply": {"targetId": "0191f0aa-5555-7abc-8def-0123456789ab", "body": "Text"},
              "topic_save": {"path": "physics", "nameEn": "Physics"}}
    if action in shaped:
        args["payload"] = shaped[action]
    _, result = rpc(service, WRITE, args)
    assert result.get("saved") or result.get("created")
    assert service["requests"][-1][1]["action"] == action
    assert not service["bridge"].calls


@pytest.mark.parametrize("token,slug,status", [("vinc_sk_user", "same", 200), ("vinc_ro_user", "same", 403),
                                             ("vinc_sk_user", "rules", 403)])
def test_rest_community_route_has_the_same_revision_and_permission_boundary(service, token, slug, status):
    args = write_args(A, payload={"slug": slug, "body": "REST text"})
    args.pop("space")
    req = urllib.request.Request("http://127.0.0.1:"+str(service["hub"].server_port)+"/v1/community?space="+A,
                                 data=json.dumps(args).encode(),
                                 headers={"Authorization": "Bearer "+token, "Content-Type": "application/json"})
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        assert response.status == status
    if status == 200:
        assert service["revisions"][0]["body"] == "REST text"
        assert service["revisions"][0]["channel"] == "mcp"
    else:
        assert not service["revisions"]
    assert not service["bridge"].calls
