# -*- coding: utf-8 -*-
"""구조 진단의 스페이스 경계 — 같은 슬러그가 두 스페이스에 살면 고아가 아니다.

`component_diagnosis` 는 owner 스코프를 **논리 id 문자열 집합과의 교차**로 건다.
알고리즘 투영은 전체를 훑고(`project_graph` 는 owner 를 모른다), 그 결과에서 내 id
집합에 든 것만 남기는 방식이다. 한 소유자 안에서 id 는 기본키라 유일하니 그 교차는
안전해 보이는데, **같은 슬러그를 가진 남의 노드가 문자열이 같다는 이유로 통과한다.**

새 스페이스는 전부 같은 기본 역할 넷으로 시작하므로 이것은 드문 일이 아니다.
2026-09-10 실측: `concept:agent-steward` 가 크기 1 컴포넌트로 **네 번** 나왔고 그 수가
소유자 수와 맞았다. 2026-09-06 에 "진단이 같은 노드를 두 번 센다"로 기록된 유령이
버그가 아니라 그것이었다 — 스페이스가 늘어난 만큼 늘어난 것이다.

어느 쪽이 내 것인지는 **알 방법이 없다**(알고리즘 행에 소유자가 없다). 그래서 고르지
않고 갈라서 말한다. 고아로 세면 남의 그래프를 내 정리 대상으로 내밀게 된다.
근거: [[record:one-logical-id-in-two-spaces-reads-as-two-orphans-2026-09-10]]
"""
from __future__ import annotations

import unittest

from api import query as q


class _AlgoStore:
    """`graph_algo` 와 owner 조회만 하는 대역. 드라이버를 안 쓴다."""

    def __init__(self, rows, mine):
        self._rows = rows          # [{id, value}] — value 가 컴포넌트 번호
        self._mine = mine          # 내 소유자의 논리 id 집합

    def graph_algo(self, algo, **kw):
        return list(self._rows)

    def run(self, query, params=None):
        # `_owner_concept_ids` 가 Concept 과 Decision 을 따로 묻는다.
        if "(c:Concept)" in query:
            return [{"id": i} for i in self._mine if i.startswith("concept:")]
        if "(d:Decision)" in query:
            return [{"id": i} for i in self._mine if i.startswith("decision:")]
        return []

    def close(self):
        pass


def _diag(rows, mine, **kw):
    return q.component_diagnosis(owner="user:mine", store=_AlgoStore(rows, mine), **kw)


class SharedSlug(unittest.TestCase):
    def test_one_id_in_two_components_is_not_two_orphans(self):
        """🔴 이 파일의 요지. 같은 id 가 두 컴포넌트에 있으면 두 스페이스의 노드다 —
        한 소유자 안에서는 기본키라 불가능하다."""
        res = _diag([{"id": "concept:agent-steward", "value": 1},
                     {"id": "concept:agent-steward", "value": 2}],
                    {"concept:agent-steward"})
        self.assertEqual(res["orphans"], [])
        self.assertEqual(res["orphan_count"], 0)
        self.assertEqual(res["ambiguous"], ["concept:agent-steward"])
        self.assertEqual(res["ambiguous_count"], 1)

    def test_it_says_why_rather_than_dropping_silently(self):
        """조용히 빼면 "고아가 줄었다" 로 읽힌다. 이 리포가 반복해 물린 무음 절단이다."""
        res = _diag([{"id": "concept:x", "value": 1}, {"id": "concept:x", "value": 2}],
                    {"concept:x"})
        self.assertIn("more than one space", res["ambiguous_note"])

    def test_a_component_left_empty_by_the_split_disappears(self):
        """구성원이 모호한 것뿐이던 컴포넌트는 남기지 않는다 — 크기 0 은 컴포넌트가
        아니고, 그것을 실으면 `component_count` 가 거짓이 된다."""
        res = _diag([{"id": "concept:x", "value": 1}, {"id": "concept:x", "value": 2},
                     {"id": "concept:a", "value": 3}, {"id": "concept:b", "value": 3}],
                    {"concept:x", "concept:a", "concept:b"})
        self.assertEqual(res["component_count"], 1)
        self.assertEqual(res["components"][0]["size"], 2)

    def test_a_real_orphan_still_reads_as_one(self):
        """모호한 것을 가르느라 진짜 고아를 잃으면 진단이 쓸모를 잃는다."""
        res = _diag([{"id": "concept:lonely", "value": 7}], {"concept:lonely"})
        self.assertEqual(res["orphans"], ["concept:lonely"])
        self.assertEqual(res["ambiguous"], [])

    def test_another_space_only_slug_never_enters(self):
        """내 id 집합에 없는 슬러그는 애초에 교차에서 떨어진다 — 그 경로는 이미 옳다."""
        res = _diag([{"id": "concept:theirs", "value": 1},
                     {"id": "concept:mine", "value": 2}],
                    {"concept:mine"})
        self.assertEqual(res["orphans"], ["concept:mine"])
        self.assertEqual(res["ambiguous"], [])

    def test_a_clean_graph_carries_the_fields_anyway(self):
        """부르는 쪽이 키 유무로 분기하지 않게 한다 — 없으면 있는 것과 다른 코드가 된다."""
        res = _diag([{"id": "concept:a", "value": 1}, {"id": "concept:b", "value": 1}],
                    {"concept:a", "concept:b"})
        self.assertEqual(res["ambiguous"], [])
        self.assertEqual(res["ambiguous_count"], 0)
        self.assertNotIn("ambiguous_note", res)


