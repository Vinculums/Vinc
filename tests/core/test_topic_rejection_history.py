"""기각 이력은 두 끝점의 범위를 증명한 경우에만 함께 옮긴다."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_topic_transfer import SOURCE, TARGET, TOPIC, STAMP, RawStore, seed, insert, plan
from test_topic_conversion import native, stage, RUN
from api import topic_conversion as tc
from api.query import list_merge_rejections
from store.embedded_schema import meta_table_specs, render_embedded_ddl
from store.scoped_topic_inventory import read_scoped_inventory
from store.topic_transfer import (_apply_rehearsal, fingerprint, plan_transfer,
                                  read_snapshot, transformed_snapshot, TransferRefused)


def rejection(data, a="concept:a", b="concept:b", owner=SOURCE, key=None):
    row = {column: None for column in meta_table_specs()["_MergeRejection"]["cols"]}
    row.update(pair_key=key or owner + "|" + "|".join(sorted((a, b))), owner_id=owner,
               a_id=a, b_id=b, note="Keep distinct: 원문과 판단 보존", rejected_at="2020-01-02T03:04:05Z")
    data["meta"]["_MergeRejection"].append(row)
    return row


def outside_rejection(data):
    other = copy.deepcopy(data["nodes"]["Concept"][-1])
    other["concept_id"] += "2"
    data["nodes"]["Concept"].append(other)
    return rejection(data, "concept:other", "concept:other2")


def test_selected_rejection_preserves_note_time_and_outside_history():
    data = seed()
    chosen = rejection(data)
    outside = outside_rejection(data)
    original = copy.deepcopy(data)
    selected = plan(data)
    assert selected.ready, selected.blockers
    after = transformed_snapshot(data, selected, stamp=STAMP)
    expected = {**chosen, "owner_id": TARGET,
                "pair_key": TARGET + "|concept:a|concept:b"}
    assert after["meta"]["_MergeRejection"] == [expected, outside]
    assert data == original


@pytest.mark.parametrize("a,b,key,code", [
    ("concept:a", "concept:other", None, "merge_rejection_history_not_supported"),
    ("concept:missing", "concept:other", None, "merge_rejection_topic_membership_unknown"),
    ("concept:a", "concept:b", "malformed", "unsupported_merge_rejection_key"),
    ("concept:a", "concept:a", None, "unsupported_merge_rejection_key"),
])
def test_ambiguous_and_malformed_history_refused(a, b, key, code):
    data = seed()
    rejection(data, a, b, key=key)
    assert code in plan(data).blockers


def test_native_history_full_scoped_after_and_retry(tmp_path):
    store = RawStore(tmp_path / "history.db")
    try:
        for statement in render_embedded_ddl(dim=4):
            store.run(statement)
        data = seed()
        rejection(data)
        outside_rejection(data)
        insert(store, data)
        before = read_snapshot(store)
        chosen = plan(before)
        scoped = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC)
        scoped_plan = plan_transfer(scoped.snapshot, source=SOURCE, target=TARGET, topic=TOPIC)
        assert not scoped.blockers and scoped_plan.ready
        assert set(scoped_plan.selected) == set(chosen.selected)
        expected = transformed_snapshot(before, chosen, stamp=STAMP)
        assert _apply_rehearsal(store, chosen, run_id="history", stamp=STAMP) == "committed"
        assert _apply_rehearsal(store, chosen, run_id="history", stamp=STAMP) == "already_committed"
        actual = read_snapshot(store)
        actual["meta"]["_SyncMeta"] = [r for r in actual["meta"]["_SyncMeta"]
                                       if not r["key"].startswith("topic-transfer:run:")]
        assert fingerprint(actual) == fingerprint(expected)
        after = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC, after=True)
        assert fingerprint(after.snapshot) == fingerprint(transformed_snapshot(scoped.snapshot, scoped_plan, stamp=STAMP))
    finally:
        store.close()


@pytest.mark.parametrize("direction", ["incoming", "outgoing"])
def test_history_note_references_do_not_expand_selection(direction):
    data = seed()
    row = outside_rejection(data) if direction == "incoming" else rejection(data)
    target = "concept:a" if direction == "incoming" else "concept:other"
    row["note"] = "[[" + target + "]]"
    prefix = "incoming_reference:" if direction == "incoming" else "external_reference:"
    assert any(code.startswith(prefix) for code in plan(data).blockers)


@pytest.mark.parametrize("phase", ["nodes", "delete", "journal"])
def test_native_history_failure_rolls_back_without_losing_rejection(tmp_path, phase):
    store = RawStore(tmp_path / "rollback.db")
    try:
        for statement in render_embedded_ddl(dim=4):
            store.run(statement)
        data = seed()
        rejection(data)
        insert(store, data)
        before = read_snapshot(store)
        with pytest.raises(RuntimeError):
            _apply_rehearsal(store, plan(before), run_id="history", stamp=STAMP, fail_at=phase)
        assert fingerprint(read_snapshot(store)) == fingerprint(before)
    finally:
        store.close()


def test_native_foreign_target_history_collision_refused(tmp_path):
    store = RawStore(tmp_path / "collision.db")
    try:
        for statement in render_embedded_ddl(dim=4):
            store.run(statement)
        data = seed()
        rejection(data)
        rejection(data, owner="user:foreign", key=TARGET + "|concept:a|concept:b")
        insert(store, data)
        before = fingerprint(read_snapshot(store))
        scoped = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC)
        assert "key_collision:_MergeRejection" in scoped.blockers
        assert fingerprint(read_snapshot(store)) == before
    finally:
        store.close()


def test_protocol_rejection_survives_retry_release_and_stale_personal_write_guard(native):
    data = seed()
    row = rejection(data)
    fields = ", ".join(f"n.{key}=${key}" for key in row if key != "pair_key")
    native.run("CREATE (n:_MergeRejection {pair_key:$pair_key}) SET " + fields, row)
    check = tc.preflight(native, SOURCE, TOPIC)
    assert check["ready"], check
    assert stage(native)["status"] == "graph_committed"
    for owner in (SOURCE, TARGET):
        with pytest.raises(TransferRefused, match="pending"):
            tc.check_read(native, owner)
    args = dict(source=SOURCE, target=TARGET, topic=TOPIC, run=RUN)
    assert tc.stage(native, **args, expected=check["fingerprint"], stamp=STAMP)["status"] == "graph_committed"
    assert tc.release(native, **args)["status"] == "active"
    tc.check_read(native, TARGET)
    assert list_merge_rejections(store=native, owner=SOURCE)["rejections"] == []
    assert list_merge_rejections(store=native, owner=TARGET)["rejections"] == [
        {key: row[key] for key in ("a_id", "b_id", "note", "rejected_at")}]
    payload = {"a": "concept:a", "b": "concept:b", "undo": True}
    with pytest.raises(TransferRefused, match="reference_moved_to_team"):
        tc.check_write(native, SOURCE, payload)
    tc.check_write(native, TARGET, payload)
