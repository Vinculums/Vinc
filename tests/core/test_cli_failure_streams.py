"""Failures leave the result stream empty in every console format."""
from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from api import cli, cli_output, cli_target, remote_client


@pytest.fixture(autouse=True)
def isolated_cli(monkeypatch):
    for name in ("VINC_API_KEY", "VINC_REMOTE_MCP_TOKEN", "VINC_FORMAT", "VINC_DEBUG",
                 "VINC_TARGET", "VINC_SPACE", "VINC_API_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli_target, "read_local_config", lambda: {})
    monkeypatch.setattr(remote_client, "urlopen", lambda *_a, **_k: pytest.fail("unexpected HTTP request"))


def assert_failure(capsys, fmt, machine_code):
    got = capsys.readouterr()
    assert got.out == ""
    assert "vinc_sk_synthetic" not in got.err
    assert "Traceback" not in got.err
    if fmt == "human":
        assert got.err.startswith("vinc: ")
        assert len(got.err.splitlines()) == 1
        return got.err
    error = json.loads(got.err)["error"]
    assert error["code"] == machine_code
    if fmt == "ndjson":
        assert len(got.err.splitlines()) == 1
    return error.get("message", "")


@pytest.mark.parametrize("fmt", ["human", "json", "ndjson"])
@pytest.mark.parametrize(("status", "code", "machine_code"), [
    (404, 1, "not_found"), (401, 4, "not_authenticated"),
    (503, 5, "upstream_unavailable"), (429, 6, "quota_exceeded"),
])
def test_http_failure_matrix(monkeypatch, capsys, fmt, status, code, machine_code):
    monkeypatch.setenv("VINC_API_KEY", "vinc_sk_synthetic")
    def fail(request, **_):
        assert request.get_method() == "GET"
        payload = {"error": {"code": machine_code, "message": "synthetic failure",
                             "request_id": "req_test", "details": {}}}
        raise HTTPError(request.full_url, status, "failed", {},
                        io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr(remote_client, "urlopen", fail)
    assert cli.main(["--target", "account", "--format", fmt, "whoami"]) == code
    assert_failure(capsys, fmt, machine_code)


@pytest.mark.parametrize("fmt", ["human", "json", "ndjson"])
def test_usage_completes_the_exit_format_matrix(capsys, fmt):
    assert cli.main(["--format", fmt, "search"]) == 2
    assert "required" in assert_failure(capsys, fmt, "invalid_argument")


@pytest.mark.parametrize("flags", [["--format", "ndjson"], ["--format=json"], ["--json"]])
def test_parser_errors_honor_options_after_the_verb(capsys, flags):
    assert cli.main(["search", *flags]) == 2
    assert_failure(capsys, "ndjson" if "ndjson" in flags else "json", "invalid_argument")


@pytest.mark.parametrize("fmt", ["human", "json", "ndjson"])
def test_parser_errors_honor_environment_and_guards(monkeypatch, capsys, fmt):
    monkeypatch.setenv("VINC_FORMAT", fmt)
    assert cli.main(["search"]) == 2
    assert_failure(capsys, fmt, "invalid_argument")
    assert cli.main(["--target", "account", "plan", "search"]) == 2
    assert_failure(capsys, fmt, "invalid_argument")
    assert cli.main(["--target", "local", "--space", "unreachable", "whoami"]) == 2
    assert_failure(capsys, fmt, "invalid_argument")


def test_invalid_format_still_exits_two_without_recursion(monkeypatch, capsys):
    monkeypatch.setenv("VINC_FORMAT", "invalid")
    assert cli.main(["plan", "search"]) == 2
    assert "VINC_FORMAT" in assert_failure(capsys, "human", "invalid_argument")


@pytest.mark.parametrize("fmt", ["human", "json", "ndjson"])
def test_missing_key_and_network_failures(monkeypatch, capsys, fmt):
    assert cli.main(["--format", fmt, "--target", "account", "whoami"]) == 4
    assert_failure(capsys, fmt, "not_authenticated")
    monkeypatch.setenv("VINC_API_KEY", "vinc_sk_synthetic")
    def fail(*_a, **_k):
        raise URLError("synthetic offline")
    monkeypatch.setattr(remote_client, "urlopen", fail)
    assert cli.main(["--format", fmt, "--target", "account", "whoami"]) == 5
    assert_failure(capsys, fmt, "upstream_unavailable")


@pytest.mark.parametrize("fmt", ["human", "json", "ndjson"])
@pytest.mark.parametrize("target", ["local", "account"])
@pytest.mark.parametrize("source", ["--args", "--args-file"])
def test_malformed_json_names_source_and_position(tmp_path, capsys, fmt, target, source):
    text = '{\n"query": }'
    if source == "--args-file":
        path = tmp_path / "args.json"
        path.write_text(text, encoding="utf-8")
        value = str(path)
    else:
        value = text
    assert cli.main(["--target", target, "--format", fmt,
                     "call", "vinc_search", source, value]) == 2
    message = assert_failure(capsys, fmt, "invalid_argument")
    assert source in message
    assert "line 2 column 10 (char 11)" in message


@pytest.mark.parametrize("fmt", ["human", "json", "ndjson"])
@pytest.mark.parametrize("target", ["local", "account"])
def test_tool_validation_and_confirmation_are_structured(capsys, fmt, target):
    prefix = ["--target", target, "--format", fmt]
    assert cli.main(prefix + ["call", "vinc_search", "--args", "{}"] ) == 2
    assert_failure(capsys, fmt, "invalid_argument")
    assert cli.main(prefix + ["delete", "concept:synthetic"]) == 2
    assert "confirmation required" in assert_failure(capsys, fmt, "invalid_argument")


@pytest.mark.parametrize("fmt", ["human", "json", "ndjson"])
@pytest.mark.parametrize("result", [
    {"executed": False}, {"isError": True}, {"status": "none"},
    {"error": "store unavailable"},
])
def test_tool_failures_are_also_on_stderr(capsys, fmt, result):
    cli_output.configure(fmt, "search")
    assert cli_output.emit(result) == 1
    got = capsys.readouterr()
    assert got.out == ""
    if fmt == "human":
        assert got.err.startswith("vinc: ")
    else:
        assert json.loads(got.err) == result


@pytest.mark.parametrize("fmt", ["human", "json", "ndjson"])
def test_success_bytes_are_unchanged(capsys, fmt):
    cli_output.configure(fmt, "search")
    result = {"rows": [{"id": "concept:x", "title": "한글"}], "next_cursor": None}
    assert cli_output.emit(result) == 0
    got = capsys.readouterr()
    expected = ("concept:x\t한글\n" if fmt == "human" else
                json.dumps(result["rows"][0], ensure_ascii=False) + "\n" if fmt == "ndjson" else
                json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    assert got.out == expected
    assert got.err == ""
