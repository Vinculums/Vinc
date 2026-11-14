# -*- coding: utf-8 -*-
"""brief 학습 규칙 v3. 이 파일이 지키는 것: 고정 상수가 측정한 그대로다 · 의미만 기권은 재채점 전
순서로 본다 · 이름을 댄 후보 하나면 그것으로 확정 · 문턱에서 정확히 갈린다 · 감춘 행은 특징으로
들어가고 토큰 동점 가드는 돌지 않는다 · 스위치는 명시적이고 기본은 production 이다.
근거: [[decision:ship-brief-rule-v3-on-the-owners-reading-of-the-wrong-picks-2026-11-03]]"""
from __future__ import annotations

import copy
import hashlib
import json
import math

import pytest

from api import brief as brief_mod
from api import query as q


def _hit(i, *, colbert, prod_rank, hit_tokens=0, title_tokens=0, distance=0.30, arms=("semantic",),
         title=None, sem_lead=0.0, token_total=4):
    return {"id": f"concept:n{i}", "title": title or f"Node {i}", "kind": "concept", "colbert": colbert,
            "prod_rank": prod_rank, "hit_tokens": hit_tokens, "title_tokens": title_tokens,
            "token_total": token_total, "distance": distance, "arms": list(arms), "sem_lead": sem_lead}


def _lexical_hits():
    """ColBERT 순서 1위가 토큰으로도 맞은 평범한 응답(여섯 행)."""
    return [_hit(1, colbert=0.62, prod_rank=1, hit_tokens=2, title_tokens=1, arms=("lexical", "semantic"),
                 distance=0.33, sem_lead=0.06),
            _hit(2, colbert=0.50, prod_rank=2, hit_tokens=2, title_tokens=1, arms=("lexical",), distance=None),
            _hit(3, colbert=0.50, prod_rank=3, hit_tokens=1, distance=0.40),
            _hit(4, colbert=0.48, prod_rank=4, distance=0.41),
            _hit(5, colbert=0.47, prod_rank=5, distance=0.42),
            _hit(6, colbert=0.40, prod_rank=6, distance=0.45)]


def _res(hits, **extra):
    return {"executed": True, "node_hits": hits, "node_rescore": {"state": "applied", "n": len(hits)},
            "validity_filtered": 0, "validity_best_dropped": [0, 0],
            "validity_best_dropped_distance": None, **extra}


@pytest.fixture
def run_brief(monkeypatch):
    """store 없이 brief 를 돌린다: search 응답을 그대로 주고, 노드는 늘 열린다."""
    monkeypatch.setenv("VINC_BRIEF_QUOTES", "off")
    monkeypatch.setattr(brief_mod, "_brief_query_ids", lambda *a, **k: [])
    monkeypatch.setattr(brief_mod, "_brief_entity_ids", lambda *a, **k: [])
    monkeypatch.setattr(brief_mod, "node_context", lambda **kw: {"executed": True})
    monkeypatch.setattr(brief_mod, "_brief_answer", lambda base, hit, ctx, store, **rule: {**base, "node": {"id": hit["id"]}})
    monkeypatch.setattr(brief_mod, "_brief_attach_section", lambda *a, **k: None)
    monkeypatch.setattr(brief_mod, "_brief_mirror_text", lambda store, owner, h: None)

    def go(query, res, rule="v3"):
        seen = {}

        def fake_search(query, **kw):
            seen["qc"] = kw.get("query_colbert")
            return copy.deepcopy(res)

        monkeypatch.setattr(brief_mod, "search", fake_search)
        monkeypatch.setenv("VINC_BRIEF_RULE", rule)
        out = q.brief(query, owner="o", query_colbert="TOKENS", store=object())
        out["_qc"] = seen.get("qc")
        return out
    return go


def _picked(out):
    return (out.get("node") or {}).get("id") if out["status"] == "resolved" else None


def test_constants_are_the_frozen_measurement():
    fz = {"features": "v2 ten + hidden hit diff, hidden title diff, hidden distance diff, filtered",
          "mu": q._V3_MU, "sd": q._V3_SD, "threshold": q._V3_T, "w": q._V3_W}
    assert hashlib.sha256(json.dumps(fz, sort_keys=True).encode()).hexdigest() == (
        "c9a3fb2b3d3357ccc22e843026b945ba59b0fc5a9d6d95343f4a9e302c791083")
    assert len(q._V3_MU) == len(q._V3_SD) == 14 and len(q._V3_W) == 15


def test_the_mean_row_scores_the_bias_only():
    assert q._brief_v3_p(list(q._V3_MU)) == pytest.approx(1 / (1 + math.exp(-q._V3_W[-1])))


