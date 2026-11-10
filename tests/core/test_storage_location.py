# -*- coding: utf-8 -*-
"""저장 위치(`store.location`)와 그것으로 여는 스토어 설정(`store.config.app_store_config`).

잠그는 것:
  - 데이터베이스 위치는 비밀번호를 들 수 없다. 설정에 실으려 하면 거절한다(받아 두고 버리면 파일엔 이미 남은 뒤다).
  - 예전 설정(`mode`·`data_path`)은 뜻이 바뀌지 않는다.
  - 설정이 틀린 데이터베이스 위치는 아무것도 열지 않고 사유를 말한다. 기본 폴더로 물러서지 않는다.
  - 비밀번호는 `redacted()`·repr·`/config` 어디에도 안 나온다.
  - 설치 id 는 한 번 정하면 그대로다(쓰기 임대 보유자 id 의 앞부분).
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp  # noqa: E402
from store import backend as B  # noqa: E402
from store import config as sc  # noqa: E402
from store import location as L  # noqa: E402

DB = {"engine": "neo4j", "uri": "neo4j+s://graph.example.com:7687", "database": "vinc",
      "user": "vinc_app", "secret_ref": "vinc-database:graph.example.com/vinc"}


@contextlib.contextmanager
def _home(tmp: str):
    home = Path(tmp) / ".vinc"
    saved = os.environ.pop("MEMORY_DATA_DIR", None)
    try:
        with mock.patch.object(sc, "vinc_home", lambda: home):
            yield home
    finally:
        if saved is not None:
            os.environ["MEMORY_DATA_DIR"] = saved


class ParseTests(unittest.TestCase):
    def test_데이터베이스_위치(self):
        loc = L.parse_location({"kind": "database", **DB})
        self.assertIsInstance(loc, L.DatabaseLocation)
        self.assertEqual(loc.label, "graph.example.com/vinc")
        self.assertTrue(loc.encrypted)
        self.assertEqual(loc.to_dict(), {"kind": "database", **DB})

    def test_평문_스킴은_암호화가_아니다(self):
        self.assertFalse(L.parse_database({**DB, "uri": "bolt://localhost:7687"}).encrypted)

    def test_비밀번호는_받지_않는다(self):
        for key in ("password", "secret"):
            with self.assertRaisesRegex(L.LocationError, "never the password"):
                L.parse_database({**DB, key: "hunter2"})

    def test_틀린_주소(self):
        for uri, why in [("http://h:7474", "must start with"), ("neo4j://", "no host"),
                         ("neo4j://u:p@h:7687", "user or password"), ("neo4j://h:7687/vinc", "host and port only"),
                         ("neo4j://h:99999", "invalid port")]:
            with self.subTest(uri=uri), self.assertRaisesRegex(L.LocationError, why):
                L.parse_database({**DB, "uri": uri})

    def test_틀린_이름과_참조(self):
        with self.assertRaisesRegex(L.LocationError, "database name"):
            L.parse_database({**DB, "database": "1st"})
        with self.assertRaisesRegex(L.LocationError, "keyring entry name"):
            L.parse_database({**DB, "secret_ref": "has space"})
        with self.assertRaisesRegex(L.LocationError, "needs user"):
            L.parse_database({**DB, "user": " "})
        with self.assertRaisesRegex(L.LocationError, "unsupported database engine"):
            L.parse_database({**DB, "engine": "postgres"})

    def test_폴더는_절대경로만(self):
        self.assertEqual(L.parse_location({"kind": "folder"}), L.FolderLocation())
        with self.assertRaisesRegex(L.LocationError, "absolute"):
            L.parse_location({"kind": "folder", "path": "rel/dir"})
        with self.assertRaisesRegex(L.LocationError, "kind"):
            L.parse_location({"kind": "cloud"})


class ReadLocationTests(unittest.TestCase):
    def test_예전_설정은_뜻이_그대로다(self):
        self.assertIsNone(L.read_location({}))
        self.assertEqual(L.read_location({"mode": "account"}), L.AccountLocation())
        self.assertEqual(L.read_location({"mode": "local"}), L.FolderLocation())
        self.assertEqual(L.read_location({"mode": "local", "data_path": "/srv/vinc"}),
                         L.FolderLocation("/srv/vinc"))
        self.assertEqual(L.read_location({"mode": "local", "data_path": "relative"}), L.FolderLocation())

    def test_데이터베이스_모드(self):
        loc = L.read_location({"mode": "database", "database": DB, "data_path": "/srv/vinc"})
        self.assertIsInstance(loc, L.DatabaseLocation)
        with self.assertRaisesRegex(L.LocationError, "no database is configured"):
            L.read_location({"mode": "database"})

    def test_폴더로_돌아가도_데이터베이스_덩이는_남아도_된다(self):
        self.assertEqual(L.read_location({"mode": "local", "database": DB}), L.FolderLocation())

    def test_비밀번호는_환경에서_꺼내고_지운다(self):
        env = {L.SECRET_ENV: "s3cret", "OTHER": "x"}
        self.assertEqual(L.take_secret(env), "s3cret")
        self.assertNotIn(L.SECRET_ENV, env)
        self.assertIsNone(L.take_secret(env))


class AppStoreConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ctx = _home(self.tmp)
        self.home = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        # 못 연 사유는 프로세스 전역이다. 뒤 테스트의 `/health` 가 여기 사유를 읽지 않게 지운다.
        self.addCleanup(B._clear_open_errors)

    def test_데이터베이스_위치는_neo4j_설정이다(self):
        sc.write_local_config({"mode": "database", "database": DB})
        cfg = sc.app_store_config(secret="s3cret")
        self.assertEqual(cfg.backend, "neo4j")
        self.assertIsNone(cfg.data_dir)
        self.assertEqual(cfg.database, {"uri": DB["uri"], "database": "vinc", "user": "vinc_app"})
        self.assertEqual(cfg.database_secret, "s3cret")
        self.assertTrue(cfg.lease_holder.startswith(sc.installation_id() + "@"))
        # 데이터 홈의 하위 구조는 데이터베이스 위치여도 만든다(packs 는 늘 홈에 있다).
        self.assertTrue((self.home / "packs").is_dir())

    def test_비밀번호는_어디에도_안_나온다(self):
        sc.write_local_config({"mode": "database", "database": DB})
        cfg = sc.app_store_config(secret="s3cret")
        self.assertNotIn("s3cret", json.dumps(cfg.redacted(), default=str))
        self.assertNotIn("s3cret", repr(cfg))
        self.assertNotIn("s3cret", (self.home / "config.json").read_text(encoding="utf-8"))

    def test_틀린_설정은_아무것도_열지_않고_사유를_말한다(self):
        sc.write_local_config({"mode": "database", "database": {**DB, "uri": "http://x"}})
        cfg = sc.app_store_config()
        self.assertIn("storage settings", cfg.extra["open_blocked"])
        self.assertIsNone(B.open_store(cfg))
        self.assertIn("must start with", B.last_open_error())
        self.assertFalse((self.home / "db").exists())     # 기본 폴더 스토어를 만들지 않았다

    def test_비밀번호가_없으면_열지_않는다(self):
        sc.write_local_config({"mode": "database", "database": DB})
        try:
            import neo4j  # noqa: F401
        except ImportError:
            self.skipTest("neo4j driver not installed")
        self.assertIsNone(B.open_store(sc.app_store_config(secret=None)))
        self.assertIn("no password", B.last_open_error())

    def test_폴더를_박으면_설정의_위치는_안_먹는다(self):
        sc.write_local_config({"mode": "database", "database": DB})
        with mock.patch.dict(os.environ, {"MEMORY_DATA_DIR": str(Path(self.tmp) / "pinned" / "db")}):
            cfg = sc.app_store_config()
        self.assertEqual(cfg.backend, "embedded")
        self.assertTrue(cfg.data_dir.endswith(os.path.join("pinned", "db")))

    def test_폴더와_계정은_예전_경로다(self):
        sc.write_local_config({"mode": "local", "data_path": str(Path(self.tmp) / "picked")})
        self.assertEqual(sc.app_store_config().data_dir, str(Path(self.tmp) / "picked" / "db"))
        sc.write_local_config({"mode": "account", "data_path": None})
        self.assertEqual(sc.app_store_config().data_dir, str(self.home / "db"))


class InstallationIdTests(unittest.TestCase):
    def test_한번_정하면_그대로고_깨지면_새로_만든다(self):
        home = Path(tempfile.mkdtemp()) / ".vinc"
        first = sc.installation_id(home)
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertEqual(sc.installation_id(home), first)
        (home / sc.INSTALLATION_FILE).write_text("garbage", encoding="ascii")
        second = sc.installation_id(home)
        self.assertRegex(second, r"^[0-9a-f]{32}$")
        self.assertEqual(sc.installation_id(home), second)


class ConfigViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ctx = _home(self.tmp)
        self.home = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)

    def _view(self, cfg):
        return VincApp(cfg, store=None, auth=SessionAuth("t"))._config_view()

    def test_데이터베이스_위치를_비밀_없이_알린다(self):
        sc.write_local_config({"mode": "database", "database": DB})
        view = self._view(sc.app_store_config(secret="s3cret"))
        self.assertEqual(view["location"], {"kind": "database", **DB})
        self.assertEqual(view["mode"], "local")     # 요청은 이 기기의 사이드카로 간다
        self.assertIsNone(view["location_error"])
        self.assertFalse(view["restart_needed"])
        self.assertIsNone(view["engine"])
        self.assertNotIn("s3cret", json.dumps(view))

    def test_폴더로_바꾸면_다시_띄워야_한다(self):
        sc.write_local_config({"mode": "database", "database": DB})
        cfg = sc.app_store_config(secret="s3cret")
        sc.write_local_config({"mode": "local"})
        view = self._view(cfg)
        self.assertEqual(view["location"], {"kind": "folder", "path": None})
        self.assertTrue(view["restart_needed"])

    def test_폴더에서_데이터베이스로_바꾸면_다시_띄워야_한다(self):
        cfg = sc.app_store_config()
        sc.write_local_config({"mode": "database", "database": DB})
        self.assertTrue(self._view(cfg)["restart_needed"])

    def test_틀린_설정은_문장으로(self):
        sc.write_local_config({"mode": "database"})
        view = self._view(sc.app_store_config())
        self.assertIsNone(view["location"])
        self.assertIn("no database is configured", view["location_error"])


if __name__ == "__main__":
    unittest.main()
