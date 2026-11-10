# -*- coding: utf-8 -*-
"""설정의 저장 위치가 데이터베이스면 사이드카가 Neo4j 를 연다(`store.config.app_store_config` → `VincApp.open`).

잠그는 것: `~/.vinc/config.json` 의 데이터베이스 위치와 환경의 비밀번호만으로 스토어가 열리고 프로비저닝되며,
`/config` 가 엔진을 neo4j 로 말한다. 비밀번호가 틀리면 스토어 없이 뜨고 사유를 말한다(폴더로 물러서지 않는다).
`VINC_CONFORMANCE_NEO4J_URI` 가 있을 때만 돈다. ⚠ 그 데이터베이스를 비운다. 테스트 전용 서버만 가리킬 것.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

URI = os.environ.get("VINC_CONFORMANCE_NEO4J_URI")
pytestmark = pytest.mark.skipif(not URI, reason="VINC_CONFORMANCE_NEO4J_URI 없음")


@pytest.fixture()
def home():
    from store import config as sc
    from store.neo4j_backend import Neo4jBackend
    h = Path(tempfile.mkdtemp()) / ".vinc"
    s = Neo4jBackend(URI, user=os.environ.get("VINC_CONFORMANCE_NEO4J_USER", "neo4j"),
                     password=os.environ["VINC_CONFORMANCE_NEO4J_PASSWORD"], writer=False)
    try:
        with s._driver.session(database=s.database) as sess:
            sess.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS").consume()
    finally:
        s.close()
    saved = os.environ.pop("MEMORY_DATA_DIR", None)
    with mock.patch.object(sc, "vinc_home", lambda: h):
        sc.write_local_config({"mode": "database", "database": {
            "engine": "neo4j", "uri": URI, "database": "neo4j",
            "user": os.environ.get("VINC_CONFORMANCE_NEO4J_USER", "neo4j"),
            "secret_ref": "vinc-database:test"}})
        yield h
    if saved is not None:
        os.environ["MEMORY_DATA_DIR"] = saved


def test_설정의_데이터베이스를_열고_엔진을_말한다(home):
    from api.auth import SessionAuth
    from api.server import VincApp
    from store import config as sc
    cfg = sc.app_store_config(secret=os.environ["VINC_CONFORMANCE_NEO4J_PASSWORD"])
    app = VincApp.open(cfg, auth=SessionAuth("t"))
    try:
        assert app.store is not None and app.store.driver_name == "neo4j"
        assert app.store.writer_state()["writer"] is True
        assert app.store.holder.startswith(sc.installation_id() + "@")
        view = app._config_view()
        assert view["engine"] == "neo4j" and view["location"]["kind"] == "database"
        assert view["restart_needed"] is False
        assert not (home / "db").exists()          # 폴더 스토어는 만들지 않았다
    finally:
        app.store.close()


def test_비밀번호가_틀리면_스토어_없이_사유를_말한다(home):
    from api.auth import SessionAuth
    from api.server import VincApp, store_unavailable
    from store import config as sc
    from store.backend import _clear_open_errors
    app = VincApp.open(sc.app_store_config(secret="wrong-password"), auth=SessionAuth("t"))
    assert app.store is None
    reason = store_unavailable()["open_error"]
    _clear_open_errors()           # 사유는 프로세스 전역이다. 뒤 테스트가 읽지 않게
    assert "wrong-password" not in reason
    assert "auth" in reason.lower() or "unauthorized" in reason.lower()
    assert not (home / "db").exists()
