# -*- coding: utf-8 -*-
"""VIN-89 — 관계 목록의 **이어 읽을 자리**(`links_offset` → `next_offset`).

상한(`_NODE_LINKS_CAP` = 24)은 전부터 있었고 `truncated` 로 잘렸다고 말하기도 했다.
없던 것은 **그 뒤로 가는 길**이다: 25개짜리 이웃에서 24줄과 `total:25` 를 받고 나면
스물다섯째 줄에 닿을 방법이 아예 없었다(실측 2026-09-20, D0 가 같은 픽스처로 먼저
관측했다). 화면이 "보이는 것이 전부"라고 말할 수밖에 없는 상태였다.

근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §6]] — 총수와 연속을 보이고,
보이는 상한을 이웃 전체라고 말하지 말 것.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import query as q  # noqa: E402
from store import pkns  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class NodeLinksContinuationE2E(unittest.TestCase):
    OWNER = "user:a"
    N = 25              # 상한 24 를 하나 넘긴다 — 경계가 한 줄일 때가 제일 잘 샌다

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
        }.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())
        self.store.provision(dim=4)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _concept(self, cid, title):
        self.store.run(
            "MERGE (n:Concept {concept_id:$id}) SET n.title=$t, n.domain='d', "
            "n.owner_id=$o",
            {"id": pkns.key(self.OWNER, cid), "t": title, "o": self.OWNER})

    def _hub(self, n=None):
        """이웃 `n` 개를 단 허브 하나."""
        self._concept("concept:hub", "허브")
        for i in range(self.N if n is None else n):
            self._concept(f"concept:n{i:02d}", f"title{i:02d}")
            self.store.run(
                "MATCH (a:Concept {concept_id:$a}), (b:Concept {concept_id:$b}) "
                "MERGE (a)-[:PREREQUISITE_OF]->(b)",
                {"a": pkns.key(self.OWNER, "concept:hub"),
                 "b": pkns.key(self.OWNER, f"concept:n{i:02d}")})

    def _links(self, offset=0):
        res = q.node_context(owner=self.OWNER, node="concept:hub", store=self.store,
                             links_offset=offset)
        self.assertTrue(res["executed"], res.get("note"))
        return res["links"]

    def test_every_neighbour_is_reachable_by_following_next_offset(self):
        # 이 파일의 요점. 창을 따라가면 이웃 **전부**가 나와야 한다.
        self._hub()
        seen, off, pages = [], 0, 0
        while True:
            links = self._links(off)
            seen += [i["id"] for i in links["items"]]
            pages += 1
            self.assertLess(pages, 5, "페이지가 끝나지 않는다")
            if not links["truncated"]:
                self.assertNotIn("next_offset", links)
                break
            off = links["next_offset"]
        self.assertEqual(len(seen), self.N)
        self.assertEqual(len(set(seen)), self.N, "창이 겹쳐 같은 줄을 두 번 냈다")
        self.assertEqual(links["total"], self.N)

    def test_the_first_window_says_there_is_more(self):
        self._hub()
        links = self._links()
        self.assertEqual(len(links["items"]), q._NODE_LINKS_CAP)
        self.assertEqual(links["offset"], 0)
        self.assertTrue(links["truncated"])
        self.assertEqual(links["next_offset"], q._NODE_LINKS_CAP)

    def test_a_list_inside_the_cap_offers_no_continuation(self):
        # 상한 안이면 `truncated` 는 거짓이고 이어 갈 자리도 없어야 한다 —
        # 있지도 않은 다음 페이지를 화면이 부르면 빈 목록을 "다 지웠다"로 읽는다.
        self._hub(n=3)
        links = self._links()
        self.assertEqual(len(links["items"]), 3)
        self.assertFalse(links["truncated"])
        self.assertNotIn("next_offset", links)

    def test_an_offset_past_the_end_is_empty_and_final(self):
        # 오프셋이 끝을 넘어가도 오류가 아니다. 빈 창이고 더 갈 곳이 없다.
        self._hub()
        links = self._links(offset=self.N + 10)
        self.assertEqual(links["items"], [])
        self.assertFalse(links["truncated"])
        self.assertEqual(links["total"], self.N, "총수는 창과 무관하게 이웃 전체다")

    def test_a_negative_offset_reads_from_the_start(self):
        self._hub()
        self.assertEqual(self._links(offset=-5)["offset"], 0)

    # ── 계약이 픽스처로 남기라고 이름 붙인 모양들 ──────────────────────────────
    # 근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §8]] IA-03 — 사이클과 두 부모는
    # "결과 신분 하나, 관계 둘 다 보존, 재귀 루프 없음"이다.

    def _edge(self, rel, a, b):
        self.store.run(
            f"MATCH (x:Concept {{concept_id:$a}}), (y:Concept {{concept_id:$b}}) "
            f"MERGE (x)-[:{rel}]->(y)",
            {"a": pkns.key(self.OWNER, a), "b": pkns.key(self.OWNER, b)})

    def test_a_cycle_answers_once_and_does_not_recurse(self):
        # A→B→A. 이웃만 보는 계약이라 순환이 응답을 늘리거나 멈추게 해서는 안 된다.
        for cid in ("concept:a", "concept:b"):
            self._concept(cid, cid.split(":")[1])
        self._edge("PREREQUISITE_OF", "concept:a", "concept:b")
        self._edge("PREREQUISITE_OF", "concept:b", "concept:a")
        got = q.node_context(owner=self.OWNER, node="concept:a", store=self.store)
        self.assertTrue(got["executed"])
        ids = [i["id"] for i in got["links"]["items"]]
        # 같은 이웃이 양방향으로 한 번씩. **한쪽으로 접지 않는다** — 나감과 들어옴은
        # 다른 사실이고, 접으면 화면이 반대 방향을 잃는다.
        self.assertEqual(sorted(ids), ["concept:b", "concept:b"])
        self.assertEqual(sorted(i["dir"] for i in got["links"]["items"]), ["in", "out"])

    def test_two_parents_both_survive_as_incoming(self):
        for cid in ("concept:p1", "concept:p2", "concept:child"):
            self._concept(cid, cid.split(":")[1])
        self._edge("PREREQUISITE_OF", "concept:p1", "concept:child")
        self._edge("PREREQUISITE_OF", "concept:p2", "concept:child")
        got = q.node_context(owner=self.OWNER, node="concept:child", store=self.store)
        items = got["links"]["items"]
        self.assertEqual(sorted(i["id"] for i in items),
                         ["concept:p1", "concept:p2"])
        self.assertTrue(all(i["dir"] == "in" for i in items), items)
        # 결과 신분은 하나다 — 부모가 둘이라고 자식이 두 줄이 되지 않는다.
        self.assertEqual(got["node"], "concept:child")

    def test_an_orphan_answers_executed_with_zero_rather_than_failing(self):
        # 연결 0 은 오류가 아니다. `executed:false` 로 내면 화면이 "못 읽었다"로 읽는다.
        self._concept("concept:lonely", "외톨")
        got = q.node_context(owner=self.OWNER, node="concept:lonely", store=self.store)
        self.assertTrue(got["executed"])
        self.assertEqual(got["links"]["total"], 0)
        self.assertEqual(got["links"]["items"], [])
        self.assertFalse(got["links"]["truncated"])
        # 그리고 고립돼도 목록에는 선다 — 연결은 목록의 조건이 아니다.
        listed = q.node_list(owner=self.OWNER, store=self.store, kind="concept")
        self.assertIn("concept:lonely", [i["id"] for i in listed["items"]])


if __name__ == "__main__":
    unittest.main()
