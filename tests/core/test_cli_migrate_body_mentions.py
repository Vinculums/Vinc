"""`vinc migrate-body-mentions`: 본문 스팬 열을 여는 opt-in DDL 의 유일한 실행 경로."""
import io
import json
from contextlib import redirect_stderr, redirect_stdout

import pytest

from api import cli, cli_target
from store import embedded_schema as schema
from store.backend import open_store
from store.config import load_config


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.delenv("VINC_TARGET", raising=False)
    monkeypatch.setattr(cli_target, "read_local_config", lambda: {})
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    path = str(tmp_path / "db")
    store = open_store(load_config({"data_dir": path}))
    assert store is not None
    store.provision(dim=4)
    store.close()
    return path


def invoke(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["--format", "json", *args])
    return code, out.getvalue(), err.getvalue()


def ready(path):
    store = open_store(load_config({"data_dir": path}))
    try:
        return schema.body_mentions_ready(store)
    finally:
        store.close()


def test_without_approve_refuses_and_writes_nothing(data):
    code, out, err = invoke(["migrate-body-mentions", "--data", data])
    assert code == 2 and out == ""
    error = json.loads(err)["error"]
    assert error["code"] == "invalid_argument"
    assert "explicit approval" in error["message"]
    assert not ready(data)


def test_approve_migrates_once_then_reports_no_change(data):
    code, out, err = invoke(["migrate-body-mentions", "--data", data, "--approve"])
    assert code == 0, err
    assert json.loads(out) == {"kind": "migrate-body-mentions", "executed": True, "changed": True}
    assert ready(data)
    code, out, _ = invoke(["migrate-body-mentions", "--data", data, "--approve"])
    assert code == 0 and json.loads(out)["changed"] is False


def test_help_names_the_verb():
    with redirect_stdout(io.StringIO()) as out:
        assert cli.main(["migrate-body-mentions", "--help"]) == 0
    assert "--approve" in out.getvalue()
