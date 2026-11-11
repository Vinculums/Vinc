# -*- coding: utf-8 -*-
"""데이터베이스 연결 확인과 저장 위치 쓰기(`store.neo4j_backend.inspect_database`, `POST /config`).

잠그는 것:
  - 확인은 아무것도 남기지 않는다(쓰기 권한은 되돌리는 트랜잭션으로 보고, 임대를 집지 않는다).
  - 빈 데이터베이스·Vinc 그래프·남의 데이터·틀린 비밀번호·닿지 않는 주소·없는 데이터베이스를 가른다.
  - 비밀번호는 결과·설정 파일 어디에도 없다.
  - 위험(평문 연결 등)이 있으면 `accept_risk` 없이 적지 않는다(409).
서버가 필요 없는 것(주소 검사·비밀번호 누락)은 늘 돌고, 나머지는 `VINC_CONFORMANCE_NEO4J_URI` 가 있을 때만
돈다. ⚠ 그 데이터베이스를 비운다. 테스트 전용 서버만 가리킬 것.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp  # noqa: E402
from store import config as sc  # noqa: E402

URI = os.environ.get("VINC_CONFORMANCE_NEO4J_URI")
USER = os.environ.get("VINC_CONFORMANCE_NEO4J_USER", "neo4j")
PASSWORD = os.environ.get("VINC_CONFORMANCE_NEO4J_PASSWORD", "")
needs_neo4j = pytest.mark.skipif(not URI, reason="VINC_CONFORMANCE_NEO4J_URI 없음")


@pytest.fixture()
def app():
    home = Path(tempfile.mkdtemp()) / ".vinc"
    saved = os.environ.pop("MEMORY_DATA_DIR", None)
    with mock.patch.object(sc, "vinc_home", lambda: home):
        a = VincApp(sc.load_config({"data_dir": str(home / "db")}), store=None, auth=SessionAuth("t"))
        a.home = home
        yield a
    if saved is not None:
        os.environ["MEMORY_DATA_DIR"] = saved


def _clean():
    from store.neo4j_backend import Neo4jBackend
    s = Neo4jBackend(URI, user=USER, password=PASSWORD, writer=False)
    try:
        with s._driver.session(database=s.database) as sess:
            sess.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS").consume()
    finally:
        s.close()


def _body(**kw):
    return {"uri": URI or "bolt://127.0.0.1:7687", "database": "neo4j", "user": USER,
            "password": PASSWORD or "x", **kw}


# ── 서버 없이 ───────────────────────────────────────────────────────────────
def test_틀린_주소와_빠진_비밀번호는_400(app):
    code, out = app.write_config_inspect_database(_body(uri="http://h:7474"))
    assert code == 400 and out["error"] == "bad_location"
    code, out = app.write_config_inspect_database(_body(password=""))
    assert code == 400 and "password" in out["error"]


def test_위치는_예전_키와_섞지_않는다(app):
    code, out = app.write_config({"location": {"kind": "account"}, "mode": "local"})
    assert code == 400


def test_계정과_폴더_위치를_적는다(app):
    code, view = app.write_config({"location": {"kind": "account"}, "mode_chosen": True})
    assert code == 200 and view["location"] == {"kind": "account"} and view["mode"] == "account"
    folder = str(Path(tempfile.mkdtemp()) / "picked")
    code, view = app.write_config({"location": {"kind": "folder", "path": folder}})
    assert code == 200, view
    assert view["location"] == {"kind": "folder", "path": folder} and view["mode"] == "local"


def test_닿지_않는_주소는_ok_가_아니다(app):
    code, out = app.write_config_inspect_database(_body(uri="bolt://127.0.0.1:1"))
    assert code == 200 and out["ok"] is False
    assert out["problem"] in ("unreachable", "driver_missing")


def test_이_기계_밖으로_가는_평문만_위험이다():
    """닿지 않는 주소여도 위험 판정은 연결 전에 난다. 루프백은 평문이어도 위험이 아니다."""
    from store.neo4j_backend import inspect_database
    far = inspect_database("bolt://vinc-check.invalid:7687", user="neo4j", password="x", database="neo4j", timeout_s=2)
    assert "unencrypted_transport" in far["risks"]
    near = inspect_database("bolt://127.0.0.1:1", user="neo4j", password="x", database="neo4j", timeout_s=2)
    assert "unencrypted_transport" not in near["risks"]
    secure = inspect_database("bolt+s://vinc-check.invalid:7687", user="neo4j", password="x", database="neo4j", timeout_s=2)
    assert "unencrypted_transport" not in secure["risks"]


def _foreign():
    from store.neo4j_backend import Neo4jBackend
    s = Neo4jBackend(URI, user=USER, password=PASSWORD, writer=False)
    try:
        with s._driver.session(database=s.database) as sess:
            sess.run("CREATE (:Customer {name: 'acme'})").consume()
    finally:
        s.close()


# ── 서버와 함께 ─────────────────────────────────────────────────────────────
@needs_neo4j
def test_빈_데이터베이스(app):
    _clean()
    code, out = app.write_config_inspect_database(_body())
    assert code == 200 and out["ok"], out
    assert out["auth_ok"] and out["version_supported"] and out["write_permission"] is True
    assert out["vinc_schema_version"] is None and out["vinc_counts"] == {"nodes": 0, "docs": 0}
    assert out["foreign_labels"]["nodes"] == 0
    assert "unencrypted_transport" not in out["risks"]   # 이 기계의 bolt:// 는 밖으로 안 나간다
    assert PASSWORD not in json.dumps(out)
    # 쓰기 확인이 남긴 것이 없다.
    from store.neo4j_backend import Neo4jBackend
    s = Neo4jBackend(URI, user=USER, password=PASSWORD, writer=False)
    try:
        assert s.run("MATCH (n) RETURN count(n) AS c") == [{"c": 0}]
    finally:
        s.close()


@needs_neo4j
def test_Vinc_그래프와_남의_데이터를_센다(app):
    _clean()
    from store.neo4j_backend import Neo4jBackend
    s = Neo4jBackend(URI, user=USER, password=PASSWORD, heartbeat=False)
    try:
        s.provision(dim=4)
        s.run("CREATE (:Concept {concept_id: 'c1', title: 'one'}), (:Concept {concept_id: 'c2', title: 'two'})")
        s.run("CREATE (:_DocMeta {doc_id: 'd1'})")
        s.run("CREATE (:Customer {name: 'acme'}), (:Customer {name: 'globex'}), (:Invoice {n: 1})")
        code, out = app.write_config_inspect_database(_body())
        holder = s.holder
    finally:
        s.close()
    assert out["ok"], out
    from store.embedded_schema import SCHEMA_VERSION
    assert out["vinc_schema_version"] == SCHEMA_VERSION
    assert out["vinc_counts"] == {"nodes": 2, "docs": 1}
    assert out["foreign_labels"] == {"count": 2, "names": ["Customer", "Invoice"], "nodes": 3}
    assert "foreign_data_present" in out["risks"]
    # 확인하는 동안 열려 있던 쓰기 프로세스가 보인다(화면은 "다른 Vinc 가 쓰는 중"을 말할 수 있다).
    assert out["writer_lease"] == {"holder": holder, "active": True}


@needs_neo4j
def test_틀린_비밀번호와_없는_데이터베이스(app):
    code, out = app.write_config_inspect_database(_body(password="definitely-wrong"))
    assert out["ok"] is False and out["problem"] == "auth_failed"
    assert "definitely-wrong" not in json.dumps(out)
    code, out = app.write_config_inspect_database(_body(database="nosuchdb"))
    assert out["ok"] is False and out["problem"] == "database_not_found", out


@needs_neo4j
def test_위험은_확인해야_적고_비밀번호는_파일에_없다(app):
    _clean()
    loc = {"kind": "database", "engine": "neo4j", "uri": URI, "database": "neo4j", "user": USER,
           "secret_ref": "vinc-database:test"}
    _foreign()                                            # 남의 데이터가 있으면 위험이다
    code, out = app.write_config({"location": loc, "password": PASSWORD})
    assert code == 409 and out["error"] == "risky_database"
    assert "foreign_data_present" in out["check"]["risks"]
    assert not (app.home / "config.json").exists()
    code, out = app.write_config({"location": loc, "password": "definitely-wrong", "accept_risk": True})
    assert code == 400 and out["error"] == "auth_failed"
    code, view = app.write_config({"location": loc, "password": PASSWORD, "accept_risk": True,
                                   "mode_chosen": True})
    assert code == 200, view
    assert view["location"] == loc and view["mode"] == "local" and view["restart_needed"] is True
    text = (app.home / "config.json").read_text(encoding="utf-8")
    assert PASSWORD not in text and json.loads(text)["mode"] == "database"
    # 다시 폴더로: 데이터베이스 덩이는 남고 위치는 폴더다.
    code, view = app.write_config({"location": {"kind": "folder"}})
    assert code == 200 and view["location"] == {"kind": "folder", "path": None}
    assert json.loads((app.home / "config.json").read_text(encoding="utf-8"))["database"]["uri"] == URI
