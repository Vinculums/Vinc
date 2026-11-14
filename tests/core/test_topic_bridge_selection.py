"""브리지의 연결 범위와 소유권을 완전히 확인한 경우에만 이동한다."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_topic_transfer import SOURCE, TARGET, TOPIC, STAMP, SEP, RawStore, seed, insert, plan
from store.embedded_schema import render_embedded_ddl, rel_table_specs
from store.scoped_topic_inventory import read_scoped_inventory
from store.topic_transfer import (fingerprint, read_snapshot,
                                  transformed_snapshot, plan_transfer)
from topic_rehearsal import apply_rehearsal  # noqa: E402


def connected_bridge():
    data = seed()
    bridge = data["nodes"]["Bridge"][0]
    bridge.update(source_domain="", target_domain="")
    for label, target, role in (("Concept", "concept:a", "left"),
                                ("Concept", "concept:b", "right"),
                                ("Decision", "decision:d", "evidence")):
        spec = next(r for r in rel_table_specs() if (r["label"], r["from"], r["to"]) ==
                    ("CONNECTS", "Bridge", label))
        props = {key: None for key in spec["props"]}
        props["role"] = role
        data["edges"].append({"label": "CONNECTS", "from_label": "Bridge", "to_label": label,
                              "from": bridge["bridge_id"], "to": SOURCE + SEP + target,
                              "props": props})
    data["edges"].append(copy.deepcopy(data["edges"][-1]))
    vector = copy.deepcopy(data["nodes"]["NodeVec"][0])
    vector.update(vec_id=SOURCE + "#bridge:inside", node_id="bridge:inside", kind="bridge")
    data["nodes"]["NodeVec"].append(vector)
    return data


def test_owned_bridge_derives_scope_without_changing_declared_coordinates():
    data = connected_bridge()
    before = copy.deepcopy(data)
    chosen = plan(data)
    assert chosen.ready, chosen.blockers
    assert ("Bridge", SOURCE + SEP + "bridge:inside") in chosen.selected
    after = transformed_snapshot(data, chosen, stamp=STAMP)
    assert data == before
    assert after["nodes"]["Bridge"][0]["source_domain"] == ""
    assert after["nodes"]["Bridge"][0]["target_domain"] == ""
    assert after["nodes"]["Bridge"][0]["owner_id"] == TARGET
    assert len([e for e in after["edges"] if e["label"] == "CONNECTS"]) == 4


@pytest.mark.parametrize("change", ["ownerless", "foreign", "outside", "unassigned", "declared_outside"])
def test_derived_membership_never_claims_owner_or_overrides_other_scope(change):
    data = connected_bridge()
    bridge = data["nodes"]["Bridge"][0]
    endpoint = data["nodes"]["Concept"][1]
    if change == "ownerless":
        bridge["owner_id"] = ""
    elif change == "foreign":
        endpoint["owner_id"] = "user:foreign"
    elif change == "outside":
        endpoint["domain"] = "personal/outside"
    elif change == "unassigned":
        endpoint["domain"] = ""
    else:
        bridge.update(source_domain="personal/outside", target_domain="personal/outside")
    chosen = plan(data)
    assert ("Bridge", bridge["bridge_id"]) not in chosen.selected
    assert not chosen.ready
    assert any(code.startswith("boundary_relation:") for code in chosen.blockers)


def test_native_bridge_scope_and_after_inventory_preserve_full_payload(tmp_path):
    store = RawStore(tmp_path / "bridge.db")
    try:
        for statement in render_embedded_ddl(dim=4):
            store.run(statement)
        insert(store, connected_bridge())
        before = read_snapshot(store)
        chosen = plan(before)
        scoped = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC)
        scoped_plan = plan_transfer(scoped.snapshot, source=SOURCE, target=TARGET, topic=TOPIC)
        assert not scoped.blockers
        assert scoped_plan.ready and set(scoped_plan.selected) == set(chosen.selected)
        expected = transformed_snapshot(before, chosen, stamp=STAMP)
        assert apply_rehearsal(store, chosen, run_id="derived-bridge-topic", stamp=STAMP) == "committed"
        actual = read_snapshot(store)
        actual["meta"]["_SyncMeta"] = [row for row in actual["meta"]["_SyncMeta"]
                                       if not row["key"].startswith("topic-transfer:run:")]
        assert fingerprint(actual) == fingerprint(expected)
        after = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC, after=True)
        assert len(after.snapshot["nodes"]["Bridge"]) == 1
    finally:
        store.close()


@pytest.mark.parametrize("change", ["ownerless", "foreign", "outside"])
def test_native_scoped_bridge_selection_refuses_ambiguous_membership(tmp_path, change):
    data = connected_bridge()
    if change == "ownerless":
        data["nodes"]["Bridge"][0]["owner_id"] = ""
    elif change == "foreign":
        data["nodes"]["Concept"][1]["owner_id"] = "user:foreign"
    else:
        data["nodes"]["Concept"][1]["domain"] = "personal/outside"
    store = RawStore(tmp_path / "refused.db")
    try:
        for statement in render_embedded_ddl(dim=4):
            store.run(statement)
        insert(store, data)
        before = fingerprint(read_snapshot(store))
        scoped = read_scoped_inventory(store, source=SOURCE, target=TARGET, topic=TOPIC)
        chosen = plan_transfer(scoped.snapshot, source=SOURCE, target=TARGET, topic=TOPIC)
        assert not chosen.ready
        assert ("Bridge", SOURCE + SEP + "bridge:inside") not in chosen.selected
        assert fingerprint(read_snapshot(store)) == before
    finally:
        store.close()
