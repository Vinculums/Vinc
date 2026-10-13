import copy
import json

import pytest

from rag_eval.laya import public_auxiliary as A
from rag_eval.laya import public_sources as S


def row(pid, role, a, b, family):
    return A.pair('fixture', pid, role, 'fact', 'SUPPORTS', a, b, 'en_en', [family],
                  'fictional_test', {'file': 'fixture'})


def test_nli_entailment_keeps_native_question_and_neutral_unknown():
    assert A.native_targets('nli', 'neutral') == {'aux_entails': None, 'aux_nli_contradicts': None}
    r = row('a', 'train', 'a', 'b', 'f:a')
    r.update(task='nli', native_label='entailment', targets=A.native_targets('nli', 'entailment'))
    r['independence_group'] = 'component:fixture'
    exported = A.training_row(r)
    assert set(exported['questions']) == {'aux_entails', 'aux_nli_contradicts'}
    assert 'supports' not in exported['gold']
    assert exported['gold']['aux_entails']['probabilities']['true'] == 1
    assert not exported['meta']['vinc_predicate_gold']


def test_unknown_cannot_become_false_or_hard_target():
    r = row('a', 'train', 'a', 'b', 'f:a')
    r['targets'] = {'aux_fact_supports': None, 'aux_fact_refutes': None}
    with pytest.raises(ValueError, match='known targets'):
        A.training_row(r)


@pytest.mark.parametrize('task,label', [('nli','maybe'),('fact','UNKNOWN'),('sts',True),('paraphrase',2)])
def test_invalid_native_labels_refused(task, label):
    with pytest.raises(ValueError):
        A.native_targets(task, label)


def test_transitive_train_chain_connected_to_dev_is_removed():
    rows = [row('t1','train','x','bridge','f:train'), row('t2','train','bridge','y','f:second'),
            row('d','dev','y','held','f:dev'), row('safe','train','safe','other','f:safe')]
    selected, excluded = A.separate(rows)
    assert {r['upstream_id'] for r in selected} == {'safe', 'd'}
    assert excluded['train_reserved_for_dev_component'] == 2
    A.validate_exports([r for r in selected if r['role']=='train'], [r for r in selected if r['role']=='dev'])


def test_translation_family_cannot_cross_roles_with_different_words():
    selected, _ = A.separate([row('t','train','English','claim','translation:one'),
                             row('d','dev','한국어','주장','translation:one')])
    assert len(selected) == 1 and selected[0]['role'] == 'dev'


def test_normalized_endpoint_and_native_target_mutations_refused():
    selected, _ = A.separate([row('t','train','A','B','f:train'),row('d','dev','C','D','f:dev')])
    t, d = selected
    assert A.validate_exports([t],[d])['cross_role_identity_keys'] == 0
    bad = copy.deepcopy(d)
    bad['a'] = '  a  '
    with pytest.raises(ValueError, match='crosses roles'):
        A.validate_exports([t],[bad])
    bad = copy.deepcopy(d)
    bad['targets']['aux_fact_supports'] = 0
    with pytest.raises(ValueError, match='authority changed'):
        A.validate_exports([t],[bad])


def test_replay_selection_is_input_order_independent():
    rows = [row(str(i),'train','A'+str(i),'B'+str(i),'f:'+str(i)) for i in range(40)]
    assert A.select(rows, 7) == A.select(list(reversed(rows)), 7)


@pytest.mark.parametrize('name', ['../outside','/absolute','nested/../../outside','a\\b'])
def test_source_paths_refuse_escape(tmp_path, name):
    with pytest.raises(ValueError):
        S.safe_path(tmp_path, name)


def test_source_freeze_rejects_mutation_and_changed_authority(tmp_path):
    rows = [{'source':'one','name':'one/'+k+'.txt','url':'https://example.com/'+k,
             'kind':k,'revision':'fixed','license':'fixture','max_bytes':100} for k in ['rights','readme']]
    plan = {'schema':'vinc.laya.public-source-plan.v1','scope':'local-auxiliary-development',
            'authority':'decision:fixture','production_weights_approved':False,'files':rows}
    raw = S.encoded(plan)
    (tmp_path/'source-plan.json').write_bytes(raw)
    (tmp_path/'one').mkdir()
    for r in rows:
        (tmp_path/r['name']).write_bytes(b'fixed')
    receipts = [dict(r,sha256=S.digest(b'fixed'),bytes=5) for r in rows]
    freeze = {'schema':'vinc.laya.public-source-freeze.v1','plan_sha256':S.digest(raw),
              'authority':plan['authority'],'scope':plan['scope'],'production_weights_approved':False,'files':receipts}
    (tmp_path/'source-freeze.json').write_bytes(S.encoded(freeze))
    assert S.verify(tmp_path)['verified']
    (tmp_path/'one/rights.txt').write_bytes(b'changed')
    with pytest.raises(ValueError, match='bytes changed'):
        S.verify(tmp_path)
    (tmp_path/'one/rights.txt').write_bytes(b'fixed')
    freeze['production_weights_approved'] = True
    (tmp_path/'source-freeze.json').write_bytes(S.encoded(freeze))
    with pytest.raises(ValueError, match='authority mismatch'):
        S.verify(tmp_path)
