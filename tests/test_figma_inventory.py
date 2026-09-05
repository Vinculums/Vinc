# -*- coding: utf-8 -*-
"""화면·모달·아이콘 인벤토리 게이트 — 결손과 회귀를 갈라야 쓸모가 있다.

왜 이 파일이 있나: 이 게이트의 값은 **아직 안 그린 것**(결손)과 **새로 생겼는데 아무도
선언 안 한 것**(회귀)을 구분하는 데 전부 들어 있다. 둘을 같이 빨갛게 만들면 결손 31건
때문에 CI 가 영구히 빨갛고 곧 꺼진다. 둘 다 조용히 넘기면 2026-09-05 이전으로 돌아간다
— 그때는 앱 모달 다섯과 문서 다섯 쪽이 몇 주 동안 아무에게도 안 세어졌다.

그래서 여기서 지키는 것은 하나다: **선언은 통과, 미선언은 실패.**
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "design-hub" / "scripts" / "figma_inventory.py"
_spec = importlib.util.spec_from_file_location("figma_inventory", _PATH)
fi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fi)


def fails(r) -> int:
    return len(r["undeclared"]) + len(r["rotten"]) + len(r["stale_map"])


class DeclaredIsNotFailure(unittest.TestCase):
    def test_a_declared_gap_is_a_gap_not_a_failure(self):
        r = fi._check("t", {"/a/", "/b/"}, {"/a/": "Frame A", "/b/": None}, {"Frame A"})
        self.assertEqual(r["gaps"], ["/b/"])
        self.assertEqual(fails(r), 0)

    def test_everything_mapped_and_present_is_clean(self):
        r = fi._check("t", {"/a/"}, {"/a/": "Frame A"}, {"Frame A"})
        self.assertEqual((r["gaps"], fails(r)), ([], 0))


class UndeclaredIsFailure(unittest.TestCase):
    def test_a_new_code_item_with_no_map_entry_fails(self):
        # 화면이나 모달을 늘리면서 Figma 대응을 안 적은 경우. 이 게이트의 존재 이유다.
        r = fi._check("t", {"/a/", "/new/"}, {"/a/": "Frame A"}, {"Frame A"})
        self.assertEqual(r["undeclared"], ["/new/"])
        self.assertEqual(fails(r), 1)

    def test_a_map_entry_pointing_at_a_missing_frame_fails(self):
        # Figma 에서 프레임을 개명·삭제하면 맵이 조용히 썩는다.
        r = fi._check("t", {"/a/"}, {"/a/": "Frame A"}, set())
        self.assertEqual(r["rotten"], [("/a/", "Frame A")])
        self.assertEqual(fails(r), 1)

    def test_a_map_entry_whose_code_item_vanished_fails(self):
        # 라우트를 지웠는데 맵에 남아 있으면 게이트가 없는 화면을 지키게 된다.
        r = fi._check("t", set(), {"/gone/": "Frame G"}, {"Frame G"})
        self.assertEqual(r["stale_map"], ["/gone/"])
        self.assertEqual(fails(r), 1)


class IconGapsUseTheDeclaredSet(unittest.TestCase):
    def test_a_declared_icon_gap_does_not_fail(self):
        r = fi._check("i", {"pack"}, {"pack": "Icon / pack"}, set(), frozenset({"pack"}))
        self.assertEqual((r["gaps"], fails(r)), (["pack"], 0))

    def test_an_undeclared_new_icon_fails(self):
        r = fi._check("i", {"brand-new"}, {"brand-new": "Icon / brand-new"}, set(), frozenset({"pack"}))
        self.assertEqual(fails(r), 1)


class ExtractorsSeeTheRealTree(unittest.TestCase):
    """추출기가 조용히 빈손이면 게이트는 통과하면서 아무것도 안 지킨다."""

    def test_routes_and_dialogs_and_icons_are_non_empty(self):
        self.assertGreater(len(fi.site_routes()), 20)
        self.assertGreater(len(fi.app_dialogs()), 10)
        self.assertGreater(len(fi.code_icons()), 20)

    def test_the_helper_definition_file_is_not_counted_as_a_call_site(self):
        # components.ts 는 openSheet 등을 **정의**한다. 세면 유령 모달 넷이 생긴다.
        self.assertFalse([d for d in fi.app_dialogs() if d.startswith("components.ts:")])

    def test_internal_only_routes_are_excluded(self):
        self.assertFalse([r for r in fi.site_routes() if r.startswith("/vt-check")])


if __name__ == "__main__":
    unittest.main()
