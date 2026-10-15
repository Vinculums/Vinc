"""오래된 초록색 런이나 다른 SHA를 사본 실행 자격으로 쓰지 않는다."""
import copy
from pathlib import Path
import subprocess

import pytest
import yaml

from scripts.qa.verify_rehearsal_source import REQUIRED, verify

SHA = "a" * 40


def baseline():
    return {"total_count": 2, "workflow_runs": [
        {"id": index + 1, "head_sha": SHA, "path": path, "event": "pull_request",
         "status": "completed", "conclusion": "success"}
        for index, path in enumerate(REQUIRED)]}


def test_exact_head_requires_both_latest_successful_runs():
    assert len(verify(baseline(), SHA)) == 2
    data = baseline()
    data["workflow_runs"].append({**data["workflow_runs"][0], "id": 3, "conclusion": "failure"})
    data["total_count"] = 3
    with pytest.raises(ValueError, match="not_successful"):
        verify(data, SHA)


@pytest.mark.parametrize("change", ["wrong_sha", "missing", "pending", "skipped", "truncated", "manual"])
def test_incomplete_or_unqualified_evidence_refuses(change):
    data = copy.deepcopy(baseline())
    item = data["workflow_runs"][0]
    if change == "wrong_sha": item["head_sha"] = "b" * 40
    if change == "missing": data["workflow_runs"].pop(); data["total_count"] = 1
    if change == "pending": item["status"] = "in_progress"
    if change == "skipped": item["conclusion"] = "skipped"
    if change == "truncated": data["total_count"] = 101
    if change == "manual": item["event"] = "workflow_dispatch"
    with pytest.raises(ValueError): verify(data, SHA)


def test_entrypoint_checks_before_cloud_access_and_has_cleanup():
    path = Path(__file__).resolve().parents[2] / ".github/workflows/topic-copy-rehearsal.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    job = workflow["jobs"]["audit"]
    steps = job["steps"]
    guard = next(i for i, step in enumerate(steps) if step.get("name", "").startswith("Verify exact"))
    login = next(i for i, step in enumerate(steps) if step.get("uses", "").startswith("azure/login"))
    assert guard < login
    text = path.read_text(encoding="utf-8-sig")
    assert 'qualified-source,dst=/src,readonly' in text
    assert '--network none --cpus 1 --memory 2g --memory-swap 2g' in text
    assert "upload-artifact" not in text and "containerapp update" not in text
    assert "REHEARSE PERSONAL VINC COPY" in text and "refs/heads/main" in text
    for step in steps:
        if step.get("name", "").startswith(("Remove", "Stop")):
            assert "always()" in step["if"]
        if "run" in step:
            subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
