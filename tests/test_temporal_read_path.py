# -*- coding: utf-8 -*-
"""읽기 경로의 유효구간 기본값 — 채택된 시간 정책 §4.2.

**이 파일이 지키는 것은 필터가 아니라 그 예외다.** 정책 초안의 파생식은
`current = valid_from <= now` 였고 거기엔 미기재 분기가 없었다. 그런데 실측으로 Record
610건 중 **432건(70.8%)** 이 `valid_from` 을 안 달고 있다. 그 정의로 기본 읽기를 걸면
낡은 것이 아니라 코퍼스의 7할이 사라진다. 그래서 두 규칙은 함께 나가거나 둘 다 안 나간다.

근거: [[decision:temporal-model-operational-policy-2026-09-09]] ·
[[record:current-only-retrieval-would-hide-seven-in-ten-records-2026-09-09]]
"""
from __future__ import annotations

import os
import tempfile
import unittest

from api import fragment as frag
from api import query as q

try:
    import ladybug                          # noqa: F401
    _DRIVER = True
except Exception:                           # pragma: no cover
    _DRIVER = None


class DerivedState(unittest.TestCase):
    """파생식 자체. 스토어가 없어도 도는 순수 함수라 여기서 못 박는다."""

    NOW = "2026-09-09"

    def state(self, vf, vu):
        return q.validity_state(vf, vu, now=self.NOW)

    def test_unstated_validity_is_current(self):
        """이 한 줄이 이 변경의 전부다 — 빠지면 610건 중 432건이 사라진다."""
        self.assertEqual(self.state("", ""), "current")
        self.assertEqual(self.state(None, None), "current")

    def test_an_open_ended_claim_is_current(self):
        self.assertEqual(self.state("2026-01-01", ""), "current")

    def test_only_an_explicit_end_makes_it_historical(self):
        self.assertEqual(self.state("2026-01-01", "2026-09-01"), "historical")
        self.assertEqual(self.state("", "2026-09-01"), "historical")

    def test_the_interval_is_half_open(self):
        """끝나는 순간은 이미 밖이다: [valid_from, valid_until)."""
        self.assertEqual(self.state("", "2026-09-09"), "historical")
        self.assertEqual(self.state("", "2026-09-10"), "current")

    def test_a_start_in_the_future_is_future(self):
        self.assertEqual(self.state("2026-12-01", ""), "future")

    def test_a_year_only_bound_compares_lexicographically(self):
        """`YYYY[-MM[-DD]]` 는 사전식 비교다 — 연도만 적힌 시작은 그 해 내내 current."""
        self.assertEqual(self.state("2026", ""), "current")
        self.assertEqual(self.state("", "2026"), "historical")
        self.assertEqual(self.state("2027", ""), "future")

    def test_now_defaults_to_today_rather_than_a_frozen_constant(self):
        self.assertEqual(q.validity_state("", ""), "current")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ReadPathDefault(unittest.TestCase):
    OWNER = "user:temporal-read-path"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            # 셋 다 같은 낱말로 걸린다 — 갈리는 것은 유효구간뿐이다.
            {"id": "record:quorum-note-unstated", "type": "record",
             "title": "quorum settings for the cluster", "domain": "eng"},
            {"id": "record:quorum-note-current", "type": "record",
             "title": "quorum settings for the cluster today", "domain": "eng",
             "valid_from": "2026-01-01"},
            {"id": "record:quorum-note-retired", "type": "record",
             "title": "quorum settings for the cluster before the move", "domain": "eng",
             "valid_from": "2025-01-01", "valid_until": "2026-01-01"},
        ]}, store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _ids(self, **kw):
        res = q.search("quorum settings", owner=self.OWNER, top_k=10,
                       store=self.store, **kw)
        return [h["id"] for h in res.get("node_hits") or []], res

    def test_the_default_keeps_unstated_and_current_and_drops_the_retired_one(self):
        ids, _ = self._ids()
        self.assertIn("record:quorum-note-unstated", ids,
                      "미기재를 걸렀다 — 이 코퍼스의 7할이 그 상태다")
        self.assertIn("record:quorum-note-current", ids)
        self.assertNotIn("record:quorum-note-retired", ids)

    def test_the_historical_switch_brings_the_retired_one_back(self):
        ids, _ = self._ids(historical=True)
        self.assertIn("record:quorum-note-retired", ids)
        self.assertEqual(len(ids), 3)

    def test_filtering_is_announced_rather_than_silent(self):
        """조용히 줄어든 결과는 '그래프에 이것뿐'으로 읽힌다 — 그것이 이 정책이 막는 고장이다."""
        _, res = self._ids()
        self.assertEqual(res.get("validity_filtered"), 1)
        self.assertIn("historical=true", res.get("validity_note") or "")

    def test_nothing_is_announced_when_nothing_was_hidden(self):
        _, res = self._ids(historical=True)
        self.assertIsNone(res.get("validity_filtered"))

    def test_brief_inherits_the_default_and_the_switch(self):
        out = q.brief("quorum settings for the cluster before the move",
                      owner=self.OWNER, store=self.store)
        self.assertNotEqual((out.get("node") or {}).get("id"),
                            "record:quorum-note-retired")
        back = q.brief("quorum settings for the cluster before the move",
                       owner=self.OWNER, store=self.store, historical=True)
        self.assertEqual((back.get("node") or {}).get("id"),
                         "record:quorum-note-retired")

    def test_a_direct_id_reference_is_not_filtered(self):
        """id 를 그대로 준 것은 명시적 요청이다 — 기본 필터가 가로막으면 안 된다."""
        out = q.brief("what does record:quorum-note-retired say",
                      owner=self.OWNER, store=self.store)
        self.assertEqual(out.get("status"), "resolved")
        self.assertEqual(out["node"]["id"], "record:quorum-note-retired")
        self.assertEqual(out.get("resolved_via"), "id")

    def test_other_kinds_carry_no_interval_and_are_never_hidden(self):
        """유효구간 열은 Record 에만 있다. 다른 종류가 조용히 사라지면 안 된다."""
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:quorum", "type": "concept", "title": "quorum settings"},
            {"id": "decision:quorum-three", "type": "decision",
             "title": "quorum settings are three"},
            {"id": "episode:2026-02-02-quorum-change", "type": "episode",
             "title": "quorum settings changed", "date": "2026-02-02"},
        ]}, store=self.store)
        ids, _ = self._ids()
        for nid in ("concept:quorum", "decision:quorum-three",
                    "episode:2026-02-02-quorum-change"):
            self.assertIn(nid, ids, f"{nid} 가 유효구간 필터에 걸렸다")


