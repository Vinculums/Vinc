import hashlib
import json

import pytest

from scripts.qa.current_relevance_probe import load_validation, passes, validation_metrics, split


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
