# -*- coding: utf-8 -*-
"""허브 env 게이트 검증 — scripts/check_hub_env.py.

이 게이트가 존재하는 이유가 곧 이 테스트의 이유다: `VINC_MCP_ISSUER` 하나가 빠져
커넥터가 전면 불가였는데 `/health` 는 200 이었다. 게이트가 **조용히 통과**하면
그 사고가 그대로 재현된다 — 그래서 "누락이 실제로 잡히는가"를 못 박는다.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
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

    def _check(self, env):
        """`check()` 를 돌리되 stderr 를 **가둬서** 돌려준다.

        ⚠ 가두지 않으면 CI 가 오염된다: `check()` 는 누락을 `::error::` 로 찍는데,
        그건 GitHub Actions 의 워크플로 명령이라 **테스트가 통과해도 잡 주석에
        "10 errors" 로 뜬다**(2026-08-01 실측 — 이 테스트가 일부러 누락을 넣어
        돌리므로 매 실행마다 나왔다). 빨간 주석이 상시로 떠 있으면 사람이 진짜
        오류를 무시하게 되고, 그게 이 게이트가 막으려는 사고와 정확히 같은 형태다.

        덤으로 메시지 내용까지 검증할 수 있게 돌려준다 — 종료코드만 보면
        "무엇이 빠졌는지" 잘못 말하는 회귀를 놓친다.
        """
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code = self.m.check(env)
        return code, buf.getvalue()

    def test_전부_있으면_통과(self):
        names = list(self.m.REQUIRED) + list(self.m.EXPECTED)
        code, _ = self._check(self._env(names))
        self.assertEqual(code, 0)

    def test_ISSUER_가_빠지면_반드시_실패한다(self):
        """실제 사고가 이것이다. 이 케이스가 통과하면 게이트가 무의미하다."""
        names = [n for n in self.m.REQUIRED if n != "VINC_MCP_ISSUER"]
        code, err = self._check(self._env(names))
        self.assertEqual(code, 1)
        # 종료코드만이 아니라 **그 키를 지목하는지**까지 본다.
        self.assertIn("VINC_MCP_ISSUER", err)

    def test_필수값_각각이_독립적으로_잡힌다(self):
        """하나만 빼도 잡혀야 한다 — 특정 항목만 검사하는 구현 실수를 막는다."""
        for k in self.m.REQUIRED:
            with self.subTest(missing=k):
                names = [n for n in self.m.REQUIRED if n != k]
                code, err = self._check(self._env(names))
                self.assertEqual(code, 1, f"{k} 누락이 안 잡혔다")
                self.assertIn(k, err, f"{k} 누락인데 메시지가 그 키를 안 지목한다")

    def test_권고값_누락은_경고일_뿐_배포를_막지_않는다(self):
        """VINC_QUOTA 는 없어도 기동한다 — 이걸로 배포를 막으면 게이트가 과하다."""
        code, _ = self._check(self._env(list(self.m.REQUIRED)))
        self.assertEqual(code, 0)

    def test_빈_배열은_실패(self):
        code, _ = self._check([])
        self.assertEqual(code, 1)

    # ── VINC_MCP_STATE_DIR 경로 검증(2026-08-03 unknown client_id 사고) ──────
    def _with(self, name, value):
        return [{"name": n, "value": ""} for n in self.m.REQUIRED] + \
               [{"name": name, "value": value}]

    def test_상태경로_미설정은_정상이다(self):
        """엔트리포인트가 둘 다 기본값을 주므로 env 부재가 정상이다 — 여기서
        실패시키면 멀쩡한 배포가 막힌다."""
        code, _ = self._check(self._env(list(self.m.REQUIRED)))
        self.assertEqual(code, 0)

    def test_작업본이_로컬이고_스냅샷이_영속볼륨이면_통과(self):
        env = [{"name": n, "value": ""} for n in self.m.REQUIRED] + [
            {"name": "VINC_MCP_STATE_DIR", "value": "/var/lib/vinc-mcp"},
            {"name": "VINC_MCP_STATE_SNAPSHOT", "value": "/data/mcp/oauth.db"}]
        code, err = self._check(env)
        self.assertEqual(code, 0, err)

    def test_작업본이_영속볼륨이면_배포를_막는다(self):
        """**사고 ② 재발 지점.** SQLite 를 SMB(/data)에 올리면 첫 쓰기가
        `database is locked` 로 죽어 MCP 가 기동하지 못한다(허브 전면 장애)."""
        for bad in ("/data/mcp", "/data/mcp/", "/data/state"):
            with self.subTest(path=bad):
                code, err = self._check(self._with("VINC_MCP_STATE_DIR", bad))
                self.assertEqual(code, 1, f"{bad} 가 통과했다")
                self.assertIn("VINC_MCP_STATE_DIR", err)
                self.assertIn("database is locked", err)   # 왜 막는지까지 말한다

    def test_스냅샷이_영속볼륨_밖이면_배포를_막는다(self):
        """**사고 ① 재발 지점.** 스냅샷이 이미지 레이어에 있으면 배포마다 등록이
        사라져 커넥터가 400 으로 끊긴다."""
        for bad in ("/root/.vinc/mcp/oauth.db", "/tmp/oauth.db"):
            with self.subTest(path=bad):
                code, err = self._check(self._with("VINC_MCP_STATE_SNAPSHOT", bad))
                self.assertEqual(code, 1, f"{bad} 가 통과했다")
                self.assertIn("unknown client_id", err)

    def test_경로가_비면_막는다(self):
        """secretRef 로 넣으면 값이 안 보여 검증이 불가능하다 — 통과시키면 게이트가
        있으나 마나다."""
        for key in ("VINC_MCP_STATE_DIR", "VINC_MCP_STATE_SNAPSHOT"):
            with self.subTest(key=key):
                code, err = self._check(self._with(key, ""))
                self.assertEqual(code, 1)
                self.assertIn(key, err)

    def test_경로오류만으로_막힐_때_사유를_정확히_말한다(self):
        """'0개 누락'으로 끝나면 사람이 없는 누락을 찾아 헤맨다."""
        _, err = self._check(self._with("VINC_MCP_STATE_DIR", "/data/x"))
        self.assertIn("값 오류 1건", err)
        self.assertNotIn("필수값 0개 누락", err)

    def test_게이트_출력이_CI_주석을_오염시키지_않는다(self):
        """`::error::` 가 테스트 실행 중 실제 stderr 로 새지 않아야 한다.

        위 `_check` 를 우회해 직접 부르는 테스트가 나중에 추가되면 다시 오염된다.
        그때 이 테스트가 아니라 그 테스트가 통과해버리므로, 여기서는 최소한
        "가두면 안 샌다"는 계약만 못 박는다.
        """
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.m.check([])
        self.assertIn("::error::", buf.getvalue())  # 메시지 자체는 나와야 하고



if __name__ == "__main__":
    unittest.main()
