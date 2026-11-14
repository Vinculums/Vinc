# -*- coding: utf-8 -*-
"""brief 규칙 표: 정해 둔 검색 응답(hits)을 넣으면 상태·고른 노드·해소 기록·되묻기 목록·마무리 단계가 이렇게 나온다.

규칙(production·v3)마다, 직참조·none·choose·폴스루·동점 가드 갈래까지 한 줄씩 적었다. 기대값은 이 표를 만든
날의 코드에서 그대로 받아 적은 것이다. 해소 규칙이나 마무리(인용·절·되묻기 재료)를 옮기거나 쪼갤 때 이 표가
같아야 하고, 일부러 바꾸는 변경만 기대값을 고친다.

검색과 노드 읽기는 갈아 끼운다(스토어 없이 돈다): `search` 는 표의 응답을 그대로 주고, `node_context` 는 표가
여는 id 만 연다. 답 조립(`_brief_answer`)·절(`_brief_attach_section`)·인용(`_brief_node_quotes`)·되묻기 재료
(`_brief_candidate_material`)는 불린 순서만 적는다.
"""
from __future__ import annotations

import copy
import os
from unittest import mock

import pytest

from api import brief as brief_mod
from api import query as q


def _hit(i, *, hit_tokens=0, title_tokens=0, token_total=2, distance=0.30, arms=("semantic",), title=None,
         sem_lead=None, colbert=None, prod_rank=None, kind="concept"):
    h = {"id": f"concept:n{i}", "title": title or f"Node {i}", "kind": kind, "hit_tokens": hit_tokens,
         "title_tokens": title_tokens, "token_total": token_total, "distance": distance, "arms": list(arms)}
    if sem_lead is not None:
        h["sem_lead"] = sem_lead
    if colbert is not None:
        h["colbert"], h["prod_rank"] = colbert, prod_rank
    return h


def _res(hits, *, rescored=False, **extra):
    out = {"executed": True, "node_hits": hits, "validity_filtered": 0, "validity_best_dropped": [0, 0],
           "validity_best_dropped_distance": None}
    if rescored:
        out["node_rescore"] = {"state": "applied", "n": len(hits)}
    out.update(extra)
    return out


def _v3_hits(top_colbert=0.62, second_colbert=0.50, top_tokens=2):
    return [_hit(1, colbert=top_colbert, prod_rank=1, hit_tokens=top_tokens, title_tokens=1,
                 arms=("lexical", "semantic"), distance=0.33, sem_lead=0.06, token_total=4),
            _hit(2, colbert=second_colbert, prod_rank=2, hit_tokens=2, title_tokens=1, arms=("lexical",),
                 distance=None, token_total=4),
            _hit(3, colbert=0.50, prod_rank=3, hit_tokens=1, distance=0.40, token_total=4),
            _hit(4, colbert=0.48, prod_rank=4, distance=0.41, token_total=4),
            _hit(5, colbert=0.47, prod_rank=5, distance=0.42, token_total=4),
            _hit(6, colbert=0.40, prod_rank=6, distance=0.45, token_total=4)]


_LEX = [_hit(1, hit_tokens=3, title_tokens=3, token_total=3, arms=("lexical", "semantic"), distance=0.30,
             title="Release train"),
        _hit(2, hit_tokens=1, title_tokens=1, arms=("lexical",), distance=None, title="Release notes"),
        _hit(3, hit_tokens=0, arms=("semantic",), distance=0.41)]
_TIE = [_hit(1, hit_tokens=1, title_tokens=1, arms=("lexical",), distance=None, title="Train schedule"),
        _hit(2, hit_tokens=1, title_tokens=1, arms=("lexical",), distance=None, title="Train depot"),
        _hit(3, hit_tokens=0, arms=("semantic",), distance=0.44)]