class TheFilterMustNotManufactureConfidence(ReadPathDefault):
    """필터는 **보이는 것**을 바꾸지 답을 바꾸지 않는다.

    유효구간 필터가 동점이던 후보 하나를 지우면 남은 하나가 단독 승자가 된다. 그러면
    원래 "모르겠다"(choose)였을 질문이 확신 있는 답이 되고, 그건 §4.2 가 의도한 것이
    아니다. 실측으로 답 불가 질문 6건 중 1건이 그렇게 확정됐다(정직성 0.0 → 0.1667).

    근거: [[record:the-validity-filter-broke-a-tie-and-manufactured-confidence-2026-09-09]]
    """

    def setUp(self):
        super().setUp()
        # 같은 점수로 붙는 둘 — 하나는 폐기됐고 하나는 살아 있다.
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "record:cache-policy-live", "type": "record",
             "title": "cache policy for the edge", "domain": "eng"},
            {"id": "record:cache-policy-retired", "type": "record",
             "title": "cache policy for the edge", "domain": "eng",
             "valid_from": "2025-01-01", "valid_until": "2026-01-01"},
        ]}, store=self.store)

    def test_a_tie_broken_only_by_the_filter_does_not_resolve(self):
        out = q.brief("cache policy for the edge", owner=self.OWNER, store=self.store)
        self.assertEqual(out["status"], "choose",
                         "필터가 동점을 깨서 확신을 만들었다")
        self.assertIn("no longer current", out.get("validity_note") or "")

    def test_it_still_resolves_when_the_winner_is_clear_on_its_own(self):
        """지운 것이 있다고 무조건 못 정하게 하면, 필터가 다른 방향으로 답을 바꾼다."""
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "record:cache-policy-live-and-detailed", "type": "record",
             "title": "cache policy for the edge in detail", "domain": "eng"},
        ]}, store=self.store)
        out = q.brief("cache policy for the edge in detail",
                      owner=self.OWNER, store=self.store)
        self.assertEqual(out["status"], "resolved")
        self.assertEqual(out["node"]["id"], "record:cache-policy-live-and-detailed")

    def test_the_historical_switch_brings_the_hidden_one_back_into_view(self):
        """스위치는 가려진 쪽을 보여 준다.

        ⚠ 여기서 `brief` 의 상태를 보지 않는다. 두 제목이 질의와 **글자 그대로 같아**
        `_brief_resolve` 의 "이름을 댄 경우" 갈래가 랭킹 앞에서 먼저 확정하기 때문이다
        (기존 계약). 그 갈래가 같은 제목 둘 중 하나를 고르는 것은 이 변경과 무관한
        별개의 문제이고, 이 테스트가 볼 것은 필터가 무엇을 감췄나뿐이다."""
        base = q.search("cache policy for the edge", owner=self.OWNER,
                        top_k=10, store=self.store)
        self.assertNotIn("record:cache-policy-retired",
                         [h["id"] for h in base["node_hits"]])
        self.assertTrue(base.get("validity_filtered"))
        both = q.search("cache policy for the edge", owner=self.OWNER,
                        top_k=10, store=self.store, historical=True)
        self.assertIn("record:cache-policy-retired",
                      [h["id"] for h in both["node_hits"]])
        self.assertIsNone(both.get("validity_filtered"))


if __name__ == "__main__":
    unittest.main()
