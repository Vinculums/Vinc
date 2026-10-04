import copy
import json
from pathlib import Path

import pytest
from vinc_client import (OutputGate, OutputGateStopped, ToolProof, VincInvalidArgument,
                         build_package_fragment, check_package_handoff)

PROFILES = {p["id"]: p for p in json.loads((Path(__file__).parents[3] /
    "fixtures/output_gate/proposed-role-checks.json").read_text(encoding="utf-8"))["profiles"]}


def package(conclusion="Keep the original recommendation.", claim="Keep the cited rule."):
    return build_package_fragment("Which change?", conclusion,
        claims=[{"claim": claim, "evidence_ids": ["record:rule"]}],
        produced_by="concept:agent-product-owner", run_id="producer-run-1",
        package_id="record:package-fixture")["nodes"][0]


def gate(role="concept:agent-product-owner"):
    return OutputGate(PROFILES[role])


def test_handoff_keeps_exact_quoted_conclusion_claim_and_provenance():
    node = package(conclusion="Keep punctuation: `a` and\n한국어.")
    original = copy.deepcopy(node)
    block = check_package_handoff(node, space="personal", gate=gate(),
                                  readable_ids=["record:rule"], repositories=["Vinc"])
    quoted = json.loads(block.split("```\n", 1)[1].rsplit("\n```", 1)[0])
    assert node == original
    assert quoted["conclusion"] == node["props"]["conclusion"]
    assert quoted["claims"][0]["claim"] == node["props"]["claims"][0]["claim"]
    assert quoted["produced_by"] == node["props"]["produced_by"]
    assert quoted["run_id"] == node["props"]["run_id"]
    assert quoted["package"] == {"space": "personal", "node_id": node["id"]}
    assert quoted["claims"][0]["evidence_status"] == "resolved" and "DATA" in block


def test_missing_current_reader_evidence_blocks_the_entire_handoff():
    with pytest.raises(OutputGateStopped) as failure:
        check_package_handoff(package(), space="personal", gate=gate(),
                              readable_ids=[], repositories=["Vinc"])
    assert "handoff_manifest" in failure.value.result.failed_checks


@pytest.mark.parametrize("broken", ["claims", "producer", "run"])
def test_empty_manifest_fields_are_rejected(broken):
    node = package()
    node["props"][{"claims": "claims", "producer": "produced_by", "run": "run_id"}[broken]] = (
        [] if broken == "claims" else "")
    with pytest.raises(VincInvalidArgument):
        check_package_handoff(node, space="personal", gate=gate(),
                              readable_ids=["record:rule"], repositories=["Vinc"])


@pytest.mark.parametrize("claim,action", [
    ("Updated sync scripts", "edit"),
    ("Wrote reason into Vinc graph", "graph_write"),
    ("Ensured gate scripts pass", "gate"),
    ("Covered with unit tests", "tests"),
])
def test_original_exp05_unwitnessed_claims_block_until_exact_host_evidence(claim, action):
    selected = gate("concept:agent-design")
    assert selected.evaluate(claim).verdict == "block"
    node = package(claim=claim)
    node["props"]["produced_by"] = "concept:agent-design"
    with pytest.raises(OutputGateStopped):
        check_package_handoff(node, space="personal", gate=selected,
                              readable_ids=["record:rule"], repositories=["Vinc"])
    proof = ToolProof(action, "exact-target", "producer-run-1-call", True, claim)
    commands = [ToolProof("command", check["check"]["command"], "recorded-command-" + str(i), True)
                for i, check in enumerate(PROFILES["concept:agent-design"]["gates"])
                if check["check"]["type"] == "command_success"]
    block = check_package_handoff(node, space="personal", gate=selected,
        readable_ids=["record:rule"], proofs=[proof, *commands], repositories=["Vinc"])
    assert claim in block
    assert selected.evaluate(claim, proofs=[ToolProof(action, "target", "failed", False, claim)]).verdict == "block"


def test_past_edit_refusal_and_proposal_are_not_completion_claims():
    selected = gate("concept:agent-design")
    assert selected.evaluate("Update sync scripts next.").verdict == "pass"
    assert selected.evaluate("I did not update sync scripts.").verdict == "pass"


def test_existing_command_gate_is_not_skipped_at_package_handoff():
    props = copy.deepcopy(PROFILES["concept:agent-design"])
    command = next(g["check"]["command"] for g in props["gates"]
                   if g["check"]["type"] == "command_success")
    with pytest.raises(OutputGateStopped) as failure:
        check_package_handoff(package(), space="personal", gate=OutputGate(props),
                              readable_ids=["record:rule"], repositories=["Vinc"])
    assert any(check.startswith("command_") for check in failure.value.result.failed_checks)
    assert command


def test_oversized_package_cannot_silently_truncate_a_recommendation():
    with pytest.raises(VincInvalidArgument, match="package_budget_exceeded"):
        check_package_handoff(package(), space="personal", gate=gate(),
                              readable_ids=["record:rule"], repositories=["Vinc"], max_chars=100)
