# -*- coding: utf-8 -*-
"""VIN-845 학습 해소 규칙. 이 파일이 지키는 것: 특징 열 개의 순서와 정의 · 이름을 댄 후보가 하나면
그것으로 확정 · 문턱에서 정확히 갈린다 · 스위치가 꺼져 있으면 기존 규칙이다.
근거: [[decision:the-refitted-brief-rule-is-the-one-vin-845-ships-2026-10-30]]"""
from __future__ import annotations

import math

import pytest

from api import query as q


def _hits():
    return [{"id": "a", "title": "Alpha", "colbert": 0.62, "prod_rank": 3, "hit_tokens": 2, "token_total": 4,
             "title_tokens": 1, "sem_lead": 0.05, "distance": 0.4},
            {"id": "b", "title": "Bravo", "colbert": 0.50, "prod_rank": 1, "hit_tokens": 1, "token_total": 4,
             "title_tokens": 1, "distance": None}]


def test_features_follow_the_research_definition():
    assert q._brief_learned_features("알파는?", _hits()) == pytest.approx(
        [0.62, 0.12, 0.0, 3.0, 0.5, 1.0, 1.0, 0.05, 0.4, 1.0])
    one = q._brief_learned_features("alpha", _hits()[:1])
    assert one[1] == pytest.approx(0.62) and one[6] == 2.0
    assert q._brief_learned_features("x", [{"id": "a"}]) is None     # 재채점 안 된 목록


def test_the_mean_row_scores_the_bias_only():
    assert q._brief_learned_p(list(q._LEARNED_MU)) == pytest.approx(1 / (1 + math.exp(-q._LEARNED_W[-1])))


def test_threshold_splits_and_named_wins(monkeypatch):
    monkeypatch.setattr(q, "_brief_learned_p", lambda x: q._LEARNED_T)
    st, picked, p = q._brief_resolve_learned("what is it", _hits())
    assert (st, picked[0]["id"], p) == ("resolved", "a", q._LEARNED_T)
    monkeypatch.setattr(q, "_brief_learned_p", lambda x: q._LEARNED_T - 1e-9)
    st, picked, _ = q._brief_resolve_learned("what is it", _hits())
    assert st == "choose" and [h["id"] for h in picked] == ["a", "b"]
    st, picked, p = q._brief_resolve_learned("  bravo ", _hits())
    assert (st, picked[0]["id"], p) == ("resolved", "b", None)


def test_switch_defaults_to_production(monkeypatch):
    monkeypatch.delenv("VINC_BRIEF_RULE", raising=False)
    assert q._brief_rule_learned() is False
    monkeypatch.setenv("VINC_BRIEF_RULE", "learned")
    assert q._brief_rule_learned() is True


def test_production_rule_never_sees_the_colbert_order(monkeypatch):
    seen = {}

    def fake_search(query, **kw):
        seen["qc"] = kw.get("query_colbert")
        return {"executed": True, "node_hits": [], "rows": []}

    monkeypatch.setattr(q, "search", fake_search)
    monkeypatch.delenv("VINC_BRIEF_RULE", raising=False)
    q.brief("what is it", owner="o", query_colbert="TOKENS", store=object())
    assert seen["qc"] is None
    monkeypatch.setenv("VINC_BRIEF_RULE", "learned")
    q.brief("what is it", owner="o", query_colbert="TOKENS", store=object())
    assert seen["qc"] == "TOKENS"
