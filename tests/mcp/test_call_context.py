"""호출의 값 묶음(`toolcall.CallContext`)이 `/mcp` 와 REST 에서 한 곳(`MCPHTTPApp.call_context`)에서
만들어지고, `Dispatcher.handle` · `toolcall.execute` · `tools.call` 이 옛 키워드도 그대로 받는다."""
from __future__ import annotations

import dataclasses

import pytest

from vinc_mcp import rpc, toolcall, tools
from vinc_mcp.http_server import MCPHTTPApp


class _Bridge:
    owner = "user:local"

    def __init__(self):
        self.calls = []

    def request(self, method, path, params=None, body=None, timeout=60, **_kw):
        self.calls.append((method, path, params, body))
        return {"executed": True, "rows": [], "echo": path}


def _app(**kw):
    return MCPHTTPApp(_Bridge(), static_token="S", **kw)


def _call(name, **args):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}


def test_call_context_is_frozen_and_empty_by_default():
    ctx = toolcall.CallContext()
    assert all(getattr(ctx, f.name) is None for f in dataclasses.fields(ctx))
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.forced_owner = "user:x"


def test_app_builds_one_context_with_callbacks_bound_to_the_auth():
    seen = []
    app = _app(community_write=lambda auth, args: seen.append(("w", auth, args)) or {"w": True},
               community_read=lambda auth, args: seen.append(("r", auth, args)) or {"r": True},
               free_max_domains=5)
    auth = {"owner": "community:x", "plan": "free", "member": "user:m", "scope": "full"}
    spent: dict = {}
    ctx = app.call_context(auth, spent, profile="read")
    assert (ctx.forced_owner, ctx.plan, ctx.profile) == ("community:x", "free", "read")
    assert ctx.entitlements == {"max_domains": 5}
    assert ctx.quota_view == app.quota_view
    assert ctx.events_auth == {"principal": "community:x", "can_write": True}
    assert ctx.community_write({"a": 1}) == {"w": True}
    assert ctx.community_read({"b": 2}) == {"r": True}
    assert seen == [("w", auth, {"a": 1}), ("r", auth, {"b": 2})]
    assert ctx.quota("user:m", "free") is None


def test_app_context_leaves_out_what_the_auth_or_app_does_not_give():
    ctx = _app(free_max_domains=5).call_context({"owner": None, "plan": None, "source": "static"})
    assert ctx.forced_owner is None and ctx.entitlements is None
    assert ctx.community_write is None and ctx.community_read is None and ctx.profile is None


def test_handle_answers_the_same_with_ctx_or_the_separate_keywords():
    spent_a, spent_b = [], []
    kw = {"forced_owner": "user:u1", "plan": "free", "entitlements": {"max_domains": 3},
          "quota_view": lambda o, p: {"limit": 9}}
    a, b = _Bridge(), _Bridge()
    old = rpc.Dispatcher(a, hub=True).handle(
        _call("vinc_ingest", nodes=[{"id": "concept:a", "type": "concept"}]),
        quota=lambda o, p: spent_a.append((o, p)), **kw)
    new = rpc.Dispatcher(b, hub=True).handle(
        _call("vinc_ingest", nodes=[{"id": "concept:a", "type": "concept"}]),
        ctx=toolcall.CallContext(quota=lambda o, p: spent_b.append((o, p)), **kw))
    assert old == new and a.calls == b.calls and spent_a == spent_b == [("user:u1", "free")]
    assert a.calls[0][3]["_entitlements"] == {"max_domains": 3} and a.calls[0][3]["owner"] == "user:u1"


def test_ctx_and_separate_keywords_together_are_refused():
    ctx = toolcall.CallContext(forced_owner="user:u1")
    with pytest.raises(TypeError):
        rpc.Dispatcher(_Bridge(), hub=True).handle(_call("vinc_domains"), forced_owner="user:u2", ctx=ctx)
    with pytest.raises(TypeError):
        toolcall.execute("vinc_domains", {}, _Bridge(), ctx, plan="free")
    with pytest.raises(TypeError):
        tools.call("vinc_domains", {}, _Bridge(), forced_owner="user:u2", ctx=ctx)
    with pytest.raises(TypeError):
        toolcall.context_of(None, owner="user:u1")


def test_execute_and_tools_call_read_the_owner_from_ctx():
    bridge = _Bridge()
    ctx = toolcall.CallContext(forced_owner="user:u1", plan="free")
    got = toolcall.execute("vinc_domains", {"owner": "user:other"}, bridge, ctx, hub=True)
    assert got.kind == "ok" and bridge.calls[-1][2]["owner"] == "user:u1"
    tools.call("vinc_domains", {"owner": "user:other"}, bridge, hub=True, ctx=ctx)
    assert bridge.calls[-1][2]["owner"] == "user:u1"
