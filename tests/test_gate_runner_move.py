# -*- coding: utf-8 -*-
"""gate_runner_move 가 잡는 것과 안 잡는 것.

이 게이트의 값은 **좁게 정확한 것**에 있다. 직렬화하지 않는 워크플로까지 잡으면
사람이 게이트를 무시하는 법을 배우고, 그러면 게이트가 없는 것만 못하다. 그래서
경계 쪽 사례를 함께 고정한다.

진짜 저장소를 만들어 돌린다 — 이 스크립트가 `git show` 로 두 리비전을 읽는 것이
동작의 절반이라 그 부분을 흉내내면 검사할 것이 남지 않는다.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

_SERIAL = """\
name: w
on: {push: {branches: [main]}}
concurrency:
  group: ${{ github.workflow }}
  cancel-in-progress: false
jobs:
  deploy:
    runs-on: %s
    steps: [{run: 'true'}]
"""

_EXPR = """\
name: w
on: {push: {branches: [main]}}
concurrency:
  group: ${{ github.workflow }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  deploy:
    runs-on: %s
    steps: [{run: 'true'}]
"""

_NO_CONCURRENCY = """\
name: w
on: {push: {branches: [main]}}
jobs:
  deploy:
    runs-on: %s
    steps: [{run: 'true'}]
"""


class _Repo:
    """두 커밋짜리 임시 저장소. before 를 커밋하고 after 로 고쳐 다시 커밋한다."""

    def __init__(self, before: str, after: str | None, path: str = ".github/workflows/w.yml"):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._run("git", "init", "-q")
        self._run("git", "config", "user.email", "t@t")
        self._run("git", "config", "user.name", "t")
        f = self.root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(before, encoding="utf-8")
        self._run("git", "add", "-A")
        self._run("git", "commit", "-qm", "before")
        self.base = self._out("git", "rev-parse", "HEAD").strip()
        if after is None:
            f.unlink()
        else:
            f.write_text(after, encoding="utf-8")
        self._run("git", "add", "-A")
        self._run("git", "commit", "-qm", "after")
        self.head = self._out("git", "rev-parse", "HEAD").strip()

    def _run(self, *a):
        subprocess.run(a, cwd=self.root, check=True, capture_output=True)

    def _out(self, *a):
        return subprocess.run(a, cwd=self.root, capture_output=True, text=True).stdout

    def gate(self):
        r = subprocess.run(
            [sys.executable, "-X", "utf8", str(_ROOT / "scripts" / "gate_runner_move.py"),
             self.base, self.head],
            cwd=self.root, capture_output=True, text=True,
        )
        return r.returncode, r.stdout + r.stderr

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._tmp.cleanup()


class Catches(unittest.TestCase):
    def test_직렬화하는_워크플로의_러너가_바뀌면_잡는다(self):
        """Vinc#129 의 모양 그대로다."""
        with _Repo(_SERIAL % "[self-hosted, linux, vinc-local]",
                   _SERIAL % "ubuntu-latest") as r:
            rc, out = r.gate()
            self.assertEqual(rc, 1, out)
            self.assertIn("deploy", out)
            self.assertIn("큐를 비울 것", out)


class DoesNotCatch(unittest.TestCase):
    def test_표현식_cancel_in_progress_는_대상이_아니다(self):
        """ci.yml 의 모양. push 일 때 그룹 키에 sha 가 들어가 줄이 안 선다(#124)."""
        with _Repo(_EXPR % "[self-hosted, linux, vinc-local]",
                   _EXPR % "ubuntu-latest") as r:
            rc, out = r.gate()
            self.assertEqual(rc, 0, out)

    def test_concurrency_가_없으면_대상이_아니다(self):
        with _Repo(_NO_CONCURRENCY % "[self-hosted, linux, vinc-local]",
                   _NO_CONCURRENCY % "ubuntu-latest") as r:
            rc, out = r.gate()
            self.assertEqual(rc, 0, out)

    def test_러너가_그대로면_다른_곳이_바뀌어도_통과한다(self):
        """직렬화 워크플로를 고치는 것 자체는 막지 않는다. 러너 이동만 본다."""
        after = (_SERIAL % "ubuntu-latest").replace("steps: [{run: 'true'}]",
                                                    "steps: [{run: 'echo hi'}]")
        with _Repo(_SERIAL % "ubuntu-latest", after) as r:
            rc, out = r.gate()
            self.assertEqual(rc, 0, out)

    def test_새로_생긴_워크플로는_옮긴_것이_아니다(self):
        """before 에 없던 파일은 이동이 아니다 — 큐에 옛 런이 있을 수 없다."""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            run = lambda *a: subprocess.run(a, cwd=root, check=True, capture_output=True)
            run("git", "init", "-q")
            run("git", "config", "user.email", "t@t")
            run("git", "config", "user.name", "t")
            (root / "seed.txt").write_text("x", encoding="utf-8")
            run("git", "add", "-A")
            run("git", "commit", "-qm", "seed")
            base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                  capture_output=True, text=True).stdout.strip()
            f = root / ".github" / "workflows" / "w.yml"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(_SERIAL % "ubuntu-latest", encoding="utf-8")
            run("git", "add", "-A")
            run("git", "commit", "-qm", "add")
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                  capture_output=True, text=True).stdout.strip()
            r = subprocess.run(
                [sys.executable, "-X", "utf8",
                 str(_ROOT / "scripts" / "gate_runner_move.py"), base, head],
                cwd=root, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class ThisRepo(unittest.TestCase):
    def test_직렬화하는_워크플로가_실제로_둘이다(self):
        """게이트가 겨누는 대상이 이 리포에 실재하는지 고정한다. 늘거나 줄면
        이 테스트가 먼저 말한다 — 게이트가 조용히 아무것도 안 겨누게 두지 않는다."""
        import yaml
        serial = []
        for p in sorted((_ROOT / ".github" / "workflows").glob("*.yml")):
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
            c = (doc or {}).get("concurrency")
            if isinstance(c, dict) and c.get("cancel-in-progress") is False:
                serial.append(p.name)
        self.assertEqual(serial, ["hub-deploy.yml", "release.yml"], serial)


if __name__ == "__main__":
    unittest.main()
