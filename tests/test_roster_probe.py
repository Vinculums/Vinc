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

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import roster_probe as rp  # noqa: E402


class WalkUpTests(unittest.TestCase):
    def test_바로_위에_있으면_찾는다(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / ".claude" / "agents").mkdir(parents=True)
            deep = root / "a" / "b"
            deep.mkdir(parents=True)
            found, walked = rp.walk_up(deep)
            self.assertEqual(found, (root / ".claude" / "agents").resolve())
            self.assertGreaterEqual(len(walked), 3)

    def test_아래로는_안_내려간다(self):
        """이것이 함정 자체다. 로스터가 cwd 의 **자식**에 있으면 안 보인다 —
        워크스페이스 루트에서 연 세션이 정확히 이 모양이다."""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "Vinc" / ".claude" / "agents").mkdir(parents=True)
            found, walked = rp.walk_up(root)
            self.assertIsNone(found)
            self.assertIn(root.resolve(), walked)

    def test_못_찾아도_어디를_걸었는지_돌려준다(self):
        """'없다'와 '여기서는 안 보인다'를 가르는 것이 이 반환값이다. 경로가 없으면
        빈손이 곧 부재로 읽히고, 그 오독이 이미 한 번 기록으로 남았다."""
        with tempfile.TemporaryDirectory() as t:
            found, walked = rp.walk_up(Path(t))
            self.assertIsNone(found)
            self.assertTrue(walked)
            self.assertEqual(walked[0], Path(t).resolve())


class MainTests(unittest.TestCase):
    def _run(self, arg):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = rp.main([str(arg)])
        return rc, buf.getvalue()

    def test_이_리포_안에서는_보이고_열일곱이다(self):
        """로스터의 실제 크기. 세는 자리를 여기 둔다.

        ⚠ 이 수가 열일곱인 것과, 기록이 한때 열일곱이라 **틀렸던** 것은 다른 일이다.
        2026-08-25 에는 실제로 열여섯인데 기록 두 곳이 열일곱이라 적어서 틀렸고,
        2026-08-30 에 agent-infra 가 늘어 진짜로 열일곱이 됐다. 우연히 같은 수라
        헷갈리기 쉬우므로 적어 둔다 — 옛 오류가 되돌아온 것이 아니다."""
        rc, out = self._run(_ROOT)
        self.assertEqual(rc, 0, out)
        self.assertIn("껍데기 17개", out)
        self.assertIn("agent-steward", out)

    def test_부모에서는_안_보이고_그렇게_말한다(self):
        rc, out = self._run(_ROOT.parent)
        self.assertEqual(rc, 1, out)
        self.assertIn("여기서는 안 보인다", out)
        self.assertIn("멈췄다", out)

    def test_디렉터리가_아니면_2(self):
        rc, _ = self._run(_ROOT / "CLAUDE.md")
        self.assertEqual(rc, 2)


class ClaudeMdTests(unittest.TestCase):
    def test_지시_파일이_이_도구를_가리킨다(self):
        """도구가 있어도 세션이 그것을 모르면 R9 의 절반도 못 낸 것이다. CLAUDE.md 는
        루트에서 연 세션에도 import 로 실려 오므로, 함정을 밟는 자리에 닿는다."""
        text = (_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("roster_probe.py", text)


if __name__ == "__main__":
    unittest.main()
