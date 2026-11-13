# -*- coding: utf-8 -*-
"""유효구간 동점 가드는 확정에 쓴 규칙으로 견준다(VIN-759).

이 파일이 지키는 것: 감춘 행의 점수는 축별 최댓값으로 모인다 · 1위가 그 최댓값에 확정 규칙으로
이기면 되돌리지 않는다 · 못 이기면 튜플로는 작아 보여도 되돌린다.
근거: [[vinc.spec.retrieval-knobs]] §2
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from api import fragment as frag
from api import brief as brief_mod
from api import query as q

try:
    import ladybug                          # noqa: F401
    _DRIVER = True
except Exception:                           # pragma: no cover
    _DRIVER = None

QUERY = "cache policy live rollout window"


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class TieGuardOwnRule(unittest.TestCase):
    OWNER = "user:brief-tie-guard"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
                     "VINC_BRIEF_STRICT": None, "VINC_BRIEF_ANSWERABILITY": None}.items():
            self._env[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=8)
        # 1위는 제목 토큰 4개, 2위는 1개라 엄격 문턱(2)으로 갈린다.
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:cache-policy-live-rollout", "type": "concept",
             "title": "cache policy live rollout", "domain": "eng"},
            {"id": "concept:cache", "type": "concept", "title": "cache", "domain": "eng"},
        ]}, store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _brief(self, hidden):
        """검색 결과에 감춘 행의 점수(축별 최댓값)를 얹어 가드만 시험한다."""
        real = q.search

        def with_hidden(*a, **kw):
            res = real(*a, **kw)
            res["validity_filtered"] = 1
            res["validity_best_dropped"] = list(hidden)
            return res

        with mock.patch.object(brief_mod, "search", with_hidden):
            return q.brief(QUERY, owner=self.OWNER, store=self.store)

    def test_the_winner_resolves_without_hidden_rows(self):
        out = q.brief(QUERY, owner=self.OWNER, store=self.store)
        self.assertEqual(out["status"], "resolved", out.get("candidates"))
        self.assertEqual(out["node"]["id"], "concept:cache-policy-live-rollout")

    def test_a_hidden_row_with_more_hits_but_far_behind_on_title_does_not_undo_it(self):
        """옛 가드는 (5,2) >= (4,4) 로 되돌렸다. 1위는 제목으로 2개 앞서므로 동점이 아니다."""
        out = self._brief((5, 2))
        self.assertEqual(out["status"], "resolved", out.get("validity_note"))
        self.assertNotIn("tied with", out.get("validity_note") or "")

    def test_a_hidden_row_close_on_both_axes_undoes_it(self):
        """(3,4) 는 튜플로 (4,4) 보다 작아 옛 가드를 지났다. 1위가 어느 축에서도 2개 앞서지
        못하므로 그 행이 남았다면 되묻기였다."""
        out = self._brief((3, 4))
        self.assertEqual(out["status"], "choose")
        self.assertIn("tied with the leading candidate", out.get("validity_note") or "")
        self.assertEqual(out["candidates"][0]["id"], "concept:cache-policy-live-rollout")

    def test_hidden_scores_are_collected_per_axis(self):
        """폐기 기록 둘: 하나는 hit 가 높고(속성으로 맞음) 하나는 제목이 높다. 모인 점수는
        두 축의 최댓값이고, 행 하나의 튜플이 아니다."""
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "record:cache-props-old", "type": "record", "title": "cache",
             "domain": "eng", "props": {"note": "policy live rollout window"},
             "valid_from": "2025-01-01", "valid_until": "2026-01-01"},
            {"id": "record:cache-policy-live-old", "type": "record",
             "title": "cache policy live rollout", "domain": "eng",
             "valid_from": "2025-01-01", "valid_until": "2026-01-01"},
        ]}, store=self.store)
        res = q.search(QUERY, owner=self.OWNER, top_k=5, store=self.store)
        self.assertEqual(res.get("validity_filtered"), 2)
        hit, title = res["validity_best_dropped"]
        self.assertEqual(title, 4)
        self.assertGreater(hit, title, res["validity_best_dropped"])


if __name__ == "__main__":
    unittest.main()
