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
        # 요지는 "무인증 준비성 프로브" — 토큰 없이 200 이어야 한다.
        # (status 값 자체는 스토어 상태에 달렸다 — HealthContractTests 참조)
        with running_server(_app()) as base:
            code, obj = _req(base, "/health", token=None)
            self.assertEqual(code, 200)
            self.assertIn(obj["status"], ("ok", "degraded"))


class HealthContractTests(unittest.TestCase):
    """/health 는 **성치 않은 기동을 숨기지 않는다**(2026-07 macOS 리포트 대응):
    사이드카가 떴어도 스토어가 없거나 스키마 생성이 실패했으면 status=degraded +
    사유를 준다. "ok" 만 보고 정상이라 믿으면 뒤따르는 전 조회가 원인 불명으로
    깨지고, 앱에는 health TIMEOUT/빈 결과만 남는다."""

    def test_no_store_is_degraded(self):
        with running_server(_app(store=None)) as base:
            code, obj = _req(base, "/health", token=None)
            self.assertEqual(code, 200)
            self.assertEqual(obj["status"], "degraded")
            self.assertIn("store", obj.get("error", ""))

    def test_provision_error_surfaced(self):
        app = VincApp(store=None, auth=SessionAuth(_TOKEN),
                      provision_error="RuntimeError: boom")
        with running_server(app) as base:
            code, obj = _req(base, "/health", token=None)
            self.assertEqual(code, 200)
            self.assertEqual(obj["status"], "degraded")
            self.assertEqual(obj["provision_error"], "RuntimeError: boom")

    def test_healthy_store_is_ok(self):
        # 스토어가 성하면 그대로 ok — 강등 신호가 남발되지 않아야 한다.
        class _FakeStore:
            driver_name = "fake"

            def close(self):   # VincApp.close() 가 종료 시 호출
                pass
        with running_server(_app(store=_FakeStore())) as base:
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
    """POST /ingest — 적재 단일화(2026-07-11): 레거시 fragment 경로(Record/props,
    api/fragment.merge_fragment)로 수렴. 테마 적재(Entity/TYPED)는 휴면. MERGE
    create/match 판별·상세 계약은 tests/test_atlas.py 가 커버(여기선 HTTP 왕복)."""

    class _Store:
        """레거시 merge_fragment 는 store.run(cypher, params)->list 를 쓴다(count 는
        빈 리스트=신규). test_atlas 의 더블과 동형 — HTTP 왕복만 검증한다."""
        driver_name = "fake"
        def run(self, cypher, params=None):
            return []
        def close(self): pass

    def test_no_theme_id_required(self):
        # 단일화 핵심: theme_id 없이도 적재된다(구 D11-R2 400 reject 폐지).
        app = _app(store=self._Store())
        with running_server(app) as base:
            code, obj = _req(base, "/ingest", method="POST", body={
                "nodes": [{"id": "concept:a", "type": "concept", "title": "A"}],
                "edges": []})
            self.assertEqual(code, 200)
            self.assertTrue(obj["executed"])
            self.assertEqual(obj["merged"]["nodes"], {"created": 1, "matched": 0})

    def test_no_store_degrades_executed_false(self):
        with running_server(_app()) as base:
            code, obj = _req(base, "/ingest", method="POST", body={
                "nodes": [{"id": "concept:a", "type": "concept", "title": "A"}]})
            self.assertEqual(code, 200)
            self.assertFalse(obj["executed"])
            self.assertEqual(obj["warnings"], ["no reachable store"])

    def test_ingest_and_ingest_fragment_are_the_same_path(self):
        # /ingest 와 /ingest/fragment 가 동일 레거시 경로임을 HTTP 로 확인.
        app = _app(store=self._Store())
        with running_server(app) as base:
            a = _req(base, "/ingest", method="POST", body={
                "nodes": [{"id": "concept:x", "type": "concept"}]})[1]
            b = _req(base, "/ingest/fragment", method="POST", body={
                "nodes": [{"id": "concept:x", "type": "concept"}]})[1]
            self.assertEqual(a["merged"], b["merged"])


