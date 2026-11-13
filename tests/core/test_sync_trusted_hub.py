# -*- coding: utf-8 -*-
"""기기 사이드카의 믿는 pull 은 이 기기가 정한 허브로만 간다.

`/sync`(받고 보내기), `/sync/pull-all`(계정 전부 받기), `/config/move` 의 계정에서 옮기기는 받은
실행 기록의 상태를 허브의 것 그대로 믿는다(`api/sync.py` `trusted_sender`). 그래서 받을 곳은 요청
본문이 아니라 기기의 설정(`VINC_SYNC_URL`, 없으면 `https://mcp.vincs.io`)이 정한다. 본문의 `base` 가
그와 다르면 아무 잡도 받지 않고 400 `untrusted_hub` 로 답한다. 같거나 없으면 정한 허브로 간다.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import server as srv, sync  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp  # noqa: E402
from store.config import load_config  # noqa: E402

EVIL = "https://hub.example.test"
HUB = "https://mcp.vincs.io"
ENV = "VINC_SYNC_URL"
DATABASE = {"kind": "database", "engine": "neo4j", "uri": "bolt://127.0.0.1:1", "database": "neo4j",
            "user": "neo4j", "secret_ref": "vinc-database:test"}


class _Remote:
    """`sync.RemoteHTTP` 대신 받는 주소만 적는다."""
    made: list[str] = []

    def __init__(self, token, base=None):
        _Remote.made.append(base)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setattr(srv.sync, "RemoteHTTP", _Remote)
    monkeypatch.setattr(srv.sync, "run", lambda *a, **k: {})
    monkeypatch.setattr(srv.sync, "pull_everything", lambda *a, **k: {})
    _Remote.made = []
    out = VincApp(load_config({"data_dir": str(tmp_path / "db")}), store=None,
                  auth=SessionAuth("fixture"))
    out.store = type("S", (), {"close": lambda self: None})()
    try:
        yield out
    finally:
        out.close()


def _call(app, route: str, base) -> tuple[int, dict]:
    body = {"token": "k", **({"base": base} if base is not None else {})}
    if route == "/sync":
        return app.submit_sync(body)
    if route == "/sync/pull-all":
        return app.submit_sync_pull_all(body)
    return app.submit_config_move({**body, "to": DATABASE, "from": "account", "password": "p"})


def _settle(app, code: int, out: dict) -> None:
    if code == 202:
        job = app.jobs.get(out["job_id"])
        assert job is not None and job._done.wait(10), out


@pytest.mark.parametrize("route", ["/sync", "/sync/pull-all", "/config/move"])
def test_a_trusted_pull_toward_another_hub_is_refused_before_any_job(app, route):
    code, out = _call(app, route, EVIL)
    _settle(app, code, out)
    assert code == 400 and out["error"] == "untrusted_hub", out
    assert HUB in out["note"] and "VINC_SYNC_URL" in out["note"], out
    assert _Remote.made == []


@pytest.mark.parametrize("route", ["/sync", "/sync/pull-all"])
@pytest.mark.parametrize("base", [None, "", HUB, HUB + "/"])
def test_no_base_or_the_configured_hub_goes_to_the_configured_hub(app, route, base):
    code, out = _call(app, route, base)
    _settle(app, code, out)
    assert code == 202, out
    assert _Remote.made == [HUB]


def test_the_device_configuration_names_the_hub(app, monkeypatch):
    monkeypatch.setenv(ENV, "http://127.0.0.1:8765/")
    code, out = _call(app, "/sync", None)
    _settle(app, code, out)
    assert code == 202 and _Remote.made == ["http://127.0.0.1:8765"], (code, out, _Remote.made)
    code, out = _call(app, "/sync/pull-all", HUB)
    assert code == 400 and out["error"] == "untrusted_hub", out
    assert "http://127.0.0.1:8765" in out["note"], out
