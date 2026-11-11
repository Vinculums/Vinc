# -*- coding: utf-8 -*-
"""저장 위치 이동 잡(`POST /config/move`) — 다 왔을 때만 위치가 바뀌고 원본은 그대로다.

잠그는 것:
  - 폴더 → 폴더, 폴더 → Neo4j, Neo4j → 폴더가 옮기고 나서 설정을 바꾼다(`switched`).
  - 빠진 행이 있으면 설정을 바꾸지 않는다.
  - 이미 그래프가 있는 폴더, 지금 연 자리, 다른 Vinc 가 쓰는 데이터베이스는 받지 않는다.
  - 비밀번호는 설정 파일에 없다.
Neo4j 쪽은 `VINC_CONFORMANCE_NEO4J_URI` 가 있을 때만 돈다. ⚠ 그 데이터베이스를 비운다.
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

from api import docstore, fragment, query as q  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp  # noqa: E402
from store import config as sc  # noqa: E402
from store import migrate  # noqa: E402

URI = os.environ.get("VINC_CONFORMANCE_NEO4J_URI")
USER = os.environ.get("VINC_CONFORMANCE_NEO4J_USER", "neo4j")
PASSWORD = os.environ.get("VINC_CONFORMANCE_NEO4J_PASSWORD", "")
OWNER = "user:move"
needs_neo4j = pytest.mark.skipif(not URI, reason="VINC_CONFORMANCE_NEO4J_URI 없음")


@pytest.fixture()
def home():
    h = Path(tempfile.mkdtemp()) / ".vinc"
    saved = os.environ.pop("MEMORY_DATA_DIR", None)
    with mock.patch.object(sc, "vinc_home", lambda: h):
        yield h
    if saved is not None:
        os.environ["MEMORY_DATA_DIR"] = saved


def _open(cfg):
    app = VincApp.open(cfg, auth=SessionAuth("t"))
    if app.store is None:
        pytest.skip("스토어를 못 열었다(드라이버 미설치)")
    return app


def _fill(store):
    nodes = [{"id": "concept:move", "type": "concept", "title": "Move"}]
    nodes += [{"id": f"concept:move-{i}", "type": "concept", "title": f"Move {i}"} for i in range(4)]
    edges = [{"from": f"concept:move-{i}", "to": "concept:move", "type": "relates_to"} for i in range(4)]
    assert fragment.merge_fragment({"owner": OWNER, "nodes": nodes, "edges": edges}, store=store)["executed"]
    assert docstore.put_doc(store, owner=OWNER, doc_id="move.doc", title="Move doc",
                            text="One paragraph.\n\nAnother paragraph.")["executed"]


def _wait(app, accepted):
    code, job = accepted
    assert code == 202, job
    j = app.jobs.get(job["job_id"])
    assert j._done.wait(120)
    assert j.status == "done", j.to_dict()
    return j.result


def _neighbors(store):
    return q.neighbors("concept:move", owner=OWNER, store=store)["count"]


def _clean_neo4j():
    from store.neo4j_backend import Neo4jBackend
    s = Neo4jBackend(URI, user=USER, password=PASSWORD, writer=False)
    try:
        with s._driver.session(database=s.database) as sess:
            sess.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS").consume()
    finally:
        s.close()


def test_폴더에서_폴더로(home):
    app = _open(sc.app_store_config())
    try:
        _fill(app.store)
        dest = Path(tempfile.mkdtemp()) / "moved"
        res = _wait(app, app.submit_config_move({"to": {"kind": "folder", "path": str(dest)}}))
        assert res["switched"] is True and res["copy"]["missing"] == 0
        conf = sc.read_local_config()
        assert conf["mode"] == "local" and conf["data_path"] == str(dest) and conf["mode_chosen"] is True
        assert res["config"]["restart_needed"] is True
        assert _neighbors(app.store) == 4                 # 원본은 그대로다
    finally:
        app.close()
    moved = _open(sc.app_store_config())
    try:
        assert moved.cfg.data_dir == str(dest / "db") and _neighbors(moved.store) == 4
    finally:
        moved.close()


def test_받지_않는_대상(home):
    app = _open(sc.app_store_config())
    try:
        code, out = app.submit_config_move({"to": {"kind": "folder"}})
        assert code == 409 and out["error"] == "same_location"
        busy = Path(tempfile.mkdtemp())
        (busy / "db").write_bytes(b"x")
        code, out = app.submit_config_move({"to": {"kind": "folder", "path": str(busy)}})
        assert code == 409 and out["error"] == "folder_has_store"
        code, out = app.submit_config_move({"to": {"kind": "account"}})
        assert code == 400
        code, out = app.submit_config_move({"to": {"kind": "folder", "path": "rel"}})
        assert code == 400 and out["error"] == "bad_location"
    finally:
        app.close()


def test_빠진_행이_있으면_바꾸지_않는다(home):
    app = _open(sc.app_store_config())
    try:
        _fill(app.store)
        sc.write_local_config({"mode": "local", "mode_chosen": True})
        before = sc.read_local_config()
        real = migrate.copy_store
        with mock.patch.object(migrate, "copy_store",
                               lambda *a, **k: {**real(*a, **k), "missing": 1}):
            res = _wait(app, app.submit_config_move(
                {"to": {"kind": "folder", "path": str(Path(tempfile.mkdtemp()) / "short")}}))
        assert res["switched"] is False and res["config"] is None
        assert sc.read_local_config() == before
    finally:
        app.close()


@needs_neo4j
def test_폴더에서_Neo4j_로_갔다가_폴더로_돌아온다(home):
    _clean_neo4j()
    loc = {"kind": "database", "engine": "neo4j", "uri": URI, "database": "neo4j", "user": USER,
           "secret_ref": "vinc-database:test"}
    app = _open(sc.app_store_config())
    try:
        _fill(app.store)
        # 이 기계의 bolt:// 는 위험이 아니라 확인 없이 옮긴다.
        res = _wait(app, app.submit_config_move({"to": loc, "password": PASSWORD}))
        assert res["switched"] is True and res["copy"]["missing"] == 0, res
        assert res["config"]["location"] == loc and res["config"]["restart_needed"] is True
        assert PASSWORD not in (home / "config.json").read_text(encoding="utf-8")
        again = _wait(app, app.submit_config_move({"to": loc, "password": PASSWORD, "accept_risk": True}))
        assert again["switched"] is True and again["copy"]["missing"] == 0     # 다시 돌려도 같다
    finally:
        app.close()
    db = _open(sc.app_store_config(secret=PASSWORD))
    try:
        assert db.store.driver_name == "neo4j" and _neighbors(db.store) == 4
        assert docstore.get_doc(db.store, "move.doc", owner=OWNER).get("text")
        code, out = db.submit_config_move({"to": loc, "password": PASSWORD, "accept_risk": True})
        assert code == 409 and out["error"] == "another_writer"         # 지금 이 프로세스가 쓴다
        back = Path(tempfile.mkdtemp()) / "back"
        res = _wait(db, db.submit_config_move({"to": {"kind": "folder", "path": str(back)}}))
        assert res["switched"] is True and res["copy"]["missing"] == 0
        assert json.loads((home / "config.json").read_text(encoding="utf-8"))["mode"] == "local"
    finally:
        db.close()
    folder = _open(sc.app_store_config())
    try:
        assert folder.cfg.data_dir == str(back / "db") and _neighbors(folder.store) == 4
    finally:
        folder.close()


class _Wire:
    """계정 허브 대역: 다른 스토어 하나를 계정 그래프로 내보인다(`tests/core/test_local_mode.py` 의 것과 같다)."""

    def __init__(self, store, owner):
        self.store, self.owner = store, owner

    def pull(self, since, everything=False):
        from api import sync
        return sync.export_delta(owner=self.owner, since=since, everything=everything, store=self.store)

    def docs_pull(self, since, everything=False):
        from api import docsync
        return docsync.export_docs(self.store, owner=self.owner, since=since, everything=everything)


@needs_neo4j
def test_계정_그래프를_Neo4j_로_받는다(home):
    _clean_neo4j()
    from store.backend import open_store
    account = open_store(sc.load_config({"data_dir": str(Path(tempfile.mkdtemp()) / "acct")}))
    if account is None:
        pytest.skip("임베디드 드라이버 미설치")
    account.provision(dim=sc.load_config().embedding_dim)
    nodes = [{"id": "concept:acct", "type": "concept", "title": "Account node"},
             {"id": "concept:acct-2", "type": "concept", "title": "Second"}]
    edges = [{"from": "concept:acct-2", "to": "concept:acct", "type": "relates_to"}]
    assert fragment.merge_fragment({"owner": "user:acct", "nodes": nodes, "edges": edges},
                                   store=account)["executed"]
    loc = {"kind": "database", "engine": "neo4j", "uri": URI, "database": "neo4j", "user": USER,
           "secret_ref": "vinc-database:test"}
    app = _open(sc.app_store_config())
    try:
        code, out = app.submit_config_move({"to": {"kind": "folder", "path": "/x"}, "from": "account",
                                            "token": "k"})
        assert code == 400
        from api import server as srv
        with mock.patch.object(srv.sync, "RemoteHTTP", lambda token, base=None: _Wire(account, "user:acct")):
            res = _wait(app, app.submit_config_move({"to": loc, "password": PASSWORD, "accept_risk": True,
                                                     "from": "account", "token": "k"}))
        assert res["switched"] is True and res["copy"]["missing"] == 0, res
    finally:
        app.close()
        account.close()
    db = _open(sc.app_store_config(secret=PASSWORD))
    try:
        assert q.neighbors("concept:acct", owner="tenant:local", store=db.store)["count"] == 1
    finally:
        db.close()


@needs_neo4j
def test_고정_경로로_뜬_허브는_데이터베이스로_복사만_하고_설정은_안_쓴다(home):
    """허브는 위치를 환경(VINC_DATABASE_*)이 정하므로 설정 파일을 바꿔 봐야 소용없다. 고정 경로
    프로세스는 데이터베이스로 복사하고 다 왔는지만 말하며, 폴더로 옮기기는 거절한다."""
    _clean_neo4j()
    loc = {"kind": "database", "engine": "neo4j", "uri": URI, "database": "neo4j", "user": USER,
           "secret_ref": "vinc-database:test"}
    data = Path(tempfile.mkdtemp()) / "db"
    app = _open(sc.app_store_config(str(data)))
    try:
        app.data_pinned = True        # `vinc serve --data`(허브 엔트리포인트)가 하는 것과 같다
        _fill(app.store)
        code, out = app.submit_config_move({"to": {"kind": "folder", "path": str(Path(tempfile.mkdtemp()))}})
        assert code == 409 and out["error"] == "pinned"
        res = _wait(app, app.submit_config_move({"to": loc, "password": PASSWORD, "accept_risk": True}))
        assert res["switched"] is False and res["copied"] is True and res["copy"]["missing"] == 0, res
        assert res["copy"]["nodes"] > 0 and "VINC_DATABASE_URI" in res["next"]
        assert not (home / "config.json").exists()
    finally:
        app.close()
