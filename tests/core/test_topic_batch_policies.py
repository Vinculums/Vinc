"""Bound consent and personal history survive durable restoration together."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_topic_transfer import SOURCE, TARGET, TOPIC, STAMP, RawStore, seed, insert
from test_topic_conversion_batches import fixture, finish, Publication, RUN
from test_topic_reference_history_policy import history
from test_topic_rejection_history import rejection
from api import topic_conversion as legacy, docstore
from api.topic_conversion_batches import Batches
from store import durable_generation as dg
from store.embedded_schema import render_embedded_ddl
from store.topic_transfer import read_snapshot, plan_transfer, transformed_snapshot, fingerprint, TransferRefused


def test_consent_and_retained_history_survive_missing_local_resume(tmp_path):
    store = RawStore(tmp_path / "policy.db")
    restored = None
    try:
        store._FTS_INDEX, store._VEC_INDEX, store._NODE_VEC_INDEX = "chunk_ft", "chunk_vec", "node_vec"
        for ddl in render_embedded_ddl(dim=4):
            store.run(ddl)
        data = json.loads(json.dumps(seed()).replace("doc1", "graph.md"))
        history(data)
        rejected = rejection(data, "concept:a", "concept:absent")
        insert(store, data)
        before = read_snapshot(store)
        plan = plan_transfer(before, source=SOURCE, target=TARGET, topic=TOPIC, conventions_policy="move_original")
        expected = transformed_snapshot(before, plan, stamp=STAMP)
        flow, _, header = fixture(store, tmp_path, conventions_policy="move_original", max_rows=4)
        journal = legacy._get(store, RUN)
        assert journal["conventions_policy"] == header["conventions_policy"] == "move_original"
        assert journal["expected"] != plan.before_hash
        with pytest.raises(TransferRefused, match="run_conventions_policy_changed"):
            flow.initialize(flow.root / "topic-manifests" / (RUN + ".jsonl"), journal["manifest_hash"], journal["expected"])
        flow.step()
        destination = tmp_path / "restored" / "policy.db"
        assert dg.restore(flow.root, destination, skip_invalid=False).startswith("restored:")
        restored = RawStore(destination)
        restored._FTS_INDEX, restored._VEC_INDEX, restored._NODE_VEC_INDEX = "chunk_ft", "chunk_vec", "node_vec"
        resumed = Batches(restored, root=flow.root, publisher=Publication(restored, flow.root), run=RUN,
                          source=SOURCE, target=TARGET, topic=TOPIC, max_rows=4)
        result = finish(resumed)
        assert result["status"] == "graph_committed" and result["conventions_policy"] == "move_original"
        assert result["retained_personal_history"] == {
            "_Tombstone": 1, "_DocTombstone": 1, "_EdgeTombstone": 1, "_MergeRejection": 1}
        actual = read_snapshot(restored)
        actual["meta"]["_SyncMeta"] = [r for r in actual["meta"]["_SyncMeta"] if not r["key"].startswith(legacy.PREFIX)]
        assert fingerprint(actual) == fingerprint(expected)
        assert actual["meta"]["_MergeRejection"] == [rejected]
        assert docstore.resolve_conventions_doc_id(restored, TARGET) == "graph.md"
    finally:
        if restored is not None:
            restored.close()
        store.close()
