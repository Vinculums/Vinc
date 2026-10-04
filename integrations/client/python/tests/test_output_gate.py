import json
from pathlib import Path

import pytest

from vinc_client.output_gate import OutputGate, ToolProof, preserve_required_route

FIXTURES = Path(__file__).resolve().parents[3] / 'fixtures' / 'output_gate'
CASES = json.loads((FIXTURES / 'hand-labelled.json').read_text(encoding='utf-8'))['cases']
PROFILES = {p['id']: p for p in json.loads((FIXTURES / 'proposed-role-checks.json').read_text(encoding='utf-8'))['profiles']}


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_original_frozen_hand_labels(case):
    gate = OutputGate(PROFILES[case['role_id']], self_authored=case['self_authored'])
    assert gate.evaluate(case['answer']).verdict == case['expected']


def test_reason_prose_is_not_authority():
    props = json.loads(json.dumps(PROFILES['concept:agent-reviewer-judgement']))
    props['person_only'][0]['reason'] = 'pricing sign off approval migration'
    gate = OutputGate(props)
    assert gate.evaluate('Please approve the price.').verdict == 'pass'
    assert gate.evaluate('Merge this PR.').verdict == 'interrupt'


def test_side_effect_claim_requires_matching_successful_host_proof():
    gate = OutputGate(PROFILES['concept:agent-dev-core'])
    assert gate.evaluate('Tests passed.').verdict == 'block'
    assert gate.evaluate('Tests passed.', proofs=[ToolProof('tests', 'tests/core', 'call1', False)]).verdict == 'block'
    assert gate.evaluate('Tests passed.', proofs=[ToolProof('gate', 'lint', 'call2', True)]).verdict == 'block'
    assert gate.evaluate('Tests passed.', proofs=[ToolProof('tests', 'tests/core', 'call3', True, 'Tests passed.')]).verdict == 'pass'
    assert gate.evaluate('All tests passed.', proofs=[ToolProof('tests', 'tests/core', 'call3', True, 'Tests passed.')]).verdict == 'block'
    assert gate.evaluate('Tests passed.', proofs=[{'action': 'tests', 'success': True}]).verdict == 'block'


def test_gate_override_cannot_delete_replace_or_relax_required_gate():
    props = PROFILES['concept:agent-dev-core']
    for change in ('delete', 'replace', 'relax'):
        current = json.loads(json.dumps(props))
        if change == 'delete':
            current['gates'].pop(0)
        elif change == 'replace':
            current['gates'][0]['check'] = {'type': 'korean_comment_allowed'}
        else:
            current['gates'][0]['on_fail'] = 'block'
        assert OutputGate(current).evaluate('Okay').verdict == 'block'
    assert OutputGate(props, extra_gates=[props['gates'][0]]).evaluate('Okay').verdict == 'block'


def test_snapshot_immutability():
    props = json.loads(json.dumps(PROFILES['concept:agent-reviewer-judgement']))
    gate = OutputGate(props)
    props['person_only'].clear()
    props['gates'].clear()
    assert gate.evaluate('Merge this PR.').verdict == 'interrupt'


def test_gate_runner_route_cannot_be_bypassed_even_if_old_edges_remain():
    base = [('dev-app', 'gate-runner'), ('gate-runner', 'qa')]
    with pytest.raises(ValueError):
        preserve_required_route(base, [('dev-app', 'qa'), ('gate-runner', 'qa')])
    with pytest.raises(ValueError):
        preserve_required_route(base, base + [('dev-app', 'qa')])
    assert preserve_required_route(base, base + [('qa', 'owner')])


def test_missing_unknown_malformed_contract_blocks():
    for field, value in [('gates', []), ('never_checks', [{'type': 'unknown'}]), ('person_only', ['merge'])]:
        props = dict(PROFILES['concept:agent-dev-core'], **{field: value})
        assert OutputGate(props).evaluate('Okay').verdict == 'block'


def test_handoff_requires_real_command_results_and_resolved_manifest():
    gate = OutputGate(PROFILES['concept:agent-dev-core'])
    assert gate.evaluate('Ready', phase='handoff').verdict == 'block'
    manifest = {'producer': 'concept:agent-dev-core', 'run': 'r1',
                'claims': [{'what': 'Implemented', 'evidence_ids': ['record:evidence']} ]}
    proofs = [ToolProof('command', c, 'call' + str(i), True) for i, c in enumerate(PROFILES['concept:agent-dev-core']['source_gates'])]
    assert gate.evaluate('Ready', phase='handoff', manifest=manifest, proofs=proofs).verdict == 'block'
    assert gate.evaluate('Ready', phase='handoff', manifest=manifest, proofs=proofs,
                         resolved_sources=['record:evidence'], repositories=['Vinc']).verdict == 'pass'


def test_explicit_request_matches_action_only_and_unknown_fails_closed():
    gate = OutputGate(PROFILES['concept:agent-copy-writer'])
    assert gate.evaluate('Ready', person_only_requests=[{'action': 'sign_off', 'reason': 'Screen review'}]).verdict == 'interrupt'
    assert gate.evaluate('Ready', person_only_requests=[{'action': 'sign off on screen', 'reason': 'Screen review'}]).verdict == 'block'


def test_node_ids_and_removed_lines_are_not_action_proposals():
    gate = OutputGate(PROFILES['concept:agent-dev-core'], self_authored=True)
    assert gate.evaluate('See decision:merge-main-after-migration.').verdict == 'pass'
    assert gate.evaluate('```diff\n- vinc_merge_nodes()\n```').verdict == 'pass'
    assert gate.evaluate('```diff\n+ vinc_merge_nodes()\n```').verdict == 'block'


def test_empty_output_and_unsupported_machine_action_block():
    assert OutputGate(PROFILES['concept:agent-dev-core']).evaluate(' ').verdict == 'block'
    props = dict(PROFILES['concept:agent-dev-core'], never_checks=[{'type':'forbidden_actions', 'actions':['scope']}])
    assert OutputGate(props).evaluate('Okay').verdict == 'block'
