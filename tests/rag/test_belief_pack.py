"""유료 전송 없이 승인 경계·재개·채점 분모를 검증한다."""
import copy
import json
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rag_eval.belief_pack import protocol as p
from rag_eval.belief_pack import run as r
from rag_eval.belief_pack import review as v

DATA = Path(__file__).resolve().parents[2] / 'rag_eval/evalsets/belief-pack-v1'


@pytest.fixture
def bundle(tmp_path):
    cases = p.load_cases(DATA / 'cases.json')
    settings = dict(endpoint='https://fiction.openai.azure.com', deployment='lab-mini',
                    model='gpt-4.1-mini', model_version='2025-04-14', api_version='2024-10-21',
                    seed=20261009, max_completion_tokens=512, temperature=0, timeout_seconds=60,
                    input_usd_per_million=.4, output_usd_per_million=1.6,
                    rate_source='fiction:rate', rate_checked_at='2026-10-09')
    plan = p.make_plan(cases, settings)
    now = datetime.now(timezone.utc)
    approval = dict(approved=True, plan_sha256=plan['plan_sha256'], approved_by='test-only',
                    decision_ref='fiction:approval', run_id='test', run_dir=str(tmp_path/'run'),
                    approved_at=(now-timedelta(minutes=1)).isoformat(),
                    expires_at=(now+timedelta(hours=1)).isoformat(), max_calls=40,max_usd=1,
                    second_vendor='deferred_single_vendor_pilot')
    return cases, plan, approval, tmp_path/'run'


def answer(settings, job):
    return dict(text='판단 근거가 없습니다.', model='gpt-4.1-mini-2025-04-14',
                usage=dict(prompt_tokens=100,completion_tokens=10,total_tokens=110),
                finish_reason='stop', refusal=None, response_id='fiction:response')


def test_paired_plan_never_sends_rubric(bundle):
    cases, plan, _, _ = bundle
    assert len(plan['requests']) == 40
    first = [j['arm'] for j in plan['requests'][::2]]
    assert first.count('with') == first.count('without') == 10
    for c in cases:
        a,b = (p.messages(c, arm) for arm in p.ARMS)
        assert a[0] == b[0]
        assert b[1]['content'].startswith(a[1]['content']+'\nBELIEFS')
        assert c['rubric']['boundary'] not in b[1]['content']
    assert p.make_plan(cases, plan['settings']) == plan


@pytest.mark.parametrize('mutation', ['approval','expired','cost','calls','vendor','directory'])
def test_refuses_before_transport(bundle, mutation):
    cases,plan,approval,out=bundle
    if mutation=='approval':approval['approved']=False
    if mutation=='expired':approval['expires_at']='2000-01-01T00:00:00+00:00'
    if mutation=='cost':approval['max_usd']=0
    if mutation=='calls':approval['max_calls']=41
    if mutation=='vendor':approval['second_vendor']='undecided'
    if mutation=='directory':approval['run_dir']=str(out/'other')
    with pytest.raises(ValueError):
        r.execute(plan,cases,approval,out,lambda *_: pytest.fail('paid call'))


@pytest.mark.parametrize('mutation',['source','case','request'])
def test_plan_drift_refused(bundle, mutation):
    cases,plan,approval,out=bundle
    if mutation=='source':plan['source_sha256']['run.py']='wrong'
    if mutation=='case':cases[0]['question']='changed'
    if mutation=='request':plan['requests'][0]['body']['temperature']=1
    with pytest.raises(ValueError,match='hash changed'):
        r.execute(plan,cases,approval,out,lambda *_:pytest.fail('paid call'))


def test_complete_resume_makes_no_calls(bundle):
    cases,plan,approval,out=bundle
    assert r.execute(plan,cases,approval,out,answer)['completed']==40
    assert r.execute(plan,cases,approval,out,lambda *_:pytest.fail('repeat'))['completed']==40


def test_failure_stops_and_never_retries(bundle):
    cases,plan,approval,out=bundle
    calls=[]
    def fail(*_):
        calls.append(1)
        raise TimeoutError('secret-must-not-be-logged')
    with pytest.raises(RuntimeError):r.execute(plan,cases,approval,out,fail)
    assert len(calls)==1
    assert 'secret-must-not-be-logged' not in (out/'journal.jsonl').read_text()
    with pytest.raises(ValueError,match='failed/unknown'):
        r.execute(plan,cases,approval,out,lambda *_:pytest.fail('retry'))


