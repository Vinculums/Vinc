"""격리 사본 하네스 자체를 합성 네이티브 DB로 검증한다."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_topic_transfer import RawStore, seed, insert, SOURCE, TOPIC
from test_topic_reference_history_policy import history
from test_topic_rejection_history import rejection
from store.embedded_schema import render_embedded_ddl
from scripts.qa.topic_copy_rehearsal import rehearse, main


def test_copy_rehearsal_preserves_original_and_resumes_production_publication(tmp_path, monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    monkeypatch.setenv("VINC_TOPIC_CONVERSION", "1")
    monkeypatch.setenv("VINC_DURABLE_ACK", "off")
    original = tmp_path / "original.db"
    store = RawStore(original)
    try:
        for ddl in render_embedded_ddl(dim=4):
            store.run(ddl)
        data = json.loads(json.dumps(seed()).replace("doc1", "graph.md"))
        history(data)
        rejection(data, "concept:a", "concept:absent")
        insert(store, data)
    finally:
        store.close()
    result = rehearse(original, tmp_path / "execution", SOURCE, topic=TOPIC, max_rows=4)
    assert result["qualified"] and result["original_copy_unchanged"]
    assert result["missing_local_recovery"] and result["other_owned_rows_unchanged"]
    assert result["retained_personal_history"]["_MergeRejection"] == 1
    assert result["status"] == "active" and result["account_activation"] == "simulated"
    assert SOURCE not in json.dumps(result)
    with pytest.raises(ValueError, match="fresh_copy_required"):
        rehearse(original, tmp_path / "execution", SOURCE, topic=TOPIC)


def test_missing_explicit_runner_authority_refuses_before_open(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("VINC_COPY_REHEARSAL", raising=False)
    assert main() == 2


def test_synthetic_workflow_has_no_production_access():
    import yaml
    path = Path(__file__).resolve().parents[2] / ".github/workflows/topic-copy-smoke.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    job = workflow["jobs"]["synthetic"]
    steps = {step["name"]: step for step in job["steps"] if "name" in step}
    run = steps["Build and verify only synthetic data in the execution container"]["run"]
    assert "--network none" in run and "--memory 2g --memory-swap 2g" in run
    assert "--cpus 1" in run and "--read-only" in run
    assert "secrets." not in run and "AZURE_CONFIG_DIR" not in run
    assert workflow["permissions"] == {"contents": "read"}
    assert "azure/login" not in path.read_text() and "storage share" not in path.read_text()


def test_failed_rehearsal_names_its_stage_without_content(tmp_path, monkeypatch):
    from api.topic_conversion_batches import Batches
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    monkeypatch.setenv("VINC_TOPIC_CONVERSION", "1")
    monkeypatch.setenv("VINC_DURABLE_ACK", "off")
    original = tmp_path / "original.db"
    store = RawStore(original)
    try:
        for ddl in render_embedded_ddl(dim=4):
            store.run(ddl)
        insert(store, seed())
    finally:
        store.close()
    def broken(self, *args, **kwargs):
        raise RuntimeError("synthetic_step_failure:" + SOURCE)
    monkeypatch.setattr(Batches, "step", broken)
    progress = {}
    with pytest.raises(RuntimeError):
        rehearse(original, tmp_path / "execution", SOURCE, topic=TOPIC, max_rows=4, progress=progress)
    assert progress == {"failed_stage": "first_step"}


def test_failure_fields_carry_the_table_and_only_shape_checked_detail():
    from scripts.qa.topic_copy_rehearsal import failure_fields
    from store.topic_transfer import TransferRefused
    exc = TransferRefused("conversion_payload_changed:_SyncMeta")
    exc.detail = {"counter": {"side": "source", "delta": 3}}
    assert failure_fields(exc) == {"exception": "TransferRefused", "error_code": "conversion_payload_changed",
                                   "error_table": "_SyncMeta",
                                   "error_detail": {"counter": {"side": "source", "delta": 3}}}
    exc = TransferRefused("conversion_payload_changed:Concept")
    exc.detail = {"missing": False, "counter": {"side": SOURCE, "delta": "1"}}
    assert failure_fields(exc)["error_detail"] == {"row_missing": False}
    exc.detail = {"missing": SOURCE}
    assert "error_detail" not in failure_fields(exc)
    assert SOURCE not in json.dumps(failure_fields(exc))
