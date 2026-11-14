# -*- coding: utf-8 -*-
"""`temporal.mode_scope` 는 `search` 가 한 번 풀어 `_search` 와 `_finish_search_temporal` 에 넘긴다.

세 가지를 지킨다.

1. 틀린 시점 인자가 들어가는 입구(`search`, `api.query.search`)마다 돌려주는 답이 그대로다.
   `RECORDED` 는 풀기를 한 번으로 줄이기 전의 코드에서 이 표를 뽑아 붙인 것이다.
2. `search` 한 번에 `mode_scope` 는 한 번만 불린다. 두 단계는 받은 답을 다시 풀지 않는다.
3. 두 단계는 풀린 답을 반드시 받는다. 빠뜨리면 `TypeError` 다.

스토어는 가짜라 엔진 없이 돈다.
"""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest import mock

from vinc_rag import retrieval as R


class FakeStore:
    scoped_chunk_search = True
    fts_index_remnant = False
    vector_index_remnant = False

    def run(self, query, params=None):
        return [{"c": 3}]


#: 풀기 단계에서 갈리거나 풀린 뒤 스토어를 열기 전에 돌려보내는 입력. (이름, 인자)
BAD_INPUTS = [
    ("mode bogus", dict(mode="sometime")),
    ("mode empty string", dict(mode="")),
    ("mode current + historical", dict(mode="current", historical=True)),
    ("mode current + as_of", dict(mode="current", as_of="2026")),
    ("as_of garbage", dict(as_of="not-a-date")),
    ("as_of month 13", dict(as_of="2026-13")),
    ("as_of word known_at", dict(as_of="known_at")),
    ("known_at garbage", dict(known_at="later")),
    ("known_at month 13", dict(known_at="2026-13")),
    ("as_of and known_at garbage", dict(as_of="nope", known_at="later")),
    ("mode bogus + known_at garbage", dict(mode="sometime", known_at="later")),
    ("mode bogus + as_of garbage", dict(mode="sometime", as_of="nope")),
    ("mode current + as_of + known_at garbage", dict(mode="current", as_of="2026", known_at="later")),
    ("mode bogus + bad kinds", dict(mode="sometime", kinds=["nope"])),
    ("as_of garbage + bad cursor", dict(as_of="nope", cursor="not-a-cursor")),
    ("known_at garbage + bad cursor", dict(known_at="later", cursor="not-a-cursor")),
    ("bad kinds only", dict(kinds=["nope"])),
    ("historical truthy string + mode current", dict(mode="current", historical="yes")),
]


MINIMAL = ["kind", "executed", "rows", "node_hits", "arms", "total_count", "has_more", "next_cursor"]


def shape(res: dict) -> tuple:
    """(최소 응답 키 다음에 붙은 키, error, note). 최소 응답 키로 시작하지 않으면 키 전부를 앞에 적는다."""
    keys = list(res)
    if keys[:len(MINIMAL)] != MINIMAL:
        return ("keys", tuple(keys), res.get("error"), res.get("note"))
    return (",".join(keys[len(MINIMAL):]), res.get("error"), res.get("note"))


def answer(fn):
    try:
        return fn()
    except Exception as exc:
        return ("raised", f"{type(exc).__name__}: {exc}")


def answers() -> dict:
    out = {}
    from api import query as AQ
    for label, kw in BAD_INPUTS:
        kw = dict(owner="u", top_k=5, **kw)
        out[f"search | {label}"] = answer(lambda: shape(R.search("q", store=FakeStore(), **kw)))
        out[f"api.query.search | {label}"] = answer(lambda: shape(AQ.search("q", store=FakeStore(), **kw)))
    return out


