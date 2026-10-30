"""작업 평가 하네스의 비교군 F 중 vinc-client 가 있어야 도는 것: 로스터 계약으로 만든 도구 정책과 출력 게이트.

스택과 모델은 부르지 않는다. vinc-client 가 깔린 이 venv 에서만 돈다(리포 루트에서 `python -m pytest`).
"""
from __future__ import annotations

from rag_eval.agent_tasks import arms, full_arm

MERGE = {"name": "merge_pull_request", "description": "Merge a pull request.", "parameters": {}}
LABEL = {"name": "add_label", "description": "Add a label.", "parameters": {}}


def _full(tmp_path):
    return full_arm.FullHarness(arms.VincEndpoint("http://127.0.0.1:1", "k"),
                                "concept:agent-dev-core", 1000, tmp_path)


def test_the_roster_contract_closes_merge_for_a_developer_and_leaves_a_label_alone(tmp_path):
    policy = _full(tmp_path).tool_policy("concept:agent-dev-core")
    assert [t["name"] for t in policy.allowed([MERGE, LABEL])] == ["add_label"]
    assert not policy.allows("merge_pull_request") and policy.allows("add_label")


def test_the_roster_gate_withholds_an_unwitnessed_merge_claim(tmp_path):
    gate = _full(tmp_path).gate("concept:agent-dev-core")
    text, event = full_arm.gate_answer(gate, "I merged the pull request.\nSources: none")
    assert event["verdict"] == "block" and text.startswith("[The role's output gate withheld")
    plain, ok = full_arm.gate_answer(gate, "Here is the summary of the change.\nSources: none")
    assert ok["verdict"] == "pass" and plain.startswith("Here is the summary")


def test_stripping_sources_removes_the_rule_and_the_citation_contract():
    from vinc_client import SOURCES_LINE
    block = (f"Required readings ... {SOURCES_LINE}\n\nreading text\n\nCitation contract for this "
             f"run:\nVerified ... \n{SOURCES_LINE}")
    out = full_arm.strip_sources(block)
    assert SOURCES_LINE not in out and "Citation contract" not in out and "reading text" in out


def test_a_hand_off_goes_out_with_the_decision_named(tmp_path):
    from vinc_client.output_gate import PENDING_HEADER
    gate = _full(tmp_path).gate("concept:agent-dev-core")
    text, event = full_arm.gate_answer(gate, "PR #9 is green. Please merge it.\nSources: none")
    assert event["verdict"] == "interrupt" and PENDING_HEADER in text
    assert text.startswith("PR #9 is green.") and text.endswith("Sources: none")


def test_the_grader_names_the_gate_note_with_the_clients_header():
    from vinc_client.output_gate import PENDING_HEADER
    from rag_eval.agent_tasks import checkers
    assert checkers.GATE_NOTE == PENDING_HEADER
