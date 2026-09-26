# -*- coding: utf-8 -*-
"""`design-hub/scripts/sync_out.py` 가 소비 리포를 **git 체크아웃으로만** 찾고,
내용이 같은 사본은 다시 쓰지 않는가.

두 고장을 막는다(2026-09-26 실측):

- worktree(`_wt/<이름>`) 에서 `--apply` 를 돌리면 `../../Vinc-homepage` 가
  `_wt/Vinc-homepage` 로 풀린다. 리포가 아닌 그 자리를 조용히 만들고 사본을 쏟았다.
- 내용이 같아도 매번 LF 로 다시 써서, autocrlf 체크아웃(CRLF)의 앱 쪽 사본이
  줄끝만 바뀐 채 git 에 수정으로 떴다.

픽스처는 진짜 git 리포와 worktree 다. `--git-common-dir` 로 메인 워크트리를 찾는
경로를 흉내 내면 그 경로가 실제로 도는지를 못 본다.
근거: [[record:sync-out-in-a-worktree-wrote-the-homepage-copies-into-a-stray-directory-2026-09-26]]
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
          / "design-hub" / "scripts" / "sync_out.py")

CSS = "/* AUTO-GENERATED */\n:root { --a: 1; }\n"
PNG = bytes([137, 80, 78, 71, 13, 10, 26, 10]) + b"fake"

MAP = [
    ("dist/site/site-tokens.css", "../../Vinc-homepage/site/assets/tokens.css"),
    ("dist/app/tokens.desktop.css", "../app/src/tokens.desktop.css"),
    ("assets/logo/mark.png", "../app/public/mark.png"),
]


def _load():
    spec = importlib.util.spec_from_file_location("_vinc_sync_out", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(*args: str, cwd: pathlib.Path) -> None:
    # 사용자 설정(서명·훅·autocrlf)이 픽스처에 새어 들지 않게 전역·시스템 설정을 끈다.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


@unittest.skipIf(shutil.which("git") is None, "git 이 없다")
class _Workspace(unittest.TestCase):
    """ws/Vinc(메인) · ws/_wt/cap(worktree) · 선택적으로 ws/Vinc-homepage."""

    def setUp(self):
        # 훅 안에서 돌면 GIT_DIR 이 바깥 리포를 가리킨다 — 스크립트의 rev-parse 가 그걸 읽는다.
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for k in [k for k in os.environ if k.startswith("GIT_")]:
            del os.environ[k]

        self.ws = pathlib.Path(tempfile.mkdtemp(prefix="sync-out-")).resolve()
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        self.main = self.ws / "Vinc"
        self.main.mkdir()
        _git("init", "-q", cwd=self.main)
        _git("commit", "-q", "--allow-empty", "-m", "init", cwd=self.main)
        self.wt = self.ws / "_wt" / "cap"
        _git("worktree", "add", "-q", "--detach", str(self.wt), cwd=self.main)

        self.home = self.ws / "Vinc-homepage"
        self.stray = self.ws / "_wt" / "Vinc-homepage"   # 옛 판이 만들던 자리

    def hub(self, checkout: pathlib.Path) -> pathlib.Path:
        hub = checkout / "design-hub"
        for rel, data in (("dist/site/site-tokens.css", CSS),
                          ("dist/app/tokens.desktop.css", CSS),
                          ("assets/logo/mark.png", PNG)):
            p = hub / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(data, bytes):
                p.write_bytes(data)
            else:
                p.write_text(data, encoding="utf-8", newline="")
        return hub

    def homepage(self) -> None:
        self.home.mkdir()
        _git("init", "-q", cwd=self.home)

    def run_sync(self, hub: pathlib.Path, *argv: str):
        mod = _load()
        mod.HUB, mod.MAP = hub, list(MAP)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mod.main(list(argv))
        return rc, out.getvalue(), err.getvalue()


class ConsumerMustBeACheckout(_Workspace):

    def test_worktree_writes_to_the_main_worktrees_sibling(self):
        self.homepage()
        rc, out, err = self.run_sync(self.hub(self.wt), "--apply")
        self.assertEqual(rc, 0, out + err)
        self.assertFalse(self.stray.exists(), "worktree 옆에 소비 리포 디렉터리를 만들었다")
        written = self.home / "site" / "assets" / "tokens.css"
        self.assertTrue(written.is_file(), out)
        self.assertIn("sync-sha256", written.read_text(encoding="utf-8"))

    def test_a_stray_directory_is_not_taken_for_the_repo(self):
        # 옛 판이 남긴 껍데기가 있어도 그것은 리포가 아니다.
        (self.stray / "site" / "assets").mkdir(parents=True)
        self.homepage()
        rc, out, err = self.run_sync(self.hub(self.wt), "--apply")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(list((self.stray / "site" / "assets").iterdir()), [])
        self.assertTrue((self.home / "site" / "assets" / "tokens.css").is_file())
        self.assertIn("git 체크아웃이 아니다", out)

    def test_main_checkout_uses_the_plain_relative_path(self):
        self.homepage()
        rc, out, err = self.run_sync(self.hub(self.main), "--apply")
        self.assertEqual(rc, 0, out + err)
        self.assertTrue((self.home / "site" / "assets" / "tokens.css").is_file())

    def test_no_checkout_anywhere_refuses_and_writes_nothing(self):
        hub = self.hub(self.wt)
        rc, out, err = self.run_sync(hub, "--apply")
        self.assertNotEqual(rc, 0)
        self.assertIn("Vinc-homepage", err)
        self.assertIn("[거절]", err)
        self.assertFalse(self.stray.exists(), "거절하면서 디렉터리를 만들었다")
        self.assertFalse(self.home.exists(), "거절하면서 디렉터리를 만들었다")
        # 반만 나른 상태도 남기지 않는다 — 같은 리포 안의 사본도 안 쓴다.
        self.assertFalse((self.wt / "app").exists(), out)

    def test_check_skips_a_missing_repo_without_creating_it(self):
        # CI 는 Vinc 하나만 체크아웃한다. 그 계약(건너뛰되 센다)은 그대로다.
        self.homepage()
        hub = self.hub(self.wt)
        self.run_sync(hub, "--apply")
        shutil.rmtree(self.home, ignore_errors=True)
        rc, out, err = self.run_sync(hub, "--check")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("[SKIP·리포 없음]", out)
        self.assertFalse(self.stray.exists())
        self.assertFalse(self.home.exists())


class SameContentIsNotRewritten(_Workspace):

    def setUp(self):
        super().setUp()
        self.homepage()
        self.hub_dir = self.hub(self.wt)
        rc, out, err = self.run_sync(self.hub_dir, "--apply")
        self.assertEqual(rc, 0, out + err)
        self.css = self.wt / "app" / "src" / "tokens.desktop.css"
        self.png = self.wt / "app" / "public" / "mark.png"

    def _age(self, p: pathlib.Path) -> int:
        os.utime(p, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
        return p.stat().st_mtime_ns

    def test_crlf_copy_with_the_same_content_is_left_alone(self):
        # autocrlf 체크아웃이 만드는 모양 그대로: 내용은 같고 줄끝만 CRLF.
        crlf = self.css.read_bytes().replace(b"\n", b"\r\n")
        self.css.write_bytes(crlf)
        mtime = self._age(self.css)
        rc, out, err = self.run_sync(self.hub_dir, "--apply")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.css.read_bytes(), crlf, "줄끝만 바꿔 다시 썼다")
        self.assertEqual(self.css.stat().st_mtime_ns, mtime)
        self.assertIn("[SAME] ../app/src/tokens.desktop.css", out)

    def test_identical_binary_is_left_alone(self):
        mtime = self._age(self.png)
        self.run_sync(self.hub_dir, "--apply")
        self.assertEqual(self.png.stat().st_mtime_ns, mtime)

    def test_a_real_change_is_still_written(self):
        # 위 두 건이 "아무것도 안 쓴다"로 공허하게 통과하지 않는다는 대조.
        (self.hub_dir / "dist" / "app" / "tokens.desktop.css").write_text(
            CSS.replace("1", "2"), encoding="utf-8", newline="")
        (self.hub_dir / "assets" / "logo" / "mark.png").write_bytes(PNG + b"!")
        self.run_sync(self.hub_dir, "--apply")
        self.assertIn("--a: 2", self.css.read_text(encoding="utf-8"))
        self.assertEqual(self.png.read_bytes(), PNG + b"!")


if __name__ == "__main__":
    unittest.main()
