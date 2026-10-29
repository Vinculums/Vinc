"""능력 어휘와 기본 지도: 지도를 주지 않아도 흔한 서버의 도구에 역할의 능력 정책이 걸린다."""
from __future__ import annotations

import logging

from vinc_client import capabilities
from vinc_client.tool_policy import RoleToolPolicy


class Tool:
    def __init__(self, name):
        self.name = name


def contract(**props):
    return {"id": "concept:agent-release", "props": props}


GITHUB = ["mcp__github__pull_request_read", "mcp__github__create_pull_request",
          "mcp__github__merge_pull_request", "mcp__github__push_files"]


def test_every_mapped_tool_names_an_id_in_the_vocabulary():
    for table in (*capabilities.SERVER_MAPS.values(), capabilities.BUILTIN_MAP):
        assert set(table.values()) <= set(capabilities.CAPABILITIES)
    assert capabilities.FAMILIES == ("fs", "github", "linear", "shell", "slack", "vinc")


def test_a_host_prefix_names_the_server_and_a_bare_name_must_be_unambiguous():
    assert capabilities.default_capability("mcp__github__merge_pull_request") == "github.pr.merge"
    assert capabilities.default_capability("mcp__Linear__list_issues") == "linear.read"
    assert capabilities.default_capability("merge_pull_request") == "github.pr.merge"
    # GitHub 과 Linear 둘 다에 있는 이름은 접두 없이는 묶지 않는다
    assert capabilities.default_capability("list_issues") is None
    assert capabilities.default_capability("Bash") == "shell.exec"
    # Vinc 자신의 도구는 기본 지도에 없다(역할의 disallowed_tools 와 budget 이 다룬다)
    for name in ("vinc_search", "mcp__vinc__vinc_search", "mcp__Vinc__vinc_doc_get"):
        assert capabilities.default_capability(name) is None
    assert capabilities.default_capability("mcp__unknown__tool") is None


def test_a_denied_capability_hides_the_github_tool_with_no_map():
    policy = RoleToolPolicy.from_contract(contract(capabilities={"deny": ["github.pr.merge"]}))
    kept = [t.name for t in policy.allowed([Tool(n) for n in GITHUB + ["vinc_search"]])]
    assert "mcp__github__merge_pull_request" not in kept and "vinc_search" in kept
    assert not policy.allows("mcp__github__merge_pull_request")
    assert "does not allow the mcp__github__merge_pull_request tool" in policy.refusal(
        "mcp__github__merge_pull_request")
    assert policy.restricts_tools


def test_an_approval_capability_keeps_the_tool_visible_and_asks_for_a_person():
    policy = RoleToolPolicy.from_contract(contract(capabilities={"approve": ["github.pr.*"]}))
    tools = [Tool(n) for n in GITHUB]
    assert [t.name for t in policy.allowed(tools)] == GITHUB
    assert not policy.allows("mcp__github__create_pull_request")
    assert "needs a person's approval" in policy.refusal("mcp__github__create_pull_request")
    assert policy.allows("mcp__github__push_files") and policy.holds_tools


def test_an_allow_list_closes_the_known_tools_it_does_not_name_and_leaves_the_rest():
    policy = RoleToolPolicy.from_contract(contract(capabilities={"allow": ["github.pr.read"]}))
    kept = [t.name for t in policy.allowed([Tool(n) for n in GITHUB + ["Bash", "vinc_search",
                                                                        "my_own_tool"]])]
    assert kept == ["mcp__github__pull_request_read", "vinc_search", "my_own_tool"]


def test_a_given_map_replaces_the_defaults(caplog):
    policy = RoleToolPolicy.from_contract(contract(capabilities={"deny": ["github.pr.merge"]}),
                                          {"merge_it": "github.pr.merge"})
    assert not policy.allows("merge_it") and policy.allows("mcp__github__merge_pull_request")


def test_a_role_without_capabilities_is_untouched_by_the_defaults(caplog):
    policy = RoleToolPolicy.from_contract(contract())
    assert not policy and policy.allows("mcp__github__merge_pull_request")
    with caplog.at_level(logging.WARNING, logger="vinc_client"):
        policy.warn_unmatched([Tool("mcp__github__merge_pull_request")])
    assert caplog.text == ""


def test_known_ids_and_family_patterns():
    assert capabilities.is_known("github.pr.merge") and capabilities.is_known("github.*")
    assert not capabilities.is_known("github.pr.merg") and not capabilities.is_known("acme.deploy")
