from scripts.qa.current_relevance_probe import validation_metrics, split


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
