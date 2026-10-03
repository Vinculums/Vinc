"""Public help, reference results and first local boot are English and format-aware."""
import argparse
import io
import json
import re
from contextlib import redirect_stderr, redirect_stdout

import pytest

from api import cli


def test_all_help_is_english_without_internal_ids(monkeypatch):
    captured = []
    original = argparse.ArgumentParser.format_help

    def help_text(parser):
        captured.append(parser)
        return original(parser)

    monkeypatch.setattr(argparse.ArgumentParser, "format_help", help_text)
    with redirect_stdout(io.StringIO()):
        assert cli.main(["--help"]) == 0
    root = captured[0]
    parsers = [root]
    for parser in parsers:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                parsers.extend(action.choices.values())
    for parser in parsers:
        text = original(parser)
        assert not re.search(r"[\uac00-\ud7a3]", text), text
        assert not re.search(r"\b(?:VIN-\d+|SEC-\d+|[DFRP]\d+)\b", text), text
    text = original(root)
    assert "maintenance:" in text
    assert "Vinc command line interface" in text


@pytest.mark.parametrize("fmt", ["human", "json", "ndjson"])
@pytest.mark.parametrize("valid", [True, False])
def test_refs_formats_and_streams(tmp_path, capsys, fmt, valid):
    source = tmp_path / "source.py"
    source.write_text("# [[vinc.spec.dataplane]]\n")
    manifest = tmp_path / "scripts" / "graph-refs.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"docs": ["vinc.spec.dataplane"] if valid else [], "nodes": []}))
    assert cli.main(["--format", fmt, "refs", "check", "--root", str(tmp_path)]) == (0 if valid else 1)
    got = capsys.readouterr()
    text = got.out if valid else got.err
    assert (got.err if valid else got.out) == ""
    if fmt == "human":
        assert str(manifest) in text
        if not valid:
            assert "vinc refs refresh" in text
    else:
        result = json.loads(text)
        assert result["manifest"] == str(manifest)
        assert result["cited"] == 1
        assert len(result["missing"]) == (0 if valid else 1)
        if fmt == "ndjson":
            assert len(text.splitlines()) == 1


def test_missing_manifest_names_path_and_product_command(tmp_path, capsys):
    assert cli.main(["--format", "json", "refs", "check", "--root", str(tmp_path)]) == 2
    got = capsys.readouterr()
    assert got.out == ""
    result = json.loads(got.err)
    assert result["manifest"] == str(tmp_path / "scripts" / "graph-refs.json")
    assert "vinc refs refresh" in result["error"]["message"]
    assert "python scripts" not in got.err


@pytest.mark.parametrize("debug", [False, True])
def test_first_local_command_boot_logs_require_debug(tmp_path, monkeypatch, capsys, debug):
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "db"))
    monkeypatch.setenv("VINC_TARGET", "local")
    monkeypatch.delenv("VINC_DEBUG", raising=False)
    monkeypatch.setattr("api.cli_tools.attached_bridge", lambda args: None)
    args = ["--debug"] if debug else []
    assert cli.main([*args, "--format", "json", "whoami"]) == 0
    got = capsys.readouterr()
    assert json.loads(got.out)["target"] == "local"
    if debug:
        assert "built at boot" in got.err
    else:
        assert got.err == ""