class Communities(unittest.TestCase):
    """같은 교차를 쓰는 나머지 둘. 컴포넌트만 고치면 두 진단이 계속 틀린다."""

    def _com(self, rows, mine, **kw):
        return q.communities(owner="user:mine",
                             store=_AlgoStore(rows, mine), **kw)

    def test_a_shared_slug_does_not_inflate_two_communities(self):
        res = self._com([{"id": "concept:x", "value": 1}, {"id": "concept:x", "value": 2},
                         {"id": "concept:a", "value": 1}, {"id": "concept:b", "value": 1}],
                        {"concept:x", "concept:a", "concept:b"})
        self.assertEqual(res["community_count"], 1)
        self.assertEqual(res["communities"][0]["size"], 2)
        self.assertEqual(res["ambiguous"], ["concept:x"])

    def test_context_refuses_an_ambiguous_seed_instead_of_guessing(self):
        """🔴 셋 중 가장 조용했던 자리. `assign` 이 논리 id 로 키를 잡는 dict 라 같은
        슬러그가 둘이면 **마지막 행이 이긴다** — 남의 스페이스 커뮤니티를 씨앗의
        커뮤니티로 집고 그 위에서 대표와 발췌를 만들며 아무 표시도 없다."""
        res = q.community_context(
            node="concept:x", owner="user:mine",
            store=_AlgoStore([{"id": "concept:x", "value": 1},
                              {"id": "concept:x", "value": 2},
                              {"id": "concept:a", "value": 2}],
                             {"concept:x", "concept:a"}))
        self.assertFalse(res["executed"])
        self.assertTrue(res.get("ambiguous"))
        self.assertIn("more than one space", res["note"])

    def test_context_still_answers_for_a_clean_seed(self):
        res = q.community_context(
            node="concept:a", owner="user:mine",
            store=_AlgoStore([{"id": "concept:a", "value": 5},
                              {"id": "concept:b", "value": 5}],
                             {"concept:a", "concept:b"}))
        self.assertTrue(res["executed"])
        self.assertEqual(res["size"], 2)


class VersionsMoveTogether(unittest.TestCase):
    def test_the_backfill_version_reaches_the_target(self):
        """🔴 도메인 계층 백필은 스토어가 **뒤처져 있을 때만** 돈다
        (`cur < DOMAIN_HIERARCHY_VERSION`). 라이브가 이미 목표 버전을 기록하고 있으면
        이 상수만 올려서는 아무 일도 안 일어난다 — 목표가 함께 움직여야 한다.
        2026-09-10 에 그 자리에서 걸렸다: 열은 있고 IN_DOMAIN 이 없는 노드가 남아
        있는데 백필이 도는 조건이 아니었다."""
        from store import embedded_schema as es
        self.assertGreaterEqual(es.DOMAIN_HIERARCHY_VERSION, es.SCHEMA_VERSION,
                                "백필이 도는 조건이 아니면 이 상수를 올린 의미가 없다")


if __name__ == "__main__":
    unittest.main()
