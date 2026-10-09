"""Operator recovery must retain ACKs newer than the interval snapshot."""
from pathlib import Path

import pytest

from scripts.restore_hub_snapshot import restore_downloaded
from store import durable_generation as dg


def seeded(tmp_path):
    snapshot = tmp_path / "downloaded" / "db"
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"OLD-SNAPSHOT")
    base, wal = tmp_path / "base", tmp_path / "wal"
    base.write_bytes(b"ACK-BASE")
    wal.write_bytes(b"ACK-WAL")
    root = Path(f"{snapshot}.generations")
    dg.publish(root, base, sequence=1, wal=wal)
    return snapshot, root


def test_operator_recovers_newer_ack_instead_of_interval_snapshot(tmp_path):
    snapshot, _ = seeded(tmp_path)
    out = tmp_path / "working" / "db"
    report = restore_downloaded(snapshot, out, durable_ack=True)
    assert report["state"] == "restored-generation:1"
    assert out.read_bytes() == b"ACK-BASE"
    assert Path(f"{out}.wal").read_bytes() == b"ACK-WAL"
    assert snapshot.read_bytes() == b"OLD-SNAPSHOT"


def test_operator_refuses_corrupt_latest_ack_without_old_snapshot_fallback(tmp_path):
    snapshot, root = seeded(tmp_path)
    (root / "generation-1" / "db.wal").write_bytes(b"CORRUPT")
    out = tmp_path / "working" / "db"
    with pytest.raises(RuntimeError, match="WAL digest mismatch"):
        restore_downloaded(snapshot, out, durable_ack=True)
    assert not out.exists()


def test_snapshot_covering_committed_ack_remains_valid(tmp_path):
    snapshot, root = seeded(tmp_path)
    snapshot.write_bytes(b"SNAPSHOT-COVERS-ACK")
    Path(f"{snapshot}.generation-seen").write_text("1\n")
    out = tmp_path / "working" / "db"
    assert restore_downloaded(snapshot, out, durable_ack=True)["state"] == "restored"
    assert out.read_bytes() == b"SNAPSHOT-COVERS-ACK"


def test_existing_operator_destination_is_never_replaced(tmp_path):
    snapshot, _ = seeded(tmp_path)
    out = tmp_path / "existing"
    out.write_bytes(b"DO-NOT-REPLACE")
    with pytest.raises(RuntimeError, match="already exists"):
        restore_downloaded(snapshot, out, durable_ack=True)
    assert out.read_bytes() == b"DO-NOT-REPLACE"
