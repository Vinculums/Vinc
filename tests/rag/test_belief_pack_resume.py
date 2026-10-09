"""별도 승인·원본 보존·미완료 요청 선택과 오프라인 투영을 검증한다."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

import pytest

from rag_eval.belief_pack import protocol as p, run as r, review as v
from rag_eval import belief_pack_resume as c

DATA = Path(__file__).resolve().parents[2]/'rag_eval/evalsets/belief-pack-v1'


def answer(s,j):
    return dict(text='기억 근거를 확인할 수 없습니다.',model='gpt-4.1-mini-2025-04-14',
                usage=dict(prompt_tokens=100,completion_tokens=10,total_tokens=110),
                finish_reason='stop',refusal=None,response_id='fixture:'+j['key'])


@pytest.fixture
def bundle(tmp_path):
    parent=p.read(DATA/'plan.json');cases=p.load_cases(DATA/'cases.json')
    now=datetime.now(timezone.utc)
    pa=dict(approved=True,plan_sha256=parent['plan_sha256'],approved_by='test only',
            decision_ref='fiction:test',run_id='parent',run_dir=str(tmp_path/'parent'),
            approved_at=(now-timedelta(minutes=1)).isoformat(),expires_at=(now+timedelta(hours=1)).isoformat(),
            max_calls=40,max_usd=.10,second_vendor='deferred_single_vendor_pilot')
    count=[]
    def fail16(s,j):
        count.append(1)
        if len(count)==16:raise HTTPError('https://fiction.example',429,'test secret',{},None)
        return answer(s,j)
    with pytest.raises(RuntimeError):r.execute(parent,cases,pa,pa['run_dir'],fail16)
    journal=Path(pa['run_dir'])/'journal.jsonl'
    plan=c.make_plan(parent,cases,pa,journal)
    a=dict(pa,plan_sha256=plan['plan_sha256'],run_id='continuation',run_dir=str(tmp_path/'continuation'),
           max_calls=25,retry_failed_key=plan['retry_failed_key'],total_attempt_cap=41)
    return plan,parent,cases,pa,journal,a,Path(a['run_dir'])


def test_remaining_requests_and_budget_are_bound(bundle):
    plan,parent,cases,pa,journal,_,_=bundle
    assert plan['requests']==parent['requests'][15:]
    assert plan['call_count']==25 and plan['preserved']==15 and plan['total_attempt_cap']==41
    assert plan['reserved_usd'] > plan['continuation_reservation_usd']
    before=journal.read_bytes()
    c.verify(plan,parent,cases,pa,journal)
    assert journal.read_bytes()==before


@pytest.mark.parametrize('change',['approved','retry','cap','parent'])
def test_refusal_precedes_calls_and_waits(bundle,change):
    plan,parent,cases,pa,journal,a,out=bundle
    if change=='approved':a['approved']=False
    if change=='retry':a['retry_failed_key']='other'
    if change=='cap':a['total_attempt_cap']=40
    if change=='parent':plan['parent_journal_sha256']='wrong'
    with pytest.raises(ValueError):
        c.execute(plan,parent,cases,pa,journal,a,out,
                  lambda *_:pytest.fail('call'),lambda *_:pytest.fail('wait'))


def test_completion_is_paced_and_preserved_answers_never_repeat(bundle,tmp_path):
    plan,parent,cases,pa,journal,a,out=bundle
    calls=[];waits=[];before=journal.read_bytes()
    def tracked(s,j):calls.append(j['key']);return answer(s,j)
    assert c.execute(plan,parent,cases,pa,journal,a,out,tracked,waits.append)==dict(completed=25,preserved=15,total=40)
    assert calls==[j['key'] for j in parent['requests'][15:]]
    assert waits==[60]+[30]*24 and journal.read_bytes()==before
    c.execute(plan,parent,cases,pa,journal,a,out,lambda *_:pytest.fail('repeat'),lambda *_:pytest.fail('wait'))
    result=c.review(plan,parent,cases,pa,journal,a,out/'journal.jsonl',tmp_path/'review')
    assert result['review_rows']==40
    context=p.read(tmp_path/'review/review-context.json')
    assert context['approved'] is False
    with pytest.raises(ValueError):r.check_approval(parent,context)
    projection=tmp_path/'review/review-projection.jsonl'
    combined=r.completed(parent,context,projection)
    assert len(combined)==40
    assert all(combined[j['key']]==answer(parent['settings'],j) for j in parent['requests'])
    grades=p.read(tmp_path/'review/labels/review.json')
    for g in grades:
        g.update(reviewer='test',reviewer_kind='ai',reviewed_at='2026-10-09',
                 sentences=[dict(text=g['answer'],overconfident=False,reason='fixture')],
                 unknown_acknowledged=True,evidence_note='fixture only',control_correct=False)
    report=v.report(parent,cases,context,projection,p.read(tmp_path/'review/labels/mapping.private.json'),grades)
    assert report['summary']['with']['responses']==report['summary']['without']['responses']==20


def test_second_http_failure_stops_and_preserves_status(bundle):
    plan,parent,cases,pa,journal,a,out=bundle
    calls=[]
    def fail(*_):
        calls.append(1);raise HTTPError('https://fiction.example',429,'secret',{},None)
    with pytest.raises(RuntimeError):c.execute(plan,parent,cases,pa,journal,a,out,fail,lambda _:None)
    raw=(out/'journal.jsonl').read_text()
    assert len(calls)==1 and '"http_status":429' in raw and 'secret' not in raw
    with pytest.raises(ValueError):
        c.execute(plan,parent,cases,pa,journal,a,out,lambda *_:pytest.fail('retry'),lambda _:None)


def test_incomplete_continuation_cannot_be_reviewed(bundle,tmp_path):
    plan,parent,cases,pa,journal,a,out=bundle
    out.mkdir()
    with pytest.raises(ValueError,match='all remaining'):
        c.review(plan,parent,cases,pa,journal,a,out/'journal.jsonl',tmp_path/'review')


def test_unknown_parent_outcome_refuses_recovery(bundle):
    plan,parent,cases,pa,journal,a,out=bundle
    journal.write_bytes(journal.read_bytes().replace(b'HTTPError',b'TimeoutError'))
    with pytest.raises(ValueError,match='HTTP failure'):
        c.make_plan(parent,cases,pa,journal)


def test_saved_attempt_and_continuation_plan_remain_frozen():
    root=DATA/'run-2026-10-09-pilot1'
    c.verify(p.read(root/'continuation.plan.json'),p.read(DATA/'plan.json'),
             p.load_cases(DATA/'cases.json'),p.read(root/'approval.json'),root/'journal.jsonl')
    for name,sha in p.read(root/'manifest.json')['sha256'].items():
        assert c.file_hash(root/name)==sha
    assert p.read(root/'continuation.approval.template.json')['approved'] is False