class ThemeTypeRouteTests(unittest.TestCase):
    """주제·타입 어휘 CRUD 라우트 — data_dir 없는 인메모리 ThemeStore(오프라인)."""

    def test_theme_create_list_activate(self):
        with running_server(_app()) as base:
            code, obj = _req(base, "/themes", method="POST",
                             body={"theme_id": "fashion", "title": "F", "guiding_questions": ["q?"]})
            self.assertEqual(code, 200)
            self.assertEqual(obj["theme"]["theme_id"], "fashion")
            # 시드된 어휘(구 enum → proposed)
            code, obj = _req(base, "/types?theme_id=fashion&status=proposed")
            self.assertEqual(code, 200)
            names = {t["name"] for t in obj["types"]}
            self.assertIn("concept", names)
            code, obj = _req(base, "/themes")
            self.assertEqual(obj["active_theme_id"], "fashion")

    def test_ratify_moves_proposed_to_authored(self):
        with running_server(_app()) as base:
            _req(base, "/themes", method="POST", body={"theme_id": "t"})
            code, obj = _req(base, "/types/ratify", method="POST",
                             body={"theme_id": "t", "name": "concept"})
            self.assertEqual(code, 200)
            self.assertEqual(obj["type"]["status"], "authored")

    def test_ratify_unknown_theme_404(self):
        with running_server(_app()) as base:
            code, _ = _req(base, "/types/ratify", method="POST",
                           body={"theme_id": "ghost", "name": "concept"})
            self.assertEqual(code, 404)

    def test_types_requires_theme_id(self):
        with running_server(_app()) as base:
            code, _ = _req(base, "/types")
            self.assertEqual(code, 400)


class ConnectFlowTests(unittest.TestCase):
    """계정 연결(디바이스 플로우) — 메일박스 계약: start(인증) → callback(무인증,
    state 단일사용 게이트 + Origin 화이트리스트) → status(인증, 1회 전달 후 소거)."""

    def test_full_roundtrip_delivers_key_once(self):
        with running_server(_app()) as base:
            code, st = _req(base, "/connect/start", method="POST", body={})
            self.assertEqual(code, 200)
            state = st["state"]
            self.assertGreaterEqual(len(state), 32)
            # 대기 중
            code, obj = _req(base, f"/connect/status?state={state}")
            self.assertEqual((code, obj["status"]), (200, "pending"))
            # 브라우저 콜백(무인증 — token=None)
            code, obj = _req(base, "/connect/callback", method="POST", token=None,
                             body={"state": state, "key": "vinc_sk_abc123",
                                   "email": "u@example.com"})
            self.assertEqual((code, obj["status"]), (200, "ok"))
            # 1회 전달
            code, obj = _req(base, f"/connect/status?state={state}")
            self.assertEqual(code, 200)
            self.assertEqual(obj["status"], "delivered")
            self.assertEqual(obj["key"], "vinc_sk_abc123")
            self.assertEqual(obj["email"], "u@example.com")
            # 소거 확인(재조회는 expired)
            code, obj = _req(base, f"/connect/status?state={state}")
            self.assertEqual(obj["status"], "expired")

    def test_callback_rejects_bad_state_key_and_reuse(self):
        with running_server(_app()) as base:
            _, st = _req(base, "/connect/start", method="POST", body={})
            state = st["state"]
            # 미지 state
            code, _ = _req(base, "/connect/callback", method="POST", token=None,
                           body={"state": "ghost", "key": "vinc_sk_x"})
            self.assertEqual(code, 400)
            # 키 형태 오류(vinc_sk_ 접두 아님)
            code, _ = _req(base, "/connect/callback", method="POST", token=None,
                           body={"state": state, "key": "hunter2"})
            self.assertEqual(code, 400)
            # 정상 1회 → 같은 state 재사용 거부(단일사용)
            code, _ = _req(base, "/connect/callback", method="POST", token=None,
                           body={"state": state, "key": "vinc_sk_ok"})
            self.assertEqual(code, 200)
            code, _ = _req(base, "/connect/callback", method="POST", token=None,
                           body={"state": state, "key": "vinc_sk_evil"})
            self.assertEqual(code, 400)

    def test_callback_origin_whitelist(self):
        with running_server(_app()) as base:
            _, st = _req(base, "/connect/start", method="POST", body={})
            state = st["state"]
            url = base + "/connect/callback"
            data = json.dumps({"state": state, "key": "vinc_sk_x"}).encode()
            r = urllib.request.Request(url, data=data, method="POST")
            r.add_header("Content-Type", "application/json")
            r.add_header("Origin", "https://evil.example")
            try:
                with urllib.request.urlopen(r, timeout=5) as resp:
                    code = resp.status
            except urllib.error.HTTPError as e:
                code = e.code
            self.assertEqual(code, 403)   # 브라우저 교차오리진은 화이트리스트만

    def test_start_and_status_require_auth(self):
        with running_server(_app()) as base:
            code, _ = _req(base, "/connect/start", method="POST", body={}, token=None)
            self.assertEqual(code, 401)
            code, _ = _req(base, "/connect/status?state=x", token=None)
            self.assertEqual(code, 401)


if __name__ == "__main__":
    unittest.main()
