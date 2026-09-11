# -*- coding: utf-8 -*-
"""R3(a) 요약은 **시간대별로 갈라서** 낸다.

왜: 하루 비율 한 수는 이 값의 성질을 감춘다. 2026-09-08 중간 판독에서 저하 표본이
전부 한 시간에 몰려 있었고 그 시간은 그래프에 쓰기가 몰린 시간이었다 — 조용한 시간은
정확히 0 이었다. 즉 이 양은 서 있는 상태가 아니라 **쓰기 빈도의 함수**다. 하루 비율만
내면 판정하는 사람이 그것을 "상시 이 정도"로 읽는다.

근거: [[record:r3a-sampler-runs-on-a-heavy-write-day-2026-09-08]]
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from rebuild_window_probe import summarize  # noqa: E402


def _write(rows):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
    f.close()
    return f.name


def _sample(hour, minute, rebuilding):
    return {"ts": f"2026-09-08T{hour:02d}:{minute:02d}:00Z", "lang": "en",
            "latency_ms": 200.0,
            "arms": "fts" if rebuilding else "fts+vector",
            "vector_rebuilding": rebuilding,
            "vector_note": "vector index is being rebuilt" if rebuilding else None,
            "rows": 3, "error": None}


class ByHour(unittest.TestCase):
    def test_a_burst_in_one_hour_is_not_hidden_by_the_day_ratio(self):
        """한 시간에 몰린 저하가 하루 비율에서는 낮게 보인다 — 그 대비가 요약에 남아야 한다."""
        rows = [_sample(12, m, False) for m in range(30)]
        rows += [_sample(13, m, m < 15) for m in range(30)]   # 그 시간의 절반
        rows += [_sample(14, m, False) for m in range(30)]
        out = summarize(_write(rows))
        self.assertLess(out["ratio"], 0.20, "하루 비율은 낮게 보인다")
        self.assertEqual(out["by_hour_utc"]["12"]["ratio"], 0.0)
        self.assertEqual(out["by_hour_utc"]["13"]["ratio"], 0.5)
        self.assertEqual(out["by_hour_utc"]["14"]["ratio"], 0.0)
        self.assertEqual(out["worst_hour_ratio"], 0.5)
        self.assertEqual(out["hours_with_none"], 2)
        self.assertIn("standing condition", out["read_with"])

    def test_an_evenly_spread_window_says_nothing_extra(self):
        """고르게 퍼져 있으면 시간대별이 하루 비율과 같고, 그때는 경고가 붙지 않는다."""
        rows = [_sample(h, m, m == 0) for h in (12, 13, 14) for m in range(10)]
        out = summarize(_write(rows))
        self.assertEqual(out["hours_with_none"], 0)
        self.assertNotIn("read_with", out)

    def test_a_timeout_is_not_counted_as_a_rebuild(self):
        """인코더 콜드스타트로 강등된 표본이 재빌드 분자에 섞이면 R3 가 묻지 않은 것을
        재게 된다. 빼되, 사라지지는 않게 따로 센다."""
        rows = [_sample(12, m, False) for m in range(5)]
        rows.append({"ts": "2026-09-08T12:59:00Z", "lang": "ko", "latency_ms": 1600.0,
                     "arms": "fts", "vector_rebuilding": False,
                     "vector_note": "TimeoutError: The read operation timed out",
                     "rows": 3, "error": None})
        out = summarize(_write(rows))
        self.assertEqual(out["rebuilding"], 0)
        self.assertEqual(out["degraded_not_rebuilding"], 1)


if __name__ == "__main__":
    unittest.main()
