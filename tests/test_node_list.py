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


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class NodeListAgentsScope(unittest.TestCase):
    """Agents 컬렉션의 판별식 — 주제 **와** 신분 둘이다.

    화면의 브레드크럼은 `vinc / agent`(뿌리)다. 그 주제만으로 거르면 역할이 아닌
    것이 섞이고(실측 2026-09-20: 라이브에서 Concept 29 중 7이 QA·OpenClaw·Steward
    산출물), `vinc/agent/roles` 로 좁히면 오늘은 맞지만 **역할이 자식 주제로 옮겨지는
    순간 빈다** — 2026-09-04 에 실제로 일어나 `prompts/list` 가 0건을 냈다.
    근거: [[decision:roster-readers-descend-one-child-domain]].
    """

    # setUp·헬퍼는 위 클래스와 같은 것을 **복사한다**(이 리포의 다른 노드 테스트
    # 파일들도 같은 모양이다). 상속하면 부모의 개수 단언이 이 시드 위에서 다시 돌아
    # 깨지고, 메서드 별칭으로 빌려오면 정적 분석이 그것을 못 따라간다.
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

    def _dom_node(self, lbl, pk, nid, title, dom):
        self.store.run(
            f"MERGE (n:{lbl} {{{pk}:$i}}) SET n.title=$t, n.domain=$d, n.owner_id=$o",
            {"i": pkns.key(self.OWNER, nid), "t": title, "d": dom, "o": self.OWNER})

    def _list(self, **kw):
        return q.node_list(owner=kw.pop("owner", self.OWNER), store=self.store, **kw)

    def _seed(self):
        # 역할 둘 — 하나는 뿌리에, 하나는 자식 주제에. 주제 이동에 안 깨지는지 본다.
        self._dom_node("Concept", "concept_id", "concept:agent-designer",
                       "제품 디자이너", "vinc/agent")
        self._dom_node("Concept", "concept_id", "concept:agent-reviewer",
                       "코드 리뷰어", "vinc/agent/roles")
        # 접두를 갖지만 역할이 아닌 것(라이브의 concept:agent-orchestration 자리).
        self._dom_node("Concept", "concept_id", "concept:agent-orchestration",
                       "에이전트 오케스트레이션", "vinc/agent/orchestration")
        # 예약 주제 아래지만 접두가 아닌 산출물(라이브의 QA·Steward 자리).
        self._dom_node("Concept", "concept_id", "concept:qa-test-case-chunk",
                       "QA 테스트 케이스", "vinc/agent/qa")
        # 예약 주제 **밖**. 슬래시 경계가 요점이다.
        self._dom_node("Concept", "concept_id", "concept:agentic-thing",
                       "에이전틱한 것", "vinc/agentic")

    def test_the_root_topic_alone_lets_non_roles_in(self):
        # 왜 판별식이 둘이어야 하는지 — 주제만으로는 산출물이 섞인다.
        got = self._list(domain="vinc/agent", kind="concept")
        self.assertEqual(len(got["items"]), 4)
        self.assertIn("concept:qa-test-case-chunk", [i["id"] for i in got["items"]])
        # 예약 주제 밖은 안 든다(슬래시 경계).
        self.assertNotIn("concept:agentic-thing", [i["id"] for i in got["items"]])

    def test_topic_plus_identity_finds_roles_wherever_the_topic_moved_them(self):
        got = self._list(domain="vinc/agent", kind="concept",
                         id_prefix="concept:agent-")
        ids = [i["id"] for i in got["items"]]
        # 뿌리에 있는 것과 자식 주제로 옮겨진 것이 **둘 다** 나온다.
        self.assertIn("concept:agent-designer", ids)
        self.assertIn("concept:agent-reviewer", ids)
        self.assertNotIn("concept:qa-test-case-chunk", ids)
        self.assertEqual(got["id_prefix"], "concept:agent-")

    def test_narrowing_to_the_roles_child_loses_a_role_that_moved(self):
        # 이 테스트는 **기각된 대안을 고정한다**. `vinc/agent/roles` 로 좁히면
        # 뿌리에 있는 역할이 사라진다 — 그래서 뿌리 + 판별식을 택했다.
        got = self._list(domain="vinc/agent/roles", kind="concept",
                         id_prefix="concept:agent-")
        self.assertEqual([i["id"] for i in got["items"]], ["concept:agent-reviewer"])

    def test_the_prefix_alone_still_lets_the_known_non_role_through(self):
        # 접두만으로는 모자란다. `concept:agent-orchestration` 은 접두를 갖지만 역할이
        # 아니고, 2026-09-12 에 라이브 prompts/list 가 그것을 **열아홉 번째 역할로**
        # 내보냈다([[record:the-roster-concept-leaks-into-prompts-list-2026-09-12]]).
        # 이 테스트는 그 틈이 아직 열려 있음을 고정한다 — 다음 테스트가 닫는 법을 고정한다.
        ids = [i["id"] for i in self._list(domain="vinc/agent", kind="concept",
                                           id_prefix="concept:agent-")["items"]]
        self.assertIn("concept:agent-orchestration", ids)

    def test_the_caller_can_exclude_the_known_non_role(self):
        got = self._list(domain="vinc/agent", kind="concept",
                         id_prefix="concept:agent-",
                         id_exclude=["concept:agent-orchestration"])
        ids = [i["id"] for i in got["items"]]
        self.assertNotIn("concept:agent-orchestration", ids)
        # 역할은 그대로 남는다 — 제외가 목록을 통째로 지우지 않는다.
        self.assertIn("concept:agent-designer", ids)
        self.assertIn("concept:agent-reviewer", ids)
        # 무엇을 뺐는지 응답이 말한다. 조용히 사라지면 "없다"와 구별이 안 된다.
        self.assertEqual(got["id_excluded"], ["concept:agent-orchestration"])

    def test_an_exclusion_that_matches_nothing_changes_nothing(self):
        base = self._list(domain="vinc/agent", kind="concept",
                          id_prefix="concept:agent-")
        same = self._list(domain="vinc/agent", kind="concept",
                          id_prefix="concept:agent-", id_exclude=["concept:nope"])
        self.assertEqual([i["id"] for i in same["items"]],
                         [i["id"] for i in base["items"]])


if __name__ == "__main__":
    unittest.main()
