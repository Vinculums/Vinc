# -*- coding: utf-8 -*-
"""로컬 모드(VIN-130) — 사람이 고른 폴더에 그래프를 두고, 모드를 바꿀 때만 옮긴다.

깨지면 조용한 것들을 여기 못 박는다:
  - 설정이 가리키는 폴더를 사이드카가 안 따라가면, 앱은 "옮겼다"고 말하고 옛 폴더를 연다.
  - 고른 폴더를 못 만들 때 기본 경로로 물러서면, 빈 스토어가 "데이터가 사라졌다"로 읽힌다.
  - 옮기기가 주제 없는 노드나 순번 0 인 옛 문서를 빠뜨리면, 오류 없이 한쪽에만 남는다.
    델타 동기화는 둘 다 못 잡았다(주제 `IN` 과 `sync_seq > 0`). 그래서 양방향 왕복을
    실엔진 두 스토어로 돌려 **수로** 대조한다.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import changes, localmode, sync  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp, make_server  # noqa: E402
from store import config as sc  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()
_TOKEN = "test-token-local"


@contextlib.contextmanager
def _home(tmp: str):
    """데이터 홈(`~/.vinc`)을 임시 폴더로. `MEMORY_DATA_DIR` 은 걷는다(고정이 아니게)."""
    home = Path(tmp) / ".vinc"
    saved = os.environ.pop("MEMORY_DATA_DIR", None)
    try:
        with mock.patch.object(sc, "vinc_home", lambda: home):
            yield home
    finally:
        if saved is not None:
            os.environ["MEMORY_DATA_DIR"] = saved


class ResolverTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_설정이_없으면_홈의_db(self):
        with _home(self.tmp) as home:
            self.assertEqual(sc.resolve_data_dir(), str(home / "db"))
            self.assertTrue((home / "packs").is_dir())

    def test_설정의_폴더가_홈을_이긴다(self):
        folder = Path(self.tmp) / "picked" / "Vinc"
        with _home(self.tmp) as home:
            sc.write_local_config({"mode": "local", "data_path": str(folder)})
            self.assertEqual(sc.resolve_data_dir(), str(folder / "db"))
            self.assertTrue(folder.is_dir())
            # 세션·확장·packs 는 홈에 남는다: DB 만 간다.
            self.assertTrue((home / "packs").is_dir())
            self.assertFalse((folder / "packs").exists())

    def test_env_와_명령줄이_설정을_이긴다(self):
        folder = Path(self.tmp) / "picked"
        with _home(self.tmp):
            sc.write_local_config({"data_path": str(folder)})
            with mock.patch.dict(os.environ, {"MEMORY_DATA_DIR": "/env/db"}):
                self.assertEqual(sc.resolve_data_dir(), "/env/db")
                self.assertEqual(sc.resolve_data_dir("/cli/db"), "/cli/db")

    def test_상대경로와_깨진_설정은_기본으로(self):
        with _home(self.tmp) as home:
            sc.write_local_config({"data_path": "relative/place"})
            self.assertEqual(sc.resolve_data_dir(), str(home / "db"))
            (home / sc.LOCAL_CONFIG).write_text("{not json", encoding="utf-8")
            self.assertEqual(sc.read_local_config(), {})
            self.assertEqual(sc.resolve_data_dir(), str(home / "db"))

    def test_못_만드는_폴더여도_기본으로_물러서지_않는다(self):
        """빠진 외장 디스크 같은 경우. 물러서면 다른(빈) 스토어가 조용히 열린다."""
        blocker = Path(self.tmp) / "a-file"
        blocker.write_text("x", encoding="utf-8")
        folder = blocker / "inside"
        with _home(self.tmp):
            sc.write_local_config({"data_path": str(folder)})
            self.assertEqual(sc.resolve_data_dir(), str(folder / "db"))

    def test_설정_쓰기는_합치고_None_은_지운다(self):
        with _home(self.tmp):
            sc.write_local_config({"mode": "local", "data_path": "/x"})
            out = sc.write_local_config({"data_path": None})
            self.assertEqual(out, {"mode": "local"})
            self.assertEqual(sc.read_local_config(), {"mode": "local"})


class InspectFolderTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_받을_수_없는_경로(self):
        self.assertEqual(localmode.inspect_folder("")["problem"], "empty")
        self.assertEqual(localmode.inspect_folder("rel/path")["problem"], "not_absolute")
        f = Path(self.tmp) / "file.txt"
        f.write_text("x", encoding="utf-8")
        self.assertEqual(localmode.inspect_folder(str(f))["problem"], "not_a_folder")
        (Path(self.tmp) / "hasdir" / "db").mkdir(parents=True)
        self.assertEqual(localmode.inspect_folder(str(Path(self.tmp) / "hasdir"))["problem"],
                         "db_is_a_folder")

    def test_아직_없는_폴더는_받는다(self):
        out = localmode.inspect_folder(str(Path(self.tmp) / "new" / "Vinc"))
        self.assertTrue(out["ok"], out)
        self.assertFalse(out["exists"])
        self.assertEqual(out["risks"], [])

    def test_이미_있는_스토어를_알아본다(self):
        (Path(self.tmp) / "db").write_bytes(b"\0" * 10)
        out = localmode.inspect_folder(self.tmp)
        self.assertTrue(out["ok"] and out["has_store"])
        self.assertEqual(out["store_bytes"], 10)

    def test_동기화_폴더는_위험으로_알린다(self):
        out = localmode.inspect_folder(str(Path(self.tmp) / "Dropbox" / "Vinc"))
        self.assertTrue(out["ok"])
        self.assertEqual(out["synced"], "Dropbox")
        self.assertEqual(out["risks"], ["synced"])
        mac = localmode.inspect_folder(
            str(Path(self.tmp) / "Library" / "CloudStorage" / "OneDrive-Personal" / "V"))
        self.assertEqual(mac["synced"], "OneDrive")
        # 짧은 표식은 조각 전체가 같을 때만(오탐 방지).
        self.assertIsNone(localmode.inspect_folder(str(Path(self.tmp) / "megaproject"))["synced"])

    def test_UNC_는_네트워크(self):
        self.assertTrue(localmode._is_network(Path("//server/share/Vinc")))


@contextlib.contextmanager
def _server(app):
    httpd = make_server(app, host="127.0.0.1", port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        app.close()


def _req(base, path, *, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method)
    r.add_header("Authorization", f"Bearer {_TOKEN}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


class ConfigRouteTests(unittest.TestCase):
    """스토어 없이도 답해야 한다: 폴더를 잘못 골라 안 열렸을 때 다시 고를 길이 이것뿐이다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ctx = _home(self.tmp)
        self.home = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)

    def _app(self):
        from store.config import load_config
        return VincApp(load_config({"data_dir": str(self.home / "db")}), store=None,
                       auth=SessionAuth(_TOKEN))

    def test_정하기_전에는_모드가_없고_스토어_없이_답한다(self):
        """"account" 로 채우면 앱은 이미 정했다고 읽고 기존 설치의 데이터를 안 본다."""
        with _server(self._app()) as base:
            code, obj = _req(base, "/config")
        self.assertEqual(code, 200, obj)
        self.assertIsNone(obj["mode"])
        self.assertIsNone(obj["data_path"])
        self.assertFalse(obj["store_open"])
        self.assertFalse(obj["restart_needed"])

    def test_폴더를_고르면_만들기만_하고_재기동을_말한다(self):
        folder = Path(self.tmp) / "picked"
        folder.mkdir()
        (folder / "keep.txt").write_text("mine", encoding="utf-8")
        with _server(self._app()) as base:
            code, obj = _req(base, "/config", method="POST",
                             body={"mode": "local", "data_path": str(folder)})
        self.assertEqual(code, 200, obj)
        self.assertEqual(obj["mode"], "local")
        self.assertEqual(obj["data_path"], str(folder))
        self.assertTrue(obj["restart_needed"])
        self.assertEqual((folder / "keep.txt").read_text(encoding="utf-8"), "mine")
        self.assertEqual(sc.read_local_config()["data_path"], str(folder))

    def test_동기화_폴더는_확인_없이는_409(self):
        folder = Path(self.tmp) / "OneDrive" / "Vinc"
        with _server(self._app()) as base:
            code, obj = _req(base, "/config", method="POST", body={"data_path": str(folder)})
            self.assertEqual(code, 409, obj)
            self.assertEqual(obj["error"], "risky_folder")
            self.assertFalse(folder.exists())
            self.assertNotIn("data_path", sc.read_local_config())
            code, obj = _req(base, "/config", method="POST",
                             body={"data_path": str(folder), "accept_risk": True})
        self.assertEqual(code, 200, obj)
        self.assertTrue(folder.is_dir())

    def test_기본_폴더를_고르면_설정을_비운다(self):
        with _server(self._app()) as base:
            _req(base, "/config", method="POST", body={"data_path": str(Path(self.tmp) / "x")})
            code, obj = _req(base, "/config", method="POST", body={"data_path": str(self.home)})
        self.assertEqual(code, 200, obj)
        self.assertIsNone(obj["data_path"])
        self.assertFalse(obj["restart_needed"])

    def test_틀린_값(self):
        with _server(self._app()) as base:
            self.assertEqual(_req(base, "/config", method="POST", body={"mode": "cloud"})[0], 400)
            self.assertEqual(_req(base, "/config", method="POST", body={})[0], 400)
            code, obj = _req(base, "/config", method="POST", body={"data_path": "rel"})
            self.assertEqual((code, obj["error"]), (400, "not_absolute"))

    def test_고정된_프로세스는_폴더를_안_받는다(self):
        with mock.patch.dict(os.environ, {"MEMORY_DATA_DIR": str(self.home / "db")}):
            with _server(self._app()) as base:
                code, obj = _req(base, "/config", method="POST",
                                 body={"data_path": str(Path(self.tmp) / "y")})
                self.assertEqual((code, obj["error"]), (409, "pinned"))
                code, obj = _req(base, "/config")
        self.assertTrue(obj["pinned"])

    def test_폴더_살피기(self):
        with _server(self._app()) as base:
            code, obj = _req(base, "/config/inspect?path="
                             + urllib.request.quote(str(Path(self.tmp) / "Dropbox")))
        self.assertEqual(code, 200)
        self.assertEqual(obj["risks"], ["synced"])

    def test_옮기기는_키가_있어야_하고_겹치지_않는다(self):
        app = self._app()
        # 가드만 통과시킨다(잡은 아래에서 막는다). 스냅샷은 꺼져 있어 close 만 부른다.
        app.store = type("S", (), {"close": lambda self: None})()
        gate = threading.Event()
        with mock.patch.object(sync, "push_everything", lambda *a, **k: gate.wait(5) or {}):
            with _server(app) as base:
                self.assertEqual(_req(base, "/sync/push-all", method="POST", body={})[0], 400)
                code, _ = _req(base, "/sync/push-all", method="POST", body={"token": "k"})
                self.assertEqual(code, 202)
                code, obj = _req(base, "/sync/pull-all", method="POST", body={"token": "k"})
                self.assertEqual(code, 409, obj)
                gate.set()

    def test_표식(self):
        """설정과 올려 보내기는 이 스토어를 안 바꾼다. 받기는 바꾼다."""
        self.assertFalse(changes.counts("/config"))
        self.assertFalse(changes.counts("/sync/push-all"))
        self.assertTrue(changes.counts("/sync/pull-all"))


