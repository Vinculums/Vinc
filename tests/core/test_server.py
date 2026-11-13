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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store import pkns  # noqa: E402


def _pk(nid: str, owner: str = "user:a") -> str:
    """논리 슬러그 → 스토어 바인딩용 물리 키.

    이 e2e 는 HTTP 로 쓰고 스토어를 **직접** 읽고 심는다. PK 네임스페이싱이 켜지면
    API 쪽 쓰기가 owner 를 합성하므로 직접 층도 같은 키를 써야 서로를 찾는다."""
    return pkns.key(owner, nid)

from api import cli  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp, make_server  # noqa: E402
from store.embedded_backend import import_driver  # noqa: E402

_DRIVER, _NAME = import_driver()

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
    # 잡 큐를 실제로 돌리되 스토어는 주입(기본 None → **조회 거절**, 아래 참조).
    return VincApp(store=store, auth=SessionAuth(_TOKEN))


class _EmptyStore:
    """무엇을 물어도 빈 행을 주는 스토어 — "열려는 있고 내용이 없다".

    `store=None`("열리지 않았다")과 **다른 상태**다. 둘을 같은 것으로 다루던 것이
    이 파일이 오래 갖고 있던 결함이다: 여러 테스트가 `_app()`(무스토어)로 조회
    라우트를 때리고 200 을 기대했는데, 그게 통한 이유는 `api.query.execute` 가
    `store=None` 을 받으면 **자기가 스토어를 다시 열어** CLI 와 같은 답을 냈기
    때문이다. 즉 그 200 은 사이드카가 스토어를 쓴 결과가 아니라 우회로의 산물이고,
    실제 락 충돌 상황에서는 그 우회로도 실패한다. 이제 조회는 거절되므로(503),
    "빈 결과"를 보고 싶은 테스트는 빈 스토어를 명시로 준다."""

    driver_name = "fake"

    def reader_view(self): return self
    def run(self, cypher, params=None): return []
    def fulltext_search(self, text, top_k, filters=None): return []
    def vector_search(self, embedding, top_k, filters=None): return []
    def close(self): pass


@contextlib.contextmanager
def cli_store(store):
    """CLI 쪽(별도 프로세스 모형)이 열 스토어를 못박는다 — 게이트① 동등성 대조용.

    사이드카는 공유 스토어를 들고 있고 CLI 는 매 호출 자기가 연다. 동등성을 보려면
    **양쪽이 같은 내용을 읽어야** 하는데, 임베디드 DB 는 단일 writer 라 실파일로는
    동시에 못 연다. 그래서 CLI 가 여는 지점(`api.query.open_store`)을 서버에 주입한
    것과 같은 대역으로 바꾼다. 여는 지점은 셋이다 — `execute` 는 `store.querybase` 에서,
    검색 코어는 `vinc_rag.retrieval` 에서 `open_store` 를 찾는다(VIN-243)."""
    import api.query as _query
    import store.querybase as _base
    import vinc_rag.retrieval as _retrieval
    mods = (_query, _base, _retrieval)
    old = [m.open_store for m in mods]
    for m in mods:
        m.open_store = lambda *a, **k: store
    try:
        yield
    finally:
        for m, o in zip(mods, old):
            m.open_store = o


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

    def test_no_store_carries_the_open_error(self):
        """강등이면 **왜** 도 함께 준다 — `EmbeddedBackend.last_open_error`.

        2026-08-14: 확장 경로 문제로 스토어가 안 열렸는데 /health 는 "no reachable
        store" 한 줄뿐이라 앱이 "지도를 불러오지 못했습니다" 밖에 말할 수 없었다.
        그 화면은 **빈 그래프와 구분되지 않는다.** 사유는 이미 `last_open_error` 에
        담겨 stdout 에도 찍히고 있었는데 **읽는 곳이 없었다**."""
        from store.embedded_backend import EmbeddedBackend
        old = EmbeddedBackend.last_open_error
        EmbeddedBackend.last_open_error = "RuntimeError: IO exception: Failed to load library"
        try:
            with running_server(_app(store=None)) as base:
                code, obj = _req(base, "/health", token=None)
                self.assertEqual(code, 200)
                self.assertEqual(obj["status"], "degraded")
                self.assertIn("Failed to load library", obj["open_error"])
        finally:
            EmbeddedBackend.last_open_error = old

    def test_no_store_without_open_error_stays_quiet(self):
        # 사유가 없으면 키를 만들지 않는다 — 빈 문자열로 "뭔가 있다"고 오해시키지 않는다.
        from store.embedded_backend import EmbeddedBackend
        old = EmbeddedBackend.last_open_error
        EmbeddedBackend.last_open_error = ""
        try:
            with running_server(_app(store=None)) as base:
                self.assertNotIn("open_error", _req(base, "/health", token=None)[1])
        finally:
            EmbeddedBackend.last_open_error = old

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

    def test_vector_index_remnant_is_surfaced(self):
        """벡터 색인이 잔해로 막힌 스토어는 `/health` 에 `vector_index: remnant` 를 싣는다.
        의미 팔이 꺼져 있고 임베딩 적용이 거절되는 상태라 밖에서 보여야 한다. 정상이면
        키가 없다(헬스 계약 불변)."""
        class _FakeStore:
            driver_name = "fake"
            vector_index_remnant = False

            def close(self):
                pass
        store = _FakeStore()
        with running_server(_app(store=store)) as base:
            self.assertNotIn("vector_index", _req(base, "/health", token=None)[1])
            store.vector_index_remnant = True
            code, obj = _req(base, "/health", token=None)
            self.assertEqual(code, 200)
            self.assertEqual(obj.get("vector_index"), "remnant")

    def test_fts_and_node_mirror_remnants_are_surfaced(self):
        """FTS 잔해(`Chunk` — 문서 저장·삭제가 전부 거절된다)와 노드 미러 잔해도 같은 모양으로
        싣는다. 정상이면 키가 없다."""
        class _FakeStore:
            driver_name = "fake"
            fts_index_remnant = False
            node_vector_index_remnant = False

            def close(self):
                pass
        store = _FakeStore()
        with running_server(_app(store=store)) as base:
            obj = _req(base, "/health", token=None)[1]
            self.assertNotIn("fts_index", obj)
            self.assertNotIn("node_vector_index", obj)
            store.fts_index_remnant = store.node_vector_index_remnant = True
            obj = _req(base, "/health", token=None)[1]
            self.assertEqual(obj.get("fts_index"), "remnant")
            self.assertEqual(obj.get("node_vector_index"), "remnant")
            self.assertNotIn("vector_index", obj)

    def test_live_probe_does_not_read_the_store(self):
        """`/health?live=1` 은 워치독용 생존 확인이다. 5초마다 불리므로 스토어를 읽으면
        (리더를 열고 백로그를 세면) 허브가 하루 종일 활성 요금 구간에 머문다. 상태 판정은
        그대로 싣고, 임베딩 진단만 뺀다. 쿼리 없는 `/health` 는 예전과 같다."""
        class _FakeStore:
            driver_name = "fake"

            def close(self):
                pass
        app = _app(store=_FakeStore())
        calls = []
        app.embed_health = lambda: calls.append(1) or {"pending": 0}
        with running_server(app) as base:
            code, obj = _req(base, "/health?live=1", token=None)
            self.assertEqual(code, 200)
            self.assertEqual(obj["status"], "ok")
            self.assertNotIn("embed", obj)
            self.assertEqual(calls, [], "생존 확인이 스토어를 읽었다")
            code, obj = _req(base, "/health", token=None)
            self.assertEqual(obj.get("embed"), {"pending": 0})
            self.assertEqual(calls, [1])

    def test_live_probe_still_reports_a_degraded_store(self):
        # 워치독은 이 판정으로 "성치 않은 사이드카"를 본다 — 생존 확인이라고 ok 로 뭉개면 안 된다.
        with running_server(_app(store=None)) as base:
            code, obj = _req(base, "/health?live=1", token=None)
            self.assertEqual(code, 200)
            self.assertEqual(obj["status"], "degraded")

    def test_embed_apply_pause_switch(self):
        """`VINC_EMBED_APPLY_PAUSED` 가 켜지면 `/embed/apply` 는 503 으로 거절하고
        `/health` 에 드러난다. 꺼지면 둘 다 평소대로다."""
        class _FakeStore:
            driver_name = "fake"

            def close(self):
                pass
        from api import docstore
        calls = []
        real = docstore.apply_embeddings
        docstore.apply_embeddings = lambda *a, **k: calls.append(a) or {"embedded": 0}
        old = os.environ.get("VINC_EMBED_APPLY_PAUSED")
        try:
            with running_server(_app(store=_FakeStore())) as base:
                os.environ["VINC_EMBED_APPLY_PAUSED"] = "on"
                code, obj = _req(base, "/embed/apply", method="POST",
                                 body={"doc_id": "d1", "embeddings": {}})
                self.assertEqual(code, 503)
                self.assertEqual(obj, {"executed": False,
                                       "error": "embedding_apply_paused"})
                self.assertEqual(calls, [])
                self.assertIs(_req(base, "/health", token=None)[1]
                              .get("embed_apply_paused"), True)

                os.environ["VINC_EMBED_APPLY_PAUSED"] = ""
                code, _ = _req(base, "/embed/apply", method="POST",
                               body={"doc_id": "d1", "embeddings": {}})
                self.assertEqual(code, 200)
                self.assertEqual(len(calls), 1)
                self.assertNotIn("embed_apply_paused",
                                 _req(base, "/health", token=None)[1])
        finally:
            docstore.apply_embeddings = real
            if old is None:
                os.environ.pop("VINC_EMBED_APPLY_PAUSED", None)
            else:
                os.environ["VINC_EMBED_APPLY_PAUSED"] = old

    def test_health_reports_the_effective_rag_switches(self):
        """`/health` 가 ColBERT 재채점과 brief 규칙의 실제 값을 늘 싣는다. 운영 값이 저장소 변수에만
        있어서 밖에서 확인할 길이 없던 것을 닫는다."""
        old = {k: os.environ.pop(k, None) for k in ("VINC_BRIEF_RULE", "VINC_NODE_COLBERT")}
        try:
            with running_server(_app(store=None)) as base:
                self.assertEqual(_req(base, "/health", token=None)[1].get("retrieval_switches"),
                                 {"VINC_NODE_COLBERT": "off", "VINC_BRIEF_RULE": "production"})
                os.environ["VINC_BRIEF_RULE"] = "v3"
                self.assertEqual(_req(base, "/health", token=None)[1]["retrieval_switches"]["VINC_BRIEF_RULE"],
                                 "v3")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_health_names_retrieval_escape_hatches_that_are_open(self):
        """엄격 판정·의미 우위 승격·의미 순서·의미 팔 중 꺼진 것이 /health 에 이름으로 보인다.
        다 켜져 있으면 키가 없다."""
        keys = ("VINC_BRIEF_STRICT", "VINC_BRIEF_SEM_PROMOTE", "VINC_NODE_SEM_RANK",
                "VINC_NODE_SEMANTIC_ARM")
        old = {k: os.environ.pop(k, None) for k in keys}
        try:
            with running_server(_app(store=None)) as base:
                self.assertNotIn("retrieval_overrides", _req(base, "/health", token=None)[1])
                os.environ["VINC_BRIEF_STRICT"] = "off"
                os.environ["VINC_BRIEF_SEM_PROMOTE"] = "off"
                os.environ["VINC_NODE_SEM_RANK"] = "0"
                self.assertEqual(_req(base, "/health", token=None)[1].get("retrieval_overrides"),
                                 ["VINC_BRIEF_STRICT", "VINC_BRIEF_SEM_PROMOTE", "VINC_NODE_SEM_RANK"])
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

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


