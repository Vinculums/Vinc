# -*- coding: utf-8 -*-
"""WAL 재생이 엔진을 죽여도 사이드카는 뜬다(store/wal_guard.py).

2026-09-27 데스크톱 0.9.4 에서 실제로 났다: `~/.vinc/db.wal` 하나가 재생 도중 엔진을
네이티브로 죽였고(0xC0000409 · 접근 위반, 0.18.0 과 0.20.3 둘 다), 사이드카가 7초 만에
사라져 로그인 화면이 "연결을 확인하라"만 말했다. 여기서 못 박는 것은 셋이다:

- 네이티브 죽음만 죽음이다. 파이썬 쪽 실패(모듈 못 찾음 등)로 멀쩡한 WAL 을 치우면 안 된다.
- WAL 은 옮기지 지우지 않는다. 옮긴 뒤에도 죽으면 스토어 없이 뜬다.
- 허브는 점검하지 않는다(`--parent-pid` 없이 뜬다).
"""
from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from store import wal_guard

try:
    import ladybug as _DRIVER  # noqa: F401
except Exception:   # noqa: BLE001
    _DRIVER = None

#: 진짜 네이티브 죽음. Windows 는 0xC0000409(fail-fast, 그날 엔진이 낸 바로 그 코드),
#: POSIX 는 SIGABRT 다. `ctypes.string_at(0)` 은 안 된다: Windows 에서 ctypes 가 접근
#: 위반을 OSError 로 바꿔 1 로 끝난다.
CRASH = "import os; os.abort()"
NATIVE = 0xC0000409 if sys.platform == "win32" else -6


class _Run:
    """`subprocess.run` 대역: 부를 때마다 다음 종료 코드를 준다(예외면 던진다)."""

    def __init__(self, *codes):
        self.codes = list(codes)
        self.calls = 0

    def __call__(self, cmd, **kw):
        self.calls += 1
        code = self.codes.pop(0)
        if isinstance(code, BaseException):
            raise code
        return subprocess.CompletedProcess(cmd, code)


class GuardTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "db")
        self.wal = self.db + ".wal"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _wal(self, data=b"\x01" * 64):
        with open(self.wal, "wb") as fh:
            fh.write(data)

    def _aside(self):
        return [n for n in os.listdir(self.dir) if n.startswith("db.wal.corrupt-")]

    def test_WAL_이_없으면_자식을_안_띄운다(self):
        run = _Run()
        self.assertEqual(wal_guard.guard(self.db, run=run), wal_guard.Guarded())
        self._wal(b"")
        self.assertEqual(wal_guard.guard(self.db, run=run), wal_guard.Guarded())
        self.assertEqual(run.calls, 0)

    def test_자식이_열었으면_그대로_연다(self):
        self._wal()
        g = wal_guard.guard(self.db, run=_Run(0))
        self.assertTrue(g.open)
        self.assertEqual(g.note, "")
        self.assertEqual(self._aside(), [])

    def test_파이썬_쪽_실패는_죽음이_아니다(self):
        """1 = 잡히지 않은 예외(모듈을 못 찾는 것도 여기다) · 2 = 인자 · 3 = Windows CRT 의
        raise(SIGSEGV) · PROBE_RAISED = 열기 예외. 셋 다 판정하지 않는다.
        이걸 죽음으로 읽으면 멀쩡한 WAL 을 치워 체크포인트 안 된 쓰기를 버린다."""
        self._wal()
        for code in (1, 2, 3, wal_guard.PROBE_RAISED):
            g = wal_guard.guard(self.db, run=_Run(code))
            self.assertTrue(g.open, code)
            self.assertEqual(self._aside(), [], code)
        self.assertTrue(os.path.exists(self.wal))

    def test_판정_못_하면_안_건드린다(self):
        self._wal()
        for exc in (subprocess.TimeoutExpired("probe", 1), OSError("no exe")):
            g = wal_guard.guard(self.db, run=_Run(exc))
            self.assertTrue(g.open)
        self.assertTrue(os.path.exists(self.wal))

    def test_엔진이_죽으면_WAL_을_옮기고_연다(self):
        self._wal()
        run = _Run(NATIVE, 0)
        g = wal_guard.guard(self.db, run=run, now=lambda: 0)
        self.assertTrue(g.open)
        self.assertFalse(os.path.exists(self.wal))
        aside = self._aside()
        self.assertEqual(len(aside), 1)
        self.assertEqual(g.set_aside, os.path.join(self.dir, aside[0]))
        self.assertIn(g.set_aside, g.note)
        self.assertEqual(run.calls, 2)   # 옮긴 뒤 한 번 더 본다
        self.assertNotIn("—", g.note)    # /health 로 나가 화면에 닿는다

    def test_옮겨도_죽으면_스토어_없이_뜬다(self):
        self._wal()
        g = wal_guard.guard(self.db, run=_Run(NATIVE, NATIVE))
        self.assertFalse(g.open)
        self.assertEqual(len(self._aside()), 1)
        self.assertIn("열지 않고", g.note)

    def test_못_옮기면_열지_않는다(self):
        """옮기지 못했는데 열면 같은 재생이 사이드카를 죽인다."""
        self._wal()
        real = os.replace

        def refuse(src, dst):
            raise PermissionError(src)
        os.replace = refuse
        try:
            g = wal_guard.guard(self.db, run=_Run(NATIVE))
        finally:
            os.replace = real
        self.assertFalse(g.open)
        self.assertTrue(os.path.exists(self.wal))

    def test_진짜_네이티브_죽음을_죽음으로_읽는다(self):
        """대역이 아니라 실제로 죽는 자식으로 본다(종료 코드의 부호·폭이 OS 마다 다르다)."""
        self._wal()
        cmds = []

        def run(cmd, **kw):
            cmds.append(cmd)
            # 옮기기 전에는 죽고, 옮긴 뒤에는 연다.
            code = CRASH if os.path.exists(self.wal) else "raise SystemExit(0)"
            return subprocess.run([sys.executable, "-c", code], **kw)

        g = wal_guard.guard(self.db, run=run)
        self.assertTrue(g.open)
        self.assertEqual(len(self._aside()), 1)
        self.assertEqual(len(cmds), 2)

    def test_자식_명령은_동결본과_소스를_가른다(self):
        self.assertEqual(wal_guard.probe_command("x")[1:], ["-m", "api.cli", "store-probe", "--data", "x"])
        frozen = getattr(sys, "frozen", None)
        sys.frozen = True
        try:
            self.assertEqual(wal_guard.probe_command("x")[1:], ["store-probe", "--data", "x"])
        finally:
            if frozen is None:
                del sys.frozen
            else:
                sys.frozen = frozen


