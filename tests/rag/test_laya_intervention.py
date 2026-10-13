import copy

import pytest

from rag_eval.laya import development as D, intervention as I
from rag_eval.laya.positive_diagnosis import slices
from rag_eval.laya import development_verify as V


def sample_rows():
    def row(task,y,source='core',lang='en_en'):
        return {'state':f'{source} {lang} {task} {y}', 'questions':{task:{'instructions':task}},
                'gold':{task:{'probabilities':{'false':float(1-y),'true':float(y)}}},
                'meta':{'pair_id':f'{source}:{lang}:{task}:{y}','source':source,'language':lang,'vinc_predicate_gold':False}}
    core=[row(t,y) for t in ('supports','contradicts','same_as') for y in (0,1)]
    native=[row(t,y,s,l) for s,l,ts in [('scifact','en_en',['aux_fact_supports','aux_fact_refutes']),
             ('vitaminc','en_en',['aux_fact_supports','aux_fact_refutes']),
             ('nli','ko_ko',['aux_entails','aux_nli_contradicts']),('sts','ko_ko',['aux_sts_ge3']),
             ('paws','en_en',['aux_paraphrase']),('paws','ko_ko',['aux_paraphrase'])] for t in ts for y in (0,1)]
    return core,native


@pytest.mark.parametrize('arm', ['core','mixed'])
def test_balancing_replays_without_counting_repetitions_as_new_items(arm):
    core,native=sample_rows()
    a,receipt=I.balanced_rows(core,native,arm,20261007)
    b,second=I.balanced_rows(core*2,native,arm,20261007)
    assert I.jsonl_bytes(a)==I.jsonl_bytes(b) and receipt==second
    assert len(a)==3012 and receipt['positive_items']==1506
    assert receipt['unique_items']==(6 if arm=='core' else 24)
    assert sum(s['draws'] for s in receipt['strata'] if s['stratum'][0]=='core')==(3012 if arm=='core' else 1506)
    for s in receipt['strata']:
        opposite=next(t for t in receipt['strata'] if t['stratum']==s['stratum'][:-1]+[1-s['stratum'][-1]])
        assert s['draws']==opposite['draws']


def test_native_labels_cannot_enter_as_core_predicates():
    core,native=sample_rows()
    native[0]['meta']['vinc_predicate_gold']=True
    with pytest.raises(ValueError,match='relabeling'):I.balanced_rows(core,native,'mixed',7)


def test_missing_native_class_or_core_positive_is_refused():
    core,native=sample_rows()
    with pytest.raises(ValueError,match='core task/class'):I.balanced_rows(core[1:],native,'core',7)
    with pytest.raises(ValueError,match='native task/source'):I.balanced_rows(core,native[1:],'mixed',7)


def test_f1_selection_preserves_precision_and_legacy_rule():
    def epoch(n,p,r,nll):
        return {'epoch':n,'development':{'supports':{'positive_precision':p,'positive_recall':r,'nll':nll}}}
    h=[epoch(1,.8,.1,.1),epoch(2,.6,.5,.2),epoch(3,.05,1.,.3)]
    assert I.select_checkpoint(h,I.SELECTION)['epoch']==2
    assert I.select_checkpoint(h,'core_supports_nll_then_positive_recall')['epoch']==1
    assert I.positive_f1({'positive_precision':None,'positive_recall':0.})==0.
    with pytest.raises(ValueError):I.select_checkpoint(h,'posthoc')


def test_new_registration_cannot_silently_use_old_selection_or_unbounded_runs():
    reg={'schema':I.SCHEMA,'base_sha256':D.BASE_SHA,'loss':'soft-ce','sealed_test':False,
         'epochs':2,'item_cap':3012,'scheduled_updates':96,'seed':20261007,'selection':I.SELECTION,
         'arm':'mixed','max_training_runs':2,'no_new_paid_resource':True,'development':[{'path':'development.jsonl'}]}
    D.validate_registration(reg)
    for key,value in [('selection','core_supports_nll_then_positive_recall'),('max_training_runs',3),('loss','rlcd'),('no_new_paid_resource',False)]:
        bad=copy.deepcopy(reg);bad[key]=value
        with pytest.raises(ValueError):D.validate_registration(bad)


