# -*- coding: utf-8 -*-
"""스타일시트는 `app.css` 목차 하나와 `src/css/*.css` 기능별 파일들이다.

`app.css` 는 `@import` 만 담고, 그 적힌 순서가 곧 캐스케이드 순서다. 이 파일들이 지키는 것:

- `src/css/` 의 파일은 하나도 빠짐없이, 한 번씩만 `@import` 된다(빠진 파일은 규칙이 조용히 사라진다).
- `app.css` 에는 `@import` 말고 규칙이 없다(`@import` 는 다른 규칙보다 앞에 서야 한다).
- 토큰 파일이 기능별 파일보다 먼저 온다.
- 글꼴 `url()` 은 파일 기준 상대경로이고 가리키는 글꼴 파일이 있다.
"""
from __future__ import annotations

import re
import unittest

from _src import SRC

COMMENT = re.compile(r"/\*.*?\*/", re.S)
IMPORT = re.compile(r'@import "\./([\w./-]+\.css)";')


class CssSplitTests(unittest.TestCase):
    index = (SRC / "app.css").read_text(encoding="utf-8")
    imports = IMPORT.findall(index)

    def test_css_폴더의_파일은_한_번씩_import_된다(self):
        on_disk = sorted(f"css/{p.name}" for p in (SRC / "css").glob("*.css"))
        listed = [i for i in self.imports if i.startswith("css/")]
        self.assertEqual(sorted(listed), on_disk)
        self.assertEqual(len(listed), len(set(listed)), "같은 파일을 두 번 import 했다")

    def test_import_가_가리키는_파일이_있다(self):
        for rel in self.imports:
            self.assertTrue((SRC / rel).is_file(), f"app.css 가 없는 파일을 import 한다: {rel}")

    def test_app_css_에는_import_말고_규칙이_없다(self):
        rest = IMPORT.sub("", COMMENT.sub("", self.index))
        self.assertEqual(rest.strip(), "", "app.css 에 @import 가 아닌 글이 있다 — 규칙은 src/css/ 의 기능별 파일에 둔다")

    def test_토큰이_기능별_파일보다_먼저_온다(self):
        first_feature = next(i for i, rel in enumerate(self.imports) if rel.startswith("css/"))
        for tok in ("tokens.desktop.css", "tokens.web.css"):
            self.assertLess(self.imports.index(tok), first_feature)

    def test_글꼴_url_은_파일_기준_상대경로로_글꼴을_가리킨다(self):
        base = SRC / "css" / "base.css"
        urls = re.findall(r'url\("([^"]*Pretendard[^"]*)"\)', base.read_text(encoding="utf-8"))
        self.assertEqual(len(urls), 4)
        for u in urls:
            self.assertTrue(u.startswith("../fonts/"), u)
            self.assertTrue((base.parent / u).is_file(), f"글꼴 파일이 없다: {u}")


if __name__ == "__main__":
    unittest.main()
