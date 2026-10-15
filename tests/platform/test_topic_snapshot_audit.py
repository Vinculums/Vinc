"""격리 사본 조사는 불변성과 출력 비공개 경계를 지켜야 한다."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from test_topic_transfer import RawStore, SOURCE, seed, insert
from store.embedded_schema import render_embedded_ddl
from store.topic_transfer import fingerprint, read_snapshot
from scripts.qa.topic_snapshot_audit import audit, main


def test_audit_preserves_all_rows_and_redacts_identifiers(tmp_path):
    store = RawStore(tmp_path / "synthetic.db")
    try:
        for statement in render_embedded_ddl(dim=4):
            store.run(statement)
        snapshot = seed()
        insert(store, snapshot)
        before = fingerprint(read_snapshot(store))
        report = audit(store, SOURCE, "vinc")
        assert report["complete"] and not report["transfer_qualified"]
        assert report["documents"] == 1
        encoded = json.dumps(report)
        for forbidden in (SOURCE, "concept:a", "concept:b", "doc-one", "fingerprint", "inventory_hash"):
            assert forbidden not in encoded
        assert fingerprint(read_snapshot(store)) == before
    finally:
        store.close()


def test_cli_refuses_non_runner_and_invalid_owner(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert main() == 2
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("AUDIT_OWNER_UUID", "private-error-must-not-appear")
    assert main() == 1
    report = json.loads((tmp_path / "topic-audit-report.json").read_text())
    assert report == {"complete": False, "diagnostic_only": True, "error": "audit_failed"}


def test_workflow_has_no_live_mutation_or_raw_artifact():
    import yaml
    path = Path(__file__).resolve().parents[2] / ".github/workflows/topic-snapshot-audit.yml"
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["concurrency"] == {"group": "hub-deploy", "cancel-in-progress": False}
    assert data["jobs"]["audit"]["timeout-minutes"] == 30
    assert "refs/heads/main" in text and "PROD_OPERATORS" in text
    assert "upload-artifact" not in text and "containerapp update" not in text
    assert "revision deactivate" not in text and "storage file upload" not in text
    assert '--snapshot "$SNAPSHOT"' in text
    assert "VINC_PK_NAMESPACED" in text and ".themes.json" in text