class _Wire:
    """허브의 동기화 끝점 넷을 두 번째 스토어 위에서 흉내 낸다(`vinc_mcp/http_server._sync`
    가 사이드카로 넘기는 것과 같은 함수). 허브는 토큰에서 owner 를 정하므로 여기서도 고정."""

    def __init__(self, store, owner):
        self.store, self.owner = store, owner

    def push(self, chunks):
        agg = {"applied": 0, "skipped_stale": 0, "kept_deleted": 0}
        for ch in chunks:
            r = sync.apply_delta(owner=self.owner, bundle=ch, store=self.store)
            for k in agg:
                agg[k] += int(r.get(k) or 0)
        return agg

    def docs_push(self, payload):
        from api import docsync
        return docsync.import_docs(self.store, owner=self.owner, payload=payload)

    def pull(self, since, everything=False):
        return sync.export_delta(owner=self.owner, since=since, everything=everything,
                                 domains=None if everything else ["eng"], store=self.store)

    def docs_pull(self, since, everything=False):
        from api import docsync
        return docsync.export_docs(self.store, owner=self.owner, since=since,
                                   everything=everything)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class MoveRoundTripE2E(unittest.TestCase):
    """로컬 → 계정 → 새 폴더. 노드·엣지·문서 수가 양쪽에서 같아야 한다."""

    LOCAL, ACCT = "tenant:local", "user:acct"

    def _open(self, name):
        from store.backend import open_store
        from store.config import load_config
        st = open_store(load_config({"data_dir": os.path.join(self.tmp, name)}))
        st.provision(dim=4)
        self.addCleanup(st.close)
        return st

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        saved = {k: os.environ.get(k) for k in ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR")}
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        os.environ.pop("MEMORY_DATA_DIR", None)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None
                                 else os.environ.__setitem__(k, v) for k, v in saved.items()])
        self.device = self._open("device")
        self.account = self._open("account")
        from api import docstore as ds
        from api import fragment as frag
        frag.merge_fragment({"owner": self.LOCAL, "nodes": [
            {"id": "concept:core", "type": "concept", "title": "core", "domain": "eng"},
            # 주제가 없다. 델타 동기화의 `domain IN` 은 이것을 한 번도 못 날랐다.
            {"id": "concept:loose", "type": "concept", "title": "loose"},
            {"id": "record:note", "type": "record", "title": "note",
             "props": {"a": 1, "b": "two"}},
            {"id": "decision:pick", "type": "decision", "title": "pick", "domain": "eng"},
            {"id": "episode:day", "type": "episode", "title": "day", "domain": "eng"},
        ], "edges": [
            {"from": "concept:loose", "to": "concept:core", "type": "relates_to"},
            {"from": "record:note", "to": "concept:core", "type": "relates_to"},
        ]}, store=self.device)
        emb = lambda ts: [[1.0, 0.0, 0.0, 0.0] for _ in ts]   # noqa: E731
        ds.put_doc(self.device, owner=self.LOCAL, text="# new\n\nbody", title="new", embed=emb)
        old = ds.put_doc(self.device, owner=self.LOCAL, text="# old\n\nbody", title="old",
                         embed=emb)
        # 순번 열이 붙기 전부터 있던 문서(ALTER DEFAULT 0). 델타는 이것을 못 날랐다.
        self.device.run("MATCH (d:_DocMeta) WHERE d.doc_id = $d SET d.sync_seq = 0",
                        {"d": old["doc_id"]})

    def _edges(self, store, owner):
        rows = store.run("MATCH (a)-[r]->(b) WHERE a.owner_id = $o AND b.owner_id = $o "
                         "AND NOT label(r) IN ['MENTIONS', 'HAS_CHUNK', 'NEXT'] "
                         "RETURN count(r) AS c", {"o": owner})
        return int(rows[0]["c"])

    def test_양방향_무손실(self):
        before = sync.owner_counts(self.device, self.LOCAL)
        self.assertEqual(before["nodes"], 5)
        self.assertEqual(before["docs"], 2)
        edges_before = self._edges(self.device, self.LOCAL)
        self.assertGreaterEqual(edges_before, 2)

        up = sync.push_everything(self.device, None, owners=[self.LOCAL, "user:local"],
                                  remote=_Wire(self.account, self.ACCT))
        self.assertTrue(up["executed"])
        self.assertEqual(up["missing"], 0, up)
        self.assertEqual(up["kept_deleted"], 0)
        in_account = sync.owner_counts(self.account, self.ACCT)
        for k in ("Concept", "Episode", "Decision", "Record", "nodes", "docs"):
            self.assertEqual(in_account[k], before[k], k)
        self.assertEqual(self._edges(self.account, self.ACCT), edges_before)
        # 올려 보내기는 이 기기를 안 바꾼다.
        self.assertEqual(sync.owner_counts(self.device, self.LOCAL), before)

        fresh = self._open("fresh")
        down = sync.pull_everything(fresh, None, owner=self.LOCAL,
                                    remote=_Wire(self.account, self.ACCT))
        self.assertEqual(down["missing"], 0, down)
        self.assertEqual((down["nodes_mode"], down["docs_mode"]), ("everything", "everything"))
        after = sync.owner_counts(fresh, self.LOCAL)
        for k in ("Concept", "Episode", "Decision", "Record", "nodes", "docs"):
            self.assertEqual(after[k], before[k], k)
        self.assertEqual(self._edges(fresh, self.LOCAL), edges_before)
        props = fresh.run("MATCH (r:Record) WHERE r.owner_id = $o RETURN r.props AS p",
                          {"o": self.LOCAL})
        self.assertEqual(json.loads(props[0]["p"]), {"a": 1, "b": "two"})

    def test_계정에서_먼저_지운_노드는_따로_센다(self):
        """계정의 삭제가 이 기기의 마지막 편집보다 새것이면 삭제가 이긴다. 받았다는 수에
        섞으면 계정에 없는 노드를 무손실로 읽는다."""
        sync.push_everything(self.device, None, owners=[self.LOCAL],
                             remote=_Wire(self.account, self.ACCT))
        from api import query as q
        q.delete_node("concept:loose", owner=self.ACCT, store=self.account)
        up = sync.push_everything(self.device, None, owners=[self.LOCAL],
                                  remote=_Wire(self.account, self.ACCT))
        self.assertEqual(up["kept_deleted"], 1, up)
        self.assertEqual(up["missing"], 0)
        self.assertEqual(sync.owner_counts(self.account, self.ACCT)["nodes"], 4)

    def test_옛_허브면_전량이_아니라고_말한다(self):
        wire = _Wire(self.account, self.ACCT)
        sync.push_everything(self.device, None, owners=[self.LOCAL], remote=wire)

        class _Old(_Wire):
            def pull(self, since, everything=False):
                return super().pull(since, everything=False)

            def docs_pull(self, since, everything=False):
                out = super().docs_pull(since, everything=False)
                out.pop("everything", None)
                return out

        down = sync.pull_everything(self._open("old"), None, owner=self.LOCAL,
                                    remote=_Old(self.account, self.ACCT))
        self.assertEqual(down["nodes_mode"], "topics")
        self.assertEqual(down["docs_mode"], "delta")