def test_first_ten_features_follow_the_research_definition():
    # v3 특징의 앞 열(:func:`_brief_learned_features`)은 연구 정의 그대로다. 순서·정의가 바뀌면 상수가 뜻을 잃는다.
    hits = [{"id": "a", "title": "Alpha", "colbert": 0.62, "prod_rank": 3, "hit_tokens": 2, "token_total": 4,
             "title_tokens": 1, "sem_lead": 0.05, "distance": 0.4},
            {"id": "b", "title": "Bravo", "colbert": 0.50, "prod_rank": 1, "hit_tokens": 1, "token_total": 4,
             "title_tokens": 1, "distance": None}]
    assert q._brief_learned_features("알파는?", hits) == pytest.approx(
        [0.62, 0.12, 0.0, 3.0, 0.5, 1.0, 1.0, 0.05, 0.4, 1.0])
    one = q._brief_learned_features("alpha", hits[:1])
    assert one[1] == pytest.approx(0.62) and one[6] == 2.0
    assert q._brief_learned_features("x", [{"id": "a"}]) is None     # 재채점 안 된 목록


def test_hidden_row_features():
    hits = _lexical_hits()
    res = _res(hits, validity_filtered=3, validity_best_dropped=[4, 2], validity_best_dropped_distance=0.30)
    x = q._brief_v3_features("what is it", hits[:5], res)
    assert x[:10] == pytest.approx(q._brief_learned_features("what is it", hits[:5]))
    assert x[10:] == pytest.approx([4 - 2, 2 - 1, 0.30 - 0.33, 1.0])
    # 감춘 행이 없거나 거리가 비면: 차이는 0 − 1위, 거리 차이는 1.0, 필터 0.
    x = q._brief_v3_features("what is it", hits[:5], _res(hits, validity_best_dropped=None))
    assert x[10:] == pytest.approx([-2, -1, 1.0, 0.0])
    nod = copy.deepcopy(hits)
    nod[0]["distance"] = None
    x = q._brief_v3_features("q", nod[:5], _res(nod, validity_best_dropped_distance=0.2))
    assert x[12] == 1.0
    assert q._brief_v3_features("q", [{"id": "a"}], _res([])) is None     # 재채점 안 된 목록


def test_named_exact_title_wins_even_below_threshold(run_brief, monkeypatch):
    hits = _lexical_hits()
    hits[2]["title"] = "Release  Train"
    monkeypatch.setattr(brief_mod, "_brief_v3_p", lambda x: 0.0)
    out = run_brief("  release train ", _res(hits))
    assert _picked(out) == "concept:n3" and out["resolution"]["rule"] == "v3"
    # 같은 제목이 둘이면 이름 갈래가 아니다 — 문턱 아래라 되묻는다.
    hits[3]["title"] = "release train"
    out = run_brief("release train", _res(hits))
    assert out["status"] == "choose"


def test_learned_pick_splits_at_the_threshold(run_brief, monkeypatch):
    monkeypatch.setattr(brief_mod, "_brief_v3_p", lambda x: q._V3_T)
    out = run_brief("what is it", _res(_lexical_hits()))
    assert _picked(out) == "concept:n1" and out["resolution"] == {"rule": "v3", "p": round(q._V3_T, 4)}
    monkeypatch.setattr(brief_mod, "_brief_v3_p", lambda x: q._V3_T - 1e-9)
    out = run_brief("what is it", _res(_lexical_hits()))
    assert out["status"] == "choose"
    # 되묻기 목록은 ColBERT 순서 그대로다.
    assert [c["id"] for c in out["candidates"]] == [f"concept:n{i}" for i in range(1, 6)]


def test_real_probability_above_and_below(run_brief):
    strong = _lexical_hits()
    out = run_brief("what is it", _res(strong))
    p = q._brief_v3_p(q._brief_v3_features("what is it", strong[:5], _res(strong)))
    assert p >= q._V3_T and _picked(out) == "concept:n1"
    weak = copy.deepcopy(strong)
    weak[0].update(colbert=0.40, prod_rank=9, hit_tokens=0, title_tokens=0, sem_lead=0.0, distance=0.6)
    weak[1]["colbert"] = 0.399
    weak[1]["prod_rank"] = 1
    p = q._brief_v3_p(q._brief_v3_features("what is it", weak[:5], _res(weak)))
    assert p < q._V3_T and run_brief("what is it", _res(weak))["status"] == "choose"


def _semantic_only_prod_head():
    """재채점 전 앞 다섯은 전부 멀리 있는 의미만 후보이고, ColBERT 가 토큰 맞은 행을 앞으로 올렸다."""
    hits = [_hit(i, colbert=0.5 - i * 0.01, prod_rank=i, distance=0.5 + i * 0.01) for i in range(1, 7)]
    lex = _hit(7, colbert=0.9, prod_rank=7, hit_tokens=2, title_tokens=1, arms=("lexical",), distance=None)
    return [lex] + hits[:5]


def test_semantic_none_abstains_on_the_pre_rescore_order(run_brief):
    hits = _semantic_only_prod_head()
    out = run_brief("what is it", _res(hits))
    assert out["status"] == "none" and out["note"] == "no sufficiently close semantic candidate"
    # ColBERT 순서 앞 다섯으로 보면 토큰 맞은 행 때문에 기권하지 않는다 — 기권은 재채점 전 순서에서 나온다.
    assert not brief_mod._brief_semantic_none(_res(hits))


