# -*- coding: utf-8 -*-
"""VIN-796 — 플러그인 호스트(ChatGPT 플러그인·dots, Claude Tag MCP 커넥터, Grok Bot 커스텀
커넥터)가 OAuth 로 붙어서 액세스 토큰 수명을 넘겨 붙어 있는 데 필요한 것.

- 인가 응답의 `iss`(RFC 9207)와 그 광고
- CIMD 문서의 `client_id` 대조와 주소 모양(MCP 2026-07-28)
- 갱신 토큰 회전: 검증은 소비 전에, 회전은 한 트랜잭션, 동시 갱신에는 같은 쌍

라이브 호스트 확인은 여기서 못 한다(사람 몫, VIN-796 이슈에 목록이 있다).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import edverify as ev                    # noqa: E402
from vinc_mcp.bridge import Bridge                     # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402
from vinc_mcp.oauth import OAuthGate, _s256            # noqa: E402
from vinc_mcp.hub_state import REFRESH_REUSE_GRACE_S, Store  # noqa: E402

ISSUER = "https://mcp.example"
CIMD_URL = "https://chatgpt.com/oauth/client.json"
CALLBACK = "https://chatgpt.com/connector/oauth/callback"
VERIFIER = "v" * 48


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")

    def request(self, method, path, params=None, body=None, timeout=60):
        return {"executed": False}


def _cimd_doc(url=CIMD_URL, **over):
    doc = {"client_id": url, "client_name": "ChatGPT", "redirect_uris": [CALLBACK]}
    doc.update(over)
    return doc


class _Fetcher:
    """CIMD 가져오기 대역. 몇 번 불렸는지 센다."""

    def __init__(self, doc=None):
        self.doc = doc if doc is not None else _cimd_doc()
        self.calls: list[str] = []

    def __call__(self, url):
        self.calls.append(url)
        return dict(self.doc)


def _gate(store=None, fetch=None):
    return OAuthGate(ISSUER, "adminkey", store=store or Store(":memory:"),
                     cimd_fetch=fetch or _Fetcher())


def _authorize(gate, **over):
    params = {"client_id": CIMD_URL, "redirect_uri": CALLBACK, "response_type": "code",
              "code_challenge": _s256(VERIFIER), "code_challenge_method": "S256",
              "state": "st", "key": "adminkey", "scope": "offline_access"}
    params.update(over)
    return gate.authorize(params)


def _connected(gate, resource=""):
    """CIMD 클라이언트로 코드 교환까지 끝낸 토큰 응답."""
    extra = {"resource": resource} if resource else {}
    loc = _authorize(gate, **extra)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
    return gate.token({"grant_type": "authorization_code", "code": code,
                       "client_id": CIMD_URL, "redirect_uri": CALLBACK,
                       "code_verifier": VERIFIER, **extra})


def _refresh(gate, tok, **over):
    return gate.token({"grant_type": "refresh_token", "refresh_token": tok,
                       "client_id": CIMD_URL, **over})


# ── iss(RFC 9207) ───────────────────────────────────────────────────────────

def test_metadata_says_iss_is_sent_and_the_admin_key_redirect_carries_it():
    gate = _gate()
    meta = gate.as_metadata()
    assert meta["authorization_response_iss_parameter_supported"] is True
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(_authorize(gate)).query)
    # 글자 그대로 같아야 한다 — 클라이언트는 정규화 없이 비교한다.
    assert qs["iss"] == [meta["issuer"]]
    assert qs["state"] == ["st"] and qs["code"]


def test_the_member_approval_redirect_carries_iss_too():
    mcp_seed = bytes((i * 5 + 2) & 0xFF for i in range(32))
    acct_seed = bytes((i * 9 + 4) & 0xFF for i in range(32))
    gate = OAuthGate(ISSUER, admin_key=None, store=Store(":memory:"), cimd_fetch=_Fetcher())
    app = MCPHTTPApp(_StubBridge(), oauth=gate, mcp_seed=mcp_seed,
                     account_public=ev.secret_to_public(acct_seed),
                     connect_url="https://vincs.io/connect")
    loc = app.begin_member_connect({
        "client_id": CIMD_URL, "redirect_uri": CALLBACK, "response_type": "code",
        "code_challenge": _s256(VERIFIER), "code_challenge_method": "S256", "state": "s9"})
    req = urllib.parse.parse_qs(loc.split("#", 1)[1])["req"][0]
    now = int(time.time())
    aa = ev.sign_assertion(acct_seed, {"typ": "aa", "iss": "account", "aud": "mcp",
                                       "req": req, "sub": "u-1", "plan": "free",
                                       "iat": now, "exp": now + 120})
    back = app.complete_member_connect({"req": req, "aa": aa})
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(back).query)
    assert qs["iss"] == [ISSUER] and qs["state"] == ["s9"] and qs["code"]


def test_iss_is_appended_after_a_query_the_redirect_already_has():
    fetch = _Fetcher(_cimd_doc(redirect_uris=[CALLBACK + "?tenant=a"]))
    gate = _gate(fetch=fetch)
    loc = _authorize(gate, redirect_uri=CALLBACK + "?tenant=a")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
    assert qs["tenant"] == ["a"] and qs["iss"] == [ISSUER]


# ── CIMD ────────────────────────────────────────────────────────────────────

def test_a_cimd_document_whose_client_id_is_not_its_url_is_refused():
    gate = _gate(fetch=_Fetcher(_cimd_doc(client_id="https://other.example/client.json")))
    with pytest.raises(ValueError, match="client_id does not match"):
        _authorize(gate)


def test_a_cimd_document_without_client_id_is_refused():
    doc = _cimd_doc()
    del doc["client_id"]
    with pytest.raises(ValueError, match="client_id does not match"):
        _authorize(_gate(fetch=_Fetcher(doc)))


@pytest.mark.parametrize("url", ["https://chatgpt.com", "https://chatgpt.com/",
                                 "https://chatgpt.com/client.json#x",
                                 "https://user@chatgpt.com/client.json"])
def test_a_cimd_url_of_the_wrong_shape_is_refused_before_any_fetch(url):
    fetch = _Fetcher(_cimd_doc(url=url))
    with pytest.raises(ValueError, match="https URL"):
        _authorize(_gate(fetch=fetch), client_id=url)
    assert fetch.calls == []


def test_member_approval_fetches_the_cimd_document_once():
    """회원 경로가 이름을 얻으려고 문서를 두 번 가져오던 것을 한 번으로 묶는다."""
    fetch = _Fetcher()
    gate = OAuthGate(ISSUER, admin_key=None, store=Store(":memory:"), cimd_fetch=fetch)
    _, meta = gate.begin_connect({
        "client_id": CIMD_URL, "redirect_uri": CALLBACK, "response_type": "code",
        "code_challenge": _s256(VERIFIER), "code_challenge_method": "S256"})
    assert meta["client_name"] == "ChatGPT"
    assert fetch.calls == [CIMD_URL]


def test_dcr_still_registers_and_refreshes():
    """DCR 은 MCP 2026-07-28 에서 호환용으로 남는다. 등록 → 코드 → 갱신이 그대로다."""
    gate = _gate()
    reg = gate.register({"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                         "client_name": "Claude"})
    loc = gate.authorize({"client_id": reg["client_id"],
                          "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                          "response_type": "code", "code_challenge": _s256(VERIFIER),
                          "code_challenge_method": "S256", "key": "adminkey"})
    code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
    tok = gate.token({"grant_type": "authorization_code", "code": code,
                      "client_id": reg["client_id"],
                      "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                      "code_verifier": VERIFIER})
    again = gate.token({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    assert gate.verify(again["access_token"])


# ── 갱신 토큰 ────────────────────────────────────────────────────────────────

def test_a_connection_outlives_its_access_token():
    """액세스 토큰이 만료돼도 갱신 토큰 사슬로 계속 붙어 있는다(Done when 의 기계 몫)."""
    gate = _gate()
    tok = _connected(gate)
    for _ in range(3):
        gate.store._db.execute("UPDATE tokens SET exp=0 WHERE token=?", (tok["access_token"],))
        gate.store._db.commit()
        assert gate.verify(tok["access_token"]) is False
        nxt = _refresh(gate, tok["refresh_token"])
        assert gate.verify(nxt["access_token"])
        assert nxt["refresh_token"] != tok["refresh_token"]
        assert nxt["expires_in"] == gate.token_ttl
        tok = nxt


def test_a_second_refresh_with_the_same_token_gets_the_same_pair():
    gate = _gate()
    tok = _connected(gate)
    a = _refresh(gate, tok["refresh_token"])
    b = _refresh(gate, tok["refresh_token"])
    assert (b["access_token"], b["refresh_token"]) == (a["access_token"], a["refresh_token"])
    assert 0 < b["expires_in"] <= gate.token_ttl
    # 그 쌍은 정상적으로 다음 회전을 한다.
    c = _refresh(gate, a["refresh_token"])
    assert gate.verify(c["access_token"]) and c["refresh_token"] != a["refresh_token"]


def test_parallel_refreshes_after_expiry_all_land_on_one_pair():
    """여러 작업자가 만료 뒤 같은 토큰으로 동시에 갱신한다(OpenClaw 하위 에이전트 폭주)."""
    gate = _gate()
    tok = _connected(gate)
    got, errors = [], []
    start = threading.Barrier(8)

    def worker():
        start.wait()
        try:
            got.append(_refresh(gate, tok["refresh_token"]))
        except ValueError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert errors == []
    assert len({(g["access_token"], g["refresh_token"]) for g in got}) == 1
    assert gate.verify(got[0]["access_token"])


def test_the_old_token_stops_once_its_successor_has_been_used():
    gate = _gate()
    tok = _connected(gate)
    a = _refresh(gate, tok["refresh_token"])
    _refresh(gate, a["refresh_token"])                  # 클라이언트는 이미 다음으로 갔다
    with pytest.raises(ValueError, match="invalid refresh_token"):
        _refresh(gate, tok["refresh_token"])


def test_the_old_token_stops_after_the_window():
    gate = _gate()
    tok = _connected(gate)
    _refresh(gate, tok["refresh_token"])
    gate.store._db.execute("UPDATE refresh SET rotated_at=rotated_at-? WHERE token=?",
                           (REFRESH_REUSE_GRACE_S + 1, tok["refresh_token"]))
    gate.store._db.commit()
    with pytest.raises(ValueError, match="invalid refresh_token"):
        _refresh(gate, tok["refresh_token"])


def test_rotated_rows_are_swept_after_the_window():
    store = Store(":memory:")
    store.add_refresh("r1", "c1", "user:u1", "free")
    store.rotate_refresh("r1", access="a2", refresh="r2", exp=9e9, now=1000)
    assert store.get_refresh("r1") is not None
    store.purge_expired(now=1000 + REFRESH_REUSE_GRACE_S + 1)
    assert store.get_refresh("r1") is None
    assert store.get_refresh("r2") is not None          # 안 돌린 토큰은 만료가 없다


def test_a_resource_mismatch_does_not_burn_the_refresh_token():
    gate = _gate()
    tok = _connected(gate, resource=ISSUER + "/mcp")
    with pytest.raises(ValueError, match="resource mismatch"):
        _refresh(gate, tok["refresh_token"], resource="https://other.example/mcp")
    ok = _refresh(gate, tok["refresh_token"], resource=ISSUER + "/mcp")
    assert ok["resource"] == ISSUER + "/mcp"
    assert gate.resolve(ok["access_token"])["resource"] == ISSUER + "/mcp"


def test_a_failed_write_during_rotation_leaves_the_old_token_usable():
    """둘째 쓰기(새 갱신 토큰)가 실패하면 첫 쓰기(새 액세스 토큰)까지 되돌아간다."""
    store = Store(":memory:")
    store.add_refresh("r1", "c1", "user:u1", "free")
    with pytest.raises(sqlite3.IntegrityError):
        # 새 갱신 토큰 값이 이미 있는 행과 겹치게 해서 INSERT 를 실패시킨다.
        store.rotate_refresh("r1", access="a2", refresh="r1", exp=9e9, now=1000)
    assert store.get_token("a2", now=1000) is None
    out = store.rotate_refresh("r1", access="a2", refresh="r2", exp=9e9, now=1001)
    assert out and out["replayed"] is False


def test_a_snapshot_from_before_the_rotation_columns_still_refreshes(tmp_path):
    """배포 전 스냅샷의 refresh 표에는 회전 열이 없다. 열고 나면 그 토큰이 그대로 돈다."""
    path = tmp_path / "oauth.db"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE refresh (token TEXT PRIMARY KEY, client_id TEXT NOT NULL,"
        " owner TEXT, plan TEXT, resource TEXT NOT NULL DEFAULT '');"
        "INSERT INTO refresh VALUES ('old', 'c1', 'user:u1', 'free', '');")
    db.commit()
    db.close()
    store = Store(str(path))
    try:
        out = store.rotate_refresh("old", access="a2", refresh="r2", exp=9e9, now=1000)
        assert out["owner"] == "user:u1" and out["replayed"] is False
    finally:
        store.close()


def test_the_window_survives_a_restart(tmp_path):
    """회전 기록은 SQLite 에 있다. 재기동 직후 같은 토큰이 다시 와도 같은 쌍이다."""
    path = str(tmp_path / "oauth.db")
    store = Store(path)
    gate = _gate(store=store)
    tok = _connected(gate)
    a = _refresh(gate, tok["refresh_token"])
    store.close()
    again = _gate(store=Store(path))
    b = _refresh(again, tok["refresh_token"])
    assert (b["access_token"], b["refresh_token"]) == (a["access_token"], a["refresh_token"])
    again.store.close()


# ── 실제 HTTP 경로 ───────────────────────────────────────────────────────────

class _Server:
    def __init__(self, gate):
        self.httpd = make_server(MCPHTTPApp(_StubBridge(), oauth=gate), "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return json.loads(r.read())

    def form(self, path, body):
        req = urllib.request.Request(self.base + path,
                                     data=urllib.parse.urlencode(body).encode(), method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def authorize_location(self, params):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(self.base + "/authorize?" + urllib.parse.urlencode(params),
                               timeout=5)
            return resp.headers.get("Location")
        except urllib.error.HTTPError as e:
            return e.headers.get("Location")

    def stop(self):
        self.httpd.shutdown()


def test_the_http_path_sends_iss_and_answers_a_repeated_refresh_with_the_same_pair():
    gate = _gate()
    srv = _Server(gate)
    try:
        meta = srv.get("/.well-known/oauth-authorization-server")
        assert meta["authorization_response_iss_parameter_supported"] is True
        assert "offline_access" in meta["scopes_supported"]
        assert meta["client_id_metadata_document_supported"] is True
        assert meta["registration_endpoint"].endswith("/register")
        loc = srv.authorize_location({
            "client_id": CIMD_URL, "redirect_uri": CALLBACK, "response_type": "code",
            "code_challenge": _s256(VERIFIER), "code_challenge_method": "S256",
            "state": "st", "key": "adminkey"})
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
        assert qs["iss"] == [meta["issuer"]]
        status, tok = srv.form("/token", {"grant_type": "authorization_code",
                                          "code": qs["code"][0], "client_id": CIMD_URL,
                                          "redirect_uri": CALLBACK, "code_verifier": VERIFIER})
        assert status == 200 and tok["refresh_token"]
        body = {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                "client_id": CIMD_URL}
        s1, a = srv.form("/token", body)
        s2, b = srv.form("/token", body)
        assert (s1, s2) == (200, 200)
        assert (a["access_token"], a["refresh_token"]) == (b["access_token"], b["refresh_token"])
        s3, err = srv.form("/token", {**body, "refresh_token": "never-issued"})
        assert s3 == 400 and err["error"] == "invalid_grant"
    finally:
        srv.stop()
