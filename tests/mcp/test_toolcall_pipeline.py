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
