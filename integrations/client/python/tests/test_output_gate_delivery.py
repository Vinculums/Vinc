"""사람에게 가는 답의 전달: 사람이 결정할 일이 있으면 답은 나가고 그 결정이 이름과 사유로 붙는다."""
import json
from pathlib import Path

import pytest

from vinc_client.output_gate import (PENDING_HEADER, GateResult, OutputGate, OutputGateStopped,
                                     delivered_result, with_pending)

FIXTURES = Path(__file__).resolve().parents[3] / 'fixtures' / 'output_gate'
PROFILES = {p['id']: p for p in json.loads((FIXTURES / 'proposed-role-checks.json').read_text(encoding='utf-8'))['profiles']}
DEV = OutputGate(PROFILES['concept:agent-dev-core'])


def test_a_passing_answer_goes_out_unchanged():
    d = DEV.deliver('Here is the plan.\nSources: none')
    assert (d.text, d.result.verdict) == ('Here is the plan.\nSources: none', 'pass')


def test_a_hand_off_goes_out_with_the_decision_named_above_the_sources_line():
    d = DEV.deliver('PR #9 is green. Please merge it.\nSources: [record:x]')
    assert d.result.verdict == 'interrupt'
    assert d.text == ('PR #9 is green. Please merge it.\n\n' + PENDING_HEADER
                      + '\n- merge: merge\nSources: [record:x]')
    assert delivered_result(d.text) == GateResult('interrupt', ('person_only',), ('merge',))


def test_without_a_sources_line_the_note_goes_last():
    d = DEV.deliver('Please merge it.')
    assert d.text == 'Please merge it.\n\n' + PENDING_HEADER + '\n- merge: merge'


def test_a_block_and_a_hand_off_to_another_agent_still_stop():
    with pytest.raises(OutputGateStopped) as exc:
        DEV.deliver('I merged it.')
    assert exc.value.result.verdict == 'block'
    with pytest.raises(OutputGateStopped):
        DEV.deliver('Please merge it.', phase='handoff', repositories=['Vinc'])


def test_require_pass_stays_strict():
    with pytest.raises(OutputGateStopped) as exc:
        DEV.require_pass('Please merge it.')
    assert exc.value.result.verdict == 'interrupt'


def test_delivered_result_reads_only_the_note():
    assert delivered_result('Plain answer.') == GateResult('pass')
    assert delivered_result(None) == GateResult('pass')
    note = with_pending('x', GateResult('interrupt', ('person_only',), ('merge', 'migration')),
                        [{'action': 'merge', 'reason': 'merge'}])
    assert note.endswith('- merge: merge\n- migration')
    assert delivered_result(note).person_only_actions == ('merge', 'migration')


def test_person_only_is_a_copy():
    DEV.person_only.clear()
    assert DEV.person_only
