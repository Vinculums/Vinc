from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid

import pytest
from vinc_mcp import edverify
from vinc_mcp.http_server import MCPHTTPApp
from vinc_mcp.bridge import Bridge

SEED = bytes(range(32))
SUB = "00000000-0000-4000-8000-000000000003"
TARGET = "team:00000000-0000-4000-8000-000000000004"
RUN = "00000000-0000-4000-8000-000000000005"


class Capture(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="synthetic", owner="user:local")
        self.calls = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, body))
        return {"status": "graph_committed"}


def test_inventory_status_forces_source_and_does_not_require_a_reservation(app):
    args = {"owner":"user:victim","target":"team:victim","run":"victim","ticket":RUN}
    raw = json.dumps(args).encode()
    app.graph_owner_request(sign(raw,op="topic-conversion:inventory-status",target=None,run=None),args,raw)
    assert app.bridge.calls[0][2] == {"owner":"user:"+SUB,"action":"inventory-status","ticket":RUN}


def sign(raw, **overrides):
    now = int(time.time())
    return edverify.sign_assertion(SEED, {
        "typ": "proxy-conversion", "iss": "account", "aud": "mcp", "sub": SUB,
        "op": "topic-conversion:stage", "target": TARGET, "run": RUN,
        "bh": base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode(),
        "jti": str(uuid.uuid4()), "iat": now, "exp": now+60, **overrides,
    })


@pytest.fixture
def app():
    return MCPHTTPApp(Capture(), static_token="synthetic", account_public=edverify.secret_to_public(SEED))


def test_only_signed_subject_run_and_fresh_target_authorize_the_move(app):
    args = {"owner": "user:victim", "target": "team:victim", "run": "victim", "action": "release",
            "topic": "project", "fingerprint": "a"*64}
    raw = json.dumps(args).encode()
    app.graph_owner_request(sign(raw), args, raw)
    method,path,body = app.bridge.calls[0]
    assert (method,path) == ("POST","/topic-conversion")
    assert body == {"owner":"user:"+SUB,"target":TARGET,"run":RUN,"action":"stage",
                    "topic":"project","fingerprint":"a"*64}


@pytest.mark.parametrize("typ", ["proxy","proxy-team"])
def test_ordinary_graph_assertions_do_not_grant_conversion(app,typ):
    with pytest.raises((ValueError,PermissionError)):
        app.graph_owner_request(sign(b"",typ=typ),{},b"")
    assert app.bridge.calls == []


@pytest.mark.parametrize("over", [{"op":"ingest-pack"},{"target":"user:other"},
    {"target":"team:not-a-uuid"},{"run":"../other"},{"sub":"not-a-uuid"}])
def test_invalid_capabilities_are_refused_before_sidecar(app,over):
    with pytest.raises((ValueError,PermissionError)):
        app.graph_owner_request(sign(b"",**over),{},b"")
    assert app.bridge.calls == []


def test_signed_body_cannot_be_replaced_or_replayed(app):
    raw=b'{"topic":"project"}'; token=sign(raw)
    with pytest.raises(PermissionError,match="digest"):
        app.graph_owner_request(token,{"topic":"other"},b'{"topic":"other"}')
    app.graph_owner_request(token,{"topic":"project"},raw)
    with pytest.raises(PermissionError,match="already used"):
        app.graph_owner_request(token,{"topic":"project"},raw)
    assert len(app.bridge.calls)==1