def test_inflight_and_partial_line_refused(bundle):
    cases,plan,approval,out=bundle
    out.mkdir()
    j=plan['requests'][0]
    row=dict(event='started',key=j['key'],plan_sha256=plan['plan_sha256'],
             approval_sha256=p.digest(approval),request_sha256=p.digest(j))
    r.append(out/'journal.jsonl',row)
    with pytest.raises(ValueError,match='in-flight'):
        r.execute(plan,cases,approval,out,lambda *_:pytest.fail('retry'))
    (out/'journal.jsonl').write_bytes(b'{')
    with pytest.raises(ValueError,match='partial journal'):
        r.execute(plan,cases,approval,out,lambda *_:pytest.fail('retry'))


def test_existing_lock_is_preserved(bundle):
    cases,plan,approval,out=bundle
    out.mkdir();(out/'run.lock').write_text('another-run')
    with pytest.raises(FileExistsError):r.execute(plan,cases,approval,out,answer)
    assert (out/'run.lock').read_text()=='another-run'


@pytest.mark.parametrize('field,value',[('finish_reason','length'),('model','changed'),('text',''),('refusal','refused')])
def test_bad_response_kept_but_stops(bundle,field,value):
    cases,plan,approval,out=bundle
    def invalid(s,j):
        data=answer(s,j);data[field]=value;return data
    with pytest.raises(RuntimeError):r.execute(plan,cases,approval,out,invalid)
    data=[json.loads(x) for x in (out/'journal.jsonl').read_text().splitlines()]
    assert len(data)==2 and data[-1]['result'][field]==value


def test_review_requires_complete_evidence_and_preserves_denominators(bundle,tmp_path):
    cases,plan,approval,out=bundle
    r.execute(plan,cases,approval,out,answer)
    v.export_review(plan,cases,approval,out/'journal.jsonl',tmp_path/'review')
    mapping=p.read(tmp_path/'review/mapping.private.json')
    grades=p.read(tmp_path/'review/review.json')
    assert all('arm' not in g and 'model' not in g for g in grades)
    with pytest.raises(ValueError):v.report(plan,cases,approval,out/'journal.jsonl',mapping,grades)
    for g in grades:
        g.update(reviewer='test fixture',reviewer_kind='ai',reviewed_at='2026-10-09',
                 sentences=[dict(text=g['answer'],overconfident=False,reason='test fixture')],
                 unknown_acknowledged=True,evidence_note='test fixture, not a measurement',control_correct=False)
    report=v.report(plan,cases,approval,out/'journal.jsonl',mapping,grades)
    assert report['summary']['with']['group_count']['n']==3
    assert report['summary']['without']['group_count']['n']==0
    assert report['summary']['with']['answerable_controls']==dict(correct=0,n=2,rate=0)
    assert report['summary']['without']['unknown_acknowledgment']['n']==20
    assert report['summary']['without']['unavailable_group_count_acknowledged']['correct']==3
    guessing=copy.deepcopy(grades)
    for g in guessing:
        g['reported_group_count']=9
    guessed=v.report(plan,cases,approval,out/'journal.jsonl',mapping,guessing)
    assert guessed['summary']['without']['unavailable_group_count_acknowledged']['correct']==0
    bad=copy.deepcopy(grades);bad[0]['sentences'][0]['text']='invented'
    with pytest.raises(ValueError,match='exact response'):
        v.report(plan,cases,approval,out/'journal.jsonl',mapping,bad)
    with pytest.raises(ValueError,match='unique grades'):
        v.report(plan,cases,approval,out/'journal.jsonl',mapping,grades[:-1]+[grades[0]])


def test_journal_tampering_and_duplicate_response_refused(bundle):
    cases,plan,approval,out=bundle
    r.execute(plan,cases,approval,out,answer)
    path=out/'journal.jsonl'
    lines=path.read_text().splitlines()
    row=json.loads(lines[1]);row['result']['text']='edited';lines[1]=json.dumps(row)
    path.write_text('\n'.join(lines)+'\n')
    with pytest.raises(ValueError,match='invalid completion'):r.completed(plan,approval,path)


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,True])
def test_invalid_rates_refused(bundle,value):
    cases,plan,_,_=bundle
    settings=dict(plan['settings'],input_usd_per_million=value)
    with pytest.raises(ValueError):p.make_plan(cases,settings)


