"""`vinc_path` 가 개념 층만 걷는다는 사실을 **답으로 말하는가**.

왜 생겼나: `shortest_path_plan` 의 패턴은 `(a:Concept)-[...]-(b:Concept)` 다. 그래서
record·decision·episode id 를 주면 노드가 멀쩡히 있고 이웃도 많아도 매치가 0이고,
답은 `found:false` 였다. 그 값은 "안 이어져 있다"와 글자 그대로 같아서 구분이
불가능했다.

2026-09-07 에 실제로 물렸다. 새로 쓴 record 와 decision 사이에 `vinc_path` 를 두 번
불러 둘 다 `found:false` 를 받고 "이 노드들은 그래프의 섬이다"라고 보고했다.
`vinc_neighbors` 로 다시 재니 그 record 는 이웃 13개짜리 개념에 이미 붙어 있었다.
빈 답을 발견으로 읽은 것이다.

여기서 무는 것: 비-개념 끝점은 **사유를 말한다**(연결 부재와 다른 문장), 정상
개념의 경로 부재는 **여전히 depth 를 말한다**, 그리고 없는 id 는 여전히 unknown 이다.
근거: [[vinc.plan.retrieval-research-2026-09-08 §10]]
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from api import query as q  # noqa: E402


class _Store:
    """경로는 못 찾고(=행 0) 노드 존재 여부만 답하는 스토어."""

    def __init__(self, known: set[str]):
        self.known = known

    def run(self, cypher: str, params: dict | None = None) -> list[dict]:
        # 최단 경로 쿼리 → 언제나 빈 결과(이 테스트의 관심은 그 다음 분기다)
        if "ALL SHORTEST" in cypher:
            return []
        # 폴백 검색(near_nodes) → 아는 id 만 돌려준다
        rows = [{"id": i, "title": i, "domain": ""} for i in sorted(self.known)]
        needle = ""
        for v in (params or {}).values():
            if isinstance(v, str):
                needle = v
                break
        hit = [r for r in rows if needle and needle.split(":", 1)[-1] in r["id"]]
        return hit or []

    def close(self):
        pass


def _path(a: str, b: str, known: set[str]) -> dict:
    return q.shortest_path(a, b, cfg=None, store=_Store(known))


class PathWalksConceptsOnlyTests(unittest.TestCase):
    def test_a_record_endpoint_says_it_is_not_a_concept(self):
        """존재하는 record 를 끝점으로 주면 사유가 나온다 — 빈 found 가 아니라."""
        r = _path("record:some-measurement-2026-09-07", "concept:hub",
                  {"record:some-measurement-2026-09-07", "concept:hub"})
        self.assertFalse(r["found"])
        self.assertIn("concept layer only", r.get("error", ""))
        self.assertIn("record:some-measurement-2026-09-07", r.get("error", ""))
        self.assertIn("vinc_neighbors", r.get("hint", ""))

    def test_a_decision_endpoint_is_named_too(self):
        r = _path("decision:pick-a", "decision:pick-b",
                  {"decision:pick-a", "decision:pick-b"})
        self.assertIn("concept layer only", r.get("error", ""))
        self.assertIn("decision:pick-a", r["error"])
        self.assertIn("decision:pick-b", r["error"])

    def test_two_concepts_with_no_path_still_talk_about_depth(self):
        """정상 개념 둘의 경로 부재는 **다른 처방**이다 — 그 문장이 안 바뀌어야 한다."""
        r = _path("concept:a", "concept:b", {"concept:a", "concept:b"})
        self.assertFalse(r["found"])
        self.assertIn("no path within max_depth", r.get("error", ""))
        self.assertIn("max_depth", r.get("hint", ""))
        self.assertNotIn("concept layer only", r.get("error", ""))

    def test_an_unknown_id_is_still_unknown(self):
        """F7 의 기존 판정이 살아 있어야 한다 — 없는 id 는 층 문제가 아니다."""
        r = _path("concept:ghost", "concept:a", {"concept:a"})
        self.assertIn("unknown node", r.get("error", ""))
        self.assertNotIn("concept layer only", r.get("error", ""))

    def test_the_tool_description_says_concept_ids_only(self):
        """화면·모델이 읽는 계약에도 적혀 있어야 한다(코드에만 있으면 안 읽힌다)."""
        from vinc_mcp import tools
        spec = next(t for t in tools.TOOLS if t["name"] == "vinc_path")
        self.assertIn("Concept ids", spec["description"])
        props = spec["inputSchema"]["properties"]
        for k in ("a", "b"):
            self.assertIn("concept:", props[k]["description"])


if __name__ == "__main__":
    unittest.main()
