# -*- coding: utf-8 -*-
"""좌측 문서 트리: 3단계까지 펼친 채 시작하고, 줄 클릭은 접고 펴기만 한다(2026-09-26).

- 기본 펼침은 `app/src/lnb-tree.ts` 의 규칙이고 `app/dev/lnb-tree-check.ts` 가 실제로
  돌린다. 여기서는 `lnb.ts` 가 그 규칙을 쓰는지, 펼침을 저장하지 않는지 잠근다.
- 줄 클릭이 지도까지 바꾸던 것(이름 = 주제 고르기)을 줄 끝 버튼으로 옮겼다. 줄 클릭이
  다시 `pickTopic` 을 부르면 "안을 보려고" 누른 사람의 지도가 재배치된다.
근거: [[decision:documents-and-graph-share-one-lnb-tree-2026-11-06]]

앱에는 JS 테스트 러너가 없어 소스에서 잠근다.
"""
from __future__ import annotations

import re
import unittest

from _src import ROOT, braced, cut, func, read

LNB = read("lnb")
TREE = read("lnb-tree")


class TreeDefaultTests(unittest.TestCase):
    def test_규칙_모듈은_import_가_없다(self):
        self.assertNotRegex(TREE, r"(?m)^import ")
        self.assertTrue((ROOT / "app" / "dev" / "lnb-tree-check.ts").exists())
        # 2026-11-05: 문서 · 그래프 트리 모두 맨 위 주제 아래 영역까지만 펼친 채 시작한다
        self.assertIn("export const DEFAULT_OPEN_DEPTH = 1;", TREE)

    def test_펼침을_저장하지_않고_켤_때마다_기본에서_시작한다(self):
        self.assertNotIn("saveExpanded", LNB)
        self.assertNotIn("loadExpanded", LNB)
        self.assertIn("localStorage.removeItem(KEY_EXPANDED)", LNB)
        self.assertIn("applyDefaults(tops, st.expanded, st.seenDefault)", LNB)

    def test_하위_주제가_있는_줄은_자기_항목을_접어_둔다(self):
        rows = func(LNB, "groupRows")
        self.assertIn("foldsOwnRows(n)", rows)
        self.assertIn("ownKey(n.path)", rows)
        self.assertIn('class="lnb-fold"', rows)


class TreeClickTests(unittest.TestCase):
    def setUp(self):
        self.click = braced(LNB, 'body.addEventListener("click"')

    def test_주제_줄을_누르면_지금_표면이_따라간다(self):
        """2026-11-05 발주자 지시: 문서도 그래프처럼 — 줄을 누르면 문서 목록이 그 주제로 좁혀지며 펴지고,
        이미 그 주제면 접고 편다. Ctrl · Cmd 는 주제를 더한다."""
        group = self.click[self.click.index('const group = t.closest<HTMLElement>(".lnb-group")'):]
        self.assertIn("pickTopic(path, true)", group)        # Ctrl · Cmd = 더하기
        self.assertIn("follow(path)", group)                  # 아직 그 주제가 아니면 따라간다
        self.assertIn("toggleRow(path)", group)               # 이미 그 주제면 접고 편다
        self.assertIn("pickTopic(path, false)", self.click)   # 문서 표면의 따라가기

    def test_줄_끝_좁히기_버튼은_없다(self):
        """줄 클릭이 같은 일을 하므로 줄 끝 버튼(.lnb-gscope)은 걷었다(2026-11-05)."""
        self.assertNotIn("lnb-gscope", LNB)
        rows = func(LNB, "groupRows")
        self.assertIn('<div class="lnb-grow', rows)

    def test_화살표_키로_움직이고_접고_편다(self):
        keys = cut(LNB, "// 트리 키보드", "// 주제 우클릭")
        for k in ("ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"):
            self.assertIn(f'"{k}"', keys, k)
        self.assertIn("toggleRow(key)", keys)

    def test_새_문구가_두_언어에_있다(self):
        copy = read("copy")
        self.assertEqual(len(re.findall(r"^\s+scopeBtn: \(name: string\)", copy, re.M)), 0)   # 버튼과 함께 걷었다
        self.assertEqual(len(re.findall(r"^\s+ownItems: \(n: number\)", copy, re.M)), 2)


if __name__ == "__main__":
    unittest.main()