class ServeTest(unittest.TestCase):

    def test_이_기기의_사이드카만_점검한다(self):
        """셸과 MCP 브리지는 `--parent-pid` 로 띄우고 허브는 안 넘긴다. 허브의 WAL 은
        체크포인트 전의 공용 쓰기라 자동으로 치우지 않는다."""
        from api import server
        src = inspect.getsource(server.serve)
        self.assertIn("guard_wal=parent_pid is not None", src)
        entry = open(os.path.join(os.path.dirname(__file__), "..", "..", "packaging",
                                  "docker-entrypoint.sh"), encoding="utf-8").read()
        start = entry[entry.index("start_sidecar() {"):]
        self.assertNotIn("--parent-pid", start[: start.index("}")])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class AppOpenTest(unittest.TestCase):

    def setUp(self):
        from store.embedded_backend import EmbeddedBackend
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "db")
        self._saved = EmbeddedBackend.last_open_error
        self._probe = wal_guard.probe_command
        with open(self.db + ".wal", "wb") as fh:
            fh.write(b"\x01" * 64)

    def tearDown(self):
        from store.embedded_backend import EmbeddedBackend
        EmbeddedBackend.last_open_error = self._saved
        wal_guard.probe_command = self._probe
        shutil.rmtree(self.dir, ignore_errors=True)

    def _open(self):
        from api.server import VincApp
        from store.config import load_config
        return VincApp.open(load_config({"data_dir": self.db}), guard_wal=True)

    def test_죽는_WAL_은_옮기고_스토어를_연다(self):
        wal = self.db + ".wal"
        wal_guard.probe_command = lambda d: [
            sys.executable, "-c", f"import os\nif os.path.exists({wal!r}): {CRASH}"]
        app = self._open()
        try:
            self.assertIsNotNone(app.store)
            self.assertIn("corrupt-", app.store_note)
            self.assertFalse(os.path.exists(wal))
        finally:
            app.close()

    def test_스토어가_열리지_않아도_사이드카는_뜬다(self):
        """로그인(`/connect/start`)과 계정 통과로는 로컬 스토어 없이 된다. 사유는
        `/health` 와 조회 503 이 같이 쓰는 자리(`last_open_error`)로 간다."""
        from api.server import store_unavailable
        wal_guard.probe_command = lambda d: [sys.executable, "-c", CRASH]
        app = self._open()
        try:
            self.assertIsNone(app.store)
            self.assertIn("열지 않고", store_unavailable().get("open_error", ""))
        finally:
            app.close()


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ProbeCommandTest(unittest.TestCase):

    def test_store_probe_는_열고_닫고_0_을_낸다(self):
        """자식이 닫는 것이 곧 체크포인트다. 0 이면 부모는 WAL 없이 연다."""
        d = tempfile.mkdtemp()
        try:
            db = os.path.join(d, "db")
            root = os.path.join(os.path.dirname(__file__), "..", "..")
            res = subprocess.run(wal_guard.probe_command(db), cwd=root,
                                 capture_output=True, timeout=120)
            self.assertEqual(res.returncode, 0, res.stderr[-400:])
            self.assertTrue(os.path.exists(db))
            self.assertFalse(os.path.exists(db + ".wal"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
