"""문서 목록의 주제와 이동 대상은 미러 유무 때문에 달라지지 않는다."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_topic_transfer import SOURCE, TARGET, TOPIC, STAMP, SEP, RawStore, seed, insert, plan
from store.embedded_schema import render_embedded_ddl
from store.scoped_topic_inventory import read_scoped_inventory
from store.topic_transfer import (_apply_rehearsal, fingerprint, read_snapshot,
                                  transformed_snapshot, plan_transfer)
from api.docstore import MentionTally, list_docs


def without_mirror():
    data = seed()
    data["nodes"]["Concept"] = [row for row in data["nodes"]["Concept"]
                                 if not row["concept_id"].endswith("concept:doc.doc1")]
    return data


def test_document_selection_uses_existing_derived_topic_without_creating_a_mirror():
    data = without_mirror()
    original = copy.deepcopy(data)
    chosen = plan(data)
    assert chosen.ready, chosen.blockers
    assert chosen.documents == ("doc1",)
    after = transformed_snapshot(data, chosen, stamp=STAMP)
    assert data == original
    assert len(after["nodes"]["Concept"]) == len(data["nodes"]["Concept"])
    assert after["meta"]["_DocMeta"][0]["owner_id"] == TARGET
    assert after["nodes"]["Chunk"][0]["owner_id"] == TARGET
    assert after["nodes"]["ChunkVec"][0]["owner_id"] == TARGET
    assert after["nodes"]["Chunk"][0]["text"] == data["nodes"]["Chunk"][0]["text"]


def test_authored_outside_topic_wins_over_derived_mentions():
    data = seed()
    mirror = next(row for row in data["nodes"]["Concept"] if row["concept_id"].endswith("concept:doc.doc1"))
    mirror["domain"] = "personal/outside"
    chosen = plan(data)
    assert "doc1" not in chosen.documents
    assert any(code.startswith("boundary_relation:MENTIONS:") for code in chosen.blockers)


def test_unassigned_document_is_not_added_by_body_reference():
    data = without_mirror()
    data["edges"] = [edge for edge in data["edges"] if edge["label"] != "MENTIONS"]
    chosen = plan(data)
    assert "doc1" not in chosen.documents
    assert any(code.startswith("incoming_reference:") for code in chosen.blockers)


def test_foreign_document_is_not_selected_from_owned_mentions():
    data = without_mirror()
    data["meta"]["_DocMeta"][0]["owner_id"] = "user:foreign"
    chosen = plan(data)
    assert "doc1" not in chosen.documents
    assert any(code.startswith("boundary_relation:MENTIONS:") for code in chosen.blockers)


def test_topic_ranking_keeps_authored_precedence_and_deterministic_ties():
    tally = MentionTally()
    tally.dom["doc"] = {"vinc/b": 2, "vinc/a": 2}
    assert tally.topics("doc") == ["vinc/a", "vinc/b"]
    tally.authored["doc"] = ("personal/outside", "concept:doc.doc", "doc")
    assert tally.topics("doc") == ["personal/outside", "vinc/a", "vinc/b"]


def test_native_scoped_inventory_and_full_transfer_match_document_list(tmp_path):
    store = RawStore(tmp_path / "synthetic.db")
    try:
        for statement in render_embedded_ddl(dim=4):
            store.run(statement)
        insert(store, without_mirror())
        listing = list_docs(store, owner=SOURCE, detail="full")
        doc = next(row for row in listing["docs"] if row["doc_id"] == "doc1")
        assert doc["topic"] == TOPIC
        before = read_snapshot(store)
        chosen = plan(before)
        scoped = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC)
        scoped_plan = plan_transfer(scoped.snapshot, source=SOURCE, target=TARGET, topic=TOPIC)
        assert not scoped.blockers
        assert scoped_plan.ready and scoped_plan.documents == chosen.documents
        assert set(scoped_plan.selected) == set(chosen.selected)
        expected = transformed_snapshot(before, chosen, stamp=STAMP)
        assert _apply_rehearsal(store, chosen, run_id="derived-doc-topic", stamp=STAMP) == "committed"
        actual = read_snapshot(store)
        actual["meta"]["_SyncMeta"] = [row for row in actual["meta"]["_SyncMeta"]
                                       if not row["key"].startswith("topic-transfer:run:")]
        assert fingerprint(actual) == fingerprint(expected)
        after = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC, after=True)
        assert {row["doc_id"] for row in after.snapshot["meta"]["_DocMeta"]} == {"doc1"}
    finally:
        store.close()


def test_scoped_topic_tally_spans_pages_and_normalizes_domains(tmp_path):
    data = without_mirror()
    data["nodes"]["Concept"][1]["domain"] = " " + TOPIC + "/child "
    for i in range(70):
        doc, chunk = "zdoc" + str(i).zfill(3), "zchunk" + str(i).zfill(3)
        meta = copy.deepcopy(data["meta"]["_DocMeta"][0])
        meta["doc_id"] = doc
        data["meta"]["_DocMeta"].append(meta)
        row = copy.deepcopy(data["nodes"]["Chunk"][0])
        row.update(doc_id=doc, chunk_id=chunk)
        data["nodes"]["Chunk"].append(row)
        for target in ("concept:a", "concept:b"):
            edge = copy.deepcopy(next(e for e in data["edges"] if e["label"] == "MENTIONS"))
            edge.update({"from": chunk, "to": SOURCE + SEP + target})
            data["edges"].append(edge)
    store = RawStore(tmp_path / "pages.db")
    try:
        for statement in render_embedded_ddl(dim=4):
            store.run(statement)
        insert(store, data)
        full = plan(data)
        scoped = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC)
        actual = plan_transfer(scoped.snapshot, source=SOURCE, target=TARGET, topic=TOPIC)
        assert set(actual.documents) == set(full.documents) == {"doc1", *("zdoc" + str(i).zfill(3) for i in range(70))}
        # 잘못된 외부 좌표 자체는 재분류하지 않고 관계 경계로 남긴다.
        assert any(b.startswith("boundary_relation:") for b in actual.blockers)
    finally:
        store.close()
