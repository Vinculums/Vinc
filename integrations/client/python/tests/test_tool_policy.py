"""A role's disallowed_tools: one policy object that every framework package applies."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest
from vinc_client import (AsyncVincClient, RolePrefetch, RoleToolPolicy, VincClient,
                         VincInvalidArgument)
from vinc_client.tool_policy import tool_name

ROLE = "concept:agent-copy-writer"


def contract(disallowed=None):
    props = {"reads": []}
    if disallowed is not None:
        props["disallowed_tools"] = disallowed
    return {"found": True, "id": ROLE, "props": props}


def test_absent_list_disallows_nothing_and_a_list_is_kept_by_exact_name():
    assert not RoleToolPolicy.from_contract(contract())
    policy = RoleToolPolicy.from_contract(contract(["Bash", "Edit"]))
    assert policy and policy.role_id == ROLE and policy.disallowed == {"Bash", "Edit"}
    assert not policy.allows("Bash") and policy.allows("bash") and policy.allows("Bash ")
    assert policy.allows(None)


@pytest.mark.parametrize("bad", ["Bash", ["Bash", 3], ["Bash", " "], {"Bash": True},
                                 ["x" * 257], ["t"] * 257])
def test_a_malformed_list_stops_instead_of_guessing(bad):
    with pytest.raises(VincInvalidArgument) as caught:
        RoleToolPolicy.from_contract(contract(bad))
    assert caught.value.code == "invalid_role_contract"


def test_tool_names_come_from_objects_and_provider_dicts():
    assert tool_name(SimpleNamespace(name="Bash")) == "Bash"
    assert tool_name({"name": "web_search", "type": "web_search"}) == "web_search"
    assert tool_name({"type": "function", "function": {"name": "Edit"}}) == "Edit"
    assert tool_name({"type": "code_interpreter"}) is None
    assert tool_name(object()) is None


def test_allowed_keeps_order_and_unmatched_entries_are_warned_once(caplog):
    policy = RoleToolPolicy(ROLE, ["Write", "Bash", "Agent"])
    tools = [SimpleNamespace(name="lookup"), SimpleNamespace(name="Bash"),
             {"type": "function", "function": {"name": "search"}}]
    assert [tool_name(t) for t in policy.allowed(tools)] == ["lookup", "search"]
    with caplog.at_level(logging.WARNING, logger="vinc_client"):
        assert policy.warn_unmatched(tools) == ["Agent", "Write"]
    assert len(caplog.records) == 1
    assert ROLE in caplog.records[0].getMessage() and "Agent, Write" in caplog.records[0].getMessage()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="vinc_client"):
        assert RoleToolPolicy(ROLE, ["Bash"]).warn_unmatched(tools) == []
    assert not caplog.records
    assert policy.refusal("Bash") == f"The role {ROLE} does not allow the Bash tool; continue without it."


@pytest.mark.parametrize("async_mode", [False, True])
def test_prefetch_with_contract_returns_the_same_block_and_the_contract_it_read(async_mode):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json=contract(["Bash"]))
        if request.url.path.endswith("brief"):
            return httpx.Response(200, json={"status": "resolved", "node": {
                "id": "decision:fixture", "cite_id": "decision:fixture"}, "one_liner": "Keep it."})
        return httpx.Response(200, json={"node_hits": []})

    async def go():
        client = (AsyncVincClient if async_mode else VincClient)(
            "vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler))
        call = client.prefetch_with_contract(ROLE, "Review.")
        fetched = await call if async_mode else call
        calls = len(seen)
        call = client.prefetch(ROLE, "Review.")
        block = await call if async_mode else call
        assert isinstance(fetched, RolePrefetch)
        assert fetched.block == block and isinstance(block, str)
        assert fetched.contract["props"]["disallowed_tools"] == ["Bash"]
        # 계약은 같은 읽기에서 나온다. 역할을 한 번 더 읽지 않는다.
        assert calls == len(seen) - calls == 3

    asyncio.run(go())