_NARROW = [_hit(1, hit_tokens=2, title_tokens=2, arms=("lexical", "semantic"), distance=0.30, title="Release train"),
           _hit(2, hit_tokens=1, title_tokens=1, arms=("lexical", "semantic"), distance=0.35, title="Release notes")]
_FAR = [_hit(1, arms=("semantic",), distance=0.61), _hit(2, arms=("semantic",), distance=0.63)]
_NEAR_PROMOTE = [_hit(1, arms=("semantic",), distance=0.20, sem_lead=0.20),
                 _hit(2, arms=("semantic",), distance=0.40), _hit(3, arms=("semantic",), distance=0.41)]
_ALL = {"concept:n1", "concept:n2", "concept:n3", "concept:n4", "concept:n5", "concept:n6", "concept:alpha"}

#: (이름, 규칙, 질문, search 응답, node_context 가 여는 id). 응답이 None 이면 search 를 부르지 않아야 한다(직참조).
CASES = [
    ("production lexical winner", "production", "the release train", _res(_LEX), _ALL),
    ("production token tie", "production", "train", _res(_TIE), _ALL),
    ("production one token lead is not enough", "production", "the release train", _res(_NARROW), _ALL),
    ("production no hits", "production", "nothing here", _res([]), _ALL),
    ("production distant semantic only", "production", "zzz qqq", _res(_FAR), _ALL),
    ("production semantic lead promotes", "production", "알파 베타 감마", _res(_NEAR_PROMOTE), _ALL),
    ("production first pick unreadable", "production", "the release train", _res(_LEX), _ALL - {"concept:n1"}),
    ("production no pick readable", "production", "the release train", _res(_LEX), set()),
    ("production hidden tie guard", "production", "the release train",
     _res(_LEX, validity_filtered=1, validity_best_dropped=[3, 3], validity_note="hid 1 claim"), _ALL),
    ("production chunk arm error with node hits", "production", "the release train",
     _res(_LEX, error="fts unavailable"), _ALL),
    ("production chunk arm error without node hits", "production", "the release train",
     _res([], error="fts unavailable"), _ALL),
    ("production single semantic hit", "production", "lonely", _res([_hit(1, arms=("semantic",), distance=0.30)]), _ALL),
    ("production named exact title", "production", "Release notes", _res(_LEX), _ALL),
    ("production named title, its node unreadable", "production", "Release train", _res(_LEX),
     _ALL - {"concept:n1"}),
    ("direct id resolved", "production", "what does concept:alpha say", None, _ALL),
    ("direct id unreadable", "production", "what does concept:ghost say", None, _ALL),
    ("v3 configured without rescore", "v3", "the release train", _res(_LEX), _ALL),
    ("v3 strong lexical top", "v3", "what is it", _res(_v3_hits(), rescored=True), _ALL),
    ("v3 weak margin", "v3", "what is it", _res(_v3_hits(0.51, 0.50, 0), rescored=True), _ALL),
    ("v3 named exact title", "v3", "node 3", _res(_v3_hits(), rescored=True), _ALL),
    ("v3 distant semantic only", "v3", "zzz qqq",
     _res([dict(h, arms=["semantic"], hit_tokens=0, title_tokens=0, distance=0.6 + i / 100)
           for i, h in enumerate(_v3_hits())], rescored=True), _ALL),
    ("v3 first pick unreadable", "v3", "what is it", _res(_v3_hits(), rescored=True), _ALL - {"concept:n1"}),
    ("v3 direct id resolved", "v3", "what does concept:alpha say", None, _ALL),
]


