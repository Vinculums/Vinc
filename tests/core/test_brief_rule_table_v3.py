# -*- coding: utf-8 -*-
"""brief 규칙 표(v3 쪽): 노드 재채점이 적용된 질의에서만 닿는 v3 갈래를 한 줄씩 적었다.

`test_brief_rule_table.py` 의 줄은 재채점이 안 된 v3 를 포함해 몇 갈래를 못 덮는다. 로컬에는 ColBERT 투영이 없어
실제 스토어로는 재채점이 적용된 질의를 못 만든다. 그래서 이 갈래는 정해 둔 검색 응답으로만 닿는다. 기대값은 규칙을
표로 옮기기 전 코드에서 그대로 받아 적은 것이다. 해소 규칙을 옮기거나 쪼갤 때 이 표가 같아야 하고, 일부러 바꾸는
변경만 기대값을 고친다. 관찰 방식(`observe`)은 앞 표와 같다.
"""
from __future__ import annotations

import pytest
from test_brief_rule_table import _ALL, _FAR, _LEX, _hit, _res, _v3_hits, observe

_NAMED_TWICE = [dict(h, title="Twin") if h["id"] in ("concept:n1", "concept:n2") else h for h in _v3_hits()]


def _far_semantic_only_head():
    """재채점 전 앞 다섯은 멀리 있는 의미만 후보이고, ColBERT 가 토큰 맞은 행을 맨 앞으로 올렸다."""
    far = [_hit(i, colbert=0.5 - i * 0.01, prod_rank=i, distance=0.5 + i * 0.01) for i in range(1, 6)]
    lex = _hit(7, colbert=0.9, prod_rank=7, hit_tokens=2, title_tokens=1, arms=("lexical",), distance=None)
    return [lex] + far


def _colbert_head_is_semantic_only():
    """ColBERT 앞 다섯은 멀리 있는 의미만이지만 재채점 전 1위는 토큰으로 맞았다."""
    far = [_hit(i, colbert=0.9 - i * 0.01, prod_rank=i + 1, distance=0.6) for i in range(1, 6)]
    lex = _hit(9, colbert=0.1, prod_rank=1, hit_tokens=2, arms=("lexical",), distance=None)
    return far + [lex]


#: (이름, 규칙, 질문, search 응답, node_context 가 여는 id)
CASES = [
    ("v3 abstains on the pre-rescore head", "v3", "what is it", _res(_far_semantic_only_head(), rescored=True), _ALL),
    ("v3 ignores a colbert head that is semantic only", "v3", "what is it",
     _res(_colbert_head_is_semantic_only(), rescored=True), _ALL),
    ("v3 features missing asks back in fused order", "v3", "the release train", _res(_LEX, rescored=True), _ALL),
    ("v3 two titles equal the question", "v3", "twin", _res(_NAMED_TWICE, rescored=True), _ALL),
    ("v3 hidden tie is not guarded", "v3", "what is it",
     _res(_v3_hits(), rescored=True, validity_filtered=1, validity_best_dropped=[2, 1],
          validity_best_dropped_distance=0.34, validity_note="hid 1 claim"), _ALL),
    ("v3 rescored without hits", "v3", "what is it", _res([], rescored=True), _ALL),
    ("v3 skipped rescore names the reason", "v3", "the release train",
     _res(_LEX, node_rescore={"state": "skipped", "reason": "tokens missing"}), _ALL),
    ("v3 skipped rescore then distant semantic only", "v3", "zzz qqq",
     _res(_FAR, node_rescore={"state": "skipped", "reason": "no owner scope"}), _ALL),
]

