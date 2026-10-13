import copy
import math

import pytest

from rag_eval.laya import development as D


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
