"""역할 실행 정책의 어휘가 허브의 쓰기 검사와 클라이언트에서 같은가. 두 쪽은 서로 import 하지 않는다."""
import importlib
import sys
import types
from pathlib import Path

from vinc_client import capabilities, role_policy

API = Path(__file__).resolve().parents[2] / "api"


def _hub(name="role_policy_contract"):
    # api 패키지의 __init__ 은 저장소 엔진을 읽는다. 이 venv 에는 없으므로 빈 패키지로 감싸 읽는다.
    if "hub_api" not in sys.modules:
        package = types.ModuleType("hub_api")
        package.__path__ = [str(API)]
        sys.modules["hub_api"] = package
    return importlib.import_module(f"hub_api.{name}")


def test_the_vocabulary_is_the_same_on_both_sides():
    hub = _hub()
    for name in ("OUTPUT_KINDS", "ACT_MODES", "BUDGET_INTS", "BUDGET_NUMBERS",
                 "CAPABILITY_LISTS", "MAX_ENTRIES"):
        assert getattr(hub, name) == getattr(role_policy, name), name
    for name in ("ACTION", "CAPABILITY"):
        assert getattr(hub, name).pattern == getattr(role_policy, name).pattern, name


def test_what_the_hub_accepts_the_client_reads():
    hub = _hub()
    props = {"output_kind": "write_proposal", "act_policy": {"merge": "ask_first"},
             "budget": {"model_calls": 12, "wall_seconds": 60.5},
             "capabilities": {"allow": ["vinc.read"], "deny": ["git.push"]}}
    assert hub.check(props) == []
    policy = role_policy.RolePolicy.from_contract({"id": "concept:agent-x", "props": props})
    assert policy.act("merge") == "ask_first" and policy.capability("git.push") == "deny"


def test_the_capability_vocabulary_is_the_same_on_both_sides():
    hub = _hub("capabilities")
    assert hub.VOCABULARY_VERSION == capabilities.VOCABULARY_VERSION
    assert hub.CAPABILITIES == frozenset(capabilities.CAPABILITIES)
    assert hub.FAMILIES == capabilities.FAMILIES
    every_mapped = {cap for table in capabilities.SERVER_MAPS.values() for cap in table.values()}
    assert every_mapped | set(capabilities.BUILTIN_MAP.values()) <= hub.CAPABILITIES


def test_the_hub_warns_on_a_typo_inside_a_known_family_only():
    hub = _hub()
    warned = hub.check({"capabilities": {"deny": ["github.pr.merg", "acme.deploy"],
                                         "approve": ["github.pr.*"]}})
    assert len(warned) == 1 and "github.pr.merg" in warned[0] and "acme" not in warned[0]


def _mcp(name):
    # vinc_mcp 의 __init__ 은 서버를 띄울 것들을 읽는다. 빈 패키지로 감싸 모듈 하나만 읽는다.
    if "hub_mcp" not in sys.modules:
        package = types.ModuleType("hub_mcp")
        package.__path__ = [str(API.parent / "vinc_mcp")]
        sys.modules["hub_mcp"] = package
    return importlib.import_module(f"hub_mcp.{name}")


def test_the_run_protocol_judges_a_capability_as_the_client_does():
    protocol = _mcp("run_protocol")
    caps = {"deny": ["shell.*", "github.repo.write"], "approve": ["github.pr.*"],
            "allow": ["github.pr.read", "vinc.read"]}
    policy = role_policy.RolePolicy.from_contract({"id": "concept:agent-x",
                                                   "props": {"capabilities": caps}})
    for cap in ("shell.exec", "github.repo.write", "github.pr.merge", "github.pr.read",
                "vinc.read", "slack.read", "shell"):
        assert protocol.capability_verdict(caps, cap) == policy.capability(cap), cap
