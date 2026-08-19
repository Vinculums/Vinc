# -*- coding: utf-8 -*-
"""B-B3 — 회원 로그인 승인 흐름(CRA 발행 → AA 검증 → 코드 발급).

보안 주장: owner 는 **계정이 서명한 AA** 에서만 온다. 위조·만료·타 요청 AA 로는
코드가 나오지 않고, 승인 1건은 단 한 번만 소비된다(재생 불가).
"""
from __future__ import annotations

import sys
import time
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp import edverify as ev              # noqa: E402
from vinc_mcp.bridge import Bridge               # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp      # noqa: E402
from vinc_mcp.oauth import OAuthGate, _s256      # noqa: E402
from vinc_mcp.store import Store                 # noqa: E402

MCP_SEED = bytes((i * 5 + 2) & 0xFF for i in range(32))
ACCT_SEED = bytes((i * 9 + 4) & 0xFF for i in range(32))


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")

    def request(self, method, path, params=None, body=None, timeout=60):
        return {"executed": False}


@pytest.fixture
def app():
    gate = OAuthGate("https://mcp.test", admin_key=None, store=Store(":memory:"))
    return MCPHTTPApp(_StubBridge(), oauth=gate, mcp_seed=MCP_SEED,
                      account_public=ev.secret_to_public(ACCT_SEED),
                      connect_url="https://vincs.io/connect")


def _register_and_begin(app):
    reg = app.oauth.register({"redirect_uris": ["https://claude.ai/cb"],
                              "client_name": "Claude"})
    loc = app.begin_member_connect({
        "client_id": reg["client_id"], "redirect_uri": "https://claude.ai/cb",
        "response_type": "code", "code_challenge": _s256("v" * 48),
        "code_challenge_method": "S256", "state": "st1"})
    frag = urllib.parse.parse_qs(loc.split("#", 1)[1])
    return reg, frag["req"][0], frag["cra"][0]


def _aa(req, sub="u-1", plan="pro", seed=ACCT_SEED, **over):
    now = int(time.time())
    body = {"typ": "aa", "iss": "account", "aud": "mcp", "req": req,
            "sub": sub, "plan": plan, "iat": now, "exp": now + 120}
    body.update(over)
    return ev.sign_assertion(seed, body)


def test_member_connect_enabled_and_cra_is_verifiable(app):
    assert app.member_connect_enabled()
    _, req, cra = _register_and_begin(app)
    # 계정측이 MCP 공개키로 CRA 를 검증할 수 있어야 한다(인터롭 계약)
    payload = ev.verify_assertion(ev.secret_to_public(MCP_SEED), cra, max_age=600)
    assert payload["typ"] == "cra" and payload["iss"] == "mcp"
    assert payload["req"] == req and payload["client_name"] == "Claude"
    assert payload["redirect_uri"] == "https://claude.ai/cb"


def test_begin_uses_fragment_not_query(app):
    _, req, _ = _register_and_begin(app)
    loc = app.begin_member_connect({
        "client_id": app.oauth.register({"redirect_uris": ["https://claude.ai/cb"]})["client_id"],
        "redirect_uri": "https://claude.ai/cb", "response_type": "code",
        "code_challenge": _s256("v" * 48), "code_challenge_method": "S256"})
    # CRA 는 fragment 로만 — 쿼리스트링에 실리면 로그·Referer 유출
    assert "#req=" in loc and "?" not in loc.split("#")[0].split("//")[1]


def test_complete_issues_code_and_stamps_owner(app):
    reg, req, _ = _register_and_begin(app)
    loc = app.complete_member_connect({"req": req, "aa": _aa(req, sub="u-1", plan="pro")})
    code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
    assert urllib.parse.urlparse(loc).netloc == "claude.ai"
    # 코드에 owner/plan 이 스탬핑되고 토큰까지 전파돼야 한다
    tok = app.oauth.token({"grant_type": "authorization_code", "code": code,
                           "client_id": reg["client_id"],
                           "redirect_uri": "https://claude.ai/cb",
                           "code_verifier": "v" * 48})
    rec = app.oauth.resolve(tok["access_token"])
    assert rec["owner"] == "user:u-1" and rec["plan"] == "pro"
    # 그 토큰으로 들어오면 resolve_auth 가 owner 를 강제한다
    got = app.resolve_auth("Bearer " + tok["access_token"])
    assert got["owner"] == "user:u-1" and got["source"] == "oauth"


def test_state_is_preserved_through_approval(app):
    _, req, _ = _register_and_begin(app)          # state=st1
    loc = app.complete_member_connect({"req": req, "aa": _aa(req)})
    assert urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["state"] == ["st1"]


def test_forged_or_wrong_aa_rejected(app):
    _, req, _ = _register_and_begin(app)
    # 1) 공격자 키로 서명한 AA
    with pytest.raises(PermissionError):
        app.complete_member_connect({"req": req, "aa": _aa(req, seed=bytes(32))})
    # 2) 만료 AA
    with pytest.raises(PermissionError):
        app.complete_member_connect({"req": req, "aa": _aa(req, iat=1, exp=2)})
    # 3) 다른 요청에 발급된 AA(요청 바인딩 위반)
    with pytest.raises(PermissionError):
        app.complete_member_connect({"req": req, "aa": _aa("other-req")})
    # 4) typ 위조(CRA 를 AA 자리에 재사용)
    with pytest.raises(PermissionError):
        app.complete_member_connect({"req": req, "aa": _aa(req, typ="cra")})
    # 4b) aud 위조(L8 — 계정은 aa 를 aud="mcp" 로 서명한다)
    with pytest.raises(PermissionError):
        app.complete_member_connect({"req": req, "aa": _aa(req, aud="account")})
    # 5) sub 없음
    with pytest.raises(ValueError):
        app.complete_member_connect({"req": req, "aa": _aa(req, sub="")})


def test_approval_is_single_use(app):
    _, req, _ = _register_and_begin(app)
    app.complete_member_connect({"req": req, "aa": _aa(req)})
    with pytest.raises(ValueError):          # 재생 → connect_request 이미 소비
        app.complete_member_connect({"req": req, "aa": _aa(req)})


def test_unknown_plan_falls_back_to_free(app):
    _, req, _ = _register_and_begin(app)
    loc = app.complete_member_connect({"req": req, "aa": _aa(req, plan="enterprise")})
    code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
    assert app.oauth.store.pop_code(code)["plan"] == "free"


def test_disabled_when_keys_missing():
    gate = OAuthGate("https://mcp.test", admin_key=None, store=Store(":memory:"))
    app = MCPHTTPApp(_StubBridge(), oauth=gate)      # 키·URL 없음
    assert not app.member_connect_enabled()
    with pytest.raises(PermissionError):
        app.begin_member_connect({})
    with pytest.raises(PermissionError):
        app.complete_member_connect({"req": "x", "aa": "y"})
