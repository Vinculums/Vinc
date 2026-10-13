"""가상 AI 판정으로 동결·재구성·권한 경계를 검사한다. 실제 판정으로 집계하지 않는다."""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from rag_eval.laya import ai_candidates as A, data_contracts as D, candidate_revision as R


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    prepared, source = tmp_path/'prepared', tmp_path/'sources'
    prepared.mkdir()
    R.write(prepared,'manifest.json',{'fixture':True})
    cases = []
    sessions = {}
    for cohort in A.G.COHORTS:
        sessions[cohort] = D.fingerprint({'fixture':cohort})
        cases.append({'pair_id':'pair:'+cohort,'pair_sha256':D.fingerprint(cohort),'evidence_sha256':D.fingerprint([cohort]),
                      'session_sha256':sessions[cohort],'rubric_id':'rubric:fixture','cohort':cohort,
                      'cohort_role':'dev' if cohort=='development' else 'train-candidate',
                      'family_id':'family:'+cohort,'language_cell':'en_en','a':'Fictional A','b':'Fictional B','evidence':[]})
    monkeypatch.setattr(A,'candidates',lambda *_:copy.deepcopy(cases))
    policy={'tier':A.TIER,'decision_ref':'decision:fixture','owner_delegation':True,'training_authorized':False,
            'human_review_claim':False,'independent_review_claim':False,'teacher_api_authorized':False,
            'source_permission_promotion':False,'model_admission_authorized':False,'allowed_actors':['fixture:ai'],
            'valid_from':'2026-01-01T00:00:00Z','cohort_counts':dict.fromkeys(A.G.COHORTS,1),
            'prepared_manifest_sha256':D.fingerprint({'fixture':True}),'session_sha256':sessions}
    policy_path=tmp_path/'policy.json'; policy_path.write_bytes(D.encoded(policy))
    authors,reviews=[],[]
    start=datetime.now(timezone.utc)-timedelta(seconds=3)
    for case in cases:
        row={k:case[k] for k in ('pair_id','pair_sha256','evidence_sha256','session_sha256','rubric_id','cohort')}
        row.update(schema=A.EVENT,actor='fixture:ai',actor_kind='ai',model_family='GPT-6',exact_served_model=None,
                   runtime='Codex API',stage='author',at=start.isoformat(),labels=dict.fromkeys(A.PREDICATES,1),
                   rationale=dict.fromkeys(A.PREDICATES,'Fictional assertion for validation only.'),evidence_strength='direct',
                   reviews=[],independent_review=False)
        row['event_id']='annotation:'+D.fingerprint(row)
        authors.append(row)
        review=copy.deepcopy(row);review.pop('event_id');review.update(stage='self_review',at=(start+timedelta(seconds=1)).isoformat(),reviews=[row['event_id']])
        if case['cohort']=='native': review['labels']['supports']=None;review['evidence_strength']='ambiguous'
        review['event_id']='annotation:'+D.fingerprint(review)
        reviews.append(review)
    author=tmp_path/'author.jsonl'; review=tmp_path/'review.jsonl'
    write_rows(author,authors);write_rows(review,reviews)
    policy.update(author_sha256=D.digest(author.read_bytes()),self_review_sha256=D.digest(review.read_bytes()))
    policy_path.write_bytes(D.encoded(policy))
    return prepared,source,policy_path,author,review,authors,reviews


def write_rows(path,rows):
    path.write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows),encoding='utf-8')


def rehash(row):
    row['event_id']='annotation:'+D.fingerprint({k:v for k,v in row.items() if k!='event_id'})


def test_complete_freeze_preserves_ai_tier_unknown_and_source_roles(inputs,tmp_path):
    prepared,source,policy,author,review,*_=inputs
    result=A.freeze(prepared,author,review,tmp_path/'out',source,policy)
    assert result['ai_author_events']==result['same_agent_review_events']==3
    assert result['human_annotation_events']==result['independent_review_events']==result['training_exportable_pairs']==0
    assert result['changed_label_cases']==1
    assert result['cohorts']['native']['hard_target_candidates']==0
    assert A.verify(prepared,tmp_path/'out',source,policy)['verified']
    native=A.read_lines(tmp_path/'out/native.jsonl')[0]
    assert native['labels']['supports'] is None and not native['hard_target_candidate']
    assert not native['training_authorized'] and native['cohort_role']=='train-candidate'
    assert A.read_lines(tmp_path/'out/development.jsonl')[0]['cohort_role']=='dev'


