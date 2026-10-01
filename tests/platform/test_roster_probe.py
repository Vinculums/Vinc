# -*- coding: utf-8 -*-
"""R9 의 절반 — 로스터가 이 세션에서 보이는가. [[vinc.ops.graph-claim-decay]] §7.4.

나머지 절반(하네스가 세션 첫 줄에 스스로 찍는 것)은 이 리포가 못 낸다. 여기서 낼 수
있는 것은 **물으면 답하는 도구**이고, 그것이 필요한 이유는 [[record:agent-files-are-found-by-walking-up-from-cwd]]
가 이미 적어 둔 함정을 그 뒤에도 밟았기 때문이다: 잘못된 디렉터리에서 `ls` 한 결과를
보고 "로스터가 아예 없다"고 단정했다.

그래서 고정할 것은 개수가 아니라 **빈손의 두 뜻을 가르는 것**이다."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import roster_probe as rp  # noqa: E402


def _roster(root: Path, *names: str) -> Path:
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True)
    for n in names:
        (agents / f"{n}.md").write_text("---\n", encoding="utf-8")
    return agents


class WalkUpTests(unittest.TestCase):
    """전부 임시 디렉터리 안에서 한다. 이 리포가 어디 체크아웃됐는지(메인·`_wt/x`·
    `Vinc/.claude/worktrees/x`)가 결과를 바꾸면 안 된다 — 전의 '부모에서는 안 보인다'
    사례가 중첩 worktree 에서 늘 빨갰던 이유가 그것이다
    ([[record:roster-probe-test-fails-inside-a-nested-dev-worktree-2026-09-13]])."""

    def test_바로_위에_있으면_찾는다(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _roster(root)
            deep = root / "a" / "b"
            deep.mkdir(parents=True)
            found, walked = rp.walk_up(deep, stop=root)
            self.assertEqual(found, (root / ".claude" / "agents").resolve())
            self.assertGreaterEqual(len(walked), 3)

    def test_아래로는_안_내려간다(self):
        """이것이 함정 자체다. 로스터가 cwd 의 **자식**에 있으면 안 보인다 —
        로스터가 `Vinc/.claude/agents` 에 있던 동안 루트에서 연 세션이 이 모양이었다."""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "Vinc" / ".claude" / "agents").mkdir(parents=True)
            found, walked = rp.walk_up(root, stop=root)
            self.assertIsNone(found)
            self.assertIn(root.resolve(), walked)

    def test_못_찾아도_어디를_걸었는지_돌려준다(self):
        """'없다'와 '여기서는 안 보인다'를 가르는 것이 이 반환값이다. 경로가 없으면
        빈손이 곧 부재로 읽히고, 그 오독이 이미 한 번 기록으로 남았다."""
        with tempfile.TemporaryDirectory() as t:
            found, walked = rp.walk_up(Path(t), stop=Path(t))
            self.assertIsNone(found)
            self.assertEqual(walked, [Path(t).resolve()])


class MainTests(unittest.TestCase):
    def _run(self, arg, stop=None):
        import contextlib
        import functools
        import io
        from unittest import mock
        buf = io.StringIO()
        walk = functools.partial(rp.walk_up, stop=stop) if stop else rp.walk_up
        with contextlib.redirect_stdout(buf), mock.patch.object(rp, "walk_up", walk):
            rc = rp.main([str(arg)])
        return rc, buf.getvalue()

    def test_루트에_설치되면_그_아래_어느_체크아웃에서도_보인다(self):
        """새 배치의 요점. 로스터는 워크스페이스 루트에 한 벌이고, 메인 체크아웃·
        그 옆 `_wt/<name>`·메인 안에 중첩된 worktree 가 모두 걸어 올라가 같은 곳에 닿는다."""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            agents = _roster(root, "agent-steward", "agent-dev-core")
            for start in (root, root / "Vinc", root / "_wt" / "pkg" / "tests",
                          root / "Vinc" / ".claude" / "worktrees" / "pkg"):
                start.mkdir(parents=True, exist_ok=True)
                rc, out = self._run(start, stop=root)
                self.assertEqual(rc, 0, out)
                self.assertIn(f"찾았다: {agents.resolve()}", out)
                self.assertIn("껍데기 2개", out)
                self.assertIn(f"cwd: {start.resolve()}", out)

    def test_설치되지_않았으면_안_보이고_어디서_멈췄는지와_설치법을_말한다(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "Vinc").mkdir()
            rc, out = self._run(root / "Vinc", stop=root)
            self.assertEqual(rc, 1, out)
            self.assertIn("여기서는 안 보인다", out)
            self.assertIn(f"{root.resolve()} 에서 멈췄다", out)
            self.assertIn("agents_sync.py --install", out)

    def test_디렉터리가_아니면_2(self):
        rc, _ = self._run(_ROOT / "CLAUDE.md")
        self.assertEqual(rc, 2)


class TrackedRosterTests(unittest.TestCase):
    def test_추적되는_껍데기는_스물셋이다(self):
        """로스터의 실제 크기. 세는 자리를 여기 둔다. 리포가 추적하는 사본을 센다 —
        설치본은 리포 밖이라 테스트가 기대면 체크아웃 위치에 따라 결과가 갈린다.

        ⚠ 이 수의 이력: 2026-08-25 에는 실제로 열여섯인데 기록 두 곳이 열일곱이라 적어서
        틀렸고, 2026-08-30 에 agent-infra 가 늘어 진짜로 열일곱이 됐고, 2026-09-12 에
        agent-deploy 껍데기가 생겨 열여덟이 됐고, 2026-09-15 에 agent-qa 가 늘어
        열아홉이 됐고([[decision:agent-deploy-ratified-2026-09-11]]), 2026-09-16 로스터 v2 가
        셋을 더하고 하나를 흡수해 스물하나가 됐고([[decision:roster-v2-ratified-2026-09-16]]),
        2026-09-26 에 백오피스 지표 역할 둘이 늘어 스물셋이 됐다
        ([[decision:backoffice-metric-roles-added-2026-09-26]])."""
        from agents_sync import SHELL_DIR
        shells = sorted(p.stem for p in SHELL_DIR.glob("*.md"))
        self.assertEqual(len(shells), 23, shells)
        self.assertIn("agent-steward", shells)

    def test_리포_안에는_하네스가_찾는_로스터가_없다(self):
        """리포 안 `.claude/agents` 는 체크아웃의 브랜치를 따라 수가 달라지는 두 번째
        로스터였다. 추적 사본이 하네스 경로로 돌아오면 여기서 빨개진다."""
        self.assertFalse((_ROOT / ".claude" / "agents").exists())


class ClaudeMdTests(unittest.TestCase):
    def test_지시_파일이_이_도구와_설치_명령을_가리킨다(self):
        """도구가 있어도 세션이 그것을 모르면 R9 의 절반도 못 낸 것이다. CLAUDE.md 는
        루트에서 연 세션에도 import 로 실려 오므로, 함정을 밟는 자리에 닿는다."""
        text = (_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("roster_probe.py", text)
        self.assertIn("agents_sync.py --install", text)


if __name__ == "__main__":
    unittest.main()