def test_semantic_none_ignores_a_colbert_head_that_is_semantic_only(run_brief, monkeypatch):
    # 반대: ColBERT 앞 다섯은 전부 멀리 있는 의미만이지만, 재채점 전 1위는 토큰으로 맞았다.
    far = [_hit(i, colbert=0.9 - i * 0.01, prod_rank=i + 1, distance=0.6) for i in range(1, 6)]
    lex = _hit(9, colbert=0.1, prod_rank=1, hit_tokens=2, arms=("lexical",), distance=None)
    monkeypatch.setattr(brief_mod, "_brief_v3_p", lambda x: 1.0)
    out = run_brief("what is it", _res(far + [lex]))
    assert _picked(out) == "concept:n1"


def test_token_tie_guard_does_not_run_on_v3(run_brief, monkeypatch):
    # 감춘 행이 1위와 토큰으로 비긴다. 기존 가드라면 되묻고, v3 는 특징으로만 본다.
    res = _res(_lexical_hits(), validity_filtered=1, validity_best_dropped=[2, 1],
               validity_best_dropped_distance=0.34)
    monkeypatch.setattr(brief_mod, "_brief_resolve", lambda query, hits, **kw: ("resolved", [hits[0]]))
    assert run_brief("what is it", res, rule="production")["status"] == "choose"
    monkeypatch.setattr(brief_mod, "_brief_v3_p", lambda x: 0.9)
    out = run_brief("what is it", res)
    assert _picked(out) == "concept:n1" and "validity_note" not in out


def test_not_rescored_falls_back_to_production(run_brief, monkeypatch):
    called = []
    monkeypatch.setattr(brief_mod, "_brief_resolve_v3", lambda *a: called.append(1) or ("choose", [], None))
    res = _res(_lexical_hits(), node_rescore={"state": "skipped", "reason": "tokens missing"})
    out = run_brief("what is it", res)
    assert not called
    # 되돌아간 사실이 답에 남는다: 어느 규칙이 답했는지, 무엇이 설정돼 있었는지, 왜 재채점이 없었는지.
    assert out["resolution"] == {"rule": "production", "configured": "v3", "rescore": "skipped",
                                 "reason": "tokens missing"}


def test_fallback_is_named_on_a_semantic_none_answer_too(run_brief):
    far = [_hit(i, colbert=0.5, prod_rank=i, distance=0.9) for i in range(1, 6)]
    out = run_brief("what is it", _res(far, node_rescore={"state": "skipped", "reason": "no owner scope"}))
    assert out["status"] == "none"
    assert out["resolution"]["rule"] == "production" and out["resolution"]["configured"] == "v3"


def test_production_rule_adds_no_resolution_key(run_brief):
    out = run_brief("what is it", _res(_lexical_hits()), rule="production")
    assert "resolution" not in out


def test_retrieval_switches_report_the_effective_values(monkeypatch):
    from vinc_rag import colbert
    monkeypatch.delenv("VINC_BRIEF_RULE", raising=False)
    monkeypatch.delenv("VINC_NODE_COLBERT", raising=False)
    assert q.retrieval_switches() == {"VINC_NODE_COLBERT": "off", "VINC_BRIEF_RULE": "production"}
    monkeypatch.setenv("VINC_BRIEF_RULE", "v3")
    monkeypatch.setenv("VINC_NODE_COLBERT", "on")
    monkeypatch.setattr(colbert, "rescore_on", lambda: True)
    assert q.retrieval_switches() == {"VINC_NODE_COLBERT": "on", "VINC_BRIEF_RULE": "v3"}
    monkeypatch.setattr(colbert, "rescore_on", lambda: False)   # 설정은 on 이지만 numpy 가 없으면 실제로는 꺼진다
    assert q.retrieval_switches()["VINC_NODE_COLBERT"] == "off"


def test_switch_is_explicit_and_defaults_to_production(monkeypatch, run_brief):
    monkeypatch.delenv("VINC_BRIEF_RULE", raising=False)
    assert q._brief_rule() == "production" and not q._BRIEF_RULES[q._brief_rule()].uses_colbert
    for raw, want in (("v3", "v3"), (" V3 ", "v3"), ("learned", "production"), ("bogus", "production"),
                      ("", "production")):
        monkeypatch.setenv("VINC_BRIEF_RULE", raw)
        assert q._brief_rule() == want
    monkeypatch.setenv("VINC_BRIEF_RULE", "v3")
    assert q._brief_rule() == "v3" and q._BRIEF_RULES[q._brief_rule()].uses_colbert
    assert run_brief("what is it", _res([]), rule="v3")["_qc"] == "TOKENS"
    assert run_brief("what is it", _res([]), rule="production")["_qc"] is None
    # 모르는 값은 production 그대로 답한다: ColBERT 순서를 안 받고 `resolution` 도 싣지 않는다.
    out = run_brief("what is it", _res(_lexical_hits()), rule="learned")
    assert out["_qc"] is None and "resolution" not in out
