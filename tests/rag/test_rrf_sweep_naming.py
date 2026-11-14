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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from rag_eval.rerank import DEFAULT_RRF_K, cond_name, weighted_rrf  # noqa: E402


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


def test_ko_stripper_is_one_function_and_leaves_english_alone():
    """색인 쪽 백필과 질의 쪽이 **같은 함수**를 써야 한다 — 두 벌이 되면 조용히 갈라지고,
    갈라진 순간 이 실험은 아무것도 재지 못한다(색인에 없는 토큰을 묻게 된다).

    영어를 건드리지 않는 것도 계약이다. 원문 열의 영어 어간 처리가 계속 맞아야
    한국어를 살리면서 영어를 잃지 않는다. 근거: [[vinc.plan.retrieval-research-2026-09-08]]"""
    from rag_eval.rerank import ko_stripped_text
    import r1c_ko_column

    assert r1c_ko_column.ko_stripped_text is ko_stripped_text

    assert ko_stripped_text("데이터 영속성을 보장한다") == "데이터 영속성 보장한다"
    assert ko_stripped_text("워커는 청크를 나눈다") == "워커 청크 나눈다"
    # 영어는 한 글자도 안 바뀐다.
    for s in ("the embedding worker splits chunks", "workers", "HNSW index rebuild"):
        assert ko_stripped_text(s) == " ".join(s.split())
    # 어간이 한 글자로 줄 것은 손대지 않는다(`_strip_ko_suffix` 의 계약).
    assert ko_stripped_text("국가") == "국가"


def test_ko_stripper_splits_with_the_query_normalizer_pattern():
    """색인 쪽 조사 벗기기와 질의 쪽 정규화가 **같은 정규식 객체**로 낱말을 가른다. 복사본이
    남으면 한쪽만 구분 문자를 바꿔도 아무 테스트가 안 깨지고 색인과 질의가 갈라진다."""
    from rag_eval import rerank
    from store import querybase

    assert rerank.QUERY_SPLIT_RE is querybase.QUERY_SPLIT_RE
    assert not hasattr(rerank, "_KO_SPLIT")
    # 구분 문자 전부를 한 문장에 넣어도 두 쪽의 낱말 경계가 같다.
    text = "환불은,며칠/이내에?되나요!(약관);제어가:{정책}[목록]\"따옴표\"'작은'`틱`~물결"
    words = [w for w in querybase.QUERY_SPLIT_RE.split(text) if w]
    assert rerank.ko_stripped_text(text) == " ".join(querybase._strip_ko_suffix(w) for w in words)
    assert len(words) == 12

