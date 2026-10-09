from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from api.mirror_status import MAX_RESULT_BYTES, read_status
from scripts.check_node_mirror import observe


def result_files(base: Path, body, code="200"):
    Path(str(base) + ".code").write_text(code, encoding="utf-8")
    Path(str(base) + ".out").write_text(json.dumps(body), encoding="utf-8")


def test_completion_is_durable_and_hides_source_ids_and_raw_errors(tmp_path):
    base = tmp_path / "result"
    result_files(base, {"kind": "node-mirror.rebuild", "executed": True, "index_ok": True,
                        "owners": ["user:private"], "id_collisions": ["concept:private"],
                        "error": "secret raw error", "rows": 123})
    assert read_status(base, enabled=True) == {"state": "complete", "index_ok": True}
    assert read_status(base, enabled=True) == {"state": "complete", "index_ok": True}


def test_pending_disabled_and_partial_body_are_distinct(tmp_path):
    base = tmp_path / "result"
    assert read_status(base, enabled=False) == {"state": "disabled"}
    assert read_status(base, enabled=True) == {"state": "pending"}
    Path(str(base) + ".code").write_text("", encoding="utf-8")
    Path(str(base) + ".out").write_text("{incomplete", encoding="utf-8")
    assert read_status(base, enabled=True) == {"state": "pending"}


def test_verified_unchanged_index_is_current_without_rebuild(tmp_path):
    base = tmp_path / "result"
    result_files(base, {"kind": "node-mirror.rebuild", "executed": False,
                        "unchanged": True, "index_ok": True, "owners": ["user:private"]})
    assert read_status(base, enabled=True) == {"state": "current", "index_ok": True}


@pytest.mark.parametrize("body,code,reason", [
    ({"kind": "other"}, "200", "invalid_result"),
    ({"kind": "node-mirror.rebuild", "executed": False}, "200", "not_executed"),
    ({"kind": "node-mirror.rebuild", "executed": False, "index_ok": True}, "200", "not_executed"),
    ({"kind": "node-mirror.rebuild", "executed": False, "unchanged": True, "index_ok": False}, "200", "not_executed"),
    ({"kind": "node-mirror.rebuild", "executed": False, "unchanged": 1, "index_ok": True}, "200", "not_executed"),
    ({"kind": "node-mirror.rebuild", "executed": True, "index_ok": False}, "200", "index_unavailable"),
    ({"error": "private raw upstream body"}, "503", "request_failed"),
])
def test_failure_cannot_look_complete(tmp_path, body, code, reason):
    base = tmp_path / "result"
    result_files(base, body, code)
    assert read_status(base, enabled=True) == {"state": "failed", "reason": reason}


def test_invalid_or_oversized_body_is_not_exposed(tmp_path):
    base = tmp_path / "result"
    Path(str(base) + ".code").write_text("200", encoding="utf-8")
    Path(str(base) + ".out").write_text("private not-json", encoding="utf-8")
    assert read_status(base, enabled=True)["reason"] == "unreadable_result"
    Path(str(base) + ".out").write_bytes(b"x" * (MAX_RESULT_BYTES + 1))
    assert read_status(base, enabled=True)["reason"] == "result_over_limit"


@pytest.mark.parametrize("state,expected", [
    ({"state": "complete", "index_ok": True}, 0),
    ({"state": "current", "index_ok": True}, 0),
    ({"state": "current", "index_ok": False}, 1),
    ({"state": "disabled"}, 0),
    ({"state": "failed", "reason": "private raw upstream body"}, 1),
])
def test_observer_terminates_without_printing_raw_health(state, expected):
    response = io.BytesIO(json.dumps({"node_mirror": state, "owners": ["user:private"]}).encode())
    output = io.StringIO()
    with patch("urllib.request.urlopen", return_value=response) as get, redirect_stdout(output):
        assert observe("https://example.invalid/health") == expected
    assert get.call_count == 1
    assert "private" not in output.getvalue()


def test_pending_observer_uses_elapsed_deadline_and_never_rebuilds():
    response = lambda *a, **k: io.BytesIO(b'{"node_mirror":{"state":"pending"}}')
    with patch("urllib.request.urlopen", side_effect=response) as get, \
            patch("time.monotonic", side_effect=[0, 0, 0, 1, 3]), patch("time.sleep"):
        assert observe("https://example.invalid/health", timeout=2, interval=20) == 1
    assert get.call_args.args == ("https://example.invalid/health",)


def test_startup_health_without_mirror_waits_for_verified_completion():
    responses = [io.BytesIO(b'{"status":"ok"}'),
                 io.BytesIO(b'{"node_mirror":{"state":"complete","index_ok":true}}')]
    with patch("urllib.request.urlopen", side_effect=responses) as get, patch("time.sleep"):
        assert observe("https://example.invalid/health") == 0
    assert get.call_count == 2


def test_unavailable_startup_status_still_has_a_deadline():
    response = lambda *a, **k: io.BytesIO(b'{"status":"ok"}')
    with patch("urllib.request.urlopen", side_effect=response) as get, \
            patch("time.monotonic", side_effect=[0, 0, 0, 1, 3]), patch("time.sleep"):
        assert observe("https://example.invalid/health", timeout=2, interval=20) == 1
    assert get.call_count == 1


def test_temporary_health_timeout_does_not_skip_completion_check():
    complete = io.BytesIO(b'{"node_mirror":{"state":"current","index_ok":true}}')
    with patch("urllib.request.urlopen", side_effect=[TimeoutError(), complete]) as get, patch("time.sleep"):
        assert observe("https://example.invalid/health") == 0
    assert get.call_count == 2
