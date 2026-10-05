# -*- coding: utf-8 -*-
"""vinc_role_check — 패키지와 같은 결정론 출력 게이트를 허브에서 돌린다(VIN-386).

허브 사본과 패키지 원본이 갈리면 같은 출력이 경로에 따라 다른 판정을 받는다.
그래서 사본은 바이트까지 같아야 하고, 사람이 판정해 둔 사례가 두 경로에서 같은
답을 내야 한다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vinc_mcp import tools  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402
from vinc_mcp.role_output_gate import OutputGate  # noqa: E402

FIXTURES = ROOT / "integrations" / "fixtures" / "output_gate"
CASES = json.loads((FIXTURES / "hand-labelled.json").read_text(encoding="utf-8"))["cases"]
PROFILES = {p["id"]: p for p in json.loads(
    (FIXTURES / "proposed-role-checks.json").read_text(encoding="utf-8"))["profiles"]}


def test_hub_copy_is_byte_identical_to_the_package_gate():
    hub = (ROOT / "vinc_mcp" / "role_output_gate.py").read_bytes()
    package = (ROOT / "integrations" / "client" / "python" / "src" / "vinc_client"
               / "output_gate.py").read_bytes()
    assert hub == package, ("vinc_mcp/role_output_gate.py 가 vinc_client/output_gate.py 와 "
                            "갈렸다. 패키지 쪽을 고쳤으면 같은 커밋에서 그대로 복사할 것")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_hub_gate_matches_the_hand_labels(case):
    gate = OutputGate(PROFILES[case["role_id"]], self_authored=case["self_authored"])
    assert gate.evaluate(case["answer"]).verdict == case["expected"]


class _RoleBridge(Bridge):
    """`/roles` 만 답하는 브리지. 역할 노드는 고정 프로파일에서 만든다."""

    def __init__(self, props: dict | None):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:test")
        self.props = props
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        if path != "/roles":
            return {"executed": False, "error": "unexpected path " + path}
        if self.props is None:
            return {"executed": True, "found": False}
        rid = params["role"]
        return {"executed": True, "found": True, "relations": [], "peers": [],
                "node": {"id": rid, "title": "Agent role", "domain": "vinc/agent/roles",
                         "props": self.props}}


def _check(props, **args):
    bridge = _RoleBridge(props)
    out = tools.call("vinc_role_check", {"role": "concept:agent-dev-core", **args}, bridge)
    return out, bridge


def _props(role_id="concept:agent-dev-core"):
    p = dict(PROFILES[role_id])
    p.update({"mission": "m", "tier": "execute", "rung": "strong", "never": ["merge"]})
    return p


def test_tool_blocks_a_claimed_side_effect_and_passes_a_plain_answer():
    out, bridge = _check(_props(), output="The change reads correctly; two findings follow.")
    assert (out["kind"], out["verdict"], out["role"]) == ("role_check", "pass", "concept:agent-dev-core")
    out, _ = _check(_props(), output="I merged the pull request and deployed it.")
    assert out["verdict"] == "block" and out["failed_checks"]
    # 호출자는 증거를 넘길 길이 없다: 실행 주장은 허브에서 언제나 막힌다.
    out, _ = _check(_props(), output="Tests passed.")
    assert out["verdict"] == "block"
    assert all(c[0] == "GET" for c in bridge.calls), "판정 도구가 쓰기 경로를 불렀다"


def test_tool_matches_the_package_for_every_hand_label():
    for case in CASES:
        out, _ = _check(dict(PROFILES[case["role_id"]]), output=case["answer"],
                        self_authored=case["self_authored"])
        assert out["verdict"] == case["expected"], case["id"]


def test_structured_person_only_request_interrupts_and_unknown_action_blocks():
    props = _props("concept:agent-reviewer-judgement")
    action = props["person_only"][0]["action"]
    out, _ = _check(props, output="Two findings are listed.",
                    person_only_requests=[{"action": action, "reason": "needs a person"}])
    assert (out["verdict"], out["failed_checks"]) == ("interrupt", ["person_only"])
    assert out["person_only_actions"] == [action]
    out, _ = _check(props, output="Two findings are listed.",
                    person_only_requests=[{"action": "not_an_action", "reason": "x"}])
    assert (out["verdict"], out["failed_checks"]) == ("block", ["unknown_person_only_request"])


def test_prose_only_contract_blocks():
    out, _ = _check({"mission": "m", "never": ["merge"], "person_only": ["merge"]},
                    output="Looks fine.")
    assert (out["verdict"], out["failed_checks"]) == ("block", ["invalid_role_contract"])


def test_unknown_role_and_bad_output_are_not_verdicts():
    out, _ = _check(None, output="x")
    assert out["kind"] == "role_check" and out["found"] is False and "verdict" not in out
    out, bridge = _check(_props(), output=" ")
    assert (out["executed"], out["error"]) == (False, "output_required") and not bridge.calls
    out, bridge = _check(_props(), output="x" * 131073)
    assert out["error"] == "output_required" and not bridge.calls


def test_role_check_is_a_viewer_tool_with_a_rest_route():
    from vinc_mcp import rest_routes
    assert tools.tool_tier("vinc_role_check") == "viewer"
    route, fields = rest_routes.find_route("POST", "/v1/roles/concept%3Aagent-qa/check")
    assert route.tool == "vinc_role_check" and fields == {"id": "concept:agent-qa"}
