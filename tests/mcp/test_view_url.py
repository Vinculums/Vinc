# -*- coding: utf-8 -*-
"""선택이 주소가 되고, 주소를 붙여넣으면 도구가 같은 곳을 본다.

정본: [[decision:a-selection-becomes-an-address-you-paste]].

⚠ 이 문법에는 **거울이 둘**이다 — `app/src` 가 만들고 `vinc_mcp/viewurl.py` 가 읽는다.
이 리포는 같은 모양으로 여러 번 물렸고(역할표·op 표·MCP 계약) 처방은 하나다: 거울을
두되 **테스트가 대조한다**. `GRAMMAR` 가 그 접점이고, 앱이 형태를 늘리면 여기가 먼저
빨개져야 한다. 앱 쪽에는 JS 테스트 러너가 없어서(`app/package.json` 에 `test` 없음)
이 파일이 두 층 사이에 놓인 유일한 검사다.

여기서 고정하는 것 넷:
  ① `GRAMMAR` 의 모든 형태를 파서가 받는다 — 하나라도 `none` 이면 그 딥링크가 죽는다.
  ② 관점(`/as/…`)은 **탐색으로** 떼어 낸다. 위치로 찾으면 꼬리를 붙이는 순간 깨진다.
  ③ 예약어가 아닌 첫 세그먼트는 주제 목록이다 — 그래서 새 키워드를 `_KEYWORDS` 에
     안 넣으면 **"그 이름의 주제"로 조용히 읽힌다.** 오류가 안 나는 쪽이라 더 나쁘다.
  ④ 노드가 아닌 선택(주제·문서)을 `vinc_graph` 에 주면 **거절하고 이유를 말한다.**
     시드를 하나 지어내면 사람이 고른 것과 다른 것을 보여 주게 된다."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import viewurl  # noqa: E402
from vinc_mcp.tools import TOOLS  # noqa: E402


class GrammarTests(unittest.TestCase):
    def test_문법의_모든_형태를_받는다(self):
        for form in viewurl.GRAMMAR:
            with self.subTest(form=form):
                self.assertNotEqual(viewurl.parse(form)["kind"], "none", form)

    def test_전체_URL_도_해시_조각도_받는다(self):
        """사람이 복사하는 것은 주소창의 전체 URL 이다."""
        whole = viewurl.parse("https://vincs.io/app/index.html#view/trace/concept:x/d2")
        frag = viewurl.parse("#view/trace/concept:x/d2")
        self.assertEqual(whole, frag)
        self.assertEqual(whole["seed"], "concept:x")
        self.assertEqual(whole["depth"], 2)

    def test_관점은_읽고_버린다(self):
        """화면이 무엇을 그리느냐는 도구가 알 바 아니다. 그리고 **탐색으로** 떼어 내야
        꼬리(`/d2`)를 붙여도 안 깨진다."""
        a = viewurl.parse("#view/trace/concept:x/d2/as/timeline")
        b = viewurl.parse("#view/trace/concept:x/d2")
        self.assertEqual(a, b)

    def test_깊이는_꼬리에_붙고_구_클라이언트가_무시한다(self):
        """`d` 접두가 없으면 숫자가 주제 이름과 구분이 안 된다."""
        self.assertEqual(viewurl.parse("#view/vinc/eng/d3")["depth"], 3)
        self.assertEqual(viewurl.parse("#view/vinc/eng/3")["domains"],
                         ["vinc/eng/3"])   # 접두 없는 숫자는 그냥 주제의 일부다

    def test_슬래시가_든_주제가_쪼개지지_않는다(self):
        self.assertEqual(viewurl.parse("#view/vinc/engineering")["domains"],
                         ["vinc/engineering"])

    def test_쉼표로_여러_주제(self):
        self.assertEqual(
            viewurl.parse("#view/vinc/engineering,vinc/design")["domains"],
            ["vinc/engineering", "vinc/design"])

    def test_모르는_표면은_none_이다(self):
        """구 표면명 딥링크(앱이 여전히 별칭으로 받는 것)는 여기서 읽지 않는다.
        복사 버튼이 만드는 것은 `#view` 뿐이고, 못 읽으면 도구가 그렇게 말한다 —
        조용히 틀리는 것이 아니라서 그걸로 족하다. 그 이름은 F8 금지어이기도 하다."""
        self.assertEqual(viewurl.parse("#packs/x")["kind"], "none")

    def test_URL_이_아니면_none(self):
        for junk in ("", "나는 URL 이 아니다", "https://vincs.io/", "#search/x", None):
            self.assertEqual(viewurl.parse(junk)["kind"], "none", repr(junk))

    def test_새_키워드는_반드시_예약어_목록에_있다(self):
        """빠뜨리면 그 키워드가 **주제 이름**으로 조용히 읽힌다. 파서가 실제로 특별
        취급하는 것과 목록이 어긋나지 않는지 본다."""
        for kw in ("trace", "ids", "doc"):
            self.assertIn(kw, viewurl._KEYWORDS)
            self.assertNotEqual(viewurl.parse(f"#view/{kw}/x")["kind"], "domains", kw)


class _Bridge:
    """도구가 브리지로 무엇을 보내는지만 본다."""

    def __init__(self):
        self.last = None

    def request(self, method, path, params=None, **kw):
        self.last = (method, path, dict(params or {}))
        return {"kind": "graph", "executed": True, "seed": (params or {}).get("seed")}

    owner = "user:a"


class GraphUrlArgumentTests(unittest.TestCase):
    def setUp(self):
        self.run = next(t for t in TOOLS if t["name"] == "vinc_graph")["run"]
        self.bridge = _Bridge()

    def test_url_에서_시드와_깊이를_읽는다(self):
        self.run({"url": "#view/trace/concept:x/d3", "owner": "user:a"}, self.bridge)
        self.assertEqual(self.bridge.last[2]["seed"], "concept:x")
        self.assertEqual(self.bridge.last[2]["depth"], 3)

    def test_명시_인자가_url_을_이긴다(self):
        """둘 다 오면 사람이 방금 친 것이 이긴다 — url 은 붙여넣은 맥락이다."""
        self.run({"url": "#view/trace/concept:x/d3", "seed_id": "concept:y",
                  "depth": 1, "owner": "user:a"}, self.bridge)
        self.assertEqual(self.bridge.last[2]["seed"], "concept:y")
        self.assertEqual(self.bridge.last[2]["depth"], 1)

    def test_url_로_왔다는_것을_밝힌다(self):
        out = self.run({"url": "#view/trace/concept:x", "owner": "user:a"}, self.bridge)
        self.assertEqual(out.get("from_url"), "concept:x")

    def test_노드가_아닌_선택은_거절하고_이유를_말한다(self):
        """주제나 문서를 골라 놓고 준 것이다. 시드를 지어내면 사람이 고른 것과 다른
        것을 보여 주게 된다 — 조용히 틀리는 쪽이라 거절이 맞다."""
        for url, word in (("#view/vinc/engineering", "domains"),
                          ("#view/doc/d4ab94561078498e3", "doc")):
            b = _Bridge()
            out = self.run({"url": url, "owner": "user:a"}, b)
            self.assertFalse(out["executed"], url)
            self.assertIn(word, out["note"])
            self.assertIsNone(b.last, "거절했으면 브리지를 부르면 안 된다")

    def test_임의_선택은_첫_개를_쓰되_말한다(self):
        out = self.run({"url": "#view/ids/concept:a,record:b", "owner": "user:a"},
                       self.bridge)
        self.assertEqual(self.bridge.last[2]["seed"], "concept:a")
        self.assertEqual(out.get("from_url"), "concept:a")

    def test_둘_다_없으면_거절한다(self):
        b = _Bridge()
        out = self.run({"owner": "user:a"}, b)
        self.assertFalse(out["executed"])
        self.assertIsNone(b.last)

    def test_읽을_수_없는_url_은_거절한다(self):
        b = _Bridge()
        out = self.run({"url": "https://example.com/", "owner": "user:a"}, b)
        self.assertFalse(out["executed"])
        self.assertIsNone(b.last)


class SchemaTests(unittest.TestCase):
    def test_seed_id_가_더는_필수가_아니고_url_이_선언돼_있다(self):
        spec = next(t for t in TOOLS if t["name"] == "vinc_graph")
        props = spec["inputSchema"]["properties"]
        self.assertIn("url", props)
        self.assertTrue(props["url"].get("description"))
        self.assertNotIn("required", spec["inputSchema"])


if __name__ == "__main__":
    unittest.main()
