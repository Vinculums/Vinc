# -*- coding: utf-8 -*-
"""`vinc_path` 의 "없는 노드" 판정은 **PK 조회**여야 한다.

이 결함은 이 파일 안에서 두 번째다. `neighbors()` 가 같은 판정을 `near_nodes`
(제목·props 유사도 검색)로 하다가 실재하는 노드를 `unknown node` 로 답했고, 그때
"유사도는 존재의 증거가 아니다 · 존재는 PK 로 묻는다"로 고쳤다. `shortest_path()`
는 그 수정을 안 받았고, 라이브에서 이웃 28개를 가진 `concept:hybrid-search` 를
"unknown node" 라고 답했다(2026-09-08).

증상이 조용한 이유: 슬러그가 제 제목 안에 글자 그대로 안 나오는 노드에만 나므로,
제목이 서술형인 노드는 우연히 통과한다. 그래서 여기서 **유사도가 0인 id** 로 못 박는다.

근거: [[record:path-called-an-existing-concept-unknown-2026-09-08]]
"""
from __future__ import annotations

import unittest

from api import query as q


class _Store:
    """PK 조회에는 답하고 유사도 검색에는 아무것도 안 주는 스토어. 실제 결함의 모양이다."""

    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.similarity_calls = 0

    def run(self, cypher: str, params: dict | None = None):
        p = params or {}
        # `near_nodes` 는 제목·props 를 훑는다 — 여기서는 늘 빈손이다.
        if "UNION ALL" in cypher or "CONTAINS" in cypher.upper():
            self.similarity_calls += 1
            return []
        # PK 조회: 파라미터에 실린 id 가 있으면 그 행을 준다.
        for v in p.values():
            if isinstance(v, str):
                for e in self.existing:
                    if v.endswith(e):
                        return [{"id": e, "title": e, "kind": e.split(":")[0]}]
        return []


class PathExistence(unittest.TestCase):
    def test_an_existing_concept_is_not_called_unknown(self):
        store = _Store({"concept:hybrid-search", "concept:single-writer"})
        r = q.shortest_path("concept:hybrid-search", "concept:single-writer",
                            owner="user:x", store=store)
        self.assertNotIn("unknown node", (r.get("error") or ""),
                         "유사도가 0이라고 존재를 부정했다")

    def test_a_genuinely_absent_id_is_still_reported(self):
        store = _Store({"concept:hybrid-search"})
        r = q.shortest_path("concept:hybrid-search", "concept:not-here",
                            owner="user:x", store=store)
        self.assertIn("unknown node", (r.get("error") or ""))
        self.assertIn("concept:not-here", (r.get("error") or ""))


if __name__ == "__main__":
    unittest.main()
