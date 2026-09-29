# -*- coding: utf-8 -*-
"""구조 진단의 스페이스 경계 — 같은 슬러그가 두 스페이스에 살아도 남의 것은 안 섞인다.

엔진 algo 확장 시절에는 투영이 소유자를 몰랐다(`project_graph` 는 전체 표만 됐다). 그래서
결과에서 내 논리 id 집합과 교차해 스코프를 걸었고, **같은 슬러그를 가진 남의 노드가 문자열이
같다는 이유로 통과했다.** 새 스페이스는 전부 같은 기본 역할 넷으로 시작하므로 드문 일이
아니다(2026-09-10 실측: `concept:agent-steward` 가 크기 1 컴포넌트로 네 번). 그때는 어느
쪽이 내 것인지 알 길이 없어 고르지 않고 갈라서 말했다.
근거: [[record:one-logical-id-in-two-spaces-reads-as-two-orphans-2026-09-10]]

2026-09-28 부터 계산은 `api/graph_algo.py` 이고 투영이 **소유자로 거른다**(양 끝이 모두
그 소유자인 엣지만). 이제는 어느 쪽이 내 것인지 안다: 내 스페이스의 그 슬러그만 들어온다.
이 파일은 그 불변식을 고정한다 — 남의 노드는 고아도, 커뮤니티 구성원도 되지 않고, 스페이스를
건너는 엣지는 투영에 없다.
"""
from __future__ import annotations

import re
import unittest

from api import query as q

MINE, THEIRS = "user:mine", "user:theirs"

_NODE = re.compile(r"MATCH \((\w):(\w+)\)")
_EDGE = re.compile(r"MATCH \(x:(\w+)\)-\[:(\w+)\]->\(y:(\w+)\)")


class _SpacesStore:
    """두 스페이스를 가진 대역. 진단이 내는 조회(투영의 노드·엣지, 소유 개념, 도메인)만
    흉내 낸다. 소유자 필터를 **걸었을 때만** 거른다 — 투영이 필터를 빼먹으면 남의 것이
    그대로 섞여 들어와 아래 테스트가 잡는다."""

    def __init__(self, nodes, edges):
        self.nodes = nodes      # {(owner, id): label}
        self.edges = edges      # [(owner_a, a, rel, owner_b, b)]

    def run(self, query, params=None):
        owner = (params or {}).get("o") or (params or {}).get("owner")
        m = _EDGE.search(query)
        if m:
            la, rel, lb = m.groups()
            return [{"a": a, "b": b} for oa, a, r, ob, b in self.edges
                    if r == rel and self.nodes.get((oa, a)) == la
                    and self.nodes.get((ob, b)) == lb
                    and (owner is None or (oa == owner and ob == owner))]
        m = _NODE.search(query)
        if m:
            label = m.group(2)
            rows = [{"id": i, "domain": ""} for (o, i), lbl in self.nodes.items()
                    if lbl == label and (owner is None or o == owner)]
            return rows
        return []

    def close(self):
        pass


def _store(mine_nodes, theirs_nodes, edges=()):
    nodes = {(MINE, i): "Concept" for i in mine_nodes}
    nodes.update({(THEIRS, i): "Concept" for i in theirs_nodes})
    return _SpacesStore(nodes, [(oa, a, "RELATED_TO", ob, b) for oa, a, ob, b in edges])


