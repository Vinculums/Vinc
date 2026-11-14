"""지침 원문 이동은 명시한 동의값과 실행 지문에 묶는다."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_topic_transfer import SOURCE, TARGET, TOPIC, STAMP, RawStore, seed, insert
from test_topic_transfer_boundaries import add_row
from api import docstore, graphmd, topic_conversion as tc
from store.embedded_schema import render_embedded_ddl
from store.scoped_topic_inventory import read_scoped_inventory
from store.topic_transfer import plan_transfer, TransferRefused
from topic_rehearsal import injected_failure

RUN = "00000000-0000-4000-8000-000000000099"


@pytest.mark.parametrize("doc", ["graph.md", graphmd.conventions_doc_id(SOURCE)])
def test_native_explicit_consent_moves_original_lookup_and_personal_fallback(tmp_path, doc):
    store = RawStore(tmp_path / "conventions.db")
    store._FTS_INDEX, store._VEC_INDEX, store._NODE_VEC_INDEX = "chunk_ft", "chunk_vec", "node_vec"
    try:
        for ddl in render_embedded_ddl(dim=4):
            store.run(ddl)
        data = json.loads(json.dumps(seed()).replace("doc1", doc))
        insert(store, data)
        original = docstore.get_doc(store, doc, owner=SOURCE)
        assert original["verified"]
        default = tc.preflight(store, SOURCE, TOPIC)
        assert "owner_conventions_require_separate_decision" in default["blockers"]
        consent = tc.preflight(store, SOURCE, TOPIC, conventions_policy="move_original")
        assert consent["ready"], consent
        assert consent["fingerprint"] != default["fingerprint"]
        assert consent["conventions"] == {"policy": "move_original", "documents": 1,
                                          "personal_defaults_after_move": True}
        args = dict(source=SOURCE, target=TARGET, topic=TOPIC, run=RUN,
                    expected=consent["fingerprint"], stamp=STAMP, conventions_policy="move_original")
        with pytest.raises(RuntimeError, match="injected_before_commit"), injected_failure("journal"):
            tc.stage(store, **args)
        assert docstore.get_doc(store, doc, owner=SOURCE)["text"] == original["text"]
        assert tc.stage(store, **args)["status"] == "graph_committed"
        store.close()
        store.reopen()
        assert tc.stage(store, **args)["status"] == "graph_committed"
        with pytest.raises(TransferRefused, match="run_conventions_policy_changed"):
            tc.stage(store, **{**args, "conventions_policy": "retain"})
        assert docstore.resolve_conventions_doc_id(store, TARGET) == doc
        after = docstore.get_doc(store, doc, owner=TARGET)
        assert after["verified"] and after["text"] == original["text"]
        personal_id = docstore.resolve_conventions_doc_id(store, SOURCE)
        assert personal_id == graphmd.conventions_doc_id(SOURCE)
        assert not docstore.get_doc(store, personal_id, owner=SOURCE).get("executed")
        assert tc.release(store, source=SOURCE, target=TARGET, topic=TOPIC, run=RUN)["status"] == "active"
        with pytest.raises(TransferRefused, match="moved"):
            tc.check_write(store, SOURCE, {"doc_id": doc})
    finally:
        store.close()


def test_default_and_invalid_consent_never_authorize_move():
    data = json.loads(json.dumps(seed()).replace("doc1", "graph.md"))
    assert not plan_transfer(data, source=SOURCE, target=TARGET, topic=TOPIC).ready
    for choice in (True, None, "copy", ""):
        with pytest.raises(TransferRefused, match="invalid_conventions_policy"):
            plan_transfer(data, source=SOURCE, target=TARGET, topic=TOPIC, conventions_policy=choice)


def test_second_personal_conventions_outside_topic_blocks_full_and_scoped(tmp_path):
    store = RawStore(tmp_path / "ambiguous.db")
    try:
        for ddl in render_embedded_ddl(dim=4):
            store.run(ddl)
        data = json.loads(json.dumps(seed()).replace("doc1", "graph.md"))
        add_row(data, "_DocMeta", doc_id=graphmd.conventions_doc_id(SOURCE), owner_id=SOURCE,
                origin="vinc:conventions", chunk_count=0)
        insert(store, data)
        full = plan_transfer(data, source=SOURCE, target=TARGET, topic=TOPIC, conventions_policy="move_original")
        assert "ambiguous_owner_conventions" in full.blockers
        scoped = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC)
        assert "ambiguous_owner_conventions" in scoped.blockers
    finally:
        store.close()
