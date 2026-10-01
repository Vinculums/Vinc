# -*- coding: utf-8 -*-
"""좌측 문서 트리: 3단계까지 펼친 채 시작하고, 줄 클릭은 접고 펴기만 한다(2026-09-26).

- 기본 펼침은 `app/src/lnb-tree.ts` 의 규칙이고 `app/dev/lnb-tree-check.ts` 가 실제로
  돌린다. 여기서는 `lnb.ts` 가 그 규칙을 쓰는지, 펼침을 저장하지 않는지 잠근다.
- 줄 클릭이 지도까지 바꾸던 것(이름 = 주제 고르기)을 줄 끝 버튼으로 옮겼다. 줄 클릭이
  다시 `pickTopic` 을 부르면 "안을 보려고" 누른 사람의 지도가 재배치된다.
근거: [[decision:the-document-tree-opens-three-levels-and-a-row-click-only-folds-2026-09-26]]

앱에는 JS 테스트 러너가 없어 소스에서 잠근다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LNB = (ROOT / "app" / "src" / "lnb.ts").read_text(encoding="utf-8")
TREE = (ROOT / "app" / "src" / "lnb-tree.ts").read_text(encoding="utf-8")


def _block(src: str, head: str, end: str) -> str:
    at = src.index(head)
    return src[at:src.index(end, at)]


class TreeDefaultTests(unittest.TestCase):
    def test_규칙_모듈은_import_가_없다(self):
        self.assertNotRegex(TREE, r"(?m)^import ")
        self.assertTrue((ROOT / "app" / "dev" / "lnb-tree-check.ts").exists())
        self.assertIn("export const DEFAULT_OPEN_DEPTH = 2;", TREE)

    def test_펼침을_저장하지_않고_켤_때마다_기본에서_시작한다(self):
        self.assertNotIn("saveExpanded", LNB)
        self.assertNotIn("loadExpanded", LNB)
        self.assertIn("localStorage.removeItem(KEY_EXPANDED)", LNB)
        self.assertIn("applyDefaults(tops, expanded, seenDefault)", LNB)

    def test_하위_주제가_있는_줄은_자기_항목을_접어_둔다(self):
        rows = _block(LNB, "function groupRows", "\n}")
        self.assertIn("foldsOwnRows(n)", rows)
        self.assertIn("ownKey(n.path)", rows)
        self.assertIn('class="lnb-fold"', rows)


class TreeClickTests(unittest.TestCase):
    def setUp(self):
        self.click = _block(LNB, 'body.addEventListener("click"', '// 트리 키보드')

    def test_줄_클릭은_접고_펴기만_한다(self):
        group = self.click[self.click.index('t.closest<HTMLElement>(".lnb-group")'):]
        self.assertIn("toggleRow(group.dataset.group", group)
        self.assertNotIn("pickTopic", group.split("\n")[1])

    def test_지도_좁히기는_줄_끝_버튼이_한다(self):
        self.assertIn('t.closest<HTMLElement>(".lnb-gscope")', self.click)
        self.assertIn("pickTopic(key, e.ctrlKey || e.metaKey)", self.click)
        rows = _block(LNB, "function groupRows", "\n}")
        self.assertIn('class="lnb-gscope"', rows)
        self.assertIn("COPY.ui.lnb.scopeBtn(n.seg)", rows)
        # 버튼은 줄 안이 아니라 줄의 형제다(버튼 안에 버튼을 둘 수 없다).
        self.assertIn('<div class="lnb-grow', rows)

    def test_화살표_키로_움직이고_접고_편다(self):
        keys = _block(LNB, "// 트리 키보드", "// 주제 우클릭")
        for k in ("ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"):
            self.assertIn(f'"{k}"', keys, k)
        self.assertIn("toggleRow(key)", keys)

    def test_새_문구가_두_언어에_있다(self):
        copy = (ROOT / "app" / "src" / "copy.ts").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"^\s+scopeBtn: \(name: string\)", copy, re.M)), 2)
        self.assertEqual(len(re.findall(r"^\s+ownItems: \(n: number\)", copy, re.M)), 2)


if __name__ == "__main__":
    unittest.main()
