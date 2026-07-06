# -*- coding: utf-8 -*-
"""사이드카 HTTP 서버 — 설계 §11 P2 게이트.

무드라이버로도 검증 가능한 것:
  ① CLI↔HTTP 응답 동등성 — 강등(무 DB) 결정론 계약에서 HTTP 바디 == CLI stdout.
  ② 잡 실행 중 조회 응답성 — 긴 잡이 워커를 점유한 동안 GET /recall 이 즉시 응답.
  ④ 로컬 인증(F4) — 토큰 없는/틀린 요청 401, /health 무인증.
  + 비동기 잡(202+job_id → 폴링), 라우트 404, fake 스토어 read/write 데이터 왕복.

루프백(127.0.0.1) 임의 포트에 실서버를 띄우고 stdlib urllib 로 때린다(무 서드파티).
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import cli  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp, make_server  # noqa: E402

_TOKEN = "test-token-abc"


@contextlib.contextmanager
def running_server(app):
    httpd = make_server(app, host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        app.close()


def _req(base, path, *, method="GET", body=None, token=_TOKEN):
    url = base + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if token is not None:
        r.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _cli_json(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(argv)
    return json.loads(buf.getvalue())


def _app(store=None):
    # 잡 큐를 실제로 돌리되 스토어는 주입(기본 None → 강등).
    return VincApp(store=store, auth=SessionAuth(_TOKEN))


class AuthTests(unittest.TestCase):
    def test_health_no_auth(self):
        with running_server(_app()) as base:
            code, obj = _req(base, "/health", token=None)
            self.assertEqual(code, 200)
            self.assertEqual(obj["status"], "ok")

    def test_missing_token_401(self):
        with running_server(_app()) as base:
            code, obj = _req(base, "/recall", token=None)
            self.assertEqual(code, 401)

    def test_wrong_token_401(self):
        with running_server(_app()) as base:
            code, _ = _req(base, "/recall", token="nope")
            self.assertEqual(code, 401)

    def test_unknown_route_404(self):
        with running_server(_app()) as base:
            self.assertEqual(_req(base, "/nope")[0], 404)
            self.assertEqual(_req(base, "/nope", method="POST", body={})[0], 404)


class EquivalenceTests(unittest.TestCase):
    """게이트 ① — 강등 결정론에서 HTTP 응답 dict == CLI 산출 dict."""

    GET_CASES = [
        ("/recall?query=x&owner=u&limit=3", ["recall", "--query", "x", "--owner", "u", "--limit", "3"]),
        ("/domains?owner=u", ["domains", "--owner", "u"]),
        ("/learner?owner=u", ["learner", "--owner", "u"]),
        ("/graph?owner=u", ["graph", "--owner", "u"]),
        ("/decide?topic=t&depth=2", ["decide", "--topic", "t", "--depth", "2"]),
        ("/search?query=x&top_k=4", ["search", "--query", "x", "--top-k", "4"]),
        ("/plan/recall?query=x", ["plan", "recall", "--query", "x"]),
        ("/plan/decide?topic=t", ["plan", "decide", "--topic", "t"]),
    ]

    POST_CASES = [
        ("/pin", {"episode": "e1"}, ["pin", "--episode", "e1"]),
        ("/tag", {"episode": "e1", "tags": ["a", "b"]}, ["tag", "--episode", "e1", "--tags", "a,b"]),
        ("/touch", {"episode": "e1"}, ["touch", "--episode", "e1"]),
        ("/activate", {"owner": "u"}, ["activate", "--owner", "u"]),
    ]

    def test_get_endpoints_match_cli(self):
        with running_server(_app()) as base:
            for path, argv in self.GET_CASES:
                code, http_obj = _req(base, path)
                self.assertEqual(code, 200, path)
                self.assertEqual(http_obj, _cli_json(argv), f"불일치: {path}")

    def test_post_endpoints_match_cli(self):
        with running_server(_app()) as base:
            for path, body, argv in self.POST_CASES:
                code, http_obj = _req(base, path, method="POST", body=body)
                self.assertEqual(code, 200, path)
                self.assertEqual(http_obj, _cli_json(argv), f"불일치: {path}")


class ResponsivenessTests(unittest.TestCase):
    """게이트 ② — 긴 잡이 워커를 점유한 동안에도 조회가 막히지 않는다."""

    def test_read_responds_while_job_running(self):
        app = _app()
        release = threading.Event()
        started = threading.Event()

        def slow():
            started.set()
            release.wait(2.0)     # 워커를 붙잡아 둔다
            return {"done": True}

        with running_server(app) as base:
            job = app.jobs.submit("slow", slow)
            self.assertTrue(started.wait(1.0), "잡이 시작되지 않음")
            # 잡이 워커를 점유한 상태에서 조회 — 큐를 우회하므로 즉시 응답해야 한다.
            self.assertFalse(job._done.is_set(), "잡이 이미 끝남 — 테스트 무효")
            code, obj = _req(base, "/recall?owner=u")
            self.assertEqual(code, 200)
            self.assertIn("executed", obj)
            self.assertEqual(app.jobs.get(job.id).status, "running")  # 여전히 실행 중
            release.set()
            job._done.wait(2.0)


class AsyncJobTests(unittest.TestCase):
    def test_ingest_returns_202_and_pollable(self):
        with running_server(_app()) as base:
            code, obj = _req(base, "/ingest/recall", method="POST",
                             body={"episodes": [{"episode_id": "x"}]})
            self.assertEqual(code, 202)
            jid = obj["job_id"]
            # 폴링 — 무드라이버라 강등(executed False)이지만 잡은 done 으로 끝난다.
            for _ in range(50):
                s, jobj = _req(base, f"/jobs/{jid}")
                if jobj["status"] in ("done", "error"):
                    break
            self.assertEqual(jobj["status"], "done", jobj)
            self.assertFalse(jobj["result"]["executed"])   # 무 DB 강등

    def test_unknown_ingest_engine_400(self):
        with running_server(_app()) as base:
            code, _ = _req(base, "/ingest/bogus", method="POST", body={})
            self.assertEqual(code, 400)

    def test_backup_missing_out_400(self):
        with running_server(_app()) as base:
            code, _ = _req(base, "/backup", method="POST", body={})
            self.assertEqual(code, 400)


class FakeStoreDataTests(unittest.TestCase):
    """스토어 주입 시 read/write 가 reader/writer 경로로 실제 데이터를 흘린다."""

    class _Reader:
        def __init__(self, rows): self._rows = rows
        def run(self, cypher, params=None): return list(self._rows)
        def fulltext_search(self, t, k, f=None): return list(self._rows)[:k]
        def vector_search(self, e, k, f=None): return list(self._rows)[:k]
        def close(self): pass

    class _Store:
        driver_name = "fake"
        def __init__(self, rows): self.rows = rows; self.writes = []
        def reader_view(self): return FakeStoreDataTests._Reader(self.rows)
        def run(self, cypher, params=None): self.writes.append((cypher, params)); return list(self.rows)
        def close(self): pass

    def test_recall_returns_reader_rows(self):
        rows = [{"id": "a", "title": "A"}]
        app = _app(store=self._Store(rows))
        with running_server(app) as base:
            code, obj = _req(base, "/recall?owner=u")
            self.assertEqual(code, 200)
            self.assertTrue(obj["executed"])
            self.assertEqual(obj["rows"], rows)

    def test_pin_write_goes_through_writer(self):
        store = self._Store([{"id": "a", "pinned": True}])
        app = _app(store=store)
        with running_server(app) as base:
            code, obj = _req(base, "/pin", method="POST", body={"episode": "a"})
            self.assertEqual(code, 200)
            self.assertTrue(obj["executed"])
            self.assertTrue(any("SET e.pinned" in c for c, _ in store.writes))


class IngestRouteTests(unittest.TestCase):
    """POST /ingest — 인라인 vinc_ingest 어댑터([[vinc.mcp.skeleton]] §7) HTTP 경로."""

    class _FakeSession:
        """engines.fragment_ingest 가 기대하는 세션 표면(run + single())만 최소 구현.
        모든 노드를 신규(비존재)로 취급 — HTTP 라우팅·잡 큐 왕복 검증이 목적이며
        create/match 판별은 tests/test_fragment_ingest.py 가 이미 커버한다."""
        class _Result:
            def single(self_inner):
                return None
        def run(self, cypher, **params):
            self.last = (cypher, params)
            return self._Result()

    class _Store:
        driver_name = "fake"
        def __init__(self):
            self.session_obj = IngestRouteTests._FakeSession()
        def session(self):
            import contextlib
            @contextlib.contextmanager
            def _cm():
                yield self.session_obj
            return _cm()
        def close(self): pass

    def test_no_store_degrades_executed_false(self):
        with running_server(_app()) as base:
            code, obj = _req(base, "/ingest", method="POST", body={
                "nodes": [{"id": "concept:a", "type": "concept", "title": "A"}]})
            self.assertEqual(code, 200)
            self.assertFalse(obj["executed"])
            self.assertEqual(obj["warnings"], ["no reachable store"])

    def test_valid_fragment_merges_via_writer_session(self):
        app = _app(store=self._Store())
        with running_server(app) as base:
            code, obj = _req(base, "/ingest", method="POST", body={
                "nodes": [{"id": "concept:a", "type": "concept", "title": "A"}],
                "edges": [],
            })
            self.assertEqual(code, 200)
            self.assertTrue(obj["executed"])
            self.assertEqual(obj["merged"]["nodes"], {"created": 1, "matched": 0})
            self.assertEqual(obj["warnings"], [])

    def test_unsupported_node_type_reports_warning_over_http(self):
        app = _app(store=self._Store())
        with running_server(app) as base:
            code, obj = _req(base, "/ingest", method="POST", body={
                "nodes": [{"id": "r1", "type": "record", "title": "T"}]})
            self.assertEqual(code, 200)
            self.assertEqual(obj["merged"]["nodes"], {"created": 0, "matched": 0})
            self.assertTrue(any("record" in w for w in obj["warnings"]))


if __name__ == "__main__":
    unittest.main()