class StoreUnavailableReadTests(unittest.TestCase):
    """**읽을 수 없는 스토어를 빈 스토어로 보고하지 않는다**(2026-08-16).

    실측 재현: 스토어 하나에 사이드카 A 를 띄운 뒤 **같은 스토어**에 사이드카 B 를
    다른 포트로 띄우면(임베디드 DB 는 단일 writer 라 락 충돌) B 는 기동에 성공하고
    `/health` 는 정직하게 degraded 를 낸다. 그런데 데이터 라우트는 그러지 않았다 —
    `GET /graph` 가 **2xx + nodes:0** 이었다. 클라이언트에게 "가진 것이 없다"와
    "읽을 수 없다"가 같은 답으로 도착했고, 앱에서는 그것이 빈 지도로 그려졌다.
    데이터 유실과 구분되지 않는 화면이다.

    전수 실측(GET 30개, store 도달 불가)에서 세 가지 거짓말이 나왔다:
      · 다수가 200 + `executed:false`  — HTTP 만 보면 성공
      · 7개가 500 `AttributeError: 'NoneType' object has no attribute 'run'`
      · `/sync/pull-delta` 는 **`executed:true`** + "nothing leaves the device"

    셋 다 `VincApp._read` 한 곳에서 났고, 거기 하나를 막아 셋 다 없앤다."""

    #: 그래프를 읽는 GET 전량(`_route_get` 표 기준). 그래프를 안 읽는 넷은 뺐다 —
    #: `/themes`·`/types`(어휘 JSON) · `/connect/status`(인메모리) · `/plan/*`(순수
    #: 플랜 빌더) · `/jobs/*`(잡 상태). 그것들은 스토어가 죽어도 답하는 게 맞다.
    READ_ROUTES = [
        "/recall?query=x&owner=u", "/domains?owner=u", "/view?owner=u",
        "/ledger?owner=u", "/share?owner=u", "/coupling?owner=u&a=a&b=b",
        "/promote-candidates?owner=u", "/suggest-ties?owner=u&node=concept:a",
        "/diagnose?owner=u", "/merge-candidates?owner=u", "/merge-rejections?owner=u",
        "/neighbors?owner=u&node=concept:a",
        "/path?owner=u&a=concept:a&b=concept:b", "/learner?owner=u",
        "/graph?owner=u&layout=none", "/search?query=x", "/decide?topic=t",
        "/doc?id=d1", "/docs?owner=u", "/conventions?owner=u", "/nav-tree?owner=u",
        "/doc/highlights?doc_id=d1&concept_id=concept:a",
        "/doc/context?owner=u&doc_id=d1",
        "/node-context?owner=u&node=concept:a", "/brief?owner=u&query=x",
        "/edge-context?owner=u&src=concept:a&dst=concept:b&type=RELATED_TO",
        "/ledger-drilldown?owner=u&domain=d&mark=m",
        "/docs-in-range?owner=u&start=2020-01-01&end=2030-01-01",
        "/sync/domains?owner=u", "/sync/pull-delta?owner=u",
        "/sync/docs-export?owner=u", "/embed/pending", "/owner-stats",
    ]

    def test_read_routes_refuse_instead_of_answering_empty(self):
        # `api.query.open_store` 도 막는다 — 안 그러면 조회가 **자기 스토어를 다시
        # 열어** 우회한다(그게 원래 이 버그가 200 으로 보이던 이유다). 락 충돌
        # 상황에서는 그 재열기도 실패하므로, 이 대역이 실측 상황과 같은 조건이다.
        with cli_store(None), running_server(_app(store=None)) as base:
            for path in self.READ_ROUTES:
                code, obj = _req(base, path)
                self.assertEqual(code, 503, f"{path} 가 읽을 수 없는 스토어에 {code} 로 답했다")
                self.assertFalse(obj.get("executed", False), path)
                self.assertEqual(obj.get("error"), "no reachable store", path)
                # 빈 성공 페이로드를 함께 싣지 않는다 — 상태만 오류이고 바디에
                # `nodes:[]`·`rows:[]` 가 있으면 그 필드를 읽는 쪽은 그대로 속는다.
                for empty_field in ("nodes", "rows", "chunks", "docs", "items"):
                    self.assertNotIn(empty_field, obj, f"{path} 가 {empty_field} 를 실었다")

    def test_refusal_carries_the_open_error(self):
        """거절만 하고 이유를 안 주면 사람은 다시 `/health` 로 물으러 가야 한다."""
        from store.embedded_backend import EmbeddedBackend
        old = EmbeddedBackend.last_open_error
        EmbeddedBackend.last_open_error = (
            "RuntimeError: IO exception: Could not set lock on file (Error: 33)")
        try:
            with cli_store(None), running_server(_app(store=None)) as base:
                code, obj = _req(base, "/graph?owner=u&layout=none")
                self.assertEqual(code, 503)
                self.assertIn("Could not set lock", obj["open_error"])
        finally:
            EmbeddedBackend.last_open_error = old

    def test_health_still_answers_200(self):
        """조회가 503 이어도 **`/health` 는 200 이다.**

        릴리스 게이트(release.yml "Smoke test sidecar")가 `/health` 의 status 를
        본다. 여기까지 503 으로 만들면 살아 있는 사이드카가 죽은 것으로 보이고,
        기동 프로브를 도는 셸·오케스트레이터가 멀쩡한 프로세스를 재기동시킨다.
        진단은 답하고 데이터는 거절한다 — 그 조합이 계약이다."""
        with cli_store(None), running_server(_app(store=None)) as base:
            code, obj = _req(base, "/health", token=None)
            self.assertEqual(code, 200)
            self.assertEqual(obj["status"], "degraded")

    #: 그래프를 쓰는 POST 전량(`_route_post` 표 기준). 그래프를 안 쓰는 여섯은 뺐다 —
    #: `/themes`·`/themes/activate`·`/types/*`(어휘 JSON) · `/connect/start`(인메모리
    #: 메일박스). READ_ROUTES 와 같은 갈래다.
    WRITE_ROUTES = [
        ("/pin", {"node": "concept:a"}), ("/tag", {"node": "concept:a", "tags": ["x"]}),
        ("/touch", {"node": "concept:a"}), ("/activate", {"node": "concept:a"}),
        ("/backup", {"out": "x.vincpack"}), ("/ingest", {"nodes": []}),
        ("/delete", {"id": "concept:a"}),
        ("/delete-edge", {"src": "concept:a", "dst": "concept:b", "type": "RELATED_TO"}),
        ("/merge-nodes", {"a": "concept:a", "b": "concept:b"}),
        ("/merge-reject", {"a": "concept:a", "b": "concept:b"}),
        ("/doc/put", {"text": "x"}), ("/doc/delete", {"doc_id": "d1"}),
        ("/conventions/build", {}), ("/doc/put-file", {"name": "a.md", "bytes_b64": ""}),
        ("/doc/extract", {"doc_id": "d1"}), ("/doc/reembed", {"doc_id": "d1"}),
        ("/evidence/backfill", {}), ("/embed/apply", {"items": []}),
        ("/domain/rename", {"src": "a", "dst": "b"}),
        ("/sync/domains", {"domains": []}), ("/sync/apply-delta", {"delta": {}}),
        ("/sync/docs-apply", {"docs": []}), ("/sync/purge-owner", {"owner": "user:u"}),
        ("/sync", {"token": "vinc_sk_x"}),
        ("/ingest/recall", {"items": []}),
    ]

    def test_write_routes_refuse_instead_of_raising_attributeerror(self):
        """쓰기가 `None.run` 으로 터지는 대신 **사유를 들고** 거절한다.

        읽기는 `_read` 로 이미 그랬는데 쓰기는 아니었다. 2026-08-30 데스크탑에서
        WAL 이 깨져 스토어가 안 열린 채 sync 를 누르니 화면에 뜬 것이 이것이다:
        `Sync failed: AttributeError: 'NoneType' object has no attribute 'run'`.
        같은 순간 `/health` 는 "Checksum verification failed" 를 알고 있었다."""
        with cli_store(None), running_server(_app(store=None)) as base:
            for path, body in self.WRITE_ROUTES:
                code, obj = _req(base, path, method="POST", body=body)
                self.assertEqual(code, 503,
                                 f"{path} 가 쓸 수 없는 스토어에 {code} 로 답했다")
                self.assertFalse(obj.get("executed", False), path)
                self.assertEqual(obj.get("error"), "no reachable store", path)
                # AttributeError 가 새어 나오지 않는다 — 사유가 없는 500 이 이 버그였다.
                self.assertNotIn("NoneType", json.dumps(obj), path)

    def test_write_refusal_carries_the_open_error(self):
        """거절이 **처방으로 이어져야** 한다. WAL 손상과 락 충돌은 처방이 다르다."""
        from store.embedded_backend import EmbeddedBackend
        old = EmbeddedBackend.last_open_error
        EmbeddedBackend.last_open_error = (
            "RuntimeError: Storage exception: Checksum verification failed, "
            "the WAL file is corrupted.")
        try:
            with cli_store(None), running_server(_app(store=None)) as base:
                code, obj = _req(base, "/sync", method="POST",
                                 body={"token": "vinc_sk_x"})
                self.assertEqual(code, 503)
                self.assertIn("WAL file is corrupted", obj["open_error"])
        finally:
            EmbeddedBackend.last_open_error = old

    def test_store_free_writes_still_answer(self):
        """어휘와 계정 연결은 그래프를 안 쓴다 — 그래프가 죽어도 막으면 회귀다.

        특히 `/connect/start`: 스토어가 깨진 바로 그때가 사용자가 계정을 다시
        연결해 보려는 때다. 거기서 503 을 주면 복구 경로 하나를 스스로 닫는다."""
        with cli_store(None), running_server(_app(store=None)) as base:
            for path, body in (("/themes", {"theme_id": "t1", "title": "T"}),
                               ("/connect/start", {})):
                code, _ = _req(base, path, method="POST", body=body)
                self.assertNotEqual(code, 503,
                                    f"{path} 는 스토어 없이도 답해야 한다")

    def test_unknown_post_route_is_still_404_not_503(self):
        """가드가 라우팅을 가리면 오타가 "스토어 문제"로 보인다."""
        with cli_store(None), running_server(_app(store=None)) as base:
            code, _ = _req(base, "/nope", method="POST", body={})
            self.assertEqual(code, 404)

    def test_writes_pass_through_when_the_store_is_open(self):
        # 거절이 상시로 켜져 있으면 계약이 아무것도 못 막는다(READ 쪽과 같은 대조).
        with running_server(_app(store=_EmptyStore())) as base:
            code, _ = _req(base, "/sync/domains", method="POST", body={"domains": []})
            self.assertNotEqual(code, 503)

    def test_open_store_is_still_the_signal_for_a_live_store(self):
        # 스토어가 열려 있으면 같은 라우트가 정상 200 이다 — 503 이 상시로 켜져
        # 있으면 이 계약은 아무것도 못 막는다(거절이 기본값이 되면 신호가 아니다).
        with running_server(_app(store=_EmptyStore())) as base:
            code, obj = _req(base, "/graph?owner=u&layout=none")
            self.assertEqual(code, 200)
            self.assertTrue(obj["executed"])


