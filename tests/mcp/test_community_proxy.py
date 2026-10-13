"""공개 커뮤니티 읽기와 계정 outbox 쓰기 경계. 실 DB 격리도 같은 경로로 검증한다."""
from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid

import pytest

from vinc_mcp import edverify, http_server as hs, tools
from vinc_mcp.bridge import Bridge

SEED = bytes(range(32))
A = "community:0191f0aa-1111-7abc-8def-0123456789ab"
B = "community:0191f0aa-2222-7abc-8def-0123456789ab"
SOCIAL = "0191f0aa-3333-7abc-8def-0123456789ab"


class CaptureBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.calls = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        return {"executed": True}


def sign(op="search", role="reader", owner=A, sub="any-authenticated-person", raw=b"", **over):
    now = int(time.time())
    payload = dict(typ="proxy-community", iss="account", aud="mcp", sub=sub,
                   graph_owner=owner, role=role, visibility="public", op=op,
                   iat=now, exp=now+60, jti=uuid.uuid4().hex,
                   bh=base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode())
    payload.update(over)
    return edverify.sign_assertion(SEED, payload)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    return hs.MCPHTTPApp(CaptureBridge(), static_token="t", account_public=edverify.secret_to_public(SEED))


def project(app, op, args, **over):
    raw = json.dumps(args).encode()
    token = sign(op=op, role="projector", sub=hs._COMMUNITY_PROJECTOR_SUB, raw=raw, **over)
    return app.graph_owner_request(token, args, raw_body=raw)


@pytest.mark.parametrize("role", ["reader", "operator"])
@pytest.mark.parametrize("sub", ["not-a-member", "another-member"])
def test_public_read_does_not_need_joining(app, role, sub):
    app.graph_owner_request(sign(role=role, sub=sub), {"owner": B, "theme_id": "other", "debug": True})
    assert app.bridge.calls == [("GET", "/search", {"owner": A}, None)]


@pytest.mark.parametrize("op", sorted(hs._COMMUNITY_READ_OPS))
def test_explicit_read_allowlist(app, op):
    app.graph_owner_request(sign(op=op), {})
    method, path, params, body = app.bridge.calls[0]
    assert (method, path) == hs._PROXY_OPS[op]
    assert method == "GET" and params["owner"] == A and body is None


@pytest.mark.parametrize("role", ["reader", "operator"])
@pytest.mark.parametrize("op", sorted(op for op, (m, _) in hs._PROXY_OPS.items() if m == "POST"))
def test_members_and_operators_cannot_skip_account_revisions(app, role, op):
    with pytest.raises(PermissionError):
        app.graph_owner_request(sign(role=role, op=op), {})
    assert app.bridge.calls == []


@pytest.mark.parametrize("op,args", [
    ("ingest-fragment", {"nodes": [], "edges": []}),
    ("doc-put", {"id": "page.example", "text": "committed revision"}),
    ("doc-put", {"id": "graph.md", "text": "committed Rules seed"}),
    ("conventions-build", {}),
    ("delete", {"node": "episode:talk-" + SOCIAL}),
    ("delete", {"node": "episode:lounge-" + SOCIAL}),
    ("doc-delete", {"id": "talk." + SOCIAL}),
    ("doc-delete", {"id": "lounge." + SOCIAL}),
])
def test_account_service_projects_authorized_committed_values(app, op, args):
    project(app, op, {**args, "owner": B, "theme_id": "other", "_entitlements": {"forged": True}})
    method, path, params, body = app.bridge.calls[0]
    assert (method, path) == hs._PROXY_OPS[op]
    assert body == {**args, "owner": A} and params is None


@pytest.mark.parametrize("op,args", [
    ("delete", {"node": "concept:page"}),
    ("delete", {"node": "episode:talk-../../other"}),
    ("delete", {"node": "episode:unrelated-" + SOCIAL}),
    ("doc-delete", {"id": "page.example"}),
    ("doc-delete", {"id": "graph.md"}),
    ("doc-delete", {"id": "graph.md~1234567890abcdef"}),
    ("doc-delete", {"id": "talk." + SOCIAL + "/other"}),
    ("doc-delete", {"doc_id": "talk." + SOCIAL, "id": "graph.md"}),
])
def test_projector_cannot_delete_wiki_or_rules(app, op, args):
    with pytest.raises(PermissionError):
        project(app, op, args)
    assert app.bridge.calls == []


@pytest.mark.parametrize("op", ["job", "delete-edge", "merge-nodes", "domain-rename", "ingest-pack", "pin"])
def test_projection_is_narrow_not_admin(app, op):
    with pytest.raises(PermissionError):
        project(app, op, {})
    assert app.bridge.calls == []


