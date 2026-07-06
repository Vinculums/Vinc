# -*- coding: utf-8 -*-
"""로컬 로그인 — 세션 토큰 검증 + argon2id 자격(설계 §3-1, F4: 네트워크 왕복 없음)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import auth  # noqa: E402

try:
    import argon2  # noqa: F401
    _HAS_ARGON2 = True
except ImportError:
    _HAS_ARGON2 = False


class TokenTests(unittest.TestCase):
    def test_issue_token_unique_urlsafe(self):
        a, b = auth.issue_token(), auth.issue_token()
        self.assertNotEqual(a, b)
        self.assertTrue(a.replace("-", "").replace("_", "").isalnum())

    def test_check_header_accepts_matching_bearer(self):
        sa = auth.SessionAuth("tok123")
        self.assertTrue(sa.check_header("Bearer tok123"))
        self.assertTrue(sa.check_header("bearer tok123"))   # 스킴 대소문자 무관

    def test_check_header_rejects(self):
        sa = auth.SessionAuth("tok123")
        for bad in (None, "", "tok123", "Bearer wrong", "Basic tok123", "Bearer"):
            self.assertFalse(sa.check_header(bad), bad)


class NoNetworkTests(unittest.TestCase):
    """F4 — 로그인 경로는 소켓을 절대 열지 않는다. 소켓을 막아 두고 통과함을 보인다."""

    def setUp(self):
        import socket
        self._orig = socket.socket

        def _blocked(*a, **k):
            raise AssertionError("로그인 경로가 소켓을 열었다 — F4 위반(네트워크 왕복 없음)")

        socket.socket = _blocked
        self.addCleanup(lambda: setattr(socket, "socket", self._orig))

    def test_token_path_no_socket(self):
        sa = auth.SessionAuth(auth.issue_token())
        self.assertTrue(sa.check_header(f"Bearer {sa.token}"))

    @unittest.skipIf(not _HAS_ARGON2, "argon2 extra 미설치 — 자격 검증 skip")
    def test_passphrase_roundtrip_no_socket(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "auth.json"
            auth.create_profile("s3cret-phrase", path=path)
            self.assertTrue(auth.verify_passphrase("s3cret-phrase", path=path))
            self.assertFalse(auth.verify_passphrase("wrong", path=path))


@unittest.skipIf(not _HAS_ARGON2, "argon2 extra 미설치 — 온보딩 skip")
class ProfileTests(unittest.TestCase):
    def test_create_then_verify(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "auth.json"
            out = auth.create_profile("pw12345", path=path)
            self.assertEqual(out["algo"], "argon2id")
            self.assertTrue(Path(out["created"]).exists())
            self.assertTrue(auth.verify_passphrase("pw12345", path=path))

    def test_no_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "auth.json"
            auth.create_profile("pw12345", path=path)
            with self.assertRaises(FileExistsError):
                auth.create_profile("other", path=path)
            auth.create_profile("other", path=path, force=True)   # force → 교체
            self.assertTrue(auth.verify_passphrase("other", path=path))

    def test_empty_passphrase_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                auth.create_profile("", path=Path(d) / "auth.json")

    def test_verify_missing_file_is_false(self):
        self.assertFalse(auth.verify_passphrase("x", path="/nonexistent/auth.json"))


@unittest.skipIf(_HAS_ARGON2, "argon2 설치됨 — 미설치 강등 경로만 대상")
class ArgonMissingTests(unittest.TestCase):
    def test_create_profile_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(auth.AuthUnavailable):
                auth.create_profile("x", path=Path(d) / "auth.json")


if __name__ == "__main__":
    unittest.main()
