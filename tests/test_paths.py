# -*- coding: utf-8 -*-
"""D43 — 설치형 기본 데이터 홈(~/.vinc) 해석 계약."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store.config import resolve_data_dir  # noqa: E402


class ResolveDataDirTests(unittest.TestCase):
    def test_default_is_home_dot_vinc_with_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}, clear=False), \
                 mock.patch("pathlib.Path.home", return_value=Path(tmp)):
                env = {k: v for k, v in os.environ.items() if k != "MEMORY_DATA_DIR"}
                with mock.patch.dict(os.environ, env, clear=True):
                    got = resolve_data_dir(None)
            self.assertEqual(got, str(Path(tmp) / ".vinc" / "db"))
            for sub in ("packs", "inbox", "backups"):
                self.assertTrue((Path(tmp) / ".vinc" / sub).is_dir(), sub)

    def test_env_wins_over_default_and_no_home_dirs_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"MEMORY_DATA_DIR": "/x/store"}), \
                 mock.patch("pathlib.Path.home", return_value=Path(tmp)):
                self.assertEqual(resolve_data_dir(None), "/x/store")
            self.assertFalse((Path(tmp) / ".vinc").exists())

    def test_cli_value_wins_over_env(self):
        with mock.patch.dict(os.environ, {"MEMORY_DATA_DIR": "/x/store"}):
            self.assertEqual(resolve_data_dir("/cli/db"), "/cli/db")
