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
