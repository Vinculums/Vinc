# -*- coding: utf-8 -*-
"""변경 표식(VIN-129) — 열린 화면이 갱신 버튼 없이 다시 읽을 때를 아는 값.

여기 있는 것들은 깨져도 **조용하다**: 표식이 안 바뀌면 MCP 가 쓴 것이 열린 화면에
영영 안 나타나고(사용자는 "안 들어갔다"로 읽는다), 너무 자주 바뀌면 노드를 누를
때마다 지도가 다시 그려진다. 허브에서는 남의 쓰기로 내 화면이 다시 그려진다.
"""
from __future__ import annotations

import contextlib
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import changes  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from api.jobs import Job  # noqa: E402
from api.server import VincApp, make_server  # noqa: E402

_TOKEN = "test-token-chg"


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


class _Store:
    """쓰기를 받아 주는 최소 대역(`test_server.FakeStoreDataTests` 와 같은 모양)."""
    driver_name = "fake"

    def __init__(self):
        self.writes = []

    def reader_view(self):
        return self

    def run(self, cypher, params=None):
        self.writes.append(cypher)
        return [{"id": "a", "pinned": True}]

    def fulltext_search(self, *a, **k):
        return []

    def vector_search(self, *a, **k):
        return []

    def close(self):
        pass


def _marker(base, owner):
    code, obj = _req(base, f"/changes?owner={owner}")
    assert code == 200, (code, obj)
    return obj["marker"]


class MarkerUnitTests(unittest.TestCase):

    def test_소유자마다_따로_센다(self):
        m = changes.ChangeMarker()
        a0, b0 = m.read("user:a"), m.read("user:b")
        m.bump("user:a")
        self.assertNotEqual(m.read("user:a"), a0)
        self.assertEqual(m.read("user:b"), b0, "남의 쓰기로 내 화면이 다시 그려진다")

    def test_소유자_없는_쓰기는_모두에게_보인다(self):
        m = changes.ChangeMarker()
        a0, b0 = m.read("user:a"), m.read("user:b")
        m.bump(None)
        self.assertNotEqual(m.read("user:a"), a0)
        self.assertNotEqual(m.read("user:b"), b0)

    def test_프로세스마다_세대가_다르다(self):
        """재시작은 스냅샷 창만큼 쓰기를 되감을 수 있다. 그때는 다시 읽어야 한다."""
        self.assertNotEqual(changes.ChangeMarker().read("user:a"),
                            changes.ChangeMarker().read("user:a"))

    def test_비동기_잡은_끝난_뒤에_센다(self):
        m = changes.ChangeMarker()
        job = Job("job-1", "ingest:x")
        before = m.read("user:a")
        m.bump_when_done("user:a", job)
        self.assertEqual(m.read("user:a"), before, "데이터가 들어오기 전에 다시 읽게 된다")
        job.status = "done"
        job.result = {}
        job._done.set()
        self.assertNotEqual(m.read("user:a"), before)
        # 한 번만 센다
        once = m.read("user:a")
        self.assertEqual(m.read("user:a"), once)

    def test_판정이_아니라고_하면_안_센다_실패는_센다(self):
        m = changes.ChangeMarker()
        quiet, failed = Job("job-1", "sync"), Job("job-2", "sync")
        quiet.status, quiet.result = "done", {"pull": {"applied": 0}}
        failed.status, failed.error = "error", "boom"
        quiet._done.set()
        failed._done.set()
        before = m.read("user:a")
        m.bump_when_done("user:a", quiet, changes.sync_brought_something)
        self.assertEqual(m.read("user:a"), before)
        m.bump_when_done("user:a", failed, changes.sync_brought_something)
        self.assertNotEqual(m.read("user:a"), before)

    def test_동기화는_무엇이든_들였을_때만_센다(self):
        f = changes.sync_brought_something
        self.assertFalse(f({"pull": {"applied": 0, "deleted": 0},
                            "docs_pull": {"applied": 0, "updated": 0, "deleted": 0},
                            "push": {"pushed": 9}}))
        self.assertTrue(f({"pull": {"applied": 1}}))
        self.assertTrue(f({"pull": {"deleted": 2}}))
        self.assertTrue(f({"docs_pull": {"updated": 1}}))
        self.assertTrue(f({"account_switched": True}))

    def test_보이는_것을_안_바꾸는_쓰기만_조용하다(self):
        for p in ("/touch", "/space/proxy", "/embed/apply", "/doc/reembed"):
            self.assertFalse(changes.counts(p), p)
        for p in ("/pin", "/ingest/fragment", "/doc/put", "/delete", "/domain/rename",
                  "/sync/apply-delta", "/a-route-added-later"):
            self.assertTrue(changes.counts(p), p)