def observe(name, rule, query, res, opens):
    """한 줄을 돌려 비교할 꼴로 줄인다. 환경은 규칙 하나만 바꾸고 나머지 brief 플래그는 기본값으로 둔다."""
    calls: list[str] = []
    seen: dict = {}

    def fake_search(query, **kw):
        assert res is not None, f"{name}: 직참조 줄인데 search 를 불렀다"
        seen["query_colbert"] = kw.get("query_colbert")
        return copy.deepcopy(res)

    def fake_node_context(**kw):
        if kw["node"] in opens:
            return {"executed": True}
        return {"executed": False, "note": "node not found: " + kw["node"]}

    def fake_answer(base, hit, ctx, store, **rule_kw):
        calls.append(f"answer:{hit['id']}")
        return {**base, "node": {"id": hit["id"]}}

    def fake_section(out, store, **kw):
        calls.append("section:" + ",".join(kw.get("nodes") or []))

    def fake_quotes(store, **kw):
        calls.append(f"quotes:{kw.get('node_id')}")
        return [{"text": "q"}]

    def fake_material(store, cand, **kw):
        calls.append(f"material:{cand.get('id')}")
        return {"candidate": cand.get("id")}

    env = {k: v for k, v in os.environ.items() if not k.startswith("VINC_BRIEF_")}
    env["VINC_BRIEF_RULE"] = rule
    with mock.patch.dict(os.environ, env, clear=True), \
            mock.patch.object(brief_mod, "search", fake_search), \
            mock.patch.object(brief_mod, "node_context", fake_node_context), \
            mock.patch.object(brief_mod, "_brief_answer", fake_answer), \
            mock.patch.object(brief_mod, "_brief_attach_section", fake_section), \
            mock.patch.object(brief_mod, "_brief_node_quotes", fake_quotes), \
            mock.patch.object(brief_mod, "_brief_candidate_material", fake_material), \
            mock.patch.object(brief_mod, "_brief_entity_ids", lambda *a, **k: []), \
            mock.patch.object(brief_mod, "_brief_mirror_text", lambda store, owner, h: None):
        out = q.brief(query, owner="o", query_colbert="TOKENS", store=object())
    return {
        "status": out.get("status"),
        "picked": (out.get("node") or {}).get("id") if out.get("status") == "resolved" else None,
        "resolution": out.get("resolution"),
        "resolved_via": out.get("resolved_via"),
        "candidates": [c.get("id") for c in out.get("candidates") or []],
        "note": out.get("note"),
        "unreadable": [u.get("id") for u in out.get("unreadable") or []],
        "fell_through": [u.get("id") for u in out.get("fell_through") or []],
        "query_colbert": seen.get("query_colbert", "no search"),
        "calls": calls,
        "keys": sorted(out),
    }