if __name__ == "__main__":
    unittest.main()


class ClientFlagTests(unittest.TestCase):

    def test_전량_플래그는_켤_때만_싣는다(self):
        from vinc_mcp import syncclient
        sent = []

        def fake(base, path, token, payload, *, timeout):
            sent.append((path, dict(payload)))
            return {"executed": True, "docs": []}

        with mock.patch.object(syncclient, "_post", fake):
            syncclient.pull(since=0, token="k", everything=True)
            syncclient.pull(since=3, token="k")
            syncclient.docs_pull(since=0, token="k", everything=True)
        self.assertEqual(sent, [("/sync/pull", {"since": 0, "all": True}),
                                ("/sync/pull", {"since": 3}),
                                ("/sync/docs-pull", {"since": 0, "all": True})])


class ServeProcessTests(unittest.TestCase):
    """실제 `serve` 프로세스. 셸이 하는 일(폴더 설정 → 정상 종료 → 다시 띄우기)을 그대로 한다."""

    def _serve(self, home: Path):
        import site
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != "MEMORY_DATA_DIR"}
        env.update({"HOME": str(home), "USERPROFILE": str(home),
                    # 홈을 바꾸면 사용자 site(`~/.local`)도 따라 바뀐다. CI 러너는 드라이버를
                    # 거기 깔아서, 자식이 ladybug 를 못 찾고 스토어 없이 떴다(실측 2026-09-25).
                    "PYTHONUSERBASE": site.getuserbase()})
        self.err = Path(tempfile.mkdtemp()) / "serve.err"
        errf = open(self.err, "w", encoding="utf-8")
        self.addCleanup(errf.close)
        proc = subprocess.Popen(
            [sys.executable, "-m", "api.cli", "serve", "--port", "0", "--token", _TOKEN],
            cwd=str(Path(__file__).resolve().parents[1]), env=env,
            stdout=subprocess.PIPE, stderr=errf, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        banner = json.loads(proc.stdout.readline())
        if _DRIVER is not None:
            # 여기서 막아야 아래 "폴더에 파일이 없다"가 원인 모를 실패로 끝나지 않는다.
            self.assertEqual(banner.get("driver"), _NAME,
                             f"serve started without a store: {banner} | "
                             f"{self.err.read_text(encoding='utf-8')[-800:]}")
        return proc, f"http://127.0.0.1:{banner['port']}"

    def test_설정의_폴더를_열고_부탁하면_스스로_닫는다(self):
        home = Path(tempfile.mkdtemp())
        folder = home / "chosen"
        (home / ".vinc").mkdir()
        (home / ".vinc" / sc.LOCAL_CONFIG).write_text(
            json.dumps({"mode": "local", "data_path": str(folder)}), encoding="utf-8")
        proc, base = self._serve(home)
        code, obj = _req(base, "/config")
        self.assertEqual(code, 200, obj)
        self.assertEqual(obj["data_file"], str(folder / "db"))
        self.assertFalse(obj["restart_needed"])
        self.assertFalse(obj["pinned"])
        code, obj = _req(base, "/shutdown", method="POST", body={})
        self.assertEqual(code, 202, obj)
        self.assertEqual(proc.wait(timeout=30), 0)
        # 세션 파일·packs 는 홈에, DB 만 고른 폴더에.
        self.assertTrue((home / ".vinc" / "packs").is_dir())
        self.assertFalse((folder / "packs").exists())
        if _DRIVER is not None:
            self.assertTrue((folder / "db").is_file())
            self.assertFalse((home / ".vinc" / "db").exists())
