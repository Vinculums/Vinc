# -*- coding: utf-8 -*-
"""양자화 결과 크기 가드가 모델마다 다른 정본을 받는가 · scripts/quantize_e5.py.

왜 이걸 재나: 가드는 "다운로드가 조용히 잘렸다"를 잡으려고 200MB 를 상수로 박아 뒀는데,
그 값은 e5 계열(278MB)의 정본이다. 리랭커 118M 은 int8 이 ~120MB 라 **양자화에 성공하고도
FATAL 로 죽는다**. 그러면 아티팩트가 없어서 실험이 시작조차 못 되고, 원인은 "너무 작다"는
메시지뿐이라 잘린 다운로드와 구분이 안 된다. --min-size 가 두 자리(조기 반환·FATAL) 모두에
걸리는지 확인한다. 모델도 네트워크도 없이 돈다 — 가짜 모듈을 sys.modules 에 꽂는다.
"""
from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def _fake_deps(written_bytes: int):
    """hf_hub_download · quantize_dynamic 을 가짜로. 쓰는 파일 크기만 우리가 정한다."""
    src = tempfile.TemporaryDirectory()
    fp32 = pathlib.Path(src.name) / "model.onnx"
    fp32.write_bytes(b"\0")

    hub = types.ModuleType("huggingface_hub")
    hub.hf_hub_download = lambda repo, f, cache_dir=None: str(fp32)
    hub.list_repo_files = lambda repo: ["onnx/model.onnx"]

    quant = types.ModuleType("onnxruntime.quantization")
    quant.QuantType = types.SimpleNamespace(QInt8="qint8")
    quant.quantize_dynamic = lambda a, out, **kw: pathlib.Path(out).write_bytes(
        b"\0" * written_bytes)
    ort = types.ModuleType("onnxruntime")
    ort.quantization = quant

    saved = {k: sys.modules.get(k) for k in
             ("huggingface_hub", "onnxruntime", "onnxruntime.quantization")}
    sys.modules.update({"huggingface_hub": hub, "onnxruntime": ort,
                        "onnxruntime.quantization": quant})
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        src.cleanup()


class MinSizeGuardTests(unittest.TestCase):
    def setUp(self):
        self.m = _load("quantize_e5")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = str(pathlib.Path(self.tmp.name) / "art.onnx")

    def _run(self, argv):
        saved, sys.argv = sys.argv, ["quantize_e5.py", *argv]
        try:
            return self.m.main()
        finally:
            sys.argv = saved

    def test_기본값은_e5_정본이라_작은_결과를_실패로_본다(self):
        with _fake_deps(1024 * 1024):                      # 1MB
            self.assertEqual(self._run(["--out", self.out, "--keep-fp32"]), 1)

    def test_모델_정본을_주면_같은_결과가_통과한다(self):
        with _fake_deps(1024 * 1024):
            self.assertEqual(
                self._run(["--out", self.out, "--keep-fp32", "--min-size", "1000"]), 0)

    def test_이미_있으면_다시_굽지_않는_문턱도_같은_값을_쓴다(self):
        pathlib.Path(self.out).write_bytes(b"\0" * (1024 * 1024))
        with _fake_deps(1):                                # 다시 구우면 1바이트 = 실패
            self.assertEqual(
                self._run(["--out", self.out, "--min-size", "1000"]), 0)
        self.assertEqual(pathlib.Path(self.out).stat().st_size, 1024 * 1024)

    def test_기본값이_바뀌면_알린다(self):
        self.assertEqual(self.m._EXPECT_INT8_MIN, 200 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
