"""The encoder fingerprint names the GPU build of onnxruntime apart from the CPU build.

onnxruntime-gpu ships the same module under another distribution name. Before this, the fingerprint
came out empty on a GPU install and every query vector was refused as an unknown runtime.
"""
from __future__ import annotations

import unittest
from importlib.metadata import PackageNotFoundError
from unittest import mock

from store import encoder_runtime


def _versions(table):
    def version(name):
        if name in table:
            return table[name]
        raise PackageNotFoundError(name)
    return version


class FingerprintTest(unittest.TestCase):
    def test_cpu_build(self):
        with mock.patch.object(encoder_runtime, "version",
                               _versions({"onnxruntime": "1.30.0", "tokenizers": "0.23.2", "numpy": "2.5.3"})):
            self.assertEqual(encoder_runtime.fingerprint(), "ort=1.30.0;tok=0.23.2;np=2.5.3")

    def test_gpu_build_has_its_own_label(self):
        with mock.patch.object(encoder_runtime, "version",
                               _versions({"onnxruntime-gpu": "1.22.0", "tokenizers": "0.23.2", "numpy": "2.2.6"})):
            self.assertEqual(encoder_runtime.fingerprint(), "ort-gpu=1.22.0;tok=0.23.2;np=2.2.6")

    def test_missing_package_gives_no_fingerprint(self):
        with mock.patch.object(encoder_runtime, "version", _versions({"tokenizers": "0.23.2", "numpy": "2.5.3"})):
            self.assertEqual(encoder_runtime.fingerprint(), "")


if __name__ == "__main__":
    unittest.main()
