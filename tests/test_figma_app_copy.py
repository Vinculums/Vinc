# -*- coding: utf-8 -*-
"""figma_app_copy — 앱 보드(캡처)의 신선도 판정. 2026-09-06 에 글자 대조에서 리비전 대조로 바뀌었다."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

HUB = pathlib.Path(__file__).resolve().parents[1] / "design-hub"
_spec = importlib.util.spec_from_file_location("figma_app_copy", HUB / "scripts" / "figma_app_copy.py")
fac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fac)


class CheckTests(unittest.TestCase):
    def test_no_changes_after_the_capture_passes(self):
        r = fac.check({"sourceRev": "abc1234", "states": [{"id": "view__3d"}]}, [])
        self.assertTrue(r["ok"]); self.assertEqual(r["states"], 1)

    def test_a_commit_touching_app_src_after_the_capture_fails(self):
        r = fac.check({"sourceRev": "abc1234", "states": []}, ["deadbee fix(view): reorder lens rows"])
        self.assertFalse(r["ok"]); self.assertEqual(len(r["changes"]), 1)

    def test_a_manifest_without_a_revision_fails_by_itself(self):
        r = fac.check({"states": []}, [])
        self.assertFalse(r["ok"]); self.assertIn("sourceRev", r["reason"])


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
        changes = fac.changes_since(m["sourceRev"])
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


if __name__ == "__main__":
    unittest.main()
