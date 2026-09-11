# -*- coding: utf-8 -*-
"""태그 밖의 일을 세는 검사 — **거르는 쪽과 못 보는 쪽이 둘 다 옳은가.**

🔴 2026-09-11: v0.9.1 을 찍을 때 셸 크롬 개편 30커밋이 머지도 푸시도 안 된 브랜치에
있었고, `main` 만 세는 판정에는 보이지 않았다. `scripts/unmerged_work.py` 가 그 자리를
메운다. 이 파일이 잠그는 것은 그 도구의 두 급소다:

⑴ **스쿼시 잔해를 거르는가.** 안 거르면 목록이 40줄이 되고 아무도 안 읽는다. 못 읽는
   목록은 없는 목록과 같으므로, 이것은 미관이 아니라 기능이다.
⑵ **못 본 것을 없다고 말하지 않는가.** 얕은 체크아웃에서 조용한 빈 목록을 내면 그것이
   곧 2026-09-11 의 고장을 CI 안에서 재현하는 것이다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts import unmerged_work as U                      # noqa: E402


def git(cwd: str, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def commit(cwd: str, name: str, subject: str) -> None:
    with open(os.path.join(cwd, name), "w", encoding="utf-8") as fh:
        fh.write(subject)
    git(cwd, "add", "-A")
    git(cwd, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", subject)


class Fixture(unittest.TestCase):
    """진짜 리포를 하나 짓는다. 이 도구는 git 그 자체를 읽으므로 목으로는 못 잰다."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        git(self.dir, "init", "-q", "-b", "main")
        commit(self.dir, "a.txt", "base")
        self.prev = os.getcwd()
        os.chdir(self.dir)

    def tearDown(self):
        os.chdir(self.prev)

    def rows(self):
        return U.survey()[0]

    def names(self):
        return {r["branch"].split()[0] for r in self.rows()}


class Filtering(Fixture):
    def test_스쿼시로_머지된_브랜치는_목록에_없다(self):
        """브랜치의 커밋 제목이 main 에 있으면(스쿼시가 남긴 제목) 잔해로 본다."""
        git(".", "checkout", "-q", "-b", "feat/x")
        commit(".", "b.txt", "feat(x): do the thing")
        git(".", "checkout", "-q", "main")
        commit(".", "c.txt", "feat(x): do the thing (#42)")   # 스쿼시 머지의 모양
        self.assertNotIn("feat/x", self.names())

    def test_진짜_미머지는_목록에_있다(self):
        git(".", "checkout", "-q", "-b", "feat/y")
        commit(".", "b.txt", "feat(y): never merged")
        git(".", "checkout", "-q", "main")
        rows = self.rows()
        self.assertEqual([r["branch"].split()[0] for r in rows], ["feat/y"])
        self.assertEqual(rows[0]["new"], 1)

    def test_일부만_머지된_브랜치는_남은_것만_센다(self):
        git(".", "checkout", "-q", "-b", "feat/z")
        commit(".", "b.txt", "feat(z): first")
        commit(".", "c.txt", "feat(z): second")
        git(".", "checkout", "-q", "main")
        commit(".", "d.txt", "feat(z): first (#7)")
        rows = self.rows()
        self.assertEqual(rows[0]["new"], 1, "머지된 하나를 빼고 세야 한다")
        self.assertEqual(rows[0]["total"], 2)

    def test_main_은_자기_자신을_세지_않는다(self):
        self.assertEqual(self.rows(), [])


class Blindness(Fixture):
    def test_원격이_없으면_없다고_말하지_않는다(self):
        """🔴 조용한 빈 목록이 이 도구가 막으려는 고장 그 자체다."""
        git(".", "checkout", "-q", "-b", "feat/w")
        commit(".", "b.txt", "feat(w): unmerged")
        git(".", "checkout", "-q", "main")
        rows, saw_remote = U.survey()
        self.assertFalse(saw_remote, "원격 ref 가 없는데 봤다고 한다")
        text = U.render(rows, saw_remote, markdown=False)
        self.assertIn("못 본 것", text)
        self.assertNotIn("남은 일이 없다", text)

    def test_막지_않는다(self):
        """실험 브랜치가 태그를 볼모로 잡으면 안 된다 — 언제나 0."""
        git(".", "checkout", "-q", "-b", "feat/v")
        commit(".", "b.txt", "feat(v): unmerged")
        git(".", "checkout", "-q", "main")
        self.assertEqual(U.main(["x"]), 0)


class Rendering(unittest.TestCase):
    def _rows(self, n):
        return [{"branch": f"b{i}", "new": n - i, "total": n - i,
                 "last": "2026-09-11", "head": f"subject {i}"} for i in range(n)]

    def test_긴_목록은_접힌다(self):
        text = U.render(self._rows(U.TOP + 5), True, markdown=False)
        self.assertIn(f"브랜치 {U.TOP + 5}개", text)
        self.assertIn("그 밖 5개", text)
        self.assertNotIn("b15", text)

    def test_마크다운은_표로_낸다(self):
        text = U.render(self._rows(2), True, markdown=True)
        self.assertIn("| 브랜치 |", text)
        self.assertIn("`b0`", text)

    def test_빈_목록은_빈_목록이라고_말한다(self):
        self.assertIn("남은 일이 없다", U.render([], True, markdown=False))


if __name__ == "__main__":
    unittest.main()
