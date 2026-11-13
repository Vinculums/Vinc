# -*- coding: utf-8 -*-
"""View 는 자기가 쓰는 주소를 같은 파서로 읽는다.

정본: [[decision:the-app-reads-its-own-view-addresses-through-the-shared-parser]].

`app/src/view-url.ts` 는 `formatViewUrl`(쓰기)과 `parseViewUrl`(읽기)을 한 벌로 두고,
파이썬 거울(`vinc_mcp/viewurl.py`)은 `tests/mcp/test_view_url.py` 가 지킨다. 그런데 앱은
`parseViewUrl` 을 한 번도 부르지 않았다 — View 마운트가 해시를 손으로 쪼개 `trace`·
`read`·`as` 만 알았고, 앱이 스스로 복사하는 `#view/doc/<id>`·`#view/ids/<a,b>` 는
"doc"·"ids" 라는 **주제**로 읽혀 빈 지도가 오류 없이 떴다.

앱에는 JS 테스트 러너가 없어(`app/package.json` 에 `test` 없음) 이 리포의 관례대로
소스를 대조한다(`test_view_url.py` · `test_lnb_tint.py` 와 같은 자리). 여기서 고정하는 것:

  ① 마운트가 `parseViewUrl(location.hash)` 로 읽는다(손 파서로 되돌아가면 빨개진다).
  ② `doc` 은 리더로, `ids` 는 점등으로, `node` 는 착지로 간다 — 셋 다 `sel.kind` 로 가른다.
  ③ 앱 전용 `read` 는 공유 문법 **밖**이라 파서보다 먼저 걸러진다(안 그러면
     `parseViewUrl` 이 "read/<id>" 를 주제로 읽는다).
  ④ 마운트의 주제 판정이 해시 첫 조각(`parts[1]`)을 직접 보지 않는다."""
from __future__ import annotations

import unittest

from _src import func, read

VIEW = read("view")


class MountReadsTheSharedGrammar(unittest.TestCase):
    def setUp(self):
        self.src = func(VIEW, "renderView")

    def test_마운트가_공유_파서로_읽는다(self):
        self.assertIn("parseViewUrl(location.hash)", self.src)
        head = VIEW.split("\n", 12)[:12]
        self.assertTrue(any("parseViewUrl" in l and "view-url" in l for l in head),
                        "view.ts 가 view-url 에서 parseViewUrl 을 import 해야 한다")

    def test_read_는_파서보다_먼저_걸러진다(self):
        """`read` 는 LNB 의 넓은 착지로 공유 문법에 없다. 먼저 거르지 않으면 파서가
        `read/<id>` 를 주제 이름으로 읽는다."""
        at_read = self.src.index('readRoute = rawParts[1] === "read"')
        at_parse = self.src.index("parseViewUrl(location.hash)")
        self.assertLess(at_read, at_parse)
        self.assertRegex(self.src, r"readRoute \? null : parseViewUrl\(location\.hash\)")

    def test_doc_은_리더로_연다(self):
        self.assertIn('sel?.kind === "doc" ? sel.id', self.src)
        self.assertRegex(self.src, r"else if \(landReader\) await openDocAt\(landReader\)")

    def test_ids_는_전체로_받는다(self):
        # 점등(올가미 집합)은 3D 의 것이었다. 옛 `#view/ids/…` 주소는 깨지지 않고 전체 그래프로 열린다
        self.assertNotIn("spotIds", self.src)

    def test_node_는_착지한다(self):
        self.assertIn('sel?.kind === "node" ? sel.id', self.src)

    def test_주제는_파서가_준_목록이다(self):
        """손 파서는 `parts[1]` 만 보고 `doc`·`ids` 를 주제로 만들었다."""
        self.assertIn('sel?.kind === "domains"', self.src)
        self.assertIn("domains: sel.domains", self.src)
        self.assertNotRegex(self.src, r"\bdeep\.split\(")
        self.assertNotRegex(self.src, r"const deep = parts\[1\]")


if __name__ == "__main__":
    unittest.main()
