# -*- coding: utf-8 -*-
"""허브 인코더 런타임(onnxruntime·tokenizers·numpy)은 정확한 판으로 고정돼 있다(VIN-178).

질의 벡터와 저장된 벡터는 같은 런타임에서 나와야 한다. 같은 int8 bge-m3 모델이라도
onnxruntime 1.29 → 1.30 이면 질의 벡터 81개가 전부 코사인 0.009~0.022 움직였고, 핀
평가 스토어에서 확신 오답이 2 → 4 가 됐다. `Dockerfile.hub` 가 판 없이 설치하면 새 판이
나온 뒤의 첫 이미지 빌드가 질의 쪽만 조용히 바꾼다 — 에러는 없고 검색만 나빠진다.

이 테스트는 세 자리를 잠근다: 허브 이미지의 설치 줄이 판을 고정하는가, 모델을 굽는
bake-probe 가 판을 손으로 적지 않고 그 줄에서 뽑아 쓰는가, rerank-probe 의 추출이 고정한
줄을 여전히 찾는가.
근거: [[record:the-eval-drift-to-41-14-4-follows-the-query-encoder-runtime-not-the-engine-2026-09-28]]
"""
from __future__ import annotations

import re
import unittest
from unittest.mock import patch
from pathlib import Path

from store import encoder_runtime
from store import embedded_schema

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile.hub").read_text(encoding="utf-8")
WORKFLOWS = ROOT / ".github" / "workflows"

#: 벡터 값을 바꿀 수 있는 패키지. huggingface_hub 는 모델을 받기만 하지만 재현성을 위해
#: 같은 줄에서 함께 고정한다.
RUNTIME = ("onnxruntime", "tokenizers", "numpy", "huggingface_hub")


def _encoder_line() -> str:
    lines = [ln for ln in DOCKERFILE.splitlines()
             if ln.startswith("RUN pip install") and "onnxruntime" in ln]
    assert len(lines) == 1, f"Dockerfile.hub 에 onnxruntime 설치 줄이 {len(lines)}개다"
    return lines[0]


class EncoderRuntimePinTests(unittest.TestCase):
    def test_fingerprint_requires_all_three_runtime_packages(self):
        versions = {"onnxruntime": "1.30.0", "tokenizers": "0.23.2", "numpy": "2.5.3"}
        with patch.object(encoder_runtime, "version", side_effect=versions.__getitem__):
            self.assertEqual(encoder_runtime.fingerprint(),
                             "ort=1.30.0;tok=0.23.2;np=2.5.3")
        with patch.object(encoder_runtime, "version",
                          side_effect=encoder_runtime.PackageNotFoundError):
            self.assertEqual(encoder_runtime.fingerprint(), "")

    def test_v20_migrates_both_vector_tables_without_inventing_history(self):
        # v20 단계만 본다 — 뒤의 버전(v21 의 Chunk.lex 등)이 늘어도 이 계약은 그대로다.
        later = embedded_schema.migration_statements(20)
        v20 = [s for s in embedded_schema.migration_statements(19) if s not in later]
        self.assertEqual(v20, [
            "ALTER TABLE ChunkVec ADD encoder_runtime STRING DEFAULT ''",
            "ALTER TABLE NodeVec ADD encoder_runtime STRING DEFAULT ''",
        ])

    def test_허브_이미지가_인코더_런타임_판을_고정한다(self):
        line = _encoder_line()
        for pkg in RUNTIME:
            with self.subTest(pkg=pkg):
                self.assertRegex(line, rf"(?<![\w-]){pkg}==\d+(\.\d+)+\b",
                                 f"{pkg} 가 정확한 판(==)으로 고정돼 있지 않다: {line}")

    def test_bake_probe_는_판을_손으로_적지_않는다(self):
        text = (WORKFLOWS / "bake-probe.yml").read_text(encoding="utf-8")
        for ln in text.splitlines():
            if ln.lstrip().startswith("#"):
                continue
            with self.subTest(line=ln.strip()):
                # 판 없는 onnxruntime 설치는 허브와 다른 런타임으로 굽는다.
                self.assertIsNone(
                    re.search(r"pip install\b.*(?<![\w-])onnxruntime(?![\w=-])", ln),
                    "bake-probe 가 onnxruntime 을 판 없이 설치한다 — Dockerfile.hub 에서 뽑아 쓸 것")
        self.assertIn("onnxruntime==", text, "bake-probe 가 Dockerfile.hub 의 고정 판을 뽑지 않는다")

    def test_rerank_probe_추출이_고정한_줄을_찾는다(self):
        text = (WORKFLOWS / "rerank-probe.yml").read_text(encoding="utf-8")
        pat = re.search(r"grep -m1 -E '(\^RUN pip install \.\*onnxruntime[^']*)' Dockerfile\.hub", text)
        assert pat is not None, "rerank-probe 의 추출 패턴을 못 찾았다"
        self.assertRegex(_encoder_line(), pat.group(1))


if __name__ == "__main__":
    unittest.main()
