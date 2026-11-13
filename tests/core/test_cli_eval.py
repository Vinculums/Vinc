"""`vinc eval cards` 와 `vinc_eval_cards`: 플래그가 도구 인자로, 도구가 사이드카 `/eval-cards` 로.

근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from api import cli_tools
from vinc_mcp import rest_routes, tools
from vinc_mcp.bridge import Bridge


def args(**kw):
    return SimpleNamespace(**{"cmd": "eval", "evalcmd": "cards", "owner": None, **kw})


def test_cards_maps_the_role_and_keeps_only_the_flags_that_were_given():
    name, data, guard = cli_tools.tool_request(args(role="agent-dev-core", limit=None))
    assert (name, data, guard) == ("vinc_eval_cards", {"role": "agent-dev-core"}, None)
    assert cli_tools.tool_request(args(role="agent-dev-core", limit=3))[1] == {
        "role": "agent-dev-core", "limit": 3}


class _Bridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:test")
        self.seen = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.seen.append((method, path, dict(params or {})))
        return {"kind": "eval_cards", "executed": True, "role": params["role"], "cards": [],
                "total": 0, "truncated": False}


def test_the_tool_reads_the_sidecar_route_for_the_chosen_role():
    bridge = _Bridge()
    out = tools.call("vinc_eval_cards", {"role": "agent-dev-core", "limit": 5}, bridge)
    assert out["executed"] is True
    assert bridge.seen == [("GET", "/eval-cards",
                            {"owner": "user:test", "role": "agent-dev-core", "limit": 5})]


def test_a_role_is_required_and_the_limit_is_bounded():
    for bad in ({}, {"role": "agent-x", "limit": 0}, {"role": "agent-x", "limit": 101}):
        with pytest.raises(tools.InvalidArgument):
            tools.validate_args("vinc_eval_cards", bad)


def test_the_rest_route_and_the_tool_agree_and_it_is_read_only():
    route = next(r for r in rest_routes.READ_ROUTES if r.tool == "vinc_eval_cards")
    assert (route.method, route.path) == ("GET", "/v1/roles/{id}/eval-cards")
    assert tools.tool_tier("vinc_eval_cards") == "viewer"


def test_the_verb_is_a_tool_verb_so_the_command_line_runs_it():
    # 도구 동사 목록에 없으면 명령줄은 이 동사를 기기 전용 동사로 보내고, 그쪽에는 처리기가 없어 아무것도
    # 찍지 않고 2 로 끝난다.
    from api import cli_target
    assert "eval" in cli_target.TOOL_CMDS
    assert "eval" not in cli_target.DEVICE_CMDS
