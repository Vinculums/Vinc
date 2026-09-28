# -*- coding: utf-8 -*-
"""역할 프롬프트의 focus 재료는 **반쪽 검색을 반쪽이라고 말해야 한다**.

이 블록은 에이전트 맥락에 그대로 들어가는데, 강등된 검색과 온전한 검색은 결과만 보면
구분이 안 된다. 아무 말도 안 하면 "그래프에 이것뿐"으로 읽히고, 그건 없는 것을 없다고
답하는 것과 다르다.

근거: [[vinc.plan.encode-cold-start-declaration §3]]
"""
from __future__ import annotations

import unittest

from vinc_mcp import roles


class _Bridge:
    def __init__(self, res):
        self.res = res

    def request(self, method, path, params=None):
        return self.res


_ROWS = {"node_hits": [{"id": "concept:a", "title": "A"}], "rows": []}


def _block(extra):
    return roles._focus_block("some topic", "tenant:local", _Bridge({**_ROWS, **extra}))


class FocusMaterialLabels(unittest.TestCase):
    def test_a_full_search_says_nothing_extra(self):
        self.assertEqual(_block({"arms": "fts+vector"}), "- concept:a: A")

    def test_a_failed_semantic_arm_is_written_into_the_material(self):
        out = _block({"arms": "fts", "vector_note": "encode timed out"})
        self.assertIn("- concept:a: A", out)
        self.assertIn("semantic arm did not run", out)
        # 문장 자체를 옮기지 않는다 — 판단은 구조화 필드로만 한다.
        self.assertNotIn("encode timed out", out)

    def test_a_rebuild_is_labelled_benign_and_separately(self):
        out = _block({"arms": "fts", "vector_rebuilding": True,
                      "vector_note": "index rebuilding"})
        self.assertIn("rebuilding after a write", out)
        self.assertNotIn("did not run", out, "양성 재빌드를 실패로 말했다")

    def test_a_runtime_warning_does_not_claim_the_semantic_arm_was_absent(self):
        out = _block({"arms": "fts+vector", "vector_note": "runtime mismatch",
                      "encoder_runtime_warning": True})
        self.assertIn("ranking can be inaccurate", out)
        self.assertNotIn("semantic arm did not run", out)

    def test_a_model_migration_says_how_much_is_left(self):
        out = _block({"arms": "fts", "vector_note": "migrating",
                      "embed_migration": {"pending": 42}})
        self.assertIn("42 documents left", out)

    def test_an_empty_result_keeps_its_own_sentence(self):
        out = roles._focus_block("nothing", "tenant:local",
                                 _Bridge({"node_hits": [], "rows": [],
                                          "vector_note": "cold"}))
        self.assertIn("nothing in this graph matched", out)


if __name__ == "__main__":
    unittest.main()
