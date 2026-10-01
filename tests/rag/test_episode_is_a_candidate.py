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

    def test_the_summary_is_not_a_search_surface(self):
        """요약은 **찾는 표면이 아니다.** 문단이라 토큰이 넓고, 그건 긴 제목이 남의
        질문을 가져가는 것과 같은 기전이다. 실측으로 답 불가 질문 6건 중 1건이 사건으로
        확정됐고(정직성 0.0 → 0.1667), 이 열을 빼니 이득은 그대로 남고 그 고장만 사라졌다.
        근거: [[record:the-episode-summary-is-too-wide-a-search-surface-2026-09-09]]"""
        hits = q._node_fallback_search(self.store, "queue filled and nothing drained",
                                       limit=10, owner=self.OWNER)
        self.assertEqual([h for h in hits if h["kind"] == "episode"], [],
                         "요약으로 사건이 잡혔다 — 넓은 표면이 되돌아왔다")

    def test_the_summary_still_reaches_the_reader_once_the_episode_is_resolved(self):
        """찾는 표면에서 뺀 것이지 재료에서 뺀 것이 아니다."""
        out = q.brief("the nightly pipeline stalled", owner=self.OWNER, store=self.store)
        self.assertEqual(out["node"]["id"], "episode:2026-01-02-the-pipeline-stalled")
        self.assertIn("nothing drained", out.get("one_liner") or "")

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


class EpisodeLinks(EpisodeCandidacy):
    """확정된 사건이 **관계 없이** 답하면 안 된다.

    사건은 MENTIONS 대상이 아니라 발췌가 없다. 그래서 그 갈래가 일찍 빠져나갔는데, 그때
    ABOUT 관계까지 함께 버려졌다. 사건이 해소 후보가 아니던 동안에는 안 보였고, 후보로
    올린 순간부터는 **확정된 사건만 관계가 비는** 상태가 된다.
    근거: [[record:episode-can-never-be-a-brief-candidate-2026-09-09]]
    """

    def setUp(self):
        super().setUp()
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "episode:2026-01-02-the-pipeline-stalled", "type": "episode",
             "title": "The nightly pipeline stalled", "date": "2026-01-02"},
            {"id": "concept:queue-drain", "type": "concept", "title": "Queue drain"},
        ], "edges": [
            {"from": "episode:2026-01-02-the-pipeline-stalled",
             "to": "concept:queue-drain", "type": "about",
             "evidence": "The queue stopped draining at 02:00."},
        ]}, store=self.store)

    def test_a_resolved_episode_carries_its_about_relations(self):
        out = q.brief("the nightly pipeline stalled", owner=self.OWNER,
                      store=self.store)
        links = out.get("links") or {}
        self.assertEqual(links.get("degree"), 1, "사건의 관계가 비었다")
        self.assertEqual(links["items"][0]["id"], "concept:queue-drain")
        self.assertEqual(links["items"][0]["type"], "ABOUT")
        self.assertEqual(links["items"][0]["dir"], "out")

    def test_the_evidence_sentence_comes_with_it_when_asked(self):
        got = q.node_context(owner=self.OWNER,
                             node="episode:2026-01-02-the-pipeline-stalled",
                             limit=3, with_evidence=True, store=self.store)
        it = got["links"]["items"][0]
        self.assertTrue(it["evidence"])
        self.assertIn("stopped draining", it["evidence_text"])

    def test_an_episode_still_has_no_excerpts(self):
        """발췌가 없는 것은 결함이 아니다 — 사건은 MENTIONS 대상이 아니고 요약이 설명이다."""
        got = q.node_context(owner=self.OWNER,
                             node="episode:2026-01-02-the-pipeline-stalled",
                             limit=3, store=self.store)
        self.assertEqual(got.get("excerpts"), [])
        self.assertEqual(got.get("arm"), "none")

    def test_another_owners_concept_is_not_linked_in(self):
        frag.merge_fragment({"owner": "user:someone-else", "nodes": [
            {"id": "concept:queue-drain-elsewhere", "type": "concept",
             "title": "Queue drain elsewhere"},
        ]}, store=self.store)
        got = q.node_context(owner=self.OWNER,
                             node="episode:2026-01-02-the-pipeline-stalled",
                             limit=3, store=self.store)
        ids = [i["id"] for i in got["links"]["items"]]
        self.assertNotIn("concept:queue-drain-elsewhere", ids)


if __name__ == "__main__":
    unittest.main()
