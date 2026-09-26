# -*- coding: utf-8 -*-
"""api/graphmd — GRAPH.md 생성·검증(vinc.c.graph-md) + 동기화 제외.

순수 테스트(스토어 불필요):
  - 출하 템플릿 EN·ko 가 스스로의 9규칙을 통과한다(70줄 예산 포함).
  - Roles·Topics 는 **선택 절**이다: 템플릿에는 있고 순서가 맞아야 하지만, 없는
    파일도 통과한다(2026-09-05 이전에 만들어진 파일이 전부 그렇다).
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

    def test_templates_are_line_for_line_parallel(self):
        """EN/ko 1:1 은 규격(§4)의 요구인데 여태 관례로만 지켜졌다 — 여기서 고정한다.
        GM-11a 가 입도 절에 두 줄을 더하며 예산(60)이 꽉 찼으므로, 한쪽만 늘어나는
        회귀를 이 assert 가 잡는다."""
        n_en = len(self._built("en").rstrip().split("\n"))
        n_ko = len(self._built("ko").rstrip().split("\n"))
        self.assertEqual(n_en, n_ko, "EN/ko 템플릿 줄 수가 어긋났다(1:1 규격 위반)")

    def test_optional_sections_ship_in_both_templates_in_order(self):
        """Roles 는 프롬프트 표면이 읽는 모양이고 Topics 는 트리 짓는 법이다. 둘 다
        Documents 뒤, 생성 구역 앞에 온다."""
        for lang, (roles, topics, docs) in (("en", ("## Roles", "## Topics", "## Documents")),
                                            ("ko", ("## 역할", "## 주제", "## 문서"))):
            with self.subTest(lang=lang):
                text = self._built(lang)
                i_d, i_r, i_t = text.index(docs), text.index(roles), text.index(topics)
                i_g = text.index(graphmd._GEN_HEAD[lang])
                self.assertTrue(i_d < i_r < i_t < i_g, (i_d, i_r, i_t, i_g))
                self.assertIn("concept:agent-", text)
                self.assertIn("vinc/agent", text)

    def test_a_file_without_the_optional_sections_still_passes(self):
        """예전 파일에는 두 절이 없다. 그것을 BLOCK 으로 만들면 이 버전이 모든 사용자의
        관례 파일을 한꺼번에 빨갛게 칠한다 — 산문은 build 가 채워 주지 않는다."""
        lines = self._built("en").split("\n")
        i = lines.index("## Roles [schema-fixed]")
        j = lines.index(graphmd._GEN_HEAD["en"])
        stripped = "\n".join(lines[:i] + lines[j:])
        res = graphmd.check(stripped)
        self.assertEqual(res["exit_code"], 0, res["findings"])
        self.assertNotIn("section-order", [f["rule"] for f in res["findings"]])

    def test_optional_section_out_of_order_blocks(self):
        """선택은 '없어도 된다'이지 '아무 데나 있어도 된다'가 아니다."""
        lines = self._built("en").split("\n")
        i = lines.index("## Topics [this graph decides]")
        block, rest = lines[i:i + 3], lines[:i] + lines[i + 3:]
        k = rest.index("## Documents [schema-fixed]")
        res = graphmd.check("\n".join(rest[:k] + block + [""] + rest[k:]))
        self.assertIn("section-order", [f["rule"] for f in res["findings"]])
        self.assertEqual(res["exit_code"], 2)

    def test_legacy_core_vinc_tags_still_validate(self):
        """구 2분류([core]/[vinc]) 파일은 사용자 손에 이미 존재한다(GM-3: 산문은
        사람 소유라 재생성으로 안 바뀐다). 파서가 구 어휘를 버리면 그 파일들의
        section-order 검사가 통째로 무너진다 — 하위호환을 고정한다."""
        old = self._built("ko")
        for new, legacy in (("[이 그래프가 정함]", "[core]"), ("[주제마다 다름]", "[core]"),
                            ("[스키마 고정]", "[vinc]")):
            old = old.replace(new, legacy)
        res = graphmd.check(old)
        self.assertEqual(res["exit_code"], 0, res["findings"])

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
            "- about: Episode or Decision to Concept or Decision.",
            "- refutes: Concept to Concept.")
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
        i = lines.index("## Relations [schema-fixed]")
        j = lines.index("## Documents [schema-fixed]")
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
        out = graphmd.build(None, existing="# GRAPH.md\n\n## What this is [this graph decides]\nhi\n")
        self.assertFalse(out["executed"])
        self.assertIn("markers", out["error"])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ValiditySectionTests(unittest.TestCase):
    """유효구간 절은 **모든 그래프**에 나가는 자리에 있어야 한다.

    채택된 시간 정책이 집 관례가 됐으므로 소유자 문서 한 장이 아니라 기본 템플릿에 있다.
    그리고 **선택 절**이어야 한다 — 필수로 넣으면 그날 이전에 만들어진 모든 파일이 BLOCK
    되고(산문은 build 가 안 채운다), 그건 Roles·Topics 때 이미 배운 것이다.

    근거: [[decision:temporal-model-operational-policy-2026-09-09]]
    """

    def test_both_templates_carry_the_section(self):
        for lang, body in (("en", graphmd._TPL_EN), ("ko", graphmd._TPL_KO)):
            self.assertIn("valid_from", body, lang)
            self.assertIn("valid_until", body, lang)

    def test_the_load_bearing_clause_is_stated_not_implied(self):
        """미기재가 current 라는 것을 안 적으면, 읽는 사람은 비워 두면 숨겨진다고 읽는다."""
        self.assertIn("unstated valid_from reads as current", graphmd._TPL_EN)
        self.assertIn("current 로 읽으므로", graphmd._TPL_KO)

    def test_the_section_is_optional_so_older_files_do_not_block(self):
        self.assertIn("validity", graphmd.OPTIONAL_SECTIONS)

    def test_it_has_a_place_in_the_order(self):
        keys = [k for k, _ in graphmd.SECTIONS]
        self.assertIn("validity", keys)
        self.assertLess(keys.index("documents"), keys.index("validity"))

    def test_the_stored_status_enum_is_forbidden_in_the_house_style(self):
        """관례가 이것을 말하지 않으면 다음 사람이 status 열을 만든다."""
        self.assertIn("never store a status of stale, current or new", graphmd._TPL_EN)
        self.assertIn("저장하지 마십시오", graphmd._TPL_KO)


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

    def _put_conventions(self, text="# GRAPH.md\nOptimistic concurrency is an example.\n",
                         owner="user:a", doc_id=None):
        return ds.put_doc(self.A, owner=owner, text=text,
                          doc_id=doc_id or graphmd.conventions_doc_id(owner),
                          title="GRAPH.md", origin=graphmd.CONVENTIONS_ORIGIN)

    def test_hidden_from_the_document_list(self):
        self._put_conventions()
        ds.put_doc(self.A, owner="user:a", text="# Real\nOptimistic concurrency in practice.\n",
                   title="Real")
        ids = [d["doc_id"] for d in ds.list_docs(self.A, owner="user:a")["docs"]]
        self.assertNotIn(graphmd.conventions_doc_id("user:a"), ids)
        self.assertEqual(len(ids), 1)

    def test_the_legacy_fixed_id_is_hidden_too(self):
        """구 고정 id 를 쥔 owner 가 아직 있다 — 판정이 신 id 만 보면 그 사람의
        관례가 자기 자료 목록 사이로 올라온다."""
        self._put_conventions(doc_id=graphmd.LEGACY_CONVENTIONS_DOC_ID)
        ids = [d["doc_id"] for d in ds.list_docs(self.A, owner="user:a")["docs"]]
        self.assertEqual(ids, [])

    def test_a_document_merely_named_like_it_is_not_hidden(self):
        """접두가 같다고 삼키면 사용자가 자기 문서를 목록에서 잃는다."""
        ds.put_doc(self.A, owner="user:a", text="# notes\n본문\n",
                   doc_id="graph.md~notes", title="notes")
        ids = [d["doc_id"] for d in ds.list_docs(self.A, owner="user:a")["docs"]]
        self.assertEqual(ids, ["graph.md~notes"])

    def test_still_reachable_by_exact_id(self):
        """목록에서 감추는 것과 없는 것은 다르다 — 정확한 id 로는 여전히 읽힌다."""
        self._put_conventions()
        got = ds.get_doc(self.A, graphmd.conventions_doc_id("user:a"), owner="user:a")
        self.assertTrue(got.get("executed"))
        self.assertIn("GRAPH.md", got.get("text") or "")

    def test_hidden_from_the_left_tree(self):
        """nav_tree 는 list_docs 를 그대로 쓴다 — 한 자리를 막으면 둘 다 막힌다."""
        from api import query as q
        self._put_conventions()
        tree = q.nav_tree(owner="user:a", store=self.A)
        blob = repr(tree)
        self.assertNotIn(graphmd.conventions_doc_id("user:a"), blob)

    def test_conventions_doc_never_syncs(self):
        conv = graphmd.conventions_doc_id("user:a")
        ds.put_doc(self.A, owner="user:a", text="# GRAPH.md\nrules\n",
                   doc_id=conv, title="Conventions",
                   origin=graphmd.CONVENTIONS_ORIGIN)
        ds.put_doc(self.A, owner="user:a", text="# 보통 문서\n본문\n", title="보통")

        delta = docsync.export_docs(self.A, owner="user:a", since=0)
        ids = [d["doc_id"] for d in delta["docs"]]
        self.assertNotIn(conv, ids)
        self.assertEqual(len(ids), 1)
        # 커서는 건너뛴 문서까지 포함해 전진한다 — 다음 pull 이 되돌아가지 않게.
        self.assertGreaterEqual(delta["cursor"], 2)

        # 송신자가 실어 보내도 받지 않는다(대칭 가드). 구 고정 id 와 owner 파생 id
        # 둘 다 — 한쪽만 막으면 나머지 한쪽이 상대의 관례를 덮어쓴다.
        B = self._open("B")
        try:
            docs = [{"doc_id": d, "title": "theirs", "text": "# theirs\n",
                     "updated_at": "2099", "origin": ""}
                    for d in (graphmd.LEGACY_CONVENTIONS_DOC_ID, conv)]
            res = docsync.import_docs(B, owner="user:b", payload={"docs": docs,
                                                                  "tombstones": []})
            self.assertEqual(res["applied"], 0)
            self.assertEqual(res["skipped"], 2)
            for d in (graphmd.LEGACY_CONVENTIONS_DOC_ID, conv):
                self.assertFalse(ds.get_doc(B, d).get("executed"), d)
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
        # `_chunk_arms` 는 `(rows, vector_note)` 를 준다 — 여기서 보는 것은 rows 다.
        return q._chunk_arms(Stub(), "x", None, top_k, None)[0]

    def test_conventions_chunks_are_dropped(self):
        conv = graphmd.conventions_doc_id("user:a")
        out = self._arms([{"id": "graph.md#00000", "text": "legacy conventions"},
                          {"id": f"{conv}#00000", "text": "conventions"},
                          {"id": "doc-42#00000", "text": "real"}])
        self.assertEqual([r["id"] for r in out], ["doc-42#00000"])

    def test_a_document_merely_named_like_it_survives(self):
        """`graph.md-notes` 도 `graph.md~notes` 도 남의 문서다 — 꼬리가 owner 해시일
        때만 관례다. 접두만 보면 사용자가 검색에서 자기 문서를 잃는다."""
        rows = [{"id": "graph.md-notes#00000", "text": "mine"},
                {"id": "graph.md~notes#00000", "text": "mine too"}]
        out = self._arms(rows)
        self.assertEqual([r["id"] for r in out],
                         ["graph.md-notes#00000", "graph.md~notes#00000"])

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
                   doc_id=graphmd.conventions_doc_id("user:a"), title="GRAPH.md",
                   origin=graphmd.CONVENTIONS_ORIGIN)
        code, out = self._app().read_conventions({"owner": "user:a"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out["source"], "doc")
        self.assertNotIn("missing", out)
        self.assertIn("내 관례", out["text"])

    def test_the_response_names_the_id_the_app_must_write_to(self):
        """앱의 [저장]은 이 값으로 쓴다. 응답이 실제 자리와 다르면 저장은 성공하고
        다음 읽기는 옛 본문을 준다."""
        app = self._app()
        code, out = app.read_conventions({"owner": "user:a"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out["doc_id"], graphmd.conventions_doc_id("user:a"))
        ds.put_doc(self.A, owner="user:a", text="# GRAPH.md\n내 관례\n",
                   doc_id=out["doc_id"], title="GRAPH.md")
        self.assertIn("내 관례", app.read_conventions({"owner": "user:a"})[1]["text"])

    def test_a_second_owner_can_write_its_own_conventions(self):
        """2026-08-05 감사 §3.4 가 P1 로 지목하고 2026-09-14 에 재현된 결함 본체다.
        고정 리터럴 시절에는 먼저 쓴 owner 가 그 한 행을 점유해, 둘째 테넌트는
        읽기·쓰기 양쪽에서 owner mismatch 를 받고 기본 템플릿으로 강등됐다."""
        app = self._app()
        first = app.write_conventions_build({"owner": "user:a"})[1]
        self.assertTrue(first["executed"], first)
        second = app.write_conventions_build({"owner": "team:t1"})
        self.assertEqual(second[0], 200, second[1])
        self.assertTrue(second[1]["doc"]["executed"], second[1]["doc"])
        self.assertNotEqual(graphmd.conventions_doc_id("user:a"),
                            graphmd.conventions_doc_id("team:t1"))
        for owner in ("user:a", "team:t1"):
            got = app.read_conventions({"owner": owner})[1]
            self.assertEqual(got["source"], "doc", owner)

    def test_the_owner_holding_the_legacy_id_keeps_that_seat(self):
        """구 고정 id 에 이미 관례를 써 둔 owner 가 있다. 그 행을 옮기지 않는 것이
        이 변경의 전제다 — 읽기도 쓰기도 그 자리로 가야 관례가 두 벌이 되지 않는다."""
        ds.put_doc(self.A, owner="user:a", text="# GRAPH.md\n구 자리\n",
                   doc_id=graphmd.LEGACY_CONVENTIONS_DOC_ID, title="GRAPH.md",
                   origin=graphmd.CONVENTIONS_ORIGIN)
        app = self._app()
        got = app.read_conventions({"owner": "user:a"})[1]
        self.assertEqual(got["doc_id"], graphmd.LEGACY_CONVENTIONS_DOC_ID)
        self.assertIn("구 자리", got["text"])
        app.write_conventions_build({"owner": "user:a"})
        self.assertFalse(
            ds.get_doc(self.A, graphmd.conventions_doc_id("user:a")).get("executed"),
            "구 자리를 쥔 owner 에게 둘째 사본이 생겼다")

    def test_the_legacy_seat_does_not_block_anybody_else(self):
        """점유자가 있어도 다른 owner 는 자기 관례를 쓴다 — 그것이 이 결함의 닫힘이다."""
        ds.put_doc(self.A, owner="user:a", text="# GRAPH.md\n먼저 쓴 사람\n",
                   doc_id=graphmd.LEGACY_CONVENTIONS_DOC_ID, title="GRAPH.md")
        app = self._app()
        code, res = app.write_conventions_build({"owner": "user:b"})
        self.assertEqual(code, 200, res)
        self.assertTrue(res["doc"]["executed"], res["doc"])
        self.assertNotIn("먼저 쓴 사람", app.read_conventions({"owner": "user:b"})[1]["text"])

    def test_a_raw_put_naming_the_legacy_id_lands_on_the_owners_seat(self):
        """Vinc #342. 점유자가 있을 때 둘째 owner 가 옛 리터럴 `graph.md` 를 **직접** 지목해
        쓴다(MCP `vinc_doc_put` · API). owner mismatch 가 아니라 자기 파생 id 로 들어가야 하고,
        점유자의 행은 그대로여야 한다. 점유자 자신의 같은 쓰기는 제자리로 간다."""
        ds.put_doc(self.A, owner="user:a", text="# GRAPH.md\n점유자\n",
                   doc_id=graphmd.LEGACY_CONVENTIONS_DOC_ID, title="GRAPH.md")
        res = ds.put_doc(self.A, owner="user:b", text="# GRAPH.md\n둘째\n",
                         doc_id=graphmd.LEGACY_CONVENTIONS_DOC_ID, title="GRAPH.md")
        self.assertTrue(res["executed"], res)
        self.assertEqual(res["doc_id"], graphmd.conventions_doc_id("user:b"))
        legacy = ds.get_doc(self.A, graphmd.LEGACY_CONVENTIONS_DOC_ID)
        self.assertIn("점유자", legacy.get("text", "") or "".join(c.get("text", "") for c in legacy.get("chunks", [])), legacy)
        again = ds.put_doc(self.A, owner="user:a", text="# GRAPH.md\n점유자 갱신\n",
                           doc_id=graphmd.LEGACY_CONVENTIONS_DOC_ID, title="GRAPH.md")
        self.assertEqual(again["doc_id"], graphmd.LEGACY_CONVENTIONS_DOC_ID, again)

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
                   doc_id=graphmd.conventions_doc_id("user:a"), title="GRAPH.md")
        self._seed_domains()                       # 그 사이 그래프가 자란다
        code, second = app.write_conventions_build({"owner": "user:a"})
        self.assertEqual(code, 200, second)
        self.assertIn("- My rule.", second["text"])        # 산문 보존
        self.assertIn("research", second["text"])          # 생성 구역은 갱신
        stored = ds.get_doc(self.A, graphmd.conventions_doc_id("user:a"), owner="user:a")
        self.assertEqual(stored.get("text"), second["text"])   # 저장까지 끝난다

    def test_build_route_needs_an_owner(self):
        code, out = self._app().write_conventions_build({})
        self.assertEqual(code, 400)
        self.assertIn("owner", out["error"])

    def test_another_owner_does_not_see_it(self):
        ds.put_doc(self.A, owner="user:a", text="# GRAPH.md\n내 관례\n",
                   doc_id=graphmd.conventions_doc_id("user:a"), title="GRAPH.md")
        code, out = self._app().read_conventions({"owner": "user:b"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out["source"], "default")   # SEC-1 — 남의 관례는 안 보인다
        self.assertNotIn("내 관례", out["text"])


if __name__ == "__main__":
    unittest.main()
