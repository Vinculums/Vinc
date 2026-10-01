# -*- coding: utf-8 -*-
"""근거 백필 생산자 — 문장 판정 2단의 계약.

첫 owner 전체 실행이 scanned 202 · filled 0 이었다: ①(verbatim 공기)뿐이었고 이
코퍼스의 주장형 긴 제목은 그 조건을 물리적으로 못 만족한다. 완화(②, mentions_link
토큰 규칙 재사용)는 [[decision:relax-the-evidence-producer-rule]] 로 비준됐고,
비준 조건이 "backfill 산은 저작분과 구별 가능"이라 **confidence 0.4 = 완화 표식**
이 계약의 일부다. 여기서 그 계약을 고정한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore  # noqa: E402

OWNER = "user:alice"


class _Store:
    """backfill_evidence 가 두드리는 세 쿼리만 흉내 내는 페이크."""

    def __init__(self, edge_rows, chunk_text):
        self.edge_rows = edge_rows
        self.chunk_text = chunk_text
        self.candidate_cypher = ""
        self.sets: list[dict] = []

    def run(self, cypher, params=None):
        if "r.evidence IS NULL" in cypher:
            self.candidate_cypher = cypher
            return self.edge_rows
        if "MATCH (ch:Chunk)-[:MENTIONS]" in cypher:
            return [{"text": self.chunk_text}]
        if "SET r.evidence" in cypher:
            self.sets.append(dict(params or {}))
            return []
        return []


def _edge(at: str, bt: str) -> list[dict]:
    return [{"aid": "concept:a", "at": at, "bid": "concept:b", "bt": bt,
             "rel": "RELATED_TO"}]


def test_tier1_verbatim_wins_with_proximity_confidence():
    s = _Store(_edge("optimistic locking", "write skew"),
               "Optimistic locking prevents write skew in most stores.")
    out = docstore.backfill_evidence(s, owner=OWNER)
    assert out["filled"] == 1
    assert s.sets[0]["cf"] >= 0.5                     # ①의 대역은 0.5~0.9
    # 이미 근거가 있는 엣지는 후보 쿼리 자체가 거른다(저작 보호).
    assert "r.evidence IS NULL OR r.evidence = ''" in s.candidate_cypher


def test_tier2_token_match_fires_on_claim_shaped_titles_at_0_4():
    # 두 제목 모두 verbatim 으로는 문장에 없다 — 토큰(각 첫 6개)은 전부 있다.
    s = _Store(_edge("single writer holds the graph database",
                     "azure files rejects server side copies"),
               "The graph database holds a single writer, so azure files "
               "rejects any server side copies.")
    out = docstore.backfill_evidence(s, owner=OWNER)
    assert out["filled"] == 1
    assert s.sets[0]["cf"] == 0.4                     # 값이자 표식 — 0.5 미만 = 완화
    assert "graph database" in s.sets[0]["ev"].lower()


def test_tokens_split_across_sentences_do_not_count():
    s = _Store(_edge("single writer holds the graph database",
                     "azure files rejects server side copies"),
               "The graph database holds a single writer. "
               "Azure files rejects any server side copies.")
    out = docstore.backfill_evidence(s, owner=OWNER)
    assert out["filled"] == 0                          # 문장 경계는 여전히 경계다


def test_identical_token_sets_skip_the_relaxation():
    # 토큰셋이 같으면 아무 문장이나 잡힌다 — 완화를 건너뛰어야 한다.
    s = _Store(_edge("graph database", "database graph"),
               "Notes on graph and database tooling.")
    out = docstore.backfill_evidence(s, owner=OWNER)
    assert out["filled"] == 0