class EquivalenceTests(unittest.TestCase):
    """레거시 HTTP 계약과 도구 쌍이 없는 CLI 동사의 응답을 대조한다."""

    GET_CASES = [
        ("/learner?owner=u", ["learner", "--owner", "u"]),
        ("/graph?owner=u", ["graph", "--owner", "u"]),
        ("/plan/recall?query=x", ["plan", "recall", "--query", "x"]),
        ("/plan/decide?topic=t", ["plan", "decide", "--topic", "t"]),
    ]

    POST_CASES = [
        ("/pin", {"episode": "e1"}, ["pin", "--episode", "e1"]),
        ("/tag", {"episode": "e1", "tags": ["a", "b"]}, ["tag", "--episode", "e1", "--tags", "a,b"]),
        ("/touch", {"episode": "e1"}, ["touch", "--episode", "e1"]),
        ("/activate", {"owner": "u"}, ["activate", "--owner", "u"]),
    ]

    # ⚠ 양쪽 다 **스토어가 열린** 조건에서 대조한다(`_EmptyStore` 독스트링).
    # 전에는 둘 다 무스토어 강등으로 맞춰 봤는데, 그 조건에서 HTTP 가 CLI 와 같은
    # 답을 낸 것은 조회 경로가 스토어를 몰래 다시 열었기 때문이었다. 그 우회로를
    # 막은 지금은 열린 스토어에서 대조하는 것이 원래 재려던 것에 더 가깝다 —
    # 강등 봉투가 아니라 **실행된 응답**이 같은지를 본다.
    def test_get_endpoints_match_cli(self):
        with running_server(_app(store=_EmptyStore())) as base, cli_store(_EmptyStore()):
            for path, argv in self.GET_CASES:
                code, http_obj = _req(base, path)
                self.assertEqual(code, 200, path)
                self.assertEqual(http_obj, _cli_json(argv), f"불일치: {path}")

    def test_post_endpoints_match_cli(self):
        with running_server(_app(store=_EmptyStore())) as base, cli_store(_EmptyStore()):
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
        # 스토어를 실제로 준다 — 재려는 것은 "조회가 쓰기 큐에 막히지 않는가"이고,
        # 무스토어면 큐에 닿기도 전에 503 이라 그 질문이 검사되지 않는다.
        app = _app(store=_EmptyStore())
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
        # 잡 배선만 본다 — 엔진은 스텁으로 갈아 끼운다. 실엔진(recall)은 에피소드
        # 필수 필드가 열 개가 넘어서, 여기 채워 넣으면 이 테스트가 그 스키마에
        # 묶여 관계없는 변경에 깨진다. `VincApp` 이 `ingest` 주입을 받는 이유가 이것.
        app = VincApp(store=_EmptyStore(), auth=SessionAuth(_TOKEN), ingest={
            "recall": lambda store, body: {"engine": "recall", "executed": True,
                                           "ingested": len(body.get("episodes") or [])}})
        with running_server(app) as base:
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
            # 잡 배선을 보는 테스트다: 202 로 접수되고 폴링으로 끝을 본다.
            # (무스토어 강등은 여기서 안 본다 — 이제 라우팅에서 503 이고,
            #  그건 WriteRefusal 쪽이 전수로 본다.)
            self.assertEqual(jobj["result"]["ingested"], 1)

    def test_unknown_ingest_engine_400(self):
        with running_server(_app(store=_EmptyStore())) as base:
            code, _ = _req(base, "/ingest/bogus", method="POST", body={})
            self.assertEqual(code, 400)

    def test_backup_missing_out_400(self):
        # 열린 스토어를 준다 — 이 테스트가 보는 것은 바디 검증이지 스토어가 아니다.
        # (무스토어면 이제 라우팅 전에 503 이라 400 을 볼 수 없다.)
        with running_server(_app(store=_EmptyStore())) as base:
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
def _encode_stub(vector, *, seen: dict | None = None, delay: float = 0.0,
                 runtime: str | None = None, build: str | None = None,
                 model: str | None = None):
    """워커 인코딩 창구(`vinc_rag/embed/worker.py` `serve_encode`)와 **같은 계약**을
    말하는 최소 대역. 모델 없이 배선만 검증하려고 둔다(실모델은 440MB 다운로드가 필요)."""
    import json as _json
    import threading as _th
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from api import server as _srv
    saved_runtime = _srv._ENCODER_RUNTIME
    saved_build = _srv._ENCODER_BUILD
    if not saved_runtime:
        _srv._ENCODER_RUNTIME = "test-runtime"
    if not saved_build:
        _srv._ENCODER_BUILD = "test-build"
    runtime = _srv._ENCODER_RUNTIME if runtime is None else runtime
    build = _srv._ENCODER_BUILD if build is None else build

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
            reply = {"vectors": [list(vector)], "encoder_runtime": runtime,
                     "encoder_build": build}
            if model is not None:
                reply["model"] = model
            out = _json.dumps(reply).encode()
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
        _srv._ENCODER_RUNTIME = saved_runtime
        _srv._ENCODER_BUILD = saved_build


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
        from api import server as srv
        self.srv = srv
        self._consts = {k: getattr(srv, k) for k in
                        ("_QVEC_TIMEOUT", "_QVEC_COLD_TIMEOUT")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k, v in self._consts.items():
            setattr(self.srv, k, v)

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

    def test_remote_runtime_mismatch_does_not_label_an_old_vector_as_new(self):
        with _encode_stub([0.5, 0.5, 0.0, 0.0], runtime="old-encoder") as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            res = self._search()
        self.assertEqual(res.get("arms"), "fts")
        self.assertIn("remote encoder runtime differs", res.get("vector_note") or "")
        self.assertEqual(self.seen_vec, [])

    def _search_with_stored_model(self, stored, **stub):
        """저장 모델을 ``stored`` 로 두고 창구 대역에 대고 검색한다."""
        from unittest import mock
        state = {"stored": stored, "migrating": False, "pending": 0}
        with mock.patch.object(self.srv.docstore, "embed_model_state",
                               return_value=state), \
                _encode_stub([0.5, 0.5, 0.0, 0.0], **stub) as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            return self._search()

    def test_창구의_모델이_저장_모델과_다르면_벡터_팔을_뺀다(self):
        """RV-06 모델 절반. 폭이 같은 다른 모델은 캐스팅 오류 없이 엉뚱한 이웃을 낸다."""
        res = self._search_with_stored_model("BAAI/bge-m3", model="intfloat/multilingual-e5-base")
        self.assertEqual(res.get("arms"), "fts")
        self.assertIn("differs from the stored vectors' model", res.get("vector_note") or "")
        self.assertEqual(self.seen_vec, [], "다른 모델의 질의 벡터로 벡터 팔이 돌았다")

    def test_모델이_같거나_한쪽을_모르면_막지_않는다(self):
        for stored, model in (("BAAI/bge-m3", "BAAI/bge-m3"),   # 같다
                              ("BAAI/bge-m3", None),            # 옛 창구는 모델을 안 싣는다
                              ("", "BAAI/bge-m3")):             # 저장 모델 기록이 없다
            self.seen_vec.clear()
            res = self._search_with_stored_model(stored, model=model)
            self.assertEqual(res.get("arms"), "fts+vector", (stored, model, res))

    def test_remote_build_mismatch_with_same_runtime_refuses_old_model(self):
        with _encode_stub([0.5, 0.5, 0.0, 0.0], build="old-build") as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            res = self._search()
        self.assertEqual(res.get("arms"), "fts")
        self.assertIn("remote encoder build differs", res.get("vector_note") or "")
        self.assertEqual(self.seen_vec, [])

    def test_local_encoder_without_deployment_build_still_serves_vectors(self):
        from api import server as srv
        saved_build = srv._ENCODER_BUILD
        saved_required = srv._ENCODER_BUILD_REQUIRED
        saved_runtime = srv._ENCODER_RUNTIME
        remote_runtime = saved_runtime or "test-runtime"
        try:
            with _encode_stub([0.5, 0.5, 0.0, 0.0], build="") as url:
                srv._ENCODER_BUILD = ""
                srv._ENCODER_BUILD_REQUIRED = False
                srv._ENCODER_RUNTIME = ""
                os.environ["VINC_EMBED_ENCODE"] = url
                res = self._search()
            self.assertEqual(res.get("arms"), "fts+vector")
            self.assertEqual(srv._ENCODER_RESPONSE_RUNTIME.get(), remote_runtime)
        finally:
            srv._ENCODER_BUILD = saved_build
            srv._ENCODER_BUILD_REQUIRED = saved_required
            srv._ENCODER_RUNTIME = saved_runtime

    def test_창구가_죽어도_검색은_답하고_사유를_남긴다(self):
        os.environ["VINC_EMBED_ENCODE"] = "http://127.0.0.1:9"   # discard 포트
        # ⚠ 상한은 **모듈 속성**으로 줄인다. `VINC_EMBED_ENCODE_TIMEOUT` 은 import
        # 시점에 한 번 읽히므로 여기서 env 를 놔 봐야 안 먹는다(오래 그렇게 적혀
        # 있었다). 콜드 상한도 함께 줄인다 — 닫힌 포트는 이 기계에서 연결 거부일
        # 때도 연결 시간 초과일 때도 있고, 후자면 콜드 재시도가 붙어 30초가 된다.
        self.srv._QVEC_TIMEOUT = 0.3
        self.srv._QVEC_COLD_TIMEOUT = 0.3
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

    def _probe_while_computing(self, state, probe):
        """이행 판정 캐시를 **계산하는 도중**(저장 상태를 읽는 순간)에 다른 스레드가
        ``probe(app)`` 를 부르고 그 결과를 돌려준다. 끼어드는 자리를 훅으로 고정해
        경합을 결정론으로 만든다. 근거: [[vinc.audit.rag-code-review-2026-09-29]] 33번"""
        from unittest import mock
        app = _app(store=self._Store(self))
        out = []

        def hook(_reader, **_kw):
            if not out:                      # 첫 계산에서만 끼어든다
                out.append(None)
                t = threading.Thread(target=lambda: out.append(probe(app)))
                t.start()
                t.join(5)
            return state

        try:
            with mock.patch.object(self.srv.docstore, "embed_model_state", side_effect=hook):
                app._embed_migrating()
        finally:
            app.jobs.shutdown(drain=True)
        self.assertEqual(len(out), 2, "끼어든 스레드가 끝나지 않았다")
        return out[1]

    def test_계산_중에_들어온_검색도_이행_중임을_본다(self):
        """RV-33. 시각을 먼저 게시하면 계산 창 안의 검색이 빈 사유(이행 아님)를 읽는다."""
        note = self._probe_while_computing(
            {"stored": "model-A", "migrating": True, "pending": 7},
            lambda app: app._embed_migrating())
        self.assertIn("migration", note, "계산이 끝나기 전에 '이행 아님'이 게시됐다")

    def test_계산_중에_들어온_검색도_저장_모델과_대조한다(self):
        """RV-33 이 RV-06 모델 절반을 끄던 길. 저장 모델이 빈 값으로 먼저 게시되면
        그 창 안의 질의는 다른 모델의 벡터로 벡터 팔을 돌린다."""
        with _encode_stub([0.5, 0.5, 0.0, 0.0], model="intfloat/multilingual-e5-base") as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            vec, note = self._probe_while_computing(
                {"stored": "BAAI/bge-m3", "migrating": False, "pending": 0},
                lambda app: app._query_embedding("양자역학"))
        self.assertIsNone(vec, "계산 창 안에서 모델 대조가 건너뛰어졌다")
        self.assertIn("differs from the stored vectors' model", note)

    def test_일상_편집의_pending_은_벡터_팔을_내리지_않는다(self):
        """편집 1건의 needs_embed 는 이행이 아니다. 이행 표식(_EMBED_MIGRATING_KEY)
        없는 pending 에서 팔을 내리면 문서 하나 고칠 때마다 그래프 전체의 의미
        검색이 꺼진다(실측 2026-09-02: "1 docs pending" 창마다 전역 FTS-only 강등).
        발주자 결정("재임베딩 기간 FTS만")은 전량 재큐(모델 이행)를 말한 것이고,
        그 경우는 표식이 지킨다 — 위 테스트가 그 계약이다."""
        def _meta_rows(cypher, params):
            if "_SchemaMeta" in cypher:
                # embed_model 만 있고 이행 표식(embed_migration_model)은 없다.
                k = (params or {}).get("k")
                return [{"v": "model-A"}] if k == "embed_model" else []
            if "needs_embed" in cypher:
                return [{"c": 1}]                 # 편집 1건 대기
            return []

        class _EditReader(QueryEmbeddingWiringTests._Reader):
            def run(self, cypher, params=None):
                r = _meta_rows(cypher, params)
                return r if r else super().run(cypher, params)

        class _EditStore(QueryEmbeddingWiringTests._Store):
            def reader_view(self):
                return _EditReader(self.o)

            def run(self, cypher, params=None):
                return _meta_rows(cypher, params)

        with _encode_stub([1.0, 0, 0, 0]) as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            app = _app(store=_EditStore(self))
            try:
                res = app.read_search({"query": "양자역학", "owner": "u"})[1]
            finally:
                app.jobs.shutdown(drain=True)
        self.assertEqual(res.get("arms"), "fts+vector",
                         "편집 1건의 pending 이 벡터 팔을 내렸다 — 과잉 게이트")
        self.assertFalse(res.get("vector_note"), res.get("vector_note"))
        self.assertEqual(self.seen_vec, [[1.0, 0, 0, 0]])

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


class BriefArmsTests(unittest.TestCase):
    """`/brief` 도 `/search` 와 **같은 규칙으로** 어느 팔이 돌았는지 말하는가.

    두 표면은 같은 임베딩 창구를 쓴다. `/search` 만 `arms` 를 내던 동안, 같은 강등이
    도구에 따라 보이거나 안 보였다 — 벡터 팔이 죽어도 `/brief` 는 완전해 보이는
    답을 냈고 사용자도 에이전트도 몰랐다(08-26 이래 여섯 번 기록된 결함).

    ⚠ `arms` 는 `q.brief` 가 세운다(HTTP 계층이 아니라). 여기서 붙이면 CLI 와 갈려
    게이트① 동등성이 깨진다 — `EquivalenceTests` 가 `/brief` 를 대조한다."""

    def setUp(self):
        self.seen_vec = []
        self._saved = os.environ.get("VINC_EMBED_ENCODE")
        self._saved_to = os.environ.get("VINC_EMBED_ENCODE_TIMEOUT")

    def tearDown(self):
        for k, v in (("VINC_EMBED_ENCODE", self._saved),
                     ("VINC_EMBED_ENCODE_TIMEOUT", self._saved_to)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _brief(self, store=None):
        app = _app(store=store or QueryEmbeddingWiringTests._Store(self))
        try:
            return app.read_brief({"query": "양자역학", "owner": "u"})[1]
        finally:
            app.jobs.shutdown(drain=True)

    def test_창구가_있으면_brief_도_두_팔을_말한다(self):
        with _encode_stub([0.5, 0.5, 0.0, 0.0]) as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            res = self._brief()
        self.assertEqual(res.get("arms"), "fts+vector",
                         "brief 가 어느 팔이 돌았는지 말하지 않는다")

    def test_창구가_죽으면_brief_도_강등을_말한다(self):
        os.environ["VINC_EMBED_ENCODE"] = "http://127.0.0.1:9"   # discard 포트
        os.environ["VINC_EMBED_ENCODE_TIMEOUT"] = "0.3"
        res = self._brief()
        self.assertEqual(res.get("arms"), "fts", "벡터 팔이 죽었는데 brief 는 침묵했다")
        self.assertTrue(res.get("vector_note"), "조용히 강등됐다 — 사유가 없다")

    def test_창구가_없으면_빈_필드를_붙이지_않는다(self):
        """데스크탑의 영구 정상 상태. `arms` 는 실제로 돈 팔이므로 "fts" 가 맞고,
        시도조차 안 했으므로 사유(vector_note)는 없어야 한다."""
        os.environ.pop("VINC_EMBED_ENCODE", None)
        res = self._brief()
        self.assertEqual(res.get("arms"), "fts")
        self.assertFalse(res.get("vector_note"), "시도하지 않았는데 사유가 남았다")

    def test_재빌드_창은_구조화_표식으로_갈린다(self):
        """쓰기 뒤 HNSW 재빌드는 **양성 계약**이다(잠시 뒤 저절로 돌아온다).
        화면이 이것을 진짜 실패와 같은 톤으로 그리면 거짓 경보가 되는데, 두 경우
        모두 `arms=="fts"` + `vector_note` 라 영문 문장을 파싱하지 않고는 못 가른다.
        그래서 표식을 따로 싣는다 — 화면은 문구가 아니라 이 필드를 본다."""
        class _RebuildReader(QueryEmbeddingWiringTests._Reader):
            def vector_search(self, e, k, f=None):
                raise RuntimeError("Table Chunk doesn't have an index with name chunk_vec")

        class _RebuildStore(QueryEmbeddingWiringTests._Store):
            def reader_view(self): return _RebuildReader(self.o)

        with _encode_stub([1.0, 0.0, 0.0, 0.0]) as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            app = _app(store=_RebuildStore(self))
            try:
                got = {k: app.read_search({"query": "양자역학", "owner": "u"})[1]
                       for k in ("search",)}
                got["brief"] = app.read_brief({"query": "양자역학", "owner": "u"})[1]
            finally:
                app.jobs.shutdown(drain=True)
        for name, res in got.items():
            self.assertEqual(res.get("arms"), "fts", name)
            self.assertTrue(res.get("vector_note"), name)
            self.assertIs(res.get("vector_rebuilding"), True,
                          f"{name}: 재빌드 창을 진짜 실패와 가를 표식이 없다")

    def test_잔해로_막힌_색인은_재빌드_창이_아니라_실패로_말하고_빌드를_걸지_않는다(self):
        """색인이 없는 이유가 쓰기 뒤 재빌드가 아니라 **잔해**라면 다시 서지 않는다.
        양성 표식을 달면 "곧 돌아온다"는 거짓이 되고, 빌드를 걸면 검색마다 실패할 잡과
        스냅샷이 돈다(허브 2026-09-25: 812MB 복사를 85초마다, CPU 한도)."""
        scheduled = []

        class _RemnantReader(QueryEmbeddingWiringTests._Reader):
            vector_index_remnant = True

            def vector_search(self, e, k, f=None):
                raise RuntimeError("Table ChunkVec doesn't have an index with name chunk_vec")

            def schedule_search_indexes(self, *, dim=None):
                scheduled.append(dim)

        class _RemnantStore(QueryEmbeddingWiringTests._Store):
            def reader_view(self): return _RemnantReader(self.o)

        with _encode_stub([1.0, 0.0, 0.0, 0.0]) as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            app = _app(store=_RemnantStore(self))
            try:
                res = app.read_search({"query": "양자역학", "owner": "u"})[1]
            finally:
                app.jobs.shutdown(drain=True)
        self.assertEqual(res.get("arms"), "fts")
        self.assertIn("remnant", res.get("vector_note") or "")
        self.assertIsNot(res.get("vector_rebuilding"), True,
                         "다시 서지 않는 색인을 양성 재빌드로 그린다")
        self.assertEqual(scheduled, [], "실패할 빌드를 검색이 또 걸었다")

    def test_FTS_잔해면_청크_팔이_섰다고_말하고_빌드를_걸지_않는다(self):
        """FTS 색인이 잔해면 청크 검색이 통째로 선다(FTS 로 시작한다). 빌드를 걸면 검색마다
        실패할 잡이 돈다 — 벡터 잔해 때 허브가 겪은 그 폭주다. 사유는 구조화해서 싣는다."""
        built = []

        class _FtsRemnantReader(QueryEmbeddingWiringTests._Reader):
            fts_index_remnant = True

            def run(self, cypher, params=None):
                return [{"c": 3}] if "count(" in cypher else []

            def fulltext_search(self, t, k, f=None):
                raise RuntimeError("Binder exception: Table Chunk doesn't have an index "
                                   "with name chunk_ft.")

            def ensure_search_indexes(self, *, dim=None):
                built.append(dim)
                return {}

        class _FtsRemnantStore(QueryEmbeddingWiringTests._Store):
            def reader_view(self): return _FtsRemnantReader(self.o)

        os.environ.pop("VINC_EMBED_ENCODE", None)
        app = _app(store=_FtsRemnantStore(self))
        try:
            res = app.read_search({"query": "양자역학", "owner": "u"})[1]
        finally:
            app.jobs.shutdown(drain=True)
        self.assertTrue(str(res.get("error") or "").startswith("fts_index_remnant"), res)
        self.assertEqual(built, [], "다시 서지 않는 FTS 색인을 검색이 또 빌드하려 했다")

    def test_청크_팔이_통째로_죽으면_돌지_않은_팔의_이름을_안_단다(self):
        """`arms` 를 검색 **전에** 세우던 자리의 거짓말(2026-09-07 적대적 검토가 재현).

        벡터 팔이 `index_missing` 이 **아닌** 예외로 죽으면 `_chunk_arms` 가 재던지고
        청크 팔이 통째로 빠졌다(멀쩡한 키워드 결과까지, RV-06 이 그 재던지기를 걷어
        지금은 키워드 결과가 남는다). 그런데 응답은 계속
        "fts+vector" 라고 답했고, 화면 판정이 보는 구조화 필드는 셋 다 안 서서
        **알림도 침묵했다** — 답할 수 없는데 답한 것처럼 보이는, 이 결함의 가장 나쁜
        얼굴이다. 이제 `arms` 는 **실제로 결과를 낸 팔**에서만 나온다."""
        class _DeadReader(QueryEmbeddingWiringTests._Reader):
            def vector_search(self, e, k, f=None):
                raise RuntimeError("Binder exception: extension VECTOR 확장 미가용")

        class _DeadStore(QueryEmbeddingWiringTests._Store):
            def reader_view(self): return _DeadReader(self.o)

        with _encode_stub([1.0, 0.0, 0.0, 0.0]) as url:
            os.environ["VINC_EMBED_ENCODE"] = url
            app = _app(store=_DeadStore(self))
            try:
                got = {"search": app.read_search({"query": "양자역학", "owner": "u"})[1],
                       "brief": app.read_brief({"query": "양자역학", "owner": "u"})[1]}
            finally:
                app.jobs.shutdown(drain=True)
        for name, res in got.items():
            self.assertNotIn("vector", res.get("arms") or "",
                             f"{name}: 벡터 팔이 안 돌았는데 응답이 그 이름을 달았다")
            self.assertTrue(res.get("vector_note"),
                            f"{name}: 팔이 통째로 죽었는데 화면이 읽을 신호가 없다")
            self.assertIsNot(res.get("vector_rebuilding"), True,
                             f"{name}: 진짜 실패를 양성 재빌드와 같은 톤으로 그린다")


class QueryEmbeddingColdStartTests(unittest.TestCase):
    """상한 하나가 정반대 두 고장을 맡던 것을 가른다.

    **바쁜** 인코더는 빨리 포기하는 게 맞다 — 어차피 FTS 로 답할 것을 늦게 답하게 된다.
    **잠든** 인코더(레플리카 0에서 깨는 중)는 한 번 기다려 깨우는 게 맞다 — 깨고 나면
    그 뒤 질의가 전부 싸다. 가르는 신호는 마지막 성공 시각이고, 죽은 인코더가 모든
    질의에 긴 값을 물리지 않도록 쿨다운이 받친다."""

    def setUp(self):
        from api import server as srv
        self.srv = srv
        self._consts = {k: getattr(srv, k) for k in
                        ("_QVEC_TIMEOUT", "_QVEC_COLD_TIMEOUT",
                         "_QVEC_WARM_WINDOW", "_QVEC_COLD_COOLDOWN")}
        # 상수는 import 시점에 읽히므로 env 가 아니라 모듈 속성을 바꾼다.
        srv._QVEC_TIMEOUT = 0.2
        srv._QVEC_COLD_TIMEOUT = 1.5
        srv._QVEC_WARM_WINDOW = 300.0
        srv._QVEC_COLD_COOLDOWN = 60.0
        self._saved_url = os.environ.get("VINC_EMBED_ENCODE")

    def tearDown(self):
        for k, v in self._consts.items():
            setattr(self.srv, k, v)
        if self._saved_url is None:
            os.environ.pop("VINC_EMBED_ENCODE", None)
        else:
            os.environ["VINC_EMBED_ENCODE"] = self._saved_url

    @contextlib.contextmanager
    def _app_at(self, url):
        os.environ["VINC_EMBED_ENCODE"] = url
        app = _app()
        try:
            yield app
        finally:
            app.jobs.shutdown(drain=True)

    def test_한_번도_성공_못_했으면_긴_상한으로_한_번_더_간다(self):
        """콜드스타트다. 짧은 상한만 있으면 여기서 영원히 강등되고, 인코더는 깨지도
        않으므로 다음 질의도 같은 값을 문다."""
        with _encode_stub([1.0, 0.0], delay=0.6) as url, self._app_at(url) as app:
            vec, note = app._query_embedding("양자역학")
        self.assertEqual(vec, [1.0, 0.0], f"콜드스타트를 못 넘겼다: {note}")

    def test_방금_성공했으면_시간_초과를_바쁨으로_보고_안_기다린다(self):
        """웜 창 안에 성공한 적이 있으면 인코더는 살아 있다. 지금 초과는 '바쁨'이니
        기다려 봐야 사용자만 세운다."""
        with _encode_stub([1.0, 0.0], delay=3.0) as url, self._app_at(url) as app:
            app._qvec_ok = time.monotonic()
            t0 = time.monotonic()
            vec, note = app._query_embedding("양자역학")
            elapsed = time.monotonic() - t0
        self.assertIsNone(vec)
        self.assertTrue(note, "조용히 강등됐다 — 사유가 없다")
        self.assertLess(elapsed, self.srv._QVEC_COLD_TIMEOUT,
                        "웜인데 긴 상한까지 기다렸다 — 바쁜 인코더에 사용자를 세웠다")

    def test_인코더가_죽어_있으면_긴_상한을_무는_질의는_하나뿐이다(self):
        """이 패키지의 급소. 콜드 재시도를 무조건 하면 죽은 인코더가 **모든** 질의를
        긴 상한만큼 세운다 — 짧은 상한이 원래 막던 사고를 되살리는 것이다."""
        # ⚠ 구간은 **단조시계로만** 잰다(2026-09-15). `time.time()` 으로 재던 것이
        # CI 에서 1.27s 로 읽혀 빨개졌는데, 소켓 상한은 단조시계로 세므로 부하로는
        # 짧아질 수 없다 — 짧게 읽힌 것은 벽시계가 **뒤로 물러선** 것이다(WSL 이
        # 호스트와 시각을 다시 맞출 때 그렇게 된다). 여기 아래 단언은 "긴 상한을
        # 실제로 물었나"라는 뜻이어야지, 시계가 정직했나에 걸리면 안 된다.
        with _encode_stub([1.0, 0.0], delay=3.0) as url, self._app_at(url) as app:
            t0 = time.monotonic()
            self.assertIsNone(app._query_embedding("q1")[0])
            first = time.monotonic() - t0
            t1 = time.monotonic()
            self.assertIsNone(app._query_embedding("q2")[0])
            second = time.monotonic() - t1
            self.assertGreater(app._qvec_cold_until, time.monotonic(),
                               "긴 시도가 실패했는데 쿨다운이 안 걸렸다")
        self.assertGreaterEqual(first, self.srv._QVEC_COLD_TIMEOUT,
                                "첫 질의가 긴 상한을 물지 않았다 — 시나리오가 안 섰다")
        self.assertLess(second, self.srv._QVEC_COLD_TIMEOUT,
                        "죽은 인코더에 두 번째 질의도 긴 상한을 물었다")

    def test_성공_한_번이_다음_시간_초과를_바쁨으로_바꾼다(self):
        """`_encode_query` 의 성공 기록이 '웜' 판정의 **유일한** 입력이다. 그 한 줄이
        사라지면 인코더가 살아서 바쁠 뿐인 초과가 전부 콜드로 승격돼 긴 상한을 문다 —
        짧은 상한이 원래 막던 사고 그대로이고, 오류도 로그도 안 난다."""
        with _encode_stub([1.0, 0.0]) as url, self._app_at(url) as app:
            self.assertEqual(app._query_embedding("양자역학")[0], [1.0, 0.0])
            self.assertFalse(app._claim_cold_retry(),
                             "방금 성공했는데 콜드 자격을 준다 — 성공이 기록되지 않았다")

    def test_긴_시도가_끝나면_다음_질의가_다시_자격을_얻는다(self):
        """'기다리는 스레드가 있다' 플래그를 안 풀면 그 프로세스는 영영 콜드 재시도를
        못 한다. 인코더가 다시 잠들면 벡터 팔이 안 켜지고, 남는 신호는 평범한 시간
        초과 하나뿐이라 '바쁜 인코더'와 구분되지 않는다."""
        self.srv._QVEC_COLD_COOLDOWN = 0.0    # 쿨다운은 위 테스트가 지킨다
        with _encode_stub([1.0, 0.0], delay=3.0) as url, self._app_at(url) as app:
            self.assertIsNone(app._query_embedding("q1")[0])
            self.assertTrue(app._claim_cold_retry(),
                            "긴 시도가 끝났는데 자격이 안 돌아온다 — 플래그가 샜다")

    def test_긴_상한은_한_번에_한_스레드만_문다(self):
        """동시 질의가 다같이 기다리면 콜드스타트 한 번이 요청 전부를 세운다."""
        app = _app()
        try:
            self.assertTrue(app._claim_cold_retry(), "콜드인데 자격을 안 준다")
            self.assertFalse(app._claim_cold_retry(),
                             "이미 기다리는 스레드가 있는데 또 자격을 줬다")
        finally:
            app.jobs.shutdown(drain=True)


class IngestRouteTests(unittest.TestCase):
    """POST /ingest — 적재 단일화(2026-07-11): 레거시 fragment 경로(Record/props,
    api/fragment.merge_fragment)로 수렴. 테마 적재(Entity/TYPED)는 휴면. MERGE
    create/match 판별·상세 계약은 tests/core/test_view.py 가 커버(여기선 HTTP 왕복)."""

    class _Store:
        """레거시 merge_fragment 는 store.run(cypher, params)->list 를 쓴다(count 는
        빈 리스트=신규). test_view 의 더블과 동형 — HTTP 왕복만 검증한다."""
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

    def test_no_store_refuses_instead_of_degrading(self):
        """무스토어 적재는 **200 강등이 아니라 503 거절**이다(계약 변경).

        전에는 200 + `executed:false` + `warnings:["no reachable store"]` 였다.
        읽기 쪽이 2026-08-16 에 같은 이유로 거절로 바뀌었고, 쓰기에서는 그 거짓말이
        더 나쁘다: `executed:false` 는 "당신 데이터가 안 써졌다"는 뜻인데 상태줄은
        200 이라 HTTP 만 보는 호출자는 저장됐다고 읽는다. 사유는 바디에 그대로 실려
        오므로(`error`·`open_error`) 잃는 정보는 없다."""
        with running_server(_app()) as base:
            code, obj = _req(base, "/ingest", method="POST", body={
                "nodes": [{"id": "concept:a", "type": "concept", "title": "A"}]})
            self.assertEqual(code, 503)
            self.assertFalse(obj["executed"])
            self.assertEqual(obj["error"], "no reachable store")

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
    def test_route_requires_fields(self):
        # 바디 검증만 본다 — 무스토어 거절은 WriteRefusal 쪽이 전수로 본다.
        with running_server(_app(store=_EmptyStore())) as base:
            code, obj = _req(base, "/delete-edge", method="POST",
                             body={"from": "concept:a"})
            self.assertEqual(code, 400)                      # to/type 누락
            code, obj = _req(base, "/delete-edge", method="POST",
                             body={"from": "concept:a", "to": "concept:b",
                                   "type": "relates_to"})
            self.assertEqual(code, 200)


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

    def test_callback_carries_an_optional_mcp_key(self):
        """VIN-132: 기기별 MCP 키는 선택이다. 모양이 틀리면 그 키만 버리고 앱 연결은 산다."""
        with running_server(_app()) as base:
            for sent, want in (("vinc_sk_mcp123", "vinc_sk_mcp123"),
                               ("vinc_ro_mcp456", "vinc_ro_mcp456"),
                               ("not-a-key", ""), (None, "")):
                _, st = _req(base, "/connect/start", method="POST", body={})
                body = {"state": st["state"], "key": "vinc_sk_app", "email": "u@example.com"}
                if sent is not None:
                    body["mcp_key"] = sent
                code, obj = _req(base, "/connect/callback", method="POST", token=None, body=body)
                self.assertEqual((code, obj["status"]), (200, "ok"), sent)
                code, obj = _req(base, f"/connect/status?state={st['state']}")
                self.assertEqual((obj["status"], obj["key"], obj["mcp_key"]),
                                 ("delivered", "vinc_sk_app", want), sent)

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

    def _preflight(self, base: str, origin: str):
        """Chrome 이 공개 오리진 → 루프백에 보내는 preflight 를 그대로 흉내낸다."""
        r = urllib.request.Request(base + "/connect/callback", method="OPTIONS")
        r.add_header("Origin", origin)
        r.add_header("Access-Control-Request-Method", "POST")
        r.add_header("Access-Control-Request-Headers", "content-type")
        r.add_header("Access-Control-Request-Private-Network", "true")
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.headers.get("Access-Control-Allow-Private-Network")

    def test_preflight_grants_private_network_to_connect_origin(self):
        """이 헤더가 없으면 브라우저가 콜백을 아예 못 보낸다 — 연결 플로우가 매번
        수동 키 폴백으로 떨어졌던 원인이다(응답이 200 이라 서버 로그에는 안 남는다)."""
        with running_server(_app()) as base:
            self.assertEqual(self._preflight(base, "https://vincs.io"), "true")

    def test_preflight_denies_private_network_to_others(self):
        """무인증 라우트(/health · /connect/callback)를 웹 전체에 열지 않는다."""
        with running_server(_app()) as base:
            self.assertIsNone(self._preflight(base, "https://evil.example"))


class DocPutValidationTests(unittest.TestCase):
    def test_bad_create_does_not_reach_document_write(self):
        from unittest.mock import patch
        with running_server(_app(_EmptyStore())) as base, patch("api.docstore.put_doc") as put:
            for fields in ({"title": "A"}, {"text": None, "title": "A"},
                           {"text": [], "title": "A"}, {"text": "body", "title": ""},
                           {"text": "body", "title": "   "},
                           {"text": "body", "title": "", "id": "new-doc"}):
                with self.subTest(fields=fields):
                    code, res = _req(base, "/doc/put", method="POST",
                                     body={"owner": "user:a", **fields})
                    self.assertEqual(code, 400, res)
                    self.assertFalse(res["executed"])
            put.assert_not_called()


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

    def test_saved_title_is_findable_without_body_or_node_matches(self):
        title = "QA-TEST 01 stressed user"
        with running_server(self.app) as base:
            code, saved = _req(base, "/doc/put", method="POST", body={
                "owner": "user:a", "title": title, "text": "An unrelated body."})
            self.assertEqual(code, 200)
            # 같은 제목의 남의 문서는 나에게 보이지 않는다.
            _req(base, "/doc/put", method="POST", body={
                "owner": "user:b", "title": title, "text": "Other account."})
            from urllib.parse import urlencode
            params = urlencode({"owner": "user:a", "title": title.lower(), "detail": "summary"})
            code, matches = _req(base, "/docs?" + params)
            self.assertEqual(code, 200)
            self.assertEqual([d["doc_id"] for d in matches["docs"]], [saved["doc_id"]])
            self.assertEqual(matches["docs"][0]["title"], title)
            _, no_match = _req(base, "/docs?" + urlencode({"owner": "user:a", "title": "QA-TEST"}))
            self.assertEqual(no_match["docs"], [])
            # 기존 문서의 제목 생략·명시적 비움은 보존한다(3값 계약).
            _, kept = _req(base, "/doc/put", method="POST", body={
                "owner": "user:a", "id": saved["doc_id"], "text": "An unrelated body."})
            self.assertEqual(kept["title"], title)
            code, cleared = _req(base, "/doc/put", method="POST", body={
                "owner": "user:a", "id": saved["doc_id"], "title": "", "text": "An unrelated body."})
            self.assertEqual(code, 200)
            self.assertEqual(cleared["title"], "")
            # CLI 도구 브리지는 생략한 제목을 null로 보낸다. 빈 문자열과 다르다.
            code, untitled = _req(base, "/doc/put", method="POST", body={
                "owner": "user:a", "id": "cli.beta", "title": None, "text": "Beta body."})
            self.assertEqual(code, 200)
            self.assertTrue(untitled["executed"])

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
            # 프론트매터 domain — 주제 없는 문서는 추출 반영이 거부된다(2026-08-24,
            # 근거: [[vinc.ops.topic-assignment-eval]] §2). 반영 개념은 이 주제를 물려받는다.
            code, put3 = _req(base, "/doc/put", method="POST", body={
                "owner": "user:a", "id": did, "title": "t",
                "text": "---\ndomain: demo\n---\n# t\n\n## 개념하나\n\n"
                        "한 줄 핵심입니다.\n본문에서 개념하나를 다룬다.\n"})
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
                "MERGE (x:Concept {concept_id:$i}) SET x.title='X', "
                "x.one_liner='엑스', x.owner_id='user:a'", {"i": _pk("concept:x")})
            self.store.run(
                "MATCH (a:Concept {concept_id:$a}), (b:Concept {concept_id:$b}) "
                "MERGE (a)-[r:DERIVES_FROM]->(b) SET r.evidence='근거 구절', "
                "r.confidence=0.6",
                {"a": _pk("concept:개념하나"), "b": _pk("concept:x")})
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
            self.store.run("MERGE (c:Concept {concept_id:$i}) "
                           "SET c.stem='개념하나'", {"i": _pk("concept:개념하나")})
            code, hl = _req(base, "/doc/highlights?doc_id=" + did
                            + "&concept_id=" + _up.quote("concept:개념하나") + "&owner=user:a")
            self.assertEqual(code, 200)
            self.assertTrue(hl["executed"])
            self.assertEqual(hl["method"], "tokens")
            # D45-V2 4단계 — /docs-in-range: 기간 항목(Episode.date)
            self.store.run("MERGE (e:Episode {episode_id:$i}) SET e.owner_id='user:a', "
                           "e.title='EV', e.date='2026-05-15', e.domain='d'",
                           {"i": _pk("ev1")})
            code, rng = _req(base, "/docs-in-range?start=2026-05-01&end=2026-06-01&owner=user:a")
            self.assertEqual(code, 200)
            self.assertTrue(rng["executed"])
            self.assertIn("ev1", {i["id"] for i in rng["items"]})
            # D69 — /nav-tree: 좌측 문서 트리(주제 → 문서 + 착지 노드).
            # 추출 개념이 문서의 주제(demo)를 물려받으므로(2026-08-24) 문서는 더는
            # 미분류가 아니다 — 곧장 그 주제 밑에 서고 착지 노드도 잡힌다.
            code, nav = _req(base, "/nav-tree?owner=user:a")
            self.assertEqual(code, 200)
            self.assertTrue(nav["executed"])
            self.assertNotIn(did, {d["doc_id"] for d in nav["unfiled"]})
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
            # 본문만 다시 보내도 출처가 남고, 빈 문자열을 명시하면 본문을
            # 재청킹하지 않고 출처만 지울 수 있다.
            code, kept = _req(base, "/doc/put", method="POST",
                              body={**body, "id": a["doc_id"]})
            self.assertEqual(code, 200)
            self.assertTrue(kept["skipped"])
            code, got = _req(base, f"/doc?id={a['doc_id']}&owner=user:a")
            self.assertEqual(got["origin"], "notes.md")
            code, cleared = _req(base, "/doc/put", method="POST",
                                 body={**body, "id": a["doc_id"], "origin": ""})
            self.assertEqual(code, 200)
            self.assertFalse(cleared["skipped"])
            self.assertEqual(cleared["replaced"], 0)
            code, got = _req(base, f"/doc?id={a['doc_id']}&owner=user:a")
            self.assertEqual(got["origin"], "")

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
            # 답은 이 스토어의 벽시계를 싣는다(받는 기기가 믿는 출처의 시계로 적는다).
            self.assertRegex(exp["clock"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
            # 남의 델타는 SEC-1 로 튕긴다(로컬 문서 소유자가 다르면 적용 안 함).
            code, other = _req(base, "/sync/docs-apply", method="POST",
                               body={"owner": "user:b", "payload": exp})
            self.assertEqual(code, 200, other)
            self.assertEqual((other["applied"], other["skipped"]), (0, 1))
            # 삭제 이전 사본은 부활하지 않고, 이후 작성한 사본만 재청킹한다.
            code, _ = _req(base, "/doc/delete", method="POST",
                           body={"id": did, "owner": "user:a"})
            self.assertEqual(code, 200)
            code, back = _req(base, "/sync/docs-apply", method="POST",
                              body={"owner": "user:a", "payload": exp})
            self.assertEqual(code, 200, back)
            self.assertEqual((back["applied"], back["skipped"]), (0, 1))
            from api import revstamp
            later = revstamp._fmt(revstamp._wall_ms() + 60_000)   # 삭제보다 새것, 허용 스큐 안
            recreated = {**exp, "docs": [{**d, "updated_at": later} for d in exp["docs"]]}
            code, back = _req(base, "/sync/docs-apply", method="POST",
                              body={"owner": "user:a", "payload": recreated})
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


class EnvelopeAndDocListTests(unittest.TestCase):
    """F4·F5 — 봉투 통일과 문서 목록 페이지네이션.

    실측(2026-08-07, 라이브 13종): `kind` 가 9/13 에만 있었고 없는 넷이 하필
    recall·domains·graph·suggest_ties 였다. 그리고 `vinc_doc_list` 는 문서 120건에서
    **51,537B(약 12,900토큰)** 였다 — 목록 한 번이 컨텍스트를 삼킨다.
    """

    class _Store:
        driver_name = "fake"
        def __init__(self, n=0): self.n = n
        def reader_view(self): return self
        def run(self, cypher, params=None):
            # ⚠ 조건에 "count" 를 쓰면 안 된다 — 이 쿼리 자체가
            #   `d.chunk_count AS chunks` 를 담고 있어 스스로 걸린다.
            if "MATCH (d:_DocMeta)" in cypher and "RETURN d.doc_id" in cypher:
                return [{"doc_id": f"d{i:03d}", "title": f"문서 {i}", "origin": "o",
                         "chunks": 3, "updated_at": f"2026-08-{(i % 28) + 1:02d}",
                         "needs_embed": 0, "created_at": "", "content_time": ""}
                        for i in range(self.n)]
            return []
        def close(self): pass

    def _get(self, path, n=0):
        with running_server(_app(store=self._Store(n))) as base:
            return _req(base, path)[1]

    def test_kind_가_모든_봉투에_있다(self):
        """호스트가 코드로 후처리하려면 종류 표시가 **항상** 있어야 한다."""
        cases = {
            "/recall?query=x&owner=u": "recall",
            "/domains?owner=u": "domains",
            "/graph?owner=u&seed=concept:a": "graph",
            "/suggest-ties?owner=u&node=concept:a": "suggest_ties",
        }
        for path, kind in cases.items():
            res = self._get(path)
            self.assertEqual(res.get("kind"), kind, f"{path} 봉투에 kind 가 없다: {res}")
            self.assertIn("executed", res, f"{path} 에 executed 가 없다")

    def test_문서_목록_기본은_전량이다__navtree_보호(self):
        """`nav_tree` 가 이 경로를 그대로 쓰면서 topic·primary_concept 를 요구한다.
        기본을 요약으로 바꾸면 **좌측 문서 트리가 조용히 무너진다.**"""
        res = self._get("/docs?owner=u", n=40)
        self.assertEqual(res["count"], 40)
        self.assertNotIn("has_more", res, "전량 호출의 응답 형태가 바뀌었다")
        self.assertIn("topics", res["docs"][0], "full 인데 파생 필드가 빠졌다")

    def test_limit_을_주면_잘리고_커서가_따라온다(self):
        res = self._get("/docs?owner=u&limit=10&detail=summary", n=40)
        self.assertEqual(res["count"], 10)
        self.assertEqual(res["total_count"], 40)
        self.assertTrue(res["has_more"])
        self.assertTrue(res["next_cursor"])

    def test_커서로_다음_장을_이어_읽는다(self):
        first = self._get("/docs?owner=u&limit=30&detail=summary", n=40)
        nxt = self._get(
            f"/docs?owner=u&limit=30&detail=summary&cursor={first['next_cursor']}", n=40)
        self.assertEqual(nxt["count"], 10)
        self.assertFalse(nxt["has_more"])
        self.assertIsNone(nxt["next_cursor"])
        self.assertEqual(nxt["docs"][0]["doc_id"], "d030", "장 경계가 어긋났다")

    def test_요약형은_파생_필드를_아예_계산하지_않는다(self):
        """크기만의 문제가 아니다 — topics·primary_concept 는 전 문서의 MENTIONS 를
        훑어야 나오는 파생값이라, 목록을 보려고 그것까지 계산하면 왕복도 함께 낸다."""
        res = self._get("/docs?owner=u&limit=5&detail=summary", n=40)
        d = res["docs"][0]
        self.assertEqual(set(d), {"doc_id", "title", "updated_at", "chunks"},
                         f"요약형에 군더더기가 실렸다: {sorted(d)}")


class FailureEnvelopeTests(unittest.TestCase):
    """F4 잔여 + F7 — 실패 응답도 봉투를 지키고, 다음 행동을 안내한다.

    실측(2026-08-07)한 이전 상태는 제각각이었다:
        doc_get      → error 만            neighbors → **아무 말도 없음**
        decide       → note 만            graph     → found:false 뿐
    같은 실패인데 호출부가 세 가지 방식으로 읽어야 했다.
    """

    class _Store:
        driver_name = "fake"
        def reader_view(self): return self
        def run(self, cypher, params=None): return []
        def close(self): pass

    def _get(self, path):
        with running_server(_app(store=self._Store())) as base:
            return _req(base, path)[1]

    def test_실패에도_kind_와_executed_가_있다(self):
        """`executed` 는 **참**이어야 한다 — 조회는 돌았고 답이 없을 뿐이다.
        거짓으로 두면 호출부가 스토어 장애와 구분하지 못한다."""
        for path, kind in (("/decide?topic=%EC%97%86%EB%8A%94%EA%B2%83&owner=u", "decide"),
                           ("/graph?owner=u&seed=concept:%EC%97%86%EB%8A%94%EA%B2%83", "graph"),
                           ("/neighbors?owner=u&node=concept:%EC%97%86%EB%8A%94%EA%B2%83", "neighbors"),
                           ("/suggest-ties?owner=u&node=concept:%EC%97%86%EB%8A%94%EA%B2%83", "suggest_ties")):
            res = self._get(path)
            self.assertEqual(res.get("kind"), kind, f"{path}: {res}")
            self.assertTrue(res.get("executed"), f"{path} 가 실행조차 안 한 것으로 보고했다")
            self.assertFalse(res.get("found", False), f"{path} 가 찾았다고 보고했다")

    def test_실패는_다음_행동을_말한다(self):
        """이 리포에 이미 좋은 예가 있다 — 상한 초과가 'lower depth (e.g. 1) or pick a
        more specific seed_id' 라고 말한다. 그 형태를 실패 경로 전체에 퍼뜨린 것이다."""
        for path in ("/decide?topic=%EC%97%86%EB%8A%94%EA%B2%83&owner=u",
                     "/graph?owner=u&seed=concept:%EC%97%86%EB%8A%94%EA%B2%83",
                     "/neighbors?owner=u&node=concept:%EC%97%86%EB%8A%94%EA%B2%83",
                     "/suggest-ties?owner=u&node=concept:%EC%97%86%EB%8A%94%EA%B2%83"):
            res = self._get(path)
            hint = res.get("hint") or ""
            self.assertTrue(hint, f"{path} 에 hint 가 없다")
            self.assertIn("vinc_search", hint,
                          f"{path} 의 hint 가 다음 수를 안 준다: {hint!r}")

    def test_decide_는_정확_일치라는_것을_말한다(self):
        """`topic` 은 concept_id·decision_id·title 정확 일치다. 부분 일치가 아니라서
        `view` 는 안 맞고 `concept:view` 라야 맞는데, 그 계약이 어디에도 없었다."""
        res = self._get("/decide?topic=view&owner=u")
        self.assertIn("exactly", res["hint"])
        self.assertIn("concept:view", res["hint"], "예시 id 가 없다")


class BadReadInputTests(unittest.TestCase):
    """잘못된 조회 인자는 400 과 핸들러가 만든 오류 본문으로 답한다.

    핸들러가 `(code, obj)` 대신 dict 만 돌려주면 라우터가 dict 의 키 둘을 풀어
    `_send` 가 터지고, 클라이언트는 답 대신 끊긴 연결을 받는다."""

    CASES = (
        ("/node-edges", {"owner": "u", "nodes": "not-json"},
         {"executed": False, "error": "invalid nodes JSON array"}),
        ("/roles", {"owner": "u", "limit": "x"},
         {"executed": False, "error": "invalid role pagination"}),
    )

    def test_http_route_answers_400_with_the_handler_body(self):
        import urllib.parse
        with running_server(_app(store=_EmptyStore())) as base:
            for path, params, body in self.CASES:
                got = _req(base, path + "?" + urllib.parse.urlencode(params))
                self.assertEqual(got, (400, body), path)

    def test_in_process_bridge_gets_the_same_400(self):
        from unittest.mock import patch
        from api.cli_tools import _InProcessBridge
        bridge = _InProcessBridge(_app(store=_EmptyStore()))
        seen = []
        # 브리지는 본문만 돌려주므로 상태 코드는 진단 기록에서 읽는다.
        with patch("api.cli_tools.diagnostics.trace",
                   lambda method, route, status, *a, **k: seen.append(status)):
            got = [bridge.request("GET", path, params) for path, params, _ in self.CASES]
        self.assertEqual((seen, got), ([400, 400], [body for _, _, body in self.CASES]))


class SyncFlagTests(unittest.TestCase):
    """sync 는 접수 때 깃발을 세운다. 겹친 요청은 하나만 잡이 되고, 잡이 끝나면
    (예외로 끝나도) 다시 받는다."""

    BUSY = (409, {"error": "a sync is already in progress"})

    def _hold_worker(self, app):
        # 워커를 붙잡아 sync 잡이 큐에서 기다리게 한다: 접수와 실행 사이의 틈을 고정한다.
        hold = threading.Event()
        app.jobs.submit("hold", lambda: hold.wait(5) and {})
        return hold

    def _wait(self, app, accepted):
        # 받은 잡은 테스트가 끝나기 전에 반드시 기다린다. 남긴 잡은 `sync.run` 을 모듈에서
        # 찾으므로, 늦게 돌면 다음 테스트가 건 패치를 부른다.
        job = app.jobs.get(accepted[1]["job_id"])
        self.assertTrue(job._done.wait(5), "sync 잡이 끝나지 않았다")
        return job

    def test_overlapping_presses_make_exactly_one_job(self):
        from unittest.mock import patch
        store = _EmptyStore()
        app = _app(store=store)
        calls, entered, release = [], threading.Event(), threading.Event()

        def fake_run(st, *a, **k):
            # 이 앱의 잡만 센다. 다른 앱이 남긴 잡이 패치를 불러도 숫자가 흔들리지 않는다.
            if st is not store:
                return {"executed": True}
            calls.append(1)
            entered.set()
            release.wait(5)
            return {"executed": True}

        with patch("api.server.sync.run", fake_run):
            hold = self._hold_worker(app)
            gate = threading.Barrier(8)
            answers = []

            def press():
                gate.wait(5)
                answers.append(app.submit_sync({"token": "k"}))

            threads = [threading.Thread(target=press) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(5)
            accepted = [a for a in answers if a[0] == 202]
            self.assertEqual(len(accepted), 1, [a[0] for a in answers])
            self.assertEqual([a for a in answers if a[0] != 202], [self.BUSY] * 7)
            # 같은 깃발이 데이터 이동도 막는다.
            self.assertEqual(app._submit_move("sync:push-all", dict)[0], 409)
            hold.set()
            self.assertTrue(entered.wait(5), "sync 잡이 시작되지 않았다")
            self.assertEqual(app.submit_sync({"token": "k"}), self.BUSY)
            release.set()
            self._wait(app, accepted[0])
            self.assertEqual(len(calls), 1)
            again = app.submit_sync({"token": "k"})
            self.assertEqual(again[0], 202)
            self._wait(app, again)
            self.assertEqual(len(calls), 2)

    def test_a_job_that_raises_still_clears_the_flag(self):
        from unittest.mock import patch
        app = _app(store=_EmptyStore())

        def boom(*a, **k):
            raise RuntimeError("remote went away")

        with patch("api.server.sync.run", boom):
            first = app.submit_sync({"token": "k"})
            self.assertEqual(first[0], 202)
            self.assertEqual(self._wait(app, first).status, "error")
            second = app.submit_sync({"token": "k"})
            self.assertEqual(second[0], 202)
            # 패치가 걸린 동안 끝까지 기다린다(위 `_wait` 설명).
            self.assertEqual(self._wait(app, second).status, "error")

    def test_a_job_refused_before_it_runs_still_clears_the_flag(self):
        # 실행 전 가드(on_before_run)가 거절하면 잡 함수는 불리지 않는다.
        from api.jobs import JobQueue

        def refuse(job):
            raise RuntimeError("write refused")

        app = VincApp(store=_EmptyStore(), auth=SessionAuth(_TOKEN),
                      jobs=JobQueue(on_before_run=refuse))
        first = app.submit_sync({"token": "k"})
        self.assertEqual(self._wait(app, first).status, "error")
        second = app.submit_sync({"token": "k"})
        self.assertEqual(second[0], 202)
        self.assertEqual(self._wait(app, second).status, "error")
