# -*- coding: utf-8 -*-
"""VIN-89 — `node_list`: 리더 왼쪽이 쓰는 **평평한** 노드 목록.

`nav_tree` 로는 이 모양을 만들 수 없다. 저쪽은 주제 → 그 주제의 노드라는 트리이고,
화면의 왼쪽 목록에는 주제 머리가 없으며 종이 섞여 나오고 바닥에 "다음 항목 불러오기"가
있다 — 그룹 경계를 넘는 창이 트리 응답에는 없다.

근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §3]] — 모든 종이 닿아야 하고, 큰
결과에는 페이지 크기와 연속과 정직한 부분 표시가 있어야 한다.
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
class NodeListE2E(unittest.TestCase):
    OWNER = "user:a"

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
        self._seed()

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _k(self, i):
        return pkns.key(self.OWNER, i)

    def _dom_node(self, lbl, pk, nid, title, dom, owner=None):
        own = owner or self.OWNER
        self.store.run(
            f"MERGE (n:{lbl} {{{pk}:$i}}) SET n.title=$t, n.domain=$d, n.owner_id=$o",
            {"i": pkns.key(own, nid), "t": title, "d": dom, "o": own})

    def _seed(self):
        # 주제를 갖는 넷. 베타는 **하위 주제**라 스코프의 하위 트리 판정을 건드린다.
        self._dom_node("Concept", "concept_id", "concept:alpha", "알파", "제품")
        self._dom_node("Concept", "concept_id", "concept:beta", "베타", "제품/하위")
        self._dom_node("Concept", "concept_id", "concept:gamma", "감마", "딴주제")
        self._dom_node("Decision", "decision_id", "decision:d1", "결정 하나", "제품")
        self._dom_node("Record", "record_id", "record:r1", "기록 하나", "제품")
        self._dom_node("Episode", "episode_id", "episode:e1", "사건 하나", "제품")
        # 주제를 **갖지 않는** 둘. 스키마가 IN_DOMAIN 을 안 준다.
        self.store.run(
            "MERGE (n:Bridge {bridge_id:$i}) SET n.title='다리', n.owner_id=$o",
            {"i": self._k("bridge:b1"), "o": self.OWNER})
        self.store.run(
            "MERGE (n:Entity {entity_id:$i}) SET n.type='theory', n.title='쌍대성', "
            "n.owner_id=$o", {"i": self._k("theory:dual"), "o": self.OWNER})

    def _list(self, **kw):
        res = q.node_list(owner=kw.pop("owner", self.OWNER), store=self.store, **kw)
        return res

    def _titles(self, res):
        return [i["title"] for i in res["items"]]

    def test_every_kind_is_reachable_without_a_scope(self):
        # IA §3: 여섯 캐리어가 전부 닿아야 한다. Bridge 와 개방 타입이 주제 트리에
        # 못 선다는 이유로 목록에서까지 사라지면 안 된다.
        res = self._list()
        self.assertEqual(res["total"], 8)
        self.assertEqual({i["kind"] for i in res["items"]},
                         {"concept", "decision", "record", "episode", "bridge",
                          "theory"})
        # 개방 타입의 종은 캐리어 이름이 아니라 **저작된 타입**이다.
        self.assertIn("theory", {i["kind"] for i in res["items"]})

    def test_a_scope_takes_the_subtree_and_drops_the_topicless_kinds(self):
        res = self._list(domain="제품")
        self.assertEqual(sorted(self._titles(res)),
                         sorted(["알파", "베타", "결정 하나", "기록 하나", "사건 하나"]))
        # 주제가 없는 종은 주제 스코프에 **구조적으로** 들 수 없다 — 없는 열로는
        # 거를 수 없기 때문이고, 조용히 빠지는 것이 아니라 스키마가 그렇다.
        self.assertNotIn("다리", self._titles(res))
        self.assertEqual(res["scope"], ["제품"])

    def test_the_window_neither_repeats_nor_loses_a_row(self):
        seen, off, pages = [], 0, 0
        while True:
            res = self._list(limit=3, offset=off)
            seen += [i["id"] for i in res["items"]]
            pages += 1
            self.assertLess(pages, 6, "창이 끝나지 않는다")
            if not res["truncated"]:
                self.assertNotIn("next_offset", res)
                break
            off = res["next_offset"]
        self.assertEqual(len(seen), 8)
        self.assertEqual(len(set(seen)), 8, "창이 겹쳐 같은 줄을 두 번 냈다")

    def test_the_query_filters_before_the_window_is_cut(self):
        # 서버가 거른 뒤에 잘라야 한다. 화면이 한 창을 받아 거르면 "다음"이 무엇을
        # 뜻하는지 알 수 없다.
        res = self._list(query="하나")
        self.assertEqual(sorted(self._titles(res)),
                         sorted(["결정 하나", "기록 하나", "사건 하나"]))
        self.assertEqual(res["total"], 3)

    def test_kind_counts_answer_the_scope_not_the_filters(self):
        # 종 손잡이가 묻는 것은 "이 스코프에 무엇이 몇 개 있나"이지 "지금 걸러 낸 뒤
        # 몇 개"가 아니다. 그래서 질의·종필터가 걸려도 kinds 는 안 변한다.
        plain = self._list()["kinds"]
        self.assertEqual(self._list(query="하나")["kinds"], plain)
        self.assertEqual(self._list(kind="concept")["kinds"], plain)
        self.assertEqual(plain["concept"], 3)
        self.assertEqual(plain["bridge"], 1)
        # 스코프가 걸리면 그때는 바뀐다 — 같은 술어를 지나기 때문이다.
        self.assertEqual(self._list(domain="제품")["kinds"]["concept"], 2)

    def test_order_is_title_then_id_so_the_window_is_stable(self):
        res = self._list()
        keys = [(i["title"].casefold(), i["id"]) for i in res["items"]]
        self.assertEqual(keys, sorted(keys))

    def test_another_owner_sees_nothing_of_this_one(self):
        res = self._list(owner="user:b")
        self.assertTrue(res["executed"])
        self.assertEqual(res["total"], 0)
        self.assertEqual(res["items"], [])

    def test_an_unknown_kind_is_refused_rather_than_answered_empty(self):
        # 빈 목록으로 답하면 화면이 "그 종이 없다"로 읽는다.
        res = self._list(kind="없는종")
        self.assertFalse(res["executed"])
        self.assertIn("unknown kind", res.get("note", ""))


if __name__ == "__main__":
    unittest.main()
