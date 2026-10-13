from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_topic_transfer import (  # noqa: E402
    SOURCE, STAMP, TARGET, SEP, RawStore, insert, native, plan, seed,
)
from store.embedded_schema import meta_table_specs, node_table_specs, render_embedded_ddl  # noqa: E402
from store.topic_transfer import (  # noqa: E402
    TransferRefused, _apply_rehearsal, fingerprint, read_snapshot, transformed_snapshot,
)


def add_row(data, table, **fields):
    specs = {**node_table_specs(), **meta_table_specs()}
    row = {c: None for c in specs[table]["cols"]}
    row.update(fields)
    data["meta" if table.startswith("_") else "nodes"][table].append(row)


def assert_blocked(data, reason):
    before = fingerprint(data)
    p = plan(data)
    assert not p.ready and any(reason in b for b in p.blockers), p.blockers
    with pytest.raises(TransferRefused, match=reason):
        transformed_snapshot(data, p, stamp=STAMP)
    assert fingerprint(data) == before


@pytest.mark.parametrize("doc", ["graph.md", "graph.md~0123456789abcdef"])
def test_canonical_conventions_ids_refused_without_origin_hint(doc):
    data = json.loads(json.dumps(seed()).replace("doc1", doc))
    assert_blocked(data, "owner_conventions_require_separate_decision")


def test_similar_regular_document_is_preserved():
    data = json.loads(json.dumps(seed()).replace("doc1", "graph.md~notes"))
    p = plan(data)
    assert p.ready, p.blockers
    after = transformed_snapshot(data, p, stamp=STAMP)
    assert after["meta"]["_DocMeta"][0]["doc_id"] == "graph.md~notes"


@pytest.mark.parametrize("properties", [
    {"Attachments": [{"url": "blob:synthetic"}]},
    {"nested": [{"assets": ["file:synthetic.png"]}]},
    {"attachment_url": "https://synthetic.invalid/private-file"},
    {"blob_uri": "blob:synthetic"},
    {"attachmentUrl": "https://synthetic.invalid/private-file"},
    {"filePath": "relative/synthetic.pdf"},
    {"nested": {"asset-url": "https://synthetic.invalid/private-file"}},
])
def test_external_attachment_forms_refused(properties):
    data = seed()
    data["nodes"]["Record"][0]["props"] = json.dumps(properties)
    assert_blocked(data, "external_attachments_not_supported")


@pytest.mark.parametrize("path", ["D:\\synthetic\\file.pdf", "/synthetic/file.pdf", "file:synthetic", "s3:synthetic",
                                  "relative/synthetic.pdf", "https://synthetic.invalid/source.pdf"])
def test_external_source_files_refused(path):
    data = seed()
    data["nodes"]["Chunk"][0]["source_file"] = path
    assert_blocked(data, "external_source_file_not_supported")


@pytest.mark.parametrize("properties,reason", [
    ({"arbitrary_key": "blob:synthetic"}, "external_resource_reference_not_supported"),
    ({"nested": ["file:synthetic"]}, "external_resource_reference_not_supported"),
    ({"vocabulary": {"custom": "type"}}, "owner_vocabulary_not_supported"),
    ({"themeId": "owner-synthetic"}, "owner_vocabulary_not_supported"),
    ({"GRAPH.md": "synthetic conventions"}, "owner_conventions_require_separate_decision"),
])
def test_opaque_dependency_fields_cannot_bypass_preflight(properties, reason):
    data = seed()
    data["nodes"]["Record"][0]["props"] = json.dumps(properties)
    assert_blocked(data, reason)


@pytest.mark.parametrize("image", ["![synthetic](relative/synthetic.png)", '<img src="blob:synthetic">'])
def test_embedded_document_attachments_refused(image):
    import hashlib

    data = seed()
    chunk = data["nodes"]["Chunk"][0]
    chunk["text"] += image
    chunk["char_end"] = chunk["char_count"] = len(chunk["text"])
    data["meta"]["_DocMeta"][0]["content_hash"] = hashlib.sha256(chunk["text"].encode()).hexdigest()
    assert_blocked(data, "external_attachments_not_supported")


def test_supported_arbitrary_json_properties_remain_byte_identical():
    data = seed()
    value = '{"custom_note":{"values":[1,true,"kept"]}}'
    data["nodes"]["Record"][0]["props"] = value
    p = plan(data)
    assert p.ready, p.blockers
    assert transformed_snapshot(data, p, stamp=STAMP)["nodes"]["Record"][0]["props"] == value


