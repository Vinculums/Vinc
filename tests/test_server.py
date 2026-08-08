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
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import cli  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp, make_server  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()

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
        # GM-14 — detail 은 정적 등가 케이스로. cursor 는 값이 불투명(코덱 내부)이라
        # 정적 케이스가 불가능 — 아래 CursorRouteTests 가 동적 등가로 커버한다.
        ("/search?query=x&top_k=4&detail=full",
         ["search", "--query", "x", "--top-k", "4", "--detail", "full"]),
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


class CursorRouteTests(unittest.TestCase):
    """GM-14 — /search 커서 라우팅: 무효 커서 400(조용한 폴백 금지) + 페이지 이어읽기."""

    class _Store:
        driver_name = "fake"
        def __init__(self):
            self.rows = [{"id": f"d#{i:05d}", "text": f"본문 {i}", "score": float(99 - i)}
                         for i in range(30)]
        def reader_view(self): return self
        def fulltext_search(self, t, k, f=None): return [dict(r) for r in self.rows[:int(k)]]
        def run(self, cypher, params=None): return []
        def close(self): pass

    def test_무효_커서는_400_명시_오류다(self):
        with running_server(_app(store=self._Store())) as base:
            code, obj = _req(base, "/search?query=x&cursor=%EC%93%B0%EB%A0%88%EA%B8%B0")
            self.assertEqual(code, 400, "무효 커서가 200 으로 조용히 폴백했다")
            self.assertTrue(str(obj.get("error", "")).startswith("invalid_cursor"))
            self.assertEqual(obj.get("rows"), [])

    def test_무효_커서_오류_바디는_CLI_와_같다(self):
        """게이트① 동등성의 동적 케이스 — 커서 값이 불투명해 GET_CASES 에 못 놓는다.
        HTTP 는 상태만 400 으로 다르고 **바디는 CLI stdout 과 동일**해야 한다
        (오류 판정이 q.search 한 곳에서만 이뤄진다는 증거)."""
        with running_server(_app()) as base:      # 무스토어 강등 — CLI 와 같은 조건
            code, obj = _req(base, "/search?query=x&cursor=junk")
            self.assertEqual(code, 400)
            self.assertEqual(obj, _cli_json(
                ["search", "--query", "x", "--cursor", "junk"]))

    def test_next_cursor_로_이어_읽으면_누락_중복이_없다(self):
        import urllib.parse
        with running_server(_app(store=self._Store())) as base:
            code, page1 = _req(base, "/search?query=x&top_k=10")
            self.assertEqual(code, 200)
            self.assertTrue(page1["has_more"])
            self.assertEqual(page1["total_count"], 30)
            cur = urllib.parse.quote(page1["next_cursor"], safe="")
            code, page2 = _req(base, f"/search?query=x&top_k=10&cursor={cur}")
            self.assertEqual(code, 200)
            ids1 = {r["id"] for r in page1["rows"]}
            ids2 = {r["id"] for r in page2["rows"]}
            self.assertEqual(len(ids1), 10)
            self.assertEqual(len(ids2), 10)
            self.assertFalse(ids1 & ids2, "페이지 사이에 중복이 있다")


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


