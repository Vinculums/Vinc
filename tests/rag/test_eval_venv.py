# -*- coding: utf-8 -*-
"""G0 venv 의 판은 락에서만 나온다(`rag_eval.evalkit.venv.pins`)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rag_eval.evalkit import runtime, venv  # noqa: E402

RT = {"onnxruntime": "1.30.0", "tokenizers": "0.23.2", "numpy": "2.5.3"}


class PinsTests(unittest.TestCase):
    def test_the_lock_engine_and_runtime_are_pinned_exactly(self):
        got = venv.pins({"engine": "ladybug 0.20.3", "encoder_runtime": RT})
        self.assertEqual(got[:4], ["ladybug==0.20.3", "onnxruntime==1.30.0",
                                   "tokenizers==0.23.2", "numpy==2.5.3"])

    def test_without_an_engine_the_repo_pin_is_used(self):
        got = venv.pins({"encoder_runtime": RT})
        self.assertEqual(got[0], f"ladybug=={venv._repo_engine_pin()}")

    def test_a_missing_runtime_version_refuses(self):
        with self.assertRaises(SystemExit):
            venv.pins({"engine": "ladybug 0.20.3", "encoder_runtime": {"numpy": "2.5.3"}})

    def test_every_store_in_the_lock_resolves(self):
        import json
        for key in json.loads(runtime.LOCK.read_text(encoding="utf-8"))["stores"]:
            self.assertTrue(venv.pins(runtime.lock_entry(key))[0].startswith("ladybug=="), key)


if __name__ == "__main__":
    unittest.main()