class MarkerRouteTests(unittest.TestCase):

    def test_성공한_쓰기가_그_소유자의_표식을_바꾼다(self):
        app = VincApp(store=_Store(), auth=SessionAuth(_TOKEN))
        with _server(app) as base:
            a0, b0 = _marker(base, "user:a"), _marker(base, "user:b")
            code, _ = _req(base, "/pin", method="POST",
                           body={"episode": "a", "owner": "user:a"})
            self.assertEqual(code, 200)
            self.assertNotEqual(_marker(base, "user:a"), a0)
            self.assertEqual(_marker(base, "user:b"), b0)

    def test_노드를_여는_touch_는_표식을_안_바꾼다(self):
        """앱이 노드를 열 때마다 부른다. 세면 누를 때마다 지도가 다시 그려진다."""
        app = VincApp(store=_Store(), auth=SessionAuth(_TOKEN))
        with _server(app) as base:
            a0 = _marker(base, "user:a")
            _req(base, "/touch", method="POST", body={"node": "concept:a", "owner": "user:a"})
            self.assertEqual(_marker(base, "user:a"), a0)

    def test_실패한_쓰기는_안_센다(self):
        app = VincApp(store=_Store(), auth=SessionAuth(_TOKEN))
        with _server(app) as base:
            a0 = _marker(base, "user:a")
            code, _ = _req(base, "/backup", method="POST", body={"owner": "user:a"})
            self.assertEqual(code, 400)
            self.assertEqual(_marker(base, "user:a"), a0)

    def test_202_잡은_끝난_뒤에_바뀐다(self):
        gate = threading.Event()

        def slow(store, body):
            gate.wait(5)
            return {"executed": True}

        app = VincApp(store=_Store(), auth=SessionAuth(_TOKEN), ingest={"slow": slow})
        with _server(app) as base:
            a0 = _marker(base, "user:a")
            code, obj = _req(base, "/ingest/slow", method="POST", body={"owner": "user:a"})
            self.assertEqual(code, 202)
            self.assertEqual(_marker(base, "user:a"), a0, "접수만으로 바뀌었다")
            gate.set()
            for _ in range(100):
                s, j = _req(base, f"/jobs/{obj['job_id']}")
                if j["status"] in ("done", "error"):
                    break
                threading.Event().wait(0.02)
            self.assertNotEqual(_marker(base, "user:a"), a0)

    def test_스토어가_죽어도_답한다(self):
        """폴링이 503 을 받으면 화면은 "계정에 닿지 않는다"로 읽는다. 표식은 스토어를
        안 읽으므로 그럴 이유가 없다."""
        app = VincApp(store=None, auth=SessionAuth(_TOKEN))
        with _server(app) as base:
            code, obj = _req(base, "/changes?owner=user:a")
            self.assertEqual(code, 200)
            self.assertTrue(obj["marker"])


class HubRegistrationTests(unittest.TestCase):

    def test_허브가_여는_조회이고_열람자도_읽는다(self):
        from vinc_mcp import http_server as hs
        self.assertEqual(hs._PROXY_OPS.get("changes"), ("GET", "/changes"))
        self.assertIn("changes", hs.team_role_tiers()["viewer"])


if __name__ == "__main__":
    unittest.main()
