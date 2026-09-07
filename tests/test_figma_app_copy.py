# -*- coding: utf-8 -*-
"""figma_app_copy — 앱 보드(캡처)의 신선도 판정.

2026-09-06 에 두 번 바뀌었다: 글자 대조 → 리비전 대조 → **트리 대조**. 마지막 변경의 이유가
이 파일에서 제일 중요하다 — 리비전 대조로는 **어디서 캡처해도 통과할 수 없었다**(스쿼시 머지가
브랜치 커밋을 버리므로). 그 불가능을 없앤 사실이 `SquashInvariantTests` 에 박혀 있다."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

HUB = pathlib.Path(__file__).resolve().parents[1] / "design-hub"
_spec = importlib.util.spec_from_file_location("figma_app_copy", HUB / "scripts" / "figma_app_copy.py")
fac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fac)


class CheckTests(unittest.TestCase):
    M = {"sourceRev": "abc1234", "sourceTree": "t" * 40, "states": [{"id": "view__3d"}]}

    def test_the_same_tree_passes(self):
        r = fac.check(self.M, [], "t" * 40)
        self.assertTrue(r["ok"]); self.assertEqual(r["states"], 1)

    def test_a_different_tree_fails(self):
        r = fac.check(self.M, [], "u" * 40)
        self.assertFalse(r["ok"])

    def test_commits_do_not_decide_the_verdict(self):
        """계보는 설명이지 판정이 아니다. 리비전 대조 시절에는 이 줄이 반대였고, 그래서
        스쿼시 머지 뒤 main 이 무조건 빨개졌다."""
        self.assertTrue(fac.check(self.M, ["deadbee fix(view): reorder lens rows"], "t" * 40)["ok"])
        self.assertFalse(fac.check(self.M, [], "u" * 40)["ok"])

    def test_a_manifest_without_a_tree_fails_by_itself(self):
        r = fac.check({"sourceRev": "abc1234", "states": []}, [], "t" * 40)
        self.assertFalse(r["ok"]); self.assertIn("sourceTree", r["reason"])


def _shallow(repo) -> bool:
    import subprocess
    return subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=repo, capture_output=True, text=True).stdout.strip() == "true"


class GitTests(unittest.TestCase):
    def test_the_committed_manifest_is_fresh_or_names_the_commits(self):
        """실제 리포 위에서: 매니페스트의 리비전이 존재하고, 결과가 판정 가능한 형태여야 한다.
        얕은 체크아웃(fetch-depth 0 이 아닌 CI 잡)에서는 게이트가 판정을 거부하는 것이 계약이라 건너뛴다."""
        import json
        if _shallow(fac.REPO):
            self.skipTest("shallow checkout: the gate refuses to judge here by contract")
        m = json.loads((HUB / "reports" / "captures" / "app" / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(m.get("sourceRev"), "app manifest carries no sourceRev")
        self.assertTrue(m.get("sourceTree"), "app manifest carries no sourceTree")
        # 지문은 캡처한 그 리비전의 트리여야 한다 — 아무 값이나 적혀 있으면 판정이 거짓이 된다.
        # ⚠ 단 **그 리비전이 아직 있을 때만** 물을 수 있다. 브랜치에서 커밋한 뒤 캡처하면
        # sourceRev 는 그 브랜치 커밋이고, 스쿼시 머지가 그것을 죽인다(이 리포의 정상 흐름).
        # 그러면 깨끗한 체크아웃에서 이 줄이 RuntimeError 로 터지고, 계약은 멀쩡한데
        # 설명용 필드 하나 때문에 main 이 빨개진다. 실측: 2026-09-07, 재캡처 #1(d4c4e75)이
        # 머지된 뒤 main 의 ci 가 `path 'app/src' exists on disk, but not in 'd4c4e75...'`
        # 로 죽었다. 정본이 sourceTree 이고 sourceRev 는 설명이라는 것은 매니페스트 자신의
        # sourceTreeNote 에 적혀 있다 — 게이트(figma_app_copy.main)도 이미 그렇게 관대하다.
        # assertEqual 은 AssertionError 라 이 except 에 안 걸린다: 리비전이 살아 있는데
        # 지문이 다르면 여전히 실패한다.
        try:
            self.assertEqual(m["sourceTree"], fac.source_tree(m["sourceRev"]))
            changes = fac.changes_since(m["sourceRev"])
        except RuntimeError as e:
            self.skipTest(f"sourceRev {m['sourceRev'][:7]} is gone (squash merge): {e}")
        self.assertIsInstance(changes, list)

    def test_an_unknown_revision_raises_instead_of_passing(self):
        with self.assertRaises(RuntimeError):
            fac.changes_since("0000000000000000000000000000000000000000")

    def test_a_shallow_checkout_raises_instead_of_judging(self):
        """잘린 역사는 병합 커밋을 부모 없는 뿌리로 읽어 거짓 FAIL 을, 경계 너머 변경을 숨겨 거짓 PASS 를 낸다(2026-09-06 CI)."""
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as d:
            src, clone = pathlib.Path(d, "src"), pathlib.Path(d, "clone")
            src.mkdir()
            env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "HOME": d, "PATH": __import__("os").environ["PATH"]}
            run = lambda *a, cwd: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, env=env)
            run("init", "-q", cwd=src)
            for i in range(2):
                pathlib.Path(src, "app").mkdir(exist_ok=True); pathlib.Path(src, "app", "f.txt").write_text(str(i))
                run("add", ".", cwd=src); run("commit", "-q", "-m", f"c{i}", cwd=src)
            first = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=src, capture_output=True, text=True, env=env).stdout.strip()
            run("clone", "-q", "--depth", "1", f"file://{src.as_posix()}", str(clone), cwd=d)
            with self.assertRaises(RuntimeError) as cm:
                fac.changes_since(first, paths=("app",), repo=clone)
            self.assertIn("shallow", str(cm.exception))


class TreeTests(unittest.TestCase):
    def test_the_tree_of_a_known_revision_reads_back(self):
        if _shallow(fac.REPO):
            self.skipTest("shallow checkout: HEAD~ may not exist here")
        self.assertRegex(fac.source_tree("HEAD"), r"^[0-9a-f]{40}$")

    def test_an_unknown_path_raises_instead_of_passing(self):
        with self.assertRaises(RuntimeError):
            fac.source_tree("HEAD", paths=("no/such/dir",))


def _repo(d, env):
    """커밋 둘짜리 리포. `app/src` 아래에 파일을 두어 감시 경로를 흉내 낸다."""
    import subprocess
    run = lambda *a, cwd: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, env=env)
    src = pathlib.Path(d, "src"); src.mkdir()
    run("init", "-q", "-b", "main", cwd=src)
    app = pathlib.Path(src, "app", "src"); app.mkdir(parents=True)
    pathlib.Path(app, "a.ts").write_text("0")
    run("add", ".", cwd=src); run("commit", "-q", "-m", "base", cwd=src)
    return src, run


class SquashInvariantTests(unittest.TestCase):
    """이 게이트가 트리로 재는 **이유**. 스쿼시 머지는 커밋을 새로 만들지만 트리는 그대로
    옮긴다 — 그래서 브랜치에서 캡처한 지문이 머지된 main 에서도 맞는다. 리비전 대조는
    이 자리에서 무너졌다(브랜치 커밋이 main 의 조상이 아니라 항상 '변경됨'으로 잡혔다)."""

    def test_squash_merge_keeps_the_tree_and_changes_the_commit(self):
        import os
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                   "GIT_COMMITTER_EMAIL": "t@t", "HOME": d, "PATH": os.environ["PATH"]}
            src, run = _repo(d, env)
            run("switch", "-q", "-c", "feat", cwd=src)
            pathlib.Path(src, "app", "src", "a.ts").write_text("1")
            run("add", ".", cwd=src); run("commit", "-q", "-m", "change", cwd=src)
            branch = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True,
                                    text=True, env=env).stdout.strip()
            branch_tree = fac.source_tree(branch, paths=("app/src",), repo=src)

            run("switch", "-q", "main", cwd=src)
            run("merge", "-q", "--squash", "feat", cwd=src)
            run("commit", "-q", "-m", "change (#1)", cwd=src)
            merged = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True,
                                    text=True, env=env).stdout.strip()

            self.assertNotEqual(branch, merged, "스쿼시가 커밋을 새로 만들지 않았다")
            self.assertEqual(branch_tree, fac.source_tree(merged, paths=("app/src",), repo=src),
                             "스쿼시 머지가 app/src 트리를 바꿨다 — 이 게이트의 전제가 깨진다")
            # 그리고 브랜치에서 뜬 지문이 머지된 곳에서도 통과한다.
            man = {"sourceRev": branch, "sourceTree": branch_tree, "states": []}
            self.assertTrue(fac.check(man, [], fac.source_tree(merged, paths=("app/src",), repo=src))["ok"])

    def test_a_shallow_clone_can_still_be_judged(self):
        """얕은 체크아웃은 이제 판정을 막지 않는다 — 이 물음에는 역사가 필요 없다.
        (커밋 목록만 못 낸다. `changes_since` 의 계약은 그대로다.)"""
        import os
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                   "GIT_COMMITTER_EMAIL": "t@t", "HOME": d, "PATH": os.environ["PATH"]}
            src, run = _repo(d, env)
            clone = pathlib.Path(d, "clone")
            subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{src.as_posix()}",
                            str(clone)], cwd=d, check=True, capture_output=True, env=env)
            self.assertTrue(_shallow(clone), "얕은 클론이 아니다 — 이 검사가 아무것도 안 잰다")
            self.assertRegex(fac.source_tree("HEAD", paths=("app/src",), repo=clone), r"^[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