@pytest.mark.parametrize('mutation',['human','independent','alias','missing','duplicate','bool','rationale','evidence','session','cohort','reference','time','runtime','hash'])
def test_bad_ai_annotations_refused_before_output(inputs,tmp_path,mutation):
    prepared,source,policy,author,review,authors,reviews=inputs
    row=reviews[0]
    if mutation=='human': row['actor_kind']='human'
    if mutation=='independent': row['independent_review']=True
    if mutation=='alias': row['actor']='fixture:reviewer'
    if mutation=='missing': reviews.pop()
    if mutation=='duplicate': reviews[1]=copy.deepcopy(row)
    if mutation=='bool': row['labels']['supports']=True
    if mutation=='rationale': row['rationale']['supports']=''
    if mutation=='evidence': row['evidence_sha256']='0'*64
    if mutation=='session': row['session_sha256']='0'*64
    if mutation=='cohort': row['cohort']='native'
    if mutation=='reference': row['reviews']=['annotation:fake']
    if mutation=='time': row['at']=authors[0]['at']
    if mutation=='runtime': row['exact_served_model']='invented-model'
    if mutation!='hash': rehash(row)
    else: row['labels']['supports']=0
    write_rows(review,reviews)
    declared=json.loads(policy.read_bytes());declared['self_review_sha256']=D.digest(review.read_bytes())
    policy.write_bytes(D.encoded(declared))
    with pytest.raises(ValueError): A.freeze(prepared,author,review,tmp_path/'bad',source,policy)
    assert not(tmp_path/'bad').exists()


@pytest.mark.parametrize('field',['training_authorized','human_review_claim','independent_review_claim','teacher_api_authorized','source_permission_promotion','model_admission_authorized'])
def test_delegation_does_not_promote_other_authority(inputs,tmp_path,field):
    prepared,source,policy,author,review,*_=inputs
    data=json.loads(policy.read_bytes());data[field]=True;policy.write_bytes(D.encoded(data))
    with pytest.raises(ValueError,match='authority changed'):
        A.freeze(prepared,author,review,tmp_path/'bad',source,policy)


def test_rehashed_candidate_freeze_cannot_change_labels_or_cohort(inputs,tmp_path):
    prepared,source,policy,author,review,*_=inputs
    out=tmp_path/'saved';A.freeze(prepared,author,review,out,source,policy)
    path=out/'development.jsonl';rows=A.read_lines(path);rows[0]['labels']['supports']=0;write_rows(path,rows)
    manifest=json.loads((out/'manifest.json').read_bytes());manifest['files']=R.inventory(out)
    (out/'manifest.json').write_bytes(D.encoded(manifest))
    with pytest.raises(ValueError,match='event replay'): A.verify(prepared,out,source,policy)


def test_rehashed_raw_events_cannot_replace_registered_inputs(inputs,tmp_path):
    prepared,source,policy,author,review,*_=inputs
    out=tmp_path/'pinned';A.freeze(prepared,author,review,out,source,policy)
    changed=A.read_lines(out/'self-review.jsonl');changed[0]['labels']['supports']=0;rehash(changed[0]);write_rows(out/'self-review.jsonl',changed)
    receipt=json.loads((out/'manifest.json').read_bytes());receipt['files']=R.inventory(out)
    (out/'manifest.json').write_bytes(D.encoded(receipt))
    with pytest.raises(ValueError,match='registered AI annotation inputs'): A.verify(prepared,out,source,policy)


def test_new_freeze_cannot_add_files_inside_a_previous_version(inputs,tmp_path):
    prepared,source,policy,author,review,*_=inputs
    out=tmp_path/'frozen';A.freeze(prepared,author,review,out,source,policy)
    before=R.inventory(out)
    with pytest.raises(ValueError,match='previous AI freeze'):
        A.freeze(prepared,out/'author.jsonl',out/'self-review.jsonl',out/'child',source,policy)
    assert R.inventory(out)==before
