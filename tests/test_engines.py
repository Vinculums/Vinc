# -*- coding: utf-8 -*-
"""Engine tests: importability (no heavy deps) + pure parsing helpers."""
from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ENGINES = [
    "embed_pipeline", "recall_ingest", "pack_ingest",
    "skill_tracker", "concepts_ingest", "bridges_ingest", "mentions_link",
]  # graph_to_neo4j 제외: PM 정책 그래프 — Vinc(메모리 데이터플레인) 범위 밖


class ImportTests(unittest.TestCase):
    def test_all_engines_import_without_heavy_deps(self):
        # neo4j / chonkie / voyageai are NOT installed in the test env; modules
        # must still import (lazy loading is the F-fix for embed_pipeline).
        for name in ENGINES:
            mod = importlib.import_module(f"engines.{name}")
            self.assertTrue(hasattr(mod, "main"), f"{name} has no main()")

    def test_embed_pipeline_chonkie_is_lazy(self):
        mod = importlib.import_module("engines.embed_pipeline")
        # symbols are unbound until _load_chonkie runs
        self.assertIsNone(mod.RecursiveChunker)
        self.assertTrue(hasattr(mod, "_load_chonkie"))


class LibraryNeverExitsTests(unittest.TestCase):
    """**라이브러리 경로는 프로세스의 생사를 결정하지 않는다**(2026-07-31 실측 사고).

    `sys.exit()` 가 올리는 `SystemExit` 은 `Exception` 이 아니라 `BaseException` 이라
    `except Exception` 을 전부 통과한다. 사이드카가 이 모듈을 **잡 워커 스레드에서**
    부르는데(`POST /doc/reembed`), 그게 워커를 죽여 쓰기 큐 전체가 영구 무응답이
    됐다. 종료는 `main()` 만 결정한다."""

    def setUp(self):
        self.e = importlib.import_module("engines.embed_pipeline")

    def test_missing_stack_raises_ordinary_exception(self):
        # 이 테스트 환경엔 chonkie/sentence-transformers 가 없다(위 ImportTests 전제).
        with self.assertRaises(self.e.EmbeddingStackMissing) as ctx:
            self.e.build_embeddings_model("local-en")
        exc = ctx.exception
        self.assertIsInstance(exc, Exception,
                              "라이브러리가 Exception 이 아닌 것을 던지면 서버가 죽는다")
        self.assertNotIsInstance(exc, SystemExit)

    def test_load_chonkie_raises_instead_of_exiting(self):
        with self.assertRaises(self.e.EmbeddingStackMissing) as ctx:
            self.e._load_chonkie()
        self.assertIsInstance(ctx.exception, Exception)

    def test_no_sys_exit_outside_main(self):
        """소스 수준 가드 — `main()` 밖에서 sys.exit 이 다시 새어 들어오지 않게.

        문자열 검색이 아니라 **AST 로 실제 호출만** 센다: 주석·독스트링에 적힌
        `sys.exit()` 이라는 낱말까지 잡으면 이 규율을 설명하는 문장조차 못 쓴다."""
        import ast
        tree = ast.parse(Path(self.e.__file__).read_text(encoding="utf-8"))
        main_fn = next((n for n in tree.body
                        if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
        self.assertIsNotNone(main_fn, "main() 이 사라졌다 — 가드 전제가 깨졌다")
        lo, hi = main_fn.lineno, (main_fn.end_lineno or main_fn.lineno)

        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            is_exit = (isinstance(f, ast.Attribute) and f.attr == "exit"
                       and isinstance(f.value, ast.Name) and f.value.id == "sys")
            if is_exit and not (lo <= node.lineno <= hi):
                offenders.append(node.lineno)
        self.assertEqual(offenders, [],
                         f"main() 밖 sys.exit 호출(줄 {offenders}) — 잡 워커를 죽인다")


class EmbedModelVocabTests(unittest.TestCase):
    """embed_pipeline 의 --model 어휘·차원은 store.config(§8) 단일 출처를 따른다.
    실제 임베딩 로드(HF 다운로드) 없이 매핑만 검증(오프라인 CI)."""

    def setUp(self):
        self.e = importlib.import_module("engines.embed_pipeline")
        from store.config import DEFAULT_EMBEDDING_MODEL, EMBEDDING_DIM_BY_MODEL
        self.default = DEFAULT_EMBEDDING_MODEL
        self.dims = EMBEDDING_DIM_BY_MODEL

    def test_cli_default_is_offline_english(self):
        # F5·§8: 키 없이 기동해야 하므로 기본은 오프라인 영어 모델(local-en) — anthropic 아님.
        ns = self.e.build_parser().parse_args(["--product", "x"])
        self.assertEqual(ns.model, self.default)
        self.assertEqual(ns.model, "local-en")

    def test_cli_choices_match_config_vocab(self):
        choices = self.e.build_parser()._option_string_actions["--model"].choices
        self.assertEqual(set(choices), set(self.dims))  # 파이프라인 독자 명칭 금지

    def test_model_dim_delegates_to_config(self):
        for key, dim in self.dims.items():
            self.assertEqual(self.e.model_dim(key), dim)

    def test_local_en_maps_to_bge_english(self):
        # §8 영어 1차: local-en=bge-base-en(768), local-en-large=bge-large-en(1024).
        self.assertEqual(self.e.ST_MODEL_BY_KEY["local-en"], "BAAI/bge-base-en-v1.5")
        self.assertEqual(self.e.ST_MODEL_BY_KEY["local-en-large"], "BAAI/bge-large-en-v1.5")


class HubRootResolutionTests(unittest.TestCase):
    """Portability: concepts/bridges must resolve hub-root from arg/env/CWD,
    not a hardcoded v1 monorepo path (regression for the extraction bug)."""

    def test_concepts_set_hub_repoints_learning(self):
        c = importlib.import_module("engines.concepts_ingest")
        with tempfile.TemporaryDirectory() as d:
            c._set_hub(d)
            self.assertEqual(c.HUB, Path(d).resolve())
            self.assertEqual(c.LEARNING, Path(d).resolve() / "WORKSPACES" / "learning")

    def test_bridges_set_hub_rebuilds_bridge_dirs(self):
        b = importlib.import_module("engines.bridges_ingest")
        with tempfile.TemporaryDirectory() as d:
            b._set_hub(d)
            self.assertEqual(b.SOURCE_DIR,
                             Path(d).resolve() / "WORKSPACES" / "learning" / b.SOURCE_DOMAIN)
            # BRIDGE_DIRS rebuilt under the new hub (empty when no *-bridges dirs exist)
            self.assertTrue(all(str(d) in str(bd[0]) for bd in b.BRIDGE_DIRS))

    def test_default_hub_falls_back_to_cwd(self):
        c = importlib.import_module("engines.concepts_ingest")
        # with no env and no legacy hub, default resolves to a real Path (not crash)
        self.assertIsInstance(c._default_hub(), Path)



class RecallParseTests(unittest.TestCase):
    def test_primary_domain_normalizes_list_form(self):
        r = importlib.import_module("engines.recall_ingest")
        self.assertEqual(r._primary_domain("art"), "art")
        self.assertEqual(r._primary_domain("[math, art]"), "math")  # real hub sessions use this
        self.assertEqual(r._primary_domain(""), "")

    def test_primary_domain_extracts_from_path_workspace(self):
        # 구세대 physics 세션의 서술형 workspace — learning/<seg> 추출 (도메인 불문)
        r = importlib.import_module("engines.recall_ingest")
        self.assertEqual(r._primary_domain("WORKSPACES/learning/physics/modern + CONTEXT/identity"), "physics")
        self.assertEqual(r._primary_domain("WORKSPACES/learning/pottery/intro"), "pottery")
        # learning 세그먼트 없는 서술형은 도메인이 아님
        self.assertEqual(r._primary_domain("CONTEXT/identity + audit"), "")

    def test_parse_frontmatter(self):
        r = importlib.import_module("engines.recall_ingest")
        text = "---\nsession_id: s1\ntitle: Hello\nworkspace: planning\n---\n## 세션 요약\nbody\n"
        fm = r._parse_frontmatter(text)
        self.assertEqual(fm.get("session_id"), "s1")
        self.assertEqual(fm.get("title"), "Hello")

    def test_parse_body_sections(self):
        r = importlib.import_module("engines.recall_ingest")
        text = "## 세션 요약\nThis is the summary.\n\n## 완료 항목\n- done\n"
        sections = r._parse_body_sections(text)
        self.assertTrue(any("summary" in v.lower() or "요약" in k for k, v in sections.items()))

    def test_parse_body_sections_captures_h3(self):
        # 실데이터·reflect-session 템플릿은 ### (h3) 를 쓴다 — h2 만 잡으면 섹션이
        # 전부 공백이 됐다(P1 포트 발견). h2·h3 모두 캡처하며 h2 회귀도 유지.
        r = importlib.import_module("engines.recall_ingest")
        text = "### 세션 요약\n한 일\n\n### 완료 항목\n- a\n- b\n"
        sections = r._parse_body_sections(text)
        self.assertIn("완료 항목", sections)
        self.assertIn("세션 요약", sections)

    def test_build_episode_completed_items_and_patterns_are_lists(self):
        # completed_items·patterns 는 STRING[] (목록). completed 는 BOOL 이 아니다.
        import tempfile
        from datetime import date as _date
        r = importlib.import_module("engines.recall_ingest")
        md = ("---\nowner: user:x\ndomain: math\n---\n"
              "### 완료 항목\n- 노트 작성\n- index 갱신\n"
              "### 발견된 패턴 후보\n- 묶음 패턴\n")
        d = Path(tempfile.mkdtemp()) / "session-01.md"
        d.write_text(md, encoding="utf-8")
        ep = r.build_episode(d, _date(2026, 7, 1))
        self.assertEqual(ep["completed_items"], ["노트 작성", "index 갱신"])
        self.assertEqual(ep["patterns"], ["묶음 패턴"])
        self.assertNotIn("completed", ep)   # BOOL 필드 미신설


class ConceptsParseTests(unittest.TestCase):
    def test_h1_and_oneliner(self):
        c = importlib.import_module("engines.concepts_ingest")
        text = "# Entropy\n\n## 한 줄 핵심\n무질서도의 척도.\n"
        self.assertEqual(c._h1(text), "Entropy")
        self.assertIn("무질서", c._one_liner(text))


class WikilinkEdgeTests(unittest.TestCase):
    """F6 — 위키링크 보수: 방향은 frontmatter 명시만, 추론은 RELATED_TO 정준화."""

    @staticmethod
    def _concept(cid, stem, domain, text="", fm=None):
        return {"concept_id": cid, "stem": stem, "domain": domain,
                "_text": text, "_fm": fm or {}}

    def _extract(self, concepts):
        c = importlib.import_module("engines.concepts_ingest")
        return c.extract_edges(concepts)

    def test_keyword_lines_demoted_to_related(self):
        # 구 휴리스틱이 PREREQUISITE_OF 로 오분류하던 "다음" 일상어 → RELATED_TO.
        concepts = [
            self._concept("m-a", "01-a", "math", text="다음 주제는 [[01-b]] 를 본다.\n"),
            self._concept("m-b", "01-b", "math"),
        ]
        edges = self._extract(concepts)
        self.assertEqual(edges, [("m-a", "RELATED_TO", "m-b")])

    def test_frontmatter_declares_directed_edges(self):
        concepts = [
            self._concept("m-a", "01-a", "math",
                          fm={"prerequisite_of": ["01-b"], "derives_from": "01-c"}),
            self._concept("m-b", "01-b", "math"),
            self._concept("m-c", "01-c", "math"),
        ]
        edges = set(self._extract(concepts))
        self.assertIn(("m-a", "PREREQUISITE_OF", "m-b"), edges)
        self.assertIn(("m-a", "DERIVES_FROM", "m-c"), edges)   # 스칼라도 허용

    def test_mutual_wikilinks_collapse_to_one_related(self):
        # A↔B 상호 링크 → 정준화(min,max) 1개 — degree 부풀림 방지.
        concepts = [
            self._concept("m-a", "01-a", "math", text="[[01-b]]\n"),
            self._concept("m-b", "01-b", "math", text="[[01-a]]\n"),
        ]
        edges = self._extract(concepts)
        self.assertEqual(edges, [("m-a", "RELATED_TO", "m-b")])

    def test_related_suppressed_when_directed_exists(self):
        # 같은 무순서쌍에 방향 엣지가 있으면 RELATED_TO 억제(이중 계상 방지).
        concepts = [
            self._concept("m-a", "01-a", "math", text="[[01-b]]\n",
                          fm={"prerequisite_of": ["01-b"]}),
            self._concept("m-b", "01-b", "math"),
        ]
        edges = self._extract(concepts)
        self.assertEqual(edges, [("m-a", "PREREQUISITE_OF", "m-b")])


class OpenDomainTests(unittest.TestCase):
    """Domains must be discovered from the filesystem, not a hardcoded list."""

    def test_concepts_has_no_hardcoded_domain_list(self):
        c = importlib.import_module("engines.concepts_ingest")
        self.assertFalse(hasattr(c, "DOMAINS"))  # the fixed list is gone

    def test_concepts_discovers_arbitrary_domains(self):
        c = importlib.import_module("engines.concepts_ingest")
        with tempfile.TemporaryDirectory() as d:
            learning = Path(d) / "WORKSPACES" / "learning"
            # a domain nobody hardcoded — proves it's open
            for dom in ("pottery", "linguistics", "physics"):
                (learning / dom).mkdir(parents=True)
            found = c.discover_domains(learning)
            self.assertEqual(found, ["linguistics", "physics", "pottery"])

    def test_bridges_source_domain_is_configurable(self):
        b = importlib.import_module("engines.bridges_ingest")
        # default is math (back-compat) but it's env-driven, not forced
        self.assertIsInstance(b.SOURCE_DOMAIN, str)

    def test_bridges_collect_inputs_is_domain_agnostic(self):
        b = importlib.import_module("engines.bridges_ingest")
        fm = {"chemistry_inputs": ["01-bonds"], "biology_inputs": ["02-cells"], "title": "x"}
        inputs = b._collect_inputs(fm)
        self.assertEqual(inputs, {"chemistry": ["01-bonds"], "biology": ["02-cells"]})


class MentionsLinkTests(unittest.TestCase):
    """E-G1 compute_links — 결정론 Chunk↔Concept 링크 규칙(순수)."""

    def setUp(self):
        self.m = importlib.import_module("engines.mentions_link")

    def test_source_basename_match_is_strongest(self):
        # source_file basename 이 같으면 method='source'(절대 vs 상대경로 무관)
        chunks = [{"chunk_id": "ch1", "source_file": "/abs/path/03-duality.md",
                   "text": "무관한 본문"}]
        concepts = [{"concept_id": "c1", "title": "쌍대성", "stem": "03-duality",
                     "source_file": "math/topo/03-duality.md"}]
        links = self.m.compute_links(chunks, concepts)
        self.assertEqual(links, [{"chunk_id": "ch1", "concept_id": "c1",
                                  "method": "source"}])

    def test_title_all_tokens_present(self):
        # title 의 유의 토큰이 전부 chunk 에 있으면 method='title'
        chunks = [{"chunk_id": "ch1", "source_file": "/a/x.md",
                   "text": "이 문단은 gauge theory 와 duality 를 다룬다"}]
        concepts = [{"concept_id": "c1", "title": "gauge duality", "stem": "z",
                     "source_file": "b/y.md"}]
        links = self.m.compute_links(chunks, concepts)
        self.assertEqual([(l["concept_id"], l["method"]) for l in links],
                         [("c1", "title")])

    def test_title_partial_tokens_no_match(self):
        # title 토큰이 일부만 있으면 매치 안 함(AND 규칙 — 노이즈 억제)
        chunks = [{"chunk_id": "ch1", "source_file": "/a/x.md",
                   "text": "gauge 만 언급하고 나머지는 없음"}]
        concepts = [{"concept_id": "c1", "title": "gauge duality symmetry",
                     "stem": "z", "source_file": "b/y.md"}]
        self.assertEqual(self.m.compute_links(chunks, concepts), [])

    def test_stem_fallback(self):
        chunks = [{"chunk_id": "ch1", "source_file": "/a/x.md",
                   "text": "본문에 identifier renorm 가 토큰으로 등장"}]
        concepts = [{"concept_id": "c1", "title": "관측 불가 제목zzz",
                     "stem": "renorm", "source_file": "b/y.md"}]
        links = self.m.compute_links(chunks, concepts)
        self.assertEqual([(l["concept_id"], l["method"]) for l in links],
                         [("c1", "stem")])

    def test_deterministic_sorted_output(self):
        chunks = [{"chunk_id": "ch2", "source_file": "/a/b.md", "text": "alpha beta"},
                  {"chunk_id": "ch1", "source_file": "/a/b.md", "text": "alpha beta"}]
        concepts = [{"concept_id": "cB", "title": "alpha", "stem": "s1", "source_file": ""},
                    {"concept_id": "cA", "title": "beta", "stem": "s2", "source_file": ""}]
        links = self.m.compute_links(chunks, concepts)
        # (chunk_id, concept_id) 정렬 — 결정론
        keys = [(l["chunk_id"], l["concept_id"]) for l in links]
        self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main()
