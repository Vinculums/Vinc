# -*- coding: utf-8 -*-
"""Coverage recall 도구의 **지표가 맞는가** — rag_eval/coverage_recall.py.
test_eval_search.py 와 같은 규율: 손으로 계산한 값과 대조해 못박는다."""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

_SRC = pathlib.Path(__file__).resolve().parents[2] / "rag_eval" / "coverage_recall.py"


def _load():
    spec = importlib.util.spec_from_file_location("_vinc_eval_coverage_recall", _SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class AllHeadingsTests(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_h1은_제외되고_h2_h6은_전부_잡힌다(self):
        text = "# 제목\n\n## 둘\n\n### 셋\n\n#### 넷\n\n##### 다섯\n\n###### 여섯\n"
        heads = self.m.all_headings(text)
        self.assertEqual([d for d, _ in heads], [2, 3, 4, 5, 6])

    def test_프론트매터는_헤딩으로_안_잡힌다(self):
        text = "---\ntags: [a, b]\n---\n\n## 진짜 헤딩\n\n본문.\n"
        heads = self.m.all_headings(text)
        self.assertEqual(len(heads), 1)

    def test_같은_slug는_첫_등장만(self):
        text = "## 중복\n\n본문.\n\n### 중복\n\n본문.\n"
        heads = self.m.all_headings(text)
        self.assertEqual(len(heads), 1)
        self.assertEqual(heads[0][0], 2)   # 먼저 나온 깊이(H2)가 남는다


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_h2_h3만_있으면_전부_잡혀_recall_1(self):
        text = "## 하나\n\n본문 하나.\n\n### 둘\n\n본문 둘.\n"
        cov = self.m.coverage(text)
        self.assertEqual(cov, {"total": 2, "caught": 2, "shallow": 2, "deep": 0,
                               "missed_to_depth": 0, "missed_to_cap": 0, "recall": 1.0})

    def test_h4_이상은_깊이로_놓친다(self):
        text = "## 하나\n\n본문.\n\n#### 둘\n\n본문.\n\n##### 셋\n\n본문.\n"
        cov = self.m.coverage(text)
        self.assertEqual(cov["total"], 3)
        self.assertEqual(cov["caught"], 1)
        self.assertEqual(cov["missed_to_depth"], 2)
        self.assertEqual(cov["missed_to_cap"], 0)
        self.assertAlmostEqual(cov["recall"], 1 / 3)

    def test_h2_h3가_24개_넘으면_상한으로_놓친다(self):
        text = "".join(f"## 절{i}\n\n본문{i}.\n\n" for i in range(30))
        cov = self.m.coverage(text)
        self.assertEqual(cov["total"], 30)
        self.assertEqual(cov["shallow"], 30)
        self.assertEqual(cov["caught"], 24)          # extract_concepts 의 상한
        self.assertEqual(cov["missed_to_depth"], 0)  # 전부 H2 라 깊이 손실은 없다
        self.assertEqual(cov["missed_to_cap"], 6)    # 30 - 24
        self.assertAlmostEqual(cov["recall"], 24 / 30)

    def test_헤딩이_없으면_recall_1(self):
        # "놓친 게 없다" 와 "잴 게 없다"는 다른 사실이지만, 분모가 0인 문서를
        # 집계에서 빼는 건 main() 의 몫이다(§ 없으면 없다고 말한다) — 여기 순수
        # coverage() 는 정의상 1.0 을 반환해 0으로 나누지 않는다.
        cov = self.m.coverage("본문만 있고 헤딩이 없는 문서.\n")
        self.assertEqual(cov["total"], 0)
        self.assertEqual(cov["recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
