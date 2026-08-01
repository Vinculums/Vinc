# -*- coding: utf-8 -*-
"""api/graphmd — GRAPH.md 생성·검증(vinc.c.graph-md) + 동기화 제외.

순수 테스트(스토어 불필요):
  - 출하 템플릿 EN·ko 가 스스로의 9규칙을 통과한다(60줄 예산 포함).
  - 규칙별 위반이 정확한 수준(BLOCK/FIX)으로 잡힌다.
  - build 가 **산문을 보존**하고 생성 구역만 갈아 끼운다.
실엔진 테스트:
  - 도메인 census 가 생성 구역에 반영되고, 손대면 drift 로 잡힌다.
  - 빈 그래프에서도 문장이 나온다(빈 목록을 두면 어시스턴트가 "도메인 금지"로 오독).
  - 관례 문서는 **export 되지 않고 import 로 들어오지도 않는다**(각 그래프는 자기 관례).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import docstore as ds       # noqa: E402
from api import docsync              # noqa: E402
from api import graphmd              # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()

DAY = "2026-08-01"


class TemplateTests(unittest.TestCase):
    def _built(self, lang):
        r = graphmd.build(None, lang=lang, day=DAY)
        self.assertTrue(r["executed"])
        return r["text"]

    def test_shipped_templates_pass_their_own_rules(self):
        for lang in ("en", "ko"):
            with self.subTest(lang=lang):
                res = graphmd.check(self._built(lang))
                self.assertEqual(res["exit_code"], 0, res["findings"])
                self.assertEqual(res["lang"], lang)

    def test_templates_fit_the_line_budget(self):
        for lang in ("en", "ko"):
            n = len(self._built(lang).rstrip().split("\n"))
            self.assertLessEqual(n, graphmd.LINE_WARN, f"{lang}: {n} lines")

    def test_missing_header_blocks(self):
        text = self._built("en").split("\n", 2)[2]      # 헤더 마커 제거
        res = graphmd.check(text)
        self.assertEqual(res["exit_code"], 2)
        self.assertIn("header-marker", [f["rule"] for f in res["findings"]])

    def test_broken_markers_block(self):
        text = self._built("en").replace(graphmd._GEN_TAIL, "")
        res = graphmd.check(text)
        self.assertIn("generated-block", [f["rule"] for f in res["findings"]])
        self.assertEqual(res["exit_code"], 2)

    def test_unknown_edge_blocks(self):
        text = self._built("en").replace(
            "- about: Episode to Concept.", "- refutes: Concept to Concept.")
        res = graphmd.check(text)
        rules = [f["rule"] for f in res["findings"]]
        self.assertIn("unknown-edge", rules)
        self.assertEqual(res["exit_code"], 2)

    def test_wire_edges_are_always_allowed_without_a_store(self):
        res = graphmd.check(self._built("ko"))
        self.assertNotIn("unknown-edge", [f["rule"] for f in res["findings"]])

    def test_em_dash_is_a_fix(self):
        text = self._built("en").replace("one reusable idea", "one reusable idea — really")
        res = graphmd.check(text)
        self.assertEqual(res["exit_code"], 1)
        self.assertEqual([f["rule"] for f in res["findings"]], ["forbidden-glyph"])

    def test_stale_tool_name_is_a_fix(self):
        text = self._built("en").replace("vinc_doc_put", "vinc_doc_upsert")
        res = graphmd.check(text)
        self.assertIn("stale-tool", [f["rule"] for f in res["findings"]])
        self.assertEqual(res["exit_code"], 1)

    def test_too_long_blocks(self):
        text = self._built("en") + "\nfiller\n" * 60
        res = graphmd.check(text)
        levels = {f["rule"]: f["level"] for f in res["findings"]}
        self.assertEqual(levels.get("length"), graphmd.BLOCK)

    def test_section_out_of_order_blocks(self):
        lines = self._built("en").split("\n")
        i = lines.index("## Relations [vinc]")
        j = lines.index("## Documents [vinc]")
        lines[i], lines[j] = lines[j], lines[i]
        res = graphmd.check("\n".join(lines))
        self.assertIn("section-order", [f["rule"] for f in res["findings"]])

    def test_unknown_language_degrades_to_one_fix(self):
        res = graphmd.check("# GRAPH.md\n<!-- vinc-graph-md: 1 -->\n\n## Qu'est-ce que c'est\n"
                            "<!-- vinc:generated start -->\n<!-- vinc:generated end -->\n")
        rules = [f["rule"] for f in res["findings"]]
        self.assertEqual(rules.count("section-order"), 1)
        self.assertEqual(res["exit_code"], 1)     # 9개 BLOCK 폭탄이 아니라 FIX 하나

    def test_build_preserves_prose_and_replaces_only_the_block(self):
        original = self._built("en").replace(
            "- Do not invent a domain name inline.",
            "- Do not invent a domain name inline.\n- My own house rule.")
        out = graphmd.build(None, existing=original, day="2026-09-09")
        self.assertTrue(out["executed"])
        self.assertTrue(out["replaced"])
        self.assertIn("- My own house rule.", out["text"])       # 산문 보존
        self.assertIn("built 2026-09-09", out["text"])           # 헤더만 재스탬프
        self.assertNotIn("built 2026-08-01", out["text"])

    def test_unknown_lang_falls_back_instead_of_crashing(self):
        out = graphmd.build(None, lang="fr", day=DAY)      # HTTP 로 들어올 수 있는 값
        self.assertTrue(out["executed"])
        self.assertEqual(out["lang"], "en")

    def test_build_refuses_a_file_with_broken_markers(self):
        out = graphmd.build(None, existing="# GRAPH.md\n\n## What this is [core]\nhi\n")
        self.assertFalse(out["executed"])
        self.assertIn("markers", out["error"])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class _StoreCase(unittest.TestCase):
    """실엔진 픽스처만. 케이스를 상속으로 나누면 부모 테스트가 두 번 돈다."""

    def _open(self, name):
        from store.backend import open_store
        from store.config import load_config
        st = open_store(load_config({"data_dir": os.path.join(self._tmp, name)}))
        st.provision(dim=4)
        return st

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k) for k in
                       ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR")}
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        os.environ.pop("MEMORY_DATA_DIR", None)
        self.A = self._open("A")

    def tearDown(self):
        self.A.close()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _seed_domains(self):
        for i, dom in enumerate(("research", "product")):
            self.A.run(
                "MERGE (c:Concept {concept_id: $id}) SET c.owner_id = $o, c.domain = $d, "
                "c.title = $t", {"id": f"concept:c{i}", "o": "user:a", "d": dom, "t": f"c{i}"})


class StoreTests(_StoreCase):
    def test_empty_graph_gets_a_sentence_not_an_empty_list(self):
        text = graphmd.build(self.A, owner="user:a", day=DAY)["text"]
        self.assertIn("No domains yet", text)
        self.assertEqual(graphmd.check(text, store=self.A, owner="user:a")["exit_code"], 0)

    def test_domains_land_in_the_generated_region(self):
        self._seed_domains()
        text = graphmd.build(self.A, owner="user:a", day=DAY)["text"]
        self.assertIn("research", text)
        self.assertIn("product", text)
        self.assertEqual(graphmd.check(text, store=self.A, owner="user:a")["exit_code"], 0)

    def test_hand_editing_the_generated_region_is_drift(self):
        self._seed_domains()
        text = graphmd.build(self.A, owner="user:a", day=DAY)["text"]
        tampered = text.replace("research", "reserch")
        res = graphmd.check(tampered, store=self.A, owner="user:a")
        rules = [f["rule"] for f in res["findings"]]
        self.assertIn("generated-drift", rules)
        self.assertIn("unknown-domain", rules)
        self.assertEqual(res["exit_code"], 2)

    def test_stale_file_is_drift_after_the_graph_grows(self):
        text = graphmd.build(self.A, owner="user:a", day=DAY)["text"]   # 빈 그래프본
        self._seed_domains()                                            # 그 뒤 자란다
        res = graphmd.check(text, store=self.A, owner="user:a")
        self.assertIn("generated-drift", [f["rule"] for f in res["findings"]])
        rebuilt = graphmd.build(self.A, owner="user:a", existing=text, day=DAY)["text"]
        self.assertEqual(graphmd.check(rebuilt, store=self.A, owner="user:a")["exit_code"], 0)

    def _put_conventions(self, text="# GRAPH.md\nOptimistic concurrency is an example.\n"):
        return ds.put_doc(self.A, owner="user:a", text=text,
                          doc_id=graphmd.CONVENTIONS_DOC_ID, title="GRAPH.md",
                          origin=graphmd.CONVENTIONS_ORIGIN)

    def test_hidden_from_the_document_list(self):
        self._put_conventions()
        ds.put_doc(self.A, owner="user:a", text="# Real\nOptimistic concurrency in practice.\n",
                   title="Real")
        ids = [d["doc_id"] for d in ds.list_docs(self.A, owner="user:a")["docs"]]
        self.assertNotIn(graphmd.CONVENTIONS_DOC_ID, ids)
        self.assertEqual(len(ids), 1)

    def test_still_reachable_by_exact_id(self):
        """목록에서 감추는 것과 없는 것은 다르다 — 정확한 id 로는 여전히 읽힌다."""
        self._put_conventions()
        got = ds.get_doc(self.A, graphmd.CONVENTIONS_DOC_ID, owner="user:a")
        self.assertTrue(got.get("executed"))
        self.assertIn("GRAPH.md", got.get("text") or "")

    def test_hidden_from_the_left_tree(self):
        """nav_tree 는 list_docs 를 그대로 쓴다 — 한 자리를 막으면 둘 다 막힌다."""
        from api import query as q
        self._put_conventions()
        tree = q.nav_tree(owner="user:a", store=self.A)
        blob = repr(tree)
        self.assertNotIn(graphmd.CONVENTIONS_DOC_ID, blob)

    def test_conventions_doc_never_syncs(self):
        ds.put_doc(self.A, owner="user:a", text="# GRAPH.md\nrules\n",
                   doc_id=graphmd.CONVENTIONS_DOC_ID, title="Conventions",
                   origin=graphmd.CONVENTIONS_ORIGIN)
        ds.put_doc(self.A, owner="user:a", text="# 보통 문서\n본문\n", title="보통")

        delta = docsync.export_docs(self.A, owner="user:a", since=0)
        ids = [d["doc_id"] for d in delta["docs"]]
        self.assertNotIn(graphmd.CONVENTIONS_DOC_ID, ids)
        self.assertEqual(len(ids), 1)
        # 커서는 건너뛴 문서까지 포함해 전진한다 — 다음 pull 이 되돌아가지 않게.
        self.assertGreaterEqual(delta["cursor"], 2)

        # 구 버전 송신자가 실어 보내도 받지 않는다(대칭 가드).
        B = self._open("B")
        try:
            payload = {"docs": [{"doc_id": graphmd.CONVENTIONS_DOC_ID, "title": "theirs",
                                 "text": "# theirs\n", "updated_at": "2099", "origin": ""}],
                       "tombstones": []}
            res = docsync.import_docs(B, owner="user:b", payload=payload)
            self.assertEqual(res["applied"], 0)
            self.assertEqual(res["skipped"], 1)
            self.assertFalse(ds.get_doc(B, graphmd.CONVENTIONS_DOC_ID).get("executed"))
        finally:
            B.close()


class SearchFilterTests(unittest.TestCase):
    """검색 결과에서 관례 청크를 뺀다(GM-7).

    실엔진 FTS 대신 arm 을 직접 넣는다: 이 환경의 Chunk FTS 는 인덱스를 만들어도 0건을
    돌려주는 선행 문제가 있어(실측 2026-08-01, PLAN-embedding-retrieval 계열),
    그 위에서 테스트하면 필터가 아니라 인덱스를 재는 셈이 된다."""

    def _arms(self, rows, top_k=10):
        from api import query as q

        class Stub:
            def fulltext_search(self, *_a, **_k):
                return [dict(r) for r in rows]
        return q._chunk_arms(Stub(), "x", None, top_k, None)

    def test_conventions_chunks_are_dropped(self):
        out = self._arms([{"id": "graph.md#00000", "text": "conventions"},
                          {"id": "doc-42#00000", "text": "real"}])
        self.assertEqual([r["id"] for r in out], ["doc-42#00000"])

    def test_a_document_merely_named_like_it_survives(self):
        """접두는 `graph.md#` 다 — `graph.md-notes` 같은 남의 문서를 삼키지 않는다."""
        out = self._arms([{"id": "graph.md-notes#00000", "text": "mine"}])
        self.assertEqual([r["id"] for r in out], ["graph.md-notes#00000"])

    def test_filtering_happens_before_the_top_k_cut(self):
        """융합·절단 뒤에 걸렀다면 관례가 상위 k 를 잠식해 실 결과가 줄어든다."""
        rows = [{"id": f"graph.md#{i:05d}", "text": "c"} for i in range(3)]
        rows += [{"id": f"doc-{i}#00000", "text": "r"} for i in range(3)]
        out = self._arms(rows, top_k=3)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(r["id"].startswith("doc-") for r in out))


class ToolContractTests(unittest.TestCase):
    """MCP 표면 계약 — 도구가 존재하고, 읽기 전용이며, 쓰기 도구가 그것을 가리킨다."""

    def test_conventions_is_a_read_only_tool(self):
        from vinc_mcp import tools
        spec = next(t for t in tools.specs() if t["name"] == "vinc_conventions")
        self.assertTrue(spec["annotations"]["readOnlyHint"])
        self.assertNotIn("required", spec["inputSchema"])   # owner 없이도 부를 수 있다

    def test_ingest_points_at_conventions(self):
        """이 한 문장이 '아무도 안 읽는다' 실패 모드에 대한 유일한 구조적 방어다.
        도구 설명은 모델 제어라 항상 실린다 — 지우면 방어가 사라진다."""
        from vinc_mcp import tools
        ingest = next(t for t in tools.TOOLS if t["name"] == "vinc_ingest")
        self.assertIn("vinc_conventions", ingest["description"])

    def test_tool_routes_to_the_sidecar_route(self):
        from vinc_mcp import tools
        from vinc_mcp.bridge import Bridge

        class Stub(Bridge):
            def __init__(self):
                super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
                self.seen = None

            def request(self, method, path, params=None, body=None):
                self.seen = (method, path, params)
                return {"ok": True}

        b = Stub()
        tools.call("vinc_conventions", {}, b)
        self.assertEqual(b.seen[0], "GET")
        self.assertEqual(b.seen[1], "/conventions")
        self.assertEqual(b.seen[2]["owner"], "user:local")

    def test_web_proxy_allowlist_carries_it(self):
        """허브 화이트리스트에 없으면 웹 Vinc 에서만 조용히 404 로 죽는다(합계 뷰 실사고).

        읽기와 재생성은 **짝**이다 — 읽기만 열면 관례 화면은 열리는데 [다시 생성]만
        죽어서, 들어가는 문은 없는데 안쪽 문만 열린 그 사고가 그대로 재현된다."""
        from vinc_mcp.http_server import _PROXY_OPS
        self.assertEqual(_PROXY_OPS.get("conventions"), ("GET", "/conventions"))
        self.assertEqual(_PROXY_OPS.get("conventions-build"),
                         ("POST", "/conventions/build"))


class ConventionsRouteTests(_StoreCase):
    """`/conventions` — 관례 문서가 없어도 **빈손으로 돌려보내지 않는다**."""

    def _app(self):
        from api.server import VincApp
        return VincApp(store=self.A)

    def test_default_when_no_document_yet(self):
        self._seed_domains()
        code, out = self._app().read_conventions({"owner": "user:a"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out["source"], "default")
        self.assertTrue(out["missing"])
        self.assertIn("research", out["text"])       # 기본본에도 도메인은 실린다
        self.assertIn("relates_to", out["text"])

    def test_the_owners_own_document_wins(self):
        ds.put_doc(self.A, owner="user:a", text="# GRAPH.md\n내 관례\n",
                   doc_id=graphmd.CONVENTIONS_DOC_ID, title="GRAPH.md",
                   origin=graphmd.CONVENTIONS_ORIGIN)
        code, out = self._app().read_conventions({"owner": "user:a"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out["source"], "doc")
        self.assertNotIn("missing", out)
        self.assertIn("내 관례", out["text"])

    def test_language_choice_applies_to_the_default(self):
        code, out = self._app().read_conventions({"owner": "user:a", "lang": "ko"})
        self.assertEqual(code, 200, out)
        self.assertIn("노드 종류", out["text"])

    def test_the_route_is_actually_wired(self):
        """핸들러 직접 호출은 라우트 표 등재를 증명하지 않는다 — 실 HTTP 로 한 번 친다.
        등재를 빠뜨리면 테스트는 다 통과하고 **프로덕션만 404** 가 된다."""
        import contextlib, json as _json, threading, urllib.request
        from api.auth import SessionAuth
        from api.server import make_server

        app = self._app()
        app.auth = SessionAuth(token="t-graphmd")
        httpd = make_server(app, host="127.0.0.1", port=0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_address[1]}/conventions?owner=user:a")
            req.add_header("Authorization", "Bearer t-graphmd")
            with contextlib.closing(urllib.request.urlopen(req, timeout=5)) as resp:
                self.assertEqual(resp.status, 200)
                out = _json.loads(resp.read().decode("utf-8"))
            self.assertEqual(out["source"], "default")
            self.assertIn("relates_to", out["text"])
        finally:
            httpd.shutdown()

    def test_build_route_saves_and_keeps_the_prose(self):
        """앱의 [다시 생성] — 생성 구역만 새로 쓰고 사람이 쓴 줄은 남긴다."""
        app = self._app()
        code, first = app.write_conventions_build({"owner": "user:a"})
        self.assertEqual(code, 200, first)
        mine = first["text"].replace("- Do not invent a domain name inline.",
                                     "- Do not invent a domain name inline.\n- My rule.")
        ds.put_doc(self.A, owner="user:a", text=mine,
                   doc_id=graphmd.CONVENTIONS_DOC_ID, title="GRAPH.md")
        self._seed_domains()                       # 그 사이 그래프가 자란다
        code, second = app.write_conventions_build({"owner": "user:a"})
        self.assertEqual(code, 200, second)
        self.assertIn("- My rule.", second["text"])        # 산문 보존
        self.assertIn("research", second["text"])          # 생성 구역은 갱신
        stored = ds.get_doc(self.A, graphmd.CONVENTIONS_DOC_ID, owner="user:a")
        self.assertEqual(stored.get("text"), second["text"])   # 저장까지 끝난다

    def test_build_route_needs_an_owner(self):
        code, out = self._app().write_conventions_build({})
        self.assertEqual(code, 400)
        self.assertIn("owner", out["error"])

    def test_another_owner_does_not_see_it(self):
        ds.put_doc(self.A, owner="user:a", text="# GRAPH.md\n내 관례\n",
                   doc_id=graphmd.CONVENTIONS_DOC_ID, title="GRAPH.md")
        code, out = self._app().read_conventions({"owner": "user:b"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out["source"], "default")   # SEC-1 — 남의 관례는 안 보인다
        self.assertNotIn("내 관례", out["text"])


if __name__ == "__main__":
    unittest.main()
