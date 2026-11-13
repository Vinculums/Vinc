# -*- coding: utf-8 -*-
"""문서 보조 함수 다섯 결함의 회귀(RAG 코드 리뷰 RV-15 · 26 · 27 · 28 · 29).

- RV-15: 밑줄·목차·청크 착지 오프셋이 편집기 단위(UTF-16, CRLF 한 칸)로 나간다.
- RV-26: max_chars 가 0 이하여도 분할이 끝난다.
- RV-27: 빈 제목(`#` 한 줄)이 다음 줄을 제목으로 삼지 않는다.
- RV-28: 하이라이트 토큰은 해시 순서가 아니라 제목 앞에서부터 여섯이다.
- RV-29: 근거 백필이 채우지 못한 엣지를 넘어 커서로 이어 훑고, CJK 문장을 나눈다.

결함마다 실제 ladybug 스토어에서 도는 회귀가 하나씩 있다(`EngineTests`).
근거: [[vinc.plan.rag-code-review-fixes-2026-09-29]]
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore as ds  # noqa: E402
from api import fragment  # noqa: E402
from store.embedded_backend import import_driver  # noqa: E402

_DRIVER, _NAME = import_driver()
OWNER = "user:a"


def _cm(text: str, i: int) -> int:
    """CodeMirror 가 세는 오프셋(독립 기준): CRLF 를 한 칸으로, UTF-16 단위로."""
    return len(text[:i].replace("\r\n", "\n").encode("utf-16-le")) // 2


def _cm_slice(text: str, start: int, end: int | None = None) -> str:
    """CodeMirror 가 ``[start, end)`` 에서 보는 글자(app/src/codemirror-editor.ts 가
    서버 오프셋을 그대로 넣는다)."""
    u = text.replace("\r\n", "\n").encode("utf-16-le")
    return u[2 * start:None if end is None else 2 * end].decode("utf-16-le", errors="replace")


class PureHelperTests(unittest.TestCase):
    def test_rv15_editor_offsets_match_codemirror_everywhere_but_inside_a_crlf(self):
        text = "😀a\r\nb\rc😀\r\n한글 끝"
        at = ds._editor_offsets(text, range(len(text) + 1))
        for i in range(len(text) + 1):
            if text[i - 1:i + 1] == "\r\n":
                continue                    # CR 과 LF 사이는 편집기에 없는 자리다
            self.assertEqual(at[i], _cm(text, i), i)
        self.assertEqual(ds._editor_offsets(text, [-3, 999]), {-3: 0, 999: _cm(text, len(text))})

    def test_rv26_non_positive_max_chars_still_terminates(self):
        self.assertEqual(ds.plan_boundaries("abc", max_chars=0), [0, 1, 2])
        self.assertEqual(ds.plan_boundaries("abc", max_chars=-5), [0, 1, 2])

    def test_rv27_empty_heading_does_not_take_the_next_line(self):
        self.assertEqual(ds._section_title("#\nfoo bar"), "")
        self.assertEqual(ds._section_title("text\n#   \nnext"), "")
        self.assertEqual(ds._section_title("# Title\r\nbody"), "Title")

    def test_rv28_highlight_terms_are_the_first_six_title_tokens(self):
        title = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
        self.assertEqual(ds._concept_terms(title, ""),
                         {"alpha", "beta", "gamma", "delta", "epsilon", "zeta"})

    def test_rv29_cjk_terminators_end_a_sentence(self):
        self.assertEqual(ds._sentences("一つ目の文。二つ目の文！三つ目？"),
                         ["一つ目の文。", "二つ目の文！", "三つ目？"])
        self.assertEqual(ds._sentences("pi is 3.14 here. next"), ["pi is 3.14 here.", "next"])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class EngineTests(unittest.TestCase):
    def setUp(self):
        from store.backend import open_store
        from store.config import load_config
        self._tmp = tempfile.mkdtemp()
        self.store = open_store(load_config({"data_dir": os.path.join(self._tmp, "store")}))
        self.store.provision(dim=4)

    def tearDown(self):
        self.store.close()

    def _merge(self, nodes, edges=()):
        res = fragment.merge_fragment({"owner": OWNER, "nodes": list(nodes),
                                       "edges": list(edges)}, store=self.store)
        self.assertFalse(res.get("partial"), res)

    def _put(self, text: str, **kw) -> str:
        res = ds.put_doc(self.store, owner=OWNER, text=text, title="t", **kw)
        self.assertTrue(res.get("executed"), res)
        return res["doc_id"]

    # RV-15 ───────────────────────────────────────────────────────────────────
    def test_rv15_highlight_ranges_land_on_the_word_in_the_editor(self):
        text = "😀😀 vinc\r\nsecond vinc\r\n\r\n## 😀 part\r\nthird vinc 😀\r\n"
        did = self._put(text)
        self._merge([{"id": "concept:vinc", "type": "concept", "title": "vinc"}])
        hl = ds.doc_highlights(self.store, did, "concept:vinc", owner=OWNER)
        self.assertTrue(hl["executed"], hl)
        self.assertEqual([_cm_slice(text, r["char_start"], r["char_end"]) for r in hl["ranges"]],
                         ["vinc", "vinc", "vinc"])

    def test_rv15_outline_and_chunk_offsets_land_on_their_lines_in_the_editor(self):
        text = ("😀 intro\r\n\r\n## 😀😀 first\r\nbody one\r\n\r\n"
                "## second\r\nbody two 😀\r\n\r\n## third\r\nend\r\n")
        starts = [0] + [i for i in range(len(text)) if text.startswith("## ", i)]
        did = self._put(text, boundaries=starts)
        ctx = ds.doc_context(self.store, did, owner=OWNER)
        self.assertTrue(ctx["executed"], ctx)
        self.assertEqual([s["title"] for s in ctx["sections"]], ["😀😀 first", "second", "third"])
        for s in ctx["sections"]:
            self.assertTrue(_cm_slice(text, s["char_start"]).startswith("## "), s)
        self.assertEqual(len(ctx["chunks"]), 4)
        for c in ctx["chunks"][1:]:
            self.assertTrue(_cm_slice(text, c["char_start"]).startswith("## "), c)
        # 저장값은 코드포인트 그대로다 — 연속성 검증이 서야 한다.
        self.assertTrue(ds.get_doc(self.store, did, owner=OWNER)["verified"])

    # RV-26 ───────────────────────────────────────────────────────────────────
    def test_rv26_put_doc_with_zero_max_chars_finishes(self):
        res = ds.put_doc(self.store, owner=OWNER, text="abc", title="t", max_chars=0)
        self.assertEqual(res.get("chunks"), 3, res)
        got = ds.get_doc(self.store, res["doc_id"], owner=OWNER)
        self.assertEqual((got["text"], got["verified"]), ("abc", True))

    # RV-27 ───────────────────────────────────────────────────────────────────
    def test_rv27_stored_section_title_is_not_the_line_after_an_empty_heading(self):
        def title_of(text: str) -> str:
            did = self._put(text)
            rows = self.store.run("MATCH (c:Chunk) WHERE c.doc_id = $d "
                                  "RETURN c.section_title AS s", {"d": did})
            return rows[0]["s"]
        self.assertEqual(title_of("#\nfoo bar\n"), "")
        self.assertEqual(title_of("text\n#   \nnext\n"), "")
        self.assertEqual(title_of("intro\n## Real title\nbody\n"), "Real title")

    # RV-28 ───────────────────────────────────────────────────────────────────
    def test_rv28_doc_highlights_use_the_first_six_title_tokens(self):
        words = ["one", "two", "three", "four", "five", "six",
                 "seven", "eight", "nine", "ten", "eleven", "twelve"]
        self._merge([{"id": "concept:long", "type": "concept", "title": " ".join(words)}])
        did = self._put(" ".join(words[6:]) + "\n")
        hl = ds.doc_highlights(self.store, did, "concept:long", owner=OWNER)
        self.assertEqual(hl["terms"], sorted(words[:6]))
        self.assertEqual(hl["ranges"], [])      # 링커가 안 보는 7번째 이후 낱말은 밑줄도 없다

    # RV-29 ───────────────────────────────────────────────────────────────────
    def test_rv29_backfill_pages_past_edges_it_cannot_fill(self):
        titles = {"concept:a1": "apple", "concept:a2": "banana", "concept:a3": "cherry",
                  "concept:a4": "damson", "concept:z1": "zeta", "concept:z2": "theta"}
        self._merge([{"id": i, "type": "concept", "title": t} for i, t in titles.items()],
                    [{"from": "concept:a1", "to": "concept:a2", "type": "relates_to"},  # 못 채운다
                     {"from": "concept:a3", "to": "concept:a4", "type": "relates_to"},
                     {"from": "concept:z1", "to": "concept:z2", "type": "relates_to"}])  # 채운다
        self._put("zeta works with theta.\n")
        out = ds.backfill_evidence(self.store, owner=OWNER, limit=2)
        self.assertEqual((out["scanned"], out["filled"], out["has_more"]), (2, 0, True), out)
        nxt = ds.backfill_evidence(self.store, owner=OWNER, limit=2, cursor=out["next_cursor"])
        self.assertEqual((nxt["scanned"], nxt["filled"], nxt["has_more"], nxt["next_cursor"]),
                         (1, 1, False, None), nxt)
        rows = self.store.run("MATCH (a:Concept)-[r]->(b:Concept) WHERE r.evidence <> '' "
                              "RETURN r.evidence AS ev")
        self.assertEqual([r["ev"] for r in rows], ["zeta works with theta."])
        # 깨진 커서 · 다른 범위(문서 스코프)의 커서는 조용히 첫 쪽으로 돌지 않고 거절된다.
        for bad in ("not-a-cursor", out["next_cursor"]):
            res = ds.backfill_evidence(self.store, owner=OWNER, cursor=bad,
                                       doc_id=None if bad == "not-a-cursor" else "other")
            self.assertFalse(res["executed"], res)
            self.assertTrue(res["error"].startswith("invalid_cursor"), res)
        # 라우트도 검색처럼 400 으로 거절한다(200 이면 호출자가 첫 쪽부터 다시 돈다).
        from api.server import VincApp
        app = VincApp(store=self.store)
        self.assertEqual(app.write_evidence_backfill({"owner": OWNER, "cursor": "x"})[0], 400)
        self.assertEqual(app.write_evidence_backfill({"owner": OWNER})[0], 200)

    def test_rv29_japanese_sentences_are_not_one_sentence(self):
        self._merge([{"id": "concept:alpha", "type": "concept", "title": "alpha"},
                     {"id": "concept:beta", "type": "concept", "title": "beta"}],
                    [{"from": "concept:alpha", "to": "concept:beta", "type": "relates_to"}])
        self._put("alphaは速い。これは別の文だ。betaは遅い。\n")
        out = ds.backfill_evidence(self.store, owner=OWNER)
        self.assertEqual((out["scanned"], out["filled"]), (1, 0), out)  # 같은 문단일 뿐 같은 문장이 아니다


if __name__ == "__main__":
    unittest.main()
