"""역할 실행 정책 칸 넷: 맞는 값은 조용하고, 틀린 값은 경고하되 쓰기는 막지 않으며, 렌더러가 싣는다."""
from __future__ import annotations

import pytest

from api import role_contract as rc
from api import role_policy_contract as policy
from vinc_mcp import roles

GOOD = {
    "output_kind": "verdict",
    "act_policy": {"merge": "ask_first", "comment": "act_and_report", "deploy": "never"},
    "budget": {"tool_calls": 40, "tokens": 200000, "cost_usd": 1.5, "wall_seconds": 900},
    "capabilities": {"allow": ["vinc.read", "github.pr.read"], "approve": ["github.pr.merge"],
                     "deny": ["shell.*"]},
}


def role(**props):
    base = {"mission": "Review.", "tier": "verify", "rung": "strong", "never": ["merge"],
            "person_only": [{"action": "merge", "reason": "a person merges"}]}
    return {"id": "concept:agent-checker", "type": "concept", "title": "Checker",
            "domain": "vinc/agent/roles", "props": {**base, **props}}


def test_the_renderer_and_the_write_check_name_the_same_keys():
    assert roles.POLICY_KEYS == policy.KEYS


def test_good_values_are_quiet():
    assert policy.check(GOOD) == []
    assert rc.check(role(**GOOD), created=True) == []


def test_absent_keys_are_not_warned():
    assert policy.check({}) == []


@pytest.mark.parametrize("props,needle", [
    ({"output_kind": "essay"}, "output_kind must be one of"),
    ({"act_policy": {"Merge": "act"}}, "act_policy must map"),
    ({"act_policy": {"merge": "maybe"}}, "act_policy must map"),
    ({"act_policy": ["merge"]}, "act_policy must map"),
    ({"budget": {}}, "budget must hold"),
    ({"budget": {"tool_calls": 2.5}}, "budget must hold"),
    ({"budget": {"tool_calls": True}}, "budget must hold"),
    ({"budget": {"cost_usd": -1}}, "budget must hold"),
    ({"budget": {"minutes": 3}}, "budget must hold"),
    ({"capabilities": {"allow": "vinc.read"}}, "capabilities must hold"),
    ({"capabilities": {"allow": ["Vinc.Read"]}}, "capabilities must hold"),
    ({"capabilities": {"permit": ["vinc.read"]}}, "capabilities must hold"),
    ({"capabilities": {"allow": ["vinc.*"], "deny": ["vinc.*"]}}, "more than one list"),
])
def test_bad_values_warn_once(props, needle):
    warnings = policy.check(props)
    assert len(warnings) == 1 and needle in warnings[0]


def test_a_policy_cannot_free_a_person_only_action():
    warnings = rc.check(role(act_policy={"merge": "act"}), created=True)
    assert len(warnings) == 1 and "person_only action: merge" in warnings[0]
    assert rc.check(role(act_policy={"merge": "never"}), created=True) == []


def test_the_write_still_lands_with_a_warning():
    from api import fragment
    from test_decision_type import _fresh_store, _restore
    store, previous = _fresh_store()
    try:
        bad = role(budget={"tool_calls": -3})
        result = fragment.merge_fragment({"owner": "user:u1", "nodes": [bad]}, store=store)
        assert any("budget must hold" in w for w in result.get("warnings") or [])
        assert result["merged"]["nodes"]["created"] == 1
    finally:
        store.close()
        _restore(previous)


def test_renderer_shows_the_keys_and_leaves_other_roles_alone():
    entry = {"id": "concept:agent-checker", "name": "agent-checker", "label": "Checker",
             "props": {**role()["props"], **GOOD}}
    compact = roles.render_shell(entry, compact=True)
    assert "## Output kind\n- verdict" in compact
    assert "## Act policy\n- merge: ask_first\n- comment: act_and_report\n- deploy: never" in compact
    assert "## Budget\n- tool_calls: 40" in compact
    assert "- approve: github.pr.merge" in compact and "- deny: shell.*" in compact
    full = roles.render_shell(entry)
    assert "## Capabilities\n\n- allow: vinc.read, github.pr.read" in full

    plain = {**entry, "props": role()["props"]}
    for form in (True, False):
        text = roles.render_shell(plain, compact=form)
        assert "Output kind" not in text and "Act policy" not in text and "Budget" not in text


def test_the_contract_read_carries_the_keys():
    class Bridge:
        owner = "user:u1"

        def request(self, *_a, **_k):
            node = role(**GOOD)
            return {"executed": True, "found": True, "relations": [],
                    "node": {"id": node["id"], "title": node["title"], "domain": node["domain"],
                             "props": node["props"]}}
    got = roles.contract("agent-checker", Bridge())
    assert got["executed"] is True
    assert {k: got["props"][k] for k in roles.POLICY_KEYS} == GOOD
    assert "## Act policy" in got["rendered"]
