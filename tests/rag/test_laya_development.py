import copy
import math

import pytest

from rag_eval.laya import development as D
from rag_eval.laya import development_verify as V


def registration():
    return {'schema':'vinc.laya.local-development.v1','base_sha256':D.BASE_SHA,'loss':'rlcd',
            'sealed_test':False,'epochs':2,'item_cap':3012,'scheduled_updates':96,'seed':20261007,
            'selection':'core_supports_nll_then_positive_recall','development':[{'path':'development.jsonl'}]}


@pytest.mark.parametrize('field,value', [('base_sha256','retired'),('sealed_test',True),('scheduled_updates',97),('seed','random'),('loss','another')])
def test_registration_refuses_changed_base_scope_or_budget(field,value):
    r=registration()
    D.validate_registration(r)
    r[field]=value
    with pytest.raises(ValueError):D.validate_registration(r)


def test_final_and_calibration_files_are_not_development():
    for name in ['eval.jsonl','calib.jsonl']:
        r=registration();r['development'][0]['path']=name
        with pytest.raises(ValueError,match='not development'):D.validate_registration(r)


def test_metric_true_class_and_positive_utility_are_separate():
    r=D.metrics([(0,[-4,4],[0,1],2),(0,[4,-4],[1,0],2)], [{'task':'supports'}]*2)['supports']
    assert r['n']==2 and r['positives']==1
    assert r['positive_recall']==r['positive_precision']==r['accuracy']==1
    assert r['auroc']==1 and r['nll']<.001
    r=D.metrics([(0,[4,-4],[0,1],2),(0,[4,-4],[1,0],2)], [{'task':'supports'}]*2)['supports']
    assert r['accuracy']==.5 and r['positive_recall']==0 and r['positive_precision'] is None


def test_registered_input_hash_checked_before_read(tmp_path):
    p=tmp_path/'development.jsonl';p.write_text('{"state":"fixture"}\n')
    record={'path':str(p),'sha256':D.digest(p.read_bytes())}
    assert D.checked_rows([record])==[{'state':'fixture'}]
    p.write_text('{"state":"changed"}\n')
    with pytest.raises(ValueError,match='bytes changed'):D.checked_rows([record])


def saved_run(tmp_path):
    import json
    outputs=[{'meta':{'task':'supports'},'logits':[-4,4],'target':[0.,1.]},
             {'meta':{'task':'supports'},'logits':[4,-4],'target':[1.,0.]}]
    records=[(0,r['logits'],r['target'],2) for r in outputs]
    values=D.metrics(records,[r['meta'] for r in outputs])
    data={'outputs':outputs,'metrics':values}
    (tmp_path/'epoch-1').mkdir()
    for name in ['original-development.json','epoch-1/development.json']:
        (tmp_path/name).write_bytes(D.encoded(data))
    (tmp_path/'epoch-1/model.safetensors').write_bytes(b'fictional checkpoint')
    summary={'selected_epoch':1,'selected':values,'executed_updates':94,'AMP_skips':2,'scheduled_updates':96,
             'model_sha256':D.digest(b'fictional checkpoint'),'sealed_test_opened':False,'production_approved':False}
    (tmp_path/'summary.json').write_bytes(D.encoded(summary))
    (tmp_path/'history.json').write_bytes(D.encoded([{'epoch':1,'development':values}]))
    return data


def test_saved_logit_verifier_refuses_wrong_metric(tmp_path):
    data=saved_run(tmp_path)
    assert V.verify(tmp_path)['verified']
    data['metrics']['supports']['positive_recall']=0
    (tmp_path/'original-development.json').write_bytes(D.encoded(data))
    with pytest.raises(ValueError,match='differs from logits'):V.verify(tmp_path)


def test_saved_logit_verifier_refuses_checkpoint_mutation(tmp_path):
    saved_run(tmp_path)
    (tmp_path/'epoch-1/model.safetensors').write_bytes(b'changed')
    with pytest.raises(ValueError,match='checkpoint bytes'):V.verify(tmp_path)
