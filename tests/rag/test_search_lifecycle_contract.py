"""합성 결과로 후보 제한과 의미 검색 상태의 경계를 검증한다."""
from unittest.mock import patch

import pytest

from vinc_rag import retrieval


class Store:
    driver_name = "synthetic"
    scoped_chunk_search = True

    def __init__(self, nodes=(), vectors=(), fail=False):
        self.nodes, self.vectors, self.fail = list(nodes), list(vectors), fail

    def run(self, query, params=None):
        if "UNION ALL" in query and "props_raw" in query:
            return self.nodes
        return []

    def fulltext_search(self, query, k, filters=None):
        return []

    def vector_search(self, embedding, k, filters=None):
        return []

    def node_vector_search(self, embedding, k, owner=None):
        if self.fail:
            raise RuntimeError("node_vector_query_failed")
        return self.vectors[:k]


@pytest.mark.parametrize("query,target", [
    ("release moonprobe", "Release moonprobe"),
    ("출판 달탐사", "출판 달탐사"),
])
def test_strong_episode_title_survives_mixed_kind_scan_pressure(query, target):
    token = query.split()[0]
    noise = [{"id": f"concept:noise{i}", "kind": "concept", "title": token}
             for i in range(retrieval.NODE_SCAN_CAP + 1)]
    stats = {}
    hits = retrieval._node_fallback_search(Store(noise + [
        {"id": "episode:target", "kind": "episode", "title": target}]), query,
        owner="user:synthetic", limit=1, stats=stats)
    assert [h["id"] for h in hits] == ["episode:target"]
    assert stats["candidate_count"] == len(noise) + 1
    assert stats["candidate_count_capped"]


def test_failed_node_arm_does_not_claim_complete_semantics():
    with patch.object(retrieval, "_graph_arm_search", return_value=[]):
        result = retrieval.search("probe", store=Store(fail=True),
                                  owner="user:synthetic", query_embedding=[1.0])
    assert result["executed"] and result["arms"] == "fts+vector"
    assert result["node_semantic"]["state"] == "degraded"
    assert result["node_semantic"]["error"] == "node_vector_query_failed"
    assert result["node_error"]


def test_empty_success_does_not_certify_index_freshness():
    with patch.object(retrieval, "_graph_arm_search", return_value=[]):
        result = retrieval.search("probe", store=Store(), query_embedding=[1.0])
    assert result["node_semantic"]["state"] == "complete"
    assert result["node_semantic"]["freshness"] == "not_checked"
    assert result["node_window"]["pagination"] == "first_page_only"
    assert result["node_window"]["total_count_scope"] == "chunks"


def test_validation_failure_preserves_lexical_matches_with_explicit_degradation():
    store = Store(nodes=[{"id": "episode:probe", "kind": "episode", "title": "Probe"}],
                  vectors=[{"id": "episode:probe", "kind": "episode", "title": "Old",
                            "text": "Old", "distance": .1}])
    def fail(*args, **kwargs):
        raise RuntimeError("node_vector_validation_failed")
    store.validate_node_vector_hits = fail
    with patch.object(retrieval, "_graph_arm_search", return_value=[]):
        result = retrieval.search("probe", store=store, query_embedding=[1.0])
    assert result["node_semantic"]["state"] == "degraded"
    assert result["node_semantic"]["error"] == "node_vector_validation_failed"
    assert result["node_hits"][0]["arms"] == ["title"]
