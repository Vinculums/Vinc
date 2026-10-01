# -*- coding: utf-8 -*-
"""인코드 프로브 요약기 — 워크플로가 한 번밖에 못 도는 것을 여기서 미리 태운다.

이 파서가 인라인 파이썬이던 동안에는 테스트가 불가능했고, 그동안 프로브 런 둘이 아무
수치도 못 내고 죽었다. 계산이 틀렸어도 알 방법이 없었다는 뜻이다.

여기서 못 박는 것은 셋이다: **실패한 호출이 분포에 안 섞인다**(섞이면 빨리 실패한 호출이
지연을 낮춰 초록인 채로 좋아 보인다) · **첫 호출이 웜 분포에 안 섞인다**(콜드 하나가 p95 를
끌어간다) · **잴 것을 못 쟀으면 죽는다**(수치 없이 초록인 런이 이 리포에 실제로 있었다).

근거: [[record:the-probe-payload-outgrew-the-exec-command-limit-2026-09-08]]
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from summarize_encode_probe import parse, pct, render  # noqa: E402

HUB_OK = """PROBE_CPU Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz
PROBE_NPROC 4
PROBE_FLAGS fma avx avx2 avx512f avx512_vnni
PROBE_ENCODE 0 200 26.551
PROBE_ENCODE 1 200 0.060
PROBE_ENCODE 2 200 0.070
PROBE_ENCODE 3 200 0.050
PROBE_BATCH 0 200 5.000 8
PROBE_BATCH 1 200 6.000 8
PROBE_DONE
"""

ENC_OK = """model name\t: Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz
PROBE_RSS_N 6
PROBE_RSS_MAX_KB 962416
PROBE_RSS_SUM_KB 1048576
"""


class Parsing(unittest.TestCase):
    def test_the_cold_first_call_is_kept_out_of_the_warm_distribution(self):
        d = parse(HUB_OK, ENC_OK)
        self.assertAlmostEqual(d["first_ms"], 26551, places=0)
        self.assertEqual(d["warm"]["n"], 3, "첫 호출이 웜 분포에 섞였다")
        self.assertLess(d["warm"]["p95"], 100, "콜드가 p95 를 끌어갔다")
        self.assertEqual(d["warm"]["min"], 50.0)
        self.assertEqual(d["warm"]["max"], 70.0)

    def test_failed_calls_are_not_counted(self):
        """빨리 실패한 호출이 분포에 섞이면 지연이 **좋아 보인다**. 그 모양이 위험하다."""
        hub = HUB_OK.replace("PROBE_ENCODE 2 200 0.070", "PROBE_ENCODE 2 503 0.001")
        hub = hub.replace("PROBE_BATCH 1 200 6.000 8", "PROBE_BATCH 1 500 0.002 8")
        d = parse(hub, ENC_OK)
        self.assertEqual(d["ok"], 3)
        self.assertEqual(d["calls"], 4)
        self.assertEqual(d["warm"]["n"], 2)
        self.assertNotIn(1.0, [d["warm"]["min"], d["warm"]["max"]])
        self.assertEqual(d["batches"], 1)
        self.assertEqual(d["batch"]["texts"], 8)

    def test_throughput_is_texts_over_seconds(self):
        d = parse(HUB_OK, ENC_OK)
        self.assertEqual(d["batch"]["texts"], 16)
        self.assertAlmostEqual(d["batch"]["secs"], 11.0, places=3)
        self.assertAlmostEqual(d["batch"]["per_s"], 16 / 11.0, places=6)
        self.assertAlmostEqual(d["batch"]["ms_per_text"], 11000 / 16, places=3)

    def test_rss_is_read_from_the_encoder_side_and_sits_against_2_gib(self):
        d = parse(HUB_OK, ENC_OK)
        self.assertEqual(d["rss_n"], 6)
        self.assertEqual(d["rss_max_kb"], 962416)
        rows = render(d, "t")
        self.assertTrue(any("50% · 프로세스 6개" in r for r in rows),
                        "합계가 2GiB 대비 비율로 안 나온다")

    def test_a_run_with_no_encoder_output_still_renders(self):
        d = parse(HUB_OK, "")
        self.assertIsNone(d["rss_max_kb"])
        rows = render(d, "t")
        self.assertTrue(any("패시지 처리량" in r for r in rows))


class Percentile(unittest.TestCase):
    def test_empty_is_none_and_single_is_itself(self):
        self.assertIsNone(pct([], 0.5))
        self.assertEqual(pct([7.0], 0.95), 7.0)

    def test_p95_does_not_run_off_the_end(self):
        self.assertEqual(pct([1.0, 2.0, 3.0, 4.0], 0.95), 4.0)


class ExitCode(unittest.TestCase):
    def _run(self, hub_text):
        import tempfile

        from summarize_encode_probe import main
        f = tempfile.NamedTemporaryFile("w", suffix=".out", delete=False, encoding="utf-8")
        f.write(hub_text)
        f.close()
        return main([f.name])

    def test_a_run_that_measured_nothing_fails(self):
        self.assertEqual(self._run("PROBE_CPU x\nPROBE_NPROC 4\n"), 1)

    def test_a_run_with_calls_but_no_batches_fails(self):
        hub = "PROBE_CPU x\nPROBE_NPROC 4\nPROBE_ENCODE 0 200 0.05\n"
        self.assertEqual(self._run(hub), 1, "처리량을 못 쟀는데 초록이다")

    def test_a_complete_run_passes(self):
        self.assertEqual(self._run(HUB_OK), 0)


if __name__ == "__main__":
    unittest.main()
