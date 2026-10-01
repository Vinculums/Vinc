# -*- coding: utf-8 -*-
"""끌어서 잇기(3D·2D 의 Alt 끌기)는 잇기 버튼과 **같은** 관계 고르기로 간다.

정본: [[vinc.plan.view-card-timeline-period-authoring-2026-09-27]] §3.3 (미뤘던 항목).

상태 기계(Alt 가 아니면 지나감 · 다른 노드에서 놓을 때만 부름 · 빈 곳/같은 노드/Esc/취소는
아무것도 안 부름)는 `app/dev/drag-connect-check.ts` 가 브라우저 없이 돌린다. 여기서는 그
검사가 닿지 않는 **배선**을 소스로 대조한다(`test_view_retheme.py` 와 같은 관례):

- 쓰는 길은 `openRelationPicker` 하나뿐이다 — 끌기 모듈과 렌더러는 쓰기를 모른다.
- 두 렌더러가 같은 모듈을 걸고, 해체할 때 푼다(캔버스 요소는 관점 전환 뒤에도 남는다).
- 끄는 동안 카메라가 멎는다(3D 트랙볼 · 2D sigma 카메라).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app" / "src"


def _read(rel: str) -> str:
    return (APP / rel).read_text(encoding="utf-8")


def _fn(src: str, head: str) -> str:
    """`head` 로 시작하는 함수 본문 — 같은 들여쓰기의 다음 선언 전까지."""
    at = src.index(head)
    indent = at - (src.rfind("\n", 0, at) + 1)
    rest = src[at:]
    nxt = re.search(r"\n" + " " * indent + r"(const|function|let|export|type|/\*\*|el\.add)",
                    rest[len(head):])
    return rest if not nxt else rest[:len(head) + nxt.start()]


class DragModuleTests(unittest.TestCase):
    src = _read("surfaces/drag-connect.ts")

    def test_import_0_이라_node_로_돈다(self):
        self.assertNotRegex(self.src, r"(?m)^import ")
        self.assertTrue((ROOT / "app" / "dev" / "drag-connect-check.ts").exists())

    def test_쓰기를_모른다(self):
        for bad in ("ingestFragment", "/ingest", "api.", "fetch("):
            self.assertNotIn(bad, self.src, bad)

    def test_Alt_만_받고_Shift_는_영역_선택에_양보한다(self):
        down = _fn(self.src, "const onDown = ")
        self.assertIn("!e.altKey", down)
        self.assertIn("e.shiftKey", down)
        # 노드가 아니면 평소 손에 맡긴다 — preventDefault 보다 먼저 돌아간다.
        self.assertLess(down.index("if (!hit) return"), down.index("e.preventDefault()"))

    def test_다른_노드에서_놓을_때만_부른다(self):
        end = _fn(self.src, "function end(commit: boolean)")
        self.assertIn("commit && a && b && b.id !== a.id", end)
        self.assertEqual(self.src.count("o.onConnect("), 1)
        self.assertIn("o.lock(false)", end)

    def test_취소_경로는_end_false(self):
        self.assertIn('if (e.key !== "Escape" || !src) return;', self.src)
        key = _fn(self.src, "const onKey = ")
        self.assertIn("end(false)", key)
        self.assertIn('el.addEventListener("pointercancel", onCancel, true)', self.src)
        self.assertIn('window.addEventListener("blur", onCancel)', self.src)

    def test_움직임이_없다(self):
        css = _read("app.css")
        block = css[css.index(".dc-layer"):css.index(".dc-ring.dc-no")]
        for bad in ("transition", "animation"):
            self.assertNotIn(bad, block)
        # 포인트색은 선과 고리의 획에만 — 면(fill)을 칠하지 않는다.
        self.assertNotRegex(block, r"fill:\s*hsl\(var\(--point")


class RendererWiringTests(unittest.TestCase):
    def test_3D_는_걸고_해체에서_푼다(self):
        s = _read("surfaces/view3d.ts")
        self.assertIn("wireDragConnect(el, {", s)
        self.assertIn("unwireConnect?.();", s)
        self.assertIn("controls.enabled = !on", s)

    def test_2D_는_걸고_해체에서_푼다(self):
        s = _read("surfaces/view2d.ts")
        self.assertIn("wireDragConnect(el, {", s)
        dispose = s[s.index("    dispose() {"):s.index("sigma.kill();")]
        self.assertIn("unwireConnect?.();", dispose)
        self.assertIn("sigma.getCamera().disable()", s)

    def test_렌더러는_쓰기를_모른다(self):
        for rel in ("surfaces/view3d.ts", "surfaces/view2d.ts"):
            s = _read(rel)
            self.assertNotIn("ingestFragment", s, rel)
            self.assertNotIn("openRelationPicker", s, rel)


class ViewWiringTests(unittest.TestCase):
    view = _read("surfaces/view.ts")

    def test_두_마운트가_같은_손잡이를_넘긴다(self):
        self.assertEqual(self.view.count("onConnect: connectByDrag, canConnect: canLinkPair,"), 2)

    def test_같은_관계_고르기를_연다(self):
        body = _fn(self.view, "function connectByDrag(")
        self.assertIn("m.openRelationPicker(a, b)", body)
        self.assertIn("dropPick()", body)
        self.assertNotIn("ingestFragment", body)

    def test_쓰는_길은_connect_ts_하나(self):
        """앱 전체에서 엣지를 쓰는 잇기 호출은 관계 고르기 안의 하나뿐이다."""
        c = _read("connect.ts")
        self.assertEqual(c.count("api.ingestFragment("), 1)
        picker = c[c.index("export function openRelationPicker"):]
        self.assertIn("api.ingestFragment(", picker)
        self.assertNotIn("ingestFragment", _read("surfaces/drag-connect.ts"))

    def test_버튼_길이_남아_있다(self):
        self.assertIn('q("#c-connect")!.addEventListener("click"', self.view)
        self.assertIn("m.startConnect(", self.view)

    def test_발견성_한_줄(self):
        c = _read("connect.ts")
        self.assertIn('A.dragHint(/mac/i.test(navigator.platform) ? "Option" : "Alt")', c)
        copy = _read("copy.ts")
        self.assertEqual(copy.count("dragHint:"), 2)


if __name__ == "__main__":
    unittest.main()
