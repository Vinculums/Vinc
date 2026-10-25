"""역할 실행 정책의 어휘가 허브의 쓰기 검사와 클라이언트에서 같은가. 두 쪽은 서로 import 하지 않는다."""
import importlib.util
from pathlib import Path

from vinc_client import role_policy

HUB = Path(__file__).resolve().parents[2] / "api" / "role_policy_contract.py"


def _hub():
    spec = importlib.util.spec_from_file_location("hub_role_policy_contract", HUB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
