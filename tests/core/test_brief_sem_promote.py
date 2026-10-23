# -*- coding: utf-8 -*-
"""의미 우위 승격(VIN-232) — 되묻기 1위가 의미 팔에서 나머지보다 `_SEM_PROMOTE` 이상
가까우면 확정한다. 플래그(`VINC_BRIEF_SEM_PROMOTE`)는 기본 켜짐이고 `off` 가 비상구다.

이 파일이 지키는 것: 끄면 brief 가 토큰 규칙만 쓰던 때와 한 바이트도 안 바뀐다 · 켜면 문턱에서 정확히
갈린다 · 이름 댄 갈래·단독 후보·유효구간 동점 가드·답변 가능성 게이트를 못 넘는다.
근거: [[decision:gate-d-reopens-on-the-semantic-arms-own-lead-behind-a-flag-left-off-2026-09-29]]
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from api import fragment as frag
from api import node_mirror
from api import query as q

try:
    import ladybug                          # noqa: F401
    _DRIVER = True
except Exception:                           # pragma: no cover
    _DRIVER = None

FLAG = "VINC_BRIEF_SEM_PROMOTE"


def _tie(lead=None, **top):
    """토큰이 비겨 choose 가 될 두 행. 1위에만 의미 우위를 단다."""
    first = {"id": "concept:a", "title": "cache policy live", "hit_tokens": 2,
             "title_tokens": 2, "arms": ["semantic", "title"], "sem_decisive": True, **top}
    if lead is not None:
        first["sem_lead"] = lead
    return [first, {"id": "concept:b", "title": "cache policy notes", "hit_tokens": 2,
                    "title_tokens": 2, "arms": ["semantic", "title"]}]


class PromoteRule(unittest.TestCase):
    """스토어 없이 도는 판정."""

    def setUp(self):
        self._prev = os.environ.pop(FLAG, None)

    def tearDown(self):
        os.environ.pop(FLAG, None)
        if self._prev is not None:
            os.environ[FLAG] = self._prev

    def test_the_flag_is_on_unless_switched_off(self):
        self.assertTrue(q._brief_sem_promote_on())
        for v in ("on", "1", "true", "yes", " ON ", ""):
            os.environ[FLAG] = v
            self.assertTrue(q._brief_sem_promote_on(), v)
        for v in ("off", "0", "false", "no", " OFF "):
            os.environ[FLAG] = v
            self.assertFalse(q._brief_sem_promote_on(), v)

    def test_the_bar_is_where_it_was_decided(self):
        """옮기려면 다시 잴 것 — 내리면 틀린 노드 확정이 는다([[vinc.spec.retrieval-knobs]] §2)."""
        self.assertEqual(q._SEM_PROMOTE, 0.12)

    def test_off_a_lead_changes_nothing(self):
        os.environ[FLAG] = "off"
        self.assertEqual(q._brief_resolve("cache policy", _tie(0.5))[0], "choose")

    def test_on_it_promotes_at_the_bar_and_not_below(self):
        os.environ[FLAG] = "on"
        status, picked = q._brief_resolve("cache policy", _tie(q._SEM_PROMOTE))
        # 폴스루를 위해 순위 전체를 넘긴다 — 엄격 확정과 같은 꼴이다.
        self.assertEqual((status, [h["id"] for h in picked]),
                         ("resolved", ["concept:a", "concept:b"]))
        self.assertEqual(q._brief_resolve("cache policy", _tie(q._SEM_PROMOTE - 1e-6))[0], "choose")
        self.assertEqual(q._brief_resolve("cache policy", _tie())[0], "choose")

    def test_a_token_less_first_can_be_promoted_in_the_ranking_branch(self):
        """결정은 choose 1위 전부를 대상으로 쟀다(X3 모의). 토큰이 0 인 의미 1위도 든다."""
        os.environ[FLAG] = "on"
        hits = _tie(0.2, hit_tokens=0, title_tokens=0, arms=["semantic"])
        hits[1].update(hit_tokens=0, title_tokens=0, arms=["graph"])
        self.assertEqual(q._brief_resolve("무엇이든", hits)[0], "resolved")

    def test_a_lead_on_a_row_that_is_not_first_does_not_promote(self):
        """우위는 보여 줄 첫 노드의 것이어야 한다. 순서 반영을 끈 경우(`VINC_NODE_SEM_RANK=off`)
        의미 1위가 1위 자리에 안 올 수 있다."""
        os.environ[FLAG] = "on"
        hits = [{"id": "concept:a", "title": "cache policy live", "hit_tokens": 2,
                 "title_tokens": 2, "arms": ["title"]},
                {"id": "concept:b", "title": "cache notes", "hit_tokens": 1,
                 "title_tokens": 1, "arms": ["title"], "sem_lead": 0.3}]
        self.assertEqual(q._brief_resolve("cache policy", hits)[0], "choose")

    def test_a_lone_token_less_candidate_still_asks_back(self):
        """#527 의 단독 후보 규칙이 앞에서 돌아온다. 실제로는 나머지가 없어 `sem_lead` 가
        안 붙지만, 붙어 있어도 승격되지 않는다."""
        os.environ[FLAG] = "on"
        hits = [{"id": "concept:a", "title": "alpha", "hit_tokens": 0, "title_tokens": 0,
                 "arms": ["semantic"], "sem_lead": 0.5}]
        self.assertEqual(q._brief_resolve("무엇이든", hits)[0], "choose")

    def test_exact_title_twins_still_ask_back(self):
        """같은 제목이 둘이면 어느 쪽을 물었는지 모른다(#527). 그 판정은 승격 앞이다."""
        os.environ[FLAG] = "on"
        hits = _tie(0.5, title="alpha")
        hits[1]["title"] = "Alpha"
        self.assertEqual(q._brief_resolve("alpha", hits)[0], "choose")


DIM = 4
Q = [1.0, 0.0, 0.0, 0.0]


def _at(distance):
    """질의 벡터 `Q` 에서 코사인 거리가 `distance` 인 단위 벡터."""
    c = 1.0 - distance
    return [c, (1.0 - c * c) ** 0.5, 0.0, 0.0]


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class PromoteOnTheEngine(unittest.TestCase):
    """실엔진 — 제목 팔은 둘을 비기게 올리고 의미 팔만 둘을 가른다."""
    OWNER = "user:brief-sem-promote"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
                     FLAG: None, "VINC_BRIEF_ANSWERABILITY": None,
                     "VINC_NODE_SEMANTIC_ARM": None, "VINC_NODE_SEM_RANK": None}.items():
            self._env[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=DIM)
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:cache-policy-live", "type": "concept",
             "title": "cache policy live", "domain": "eng"},
            {"id": "concept:cache-policy-notes", "type": "concept",
             "title": "cache policy notes", "domain": "eng"},
        ]}, store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _mirror(self, gap, extra=()):
        """live 는 0.05, notes 는 0.05+gap 거리에 둔다. ``extra`` 는 (id, 제목, 거리)."""
        self.store.drop_node_vector_index()
        self.store.run(
            "UNWIND $rows AS r CREATE (:NodeVec {vec_id: r.v, node_id: r.id, "
            "kind: r.k, owner_id: $o, title: r.t, text: r.t, model: 'test', "
            "embedding: r.emb})",
            {"o": self.OWNER, "rows": [
                {"v": node_mirror.vec_id(self.OWNER, nid), "id": nid, "t": t, "emb": _at(d),
                 "k": nid.split(":", 1)[0]}
                for nid, t, d in (("concept:cache-policy-live", "cache policy live", 0.05),
                                  ("concept:cache-policy-notes", "cache policy notes",
                                   0.05 + gap)) + tuple(extra)]})
        self.store.create_node_vector_index()

    def _brief(self, query="cache policy", **env):
        with mock.patch.dict(os.environ, env):
            return q.brief(query, owner=self.OWNER, query_embedding=Q, store=self.store)

    def test_node_hits_keep_the_semantic_distance_and_the_lead(self):
        """제목 팔도 찾은 노드가 거리를 잃던 자리(융합이 제목 행을 남긴다). 우위는 의미
        1위에만 붙고 값은 원시 목록의 1-2위 차다."""
        self._mirror(0.06)
        res = q.search("cache policy", owner=self.OWNER, query_embedding=Q, top_k=5,
                       store=self.store)
        rows = {h["id"]: h for h in res["node_hits"]}
        for nid in ("concept:cache-policy-live", "concept:cache-policy-notes"):
            self.assertIn("title", rows[nid]["arms"])
            self.assertIsNotNone(rows[nid].get("distance"), nid)
        self.assertAlmostEqual(rows["concept:cache-policy-live"]["sem_lead"], 0.06, places=4)
        self.assertNotIn("sem_lead", rows["concept:cache-policy-notes"])

    def test_off_the_brief_is_what_it_was_without_the_new_fields(self):
        """플래그를 끄면 search 가 새로 다는 값(`distance`·`sem_lead`)을 brief 가 읽지
        않는다. 승격이 일어날 스토어에서 두 값을 걷어 낸 답과 글자 그대로 같다."""
        self._mirror(0.15)
        real = q.search

        def without_new_fields(*a, **kw):
            res = real(*a, **kw)
            for h in res.get("node_hits") or []:
                h.pop("sem_lead", None)
                h.pop("distance", None)
            return res

        off = self._brief(**{FLAG: "off"})
        with mock.patch.object(q, "search", without_new_fields):
            stripped = self._brief(**{FLAG: "off"})
        self.assertEqual(off["status"], "choose")
        self.assertEqual(off, stripped)
        # 같은 스토어에서 기본값(켜짐)이면 승격된다 — 위 동일성이 빈 단언이 아니라는 확인이다.
        unset = self._brief()
        self.assertEqual(unset["status"], "resolved")
        self.assertEqual(unset, self._brief(**{FLAG: "on"}))

    def test_on_it_promotes_when_the_lead_clears_the_bar(self):
        self._mirror(0.15)
        out = self._brief(**{FLAG: "on"})
        self.assertEqual(out["status"], "resolved")
        self.assertEqual(out["node"]["id"], "concept:cache-policy-live")

    def test_on_it_asks_back_below_the_bar(self):
        """0.08 은 `_SEM_GAP` 을 넘어 순서는 바꾸지만 확정 문턱(`_SEM_PROMOTE`)에는 못 미친다."""
        self._mirror(0.08)
        out = self._brief(**{FLAG: "on"})
        self.assertEqual(out["status"], "choose")
        self.assertEqual(out["candidates"][0]["id"], "concept:cache-policy-live")

    def _retired_record(self):
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "record:cache-policy-old", "type": "record", "title": "cache policy old",
             "domain": "eng", "valid_from": "2025-01-01", "valid_until": "2026-01-01"},
        ]}, store=self.store)

    def test_the_validity_tie_guard_still_demotes_a_promotion(self):
        """폐기된 기록이 의미 1위였다면 남은 1위의 우위는 필터가 만든 것이다. 승격은 의미
        거리로 견주는 가드를 지나며, 거기서 되돌려진다."""
        self._retired_record()
        self._mirror(0.15, extra=[("record:cache-policy-old", "cache policy old", 0.03)])
        out = self._brief(**{FLAG: "on"})
        self.assertEqual(out["status"], "choose", out.get("node"))
        # 이 문구는 가드가 **확정을** 강등할 때만 붙는다. 꺼 두면 원래 choose 라 안 붙으므로
        # 승격이 일어났고 가드가 되돌렸다는 뜻이다(검색의 필터 공개 문구와 다르다).
        self.assertIn("tied with the leading candidate", out.get("validity_note") or "")
        self.assertNotIn("tied with", self._brief(**{FLAG: "off"}).get("validity_note") or "")
        self.assertEqual(out["candidates"][0]["id"], "concept:cache-policy-live")

    def test_a_retired_row_far_on_the_semantic_arm_does_not_undo_a_promotion(self):
        """토큰으로 비긴 폐기 행이라도 의미로 멀면 우위를 만들지 않았다. 옛 가드는 토큰만 견줘
        이런 승격을 전부 되돌렸다(토큰 없는 승격은 (0,0) 이라 항상 비긴다)."""
        self._retired_record()
        self._mirror(0.15, extra=[("record:cache-policy-old", "cache policy old", 0.40)])
        out = self._brief(**{FLAG: "on"})
        self.assertEqual(out["status"], "resolved", out.get("candidates"))
        self.assertEqual(out["node"]["id"], "concept:cache-policy-live")
        self.assertNotIn("tied with", out.get("validity_note") or "")

    def test_a_retired_row_absent_from_the_semantic_arm_does_not_undo_a_promotion(self):
        self._retired_record()
        self._mirror(0.15)
        self.assertEqual(self._brief(**{FLAG: "on"})["status"], "resolved")

    def test_a_token_decided_resolution_keeps_the_token_tie_guard(self):
        """토큰으로 가른 확정은 그대로 토큰 점수로 견준다(승격이 아니므로)."""
        rows = [{"id": "a", "hit_tokens": 4, "title_tokens": 3, "distance": 0.1},
                {"id": "b", "hit_tokens": 1, "title_tokens": 1, "distance": 0.5}]
        self.assertFalse(q._brief_semantic_tie_clear(rows, {"validity_best_dropped_distance": 0.9}))

    def test_the_semantic_tie_check_reads_the_distance_gap(self):
        rows = [{"id": "a", "hit_tokens": 2, "title_tokens": 2, "distance": 0.10},
                {"id": "b", "hit_tokens": 2, "title_tokens": 2, "distance": 0.30}]
        clear = q._brief_semantic_tie_clear
        self.assertTrue(clear(rows, {}))                                        # 의미 팔이 지운 것 없음
        self.assertTrue(clear(rows, {"validity_best_dropped_distance": 0.10 + q._SEM_PROMOTE}))
        self.assertFalse(clear(rows, {"validity_best_dropped_distance": 0.12}))   # 우위 안쪽
        self.assertFalse(clear(rows[:1], {}))                                   # 단독·이름 확정
        self.assertFalse(clear([dict(rows[0], distance=None), rows[1]], {}))    # 거리를 모르면 옛 비교

    def test_the_answerability_gate_still_demotes_a_promotion(self):
        """주제는 맞는데 질문의 나머지가 아무 데도 없으면 확정하지 않는다. 같은 플래그로
        그 나머지가 없는 질문은 승격되므로, 강등은 게이트가 한 것이다."""
        self._mirror(0.15)
        both = {FLAG: "on", "VINC_BRIEF_ANSWERABILITY": "on"}
        self.assertEqual(self._brief(**both)["status"], "resolved")
        out = self._brief("cache policy zebra walrus quantum", **both)
        self.assertEqual(out["status"], "choose", out.get("node"))
        self.assertIn("answerability_note", out)


if __name__ == "__main__":
    unittest.main()
