import copy

import pytest

from rag_eval.laya import evidence_expansion as E, public_auxiliary as A, intervention as I, development as D


def fact(pid,label,source='vitaminc',group=None):
    r=A.pair(source,pid,'train','fact',label,'Observed result '+pid,'Claim '+pid,'en_en',
             ['family:'+pid],'fictional_test',{'file':'fixture','id':pid})
    return dict(r,role='train',independence_group=group or 'component:'+pid)


def test_fact_expansion_retains_parent_and_prefers_new_source_components():
    parent=[fact('old','SUPPORTS')]
    rows=parent+[fact('same','SUPPORTS',group='component:old'),fact('new','SUPPORTS'),fact('neg','REFUTES')]
    out=E.expand(rows,parent,2)
    assert {r['upstream_id'] for r in out}=={'old','new','neg'}
    assert E.expand(list(reversed(rows)),parent,2)==out
    assert {r['upstream_id'] for r in E.expand(rows,parent,1)}=={'old','neg'}


def test_source_native_support_review_does_not_create_vinc_labels():
    rows=[fact('positive','SUPPORTS'),fact('negative','REFUTES')]
    c=E.candidates(rows)
    assert len(c)==1 and c[0]['source_native_label']=='SUPPORTS'
    assert c[0]['predicate_labels']=={'supports':None,'contradicts':None,'same_as':None}
    assert c[0]['annotation_events']==[] and c[0]['vinc_predicate_gold'] is False


def test_review_queue_prioritizes_new_components_without_using_flag_as_label():
    parent=[fact('old','SUPPORTS')]
    rows=parent+[fact('related','SUPPORTS',group='component:old'),fact('new','SUPPORTS')]
    review=E.candidates(rows)
    assert E.review_queue(review,parent)[0]=='vitaminc:new'
    assert all(r['predicate_labels']['supports'] is None for r in review)


def test_parent_tampering_and_silent_discard_refused():
    parent=[fact('old','SUPPORTS'),fact('other','SUPPORTS')]
    with pytest.raises(ValueError,match='discard'):E.expand(parent,parent,1)
    altered=copy.deepcopy(parent);altered[0]['a']='changed'
    with pytest.raises(ValueError,match='ineligible'):E.expand(parent,altered,5)


def native_and_core():
    def one(task,y,source='core',language='en_en'):
        return {'state':f'{task} {source} {language} {y}', 'questions':{task:{'instructions':task}},
                'gold':{task:{'probabilities':{'false':float(1-y),'true':float(y)}}},
                'meta':{'pair_id':f'{task}:{source}:{language}:{y}','source':source,'language':language,'vinc_predicate_gold':False}}
    core=[one(t,y) for t in ('supports','contradicts','same_as') for y in (0,1)]
    native=[one(t,y,s,l) for s,l,tasks in [('scifact','en_en',['aux_fact_supports','aux_fact_refutes']),
           ('vitaminc','en_en',['aux_fact_supports','aux_fact_refutes']),('nli','ko_ko',['aux_entails','aux_nli_contradicts']),
           ('sts','ko_ko',['aux_sts_ge3']),('paws','en_en',['aux_paraphrase']),('paws','ko_ko',['aux_paraphrase'])]
           for t in tasks for y in (0,1)]
    return core,native


def test_smaller_mixture_core_and_nonfact_draws_match_across_source_pool_change():
    core,native=native_and_core()
    enlarged=native+[{**r,'state':r['state']+' new','meta':{**r['meta'],'pair_id':r['meta']['pair_id']+':new'}}
                     for r in native if r['meta']['source'] in ('scifact','vitaminc')]
    a,ra=I.balanced_rows(core,native,'low-original',7)
    b,rb=I.balanced_rows(core,enlarged,'low-expanded',7)
    for rows,receipt in [(a,ra),(b,rb)]:
        assert len(rows)==3012 and receipt['positive_items']==1506
        assert sum(r['meta']['sampling_tier']=='core' for r in rows)==2400
        assert sum(r['meta']['sampling_tier']=='native' for r in rows)==612
    def constant(rows):return [r for r in rows if not r['meta']['task'].startswith('aux_fact_')]
    assert I.jsonl_bytes(constant(a))==I.jsonl_bytes(constant(b))
    assert I.jsonl_bytes(a)!=I.jsonl_bytes(b)


def test_lower_mixture_registration_refuses_moved_quota_or_old_arm():
    r={'schema':I.LOW_SCHEMA,'base_sha256':D.BASE_SHA,'loss':'soft-ce','sealed_test':False,'epochs':2,
       'item_cap':3012,'scheduled_updates':96,'seed':7,'selection':I.SELECTION,'arm':'low-original',
       'max_training_runs':2,'no_new_paid_resource':True,'core_items':2400,'auxiliary_items':612,
       'development':[{'path':'development.jsonl'}]}
    D.validate_registration(r)
    for k,v in [('core_items',2394),('auxiliary_items',600),('arm','mixed')]:
        bad=dict(r,**{k:v})
        with pytest.raises(ValueError):D.validate_registration(bad)
