# -*- coding: utf-8 -*-
"""evalkit 의 공용 통계와 실행 지문 한 줄 — 호출부가 인라인으로 쓰던 식과 값이 같은지 잠근다.

식을 고치면 지난 결과 JSON 의 구간과 나란히 놓을 수 없으므로, 여기서는 **옛 인라인 식 그대로**를
기준으로 두고 새 함수가 같은 값을 내는지만 본다.
"""
from __future__ import annotations

import random
import statistics
import subprocess
from pathlib import Path

from rag_eval.evalkit import runtime, stats

ROOT = Path(__file__).resolve().parents[2]


def _inline_boot(d, n, seed):
    rng, m = random.Random(seed), len(d)
    return sorted(sum(d[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))


def test_boot_means_is_the_inline_expression():
    rnd = random.Random(3)
    for size in (1, 2, 7, 40):
        d = [round(rnd.uniform(-1, 1), 3) for _ in range(size)]
        for seed in (0, 7, 20260101):
            assert stats.boot_means(d, 300, random.Random(seed)) == _inline_boot(d, 300, seed)


def test_boot_means_continues_one_random_stream():
    """`funnel.paired` 는 지표 여럿이 한 난수열을 이어 쓴다 — 두 번째 호출은 첫 호출이 쓴 뒤의 열이다."""
    d1, d2 = [0.0, 1.0, 1.0, -1.0], [2.0, 0.5, 0.5]
    rng = random.Random(0)
    first, second = stats.boot_means(d1, 50, rng), stats.boot_means(d2, 50, rng)
    ref = random.Random(0)
    m1, m2 = len(d1), len(d2)
    want1 = sorted(sum(d1[ref.randrange(m1)] for _ in range(m1)) / m1 for _ in range(50))
    want2 = sorted(sum(d2[ref.randrange(m2)] for _ in range(m2)) / m2 for _ in range(50))
    assert (first, second) == (want1, want2)


def test_mean4_rounds_to_four_places_and_says_none_for_nothing():
    assert stats.mean4([]) is None
    assert stats.mean4([1.0, 0.0, 0.0]) == 0.3333
    xs = [0.1] * 7 + [0.7, 0.3]
    assert stats.mean4(xs) == round(statistics.fmean(xs), 4)


def test_git_head_names_the_tree_or_says_nothing():
    want = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    assert runtime.git_head(ROOT) == want
    assert runtime.git_head(ROOT / "no-such-dir") == ""
