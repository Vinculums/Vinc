"""MCP/REST가 공유할 도구 호출 게이트의 순서를 고정한다."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from vinc_mcp import toolcall
from vinc_mcp import tools


class _Bridge:
    owner = "user:local"

    def __init__(self):
        self.calls = []

    def request(self, method, path, params=None, body=None, **_kw):
        self.calls.append((method, path, params, body))
        return {"kind": "search", "executed": True, "rows": []}


def _auth(owner="user:u1", role="admin", scope="full"):
    return {"ok": True, "owner": owner, "role": role, "scope": scope,
            "plan": "free", "member": "user:u1"}


def test_space_role_and_read_only_preflight():
    asked = []

    def authenticate(space):
        asked.append(space)
        return _auth(owner="team:" + space, role="viewer")

    kwargs = {"base_auth": _auth(), "address_space": None,
              "authenticate": authenticate, "valid_space": lambda s: s == "team-id"}
    assert toolcall.preflight("vinc_search", {"space": "unknown"}, **kwargs).kind == \
        "space_not_found"
    assert asked == []
    assert toolcall.preflight("vinc_ingest", {"space": "team-id"}, **kwargs).kind == \
        "role_denied"
    assert asked == ["team-id"]
    readonly = {**kwargs, "base_auth": _auth(scope="read")}
    assert toolcall.preflight("vinc_ingest", {}, **readonly).kind == "read_only"
    assert toolcall.preflight("vinc_search", {}, **readonly).kind == "ok"


def test_argument_errors_do_not_consume_quota():
    bridge = _Bridge()
    counted = []
    quota = lambda owner, plan: counted.append((owner, plan)) or None
    bad = toolcall.execute("vinc_search", {"query": "x", "limit": 999}, bridge,
                           forced_owner="user:u1", plan="free", quota=quota)
    assert bad.kind == "invalid_argument"
    assert counted == []
    assert bridge.calls == []
    good = toolcall.execute("vinc_search", {"query": "x"}, bridge,
                            forced_owner="user:u1", plan="free", quota=quota)
    assert good.kind == "ok"
    assert len(counted) == 1
    assert len(bridge.calls) == 1


def test_quota_denial_never_calls_bridge():
    bridge = _Bridge()
    denial = {"isError": True, "content": [{"type": "text", "text": "limit"}]}
    got = toolcall.execute("vinc_search", {"query": "x"}, bridge,
                           forced_owner="user:u1", plan="free",
                           quota=lambda _owner, _plan: denial)
    assert got.kind == "quota"
    assert got.result is denial
    assert bridge.calls == []


def test_failure_inventory_covers_every_public_tool():
    path = Path(__file__).parent.parent / "fixtures" / "tool_failure_shapes.json"
    inventory = json.loads(path.read_text(encoding="utf-8"))
    assert set(inventory["tools"]) == {tool["name"] for tool in tools.TOOLS}
    codes = {"success", "invalid_argument", "not_found", "upstream_unavailable", "internal",
             "person_answer_required", "request_expired", "run_state_protected"}
    assert all(rows and all(row["shape"] and row["code"] in codes for row in rows)
               for rows in inventory["tools"].values())


def test_mcp_gate_replies_keep_origin_main_bytes(tmp_path):
    fixtures = Path(__file__).parent.parent / "fixtures"
    output = tmp_path / "replies.txt"
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(fixtures / "mcp_gate_probe.py"), str(output)],
        cwd=Path(__file__).resolve().parents[2], env=env,
        capture_output=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    # The fixture file's line separators may be checked out as CRLF on Windows;
    # each tab-separated JSON payload is still compared byte for byte.
    assert output.read_bytes().splitlines() == \
        (fixtures / "mcp_gate_replies.txt").read_bytes().splitlines()


# 검증은 `toolcall` 이 소유한다: `execute`(MCP·REST)와 `call`(CLI·이벤트 기록·평가 스크립트).
# `tools.call` 은 검증하지 않는다. 아래 셋이 그 경계와, 검증이 원래 인자를 봐도 답이 같다는 전제를 지킨다.

_ROUTING_KEYS = ("target", "space", "owner", "theme_id", "debug", "_entitlements")


def test_routing_keys_that_tools_call_rewrites_never_take_part_in_validation():
    # `tools.call` 이 벗기거나 덮는 키가 필수·enum·범위가 되면, 원래 인자를 보는 검증과 고친 인자를
    # 보던 검증의 답이 갈린다. 그런 스키마를 들이려면 검증 자리를 다시 정해야 한다.
    for name, tool in tools._BY_NAME.items():
        schema = tool["inputSchema"]
        props = schema.get("properties") or {}
        for key in _ROUTING_KEYS:
            assert key not in schema.get("required", []), (name, key)
            spec = props.get(key) or {}
            assert not (spec.get("enum") or "minimum" in spec or "maximum" in spec
                        or (spec.get("items") or {}).get("enum")), (name, key)


def test_tools_call_does_not_validate_and_toolcall_call_does(monkeypatch):
    seen = []
    real = tools.validate_args
    monkeypatch.setattr(tools, "validate_args", lambda name, args: seen.append(name) or real(name, args))
    bridge = _Bridge()
    tools.call("vinc_search", {"query": "x"}, bridge)
    assert seen == []
    good = toolcall.execute("vinc_search", {"query": "x"}, bridge)
    assert good.kind == "ok" and seen == ["vinc_search"]
    seen.clear()
    toolcall.call("vinc_search", {"query": "x"}, bridge)
    assert seen == ["vinc_search"]
    try:
        toolcall.call("vinc_search", {"query": "x", "limit": 999}, bridge)
    except tools.InvalidArgument as exc:
        assert not isinstance(exc, ValueError)
        assert exc.meta["argument"] == "limit"
    else:
        raise AssertionError("toolcall.call accepted limit=999")
    # 받아들인 세 호출만 사이드카에 닿았다. 거절된 limit=999 는 닿지 않았다.
    assert len(bridge.calls) == 3


def test_toolcall_call_opens_the_bridge_only_after_validation():
    from contextlib import contextmanager
    opened, closed = [], []
    bridge = _Bridge()

    @contextmanager
    def lazy():
        opened.append(True)
        yield bridge
        closed.append(True)
    try:
        toolcall.call("vinc_search", {}, open_bridge=lazy)
    except tools.InvalidArgument:
        pass
    else:
        raise AssertionError("toolcall.call accepted a missing query")
    assert opened == [] and bridge.calls == []
    out = toolcall.call("vinc_search", {"query": "x"}, open_bridge=lazy)
    assert opened == closed == [True] and len(bridge.calls) == 1 and out["executed"] is True
    try:
        toolcall.call("vinc_search", {"query": "x"}, bridge, open_bridge=lazy)
    except TypeError:
        pass
    else:
        raise AssertionError("bridge and open_bridge together were accepted")
