# -*- coding: utf-8 -*-
"""Episode 도 제목 팔의 후보가 된다.

여기 없던 동안 사건은 **어떤 팔로도** 후보가 될 수 없었다 — 제목 팔의 UNION 이 셋뿐이고
그래프 팔은 Concept 만 승격하므로, 스토어에 사건이 몇 건 있든 `brief` 가 그 이름을 댈
방법이 0이었다. 낮은 점수가 아니라 구조적 0이다.

Decision 이 v12 전까지 같은 자리에 있었고, 그때 남긴 이유도 같다.

근거: [[record:episode-can-never-be-a-brief-candidate-2026-09-09]] ·
[[record:adding-episode-to-the-title-arm-is-a-pure-gain-2026-09-09]]
"""
from __future__ import annotations

import unittest

import os
import tempfile

from api import fragment as frag
from api import query as q

try:                                        # test_brief.py 와 같은 가드
    import ladybug                          # noqa: F401
    _DRIVER = True
except Exception:                           # pragma: no cover
    _DRIVER = None


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class EpisodeCandidacy(unittest.TestCase):
    OWNER = "user:episode-candidate"

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
            {"id": "episode:2026-01-02-the-pipeline-stalled", "type": "episode",
             "title": "The nightly pipeline stalled", "date": "2026-01-02",
             "summary": "A queue filled and nothing drained until the lock was released."},
            {"id": "concept:queue-drain", "type": "concept", "title": "Queue drain",
             "one_liner": "How a backlog empties."},
        ]}, store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_an_episode_can_be_named_by_the_title_arm(self):
        hits = q._node_fallback_search(self.store, "the nightly pipeline stalled",
                                       limit=10, owner=self.OWNER)
        ids = [h["id"] for h in hits]
        self.assertIn("episode:2026-01-02-the-pipeline-stalled", ids,
                      "사건이 제목 팔에 안 올랐다 — 구조적으로 도달 불가라는 뜻이다")
        got = next(h for h in hits if h["kind"] == "episode")
        self.assertEqual(got["matched_in"], "title")

    def test_the_summary_is_searchable_the_way_a_concept_one_liner_is(self):
        hits = q._node_fallback_search(self.store, "queue filled and nothing drained",
                                       limit=10, owner=self.OWNER)
        got = [h for h in hits if h["kind"] == "episode"]
        self.assertTrue(got, "요약으로는 못 찾았다")
        self.assertEqual(got[0]["matched_in"], "one_liner")

    def test_another_owners_episode_is_not_a_candidate(self):
        frag.merge_fragment({"owner": "user:someone-else", "nodes": [
            {"id": "episode:2026-01-03-someone-elses-outage", "type": "episode",
             "title": "The nightly pipeline stalled somewhere else",
             "date": "2026-01-03"},
        ]}, store=self.store)
        ids = [h["id"] for h in q._node_fallback_search(
            self.store, "the nightly pipeline stalled", limit=10, owner=self.OWNER)]
        self.assertNotIn("episode:2026-01-03-someone-elses-outage", ids,
                         "새 갈래가 owner 스코프를 안 걸었다")

    def test_brief_resolves_an_episode_and_can_open_it(self):
        out = q.brief("the nightly pipeline stalled", owner=self.OWNER, store=self.store)
        self.assertEqual(out["status"], "resolved", out.get("note"))
        self.assertEqual(out["node"]["id"], "episode:2026-01-02-the-pipeline-stalled")
        # 열리는 것까지 본다 — 후보에만 올리고 상세가 안 열리면 폴스루가 된다.
        self.assertEqual(out.get("fell_through", []), [])
        self.assertTrue(out.get("one_liner"), "사건의 서술이 재료로 안 실렸다")


if __name__ == "__main__":
    unittest.main()