class SharedSlug(unittest.TestCase):
    def test_a_slug_in_two_spaces_is_my_orphan_once(self):
        """🔴 이 파일의 요지. 예전에는 네 번 나오거나(교차 전) 아예 빠졌다(교차 후,
        모호함으로). 이제 내 스페이스의 그 노드 하나만 보고, 그것이 정말 고아면 고아다."""
        st = _store({"concept:agent-steward"}, {"concept:agent-steward"})
        res = q.component_diagnosis(owner=MINE, store=st)
        self.assertEqual(res["orphans"], ["concept:agent-steward"])
        self.assertEqual(res["orphan_count"], 1)
        self.assertEqual(res["ambiguous"], [])

    def test_another_space_only_slug_never_enters(self):
        st = _store({"concept:mine"}, {"concept:theirs"})
        res = q.component_diagnosis(owner=MINE, store=st)
        self.assertEqual(res["orphans"], ["concept:mine"])

    def test_an_edge_across_spaces_is_not_projected(self):
        """양 끝이 모두 내 것인 엣지만 투영한다. 한쪽이 남의 것이면 그 엣지로 내 노드가
        고아를 면하면 안 된다."""
        st = _store({"concept:a"}, {"concept:b"}, [(MINE, "concept:a", THEIRS, "concept:b")])
        res = q.component_diagnosis(owner=MINE, store=st)
        self.assertEqual(res["orphans"], ["concept:a"])

    def test_the_same_edge_in_both_spaces_counts_once(self):
        st = _store({"concept:a", "concept:b"}, {"concept:a", "concept:b"},
                    [(MINE, "concept:a", MINE, "concept:b"),
                     (THEIRS, "concept:a", THEIRS, "concept:b")])
        res = q.component_diagnosis(owner=MINE, store=st)
        self.assertEqual(res["component_count"], 1)
        self.assertEqual(res["components"][0]["size"], 2)
        self.assertEqual(res["orphans"], [])

    def test_a_clean_graph_carries_the_fields_anyway(self):
        """부르는 쪽이 키 유무로 분기하지 않게 한다 — 없으면 있는 것과 다른 코드가 된다."""
        st = _store({"concept:a", "concept:b"}, set(), [(MINE, "concept:a", MINE, "concept:b")])
        res = q.component_diagnosis(owner=MINE, store=st)
        self.assertEqual(res["ambiguous"], [])
        self.assertEqual(res["ambiguous_count"], 0)
        self.assertNotIn("ambiguous_note", res)


class Communities(unittest.TestCase):
    """같은 투영을 쓰는 나머지 둘. 컴포넌트만 맞으면 두 진단이 계속 틀린다."""

    def test_a_shared_slug_does_not_inflate_a_community(self):
        st = _store({"concept:x", "concept:a", "concept:b"}, {"concept:x", "concept:y"},
                    [(MINE, "concept:a", MINE, "concept:b"),
                     (MINE, "concept:b", MINE, "concept:x"),
                     (THEIRS, "concept:x", THEIRS, "concept:y")])
        res = q.communities(owner=MINE, store=st)
        self.assertEqual(res["community_count"], 1)
        self.assertEqual(res["communities"][0]["members"],
                         ["concept:a", "concept:b", "concept:x"])
        self.assertEqual(res["ambiguous"], [])

    def test_context_answers_for_a_shared_slug_from_my_space(self):
        """예전에는 모호해서 답하지 않았다(남의 커뮤니티를 집을 수 있었다). 이제 내
        스페이스의 커뮤니티로 답한다 — 남의 구성원(`concept:y`)은 섞이지 않는다."""
        st = _store({"concept:x", "concept:a"}, {"concept:x", "concept:y"},
                    [(MINE, "concept:x", MINE, "concept:a"),
                     (THEIRS, "concept:x", THEIRS, "concept:y")])
        res = q.community_context(node="concept:x", owner=MINE, store=st)
        self.assertTrue(res["executed"], res)
        self.assertEqual(res["size"], 2)
        ids = {m["id"] for m in res["members"]} | {r["id"] for r in res["representatives"]}
        self.assertNotIn("concept:y", ids)

    def test_context_says_so_for_an_isolated_seed(self):
        st = _store({"concept:alone"}, set())
        res = q.community_context(node="concept:alone", owner=MINE, store=st)
        self.assertFalse(res["executed"])
        self.assertIn("isolated", res["note"])


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
