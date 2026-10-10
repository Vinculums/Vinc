from pathlib import Path
import pytest
from rag_eval.laya.build_train import pair_builder, rows_for_pair, parser
from rag_eval.laya.finetune import freeze_embeddings, parser as ft_parser
from rag_eval.laya.pins import render_state


def test_pair_targets_and_direction():
    rows = rows_for_pair('A claim', 'B claim', None, {'supports':1,'contradicts':0,'same_as':1}, 'calib', 'en')
    assert len(rows) == 2
    assert rows[0]['state'] == render_state('A claim', 'B claim', None)
    assert rows[1]['state'] == render_state('B claim', 'A claim', None)
    assert rows[0]['gold']['supports']['probabilities'] == {'true':1.0,'false':0.0}
    assert 'supports' not in rows[1]['gold']


def test_unlabeled_predicate_not_trained():
    rows = rows_for_pair('A', 'B', None, {'supports':None,'contradicts':0,'same_as':1}, 'mirror', 'mixed')
    assert all('supports' not in r['gold'] for r in rows)
    assert len(rows_for_pair('A','B',None,{},'none','en')) == 0


@pytest.mark.parametrize('a_id,a_text', [('eval:a','fresh'), ('other','eval text')])
def test_graph_sources_exclude_eval_by_id_or_text(a_id,a_text):
    rows, counts, excluded, add = pair_builder([{'a':{'id':'eval:a','text':'eval text'},'b':{'id':'eval:b','text':'other eval'}}])
    add(a_id,'new:b',a_text,'new text',None,{'same_as':1},'graph','mixed')
    assert not rows
    assert excluded['graph:eval_leak'] == 1
    add(a_id,'new:b',a_text,'new text',None,{'same_as':0},'calib','mixed')
    assert len(rows) == 2
    assert counts['calib:shares_node_with_eval'] == 1


def test_reverse_duplicates_are_excluded():
    rows, counts, excluded, add = pair_builder([])
    add('a','b','text A','text B',None,{'same_as':1},'graph','en')
    add('b','a','text B','text A',None,{'same_as':1},'graph','en')
    assert len(rows) == 2
    assert excluded['graph:duplicate'] == 1


class Param:
    def __init__(self,n): self.n,self.requires_grad = n,True
    def numel(self): return self.n
    def requires_grad_(self,value): self.requires_grad=value


class Model:
    def __init__(self): self.parts = {'encoder.embeddings.weight':Param(10),'encoder.layer.0':Param(4),'head.weight':Param(2)}
    def named_parameters(self): return self.parts.items()
    def parameters(self): return self.parts.values()


def test_only_embeddings_frozen():
    m=Model()
    assert freeze_embeddings(m) == (6,16)
    assert not m.parts['encoder.embeddings.weight'].requires_grad
    assert m.parts['encoder.layer.0'].requires_grad
    assert m.parts['head.weight'].requires_grad


def test_missing_embeddings_refused():
    m=Model(); del m.parts['encoder.embeddings.weight']
    with pytest.raises(ValueError): freeze_embeddings(m)


def test_paths_are_explicit_and_recipe_defaults_preserved():
    args=parser().parse_args(['--gold-dir','gold','--candidates','c','--typed-decisions','td','--out','out'])
    assert args.gold_dir == Path('gold')
    assert args.typed_decisions_rows == 600
    ft=ft_parser().parse_args(['--snapshot','snap','--data','data','--out','out','--hf-home','cache'])
    assert (ft.epochs,ft.seed)==(4,20261007)

