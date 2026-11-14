# -*- coding: utf-8 -*-
"""쓰기 창에 걸린 문서의 안내 문구(copy.ts 의 `readerMidWrite` · `readerRetry`) 자체의 계약.

화면에 나오는가 · 갈래가 맞는가 · 사람이 누를 때만 다시 읽는가 · 편집기를 세우지 않는가는
`app/tests/home-list.test.mjs` 의 "쓰기 창에 걸린 문서" 절이 실제 리더 · 열 · 편집기 코드를 돌려 잰다.
서버가 그 갈래에만 청크 수를 싣는 것은 `tests/core/test_docstore.py` 의
`test_쓰기_창의_읽기는_성공으로_답하지_않는다` 가 잰다.
여기에 남은 것은 문구 데이터뿐이다: 두 덱이 한 벌씩 갖는지, "못 열었다"와 다른 말인지, 읽는 사람이 그 자리에서
할 수 없는 처방을 적지 않았는지, 화면 문구 규칙(em-dash 금지)을 지키는지.
"""
from __future__ import annotations

import re
import unittest

from _src import read

COPY = read("copy")


class CopyTests(unittest.TestCase):
    def test_두_덱_모두_새_문구를_갖는다(self):
        for key in ("readerMidWrite", "readerRetry"):
            with self.subTest(key):
                self.assertEqual(COPY.count(f"{key}: "), 2, "EN·KO 한 벌씩이어야 한다")

    def test_빈_문서_문구와_다른_말을_한다(self):
        """같은 문자열을 재사용하면 화면에서 안 갈린다."""
        mid = re.findall(r'readerMidWrite: "([^"]+)"', COPY)
        fail = re.findall(r'readerFail: "([^"]+)"', COPY)
        self.assertEqual((len(mid), len(fail)), (2, 2))
        self.assertEqual(set(mid) & set(fail), set())

    def test_여기서_못_하는_처방을_적지_않는다(self):
        """이 화면은 편집기를 세우지 않는 것이 존재 이유다. 읽는 사람이 그 자리에서 실행할 수단이 없는
        처방("전체 내용을 다시 저장하면 돌아와요")으로 끝나면 안 된다. 끝은 진단이어야 한다."""
        for s in re.findall(r'readerMidWrite: "([^"]+)"', COPY):
            self.assertNotIn("다시 저장", s)
            self.assertNotRegex(s, r"(?i)save the full text")

    def test_화면_문구에_em_dash_가_없다(self):
        for s in re.findall(r'reader(?:MidWrite|Retry): "([^"]+)"', COPY):
            self.assertNotIn("—", s)


if __name__ == "__main__":
    unittest.main()