EXPECTED: dict = {'v3 abstains on the pre-rescore head': {'status': 'none',
                                         'picked': None,
                                         'resolution': None,
                                         'resolved_via': None,
                                         'candidates': [],
                                         'note': 'no sufficiently close semantic candidate',
                                         'unreadable': [],
                                         'fell_through': [],
                                         'query_colbert': 'TOKENS',
                                         'calls': [],
                                         'keys': ['candidates', 'executed', 'grounds', 'kind', 'note', 'query',
                                                  'status', 'validity_best_dropped']},
 'v3 ignores a colbert head that is semantic only': {'status': 'choose',
                                                     'picked': None,
                                                     'resolution': {'rule': 'v3', 'p': 0.233},
                                                     'resolved_via': None,
                                                     'candidates': ['concept:n1', 'concept:n2', 'concept:n3',
                                                                    'concept:n4', 'concept:n5'],
                                                     'note': None,
                                                     'unreadable': [],
                                                     'fell_through': [],
                                                     'query_colbert': 'TOKENS',
                                                     'calls': ['quotes:concept:n1', 'quotes:concept:n2',
                                                               'quotes:concept:n3', 'quotes:concept:n4',
                                                               'quotes:concept:n5',
                                                               'section:concept:n1,concept:n2,concept:n3,concept:n4,concept:n5',
                                                               'material:concept:n1'],
                                                     'keys': ['candidate_material', 'candidates', 'executed',
                                                              'grounds', 'kind', 'query', 'resolution', 'status',
                                                              'validity_best_dropped']},
 'v3 features missing asks back in fused order': {'status': 'choose',
                                                  'picked': None,
                                                  'resolution': {'rule': 'v3', 'p': None},
                                                  'resolved_via': None,
                                                  'candidates': ['concept:n1', 'concept:n2', 'concept:n3'],
                                                  'note': None,
                                                  'unreadable': [],
                                                  'fell_through': [],
                                                  'query_colbert': 'TOKENS',
                                                  'calls': ['quotes:concept:n1', 'quotes:concept:n2',
                                                            'quotes:concept:n3',
                                                            'section:concept:n1,concept:n2,concept:n3',
                                                            'material:concept:n1'],
                                                  'keys': ['candidate_material', 'candidates', 'executed', 'grounds',
                                                           'kind', 'query', 'resolution', 'status',
                                                           'validity_best_dropped']},
 'v3 two titles equal the question': {'status': 'resolved',
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
 'v3 hidden tie is not guarded': {'status': 'resolved',
                                  'picked': 'concept:n1',
                                  'resolution': {'rule': 'v3', 'p': 0.9962},
                                  'resolved_via': None,
                                  'candidates': [],
                                  'note': None,
                                  'unreadable': [],
                                  'fell_through': [],
                                  'query_colbert': 'TOKENS',
                                  'calls': ['answer:concept:n1', 'quotes:concept:n1', 'section:concept:n1'],
                                  'keys': ['candidates', 'executed', 'grounds', 'kind', 'node', 'query', 'quotes',
                                           'resolution', 'status', 'validity_best_dropped', 'validity_filtered',
                                           'validity_note']},
 'v3 rescored without hits': {'status': 'none',
                              'picked': None,
                              'resolution': {'rule': 'production', 'configured': 'v3', 'rescore': 'applied'},
                              'resolved_via': None,
                              'candidates': [],
                              'note': None,
                              'unreadable': [],
                              'fell_through': [],
                              'query_colbert': 'TOKENS',
                              'calls': [],
                              'keys': ['candidates', 'executed', 'grounds', 'kind', 'query', 'resolution', 'status',
                                       'validity_best_dropped']},
 'v3 skipped rescore names the reason': {'status': 'resolved',
                                         'picked': 'concept:n1',
                                         'resolution': {'rule': 'production',
                                                        'configured': 'v3',
                                                        'rescore': 'skipped',
                                                        'reason': 'tokens missing'},
                                         'resolved_via': None,
                                         'candidates': [],
                                         'note': None,
                                         'unreadable': [],
                                         'fell_through': [],
                                         'query_colbert': 'TOKENS',
                                         'calls': ['answer:concept:n1', 'quotes:concept:n1', 'section:concept:n1'],
                                         'keys': ['candidates', 'executed', 'grounds', 'kind', 'node', 'query',
                                                  'quotes', 'resolution', 'status', 'validity_best_dropped']},
 'v3 skipped rescore then distant semantic only': {'status': 'none',
                                                   'picked': None,
                                                   'resolution': {'rule': 'production',
                                                                  'configured': 'v3',
                                                                  'rescore': 'skipped',
                                                                  'reason': 'no owner scope'},
                                                   'resolved_via': None,
                                                   'candidates': [],
                                                   'note': 'no sufficiently close semantic candidate',
                                                   'unreadable': [],
                                                   'fell_through': [],
                                                   'query_colbert': 'TOKENS',
                                                   'calls': [],
                                                   'keys': ['candidates', 'executed', 'grounds', 'kind', 'note',
                                                            'query', 'resolution', 'status',
                                                            'validity_best_dropped']}}


def test_every_case_has_a_recorded_row():
    assert sorted(EXPECTED) == sorted(c[0] for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_brief_rule_table_v3(case):
    assert observe(*case) == EXPECTED[case[0]]
