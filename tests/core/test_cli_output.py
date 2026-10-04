"""CLI stdout and status are shell contracts."""
from __future__ import annotations

import json
import subprocess
import sys

from api import cli, cli_output


def test_json_happy_path_keeps_the_old_bytes(capsys):
    assert cli.main(["plan", "search", "--format", "json"]) == 0
    got = capsys.readouterr()
    expected = {"kind": "search", "arms": ["fulltext(BM25)", "vector(주입 시)"],
                "fusion": "RRF(k=60)", "note": "store 메서드 경유 — 단일 Cypher 아님"}
    assert got.out == json.dumps(expected, ensure_ascii=False, indent=2) + "\n"
    assert got.err == ""


def test_root_format_precedes_the_verb(capsys):
    assert cli.main(["--format", "json", "plan", "search"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "search"


def test_default_format_follows_stdout_tty(monkeypatch):
    class _Stdout:
        def __init__(self, tty):
            self.tty = tty
        def isatty(self):
            return self.tty

    monkeypatch.delenv("VINC_FORMAT", raising=False)
    cli_output.configure(None, "search")
    monkeypatch.setattr(cli_output.sys, "stdout", _Stdout(True))
    assert cli_output.chosen_format() == "human"
    monkeypatch.setattr(cli_output.sys, "stdout", _Stdout(False))
    assert cli_output.chosen_format() == "json"


def test_failures_use_a_nonzero_status_without_traceback(tmp_path, capsys):
    assert cli.main(["import", "--pack", str(tmp_path / "missing"),
                     "--owner", "user:local"]) == 1
    got = capsys.readouterr()
    assert got.out == ""
    assert json.loads(got.err)["error"]["code"] == "internal"
    assert "Traceback" not in got.err


def test_usage_is_two_and_only_on_stderr(capsys):
    assert cli.main(["search"]) == 2
    got = capsys.readouterr()
    assert got.out == ""
    assert "required" in got.err


def test_ndjson_emits_one_row_per_line(capsys):
    cli_output.configure("ndjson", "search")
    assert cli_output.emit({"rows": [{"id": "a"}, {"id": "b"}], "next_cursor": "x"}) == 0
    assert capsys.readouterr().out == '{"id": "a"}\n{"id": "b"}\n'


def test_human_failure_is_on_stderr(capsys):
    cli_output.configure("human", "doc ls")
    assert cli_output.emit({"error": "store_in_use"}) == 1
    got = capsys.readouterr()
    assert got.out == ""
    assert got.err == "vinc: store_in_use\n"


def test_store_open_diagnostic_never_corrupts_json(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "api.cli", "--target", "local", "doc", "ls", "--data", str(tmp_path)],
        text=True, encoding="utf-8", capture_output=True, check=False,
    )
    assert proc.returncode == 1
    assert proc.stdout == ""
    result = json.loads(proc.stderr)
    assert result["error"]
    assert "Database path cannot be a directory" in result["open_error"]
    assert "[embedded_backend]" not in proc.stderr
