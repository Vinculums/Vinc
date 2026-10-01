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
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "scripts" / "worktree_hook.py"
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
        (cls.repo / ".gitignore").write_text(
            "node_modules\n__pycache__/\n.claude/\nvinc_store\n*.lbug\n", encoding="utf-8")
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

    def _stop(self, payload: dict) -> subprocess.CompletedProcess:
        r = self._hook("subagent-stop", {"hook_event_name": "SubagentStop", **payload})
        self.assertEqual(r.returncode, 0, f"SubagentStop 훅이 0 아닌 값으로 끝났다: {r.stderr}")
        return r

    def _push_own_commit(self, wt: Path, branch: str) -> None:
        """dev 역할이 하는 그대로: origin/main 에서 브랜치를 따고, 커밋하고, 푸시한다."""
        _git(wt, "switch", "-q", "-c", branch)
        (wt / f"{branch.replace('/', '-')}.py").write_text("x = 1\n", encoding="utf-8")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-q", "-m", "work")
        _git(wt, "push", "-q", "-u", "origin", branch)

    def _age(self, wt: Path, hours: float) -> None:
        """청소가 재는 신호(그 worktree 의 git 관리 디렉터리 index·HEAD 의 mtime)를 되돌린다."""
        gitdir = Path(_git(wt, "rev-parse", "--absolute-git-dir"))
        then = time.time() - hours * 3600
        for f in (gitdir / "index", gitdir / "HEAD"):
            os.utime(f, (then, then))

    def test_subagent_stop_removes_an_unchanged_tree_found_by_id_or_by_cwd(self):
        for label, payload_of in (
            ("bare id", lambda wt: {"agent_id": wt.name[len("agent-"):], "cwd": "C:/elsewhere"}),
            ("prefixed id", lambda wt: {"agent_id": wt.name}),
            ("cwd only", lambda wt: {"cwd": str(wt / "__pycache__")}),
        ):
            with self.subTest(label):
                wt = self._create(f"agent-stop-{label.replace(' ', '-')}")
                # 버려도 되는 무시 파일(캐시·테스트 스토어·훅 로그)은 제거를 막지 않는다
                (wt / "__pycache__").mkdir()
                (wt / "__pycache__" / "m.pyc").write_bytes(b"\0")
                (wt / ".claude").mkdir()
                (wt / ".claude" / "ui-events.jsonl").write_text("{}\n", encoding="utf-8")
                (wt / "vinc_store").write_bytes(b"\0")
                _git(wt, "switch", "-q", "-c", f"fix/unchanged-{label.replace(' ', '-')}")
                r = self._stop(payload_of(wt))
                self.assertFalse(wt.exists(), r.stderr)
                self.assertEqual(_git(self.repo, "branch", "--list", f"worktree-{wt.name}"), "")

    def test_subagent_stop_of_a_child_does_not_remove_the_parent_tree_it_ran_in(self):
        """비격리 자식은 부모의 cwd 를 물려받는다. 자식의 id 로는 트리가 없으니 아무것도
        지우지 않아야 한다 — cwd 를 따르면 아직 일하는 부모의 손대지 않은 트리가 사라진다."""
        parent = self._create("agent-stop-parent")
        self._stop({"agent_id": "child-of-parent", "cwd": str(parent)})
        self.assertTrue(parent.exists(), "자식이 멈추자 부모의 트리를 지웠다")

    def test_subagent_stop_keeps_a_tree_with_its_own_pushed_commit(self):
        wt = self._create("agent-stop-pushed")
        self._push_own_commit(wt, "fix/stop-pushed")
        self._stop({"agent_id": "stop-pushed", "cwd": str(wt)})
        self.assertTrue(wt.exists(), "푸시했지만 제 커밋이 있는 트리를 멈춤에서 지웠다")

    def test_subagent_stop_keeps_a_dirty_tree(self):
        wt = self._create("agent-stop-dirty")
        (wt / "draft.py").write_text("x = 1\n", encoding="utf-8")
        self._stop({"agent_id": "stop-dirty"})
        self.assertTrue((wt / "draft.py").exists())

    def test_subagent_stop_refuses_paths_outside_wt_and_names_that_are_not_agent_trees(self):
        self._stop({"agent_id": "../Vinc"})
        self._stop({"cwd": str(self.repo)})
        self.assertTrue((self.repo / ".gitignore").exists())
        other = self._create("pkg-not-an-agent")
        self._stop({"cwd": str(other)})
        self.assertTrue(other.exists(), "agent- 가 아닌 트리를 멈춤에서 지웠다")

    def test_create_sweeps_an_idle_pushed_tree_but_not_a_recent_one_or_a_non_agent_one(self):
        idle, recent = self._create("agent-sweep-idle"), self._create("agent-sweep-recent")
        other = self._create("pkg-sweep-idle")
        self._push_own_commit(idle, "fix/sweep-idle")
        self._push_own_commit(recent, "fix/sweep-recent")
        self._push_own_commit(other, "fix/sweep-other")
        self._age(idle, 25)
        self._age(other, 25)
        r = self._hook("create", {"name": "agent-sweep-next"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([ln for ln in r.stdout.splitlines() if ln.strip()],
                         [str(self.into / "agent-sweep-next")], "청소가 stdout 에 샜다")
        self.assertFalse(idle.exists(), r.stderr)
        self.assertTrue(recent.exists(), "하루가 안 된 트리를 쓸었다")
        self.assertTrue(other.exists(), "agent- 가 아닌 트리를 쓸었다")

    def test_create_does_not_sweep_an_idle_tree_with_unpushed_work(self):
        wt = self._create("agent-sweep-unpushed")
        (wt / "done.py").write_text("x = 1\n", encoding="utf-8")
        _git(wt, "add", "done.py")
        _git(wt, "commit", "-q", "-m", "work")
        self._age(wt, 25)
        self._create("agent-sweep-after-unpushed")
        self.assertTrue((wt / "done.py").exists())

    def test_remove_refuses_a_tree_holding_another_worktree_under_its_ignored_claude_dir(self):
        """`.claude` 는 버려도 되는 무시 디렉터리지만, 그 아래 `worktrees/*` 는 남의 일이다."""
        wt = self._create("agent-nested")
        inner = wt / ".claude" / "worktrees" / "inner"
        _git(wt, "worktree", "add", "-q", "-b", "inner-nested", str(inner))
        r = self._hook("remove", {"worktree_path": str(wt)})
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(inner.exists(), "트리 안의 다른 worktree 를 지웠다")

    def test_remove_refuses_a_tree_holding_an_ignored_file_that_is_not_a_cache(self):
        wt = self._create("agent-ignored")
        (wt / "local.lbug").write_bytes(b"data")
        r = self._hook("remove", {"worktree_path": str(wt)})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("local.lbug", r.stderr)
        self.assertTrue((wt / "local.lbug").exists())

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
