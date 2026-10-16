# -*- coding: utf-8 -*-
"""Engine tests: importability (no heavy deps) + pure parsing helpers."""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ENGINES = ["recall_ingest", "mentions_link"]
# skill_tracker/concepts_ingest/bridges_ingest/learner_ingest/pack_ingest removed
# 2026-08-12 — v1(weg-9000, Neo4j) migration tooling with zero production callers
# (agent-wiki-comparison §8; record:fact-orphan-engines-deleted).
# graph_to_neo4j 제외: PM 정책 그래프 — Vinc(메모리 데이터플레인) 범위 밖
# embed_pipeline(weg9000-hub 시절 배치 CLI) 은 2026-10-16 삭제(VIN-553).


class ImportTests(unittest.TestCase):
    def test_all_engines_import_without_heavy_deps(self):
        # neo4j / chonkie / voyageai are NOT installed in the test env; modules
        # must still import.
        for name in ENGINES:
            mod = importlib.import_module(f"engines.{name}")
            self.assertTrue(hasattr(mod, "main"), f"{name} has no main()")


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


class MentionsLinkTests(unittest.TestCase):
    """E-G1 compute_links — 결정론 Chunk↔Concept 링크 규칙(순수)."""

    def setUp(self):
        self.m = importlib.import_module("engines.mentions_link")

    def test_source_basename_matches_when_the_chunk_does_not_state_the_title(self):
        # source_file basename 이 같으면 method='source'(절대 vs 상대경로 무관).
        # 단 이 청크는 개념의 제목("쌍대성")을 **말하지 않는다** — 말하면 아래 테스트다.
        chunks = [{"chunk_id": "ch1", "source_file": "/abs/path/03-duality.md",
                   "text": "무관한 본문"}]
        concepts = [{"concept_id": "c1", "title": "쌍대성", "stem": "03-duality",
                     "source_file": "math/topo/03-duality.md"}]
        links = self.m.compute_links(chunks, concepts)
        self.assertEqual(links, [{"chunk_id": "ch1", "concept_id": "c1",
                                  "method": "source"}])

    def test_title_wins_over_shared_source_file(self):
        # 출처도 같고 제목도 말하는 청크는 'title' 이다. 순서가 뒤집혀 있던 동안에는
        # 이런 쌍이 전부 'source' 로 찍혀, 한 문서에서 통째로 뽑힌 개념은 발췌 창에
        # 올릴 것이 하나도 없었다. 실측·이유는 engines/mentions_link.py 모듈 docstring.
        chunks = [{"chunk_id": "ch1", "source_file": "/abs/path/03-duality.md",
                   "text": "이 절은 쌍대성 을 정의한다"}]
        concepts = [{"concept_id": "c1", "title": "쌍대성", "stem": "03-duality",
                     "source_file": "math/topo/03-duality.md"}]
        links = self.m.compute_links(chunks, concepts)
        self.assertEqual(links, [{"chunk_id": "ch1", "concept_id": "c1",
                                  "method": "title"}])

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

    def test_title_tokens_scattered_beyond_window_no_match(self):
        # 제목 토큰이 전부 있어도 창(_TITLE_WINDOW) 밖에 흩어져 있으면 매치 안 함.
        # 2026-09-05 실측: "Vinc Design Hub" 가 vinc·design·hub 를 문단마다 하나씩만
        # 담은 2000자 청크에 걸려 디자인과 무관한 문서 8건의 대표 개념이 됐다.
        filler = " ".join(f"w{i}" for i in range(20))
        chunks = [{"chunk_id": "ch1", "source_file": "/a/x.md",
                   "text": f"vinc {filler} design {filler} hub"}]
        concepts = [{"concept_id": "c1", "title": "Vinc Design Hub", "stem": "z",
                     "source_file": "b/y.md"}]
        self.assertEqual(self.m.compute_links(chunks, concepts), [])

    def test_title_tokens_reordered_within_window_match(self):
        # 어순이 달라도 창 안에 모여 있으면 매치 — 인용은 대개 바꿔 말한다
        chunks = [{"chunk_id": "ch1", "source_file": "/a/x.md",
                   "text": "the design hub inside vinc holds the token values"}]
        concepts = [{"concept_id": "c1", "title": "Vinc Design Hub", "stem": "z",
                     "source_file": "b/y.md"}]
        self.assertEqual([(l["concept_id"], l["method"])
                          for l in self.m.compute_links(chunks, concepts)],
                         [("c1", "title")])

    def test_title_window_is_anywhere_in_the_chunk(self):
        # 앞쪽엔 흩어져 있고 뒤쪽에 모여 있으면 매치 — 창은 "어딘가 한 번"이다
        filler = " ".join(f"w{i}" for i in range(20))
        chunks = [{"chunk_id": "ch1", "source_file": "/a/x.md",
                   "text": f"vinc {filler} design {filler} hub {filler} vinc design hub"}]
        concepts = [{"concept_id": "c1", "title": "Vinc Design Hub", "stem": "z",
                     "source_file": "b/y.md"}]
        self.assertEqual(len(self.m.compute_links(chunks, concepts)), 1)

    def test_single_token_title_has_no_window(self):
        filler = " ".join(f"w{i}" for i in range(20))
        chunks = [{"chunk_id": "ch1", "source_file": "/a/x.md",
                   "text": f"alpha {filler} beta"}]
        concepts = [{"concept_id": "c1", "title": "alpha", "stem": "z", "source_file": ""}]
        self.assertEqual(len(self.m.compute_links(chunks, concepts)), 1)

    def test_window_boundary_is_exact(self):
        # 두 토큰이 정확히 창 폭만큼 떨어지면 안 걸리고, 하나 덜 떨어지면 걸린다
        W = self.m._TITLE_WINDOW
        concepts = [{"concept_id": "c1", "title": "alpha beta", "stem": "z", "source_file": ""}]
        far = "alpha " + " ".join(f"w{i}" for i in range(W - 1)) + " beta"     # 거리 W
        near = "alpha " + " ".join(f"w{i}" for i in range(W - 2)) + " beta"    # 거리 W-1
        self.assertEqual(self.m.compute_links(
            [{"chunk_id": "ch1", "source_file": "", "text": far}], concepts), [])
        self.assertEqual(len(self.m.compute_links(
            [{"chunk_id": "ch1", "source_file": "", "text": near}], concepts)), 1)

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