RECORDED: dict = {'search | mode bogus': ('error',
                         'invalid_temporal_mode: mode must be general, current or historical',
                         None),
 'api.query.search | mode bogus': ('error',
                                   'invalid_temporal_mode: mode must be general, current or '
                                   'historical',
                                   None),
 'search | mode empty string': ('error',
                                'invalid_as_of: mode must be general, current or historical',
                                None),
 'api.query.search | mode empty string': ('error',
                                          'invalid_as_of: mode must be general, current or '
                                          'historical',
                                          None),
 'search | mode current + historical': ('error',
                                        'invalid_temporal_mode: current mode conflicts with '
                                        'historical=true',
                                        None),
 'api.query.search | mode current + historical': ('error',
                                                  'invalid_temporal_mode: current mode conflicts '
                                                  'with historical=true',
                                                  None),
 'search | mode current + as_of': ('error',
                                   'invalid_temporal_mode: current mode conflicts with as_of; use '
                                   'general or historical',
                                   None),
 'api.query.search | mode current + as_of': ('error',
                                             'invalid_temporal_mode: current mode conflicts with '
                                             'as_of; use general or historical',
                                             None),
 'search | as_of garbage': ('error',
                            "invalid_as_of: invalid as_of 'not-a-date': use YYYY, YYYY-MM, "
                            "YYYY-MM-DD or 'query'",
                            None),
 'api.query.search | as_of garbage': ('error',
                                      "invalid_as_of: invalid as_of 'not-a-date': use YYYY, "
                                      "YYYY-MM, YYYY-MM-DD or 'query'",
                                      None),
 'search | as_of month 13': ('error',
                             "invalid_as_of: invalid as_of '2026-13': use YYYY, YYYY-MM, "
                             "YYYY-MM-DD or 'query'",
                             None),
 'api.query.search | as_of month 13': ('error',
                                       "invalid_as_of: invalid as_of '2026-13': use YYYY, YYYY-MM, "
                                       "YYYY-MM-DD or 'query'",
                                       None),
 'search | as_of word known_at': ('error',
                                  "invalid_time_scope: invalid as_of 'known_at': use YYYY, "
                                  "YYYY-MM, YYYY-MM-DD or 'query'",
                                  None),
 'api.query.search | as_of word known_at': ('error',
                                            "invalid_time_scope: invalid as_of 'known_at': use "
                                            "YYYY, YYYY-MM, YYYY-MM-DD or 'query'",
                                            None),
 'search | known_at garbage': ('error', 'invalid_time_scope: invalid known_at', None),
 'api.query.search | known_at garbage': ('error', 'invalid_time_scope: invalid known_at', None),
 'search | known_at month 13': ('error', 'invalid_time_scope: invalid known_at', None),
 'api.query.search | known_at month 13': ('error', 'invalid_time_scope: invalid known_at', None),
 'search | as_of and known_at garbage': ('error',
                                         "invalid_as_of: invalid as_of 'nope': use YYYY, YYYY-MM, "
                                         "YYYY-MM-DD or 'query'",
                                         None),
 'api.query.search | as_of and known_at garbage': ('error',
                                                   "invalid_as_of: invalid as_of 'nope': use YYYY, "
                                                   "YYYY-MM, YYYY-MM-DD or 'query'",
                                                   None),
 'search | mode bogus + known_at garbage': ('error',
                                            'invalid_temporal_mode: mode must be general, current '
                                            'or historical',
                                            None),
 'api.query.search | mode bogus + known_at garbage': ('error',
                                                      'invalid_temporal_mode: mode must be '
                                                      'general, current or historical',
                                                      None),
 'search | mode bogus + as_of garbage': ('error',
                                         'invalid_temporal_mode: mode must be general, current or '
                                         'historical',
                                         None),
 'api.query.search | mode bogus + as_of garbage': ('error',
                                                   'invalid_temporal_mode: mode must be general, '
                                                   'current or historical',
                                                   None),
 'search | mode current + as_of + known_at garbage': ('error',
                                                      'invalid_temporal_mode: current mode '
                                                      'conflicts with as_of; use general or '
                                                      'historical',
                                                      None),
 'api.query.search | mode current + as_of + known_at garbage': ('error',
                                                                'invalid_temporal_mode: current '
                                                                'mode conflicts with as_of; use '
                                                                'general or historical',
                                                                None),
 'search | mode bogus + bad kinds': ('error',
                                     'invalid_temporal_mode: mode must be general, current or '
                                     'historical',
                                     None),
 'api.query.search | mode bogus + bad kinds': ('error',
                                               'invalid_temporal_mode: mode must be general, '
                                               'current or historical',
                                               None),
 'search | as_of garbage + bad cursor': ('error',
                                         "invalid_as_of: invalid as_of 'nope': use YYYY, YYYY-MM, "
                                         "YYYY-MM-DD or 'query'",
                                         None),
 'api.query.search | as_of garbage + bad cursor': ('error',
                                                   "invalid_as_of: invalid as_of 'nope': use YYYY, "
                                                   "YYYY-MM, YYYY-MM-DD or 'query'",
                                                   None),
 'search | known_at garbage + bad cursor': ('error', 'invalid_time_scope: invalid known_at', None),
 'api.query.search | known_at garbage + bad cursor': ('error',
                                                      'invalid_time_scope: invalid known_at',
                                                      None),
 'search | bad kinds only': ('note,error',
                             'invalid_kinds: unknown kind nope; use concept, record, decision, '
                             'episode',
                             'invalid_kinds: unknown kind nope; use concept, record, decision, '
                             'episode'),
 'api.query.search | bad kinds only': ('note,error',
                                       'invalid_kinds: unknown kind nope; use concept, record, '
                                       'decision, episode',
                                       'invalid_kinds: unknown kind nope; use concept, record, '
                                       'decision, episode'),
 'search | historical truthy string + mode current': ('error',
                                                      'invalid_temporal_mode: current mode '
                                                      'conflicts with historical=true',
                                                      None),
 'api.query.search | historical truthy string + mode current': ('error',
                                                                'invalid_temporal_mode: current '
                                                                'mode conflicts with '
                                                                'historical=true',
                                                                None)}


