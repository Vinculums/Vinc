# -*- coding: utf-8 -*-
"""recall 응답이 **값이 없는 키를 빼고** 나가는가.

정본: [[decision:hide-implementation-preserve-evidence-surface-failure]] ·
`vinc.canon.tool-response-disclosure` 의 "Hiding is not deleting".

`tenant`·`workspace`·`access_count`·`last_access` 는 MCP 로 쓸 수 없다. 그래서 MCP 로만
먹인 스토어에서는 전 행에 빈 값이 실려 나가고, 화면의 빈칸은 기능 결손처럼 읽힌다.

여기서 고정하는 것은 **생략과 삭제의 차이**다. 넷을 스키마에서 없애는 쪽이 더 간단해
보이지만 그러면 그 값들의 읽기 경로가 사라지고, 그것은
[[decision:every-written-field-has-a-read-path]] 와 정면으로 부딪힌다. 생략은 토큰을
똑같이 줄이면서 계약을 안 바꾼다 — 값이 생기는 날 저절로 다시 나온다.

그리고 **자리**가 중요하다. 질의는 rank 무관 전량을 투영하는데 그건 의도다:
`api/activate.py` 가 같은 행으로 컨텍스트 팩을 만들고, 팩이 그 신호의 유일한 무손실
이관 통로다(설계 §10·D4). 질의에서 빼면 팩이 신호를 잃는다. 그래서 전송 경계다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import server as sv  # noqa: E402


class OmitAbsentTests(unittest.TestCase):
    def test_없는_값은_빠진다(self):
        res = sv._omit_absent_rows({"executed": True, "rows": [
            {"id": "episode:a", "title": "A", "tenant": None, "workspace": None,
             "last_access": None},
        ]})
        self.assertEqual(res["rows"][0], {"id": "episode:a", "title": "A"})

    def test_0_은_값이라_남는다(self):
        """부재와 0 을 뭉개면 이 함수가 판단을 하기 시작한다. `access_count: 0` 은
        "없음"이 아니라 "0회"다."""
        res = sv._omit_absent_rows({"rows": [{"id": "x", "access_count": 0}]})
        self.assertEqual(res["rows"][0], {"id": "x", "access_count": 0})

    def test_빈_문자열과_빈_목록도_남는다(self):
        """뺄 것은 `None` 하나다. 여기서 falsy 를 통째로 걸면 `tags: []` 나
        `summary: ""` 처럼 **말이 되는 빈 값**까지 사라진다."""
        res = sv._omit_absent_rows({"rows": [{"id": "x", "tags": [], "summary": ""}]})
        self.assertEqual(res["rows"][0], {"id": "x", "tags": [], "summary": ""})

    def test_값이_있으면_그대로_나온다(self):
        """읽기 경로가 살아 있다는 것 — 이 검사가 "삭제가 아니다"의 본체다."""
        row = {"id": "x", "tenant": "t:1", "workspace": "w", "access_count": 3,
               "last_access": "2026-08-31"}
        res = sv._omit_absent_rows({"rows": [dict(row)]})
        self.assertEqual(res["rows"][0], row)

    def test_행이_아닌_응답은_안_건드린다(self):
        for payload in ({"executed": False, "note": "no store"},
                        {"rows": "not-a-list"},
                        {}):
            self.assertEqual(sv._omit_absent_rows(dict(payload)), payload)

    def test_dict_이_아닌_행은_그대로_둔다(self):
        res = sv._omit_absent_rows({"rows": ["plain", 3]})
        self.assertEqual(res["rows"], ["plain", 3])


class SeamTests(unittest.TestCase):
    """생략이 **전송 경계**에 있고 질의에는 없는가.

    질의에 넣으면 `api/activate.py` 의 팩이 신호를 잃는다. 그 자리를 옮기는 변이가
    이 검사 없이는 통과한다 — 응답만 보면 결과가 같기 때문이다."""

    def test_질의는_전량을_투영한다(self):
        """`recall_plan` 의 투영에 네 열이 그대로 있어야 한다."""
        from api import query as q
        plan = q.recall_plan(owner="user:x")
        for col in ("tenant", "workspace", "access_count", "last_access"):
            self.assertIn(col, plan.cypher, f"질의가 {col} 을 안 가져온다")

    def test_read_recall_이_생략을_지난다(self):
        """핸들러가 헬퍼를 실제로 부르는가 — 헬퍼만 있고 안 부르면 아무것도 안 바뀐다."""
        import inspect
        src = inspect.getsource(sv.VincApp.read_recall)
        self.assertIn("_omit_absent_rows", src)


if __name__ == "__main__":
    unittest.main()
