# -*- coding: utf-8 -*-
"""워크스페이스 루트용 WorktreeCreate/WorktreeRemove 훅 — 실제 git 으로, 실제 프로세스로.

훅은 하네스가 **별도 프로세스로** 띄우고 stdout 의 마지막 줄을 경로로 읽는다. 그래서
함수를 부르지 않고 스크립트를 그대로 띄운다. git 의 한 줄이 stdout 으로 새면 하네스가
그 줄을 경로로 읽는데, 함수 호출로는 그 고장이 안 보인다.

전부 임시 디렉터리 안에서 한다: 로컬 bare 원격 하나, 거기서 뽑은 `Vinc`, 그 옆 `_wt`.
이 리포의 체크아웃 위치나 네트워크와 무관하다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "worktree_hook.py"
_GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")


def _git(where: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True,
                          text=True, env=_GIT_ENV).stdout.strip()


def _link_dir(target: Path, link: Path) -> None:
    """윈도는 정션(권한 없이 만들 수 있고, 사고를 낸 것이 바로 이것이다), 그 밖은 심링크."""
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


class WorktreeHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name).resolve()
        cls.repo, cls.into = cls.root / "Vinc", cls.root / "_wt"
        origin = cls.root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(cls.repo)], check=True)
        (cls.repo / ".gitignore").write_text("node_modules\n", encoding="utf-8")
        _git(cls.repo, "add", ".gitignore")
        _git(cls.repo, "commit", "-q", "-m", "seed")
        _git(cls.repo, "remote", "add", "origin", str(origin))
        _git(cls.repo, "push", "-q", "-u", "origin", "main")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _hook(self, verb: str, payload: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(HOOK), verb,
             "--repo", str(self.repo), "--into", str(self.into)],
            input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
            env=_GIT_ENV, timeout=120)

    def _create(self, name: str) -> Path:
        r = self._hook("create", {"name": name, "hook_event_name": "WorktreeCreate"})
        self.assertEqual(r.returncode, 0, r.stderr)
        return Path(r.stdout.strip().splitlines()[-1])

    def test_create_prints_only_the_path_of_a_worktree_on_origin_main(self):
        r = self._hook("create", {"name": "pkg-create"})
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertEqual(lines, [str(self.into / "pkg-create")], "stdout 에 경로 말고 다른 줄이 샜다")
        wt = Path(lines[0])
        self.assertEqual(_git(wt, "branch", "--show-current"), "worktree-pkg-create")
        self.assertEqual(_git(wt, "rev-parse", "HEAD"), _git(self.repo, "rev-parse", "origin/main"))

    def test_create_refuses_a_name_that_is_not_a_slug(self):
        r = self._hook("create", {"name": "../escape"})
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.root / "escape").exists())

    def test_remove_deletes_a_clean_pushed_tree_and_its_empty_branch(self):
        wt = self._create("pkg-clean")
        r = self._hook("remove", {"worktree_path": str(wt), "reason": "subagent_finish"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(wt.exists())
        self.assertNotIn(str(wt).replace("\\", "/"), _git(self.repo, "worktree", "list"))
        self.assertEqual(_git(self.repo, "branch", "--list", "worktree-pkg-clean"), "")

    def test_remove_keeps_uncommitted_work(self):
        wt = self._create("pkg-dirty")
        (wt / "draft.py").write_text("x = 1\n", encoding="utf-8")
        r = self._hook("remove", {"worktree_path": str(wt)})
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((wt / "draft.py").exists())

    def test_remove_keeps_commits_no_remote_has(self):
        wt = self._create("pkg-ahead")
        (wt / "done.py").write_text("x = 1\n", encoding="utf-8")
        _git(wt, "add", "done.py")
        _git(wt, "commit", "-q", "-m", "work")
        r = self._hook("remove", {"worktree_path": str(wt)})
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((wt / "done.py").exists())

    def test_remove_refuses_a_path_this_hook_did_not_create(self):
        r = self._hook("remove", {"worktree_path": str(self.repo)})
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((self.repo / ".gitignore").exists())

    def test_remove_deletes_a_link_but_not_what_it_points_to(self):
        """2026-09-13 사고의 모양 그대로: 무시된 `node_modules` 가 다른 체크아웃을 가리키는
        링크다. git status 는 깨끗하다고 하고, 지우는 쪽이 링크를 따라가면 대상이 빈다."""
        wt = self._create("pkg-link")
        target = self.root / "other-checkout-node_modules"
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
        _link_dir(target, wt / "node_modules")
        r = self._hook("remove", {"worktree_path": str(wt)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(wt.exists())
        self.assertTrue((target / "keep.txt").exists(), "링크를 따라가 대상을 지웠다")


if __name__ == "__main__":
    unittest.main()
