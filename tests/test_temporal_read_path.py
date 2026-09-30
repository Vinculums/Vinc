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
from unittest import mock

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

    def test_a_future_dated_claim_is_not_hidden_but_an_expired_one_still_is(self):
        """감추는 것은 **명시된 `valid_until` 뿐**이다 — 시작이 미래인 것은 아니다.

        `future` 는 `valid_from` 에서 나오므로 `!= "current"` 로 거르면 정책이 감추라고
        한 적 없는 것이 사라진다. `_today()` 가 UTC 라 KST 로 저작한 당일 기록이 매일
        아침 아홉 시간 동안 그 상태로 떨어졌다:
        [[record:validity-state-compares-a-local-dated-claim-against-a-utc-clock-2026-09-14]]."""
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "record:quorum-note-ahead", "type": "record",
             "title": "quorum settings for the cluster next year", "domain": "eng",
             "valid_from": "2099-01-01"},
            # 제목이 질의에 안 걸리는 짝 — 의미 팔로만 닿으므로 그 팔의 판정을 잰다.
            # 제목 팔이 같은 id 를 실어 주면 아래 단언이 빈 단언이 된다.
            {"id": "record:unrelated-note-ahead", "type": "record",
             "title": "an unrelated claim not yet in force", "domain": "eng",
             "valid_from": "2099-01-01"},
        ]}, store=self.store)
        ids, _ = self._ids()
        self.assertIn("record:quorum-note-ahead", ids,
                      "시작이 미래라는 이유로 감췄다 — 정책은 valid_until 만 감춘다")
        self.assertNotIn("record:quorum-note-retired", ids,
                         "명시된 valid_until 은 그대로 감춰야 한다")
        # 의미 팔도 같은 판정이어야 한다 — 지난번에 빠진 자리가 거기다(PR #305).
        # 이 스토어에는 미러가 없으므로 가짜 팔로 같은 모양을 만든다.
        self.store.node_vector_search = lambda emb, k, owner=None: [
            {"id": "record:unrelated-note-ahead", "title": "a", "kind": "record",
             "distance": 0.1},
            {"id": "record:quorum-note-retired", "title": "b", "kind": "record",
             "distance": 0.2}]
        sem, _ = self._ids(query_embedding=[0.0, 0.0, 0.0, 1.0])
        self.assertIn("record:unrelated-note-ahead", sem, "의미 팔이 미래 시작을 감췄다")
        self.assertNotIn("record:quorum-note-retired", sem, "의미 팔이 폐기 기록을 되살렸다")

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

    def test_a_demoted_named_title_asks_back_with_the_rest_of_the_pool(self):
        """RV-23. 제목 완전일치는 한 행만 돌려주므로(폴스루 보호), 동점 가드가 그것을
        강등하면 되묻기 목록이 한 줄이었다. 되묻는데 고를 것이 하나뿐이면 되묻기가 아니다."""
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "record:cache-policy-notes", "type": "record",
             "title": "cache policy notes", "domain": "eng"},
        ]}, store=self.store)
        out = q.brief("cache policy for the edge", owner=self.OWNER, store=self.store)
        self.assertEqual(out["status"], "choose")
        ids = [c["id"] for c in out["candidates"]]
        self.assertEqual(ids[0], "record:cache-policy-live")
        self.assertIn("record:cache-policy-notes", ids)

    def test_a_demoted_list_is_cut_to_the_exposure(self):
        """확정은 순위 전체를 넘긴다(폴스루용). 동점 가드가 그것을 강등하면 노출 수를
        넘는 목록이 그대로 나갔다. 풀이 노출보다 작으면 안 보이므로 노출을 1로 줄여 잰다."""
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "record:edge-notes", "type": "record", "title": "edge notes",
             "domain": "eng"},
        ]}, store=self.store)
        with mock.patch.dict(os.environ, {"VINC_BRIEF_EXPOSE": "1"}):
            out = q.brief("cache policy edge", owner=self.OWNER, store=self.store)
        self.assertEqual(out["status"], "choose")
        self.assertIn("no longer current", out.get("validity_note") or "")
        self.assertEqual([c["id"] for c in out["candidates"]], ["record:cache-policy-live"])

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
        `_brief_resolve` 의 "이름을 댄 경우" 갈래가 판정한다(같은 제목이 둘이면 되묻는다,
        RV-10). 이 테스트가 볼 것은 필터가 무엇을 감췄나뿐이다."""
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


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class DecisionsAndDirectReadsCarryValidity(ReadPathDefault):
    """채운 필드가 읽기에서 실제로 일하는지 — 필터 밖의 두 자리.

    ① Decision 의 구간은 열이 아니라 props 다(§8 항목 5 임시 인코딩). 검색 팔이 그
    자리를 안 보면 71건에 채운 값은 없는 것과 같고, 폐기된 결정이 후속과 동점으로 나온다
    (라이브 재현 2026-09-11: `decision:d44`).
    ② id 로 직접 연 노드는 필터를 거치지 않는다. 그러면 노드 **자신**이 historical 이라고
    말해야 하는데, 여태 부착 목록만 구간을 실었다(라이브 재현: `record:fact-engines-orphans`
    가 현재형 사실로 돌아왔다).
    """

    def setUp(self):
        super().setUp()
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "decision:cache-policy-old", "type": "decision", "domain": "eng",
             "title": "cache policy is nightly",
             "props": {"valid_from": "2025-01-01", "valid_until": "2026-01-01",
                       "superseded_by": "decision:cache-policy-new"}},
            {"id": "decision:cache-policy-new", "type": "decision", "domain": "eng",
             "title": "cache policy is hourly", "props": {"valid_from": "2026-01-01"}},
            {"id": "decision:cache-policy-design", "type": "decision", "domain": "eng",
             "title": "cache policy design sketch"},
        ]}, store=self.store)

    def _hits(self, **kw):
        res = q.search("cache policy", owner=self.OWNER, top_k=10,
                       store=self.store, **kw)
        return [h["id"] for h in res.get("node_hits") or []], res

    def test_a_retired_decision_leaves_the_default_read_and_is_counted(self):
        ids, res = self._hits()
        self.assertNotIn("decision:cache-policy-old", ids, "props 의 구간을 안 읽었다")
        self.assertIn("decision:cache-policy-new", ids)
        self.assertIn("decision:cache-policy-design", ids, "미기재 결정을 걸렀다")
        self.assertEqual(res.get("validity_filtered"), 1)

    def test_the_historical_switch_brings_the_retired_decision_back(self):
        ids, res = self._hits(historical=True)
        self.assertIn("decision:cache-policy-old", ids)
        self.assertIsNone(res.get("validity_filtered"))

    def test_node_context_says_the_node_itself_is_historical(self):
        rec = q.node_context(owner=self.OWNER, node="record:quorum-note-retired",
                             store=self.store)
        self.assertEqual(rec["validity"], "historical")
        self.assertEqual(rec["valid_until"], "2026-01-01")
        dec = q.node_context(owner=self.OWNER, node="decision:cache-policy-old",
                             store=self.store)
        self.assertEqual(dec["validity"], "historical")
        self.assertEqual(dec["superseded_by"], "decision:cache-policy-new")
        live = q.node_context(owner=self.OWNER, node="decision:cache-policy-new",
                              store=self.store)
        self.assertEqual(live["validity"], "current")
        self.assertEqual(live["superseded_by"], "")

    def test_the_semantic_arm_does_not_bring_a_retired_node_back(self):
        """미러에는 구간이 없다. 제목 팔만 걸러 내면 폐기된 노드가 의미 팔로 되돌아온다
        (라이브 재현 2026-09-11: 배포 뒤에도 d44 가 semantic 표로 검색에 남았다).
        미러 대신 가짜 팔로 같은 모양을 만든다 — 이 테스트 스토어에는 미러가 없다."""
        fake = [{"id": "record:quorum-note-retired", "title": "x", "kind": "record",
                 "distance": 0.1},
                {"id": "decision:cache-policy-old", "title": "y", "kind": "decision",
                 "distance": 0.2},
                {"id": "decision:cache-policy-new", "title": "z", "kind": "decision",
                 "distance": 0.3}]
        self.store.node_vector_search = lambda emb, k, owner=None: list(fake)
        emb = [0.0, 0.0, 0.0, 1.0]
        res = q.search("cache policy", owner=self.OWNER, top_k=10, store=self.store,
                       query_embedding=emb)
        ids = [h["id"] for h in res.get("node_hits") or []]
        self.assertNotIn("record:quorum-note-retired", ids, "의미 팔이 폐기 기록을 되살렸다")
        self.assertNotIn("decision:cache-policy-old", ids, "의미 팔이 폐기 결정을 되살렸다")
        self.assertIn("decision:cache-policy-new", ids)
        # 숨긴 노드 수다: 두 팔이 함께 지운 cache-policy-old 는 한 번 센다(RV-17).
        self.assertEqual(res.get("validity_filtered"), 2,
                         "cache-policy-old(제목·의미 팔) + quorum-note-retired(의미 팔)")
        both = q.search("cache policy", owner=self.OWNER, top_k=10, store=self.store,
                        query_embedding=emb, historical=True)
        self.assertIn("decision:cache-policy-old", [h["id"] for h in both["node_hits"]])

    def test_brief_by_id_announces_the_retirement_and_the_successor(self):
        out = q.brief("decision:cache-policy-old", owner=self.OWNER, store=self.store)
        self.assertEqual(out["status"], "resolved")
        self.assertEqual(out["resolved_via"], "id")
        self.assertEqual(out["node"]["validity"], "historical")
        self.assertIn("decision:cache-policy-new", out.get("validity_note") or "")
        fresh = q.brief("decision:cache-policy-new", owner=self.OWNER, store=self.store)
        self.assertEqual(fresh["node"]["validity"], "current")
        self.assertNotIn("validity_note", fresh)


if __name__ == "__main__":
    unittest.main()
