# -*- coding: utf-8 -*-
"""R1b 의 스윕 축 둘이 실제로 무엇을 바꾸는지, 그리고 **이름이 안 바뀌는지**.

앞의 둘은 당연해 보이지만 `weighted_rrf` 의 `k` 는 여태 아무 호출부도 안 넘겼고
팔 깊이는 인자 자체가 없었다 — 즉 둘 다 한 번도 실행된 적 없는 경로다.
세 번째가 이 파일의 진짜 이유다: 기본 조건의 이름이 접미 없이 `rrf_w1` 로 남아야
이 플래그가 생기기 전에 잰 결과와 같은 칸에 놓인다.

근거: [[vinc.plan.retrieval-research-2026-09-08 §4]]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eval_rerank import DEFAULT_RRF_K, cond_name, weighted_rrf  # noqa: E402


def _rows(*ids):
    return [{"id": i} for i in ids]


def test_default_condition_keeps_its_old_name():
    # 이 셋이 갈리면 예전 결과 JSON 과 표를 나란히 못 놓는다.
    assert cond_name(1.0, DEFAULT_RRF_K, 0) == "rrf_w1"
    assert cond_name(0.5, DEFAULT_RRF_K, 0) == "rrf_w0.5"
    # 기본이 아닐 때만 축이 이름에 나타난다.
    assert cond_name(1.0, 20, 0) == "rrf_w1_k20"
    assert cond_name(1.0, DEFAULT_RRF_K, 50) == "rrf_w1_d50"
    assert cond_name(1.0, 10, 200) == "rrf_w1_k10_d200"


def test_k_flips_the_order():
    """A 는 한 팔에서 1위·다른 팔에서 6위, B 는 양쪽 3위. 작은 k 는 상위 순위를
    크게 쳐서 A 를, 큰 k 는 평탄해져 두 팔 모두 준수한 B 를 올린다."""
    kw = _rows("A", "x", "B", "y", "z", "w")
    vec = _rows("p", "q", "B", "r", "s", "A")
    assert [r["id"] for r in weighted_rrf([kw, vec], [1.0, 1.0], k=0)][0] == "A"
    assert [r["id"] for r in weighted_rrf([kw, vec], [1.0, 1.0], k=60)][0] == "B"


def test_arm_depth_drops_the_tail_contribution():
    """깊이로 자르면 잘린 뒤쪽 기여가 사라진다 — 그것이 팔 깊이 스윕이 재는 것이다."""
    kw = _rows("A", "x", "B")
    vec = _rows("p", "q", "A")
    full = weighted_rrf([kw, vec], [1.0, 1.0], k=60)
    cut = weighted_rrf([kw[:2], vec[:2]], [1.0, 1.0], k=60)
    a_full = next(r["rrf_score"] for r in full if r["id"] == "A")
    a_cut = next(r["rrf_score"] for r in cut if r["id"] == "A")
    assert a_cut < a_full
    assert "B" not in {r["id"] for r in cut}