def test_holdout_and_eval_exclusion_in_cached_training(tmp_path, monkeypatch):
    import json
    from rag_eval.laya import build_train
    gold_dir = tmp_path / 'gold'; gold_dir.mkdir()
    def pair(pid, aid, text):
        return {'pair_id':pid,'a':{'id':aid,'text':text},'b':{'id':pid+':b','text':pid+' B'},'labels':{'supports':0,'contradicts':0,'same_as':1},'lang':'en'}
    pairs=[pair('h','held:a','held text'),pair('c','fresh:a','fresh text'),pair('e','eval:a','eval text'),pair('overlap','held:a','changed text')]
    (gold_dir/'gold.jsonl').write_text(''.join(json.dumps(p)+'\n' for p in pairs))
    (gold_dir/'gold.split.json').write_text(json.dumps({'split':{'h':'calib','c':'calib','e':'eval','overlap':'calib'}}))
    hold=tmp_path/'hold.json'; hold.write_text(json.dumps({'holdout':['h']}))
    base=tmp_path/'base.jsonl'
    old=rows_for_pair('held text','other',None,{'same_as':1},'book-mirror','en')
    old+=rows_for_pair('eval text','other',None,{'same_as':1},'random','en')
    old+=rows_for_pair('good','other',None,{'same_as':1},'book-mirror','en')
    base.write_text(''.join(json.dumps(r)+'\n' for r in old))
    out=tmp_path/'out.jsonl'
    monkeypatch.delenv('VINC_RO',raising=False)
    monkeypatch.setattr(build_train,'hub_node',lambda *a: pytest.fail('Must not call the hub'))
    args=['--gold-dir',str(gold_dir),'--candidates','unused','--typed-decisions','unused','--out',str(out),'--base-training',str(base),'--holdout',str(hold)]
    assert build_train.main(args)==0
    result=[json.loads(l) for l in out.read_text().splitlines()]
    assert len(result)==4
    assert all('held text' not in r['state'] and 'eval text' not in r['state'] and 'changed text' not in r['state'] for r in result)
    summary=json.loads(out.with_suffix('.summary.json').read_text())
    assert summary['excluded']['calib:holdout_leak']==2
    assert summary['excluded']['book-mirror:blocked_text']==2
    assert summary['excluded']['random:blocked_text']==2
    with pytest.raises(FileExistsError): build_train.main(args)


def test_pair_summary_counts_question_items_separately_from_rows():
    rows, counts, excluded, add = pair_builder([])
    add("a", "b", "one", "two", None, {"supports": 1, "contradicts": 0, "same_as": 1}, "calib", "en")
    assert counts["calib:rows"] == 2
    assert counts["calib:items"] == 5


def _gold_dir(root, name, pairs, split):
    import json
    d = root / name; d.mkdir()
    (d / 'gold.jsonl').write_text(''.join(json.dumps(p) + '\n' for p in pairs))
    (d / 'gold.split.json').write_text(json.dumps({'split': split}))
    return d


def _gold_pair(pid, a_id, a_text, same_as=0):
    return {'pair_id': pid, 'a': {'id': a_id, 'text': a_text}, 'b': {'id': pid + ':b', 'text': pid + ' B'},
            'labels': {'supports': 0, 'contradicts': 0, 'same_as': same_as}, 'lang': 'en'}


def _build(tmp_path, monkeypatch, extra):
    import json
    from rag_eval.laya import build_train
    main_dir = _gold_dir(tmp_path, 'main', [_gold_pair('c', 'main:c', 'main calib'), _gold_pair('e', 'main:e', 'main eval')],
                         {'c': 'calib', 'e': 'eval'})
    base = tmp_path / 'base.jsonl'
    base.write_text(''.join(json.dumps(r) + '\n' for r in
                            rows_for_pair('other eval', 'x', None, {'same_as': 1}, 'book-mirror', 'en')
                            + rows_for_pair('kept', 'y', None, {'same_as': 1}, 'book-mirror', 'en')))
    out = tmp_path / 'out.jsonl'
    monkeypatch.delenv('VINC_RO', raising=False)
    monkeypatch.setattr(build_train, 'hub_node', lambda *a: pytest.fail('Must not call the hub'))
    code = build_train.main(['--gold-dir', str(main_dir), '--candidates', 'unused', '--typed-decisions', 'unused',
                             '--out', str(out), '--base-training', str(base)] + extra)
    return code, [json.loads(l) for l in out.read_text().splitlines()], json.loads(out.with_suffix('.summary.json').read_text())