@pytest.mark.parametrize("claims", [
    {"role": "editor"}, {"role": "admin"}, {"role": None},
    {"visibility": "private"}, {"visibility": None},
    {"role": "projector", "sub": "ordinary-member"},
    {"iss": "member"}, {"aud": "account"}, {"typ": "proxy-community-v2"},
    {"bh": ""}, {"jti": ""}, {"iat": 1, "exp": 2},
])
def test_bad_or_incomplete_capabilities_fail_closed(app, claims):
    with pytest.raises((PermissionError, ValueError)):
        app.graph_owner_request(sign(**claims), {})
    assert app.bridge.calls == []


@pytest.mark.parametrize("owner", [None, "", "community:../../etc", "community:not-a-uuid", "user:u", "team:"+A.split(":")[1], A+"\n"])
def test_malformed_owner_rejected(app, owner):
    with pytest.raises(ValueError):
        app.graph_owner_request(sign(owner=owner), {})
    assert app.bridge.calls == []


def test_unsigned_forged_and_mutated_body_rejected(app):
    with pytest.raises(PermissionError):
        app.graph_owner_request("", {})
    token = edverify.sign_assertion(bytes(32), dict(typ="proxy-community", iss="account", aud="mcp"))
    with pytest.raises(PermissionError):
        app.graph_owner_request(token, {})
    with pytest.raises(PermissionError):
        app.graph_owner_request(sign(), {"query": "changed"}, raw_body=b"changed")
    assert app.bridge.calls == []


def test_nonce_is_single_use(app):
    token = sign()
    app.graph_owner_request(token, {})
    with pytest.raises(PermissionError):
        app.graph_owner_request(token, {})
    assert len(app.bridge.calls) == 1


def test_unnamespaced_store_cannot_accept_community_scope(app, monkeypatch):
    monkeypatch.delenv("VINC_PK_NAMESPACED", raising=False)
    with pytest.raises(PermissionError, match="namespaced"):
        app.graph_owner_request(sign(), {})
    assert app.bridge.calls == []


@pytest.mark.parametrize("name", [t["name"] for t in tools.TOOLS if tools.tool_tier(t["name"]) != "viewer"])
def test_generic_mcp_write_cannot_bypass_revisions(app, name):
    with pytest.raises(PermissionError, match="account revisions"):
        tools.call(name, {}, app.bridge, forced_owner=A, allow_target=False)
    assert app.bridge.calls == []


def test_two_community_owners_keep_identical_ids_and_private_owner_separate(tmp_path, monkeypatch):
    from api import docstore, fragment, query, server
    from store.backend import open_store
    from store.config import StoreConfig
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    store = open_store(StoreConfig(data_dir=str(tmp_path / "db")))
    assert store is not None, "real embedded driver required"
    store.provision(dim=8)

    class StoreBridge(CaptureBridge):
        def request(self, method, path, params=None, body=None, timeout=60):
            if path == "/ingest/fragment":
                return fragment.merge_fragment(body, store=store)
            if path == "/doc/put":
                return docstore.put_doc(store, owner=body["owner"], doc_id=body["id"], text=body["text"], title="Same")
            if path == "/graph":
                return query.graph(owner=params["owner"], seed="concept:same", store=store)
            if path == "/doc":
                return docstore.get_doc(store, params["id"], owner=params["owner"])
            raise AssertionError(path)

    app = hs.MCPHTTPApp(StoreBridge(), static_token="t", account_public=edverify.secret_to_public(SEED))
    try:
        fragment.merge_fragment({"owner": "user:private", "nodes": [
            {"id": "concept:same", "type": "concept", "title": "private same slug"}], "edges": []}, store=store)
        for owner in (A, B):
            out = project(app, "ingest-fragment", {"nodes": [{"id": "concept:same", "type": "concept", "title": owner}], "edges": [], "owner": "user:private"}, owner=owner)
            assert out["merged"]["nodes"]["created"] == 1, out
            # 문서 메타 키는 전역이다. 반영기는 커뮤니티 UUID를 문서 id에 넣는다.
            doc_id = "page." + owner.split(":")[1] + ".same"
            written = project(app, "doc-put", {"id": doc_id, "text": owner}, owner=owner)
            assert written["executed"], written
        for owner in (A, B):
            out = app.graph_owner_request(sign(op="graph", owner=owner), {"owner": "user:private"})
            assert [n["label"] for n in out["nodes"]] == [owner], out
            doc_id = "page." + owner.split(":")[1] + ".same"
            doc = app.graph_owner_request(sign(op="doc", owner=owner), {"id": doc_id})
            assert doc["verified"] and doc["text"] == owner
            other = B if owner == A else A
            denied = app.graph_owner_request(sign(op="doc", owner=other), {"id": doc_id})
            assert not denied.get("text"), denied
            assert server._owner_scope(owner) == "community"
        private = query.graph(owner="user:private", seed="concept:same", store=store)
        assert [n["label"] for n in private["nodes"]] == ["private same slug"]
        assert hs._COMMUNITY_READ_OPS <= set(hs._PROXY_OPS)
        assert hs._COMMUNITY_PROJECT_OPS <= set(hs._PROXY_OPS)
    finally:
        store.close()