@pytest.mark.parametrize("theme", [None, "owner-synthetic"])
def test_entity_cannot_be_silently_left_out_of_topic(theme):
    data = seed()
    add_row(data, "Entity", entity_id=SOURCE + SEP + "entity:synthetic", owner_id=SOURCE,
            type="synthetic", title="Unclassified entity", theme_id=theme)
    assert_blocked(data, "entity_topic_membership_not_supported")


@pytest.mark.parametrize("physical", [False, True])
def test_merge_history_blocks_both_logical_and_physical_references(physical):
    data = seed()
    prefix = SOURCE + SEP if physical else ""
    add_row(data, "_MergeRejection", pair_key="synthetic-history", owner_id=SOURCE,
            a_id=prefix + "concept:a", b_id=prefix + "concept:b")
    assert_blocked(data, "merge_rejection_history_not_supported")


def test_merge_history_with_deleted_endpoint_refused():
    data = seed()
    add_row(data, "_MergeRejection", pair_key="synthetic-history", owner_id=SOURCE,
            a_id="concept:deleted", b_id="concept:other")
    assert_blocked(data, "merge_rejection_topic_membership_unknown")


@pytest.mark.parametrize("label,fields", [
    ("_Tombstone", {"node_id": "concept:deleted", "label": "Concept"}),
    ("_DocTombstone", {"doc_id": "deleted-document"}),
    ("_EdgeTombstone", {"ekey": "deleted-edge", "src": "concept:gone", "dst": "concept:other", "rel": "DERIVES_FROM"}),
])
def test_history_with_unknown_topic_membership_refused(label, fields):
    data = seed()
    add_row(data, label, owner_id=SOURCE, **fields)
    assert_blocked(data, "unclassified_tombstone_topic_history")


@pytest.mark.parametrize("label", ["Record", "Chunk", "ChunkVec", "NodeVec", "_DocMeta", "_SyncBase"])
def test_unknown_row_field_is_an_explicit_blocker(label):
    data = seed()
    group = "meta" if label.startswith("_") else "nodes"
    data[group][label][0]["unsupported_attachment"] = "must not disappear"
    assert_blocked(data, "incomplete_columns:" + label)


@pytest.mark.parametrize("field", ["owner_id", "record_id"])
def test_incomplete_rows_raise_clear_refusal(field):
    data = seed()
    del data["nodes"]["Record"][0][field]
    before = copy.deepcopy(data)
    with pytest.raises(TransferRefused, match="incomplete_columns:Record"):
        plan(data)
    assert data == before


@pytest.mark.parametrize("field", ["sidecars", "vocabulary", "attachments"])
def test_unrecognized_snapshot_fields_are_not_ignored(field):
    data = seed()
    data[field] = {"unsupported": "must not disappear"}
    with pytest.raises(TransferRefused, match="unsupported_snapshot_fields:" + field):
        plan(data)


def test_unrecognized_relationship_property_is_a_blocker():
    data = seed()
    data["edges"][0]["props"]["unsupported_attachment"] = "must not disappear"
    assert_blocked(data, "unsupported_relationship_columns:DERIVES_FROM")


@pytest.mark.parametrize("mode", ["extra", "missing", "unknown_pair"])
def test_unsupported_relationship_shape_refused(mode):
    data = seed()
    if mode == "extra":
        data["edges"][0]["owner_override"] = TARGET
    elif mode == "missing":
        del data["edges"][0]["props"]
    else:
        data["edges"][0]["from_label"] = "UnknownCarrier"
    with pytest.raises(TransferRefused, match="unsupported_relationship"):
        plan(data)


