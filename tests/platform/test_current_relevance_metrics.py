import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.qa.current_relevance_probe import load_validation, passes, validation_metrics, split
from scripts.qa import current_relevance_probe as probe


def test_validation_uses_graph_candidates_from_the_actual_policy_search():
    item = {'query': 'synthetic-no-match', 'negative': True}
    baseline = [(item, {'rows': [], 'node_hits': [{'id': 'adjacent', 'arms': ['graph']}]})]
    candidate = {'rows': [], 'node_hits': []}
    counts = validation_metrics(baseline, [candidate])[split(item['query'])]
    assert counts['negative_queries'] == 1
    assert counts['negative_with_primary'] == 0


def test_validation_counts_actual_gold_loss_and_unknown_primary_evidence():
    positive = {'query': 'normal-question', 'gold_node_id': 'gold'}
    negative = {'query': 'synthetic-no-match', 'negative': True}
    baseline = [(positive, {'rows': [], 'node_hits': [{'id': 'gold'}]}),
                (negative, {'rows': [], 'node_hits': []})]
    candidate = [{'rows': [], 'node_hits': []}, {'rows': [], 'node_hits': [
        {'id': 'unknown', 'relevance': {'level': 'unclassified', 'basis': 'distance_unavailable'}}]}]
    metrics = validation_metrics(baseline, candidate)
    assert metrics[split(positive['query'])]['node_gold_lost'] == 1
    assert metrics[split(negative['query'])]['negative_with_primary'] == 1
    assert metrics[split(negative['query'])]['unclassified_negative_hits'] == 1


def test_independent_split_counts_every_validation_query_in_one_bucket():
    items = [{'query': q, 'negative': True} for q in ('a', 'b', 'c')]
    baseline = [(x, {'rows': [], 'node_hits': []}) for x in items]
    metrics = validation_metrics(baseline, [{'rows': [], 'node_hits': []}] * 3,
                                 lambda q: 'independent', ('independent',))
    assert metrics['independent']['queries'] == 3
    assert not passes(metrics)  # 정답 라벨 없는 셋은 통과로 읽히지 않는다


def _write(tmp_path, items):
    path = tmp_path / 'validation.json'
    path.write_text(json.dumps(items), encoding='utf-8')
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_validation_inputs_must_be_disjoint_hashed_and_fully_labelled(tmp_path):
    good = [{'query': 'n', 'negative': True}, {'query': 'g', 'gold_node_id': 'x'},
            {'query': 'd', 'gold_doc_id': 'y'}]
    path, sha = _write(tmp_path, good)
    assert load_validation(path, sha, [{'query': 'old'}]) == good
    with pytest.raises(AssertionError, match='changed'):
        load_validation(path, '0' * 64, [])
    with pytest.raises(AssertionError, match='overlaps'):
        load_validation(path, sha, [{'query': 'g'}])
    path, sha = _write(tmp_path, good[:2])
    with pytest.raises(AssertionError, match='labels'):
        load_validation(path, sha, [])


@pytest.mark.parametrize('failure', [None, 'preparation', 'search'])
def test_probe_prepares_missing_indexes_and_still_refuses_degraded_search(tmp_path, monkeypatch, failure):
    root = Path(__file__).resolve().parents[2]
    inputs = root / 'rag_eval/fixtures/current_relevance_acceptance.json'
    validation = root / 'rag_eval/fixtures/current_relevance_validation.json'
    items = json.loads(inputs.read_text(encoding='utf-8'))
    vitems = json.loads(validation.read_text(encoding='utf-8'))
    by_query = {x['query']: x for x in items + vitems}
    source = tmp_path / 'snapshot'
    source.write_bytes(b'immutable source')
    model = tmp_path / 'model.onnx'
    model.write_bytes(b'encoder fixture')
    model.with_name('tokenizer.json').write_text('{}')
    out = tmp_path / 'result.json'
    state = SimpleNamespace(ready=False, closed=False, searches=0)

    class Store:
        def provision(self, **kwargs):
            pass

        def ensure_search_indexes(self, **kwargs):
            if failure == 'preparation':
                raise RuntimeError('index preparation failed')
            state.ready = True
            return {'fts': False, 'vector': True}

        def run(self, query, params=None):
            return [{'n': len(params['ids'])}] if params else [{'stamp': 'fixture', 'n': 1}]

        def close(self):
            state.closed = True

    class Encoder:
        def __init__(self, *args, **kwargs):
            pass

        def encode(self, queries, **kwargs):
            return [[1.0]]

    def search(query, **kwargs):
        state.searches += 1
        response = {'rows': [], 'node_hits': []}
        if not state.ready or failure == 'search':
            response['vector_note'] = 'semantic arm unavailable'
            return response
        item = by_query[query]
        for goldkey, key, identity in [('gold_node_id', 'node_hits', 'id'),
                                      ('gold_doc_id', 'rows', 'doc_id')]:
            if goldkey in item:
                response[key] = [{identity: item[goldkey], 'distance': .2, 'arms': ['vector']}]
        if 'relevance_profile' in kwargs:
            response['relevance_policy'] = {'state': 'active'}
            response['rows'] = probe.classify_rows(response['rows'], kwargs['relevance_profile'], kind='chunk')
            response['node_hits'] = probe.classify_rows(response['node_hits'], kwargs['relevance_profile'], kind='node')
        return response

    def open_store(cfg):
        assert Path(cfg.data_dir) != source
        assert Path(cfg.data_dir).read_bytes() == source.read_bytes()
        return Store()

    monkeypatch.setenv('VINC_EMBED_MODEL_PATH', str(model))
    monkeypatch.setenv('VINC_EMBED_MODEL', 'fixture')
    monkeypatch.delenv('VINC_MEASURE_IMAGE_SHA', raising=False)
    monkeypatch.setattr(probe, 'fingerprint', lambda: 'fixture')
    monkeypatch.setattr(probe, 'load_config', lambda d: SimpleNamespace(**d, embedding_dim=1))
    monkeypatch.setattr(probe, 'open_store', open_store)
    monkeypatch.setattr(probe, 'Encoder', Encoder)
    monkeypatch.setattr(probe.retrieval, 'search', search)
    monkeypatch.setattr(probe.sys, 'argv', ['probe', '--store', str(source), '--inputs', str(inputs),
                        '--inputs-sha', probe.digest(inputs), '--owner', 'fixture', '--out', str(out),
                        '--validation-inputs', str(validation), '--validation-sha', probe.digest(validation)])
    code = probe.main()
    result = json.loads(out.read_text())
    assert state.closed and result['source_preserved']
    assert source.read_bytes() == b'immutable source'
    if failure is None:
        assert code == 0 and result['status'] == 'measured', result
        assert result['completed_queries'] == result['completed_candidate_queries'] == 116
        assert result['independent_validation']['queries'] == len(vitems)
    else:
        assert code == 1 and result['status'] == 'failed_not_a_measurement'
        assert result['completed_queries'] == 0
        assert 'independent_quality_pass' not in result
        assert state.searches == (0 if failure == 'preparation' else 1)