EXPECTED: dict = {'production lexical winner': {'status': 'resolved',
                               'picked': 'concept:n1',
                               'resolution': None,
                               'resolved_via': None,
                               'candidates': [],
                               'note': None,
                               'unreadable': [],
                               'fell_through': [],
                               'query_colbert': None,
                               'calls': ['answer:concept:n1', 'quotes:concept:n1', 'section:concept:n1'],
                               'keys': ['candidates', 'executed', 'grounds', 'kind', 'node', 'query', 'quotes',
                                        'status', 'validity_best_dropped']},
 'production token tie': {'status': 'choose',
                          'picked': None,
                          'resolution': None,
                          'resolved_via': None,
                          'candidates': ['concept:n3', 'concept:n1', 'concept:n2'],
                          'note': None,
                          'unreadable': [],
                          'fell_through': [],
                          'query_colbert': None,
                          'calls': ['quotes:concept:n3', 'quotes:concept:n1', 'quotes:concept:n2',
                                    'section:concept:n3,concept:n1,concept:n2', 'material:concept:n3'],
                          'keys': ['candidate_material', 'candidates', 'executed', 'grounds', 'kind', 'query',
                                   'status', 'validity_best_dropped']},
 'production one token lead is not enough': {'status': 'choose',
                                             'picked': None,
                                             'resolution': None,
                                             'resolved_via': None,
                                             'candidates': ['concept:n1', 'concept:n2'],
                                             'note': None,
                                             'unreadable': [],
                                             'fell_through': [],
                                             'query_colbert': None,
                                             'calls': ['quotes:concept:n1', 'quotes:concept:n2',
                                                       'section:concept:n1,concept:n2', 'material:concept:n1'],
                                             'keys': ['candidate_material', 'candidates', 'executed', 'grounds',
                                                      'kind', 'query', 'status', 'validity_best_dropped']},
 'production no hits': {'status': 'none',
                        'picked': None,
                        'resolution': None,
                        'resolved_via': None,
                        'candidates': [],
                        'note': None,
                        'unreadable': [],
                        'fell_through': [],
                        'query_colbert': None,
                        'calls': [],
                        'keys': ['candidates', 'executed', 'grounds', 'kind', 'query', 'status',
                                 'validity_best_dropped']},
 'production distant semantic only': {'status': 'none',
                                      'picked': None,
                                      'resolution': None,
                                      'resolved_via': None,
                                      'candidates': [],
                                      'note': 'no sufficiently close semantic candidate',
                                      'unreadable': [],
                                      'fell_through': [],
                                      'query_colbert': None,
                                      'calls': [],
                                      'keys': ['candidates', 'executed', 'grounds', 'kind', 'note', 'query', 'status',
                                               'validity_best_dropped']},
 'production semantic lead promotes': {'status': 'resolved',
                                       'picked': 'concept:n1',
                                       'resolution': None,
                                       'resolved_via': None,
                                       'candidates': [],
                                       'note': None,
                                       'unreadable': [],
                                       'fell_through': [],
                                       'query_colbert': None,
                                       'calls': ['answer:concept:n1', 'quotes:concept:n1', 'section:concept:n1'],
                                       'keys': ['candidates', 'executed', 'grounds', 'kind', 'node', 'query',
                                                'quotes', 'status', 'validity_best_dropped']},
 'production first pick unreadable': {'status': 'resolved',
                                      'picked': 'concept:n2',
                                      'resolution': None,
                                      'resolved_via': None,
                                      'candidates': [],
                                      'note': None,
                                      'unreadable': [],
                                      'fell_through': ['concept:n1'],
                                      'query_colbert': None,
                                      'calls': ['answer:concept:n2', 'quotes:concept:n2', 'section:concept:n2'],
                                      'keys': ['candidates', 'executed', 'fell_through', 'grounds', 'kind', 'node',
                                               'query', 'quotes', 'status', 'validity_best_dropped']},
 'production no pick readable': {'status': 'none',
                                 'picked': None,
                                 'resolution': None,
                                 'resolved_via': None,
                                 'candidates': [],
                                 'note': 'node not found: concept:n1',
                                 'unreadable': ['concept:n1', 'concept:n2', 'concept:n3'],
                                 'fell_through': [],
                                 'query_colbert': None,
                                 'calls': [],
                                 'keys': ['candidates', 'executed', 'grounds', 'kind', 'note', 'query', 'status',
                                          'unreadable', 'validity_best_dropped']},
 'production hidden tie guard': {'status': 'choose',
                                 'picked': None,
                                 'resolution': None,
                                 'resolved_via': None,
                                 'candidates': ['concept:n1', 'concept:n3', 'concept:n2'],
                                 'note': None,
                                 'unreadable': [],
                                 'fell_through': [],
                                 'query_colbert': None,
                                 'calls': ['quotes:concept:n1', 'quotes:concept:n3', 'quotes:concept:n2',
                                           'section:concept:n1,concept:n3,concept:n2', 'material:concept:n1'],
                                 'keys': ['candidate_material', 'candidates', 'executed', 'grounds', 'kind', 'query',
                                          'status', 'validity_best_dropped', 'validity_filtered', 'validity_note']},
 'production chunk arm error with node hits': {'status': 'resolved',
                                               'picked': 'concept:n1',
                                               'resolution': None,
                                               'resolved_via': None,
                                               'candidates': [],
                                               'note': None,
                                               'unreadable': [],
                                               'fell_through': [],
                                               'query_colbert': None,
                                               'calls': ['answer:concept:n1', 'quotes:concept:n1',
                                                         'section:concept:n1'],
                                               'keys': ['candidates', 'executed', 'grounds', 'kind', 'node', 'query',
                                                        'quotes', 'search_error', 'status',
                                                        'validity_best_dropped']},
 'production chunk arm error without node hits': {'status': 'none',
                                                  'picked': None,
                                                  'resolution': None,
                                                  'resolved_via': None,
                                                  'candidates': [],
                                                  'note': None,
                                                  'unreadable': [],
                                                  'fell_through': [],
                                                  'query_colbert': None,
                                                  'calls': [],
                                                  'keys': ['candidates', 'executed', 'grounds', 'kind', 'query',
                                                           'search_error', 'status', 'validity_best_dropped']},
 'production single semantic hit': {'status': 'choose',
                                    'picked': None,
                                    'resolution': None,
                                    'resolved_via': None,
                                    'candidates': ['concept:n1'],
                                    'note': None,
                                    'unreadable': [],
                                    'fell_through': [],
                                    'query_colbert': None,
                                    'calls': ['quotes:concept:n1', 'section:concept:n1', 'material:concept:n1'],
                                    'keys': ['candidate_material', 'candidates', 'executed', 'grounds', 'kind',
                                             'query', 'status', 'validity_best_dropped']},
 'production named exact title': {'status': 'resolved',
                                  'picked': 'concept:n2',
                                  'resolution': None,
                                  'resolved_via': None,
                                  'candidates': [],
                                  'note': None,
                                  'unreadable': [],
                                  'fell_through': [],
                                  'query_colbert': None,
                                  'calls': ['answer:concept:n2', 'quotes:concept:n2', 'section:concept:n2'],
                                  'keys': ['candidates', 'executed', 'grounds', 'kind', 'node', 'query', 'quotes',
                                           'status', 'validity_best_dropped']},
 'production named title, its node unreadable': {'status': 'none',
                                                 'picked': None,
                                                 'resolution': None,
                                                 'resolved_via': None,
                                                 'candidates': [],
                                                 'note': 'node not found: concept:n1',
                                                 'unreadable': ['concept:n1'],
                                                 'fell_through': [],
                                                 'query_colbert': None,
                                                 'calls': [],
                                                 'keys': ['candidates', 'executed', 'grounds', 'kind', 'note',
                                                          'query', 'status', 'unreadable', 'validity_best_dropped']},
 'direct id resolved': {'status': 'resolved',
                        'picked': 'concept:alpha',
                        'resolution': None,
                        'resolved_via': 'id',
                        'candidates': [],
                        'note': None,
                        'unreadable': [],
                        'fell_through': [],
                        'query_colbert': 'no search',
                        'calls': ['answer:concept:alpha'],
                        'keys': ['candidates', 'executed', 'grounds', 'kind', 'node', 'query', 'resolved_via',
                                 'status']},
 'direct id unreadable': {'status': 'none',
                          'picked': None,
                          'resolution': None,
                          'resolved_via': None,
                          'candidates': [],
                          'note': 'node not found: concept:ghost',
                          'unreadable': ['concept:ghost'],
                          'fell_through': [],
                          'query_colbert': 'no search',
                          'calls': [],
                          'keys': ['candidates', 'executed', 'grounds', 'kind', 'note', 'query', 'status',
                                   'unreadable']},
 'v3 configured without rescore': {'status': 'resolved',
                                   'picked': 'concept:n1',
                                   'resolution': {'rule': 'production', 'configured': 'v3', 'rescore': 'missing'},
                                   'resolved_via': None,
                                   'candidates': [],
                                   'note': None,
                                   'unreadable': [],
                                   'fell_through': [],
                                   'query_colbert': 'TOKENS',
                                   'calls': ['answer:concept:n1', 'quotes:concept:n1', 'section:concept:n1'],
                                   'keys': ['candidates', 'executed', 'grounds', 'kind', 'node', 'query', 'quotes',
                                            'resolution', 'status', 'validity_best_dropped']},
 'v3 strong lexical top': {'status': 'resolved',
                           'picked': 'concept:n1',
                           'resolution': {'rule': 'v3', 'p': 0.9316},
                           'resolved_via': None,
                           'candidates': [],
                           'note': None,
                           'unreadable': [],
                           'fell_through': [],
                           'query_colbert': 'TOKENS',
                           'calls': ['answer:concept:n1', 'quotes:concept:n1', 'section:concept:n1'],
                           'keys': ['candidates', 'executed', 'grounds', 'kind', 'node', 'query', 'quotes',
                                    'resolution', 'status', 'validity_best_dropped']},
 'v3 weak margin': {'status': 'choose',
                    'picked': None,
                    'resolution': {'rule': 'v3', 'p': 0.0334},
                    'resolved_via': None,
                    'candidates': ['concept:n1', 'concept:n2', 'concept:n3', 'concept:n4', 'concept:n5'],
                    'note': None,
                    'unreadable': [],
                    'fell_through': [],
                    'query_colbert': 'TOKENS',
                    'calls': ['quotes:concept:n1', 'quotes:concept:n2', 'quotes:concept:n3', 'quotes:concept:n4',
                              'quotes:concept:n5', 'section:concept:n1,concept:n2,concept:n3,concept:n4,concept:n5',
                              'material:concept:n1'],
                    'keys': ['candidate_material', 'candidates', 'executed', 'grounds', 'kind', 'query', 'resolution',
                             'status', 'validity_best_dropped']},
 'v3 named exact title': {'status': 'resolved',
                          'picked': 'concept:n3',
                          'resolution': {'rule': 'v3', 'p': 0.9316},
                          'resolved_via': None,
                          'candidates': [],
                          'note': None,
                          'unreadable': [],
                          'fell_through': [],
                          'query_colbert': 'TOKENS',
                          'calls': ['answer:concept:n3', 'quotes:concept:n3', 'section:concept:n3'],
                          'keys': ['candidates', 'executed', 'grounds', 'kind', 'node', 'query', 'quotes',
                                   'resolution', 'status', 'validity_best_dropped']},
 'v3 distant semantic only': {'status': 'none',
                              'picked': None,
                              'resolution': None,
                              'resolved_via': None,
                              'candidates': [],
                              'note': 'no sufficiently close semantic candidate',
                              'unreadable': [],
                              'fell_through': [],
                              'query_colbert': 'TOKENS',
                              'calls': [],
                              'keys': ['candidates', 'executed', 'grounds', 'kind', 'note', 'query', 'status',
                                       'validity_best_dropped']},
 'v3 first pick unreadable': {'status': 'none',
                              'picked': None,
                              'resolution': {'rule': 'v3', 'p': 0.9316},
                              'resolved_via': None,
                              'candidates': [],
                              'note': 'node not found: concept:n1',
                              'unreadable': ['concept:n1'],
                              'fell_through': [],
                              'query_colbert': 'TOKENS',
                              'calls': [],
                              'keys': ['candidates', 'executed', 'grounds', 'kind', 'note', 'query', 'resolution',
                                       'status', 'unreadable', 'validity_best_dropped']},
 'v3 direct id resolved': {'status': 'resolved',
                           'picked': 'concept:alpha',
                           'resolution': None,
                           'resolved_via': 'id',
                           'candidates': [],
                           'note': None,
                           'unreadable': [],
                           'fell_through': [],
                           'query_colbert': 'no search',
                           'calls': ['answer:concept:alpha'],
                           'keys': ['candidates', 'executed', 'grounds', 'kind', 'node', 'query', 'resolved_via',
                                    'status']}}


def test_every_case_has_a_recorded_row():
    assert sorted(EXPECTED) == sorted(c[0] for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_brief_rule_table(case):
    assert observe(*case) == EXPECTED[case[0]]
