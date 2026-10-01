# -*- coding: utf-8 -*-
"""시점 읽기(`as_of`)와 질문 속 시점 — 계획 `vinc.plan.temporal-edges-point-in-time-document-validity-2026-09-28` §1.

재는 것:
  1. **기본값은 오늘과 같다.** `as_of` 를 안 주면 검색 결과가 한 바이트도 안 바뀐다(계획의 첫 수락 조건)
  2. **기간 읽기**: D 에 끝난 주장은 D 하루 전에는 보이고 D 에는 안 보인다. 그 기간 뒤에 시작한 것도
     안 보인다. 무엇으로 읽었는지 `time_scope` 에 싣는다
  3. **동점 가드가 그대로다**: 기간 밖이라 감춘 것이 1위와 동점이었으면 brief 는 확정하지 않는다
  4. **질문 속 시점(EXAQT 방식, 규칙만)**: 절대 시점 하나만 읽는다. 없거나 둘 이상이면 고르지 않는다
  5. **틀린 값은 오류다**: 조용히 오늘로 떨어지면 부르는 쪽은 과거를 읽은 줄 안다
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import query as q  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


class TimeScope(unittest.TestCase):

    def test_periods(self):
        self.assertEqual(q.time_scope("2026"), ("2026-01-01", "2027-01-01"))
        self.assertEqual(q.time_scope("2026-08"), ("2026-08-01", "2026-09-01"))
        self.assertEqual(q.time_scope("2026-12"), ("2026-12-01", "2027-01-01"))
        self.assertEqual(q.time_scope("2026-08-31"), ("2026-08-31", "2026-09-01"))

    def test_bad_values(self):
        for v in ("", "2026-13", "2026-02-30", "26-08", "2026/08", "August", "query"):
            self.assertIsNone(q.time_scope(v), v)

    def test_holds_during(self):
        aug = q.time_scope("2026-08")
        self.assertTrue(q.holds_during("", "", aug))                    # 미기재는 열린 끝
        self.assertTrue(q.holds_during("2026-08-20", "", aug))          # 기간 안에 시작
        self.assertTrue(q.holds_during("", "2026-08-02", aug))          # 기간 안에 끝남
        self.assertFalse(q.holds_during("2026-09-01", "", aug))         # 기간 뒤에 시작
        self.assertFalse(q.holds_during("", "2026-08-01", aug))         # 기간 첫날에 이미 끝남
        # 짧게 적힌 끝점은 그 기간의 첫날이다(`validity_state` 와 같은 규칙).
        self.assertFalse(q.holds_during("", "2026-08", aug))
        self.assertTrue(q.holds_during("2026", "", aug))

    def test_a_day_is_one_day(self):
        day = q.time_scope("2026-09-10")
        self.assertTrue(q.holds_during("", "2026-09-11", day))
        self.assertFalse(q.holds_during("", "2026-09-10", day))


class QueryTime(unittest.TestCase):

    def _labels(self, text):
        return [h["as_of"] for h in q.query_time(text)]

    def test_forms_it_reads(self):
        cases = {
            "what did we decide on 2026-08-15": ["2026-08-15"],
            "the plan as of 2026.08": ["2026-08"],
            "2026년 8월 15일 회의에서 정한 것": ["2026-08-15"],
            "2026년 8월에 정한 배포 정책": ["2026-08"],
            "2025년의 요금제": ["2025"],
            "what held in August 2026": ["2026-08"],
            "notes from Aug 15, 2026": ["2026-08-15"],
            "the 15 August 2026 decision": ["2026-08-15"],
            "pricing in 2025": ["2025"],
            "during 2024 we used Louvain": ["2024"],
        }
        for text, want in cases.items():
            self.assertEqual(self._labels(text), want, text)

    def test_what_it_does_not_read(self):
        for text in ("the 2025 roadmap",            # 전치사 없는 맨 연도
                     "ladybug 0.20.4 and v0.9.6",   # 버전
                     "last week's decision",         # 상대 시점
                     "record:fact-2026-09-28-x9",    # id 의 일부
                     "record:계획-2026년 8월",         # 한국어 날짜도 id 의 일부
                     "docs/August 2026/plan.md",     # 문서 경로 안의 영어 월
                     "port 2026"):
            self.assertEqual(self._labels(text), [], text)

    def test_two_dates_are_both_reported(self):
        self.assertEqual(self._labels("between 2026-08-01 and 2026-09-01"),
                         ["2026-08-01", "2026-09-01"])

    def test_the_same_date_twice_is_one(self):
        self.assertEqual(self._labels("2026-08 and again 2026년 8월"), ["2026-08"])


class ResolveAsOf(unittest.TestCase):

    def test_default_is_no_scope(self):
        self.assertEqual(q.resolve_as_of(None, "x"), (None, None))
        self.assertEqual(q.resolve_as_of("", "x"), (None, None))

    def test_argument(self):
        scope, info = q.resolve_as_of("2026-08", "x")
        self.assertEqual(scope, ("2026-08-01", "2026-09-01"))
        self.assertEqual(info["source"], "argument")

    def test_query_with_one_date(self):
        scope, info = q.resolve_as_of("query", "배포 정책 2026년 8월")
        self.assertEqual(scope, ("2026-08-01", "2026-09-01"))
        self.assertEqual((info["source"], info["as_of"]), ("query", "2026-08"))

    def test_query_without_or_with_two_dates_is_not_applied(self):
        for text in ("deployment policy", "2026-08 vs 2026-09"):
            scope, info = q.resolve_as_of("query", text)
            self.assertIsNone(scope, text)
            self.assertFalse(info["applied"], text)
            self.assertIn("today's view", info["note"])

    def test_a_wrong_value_raises(self):
        with self.assertRaises(ValueError):
            q.resolve_as_of("last week", "x")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치")
class OnEngine(unittest.TestCase):
    """실 엔진. 오늘 읽기와 기간 읽기가 같은 스토어에서 갈리는지 본다."""

    OWNER = "tenant:local"

    def setUp(self):
        from api import fragment as frag
        from store.backend import open_store
        from store.config import load_config
        keys = ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR", "MEMORY_EMBEDDING_MODEL")
        self._keep = {k: os.environ.get(k) for k in keys}
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        os.environ["MEMORY_DATA_DIR"] = os.path.join(tempfile.mkdtemp(), "store")
        os.environ["MEMORY_EMBEDDING_MODEL"] = "local-en"
        cfg = load_config()
        self.store = open_store(cfg)
        self.store.provision(dim=cfg.embedding_dim)
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "record:pricing-old", "type": "record", "title": "Pricing plan tiers",
             "valid_from": "2026-01-01", "valid_until": "2026-08-20"},
            {"id": "record:pricing-new", "type": "record", "title": "Pricing plan tiers revised",
             "valid_from": "2026-08-20"},
            {"id": "record:pricing-later", "type": "record", "title": "Pricing plan tiers draft",
             "valid_from": "2026-10-01"},
        ], "edges": []}, store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _ids(self, **kw):
        res = q.search("pricing plan tiers", owner=self.OWNER, store=self.store, top_k=10, **kw)
        return res, sorted(h["id"] for h in res.get("node_hits") or [])

    def test_the_default_is_today_and_unchanged(self):
        base, ids = self._ids()
        same, _ = self._ids(as_of=None)
        self.assertEqual(base, same)
        self.assertNotIn("time_scope", base)
        # 오늘(2026-09-28 이후)은 old 가 끝났다. later 는 future 라 오늘 읽기는 감추지 않는다.
        self.assertNotIn("record:pricing-old", ids)
        self.assertIn("record:pricing-new", ids)

    def test_a_period_shows_what_held_then(self):
        res, ids = self._ids(as_of="2026-08-19")
        self.assertEqual(ids, ["record:pricing-old"])
        self.assertEqual(res["time_scope"]["source"], "argument")
        self.assertEqual(res["validity_filtered"], 2)
        self.assertIn("2026-08-19", res["validity_note"])
        # 끝난 그날에는 없다(반개구간).
        _, ids = self._ids(as_of="2026-08-20")
        self.assertEqual(ids, ["record:pricing-new"])

    def test_a_month_keeps_everything_that_held_in_it(self):
        _, ids = self._ids(as_of="2026-08")
        self.assertEqual(ids, ["record:pricing-new", "record:pricing-old"])

    def test_the_question_can_name_the_date(self):
        res = q.search("pricing plan tiers in 2026년 8월 19일", owner=self.OWNER,
                       store=self.store, top_k=10, as_of="query")
        self.assertEqual(res["time_scope"]["as_of"], "2026-08-19")
        self.assertEqual([h["id"] for h in res["node_hits"]], ["record:pricing-old"])

    def test_a_wrong_value_is_an_error_not_today(self):
        res = q.search("pricing", owner=self.OWNER, store=self.store, as_of="last week")
        self.assertFalse(res["executed"])
        self.assertTrue(res["error"].startswith("invalid_as_of"))

    def test_brief_carries_the_scope(self):
        res = q.brief("pricing plan tiers", owner=self.OWNER, store=self.store,
                      as_of="2026-08-19")
        self.assertEqual(res.get("time_scope", {}).get("as_of"), "2026-08-19")
        self.assertIn(res["status"], ("resolved", "choose"))

    def test_direct_id_applies_an_explicit_period_but_keeps_default_bypass(self):
        old = "record:pricing-old"
        default = q.brief(f"what does {old} say", owner=self.OWNER, store=self.store)
        self.assertEqual(default["status"], "resolved")
        held = q.brief(f"what does {old} say", owner=self.OWNER, store=self.store,
                       as_of="2026-08-19")
        self.assertEqual(held["status"], "resolved")
        self.assertEqual(held["time_scope"]["as_of"], "2026-08-19")
        expired = q.brief(f"what does {old} say", owner=self.OWNER, store=self.store,
                          as_of="2026-08-20")
        self.assertEqual(expired["status"], "none")
        self.assertEqual(expired["validity_filtered"], 1)
        self.assertEqual(expired["time_scope"]["as_of"], "2026-08-20")

    def test_direct_id_rejects_an_invalid_period(self):
        out = q.brief("record:pricing-old", owner=self.OWNER, store=self.store,
                      as_of="last week")
        self.assertFalse(out["executed"])
        self.assertTrue(out["error"].startswith("invalid_as_of"))

    def test_period_filter_cannot_turn_a_tie_into_a_resolved_answer(self):
        from api import fragment as frag
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "record:period-tie-live", "type": "record",
             "title": "edge cache policy", "valid_from": "2026-01-01"},
            {"id": "record:period-tie-later", "type": "record",
             "title": "edge cache policy", "valid_from": "2026-09-01"},
        ]}, store=self.store)
        out = q.brief("edge cache policy", owner=self.OWNER, store=self.store,
                      as_of="2026-08")
        self.assertEqual(out["status"], "choose")
        self.assertIn("did not hold", out.get("validity_note") or "")


class Surfaces(unittest.TestCase):

    def test_the_tools_pass_it_through(self):
        from vinc_mcp import tools

        class _B:
            owner = "tenant:local"

            def request(self, method, path, params=None, body=None, **kw):
                self.seen = params
                return {}

        b = _B()
        tools._t_search({"query": "x", "as_of": "2026-08"}, b)
        self.assertEqual(b.seen["as_of"], "2026-08")
        tools._t_brief({"query": "x", "as_of": "query"}, b)
        self.assertEqual(b.seen["as_of"], "query")
        specs = {s["name"]: s for s in tools.specs()}
        for name in ("vinc_search", "vinc_brief"):
            self.assertIn("as_of", specs[name]["inputSchema"]["properties"], name)

    def test_rest_routes_accept_it(self):
        from vinc_mcp import rest_routes
        bodies = {r.path: r.body for r in rest_routes.ROUTES if r.method == "POST"}
        self.assertIn("as_of", bodies["/v1/search"])
        self.assertIn("as_of", bodies["/v1/brief"])


if __name__ == "__main__":
    unittest.main()