@contextlib.contextmanager
def _encode_stub(vector, *, seen: dict | None = None, delay: float = 0.0):
    """워커 인코딩 창구(`packaging/embed_worker.py` `serve_encode`)와 **같은 계약**을
    말하는 최소 대역. 모델 없이 배선만 검증하려고 둔다(실모델은 440MB 다운로드가 필요)."""
    import json as _json
    import threading as _th
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_POST(self):
            if delay:
                time.sleep(delay)
            n = int(self.headers.get("Content-Length") or 0)
            body = _json.loads(self.rfile.read(n) or b"{}")
            if seen is not None:
                seen.update(body)
                # 헤더도 남긴다 — 창구가 루프백을 벗어나면서 인증이 붙었고, 그걸
                # 안 싣는 회귀가 실제로 났다(2026-08-07: 질의 임베딩 전부 401).
                seen["_authorization"] = self.headers.get("Authorization")
            out = _json.dumps({"vectors": [list(vector)]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    _th.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


class QueryEmbeddingWiringTests(unittest.TestCase):
    """검색이 **쿼리 임베딩을 실제로 흘리는가** — 그리고 못 흘릴 땐 정직하게 말하는가.

    2026-07-31 까지 임베딩은 **write-only** 였다. 워커가 벡터를 채웠지만 `q.search` 를
    부르는 프로덕션 호출부 둘(`read_search`·CLI)이 `query_embedding` 을 넘기지 않아
    `store.vector_search` 가 한 번도 실행되지 않았다. 에러가 없으니 아무도 몰랐다 —
    이 리포의 상습 실패 양식(조용한 강등)이다.

    임베더는 사이드카에 없다(torch 미번들). 그래서 워커가 여는 루프백 창구에 물어보는데,
    그 창구는 없을 수도(데스크탑) 죽었을 수도 느릴 수도 있다. 어느 경우든 **검색은 답해야
    하고, 무슨 일이 있었는지는 응답에 남아야 한다.**
    """

    class _Reader:
        # SEC-1: 실제 리더 뷰를 흉내내는 더블이므로 owner 필터 적용을 선언한다.
        # 이 표식이 없으면 `api.query.search` 가 owner 스코프 요청에서 청크 팔을
        # 아예 돌리지 않는다(fail-closed — 위험 감사 1.1). 이 테스트는 owner="u" 로
        # 검색하므로 표식이 없으면 벡터 팔 자체가 안 돈다.
        scoped_chunk_search = True

        def __init__(self, outer): self.o = outer
        def run(self, cypher, params=None): return []
        def fulltext_search(self, t, k, f=None): return [{"id": "c1", "text": "본문", "score": 1.0}]
        def vector_search(self, e, k, f=None):
            self.o.seen_vec.append(list(e))
            return [{"id": "c1", "text": "본문", "distance": 0.1}]
        def close(self): pass

    class _Store:
        driver_name = "fake"
        def __init__(self, outer): self.o = outer
        def reader_view(self): return QueryEmbeddingWiringTests._Reader(self.o)
        def run(self, cypher, params=None): return []
        def close(self): pass

    def setUp(self):
        self.seen_vec = []
        self._saved = {k: os.environ.get(k)
                       for k in ("VINC_EMBED_ENCODE", "VINC_EMBED_ENCODE_TIMEOUT",
                                 "VINC_EMBED_ENCODE_TOKEN")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _search(self):
        app = _app(store=self._Store(self))
        try:
            return app.read_search({"query": "양자역학", "owner": "u"})[1]
        finally:
            app.jobs.shutdown(drain=True)

    def test_창구가_없으면_시도조차_안_하고_FTS_로_답한다(self):
        """데스크탑의 정상 상태다. 매 검색마다 연결 실패를 기다리면 그 자체로 성능 사고."""
        os.environ.pop("VINC_EMBED_ENCODE", None)
        res = self._search()
        self.assertEqual(res.get("arms"), "fts")
        self.assertFalse(res.get("vector_note"), "시도하지 않았는데 사유가 남았다")
        self.assertEqual(self.seen_vec, [], "창구가 없는데 벡터 팔이 돌았다")

    def test_창구가_있으면_벡터_팔이_실제로_돈다(self):
        with _encode_stub([0.5, 0.5, 0.0, 0.0]) as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            res = self._search()
        self.assertEqual(res.get("arms"), "fts+vector",
                         "쿼리 임베딩이 흐르지 않는다 — write-only 회귀")
        self.assertEqual(self.seen_vec, [[0.5, 0.5, 0.0, 0.0]],
                         "vector_search 가 받은 벡터가 창구 응답과 다르다")

    def test_창구가_죽어도_검색은_답하고_사유를_남긴다(self):
        os.environ["VINC_EMBED_ENCODE"] = "http://127.0.0.1:9"   # discard 포트
        os.environ["VINC_EMBED_ENCODE_TIMEOUT"] = "0.3"
        res = self._search()
        self.assertEqual(res.get("arms"), "fts")
        self.assertTrue(res.get("vector_note"), "조용히 강등됐다 — 사유가 없다")
        self.assertEqual(self.seen_vec, [])

    def test_모델_이행_중에는_벡터_팔을_쓰지_않는다(self):
        """발주자 결정: 재임베딩 기간에는 **안전한 방향**으로 FTS 만.

        이행 구간에는 새 모델로 채워진 문서와 아직 빈 문서가 섞여 있어, 벡터 팔이
        "이미 처리된 문서" 쪽으로 결과를 기울인다. 커버리지가 반쪽인 채로 순위에 끼는
        것보다 FTS 로만 답하는 편이 정직하다 — 그리고 그 사실을 응답에 남긴다."""
        def _meta_rows(cypher):
            if "_SchemaMeta" in cypher:
                return [{"v": "model-A"}]
            if "needs_embed" in cypher:
                return [{"c": 7}]                # 아직 7건 남았다
            return []

        class _MigReader(QueryEmbeddingWiringTests._Reader):
            # ⚠ 서버는 **리더 경로**로 상태를 읽는다(_embed_migrating → _reader()).
            # 스토어의 run 만 바꾸면 이 검사가 통과해 버려 아무것도 검증하지 못한다.
            def run(self, cypher, params=None):
                return _meta_rows(cypher)

        class _MigStore(QueryEmbeddingWiringTests._Store):
            def reader_view(self):
                return _MigReader(self.o)

            def run(self, cypher, params=None):
                return _meta_rows(cypher)

        with _encode_stub([1.0, 0, 0, 0]) as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            app = _app(store=_MigStore(self))
            try:
                res = app.read_search({"query": "양자역학", "owner": "u"})[1]
            finally:
                app.jobs.shutdown(drain=True)
        self.assertEqual(res.get("arms"), "fts", "이행 중인데 벡터 팔이 돌았다")
        self.assertIn("migration", res.get("vector_note") or "",
                      "이행 때문이라는 사유가 응답에 없다 — 조용한 강등")
        # GM-13 — 배지는 영문 문장(vector_note, em 대시 포함)을 파싱하지 않는다.
        # 구조화 진행도가 함께 실려야 화면이 카피 덱 문자열로 제 문장을 쓸 수 있다.
        self.assertEqual(res.get("embed_migration"), {"pending": 7},
                         "이행 진행도(embed_migration.pending)가 구조화되지 않았다")
        self.assertEqual(self.seen_vec, [])

    def test_쿼리에는_query_kind_로_요청한다(self):
        """bge 는 **쿼리에만** 지시 접두를 붙인다. passage 로 요청하면 저장 벡터와
        같은 공간이 되지 않는다 — 창구가 kind 를 보고 접두를 고르므로 여기가 계약이다."""
        seen: dict = {}
        with _encode_stub([1.0, 0.0, 0.0, 0.0], seen=seen) as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            self._search()
        self.assertEqual(seen.get("kind"), "query", f"kind 가 틀렸다: {seen}")
        self.assertEqual(seen.get("texts"), ["양자역학"])

    def test_창구에_토큰이_필요하면_실어_보낸다(self):
        """실제로 난 회귀다(2026-08-07). 모델을 `vinc-encode` 로 분리하면서 창구가
        루프백을 벗어나 인증이 붙었는데, **배치 워커만 고치고 이 경로를 빠뜨려**
        질의 임베딩이 전부 401 이었다. 배경 적재는 정상이라 겉으로는 다 되는 것처럼
        보였고 `vector_note` 한 줄이 유일한 신호였다."""
        seen: dict = {}
        with _encode_stub([1.0, 0.0, 0.0, 0.0], seen=seen) as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            os.environ["VINC_EMBED_ENCODE_TOKEN"] = "s3cret"
            self._search()
        self.assertEqual(seen.get("_authorization"), "Bearer s3cret",
                         f"인코더 토큰을 안 실었다: {seen.get('_authorization')!r}")

    def test_토큰이_없으면_헤더를_붙이지_않는다(self):
        """루프백 창구(데스크탑·구 허브)는 무토큰이다. 빈 헤더를 붙여 보내면 그쪽이
        401 을 줄 이유가 생긴다."""
        seen: dict = {}
        with _encode_stub([1.0, 0.0, 0.0, 0.0], seen=seen) as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            os.environ.pop("VINC_EMBED_ENCODE_TOKEN", None)
            self._search()
        self.assertIsNone(seen.get("_authorization"))


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


class DeleteEdgeRouteTests(unittest.TestCase):
    def test_route_requires_fields_and_degrades(self):
        with running_server(_app()) as base:
            code, obj = _req(base, "/delete-edge", method="POST",
                             body={"from": "concept:a"})
            self.assertEqual(code, 400)                      # to/type 누락
            code, obj = _req(base, "/delete-edge", method="POST",
                             body={"from": "concept:a", "to": "concept:b",
                                   "type": "relates_to"})
            self.assertEqual(code, 200)                      # store=None → 강등 계약
            self.assertFalse(obj["executed"])


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
            # 브라우저 콜백(무인증 — token=None). username(009 핸들)도 함께 전달.
            code, obj = _req(base, "/connect/callback", method="POST", token=None,
                             body={"state": state, "key": "vinc_sk_abc123",
                                   "email": "u@example.com", "username": "someuser"})
            self.assertEqual((code, obj["status"]), (200, "ok"))
            # 1회 전달
            code, obj = _req(base, f"/connect/status?state={state}")
            self.assertEqual(code, 200)
            self.assertEqual(obj["status"], "delivered")
            self.assertEqual(obj["key"], "vinc_sk_abc123")
            self.assertEqual(obj["email"], "u@example.com")
            self.assertEqual(obj["username"], "someuser")
            # 소거 확인(재조회는 expired)
            code, obj = _req(base, f"/connect/status?state={state}")
            self.assertEqual(obj["status"], "expired")

    def test_callback_username_optional_defaults_empty(self):
        with running_server(_app()) as base:
            _, st = _req(base, "/connect/start", method="POST", body={})
            state = st["state"]
            # username 미포함(구버전 connect-app 호환) → 빈 문자열로 안전 폴백.
            code, obj = _req(base, "/connect/callback", method="POST", token=None,
                             body={"state": state, "key": "vinc_sk_nouser",
                                   "email": "u@example.com"})
            self.assertEqual((code, obj["status"]), (200, "ok"))
            code, obj = _req(base, f"/connect/status?state={state}")
            self.assertEqual(obj["username"], "")

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


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class DocRouteE2E(unittest.TestCase):
    """D45 P4 — 문서 정본 편집 표면(HTTP) 실엔진 왕복: put→get→list→delete."""

    def setUp(self):
        import os
        import tempfile
        from store.config import load_config
        from store.backend import open_store
        self._tmp = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k) for k in
                       ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR")}
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        os.environ.pop("MEMORY_DATA_DIR", None)
        import os as _os
        self.store = open_store(load_config({"data_dir": _os.path.join(self._tmp, "store")}))
        self.assertIsNotNone(self.store, "임베디드 스토어 open 실패")
        self.store.provision(dim=4)
        self.app = VincApp(store=self.store, auth=SessionAuth(_TOKEN))

    def tearDown(self):
        import os
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_reembed_without_stack_keeps_write_queue_alive(self):
        """⚠ 실사고 회귀(2026-07-31) — 임베딩 스택 없는 기기에서 `/doc/reembed` 가
        쓰기 큐 전체를 영구히 잠갔다.

        기전: 라이브러리가 `sys.exit()` → `SystemExit`(BaseException) → 핸들러의
        `except Exception` 도, 잡 워커의 `except Exception` 도 통과 → **워커 스레드
        사망** → 이후 모든 쓰기가 큐에 쌓인 채 무응답(읽기는 별도 커넥션이라 정상
        이어서 더 오진하기 쉬웠다).

        이 테스트가 못박는 것: reembed 가 **정직하게 강등**되고, 그 **뒤에도** 쓰기가
        돈다. 스택이 설치된 환경에서는 reembed 가 성공하지만, 그때도 뒷부분(쓰기
        생존)은 그대로 유효한 검사다."""
        with running_server(self.app) as base:
            code, put = _req(base, "/doc/put", method="POST",
                             body={"owner": "user:a", "text": "# 회귀\n\n본문.\n",
                                   "title": "회귀"})
            self.assertEqual(code, 200)
            did = put["doc_id"]
            # 실사고 경로 — 스택이 없으면 executed=False + note 로 내려앉는다.
            code, re_ = _req(base, "/doc/reembed", method="POST",
                             body={"owner": "user:a", "id": did})
            self.assertEqual(code, 200, f"reembed 가 500 을 냈다: {re_}")
            if not re_.get("executed"):
                self.assertIn("note", re_, "강등이면 사유를 밝혀야 한다")
            # **핵심**: 그 뒤에도 쓰기 큐가 산다(예전엔 여기서 영원히 멈췄다).
            code, ex = _req(base, "/doc/extract", method="POST",
                            body={"owner": "user:a", "id": did, "apply": False})
            self.assertEqual(code, 200, "reembed 뒤 쓰기 큐가 죽었다(워커 사망 회귀)")
            self.assertTrue(ex.get("executed"))
            code, docs = _req(base, "/docs?owner=user:a")
            self.assertEqual(code, 200)

    def test_doc_crud_over_http(self):
        text = "# HTTP 편집\n\n" + "정본 편집 표면 왕복. " * 60
        with running_server(self.app) as base:
            # put(신규) → doc_id
            code, put = _req(base, "/doc/put", method="POST",
                             body={"owner": "user:a", "text": text, "title": "t"})
            self.assertEqual(code, 200)
            did = put["doc_id"]
            self.assertTrue(put["executed"] and not put["skipped"])
            # 무소유 put 거부(SEC-1)
            code, err = _req(base, "/doc/put", method="POST", body={"text": "x"})
            self.assertEqual(code, 400)
            # get(복원 + 검증)
            code, got = _req(base, f"/doc?id={did}&owner=user:a")
            self.assertEqual(code, 200)
            self.assertTrue(got["verified"])
            self.assertEqual(got["text"], text)
            # list
            code, lst = _req(base, "/docs?owner=user:a")
            self.assertEqual(code, 200)
            self.assertEqual(lst["count"], 1)
            # 편집(같은 id) → 재청킹
            code, put2 = _req(base, "/doc/put", method="POST",
                              body={"owner": "user:a", "text": "짧아짐", "id": did, "title": "t"})
            self.assertFalse(put2["skipped"])
            code, got2 = _req(base, f"/doc?id={did}&owner=user:a")
            self.assertEqual(got2["text"], "짧아짐")
            # D45-V — 추출 제안(apply 없음) → 반영 → 노드 클릭 상세(정본 발췌)
            code, put3 = _req(base, "/doc/put", method="POST", body={
                "owner": "user:a", "id": did, "title": "t",
                "text": "# t\n\n## 개념하나\n\n한 줄 핵심입니다.\n본문에서 개념하나를 다룬다.\n"})
            self.assertEqual(code, 200)
            code, prop = _req(base, "/doc/extract", method="POST",
                              body={"id": did, "owner": "user:a"})
            self.assertEqual(code, 200)
            self.assertEqual([c["title"] for c in prop["candidates"]], ["개념하나"])
            code, ap = _req(base, "/doc/extract", method="POST",
                            body={"id": did, "owner": "user:a", "apply": True})
            self.assertEqual(ap["applied"]["created"], 1)
            import urllib.parse as _up
            code, ctx = _req(base, "/node-context?node="
                             + _up.quote("concept:개념하나") + "&owner=user:a")
            self.assertEqual(code, 200)
            self.assertTrue(ctx["executed"])
            self.assertEqual(ctx["one_liner"], "한 줄 핵심입니다.")
            self.assertEqual(ctx["arm"], "mentions")
            self.assertGreater(len(ctx["excerpts"]), 0)
            # 무소유 추출 거부
            code, _ = _req(base, "/doc/extract", method="POST", body={"id": did})
            self.assertEqual(code, 400)
            # D45-V 1단계 — /edge-context: 관계 evidence·confidence + 양끝 메타
            self.store.run(
                "MERGE (x:Concept {concept_id:'concept:x'}) SET x.title='X', "
                "x.one_liner='엑스', x.owner_id='user:a'")
            self.store.run(
                "MATCH (a:Concept {concept_id:'concept:개념하나'}), "
                "(b:Concept {concept_id:'concept:x'}) "
                "MERGE (a)-[r:DERIVES_FROM]->(b) SET r.evidence='근거 구절', r.confidence=0.6")
            code, ec = _req(base, "/edge-context?src=" + _up.quote("concept:개념하나")
                            + "&dst=concept:x&type=DERIVES_FROM&owner=user:a")
            self.assertEqual(code, 200)
            self.assertTrue(ec["executed"])
            self.assertEqual(ec["evidence"], "근거 구절")
            self.assertEqual(ec["dst"]["title"], "X")
            # 화이트리스트 밖 타입 → executed=False(주입 차단)
            code, bad = _req(base, "/edge-context?src=a&dst=b&type=MENTIONS&owner=user:a")
            self.assertFalse(bad["executed"])
            # D45-V2 2단계 — /doc/highlights: 전문 내 개념 언급 char 범위
            self.store.run("MERGE (c:Concept {concept_id:'concept:개념하나'}) "
                           "SET c.stem='개념하나'")
            code, hl = _req(base, "/doc/highlights?doc_id=" + did
                            + "&concept_id=" + _up.quote("concept:개념하나") + "&owner=user:a")
            self.assertEqual(code, 200)
            self.assertTrue(hl["executed"])
            self.assertEqual(hl["method"], "tokens")
            # D45-V2 4단계 — /docs-in-range: 기간 항목(Episode.date)
            self.store.run("MERGE (e:Episode {episode_id:'ev1'}) SET e.owner_id='user:a', "
                           "e.title='EV', e.date='2026-05-15', e.domain='d'")
            code, rng = _req(base, "/docs-in-range?start=2026-05-01&end=2026-06-01&owner=user:a")
            self.assertEqual(code, 200)
            self.assertTrue(rng["executed"])
            self.assertIn("ev1", {i["id"] for i in rng["items"]})
            # D69 — /nav-tree: 좌측 문서 트리(주제 → 문서 + 착지 노드).
            # 추출로 만든 개념은 domain 이 비어 있다(귀속은 사용자 몫 — docstore).
            # 그래서 주제를 붙이기 전까지 문서는 **미분류**다. 억지 배정하지 않는다.
            code, nav = _req(base, "/nav-tree?owner=user:a")
            self.assertEqual(code, 200)
            self.assertTrue(nav["executed"])
            self.assertIn(did, {d["doc_id"] for d in nav["unfiled"]})
            self.store.run("MATCH (c:Concept {concept_id:'concept:개념하나'}) "
                           "SET c.domain='d'")
            code, nav = _req(base, "/nav-tree?owner=user:a")
            landed = {d["node_id"] for g in nav["domains"] for d in g["docs"]}
            self.assertIn("concept:개념하나", landed)
            # owner 없이는 400(SEC-1 — 무소유 트리는 없다)
            code, _ = _req(base, "/nav-tree")
            self.assertEqual(code, 400)
            # delete
            code, dele = _req(base, "/doc/delete", method="POST",
                              body={"id": did, "owner": "user:a"})
            self.assertTrue(dele["executed"])
            code, gone = _req(base, f"/doc?id={did}&owner=user:a")
            self.assertIn("error", gone)

    def test_doc_put_carries_origin(self):
        """origin(파일 import 시 파일명)이 라우트를 타고 정체성·목록까지 흐른다.

        origin 은 `make_doc_id` 산식에 들어가므로 같은 본문이라도 origin 이 다르면
        다른 문서가 되고, 같으면 목록에서 그 파일을 되찾아 **갱신**할 수 있다 —
        드롭존이 같은 파일 재드롭을 새 문서로 늘리지 않는 근거가 이것이다."""
        with running_server(self.app) as base:
            body = {"owner": "user:a", "text": "# 파일\n\n본문.", "title": "노트"}
            code, a = _req(base, "/doc/put", method="POST",
                           body={**body, "origin": "notes.md"})
            self.assertEqual(code, 200)
            code, b = _req(base, "/doc/put", method="POST",
                           body={**body, "origin": "other.md"})
            self.assertEqual(code, 200)
            self.assertNotEqual(a["doc_id"], b["doc_id"])
            # 목록이 origin 을 돌려줘야 클라가 파일명→doc_id 색인을 세울 수 있다.
            code, lst = _req(base, "/docs?owner=user:a")
            self.assertEqual(code, 200)
            by_origin = {d["origin"]: d["doc_id"] for d in lst["docs"]}
            self.assertEqual(by_origin.get("notes.md"), a["doc_id"])
            # 같은 파일 재적재(무변경) → F5 스킵. 실패가 아니라 성공으로 센다.
            code, again = _req(base, "/doc/put", method="POST",
                               body={**body, "origin": "notes.md", "id": a["doc_id"]})
            self.assertTrue(again["executed"] and again["skipped"])

    def test_doc_put_file_route(self):
        """`POST /doc/put-file` — 파일 바이트 입구(base64-in-JSON).

        추출 실패는 500 이 아니라 executed=False + 안정 코드로 내려와야 한다:
        프론트가 크기·형식 문제를 '읽지 못했습니다' 로 뭉개지 않고 사유별 문구를
        줄 수 있어야 하기 때문이다."""
        import base64 as _b64
        with running_server(self.app) as base:
            payload = "# 파일 입구\n\n본문 한 줄.\n".encode("utf-8")
            code, res = _req(base, "/doc/put-file", method="POST", body={
                "owner": "user:a", "filename": "입구.md",
                "data_b64": _b64.b64encode(payload).decode()})
            self.assertEqual(code, 200, res)
            self.assertTrue(res["executed"], res)
            self.assertEqual(res["source_format"], "text")
            did = res["doc_id"]
            # title·origin 은 파일명에서 채워진다(클라가 안 줘도 정체성이 선다).
            code, lst = _req(base, "/docs?owner=user:a")
            row = next(d for d in lst["docs"] if d["doc_id"] == did)
            self.assertEqual((row["title"], row["origin"]), ("입구", "입구.md"))
            code, got = _req(base, f"/doc?id={did}&owner=user:a")
            self.assertEqual(got["text"], payload.decode("utf-8"))
            # cp949 저장본도 살아 돌아온다(utf-8 하드코딩이던 시절의 실패 지점).
            code, cp = _req(base, "/doc/put-file", method="POST", body={
                "owner": "user:a", "filename": "메모.txt",
                "data_b64": _b64.b64encode("한글 메모".encode("cp949")).decode()})
            self.assertTrue(cp["executed"], cp)
            code, got2 = _req(base, f"/doc?id={cp['doc_id']}&owner=user:a")
            self.assertEqual(got2["text"], "한글 메모")
            # 미지원 형식 → 강등(200 + 사유 코드), 문서는 만들지 않는다.
            code, bad = _req(base, "/doc/put-file", method="POST", body={
                "owner": "user:a", "filename": "sheet.xlsx",
                "data_b64": _b64.b64encode(b"PK\x03\x04").decode()})
            self.assertEqual(code, 200)
            self.assertFalse(bad["executed"])
            self.assertEqual(bad["error"], "unsupported_format")
            # 깨진 PDF → pypdf 유무와 무관하게 강등(설치되면 unreadable, 없으면 missing).
            code, pdf = _req(base, "/doc/put-file", method="POST", body={
                "owner": "user:a", "filename": "x.pdf",
                "data_b64": _b64.b64encode(b"%PDF-1.4\nnope").decode()})
            self.assertFalse(pdf["executed"])
            self.assertIn(pdf["error"], ("pdf_support_missing", "pdf_unreadable"))
            # 무소유·깨진 base64·빈 파일은 400(계약 위반이지 강등이 아니다).
            code, _ = _req(base, "/doc/put-file", method="POST",
                           body={"filename": "a.md", "data_b64": "aGk="})
            self.assertEqual(code, 400)
            code, _ = _req(base, "/doc/put-file", method="POST", body={
                "owner": "user:a", "filename": "a.md", "data_b64": "!!not base64!!"})
            self.assertEqual(code, 400)
            code, _ = _req(base, "/doc/put-file", method="POST", body={
                "owner": "user:a", "filename": "a.md", "data_b64": ""})
            self.assertEqual(code, 400)

    def test_sync_docs_export_and_apply_routes(self):
        """`GET /sync/docs-export` · `POST /sync/docs-apply` 회귀 게이트.

        두 핸들러는 `docsync` 를 부르는데 server.py 임포트 블록에 그 모듈이 없어
        **NameError 500** 이었다. 기존 test_docsync 는 모듈을 직접 임포트해 라우트
        경유를 안 타므로 이 구멍을 못 봤다 — 그래서 라우트 레벨로 못 박는다."""
        with running_server(self.app) as base:
            code, put = _req(base, "/doc/put", method="POST",
                             body={"owner": "user:a", "text": "동기화 본문.",
                                   "title": "S", "origin": "s.md"})
            self.assertEqual(code, 200)
            did = put["doc_id"]
            code, exp = _req(base, "/sync/docs-export?owner=user:a&since=0")
            self.assertEqual(code, 200, exp)
            self.assertEqual([d["doc_id"] for d in exp["docs"]], [did])
            self.assertGreaterEqual(exp["cursor"], 1)
            # 남의 델타는 SEC-1 로 튕긴다(로컬 문서 소유자가 다르면 적용 안 함).
            code, other = _req(base, "/sync/docs-apply", method="POST",
                               body={"owner": "user:b", "payload": exp})
            self.assertEqual(code, 200, other)
            self.assertEqual((other["applied"], other["skipped"]), (0, 1))
            # 삭제 후 같은 델타 재적용 → 복원(수신측 재청킹 경로가 살아 있다).
            code, _ = _req(base, "/doc/delete", method="POST",
                           body={"id": did, "owner": "user:a"})
            self.assertEqual(code, 200)
            code, back = _req(base, "/sync/docs-apply", method="POST",
                              body={"owner": "user:a", "payload": exp})
            self.assertEqual(code, 200, back)
            self.assertEqual(back["applied"], 1)
            code, got = _req(base, f"/doc?id={did}&owner=user:a")
            self.assertEqual(got["text"], "동기화 본문.")


if __name__ == "__main__":
    unittest.main()


class DebugPlanOptInTests(unittest.TestCase):
    """F3 — 내부 실행 계획과 owner UUID 가 HTTP 응답에 기본으로 실리지 않는다.

    실측(2026-08-07): `vinc_recall` 17.9KB · `vinc_domains` 1.3KB 응답에 `plan.cypher`
    전문과 `plan.params.owner` 회원 UUID 가 매 호출 실렸다. 나머지 12개 도구는
    이미 안 실었으므로 봉투 불일치이기도 했다.
    """

    class _Store:
        driver_name = "fake"
        def reader_view(self): return self
        def run(self, cypher, params=None): return []
        def close(self): pass

    def _get(self, path):
        with running_server(_app(store=self._Store())) as base:
            return _req(base, path)[1]

    def test_기본_응답에는_plan_이_없다(self):
        for path in ("/recall?query=x&owner=u", "/domains?owner=u"):
            res = self._get(path)
            self.assertNotIn("plan", res, f"{path} 가 plan 을 실었다")
            self.assertNotIn("u", str(res.get("plan") or ""), "owner 가 에코됐다")

    def test_debug_로만_열린다(self):
        res = self._get("/recall?query=x&owner=u&debug=true")
        self.assertEqual(res["plan"]["kind"], "recall")
        self.assertIn("cypher", res["plan"])

    def test_문자열_false_는_켜지지_않는다(self):
        """`bool("false")` 는 True 다 — 이 자리의 고전적 함정. 쿼리스트링으로 오는
        값은 전부 문자열이라, 순진하게 bool() 하면 옵트인이 **항상 켜진다.**"""
        for falsy in ("false", "0", "no", "off", ""):
            res = self._get(f"/recall?query=x&owner=u&debug={falsy}")
            self.assertNotIn("plan", res, f"debug={falsy!r} 인데 plan 이 실렸다")

    def test_plan_전용_라우트는_그대로다(self):
        """진단 능력을 없앤 게 아니다 — 원래 그 목적인 라우트는 불변이다."""
        res = self._get("/plan/recall?query=x")
        self.assertEqual(res["kind"], "recall")
        self.assertIn("cypher", res)
