# -*- coding: utf-8 -*-
"""좁힌 범위가 빈 View 는 스토어가 빈 것처럼 말하지 않는다(VIN-116).

역할 노드가 없는 스토어에서 레일의 Agent 를 누르면 지도가 `vinc/agent` 로 좁혀져 0개가
되고, 가운데 빈 화면이 "문서에서 뽑아낸 개념이 없어요"를 말했다. 실제로는 개념이
119개 있었다. 원인은 둘이었다: View 의 세 빈 자리가 범위와 상관없이 같은 초대를 붙였고,
`wireEmptyInvite` 가 문서가 1건이라도 있으면 무조건 그 문구로 갈아 끼웠다.

앱에는 JS 테스트 러너가 없어(`app/package.json` 에 `test` 없음) 소스에서 잠근다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app" / "src"


def _read(rel: str) -> str:
    return (APP / rel).read_text(encoding="utf-8")


def _body(src: str, head: str) -> str:
    body = src[src.index(head):]
    return body[:body.index("\n}")]


class ScopedEmptyViewTests(unittest.TestCase):
    def test_View_의_빈_자리는_범위를_보고_고른다(self):
        """지도의 빈 자리는 `emptyFor(domains)` 를 거친다. 옛 식으로 남으면 같은 거짓말이
        되살아난다."""
        view = _read("surfaces/view.ts")
        self.assertEqual(len(re.findall(r"setMsg\(emptyFor\(domains\)\)", view)), 1)
        self.assertNotIn("emptyInviteHtml(COPY.view.idle));", view.replace(
            "      : emptyInviteHtml(COPY.view.idle);", ""))
        helper = _body(view, "  function emptyFor(")
        self.assertIn("COPY.view.scopeEmpty", helper)
        self.assertIn("{ scoped: true }", helper)

    def test_좁힌_범위에는_전체로_나가는_길이_있다(self):
        view = _read("surfaces/view.ts")
        self.assertIn('msg.querySelector("[data-ei-all]")', view)
        self.assertIn("trail.length = 1;\n      void show(trail[0]);",
                      view.replace("\r\n", "\n"))
        invite = _read("empty-invite.ts")
        self.assertIn("data-ei-all", _body(invite, "export function emptyInviteHtml"))

    def test_문서는_있고_개념은_없다는_말은_스토어_전체가_빌_때만(self):
        """좁힌 범위에서는 문구를 안 바꾸고, 안 좁혔어도 노드가 하나라도 있으면 안 바꾼다."""
        wire = _body(_read("empty-invite.ts"), "export function wireEmptyInvite")
        self.assertIn('box.hasAttribute("data-scoped")', wire)
        self.assertIn("api.navTree(cfg.owner)", wire)
        self.assertIn("counts?.kinds", wire)
        self.assertIn("if (!docs || nodes) return;", wire)

    def test_새_문구가_두_언어에_있다(self):
        copy = _read("copy.ts")
        self.assertEqual(copy.count("scopeEmpty:"), 2)
        self.assertEqual(copy.count("seeAllCta:"), 2)


if __name__ == "__main__":
    unittest.main()
