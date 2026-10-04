import json
from pathlib import Path

import pytest

from vinc_client import (ApprovalRequest, OutputGate, OutputGateStopped, RoleOutput,
                         prepare_person_only, resolve_person_only)

PROFILES = {p['id']: p for p in json.loads((Path(__file__).parents[3] /
    'fixtures/output_gate/proposed-role-checks.json').read_text(encoding='utf-8'))['profiles']}
ROLE = 'concept:agent-reviewer-judgement'


def prepare(value, role=ROLE):
    return prepare_person_only(value, OutputGate(PROFILES[role]), role_id=role, run_id='recorded-1')


def test_structured_request_without_any_hint_requires_a_bound_human_reply():
    draft = {'text':'Recommendation ready.', 'person_only_requests':[
        {'action':'merge', 'reason':'Please decide.'}]}
    pending = prepare(draft)
    assert isinstance(pending, ApprovalRequest) and pending.actions == ['merge']
    reply = {'request_id':pending.request_id, 'approved':True}
    result = resolve_person_only(pending, reply)
    assert result.status == 'approved' and result.output.model_dump() == draft
    assert draft == pending.output.model_dump()


def test_gate_detected_action_also_requests_approval_without_a_structured_hint():
    assert prepare({'text':'Merge the PR.'}).actions == ['merge']


def test_pass_and_rejection_are_distinct_and_rejection_has_no_deliverable():
    assert prepare({'text':'The review is ready.'}).status == 'pass'
    pending = prepare({'text':'Merge the PR.'})
    rejected = resolve_person_only(pending, {'request_id':pending.request_id,'approved':False})
    assert rejected.status == 'rejected' and rejected.output is None


@pytest.mark.parametrize('approved', ['false', 'true', 1, 0, None])
def test_truthy_non_boolean_values_cannot_approve(approved):
    pending = prepare({'text':'Merge the PR.'})
    with pytest.raises(ValueError, match='Invalid approval response'):
        resolve_person_only(pending, {'request_id':pending.request_id, 'approved':approved})


def test_other_draft_run_and_mutated_request_cannot_reuse_approval():
    pending = prepare({'text':'Merge the PR.'})
    other = prepare_person_only({'text':'Merge the PR.'}, OutputGate(PROFILES[ROLE]),
                                role_id=ROLE, run_id='recorded-2')
    with pytest.raises(ValueError, match='does not match'):
        resolve_person_only(pending, {'request_id':other.request_id,'approved':True})
    pending.actions.append('publish')
    with pytest.raises(ValueError, match='does not match'):
        resolve_person_only(pending, {'request_id':pending.request_id,'approved':True})


@pytest.mark.parametrize('value', [
    {'text':"I've merged the PR."},
    {'text':'Okay','person_only_requests':[{'action':'publish','reason':'Please decide.'}]},
    {'text':'Okay','approved':True},
    RoleOutput.model_construct(text=1, person_only_requests=[]),
])
def test_blocked_or_malformed_output_cannot_be_sent_to_human_as_approvable(value):
    with pytest.raises(OutputGateStopped) as exc:
        prepare(value)
    assert exc.value.result.verdict == 'block'