def test_positive_loss_is_visible_despite_majority_accuracy():
    outputs=[{'meta':{'task':'supports','lang':'en','pair_id':'pos'},'logits':[5,-5],'target':[0.,1.]},
             {'meta':{'task':'supports','lang':'en','pair_id':'neg'},'logits':[5,-5],'target':[1.,0.]}]
    s=slices(outputs)['all']
    assert s['confusion']=={'tp':0,'fp':0,'fn':1,'tn':1}
    assert s['positive_nll']>9 and s['negative_nll']<.001 and s['false_negative_ids']==['pos']


def intervention_run(root):
    import json
    core,native=sample_rows()
    def record(path):return {'path':str(path),'sha256':D.digest(path.read_bytes())}
    for name,rows in [('core-input',core),('native-input',native),('development',core[:2])]:
        (root/f'{name}.jsonl').write_bytes(I.jsonl_bytes(rows))
    rows,exposure=I.balanced_rows(core,native,'mixed',20261007)
    (root/'training.jsonl').write_bytes(I.jsonl_bytes(rows))
    (root/'exposure.json').write_bytes(D.encoded(exposure))
    reg={'schema':I.SCHEMA,'base_sha256':D.BASE_SHA,'loss':'soft-ce','sealed_test':False,
         'epochs':2,'item_cap':3012,'scheduled_updates':96,'seed':20261007,'selection':I.SELECTION,
         'arm':'mixed','max_training_runs':2,'no_new_paid_resource':True,
         'development':[record(root/'development.jsonl')],'training':[record(root/'training.jsonl')],
         'exposure':record(root/'exposure.json'),
         'sampling_inputs':{'core':[record(root/'core-input.jsonl')],'native':[record(root/'native-input.jsonl')]}}
    registration=D.encoded(reg)
    (root/'registration.json').write_bytes(registration)
    dev=core[:2]
    outputs=[{'meta':dict(r['meta'],task='supports'),'logits':[4,-4] if i==0 else [-4,4],
              'target':[float(1-i),float(i)]} for i,r in enumerate(dev)]
    values=D.metrics([(0,r['logits'],r['target'],2) for r in outputs],[r['meta'] for r in outputs])
    data={'outputs':outputs,'metrics':values}
    (root/'original-development.json').write_bytes(D.encoded(data))
    history=[]
    for epoch in (1,2):
        folder=root/f'epoch-{epoch}';folder.mkdir()
        (folder/'development.json').write_bytes(D.encoded(data))
        (folder/'model.safetensors').write_bytes(b'fixture')
        history.append({'epoch':epoch,'development':values,'executed_updates_cumulative':epoch*48})
    (root/'history.json').write_bytes(D.encoded(history))
    summary={'registration_sha256':D.digest(registration),'selected_epoch':1,'selection':I.SELECTION,
             'loss':'soft-ce',
             'selected':values,'baseline':values,'executed_updates':96,'AMP_skips':0,'scheduled_updates':96,
             'model_sha256':D.digest(b'fixture'),'sealed_test_opened':False,'production_approved':False}
    (root/'summary.json').write_bytes(D.encoded(summary))
    return history


@pytest.mark.parametrize('mutation', ['sampling','history','identities'])
def test_intervention_verifier_binds_actual_data_and_history(tmp_path,mutation):
    import json
    history=intervention_run(tmp_path)
    assert V.verify(tmp_path)['verified']
    if mutation=='sampling':
        with (tmp_path/'training.jsonl').open('ab') as stream:stream.write(b'\n')
    elif mutation=='history':
        history[0]['development']['supports']['nll']+=.1
        (tmp_path/'history.json').write_bytes(D.encoded(history))
    else:
        path=tmp_path/'epoch-1/development.json'
        data=json.loads(path.read_bytes());data['outputs'][0]['meta']['pair_id']='another'
        path.write_bytes(D.encoded(data))
    with pytest.raises(ValueError):V.verify(tmp_path)
