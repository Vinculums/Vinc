# -*- coding: utf-8 -*-
"""연결의 도구 프로파일: 호스티드 MCP 주소의 `?profile=read|core|all`.

고정하는 것:
  ① 프로파일이 없거나 `all` 이면 tools/list 가 지금과 바이트까지 같다.
  ② `read` 는 읽기 전용 키가 부를 수 있는 도구(주석표에서 유도), `core` 는 여섯이다.
  ③ 목록 밖 도구의 tools/call 은 실행되지 않고(브리지를 안 부른다) 프로파일 이름을 말하며 거절된다.
  ④ `?profile=` 을 단 주소에서도 OAuth 자원 대조가 깨지지 않는다(RFC 8707·9728)."""
from __future__ import annotations

import inspect
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import http_server as H  # noqa: E402
from vinc_mcp import toolcall, tools  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402
from vinc_mcp.oauth import OAuthGate  # noqa: E402
from vinc_mcp.rpc import Dispatcher  # noqa: E402
from vinc_mcp.store import Store  # noqa: E402

TEAM = "0198c0de-1111-7222-8333-444455556666"
CORE = ["vinc_search", "vinc_brief", "vinc_node", "vinc_neighbors", "vinc_doc_get", "vinc_ingest"]


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        return {"executed": True, "domains": []}


def _list(profile=None, hub=True):
    d = Dispatcher(_StubBridge(), hub=hub)
    return d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, profile=profile)


def _names(reply):
    return [t["name"] for t in reply["result"]["tools"]]


def _call(name, args=None, profile=None):
    b = _StubBridge()
    d = Dispatcher(b, hub=True)
    reply = d.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": name, "arguments": args or {}}}, profile=profile)
    return reply, b


# ── tools/list ───────────────────────────────────────────────────────────────
def test_no_profile_and_all_are_todays_list_byte_for_byte():
    today = json.dumps({"jsonrpc": "2.0", "id": 1,
                        "result": {"tools": tools.specs(hub=True)}}, ensure_ascii=False)
    assert json.dumps(_list(None), ensure_ascii=False) == today
    assert json.dumps(_list("all"), ensure_ascii=False) == today
    # 로컬 stdio(프로파일을 안 싣는다)도 그대로다.
    assert _list(None, hub=False)["result"]["tools"] == tools.specs(hub=False)


def test_read_is_the_read_only_key_set_in_registry_order():
    every = _names(_list(None))
    got = _names(_list("read"))
    assert got == [n for n in every if tools.tool_tier(n) == "viewer"]
    assert len(got) == 29
    for write in ("vinc_ingest", "vinc_doc_put", "vinc_delete", "vinc_run_start",
                  "vinc_community_write", "vinc_merge_nodes"):
        assert write not in got
    for read in ("vinc_search", "vinc_brief", "vinc_doc_get", "search", "fetch"):
        assert read in got


def test_core_is_the_six_in_registry_order():
    every = _names(_list(None))
    got = _names(_list("core"))
    assert sorted(got) == sorted(CORE)
    assert got == [n for n in every if n in set(CORE)]
    assert set(CORE) == set(tools.CORE_TOOLS)


def test_every_listed_tool_is_classified_explicitly():
    """`read` 는 주석의 readOnlyHint 에서 온다. 새 도구가 주석 없이 오면 기본(admin)으로 조용히
    빠지는 대신 여기서 깨진다. core 의 이름도 실재해야 한다."""
    names = _names(_list(None))
    for n in names:
        assert isinstance(tools._ANNOTATIONS.get(n, {}).get("readOnlyHint"), bool), n
    assert set(tools.CORE_TOOLS) <= set(names)


def test_modern_list_keeps_cache_hints_under_a_profile():
    from vinc_mcp import modern
    d = Dispatcher(_StubBridge(), hub=True)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
           "params": {"_meta": {modern.META_VERSION: modern.MODERN_VERSIONS[0],
                                "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "0"},
                                "io.modelcontextprotocol/clientCapabilities": {}}}}
    plain, read = d.handle(dict(msg)), d.handle(dict(msg), profile="read")
    assert "ttlMs" in plain["result"]
    assert read["result"]["ttlMs"] == plain["result"]["ttlMs"]
    assert len(read["result"]["tools"]) == 29


def test_unknown_profile_is_a_protocol_error_not_the_full_list():
    reply = _list("bogus")
    assert reply["error"]["code"] == -32602
    assert "bogus" in reply["error"]["message"]


# ── tools/call ───────────────────────────────────────────────────────────────
def test_call_outside_the_profile_is_refused_and_does_not_run():
    for profile, name in (("read", "vinc_ingest"), ("read", "vinc_delete"),
                          ("core", "vinc_recall"), ("core", "vinc_doc_put")):
        reply, b = _call(name, {"nodes": []}, profile=profile)
        res = reply["result"]
        assert res["isError"] is True, (profile, name)
        text = res["content"][0]["text"]
        assert f"'{profile}'" in text and name in text
        assert "profile=all" in text
        assert b.calls == [], (profile, name)


def test_call_inside_the_profile_runs():
    for profile in ("read", "all", None):
        reply, b = _call("vinc_domains", profile=profile)
        assert reply["result"]["isError"] is False, profile
        assert b.calls, profile
    reply, b = _call("vinc_ingest", {"nodes": [{"id": "concept:x", "type": "concept",
                                                "title": "X"}]}, profile="core")
    assert reply["result"]["isError"] is False
    assert b.calls


