"""Disk inventory must retain the canonical graph contract and multiset."""
import copy
import json

import pytest

from test_topic_transfer import SOURCE, TARGET, TOPIC, native, seed
from store.topic_manifest import TopicManifest, ManifestLimit
from store.topic_transfer import TransferRefused, fingerprint, plan_transfer, _canonical
from store.scoped_topic_inventory import read_scoped_inventory


def fill(manifest, snapshot):
    for group in ("nodes", "meta"):
        for label, rows in snapshot[group].items():
            for row in reversed(rows):
                manifest.snapshot[group][label].append(row)
    for edge in reversed(snapshot["edges"]):
        manifest.snapshot["edges"].append(edge)


def test_canonical_payload_and_thin_seal(tmp_path):
    original = seed()
    with TopicManifest(tmp_path / "scratch.sqlite") as manifest:
        fill(manifest, original)
        assert fingerprint(manifest.snapshot) == fingerprint(original)
        assert manifest.serialized_bytes() == len(_canonical(original).encode())
        actual = plan_transfer(manifest.snapshot, source=SOURCE, target=TARGET, topic=TOPIC)
        expected = plan_transfer(original, source=SOURCE, target=TARGET, topic=TOPIC)
        assert actual == expected
        sealed = tmp_path / "manifest.jsonl"
        binding = {"source": SOURCE, "target": TARGET, "topic": TOPIC}
        result = manifest.seal(sealed, binding=binding)
        header = TopicManifest.verify_seal(sealed, result["sha256"], binding=binding)
        assert header["snapshot_hash"] == fingerprint(original)
        for line in sealed.read_text().splitlines()[1:]:
            assert set(json.loads(line)) <= {"kind", "label", "key", "digest", "ordinal"}
        with pytest.raises(FileExistsError):
            manifest.seal(sealed, binding=binding)
        with pytest.raises(TransferRefused, match="conversion_manifest_invalid"):
            TopicManifest.verify_seal(sealed, result["sha256"], binding={})
        sealed.write_bytes(sealed.read_bytes()[:-4])
        with pytest.raises(TransferRefused, match="conversion_manifest_invalid"):
            TopicManifest.verify_seal(sealed, result["sha256"], binding=binding)


def test_parallel_edges_affect_digest(tmp_path):
    snapshot = seed()
    with TopicManifest(tmp_path / "scratch.sqlite") as manifest:
        fill(manifest, snapshot)
        before = manifest.fingerprint()
        manifest.add_edge(copy.deepcopy(snapshot["edges"][0]))
        snapshot["edges"].append(copy.deepcopy(snapshot["edges"][0]))
        assert before != manifest.fingerprint() == fingerprint(snapshot)


def test_limits_are_explicit_and_scratch_is_exclusive(tmp_path):
    path = tmp_path / "scratch.sqlite"
    with TopicManifest(path, max_rows=1) as manifest:
        manifest.add("Concept", seed()["nodes"]["Concept"][0])
        with pytest.raises(ManifestLimit) as error:
            manifest.add("Concept", seed()["nodes"]["Concept"][1])
        assert error.value.reason == "retained_rows"
        assert error.value.details == {"observed": 2, "limit": 1}
        with pytest.raises(FileExistsError):
            TopicManifest(path)


def test_scoped_disk_inventory_equals_legacy(native, tmp_path):
    legacy = read_scoped_inventory(native, source=SOURCE, target=TARGET, topic=TOPIC)
    with TopicManifest(tmp_path / "scratch.sqlite") as manifest:
        disk = read_scoped_inventory(native, source=SOURCE, target=TARGET, topic=TOPIC,
                                     manifest=manifest)
        assert disk.blockers == legacy.blockers
        assert disk.retained_rows == legacy.retained_rows
        assert disk.retained_bytes == legacy.retained_bytes
        assert fingerprint(disk.snapshot) == fingerprint(legacy.snapshot)


def test_disk_inventory_survives_legacy_row_budget(native, tmp_path, monkeypatch):
    import store.scoped_topic_inventory as reader
    monkeypatch.setattr(reader, "MAX_INVENTORY_ROWS", 2)
    with pytest.raises(TransferRefused, match="conversion_inventory_limit"):
        read_scoped_inventory(native, source=SOURCE, target=TARGET, topic=TOPIC)
    with TopicManifest(tmp_path / "scratch.sqlite") as manifest:
        disk = read_scoped_inventory(native, source=SOURCE, target=TARGET, topic=TOPIC,
                                     manifest=manifest)
        assert disk.retained_rows > 2


def test_real_legacy_row_and_byte_thresholds_are_exceeded_without_materialization(tmp_path):
    base = seed()["nodes"]["Concept"][0]
    with TopicManifest(tmp_path / "scratch.sqlite") as manifest:
        for index in range(5001):
            row = {**base, "concept_id": SOURCE + "\x1fconcept:large-" + str(index),
                   "one_liner": "synthetic payload " + "x" * 7000}
            manifest.add("Concept", row)
        assert manifest.retained_rows > 5000
        assert manifest.payload_bytes > 32 * 1024 * 1024
        plan = plan_transfer(manifest.snapshot, source=SOURCE, target=TARGET, topic=TOPIC)
        assert plan.ready and len(plan.selected) == 5001
        assert len(plan.before_hash) == 64


def test_pinned_engine_primary_key_update_refused_and_rollback_keeps_edges(native):
    before = fingerprint(read_scoped_inventory(native, source=SOURCE, target=TARGET, topic=TOPIC).snapshot)
    native.run("BEGIN TRANSACTION")
    try:
        native.run("MATCH (n:Concept) WHERE n.concept_id=$old SET n.title='uncommitted'",
                   {"old": SOURCE + "\x1fconcept:a"})
        with pytest.raises(RuntimeError, match="primary key"):
            native.run("MATCH (n:Concept) WHERE n.concept_id=$old SET n.concept_id=$new",
                       {"old": SOURCE + "\x1fconcept:a", "new": TARGET + "\x1fconcept:a"})
    finally:
        try:
            native.run("ROLLBACK")
        except RuntimeError as error:
            if "No active transaction" not in str(error):
                raise
    after = fingerprint(read_scoped_inventory(native, source=SOURCE, target=TARGET, topic=TOPIC).snapshot)
    assert after == before