def leaves():
    """검색 한 번이 스토어 없이 끝까지 돌도록 협력 함수만 바꾼다."""
    st = ExitStack()
    for target, name, kw in (
            (R, "_node_fallback_search", dict(return_value=[])),
            (R.temporal, "finish_nodes", {}),
            (R.temporal, "finish_docs", {}),
            (R.temporal, "doc_sources", dict(return_value={})),
            (R, "_chunk_arms", dict(return_value=([{"id": "d#00001", "score": 0.5, "text": "t"}], "", ["keyword"], False, False))),
            (R, "_attach_doc_titles", dict(return_value={})),
            (R, "_node_semantic_arm", dict(return_value=([], None, None, {}))),
            (R, "_fuse_graph_arm", dict(return_value={}))):
        st.enter_context(mock.patch.object(target, name, **kw))
    return st


VALID_RUNS = {
    "general": {},
    "as_of": {"as_of": "2026-09"},
    "as_of query": {"as_of": "query"},
    "historical": {"mode": "historical"},
    "current": {"mode": "current"},
    "general with known_at": {"known_at": "2026-10"},
    "current with known_at": {"mode": "current", "known_at": "2026-10"},
}


class EveryEntryPointAnswersAsBefore(unittest.TestCase):
    def test_the_table_is_complete(self):
        self.assertEqual(len(RECORDED), 2 * len(BAD_INPUTS))

    def test_each_bad_input_gets_the_recorded_answer_at_each_entry_point(self):
        got = answers()
        self.assertEqual(sorted(got), sorted(RECORDED))
        for key in RECORDED:
            with self.subTest(key):
                self.assertEqual(got[key], RECORDED[key])

    def test_both_entry_points_hand_back_the_minimal_response(self):
        key = "mode bogus"
        self.assertEqual(RECORDED[f"search | {key}"], RECORDED[f"api.query.search | {key}"])
        self.assertEqual(RECORDED[f"search | {key}"][0], "error")


class ScopeIsResolvedOnce(unittest.TestCase):
    def count_calls(self, fn):
        real = R.temporal.mode_scope
        with mock.patch.object(R.temporal, "mode_scope", wraps=real) as spy:
            fn()
        return spy.call_count

    def test_one_search_resolves_the_scope_one_time(self):
        for label, kw in VALID_RUNS.items():
            with self.subTest(label), leaves():
                n = self.count_calls(lambda: R.search("q", store=FakeStore(), owner="u", top_k=5, **kw))
                self.assertEqual(n, 1)

    def test_a_search_that_is_refused_resolves_it_one_time_too(self):
        for label, kw in BAD_INPUTS:
            with self.subTest(label):
                n = self.count_calls(lambda: R.search("q", store=FakeStore(), owner="u", top_k=5, **kw))
                self.assertEqual(n, 1)

    def test_the_stages_refuse_to_run_without_the_scope(self):
        with leaves():
            with self.assertRaises(TypeError):
                R._search("q", store=FakeStore(), owner="u")
            with self.assertRaises(TypeError):
                R._finish_search_temporal({"rows": [], "node_hits": []}, FakeStore(), "q", mode=None,
                                          known_at=None, kwargs={"owner": "u"}, doc_states={})

    def test_the_stages_do_not_resolve_what_they_were_given(self):
        given = R.temporal.mode_scope("current", False, None, "q")
        with leaves():
            self.assertEqual(self.count_calls(lambda: R._search(
                "q", store=FakeStore(), owner="u", mode="current", _mode_scope=given)), 0)
            self.assertEqual(self.count_calls(lambda: R._finish_search_temporal(
                {"rows": [], "node_hits": []}, FakeStore(), "q", mode="current", known_at=None,
                kwargs={"owner": "u"}, doc_states={}, _mode_scope=given)), 0)

    def test_both_stages_read_the_one_scope_the_search_resolved(self):
        """오늘이 두 번 풀리면 날짜가 갈릴 수 있다. 첫 답 하나를 두 단계가 쓴다."""
        days = iter(f"2026-01-0{i}" for i in range(1, 9))

        def moving(mode, historical, as_of, query):
            day = next(days)
            return bool(historical or mode == "historical"), (day, day + "x"), {
                "mode": mode, "as_of": day, "from": day, "until": day + "x", "source": "clock"}
        seen = {}

        def finish_nodes(store, hits, *a, scope=None, **kw):
            seen["late_scope"] = scope
        with leaves(), mock.patch.object(R.temporal, "mode_scope", side_effect=moving), \
                mock.patch.object(R.temporal, "finish_nodes", side_effect=finish_nodes):
            res = R.search("q", store=FakeStore(), owner="u", top_k=5, mode="current")
        self.assertEqual(res["time_scope"]["as_of"], "2026-01-01")
        self.assertEqual(seen["late_scope"], ("2026-01-01", "2026-01-01x"))


if __name__ == "__main__":
    unittest.main()
