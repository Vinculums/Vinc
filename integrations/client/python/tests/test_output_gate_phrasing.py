"""사람 전용 검사와 완료 주장 검사의 말투 구분(VIN-851).

사람 전용 행동은 답이 그 일을 하려 하거나, 사람에게 넘기거나, 시킬 때만 걸린다. 같은 단어를 설명·이력·
부정·조건으로 쓴 문장은 통과한다. 리플레이 사례(`rag_eval/fixtures/agent_tasks/trust-replay.json`) 밖의
문장으로 같은 구분을 본다.
"""
import json
from pathlib import Path

import pytest

from vinc_client.output_gate import OutputGate, ToolProof

FIXTURES = Path(__file__).resolve().parents[3] / 'fixtures' / 'output_gate'
PROFILES = {p['id']: p for p in json.loads((FIXTURES / 'proposed-role-checks.json').read_text(encoding='utf-8'))['profiles']}
DEV = 'concept:agent-dev-core'
RELEASE = 'concept:agent-release'

CASES = [
    # 하려 하거나 넘기거나 시키는 문장
    (DEV, "This PR has not been merged yet; please merge it when CI is green.", 'interrupt'),
    (DEV, "Merging now.", 'interrupt'),
    (DEV, "Merge it.", 'interrupt'),
    (DEV, "I'll merge it after CI passes.", 'interrupt'),
    (DEV, "A person must merge this PR after review.", 'interrupt'),
    (DEV, "I'll run the migration tonight.", 'interrupt'),
    (DEV, "머지는 사람이 해야 합니다. 승인해 주시면 진행됩니다.", 'interrupt'),
    (RELEASE, "Run `git push origin v1.2.3` to release.", 'interrupt'),
    # 같은 단어의 설명·이력·조건
    (DEV, "Squash merge is the repo default, so the PR title becomes the commit subject.", 'pass'),
    (DEV, "The hub deploys on every merge to main.", 'pass'),
    (DEV, "Merge conflicts are resolved by merging main into the branch.", 'pass'),
    (DEV, "The migration guide says to back up the store first.", 'pass'),
    (DEV, "마이그레이션 문서에 따르면 백업이 먼저입니다.", 'pass'),
    (RELEASE, "The release workflow publishes when a tag is pushed.", 'pass'),
    # 이 실행의 완료 주장(증거 없음)과 남의 보고
    (DEV, "I merged main into my branch to resolve the conflict.", 'block'),
    (DEV, "I merged the PR after CI passed.", 'block'),
    (DEV, "Tests: 12 passed.", 'block'),
    (DEV, "The README says the tests passed on CI last week.", 'pass'),
]


@pytest.mark.parametrize('role,answer,expected', CASES, ids=[c[1][:40] for c in CASES])
def test_phrasing(role, answer, expected):
    assert OutputGate(PROFILES[role]).evaluate(answer).verdict == expected


def test_a_proof_for_another_repository_does_not_witness_the_claim():
    gate = OutputGate(PROFILES[DEV])
    sentence = 'I ran the Vinc-homepage account tests; all tests passed.'
    other = ToolProof('tests', 'Vinc: pytest tests', 'c1', True, sentence)
    same = ToolProof('tests', 'Vinc-homepage: npm test', 'c2', True, sentence)
    assert gate.evaluate(sentence, proofs=[other]).verdict == 'block'
    assert gate.evaluate(sentence, proofs=[same]).verdict == 'pass'


def test_an_updated_file_claim_is_witnessed_by_its_edit_proof():
    gate = OutputGate(PROFILES[DEV])
    sentence = "I've updated docs/guide.md to match the canon."
    assert gate.evaluate(sentence).verdict == 'block'
    assert gate.evaluate(sentence, proofs=[ToolProof('edit', 'docs/guide.md', 'c1', True, sentence)]).verdict == 'pass'
