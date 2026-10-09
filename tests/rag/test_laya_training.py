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