def test_second_gold_dir_trains_on_its_calib_and_blocks_its_eval(tmp_path, monkeypatch):
    other = _gold_dir(tmp_path, 'other', [_gold_pair('oc', 'other:c', 'other calib'), _gold_pair('oe', 'other:e', 'other eval'),
                                          _gold_pair('leak', 'other:e', 'shares the eval node')],
                      {'oc': 'calib', 'oe': 'eval', 'leak': 'calib'})
    code, rows, summary = _build(tmp_path, monkeypatch, ['--train-gold-dir', str(other)])
    assert code == 0
    states = ' '.join(r['state'] for r in rows)
    assert 'other calib' in states and 'main calib' in states and 'kept' in states
    # 그 셋의 측정 쌍은 직접도, 노드 id 를 거쳐서도, 재사용한 기반 행으로도 들어오지 않는다
    assert 'other eval' not in states and 'shares the eval node' not in states
    assert summary['counts']['calib-other'] == 1
    assert summary['excluded']['calib-other:eval_leak'] == 1
    assert summary['excluded']['book-mirror:blocked_text'] == 2
    assert summary['extra_gold_dirs']['other']['role'] == 'train'


def test_block_only_gold_dir_adds_nothing_and_blocks_its_eval(tmp_path, monkeypatch):
    other = _gold_dir(tmp_path, 'measure', [_gold_pair('oc', 'm:c', 'measure calib'), _gold_pair('oe', 'm:e', 'other eval')],
                      {'oc': 'calib', 'oe': 'eval'})
    code, rows, summary = _build(tmp_path, monkeypatch, ['--block-gold-dir', str(other)])
    states = ' '.join(r['state'] for r in rows)
    assert 'measure calib' not in states and 'other eval' not in states and 'kept' in states
    assert summary['extra_gold_dirs']['measure'] == {'role': 'block', 'gold_sha256': summary['extra_gold_dirs']['measure']['gold_sha256'],
                                                     'eval_blocked': 1}


def test_same_as_negatives_carry_only_the_same_as_label(tmp_path, monkeypatch):
    import json
    neg = tmp_path / 'neg.jsonl'
    def row(a_id, a_text, rule='pack-linked-distinct-concepts', labels=None):
        return {'a': {'id': a_id, 'text': a_text}, 'b': {'id': a_id + ':b', 'text': a_text + ' other'}, 'lang': 'en',
                'source': rule, 'labels': labels or {'supports': 0, 'contradicts': 0, 'same_as': 0}, 'provenance': {'rule': rule}}
    neg.write_text(json.dumps(row('n:1', 'linked one')) + '\n\n' + json.dumps(row('main:e', 'touches eval node')) + '\n')
    code, rows, summary = _build(tmp_path, monkeypatch, ['--same-as-negatives', str(neg)])
    added = [r for r in rows if r['meta']['source'] == 'pack-linked-distinct-concepts']
    assert len(added) == 2                                        # 두 방향
    assert all(set(r['gold']) == {'same_as'} and r['gold']['same_as']['probabilities']['true'] == 0.0 for r in added)
    assert summary['excluded']['pack-linked-distinct-concepts:eval_leak'] == 1
    assert list(summary['same_as_negatives_sha256']) == ['neg.jsonl']


@pytest.mark.parametrize('rule,labels', [('teacher-llm', {'same_as': 0}), ('pack-linked-distinct-concepts', {'same_as': 1})])
def test_same_as_negatives_refuse_unregistered_rules_and_positive_labels(tmp_path, monkeypatch, rule, labels):
    import json
    neg = tmp_path / 'neg.jsonl'
    neg.write_text(json.dumps({'a': {'id': 'n:1', 'text': 'one'}, 'b': {'id': 'n:2', 'text': 'two'}, 'lang': 'en',
                               'source': rule, 'labels': labels, 'provenance': {'rule': rule}}) + '\n')
    with pytest.raises(ValueError):
        _build(tmp_path, monkeypatch, ['--same-as-negatives', str(neg)])
