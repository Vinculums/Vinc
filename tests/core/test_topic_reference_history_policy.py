"""인용 원문과 개인 삭제 이력을 보존하고 실제 자원·충돌은 계속 거절한다."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_topic_transfer import SOURCE, TARGET, TOPIC, STAMP, RawStore, seed, insert, plan
from test_topic_transfer_boundaries import add_row, assert_blocked
from store.embedded_schema import render_embedded_ddl
from store.scoped_topic_inventory import read_scoped_inventory
from store.topic_transfer import (fingerprint, read_snapshot, transformed_snapshot,
                                  plan_transfer, retained_history_counts, _apply_rehearsal)


def history(data):
    add_row(data, "_Tombstone", node_id=SOURCE + "\x1fconcept:deleted", owner_id=SOURCE,
            label="Concept", deleted_at="2020-01-01", rev=7, sync_seq=8, origin="original")
    add_row(data, "_DocTombstone", doc_id="deleted-document", owner_id=SOURCE,
            deleted_at="2020-01-02", sync_seq=9)
    add_row(data, "_EdgeTombstone", ekey="concept:gone|DERIVES_FROM|concept:other", owner_id=SOURCE,
            src="concept:gone", dst="concept:other", rel="DERIVES_FROM", deleted_at="2020-01-03",
            sync_seq=10, origin="original")


@pytest.mark.parametrize("props", [
    {"url": "https://example.invalid/private-paper?version=2"},
    {"sources": ["C:\\private\\paper.pdf", "https://example.invalid/paper"]},
    {"provenance": {"sourcepath": "/original/paper.pdf"}},
])
def test_citation_properties_preserved_exactly(props):
    data = seed()
    data["nodes"]["Record"][0]["props"] = json.dumps(props, ensure_ascii=False)
    chosen = plan(data)
    assert chosen.ready, chosen.blockers
    after = transformed_snapshot(data, chosen, stamp=STAMP)
    assert after["nodes"]["Record"][0]["props"] == data["nodes"]["Record"][0]["props"]


@pytest.mark.parametrize("props,reason", [
    ({"source": {"blob": "blob:private"}}, "external_attachments"),
    ({"url": "blob:private"}, "external_resource"),
    ({"unknown_payload": "az:private/container"}, "external_resource"),
    ({"attachments": ["blob:private/a.pdf"]}, "external_attachments"),
    ({"note": "<img src='blob:private'>"}, "external_attachments"),
    ({"note": "![managed](blob:a.png) ![web](https://example.invalid/b.png)"}, "external_attachments"),
])
def test_managed_resources_remain_guarded(props, reason):
    data = seed()
    data["nodes"]["Record"][0]["props"] = json.dumps(props)
    assert_blocked(data, reason)


# 근거: [[decision:topic-transfer-plain-references-and-external-images-2026-10-16]]
@pytest.mark.parametrize("props,expected", [
    ({"unknown_payload": "https://example.invalid/a.pdf"}, {}),
    ({"pr": "https://github.invalid/org/repo/pull/1", "path": "/api/sync.py",
      "local": "D:\\notes\\a.md", "notes_link": "file:notes"}, {}),
    ({"attachments": ["https://example.invalid/a.pdf"]}, {"external_attachments": 1}),
    ({"note": "<img src='https://example.invalid/a.png'>"}, {"external_attachments": 1}),
    ({"note": "![web](https://example.invalid/b.png)"}, {"external_attachments": 1}),
    # 근거: [[decision:topic-transfer-moves-unmanaged-attachments-as-text-2026-10-16]]
    ({"attachments": ["C:\\private\\a.pdf"]}, {"external_attachments": 1}),
    ({"files": ["store/topic_transfer.py", "api/sync.py"]}, {"external_attachments": 1}),
    ({"files": 3}, {"external_attachments": 1}),
    ({"filePath": "relative/synthetic.pdf"}, {"external_attachments": 1}),
    ({"assets": ["file:synthetic.png"]}, {"external_attachments": 1}),
    ({"note": "![local](./a.png) ![web](https://example.invalid/b.png)"}, {"external_attachments": 1}),
    ({"note": "<img alt='no source'>"}, {"external_attachments": 1}),
    ({"note": "![inline](data:image/png;base64,AAAA)"}, {"external_attachments": 1}),
    ({"ref":"concept:outside", "see": "[[" + "decision:elsewhere" + "]]"}, {"external_references": 2}),
])
def test_plain_strings_external_images_and_outside_ids_are_preserved(props, expected):
    from store.topic_transfer import preserved_reference_counts
    data = seed()
    data["nodes"]["Record"][0]["props"] = json.dumps(props, ensure_ascii=False)
    chosen = plan(data)
    assert chosen.ready, chosen.blockers
    assert preserved_reference_counts(chosen) == {"external_references": 0, "external_attachments": 0, **expected}
    after = transformed_snapshot(data, chosen, stamp=STAMP)
    assert after["nodes"]["Record"][0]["props"] == data["nodes"]["Record"][0]["props"]


def test_native_personal_history_exact_full_scoped_after_and_retry(tmp_path, monkeypatch):
    store = RawStore(tmp_path / "history.db")
    try:
        for ddl in render_embedded_ddl(dim=4):
            store.run(ddl)
        data = seed()
        history(data)
        insert(store, data)
        before = read_snapshot(store)
        scoped = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC)
        chosen = plan_transfer(scoped.snapshot, source=SOURCE, target=TARGET, topic=TOPIC)
        assert chosen.ready and not scoped.blockers, (chosen.blockers, scoped.blockers)
        assert retained_history_counts(scoped.snapshot, chosen) == {
            "_Tombstone": 1, "_EdgeTombstone": 1, "_DocTombstone": 1}
        expected_scoped = transformed_snapshot(scoped.snapshot, chosen, stamp=STAMP)
        expected = transformed_snapshot(before, plan(before), stamp=STAMP)
        assert _apply_rehearsal(store, plan(before), run_id="history-policy", stamp=STAMP) == "committed"
        assert _apply_rehearsal(store, plan(before), run_id="history-policy", stamp=STAMP) == "already_committed"
        observed = read_snapshot(store)
        observed["meta"]["_SyncMeta"] = [r for r in observed["meta"]["_SyncMeta"]
                                           if not r["key"].startswith("topic-transfer:run:")]
        assert fingerprint(observed) == fingerprint(expected)
        after = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC, after=True)
        assert fingerprint(after.snapshot) == fingerprint(expected_scoped)
        for label in ("_Tombstone", "_DocTombstone", "_EdgeTombstone"):
            assert all(row in observed["meta"][label] for row in before["meta"][label])
            assert not any(row["owner_id"] == TARGET for row in observed["meta"][label])
        from store import pkns
        from api import sync
        monkeypatch.setattr(pkns, "enabled", lambda: True)
        replay = sync.apply_delta(owner=SOURCE, store=store, bundle={"nodes": [
            {"id": "concept:deleted", "type": "concept", "title": "Stale",
             "_updated_at": "2019-01-01", "_rev": 1}]})
        assert replay["kept_deleted"] == 1 and replay["applied"] == 0
        moved = sync.apply_delta(owner=SOURCE, store=store, bundle={"nodes": [
            {"id": "concept:a", "type": "concept", "title": "Stale moved"}]})
        assert moved["error"] == "ownership_moved"
        from api import docsync
        def forbidden_recreate(*args, **kwargs):
            pytest.fail("stale deleted document reached put_doc")
        monkeypatch.setattr(docsync.ds, "put_doc", forbidden_recreate)
        stale_doc = docsync.import_docs(store, owner=SOURCE, payload={"docs": [
            {"doc_id": "deleted-document", "text": "Old deleted body", "updated_at": "2019-01-01"}]})
        assert stale_doc["skipped"] == 1 and stale_doc["applied"] == 0
    finally:
        store.close()


def test_retained_relationship_deletion_filters_stale_wire_replay(tmp_path, monkeypatch):
    from api import sync
    store = RawStore(tmp_path / "edge-history.db")
    try:
        for ddl in render_embedded_ddl(dim=4):
            store.run(ddl)
        data = seed()
        history(data)
        insert(store, data)
        sent = []
        monkeypatch.setattr(sync.frag, "merge_fragment", lambda payload, **kw:
                            sent.extend(payload["edges"]) or {"executed": True})
        sync.apply_delta(owner=SOURCE, store=store, bundle={"edges": [
            {"from": "concept:gone", "to": "concept:other", "type": "derives_from"}]})
        assert sent == []
        edge = {"from": "concept:gone", "to": "concept:other", "type": "derives_from"}
        assert sync._edges_after_deletions(store, SOURCE, {"edges": [edge], "nodes": [
            {"id": nid, "_updated_at": "2021-01-01"} for nid in (edge["from"], edge["to"])]}) == [edge]
        assert sync._edges_after_deletions(store, TARGET, {"edges": [edge]}) == [edge]
    finally:
        store.close()


# 근거: [[decision:topic-transfer-edge-history-and-foreign-rejection-endpoints-2026-10-16]]
def edge_between_selected(data, ekey):
    add_row(data, "_EdgeTombstone", ekey=ekey, owner_id=SOURCE, src="concept:a", dst="concept:b",
            rel="DERIVES_FROM", deleted_at="2020-01-03", sync_seq=10, origin="original")


def test_edge_deletion_between_selected_nodes_stays_personal(tmp_path):
    data = seed()
    edge_between_selected(data, SOURCE + "\x1fconcept:a|DERIVES_FROM|concept:b")
    chosen = plan(data)
    assert chosen.ready, chosen.blockers
    assert retained_history_counts(data, chosen)["_EdgeTombstone"] == 1
    after = transformed_snapshot(data, chosen, stamp=STAMP)
    assert after["meta"]["_EdgeTombstone"] == data["meta"]["_EdgeTombstone"]
    store = RawStore(tmp_path / "edge-between.db")
    try:
        for ddl in render_embedded_ddl(dim=4):
            store.run(ddl)
        insert(store, data)
        before = read_snapshot(store)
        scoped = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC)
        assert not scoped.blockers, scoped.blockers
        assert _apply_rehearsal(store, plan(before), run_id="edge-between", stamp=STAMP) == "committed"
        observed = read_snapshot(store)["meta"]["_EdgeTombstone"]
        assert observed == before["meta"]["_EdgeTombstone"]
    finally:
        store.close()


def test_legacy_edge_deletion_key_between_selected_nodes_stays_blocked():
    data = seed()
    edge_between_selected(data, "concept:a|DERIVES_FROM|concept:b")
    assert_blocked(data, "existing_tombstone_history_not_supported:_EdgeTombstone")