@pytest.mark.parametrize("content", ['{"themes":{}}', '{"themes":{"owner-synthetic":{"vocabulary":{"custom":"type"}}}}', "invalid-json"])
def test_native_owner_vocabulary_sidecar_refuses_before_dml(native, content):
    before = read_snapshot(native)
    p = plan(before)
    path = Path(native.path + ".themes.json")
    path.write_text(content, encoding="utf-8")
    trace = []
    original_run = native.run

    def traced_run(q, params=None, *, raw=True):
        trace.append(q)
        return original_run(q, params, raw=raw)

    native.run = traced_run
    with pytest.raises(TransferRefused, match="owner_vocabulary_sidecar_not_supported"):
        read_snapshot(native)
    with pytest.raises(TransferRefused, match="owner_vocabulary_sidecar_not_supported"):
        _apply_rehearsal(native, p, run_id="sidecar-must-not-move", stamp=STAMP)
    assert not any(re.search(r"\b(CREATE|MERGE|SET|DELETE|ALTER|DROP|COPY)\b", q) for q in trace)
    assert path.read_text(encoding="utf-8") == content
    source_count = sum(r.get("owner_id") == SOURCE for r in before["nodes"]["Concept"])
    assert native.run("MATCH (n:Concept) WHERE n.owner_id=$owner RETURN count(n) AS n", {"owner": SOURCE}) == [{"n": source_count}]
    assert native.run("MATCH (n:Concept) WHERE n.owner_id=$owner RETURN count(n) AS n", {"owner": TARGET}) == [{"n": 0}]


def test_native_unknown_relationship_column_refused(native):
    native.run("ALTER TABLE DERIVES_FROM ADD unsupported_attachment STRING DEFAULT 'synthetic'")
    with pytest.raises(TransferRefused, match="unsupported_schema_columns:DERIVES_FROM"):
        read_snapshot(native)


def test_store_without_external_state_inventory_is_refused(native):
    class Pathless:
        def run(self, *args, **kwargs):
            return native.run(*args, **kwargs)

    with pytest.raises(TransferRefused, match="external_store_state_unchecked"):
        read_snapshot(Pathless())


@pytest.mark.parametrize("case,reason", [
    ("graphmd", "owner_conventions_require_separate_decision"),
    ("attachments", "external_attachments_not_supported"),
    ("source_file", "external_source_file_not_supported"),
    ("entity", "entity_topic_membership_not_supported"),
    ("merge_history", "merge_rejection_history_not_supported"),
    ("node_history", "unclassified_tombstone_topic_history"),
    ("edge_history", "unclassified_tombstone_topic_history"),
    ("doc_history", "unclassified_tombstone_topic_history"),
])
def test_native_unsupported_payload_is_preserved_and_no_dml_runs(tmp_path, case, reason):
    data = seed()
    if case == "graphmd":
        data = json.loads(json.dumps(data).replace("doc1", "graph.md~0123456789abcdef"))
    elif case == "attachments":
        data["nodes"]["Record"][0]["props"] = '{"attachments":["blob:synthetic"]}'
    elif case == "source_file":
        data["nodes"]["Chunk"][0]["source_file"] = "file:synthetic.pdf"
    elif case == "entity":
        add_row(data, "Entity", entity_id=SOURCE + SEP + "entity:synthetic", owner_id=SOURCE,
                title="Unclassified", theme_id="owner-synthetic", type="synthetic")
    elif case == "merge_history":
        add_row(data, "_MergeRejection", pair_key="synthetic-history", owner_id=SOURCE,
                a_id=SOURCE + SEP + "concept:a", b_id=SOURCE + SEP + "concept:b")
    elif case == "node_history":
        add_row(data, "_Tombstone", node_id="concept:deleted", owner_id=SOURCE, label="Concept")
    elif case == "edge_history":
        add_row(data, "_EdgeTombstone", ekey="deleted-edge", owner_id=SOURCE,
                src="concept:gone", dst="concept:other", rel="DERIVES_FROM")
    else:
        add_row(data, "_DocTombstone", doc_id="deleted-document", owner_id=SOURCE)
    store = RawStore(tmp_path / "unsupported-synthetic.db")
    try:
        for ddl in render_embedded_ddl(dim=4):
            store.run(ddl)
        insert(store, data)
        before = read_snapshot(store)
        p = plan(before)
        assert not p.ready and any(reason in b for b in p.blockers)
        trace = []
        original_run = store.run

        def traced_run(q, params=None, *, raw=True):
            trace.append(q)
            return original_run(q, params, raw=raw)

        store.run = traced_run
        with pytest.raises(TransferRefused, match=reason):
            _apply_rehearsal(store, p, run_id="must-not-move", stamp=STAMP)
        assert not any(re.search(r"\b(CREATE|MERGE|SET|DELETE|ALTER|DROP|COPY)\b", q) for q in trace)
        assert fingerprint(read_snapshot(store)) == fingerprint(before)
    finally:
        store.close()
