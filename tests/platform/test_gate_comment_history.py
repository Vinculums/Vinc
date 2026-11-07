# -*- coding: utf-8 -*-
"""gate_comment_history: 주석 이력 표지 계수기와 래칫 비교를 잠근다.

계수기가 주석만 세는지(문자열·코드는 안 센다), 문법마다 주석을 놓치지 않는지,
그리고 표지를 더하면 막히고 빼면 통과하는지를 본다.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gate_comment_history as g  # noqa: E402

DATE = "2026-" + "09-01"  # 이 파일 자신이 표지를 들고 다니지 않도록 조립한다


def _lines(rel: str, src: str) -> list[int]:
    return [ln for ln, _ in g.marker_lines(rel, src)]


class TestMarkers(unittest.TestCase):
    def test_marker_set(self):
        for text in (f"# {DATE} 에 바꿨다", "# VIN-123 참고", "# #482 에서", "# PR 512 이후",
                     "# 발주자 지시", "# 실측 결과", "# 옛 경로", "# 레거시 호환"):
            self.assertEqual([1], _lines("a.py", text + "\n"), text)

    def test_plain_comment_and_hex_colour_are_not_markers(self):
        self.assertEqual([], _lines("a.py", "# 노드 둘을 합친다\n"))
        self.assertEqual([], _lines("a.css", "/* 배경 #fff 와 &#8212; */\n"))
        self.assertEqual([], _lines("a.py", "# 2026 년 계획\n"))


class TestSyntax(unittest.TestCase):
    def test_python_comment_and_docstring_not_string(self):
        src = (f'"""모듈 설명 {DATE}."""\n'
               f'x = "{DATE} 는 값이다"\n'
               f'def f():\n'
               f'    """첫 줄\n'
               f'    VIN-7 둘째 줄\n'
               f'    """\n'
               f'    return 1  # VIN-8\n')
        self.assertEqual([1, 5, 7], _lines("m.py", src))

    def test_typescript_line_block_and_strings(self):
        src = (f"// {DATE} 줄 주석\n"
               f"const s = \"// VIN-1 문자열 속\";\n"
               f"/* 블록\n"
               f" * VIN-2 블록 둘째 줄 */\n"
               f"const t = `템플릿 // VIN-3 ${{ a ? `안쪽 VIN-4` : \"\" }} 끝`;\n"
               f"const re = /[\"']/g; // VIN-5 정규식 뒤\n")
        self.assertEqual([1, 4, 6], _lines("a.ts", src))

    def test_javascript_url_in_string_is_not_comment(self):
        self.assertEqual([], _lines("a.mjs", 'const u = "https://x.io/VIN-9";\n'))

    def test_css_block(self):
        self.assertEqual([2], _lines("a.css", f".a {{ color: red; }}\n/* 실측 {DATE} */\n"))

    def test_yaml_hash_but_not_in_quotes(self):
        src = (f"# VIN-10 머리 주석\n"
               f"name: \"값 # VIN-11 아님\"\n"
               f"run: echo hi  # {DATE}\n")
        self.assertEqual([1, 3], _lines("ci.yml", src))

    def test_rust_lifetime_does_not_open_a_string(self):
        src = "fn f<'a>(x: &'a str) {}\n// VIN-12 러스트\n"
        self.assertEqual([2], _lines("lib.rs", src))

    def test_skipped_paths(self):
        for rel in ("tests/core/a.py", "_migration/x.py", "scripts/design/figma/a.js",
                    "CHANGELOG.md", "x.json", "app/src/a.test.ts", "pkg/tests/a.py"):
            self.assertFalse(g.wanted(rel), rel)
        self.assertTrue(g.wanted("api/query.py"))


class TestRatchet(unittest.TestCase):
    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("git 없음")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        subprocess.run(["git", "add", rel], cwd=self.root, check=True)

    def _run(self, *args: str) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = g.main(["--root", str(self.root), *args])
        return code, buf.getvalue()

    def test_add_blocks_remove_passes_and_lowers(self):
        self._write("a.py", f"# VIN-1\n# {DATE}\nx = 1\n")
        self.assertEqual(0, self._run("--write-baseline")[0])
        base = json.loads((self.root / "scripts/comment-history-baseline.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual({"a.py": 2}, base["files"])

        self._write("a.py", f"# VIN-1\n# {DATE}\n# 실측 하나 더\nx = 1\n")
        code, out = self._run()
        self.assertEqual(1, code)
        self.assertIn("BLOCK", out)
        self.assertIn("a.py:3", out)

        self._write("a.py", "# VIN-1\nx = 1\n")
        code, out = self._run()
        self.assertEqual(0, code)
        self.assertIn("--write-baseline", out)
        self._run("--write-baseline")
        base = json.loads((self.root / "scripts/comment-history-baseline.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual({"a.py": 1}, base["files"])

    def test_new_file_allowance(self):
        self._write("a.py", "x = 1\n")
        self._run("--write-baseline")
        self._write("b.ts", "".join(f"// VIN-{i}\n" for i in range(g.NEW_FILE_ALLOWANCE)))
        self.assertEqual(0, self._run()[0])
        self._write("b.ts", "".join(f"// VIN-{i}\n" for i in range(g.NEW_FILE_ALLOWANCE + 1)))
        code, out = self._run()
        self.assertEqual(1, code)
        self.assertIn("새 파일 허용치", out)

    def test_baseline_entry_for_missing_file_is_ignored(self):
        self._write("a.py", "# VIN-1\n")
        (self.root / "scripts/comment-history-baseline.json").write_text(
            json.dumps({"files": {"a.py": 1, "gone.py": 40}}), encoding="utf-8")
        self.assertEqual(0, self._run()[0])


if __name__ == "__main__":
    unittest.main()
