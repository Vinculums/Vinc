"""역할 실행 정책: 계약의 네 칸을 읽고, 틀린 칸은 런을 시작하기 전에 멈춘다."""
from __future__ import annotations

import pytest
from vinc_client import RolePolicy, VincInvalidArgument

ROLE = "concept:agent-checker"
PERSON = [{"action": "merge", "reason": "a person merges"}]


def contract(**props):
    return {"id": ROLE, "props": {"mission": "Review.", "person_only": PERSON, **props}}


def test_a_role_without_the_keys_has_an_empty_policy():
    policy = RolePolicy.from_contract(contract())
    assert not policy and policy.output_kind is None and policy.limit("tool_calls") is None
    assert policy.act("merge") == "ask_first" and policy.act("comment") is None
    assert policy.capability("vinc.read") is None


def test_the_four_keys_are_read():
    policy = RolePolicy.from_contract(contract(
        output_kind="verdict", act_policy={"merge": "never", "comment": "act_and_report"},
        budget={"tool_calls": 40, "cost_usd": 1.5},
        capabilities={"allow": ["vinc.*"], "approve": ["github.pr.merge"], "deny": ["shell.*"]}))
    assert policy and policy.output_kind == "verdict" and policy.role_id == ROLE
    assert policy.act("merge") == "never" and policy.act("comment") == "act_and_report"
    assert policy.limit("tool_calls") == 40 and policy.limit("cost_usd") == 1.5
    assert policy.capability("vinc.search") == "allow"
    assert policy.capability("github.pr.merge") == "approve"
    assert policy.capability("shell.exec") == "deny" and policy.capability("shell") is None
    assert policy.capability("github.issue.read") is None


def test_deny_wins_over_allow():
    policy = RolePolicy.from_contract(contract(capabilities={"allow": ["github.*"],
                                                             "deny": ["github.pr.merge"]}))
    assert policy.capability("github.pr.merge") == "deny"
    assert policy.capability("github.pr.read") == "allow"


@pytest.mark.parametrize("props", [
    {"output_kind": "essay"},
    {"act_policy": {"Merge": "act"}},
    {"act_policy": {"comment": "maybe"}},
    {"act_policy": {"merge": "act"}},
    {"budget": {"tool_calls": 2.5}},
    {"budget": {"tokens": -1}},
    {"budget": {"cost_usd": float("nan")}},
    {"budget": {"minutes": 3}},
    {"capabilities": {"allow": "vinc.read"}},
    {"capabilities": {"permit": ["vinc.read"]}},
    {"capabilities": {"deny": ["Shell.Exec"]}},
])
def test_a_malformed_key_stops_the_run(props):
    with pytest.raises(VincInvalidArgument) as caught:
        RolePolicy.from_contract(contract(**props))
    assert caught.value.code == "invalid_role_contract"


# ── RoleToolPolicy 가 정책을 도구에 옮긴다 ─────────────────────────────────────

from vinc_client import RoleToolPolicy  # noqa: E402

MAP = {"merge_pr": "github.pr.merge", "run_shell": "shell.exec", "vinc_search": "vinc.search",
       "read_pr": "github.pr.read"}


class Tool:
    def __init__(self, name):
        self.name = name


def test_capabilities_bind_to_tools_through_the_map():
    policy = RoleToolPolicy.from_contract(contract(capabilities={
        "deny": ["shell.*"], "approve": ["github.pr.merge"]}), MAP)
    assert policy.disallowed == {"run_shell"} and policy.approve == {"merge_pr"}
    assert not policy.allows("run_shell") and not policy.allows("merge_pr")
    assert policy.allows("vinc_search") and policy.allows("read_pr")
    # 승인 대기 도구는 보인 채로 둔다. 모델이 사람을 부를 수 있어야 한다.
    names = [t.name for t in policy.allowed([Tool(n) for n in MAP])]
    assert names == ["merge_pr", "vinc_search", "read_pr"]
    assert "needs a person's approval" in policy.refusal("merge_pr")
    assert "does not allow" in policy.refusal("run_shell")


def test_an_allow_list_closes_mapped_tools_it_does_not_name():
    policy = RoleToolPolicy.from_contract(contract(capabilities={"allow": ["vinc.*",
                                                                          "github.pr.read"]}), MAP)
    assert policy.disallowed == {"merge_pr", "run_shell"}
    assert policy.allows("vinc_search") and policy.allows("read_pr")
    assert policy.allows("a_tool_left_out_of_the_map")


def test_capabilities_without_a_map_leave_tools_the_default_maps_do_not_know(caplog):
    # 지도가 없으면 기본 지도가 묶는다(test_capabilities.py). 그 지도가 모르는 이름은 건드리지 않고 경고한다.
    policy = RoleToolPolicy.from_contract(contract(capabilities={"deny": ["shell.*"]}))
    assert policy and not policy.unbound and policy.allows("run_shell")
    with caplog.at_level("WARNING", logger="vinc_client"):
        policy.warn_unmatched([Tool("run_shell")])
    assert "not enforced" in caplog.text


def test_an_empty_map_turns_the_defaults_off(caplog):
    policy = RoleToolPolicy.from_contract(contract(capabilities={"deny": ["shell.*"]}), {})
    assert policy.unbound and policy.allows("Bash")
    with caplog.at_level("WARNING", logger="vinc_client"):
        policy.warn_unmatched([Tool("Bash")])
    assert "no capability_map binds" in caplog.text


def test_the_budget_caps_every_tool_call_of_a_run():
    policy = RoleToolPolicy.from_contract(contract(budget={"tool_calls": 2}))
    assert policy and policy.tool_calls == 2
    assert [policy.over_budget(i) for i in range(4)] == [False, False, True, True]
    assert "allows 2 tool calls per run" in policy.budget_message()
    assert not RoleToolPolicy.from_contract(contract()).over_budget(100)


@pytest.mark.parametrize("mapping", [{"merge_pr": "GitHub.Merge"}, {"": "vinc.read"}, ["vinc.read"]])
def test_a_malformed_map_stops_the_run(mapping):
    with pytest.raises(VincInvalidArgument):
        RoleToolPolicy.from_contract(contract(), mapping)


def test_a_malformed_policy_stops_the_tool_policy_too():
    with pytest.raises(VincInvalidArgument) as caught:
        RoleToolPolicy.from_contract(contract(budget={"tool_calls": -1}))
    assert caught.value.code == "invalid_role_contract"