def test_safe_prefix_resume_never_repeats_completed_calls(bundle,monkeypatch):
    cases,plan,approval,out=bundle
    original=r.check_approval
    checks=[]
    def stop_between_calls(*args):
        checks.append(1)
        if len(checks)==4:
            raise ValueError('stop before third request')
        return original(*args)
    monkeypatch.setattr(r,'check_approval',stop_between_calls)
    with pytest.raises(ValueError,match='before third'):
        r.execute(plan,cases,approval,out,answer)
    assert len(r.completed(plan,approval,out/'journal.jsonl'))==2
    monkeypatch.setattr(r,'check_approval',original)
    calls=[]
    def remaining(s,j):
        calls.append(j['key']);return answer(s,j)
    assert r.execute(plan,cases,approval,out,remaining)['completed']==40
    assert calls==[j['key'] for j in plan['requests'][2:]]


def test_wire_request_is_exact_and_http_error_not_retried(bundle,monkeypatch):
    _,plan,_,_=bundle
    job=plan['requests'][0]
    calls=[]
    class Opener:
        def open(self,request,timeout):
            calls.append((request,timeout))
            payload=dict(id='fiction:wire',model='gpt-4.1-mini-2025-04-14',
                         usage=dict(prompt_tokens=100,completion_tokens=10,total_tokens=110),
                         choices=[dict(finish_reason='stop',message=dict(content='응답'))])
            return io.StringIO(json.dumps(payload))
    monkeypatch.setenv('AZURE_OPENAI_API_KEY','test-key')
    monkeypatch.setattr(r,'build_opener',lambda cls:Opener())
    result=r.azure_call(plan['settings'],job)
    req,timeout=calls[0]
    assert req.full_url=='https://fiction.openai.azure.com/openai/deployments/lab-mini/chat/completions?api-version=2024-10-21'
    assert req.method=='POST' and req.get_header('Api-key')=='test-key'
    assert json.loads(req.data)==job['body'] and b'test-key' not in req.data
    assert timeout==60 and result['text']=='응답'
    class Broken:
        def open(self,*args,**kwargs):
            calls.append(1);raise TimeoutError('no retry')
    monkeypatch.setattr(r,'build_opener',lambda cls:Broken())
    with pytest.raises(TimeoutError):r.azure_call(plan['settings'],job)
    assert len(calls)==2
    with pytest.raises(ValueError,match='redirect'):
        r.NoRedirect().redirect_request(req,None,302,'',{},'https://elsewhere.example')


@pytest.mark.parametrize('field',['prompt_tokens','completion_tokens'])
def test_usage_over_reservation_stops_after_first_call(bundle,field):
    cases,plan,approval,out=bundle
    calls=[]
    def excessive(s,j):
        calls.append(1);data=answer(s,j)
        data['usage'][field]=j['input_token_allowance']+s['max_completion_tokens']+1
        data['usage']['total_tokens']=data['usage']['prompt_tokens']+data['usage']['completion_tokens']
        return data
    with pytest.raises(RuntimeError):r.execute(plan,cases,approval,out,excessive)
    assert len(calls)==1


def test_source_hash_survives_checkout_newline_conversion(monkeypatch):
    original=Path.read_bytes
    before=p.source_hashes()
    monkeypatch.setattr(Path,'read_bytes',lambda path:original(path).replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'))
    assert p.source_hashes()==before


def test_committed_bundle_matches_source_and_is_not_approved():
    cases=p.load_cases(DATA/'cases.json')
    plan=p.read(DATA/'plan.json')
    p.verify_plan(plan,cases)
    assert plan['settings']==p.read(DATA/'settings.json')
    manifest=p.read(DATA/'manifest.json')
    for name,expected in manifest['artifacts'].items():
        assert p.digest(p.read(DATA/name))==expected
    approval=p.read(DATA/'approval.template.json')
    assert approval['approved'] is False and approval['plan_sha256']==plan['plan_sha256']
    assert plan['reserved_usd'] <= approval['max_usd']
