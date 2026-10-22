# -*- coding: utf-8 -*-
"""제목 팔의 희소도 가중(VIN-749) — `VINC_NODE_TOKEN_IDF` 뒤에 두고 기본은 꺼 둔다.

이 파일이 지키는 것: 꺼져 있으면 토큰·행·순서가 옛 그대로다 · 켜면 한 글자 한글 토큰이
빠지고, 흔한 토큰(`한다`)을 많이 맞힌 행보다 드문 토큰(`함수여`)을 맞힌 행이 앞선다 ·
brief 는 그 무게로 확정한다.
근거: [[concept:rarity-weighted-node-tokens]]
"""
from __future__ import annotations

import pytest

from api import query as q
from store.querybase import normalize_query_tokens
from vinc_rag import retrieval

FLAG = "VINC_NODE_TOKEN_IDF"
QUERY = "배치를 계산하는 것은 상태가 아니라 함수여야 한다는 원칙"


class Store:
    driver_name = "synthetic"

    def __init__(self, nodes):
        self.nodes = list(nodes)

    def run(self, query, params=None):
        if "UNION ALL" in query and "props_raw" in query:
            return self.nodes
        return []


def _nodes():
    # 흔한 토큰(`한다`·`상태`·`아니라`)을 셋 맞히는 노드와, 드문 토큰(`함수여`)과 흔한 토큰
    # 하나를 맞히는 정답. 나머지는 `한다` 의 빈도를 올리는 배경이다.
    rows = [{"id": "concept:layout", "kind": "concept", "title": "Layout은 함수여야 한다"},
            {"id": "concept:noisy", "kind": "concept",
             "title": "상태가 아니라 근거를 다르게 다뤄야 한다"}]
    rows += [{"id": f"concept:bg{i}", "kind": "concept", "title": f"무엇을 해야 한다 {i}"}
             for i in range(20)]
    rows += [{"id": f"concept:st{i}", "kind": "concept", "title": f"상태가 아니라 {i}"}
             for i in range(12)]
    return rows


def test_single_hangul_tokens_drop_only_when_asked():
    assert "그" in normalize_query_tokens("역할을 노드로 두는 그 방식")
    assert "그" not in normalize_query_tokens("역할을 노드로 두는 그 방식",
                                             drop_single_hangul=True)
    # 전부 한 글자면 질의가 사라지지 않는다(옛 되돌리기 규칙과 같다).
    assert normalize_query_tokens("안 그", drop_single_hangul=True) == ["안", "그"]


def test_flag_off_keeps_counts_and_order(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    hits = retrieval._node_fallback_search(Store(_nodes()), QUERY, owner="u", limit=3)
    assert hits[0]["id"] == "concept:noisy"
    assert all("hit_weight" not in h for h in hits)


def test_flag_on_puts_the_rare_token_first(monkeypatch):
    monkeypatch.setenv(FLAG, "on")
    hits = retrieval._node_fallback_search(Store(_nodes()), QUERY, owner="u", limit=3)
    assert hits[0]["id"] == "concept:layout"
    top, noisy = hits[0], next(h for h in hits if h["id"] == "concept:noisy")
    assert top["hit_weight"] > noisy["hit_weight"]
    assert noisy["hit_tokens"] > top["hit_tokens"]      # 수는 그대로 실린다(계약 불변)


def test_rarest_token_weighs_one():
    rows = [{"id": "concept:a", "title": "x 희귀"}] + [
        {"id": f"concept:b{i}", "title": "x"} for i in range(9)]
    w = retrieval._token_weights(rows, ["희귀", "x", "없음"])
    assert w["희귀"] == pytest.approx(1.0)
    assert 0 < w["x"] < w["희귀"]
    assert "없음" not in w


def _pair(top_w, second_w):
    return [{"id": "concept:a", "title": "a", "hit_tokens": 3, "title_tokens": 2,
             "hit_weight": top_w, "title_weight": top_w, "arms": ["title"]},
            {"id": "concept:b", "title": "b", "hit_tokens": 3, "title_tokens": 2,
             "hit_weight": second_w, "title_weight": second_w, "arms": ["title"]}]


@pytest.mark.parametrize("flag,top_w,second_w,status", [
    (None, 2.0, 0.4, "choose"),       # 꺼지면 수만 본다: 3/2 동점
    ("on", 2.0, 0.4, "resolved"),     # 무게 차 1.6 ≥ 1
    ("on", 1.2, 0.4, "choose"),       # 무게 차 0.8 < 1
])
def test_brief_resolves_on_the_weighted_margin(monkeypatch, flag, top_w, second_w, status):
    if flag:
        monkeypatch.setenv(FLAG, flag)
    else:
        monkeypatch.delenv(FLAG, raising=False)
    got, picked = q._brief_resolve("anything", _pair(top_w, second_w))
    assert got == status
    assert picked[0]["id"] == "concept:a"


def test_rows_without_weights_fall_back_to_counts(monkeypatch):
    # 무게가 없는 행이 섞이면 수로 판정한다(수와 무게를 한 비교에 섞지 않는다).
    monkeypatch.setenv(FLAG, "on")
    rows = _pair(2.0, 0.4)
    rows[0]["hit_tokens"] = 5
    del rows[1]["hit_weight"]
    got, picked = q._brief_resolve("anything", rows)
    assert (got, picked[0]["id"]) == ("resolved", "concept:a")
