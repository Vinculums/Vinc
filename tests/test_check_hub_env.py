# -*- coding: utf-8 -*-
"""허브 env 게이트 검증 — scripts/check_hub_env.py.

이 게이트가 존재하는 이유가 곧 이 테스트의 이유다: `VINC_MCP_ISSUER` 하나가 빠져
커넥터가 전면 불가였는데 `/health` 는 200 이었다. 게이트가 **조용히 통과**하면
그 사고가 그대로 재현된다 — 그래서 "누락이 실제로 잡히는가"를 못 박는다.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_hub_env.py"


def _load():
    spec = importlib.util.spec_from_file_location("_vinc_check_hub_env", _SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class CheckHubEnvTest(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def _env(self, names):
        # Azure 는 secretRef 항목도 value 없이 name 만 준다 — 그 형태를 그대로 흉내낸다.
        return [{"name": n, "value": ""} for n in names]

    def test_전부_있으면_통과(self):
        names = list(self.m.REQUIRED) + list(self.m.EXPECTED)
        self.assertEqual(self.m.check(self._env(names)), 0)

    def test_ISSUER_가_빠지면_반드시_실패한다(self):
        """실제 사고가 이것이다. 이 케이스가 통과하면 게이트가 무의미하다."""
        names = [n for n in self.m.REQUIRED if n != "VINC_MCP_ISSUER"]
        self.assertEqual(self.m.check(self._env(names)), 1)

    def test_필수값_각각이_독립적으로_잡힌다(self):
        """하나만 빼도 잡혀야 한다 — 특정 항목만 검사하는 구현 실수를 막는다."""
        for k in self.m.REQUIRED:
            with self.subTest(missing=k):
                names = [n for n in self.m.REQUIRED if n != k]
                self.assertEqual(self.m.check(self._env(names)), 1, f"{k} 누락이 안 잡혔다")

    def test_권고값_누락은_경고일_뿐_배포를_막지_않는다(self):
        """VINC_QUOTA 는 없어도 기동한다 — 이걸로 배포를 막으면 게이트가 과하다."""
        self.assertEqual(self.m.check(self._env(list(self.m.REQUIRED))), 0)

    def test_빈_배열은_실패(self):
        self.assertEqual(self.m.check([]), 1)


if __name__ == "__main__":
    unittest.main()