def test_rest_and_cli_path_has_no_profile():
    """REST v1·CLI 는 `toolcall.execute` 를 직접 탄다. 프로파일은 MCP 연결의 일이다."""
    assert "profile" not in inspect.signature(toolcall.execute).parameters


# ── 주소의 `?profile=` ───────────────────────────────────────────────────────
def test_profile_query_parsing():
    assert H.profile_query("") == (None, False)
    assert H.profile_query("live=1") == (None, False)
    for p in ("read", "core", "all"):
        assert H.profile_query("profile=" + p) == (p, False)
    for bad in ("profile=", "profile=READ", "profile=bogus", "profile=read&profile=core"):
        assert H.profile_query(bad) == (None, True), bad


class _Srv:
    def __init__(self, app):
        self.httpd = H.make_server(app, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def send(self, path, obj=None, token="S"):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=None if obj is None else json.dumps(obj).encode(),
            headers=headers, method="GET" if obj is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), dict(e.headers)

    def stop(self):
        self.httpd.shutdown()


_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def test_http_profile_lists_and_refuses():
    srv = _Srv(H.MCPHTTPApp(_StubBridge(), static_token="S"))
    try:
        code, plain, _ = srv.send("/mcp", _LIST)
        assert code == 200
        assert json.loads(plain)["result"]["tools"] == tools.specs(hub=True)
        code, body, _ = srv.send("/mcp?profile=all", _LIST)
        assert body == plain
        code, body, _ = srv.send("/mcp?profile=read", _LIST)
        assert len(json.loads(body)["result"]["tools"]) == 29
        code, body, _ = srv.send("/mcp?profile=core", _LIST)
        assert sorted(t["name"] for t in json.loads(body)["result"]["tools"]) == sorted(CORE)
        code, body, _ = srv.send("/mcp?profile=core", {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "vinc_recall", "arguments": {}}})
        res = json.loads(body)["result"]
        assert res["isError"] is True and "'core'" in res["content"][0]["text"]
        code, body, _ = srv.send("/mcp?profile=bogus", _LIST)
        assert code == 400 and "profile=read" in body
    finally:
        srv.stop()


def test_http_401_names_the_profiled_metadata_only_when_asked():
    gate = OAuthGate("https://mcp.example", None, store=Store(":memory:"))
    srv = _Srv(H.MCPHTTPApp(_StubBridge(), static_token="S", oauth=gate))
    try:
        code, _, hdr = srv.send("/mcp", _LIST, token=None)
        assert code == 401
        assert hdr["WWW-Authenticate"] == ('Bearer resource_metadata="https://mcp.example'
                                           '/.well-known/oauth-protected-resource"')
        code, _, hdr = srv.send("/mcp?profile=read", _LIST, token=None)
        assert code == 401
        assert hdr["WWW-Authenticate"] == ('Bearer resource_metadata="https://mcp.example'
                                           '/.well-known/oauth-protected-resource?profile=read"')
    finally:
        srv.stop()


def test_well_known_echoes_a_valid_profile_only():
    gate = OAuthGate("https://mcp.example", None, store=Store(":memory:"))
    srv = _Srv(H.MCPHTTPApp(_StubBridge(), static_token="S", oauth=gate))
    try:
        base = "/.well-known/oauth-protected-resource"
        plain = json.loads(srv.send(base + "/mcp")[1])
        assert plain["resource"] == "https://mcp.example/mcp"
        for path in (base, base + "/mcp"):
            got = json.loads(srv.send(path + "?profile=read")[1])
            assert got == {**plain, "resource": "https://mcp.example/mcp?profile=read"}
            assert json.loads(srv.send(path + "?profile=bogus")[1]) == plain
        got = json.loads(srv.send(f"{base}/t/{TEAM}/mcp?profile=core")[1])
        assert got["resource"] == f"https://mcp.example/t/{TEAM}/mcp?profile=core"
    finally:
        srv.stop()


def test_token_bound_to_a_profiled_address_passes_the_audience_check():
    gate = OAuthGate("https://mcp.example", None, store=Store(":memory:"))
    for tok, resource in (("t-plain", "https://mcp.example/mcp"),
                          ("t-read", "https://mcp.example/mcp?profile=read"),
                          ("t-none", ""),
                          ("t-bogus", "https://mcp.example/mcp?profile=bogus"),
                          ("t-other", "https://mcp.example/other?profile=read"),
                          ("t-team", f"https://mcp.example/t/{TEAM}/mcp?profile=core")):
        gate.store.add_token(tok, "cid", "user:u1", "free", exp=9e9, resource=resource)
    app = H.MCPHTTPApp(_StubBridge(), oauth=gate)
    assert app.resolve_auth("Bearer t-plain")["ok"]
    assert app.resolve_auth("Bearer t-read")["ok"]
    assert app.resolve_auth("Bearer t-none")["ok"]
    assert not app.resolve_auth("Bearer t-bogus")["ok"]
    assert not app.resolve_auth("Bearer t-other")["ok"]
    # 팀 주소로 발급된 토큰은 개인 주소에 못 온다(프로파일이 붙어도 경로 대조는 그대로다).
    assert not app.resolve_auth("Bearer t-team")["ok"]
