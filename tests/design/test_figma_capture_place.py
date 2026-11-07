# -*- coding: utf-8 -*-
"""캡처 뒷단계(figma_capture_place.py)가 지키는 것.

캡처 서비스가 노드 id 를 주지 않게 된 뒤(2026-11-04) 보드는 프레임 이름으로 찾아 세운다. 여기서 잠그는 것:
보드 이름은 인벤토리 게이트의 이름 함수와 같다 · 실패한 상태와 이미 세운 상태는 다시 세우지 않는다 ·
응답에 id 가 있던 상태는 그 id 로 간다 · 되적기는 세우지 못한 상태를 이름으로 돌려준다 · use_figma 스크립트는
한 페이지만 열고 이름이 정확히 같은 최상위 프레임만 본다(지우는 호출이 없다).
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "design" / "figma_capture_place.py"
_spec = importlib.util.spec_from_file_location("figma_capture_place", _PATH)
fp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fp)

RESULTS = {
    "view__3d": {"ok": True, "placed": False, "captureId": "a", "nodeId": None, "claimUrl": "https://www.figma.com/design/K"},
    "view__item-page": {"ok": True, "placed": False, "captureId": "b", "nodeId": "12:34", "claimUrl": "x?node-id=12-34"},
    "topbar__settings-about": {"ok": False, "captureId": "c", "error": "step timeout"},
    "home__default": {"ok": True, "placed": True, "nodeId": "9:9", "board": "App · Home / dark"},
}


class Plan(unittest.TestCase):
    def test_board_names_come_from_the_inventory_gate(self):
        self.assertEqual(fp.board_name("app", "view__item-page"), fp._fi.app_board_name("view__item-page"))
        self.assertEqual(fp.board_name("app", "view__item-page"), "App · View · item-page / dark")
        site_state = next(iter(fp._fi.SITE_ROUTE_TITLES)) + "__default"
        self.assertEqual(fp.board_name("site", site_state), fp._fi.site_board_name(site_state))
        with self.assertRaises(ValueError):
            fp.board_name("email", "x__y")

    def test_failed_and_already_placed_states_are_left_alone(self):
        self.assertEqual(fp.pending(RESULTS), ["view__3d", "view__item-page"])

    def test_a_returned_node_id_is_used_and_a_missing_one_falls_back_to_the_name(self):
        p = fp.plan(RESULTS, "app", "40:3", "Surfaces")
        by = {i["state"]: i for i in p["items"]}
        self.assertEqual(by["view__item-page"]["nodeId"], "12:34")
        self.assertIsNone(by["view__3d"]["nodeId"])
        self.assertEqual((p["page"], p["section"]), ("40:3", "Surfaces"))

    def test_the_script_opens_one_page_and_never_deletes(self):
        js = fp.script(fp.plan(RESULTS, "app", "40:3", "Surfaces"))
        self.assertEqual(js.count("setCurrentPageAsync"), 1)
        self.assertNotIn(".remove(", js)
        self.assertIn('n.name === it.state', js)          # 정확히 같은 이름만
        self.assertIn('"App · View · item-page / dark"', js)
        # 섹션은 옮길 프레임이 있을 때만 만든다: createSection 은 ensureSection 안에만 있다
        self.assertEqual(js.count("createSection"), 1)
        self.assertLess(js.index("const ensureSection"), js.index("createSection"))
        self.assertIn("if (moved)", js)


class Merge(unittest.TestCase):
    def test_placement_is_written_back_and_misses_are_named(self):
        placement = {"placed": {"view__3d": {"nodeId": "50:1", "board": "App · View · 3d / dark"}},
                     "missing": ["view__item-page"], "duplicates": {}}
        new, still = fp.merge(RESULTS, placement)
        self.assertEqual(new["view__3d"]["nodeId"], "50:1")
        self.assertTrue(new["view__3d"]["placed"])
        self.assertEqual(still, ["view__item-page"])
        self.assertFalse(RESULTS["view__3d"]["placed"])   # 입력은 건드리지 않는다

    def test_cli_round_trip(self):
        with tempfile.TemporaryDirectory() as t:
            res = pathlib.Path(t) / "job.result.json"
            res.write_text(json.dumps(RESULTS), encoding="utf-8")
            out = subprocess.run([sys.executable, str(_PATH), "plan", str(res), "--surface", "app",
                                  "--page", "40:3", "--section", "Surfaces"], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("setCurrentPageAsync", out.stdout)
            pl = pathlib.Path(t) / "placement.json"
            pl.write_text(json.dumps({"placed": {
                "view__3d": {"nodeId": "50:1", "board": "App · View · 3d / dark"},
                "view__item-page": {"nodeId": "12:34", "board": "App · View · item-page / dark"}}}), encoding="utf-8")
            m = subprocess.run([sys.executable, str(_PATH), "merge", str(res), str(pl)], capture_output=True, text=True)
            self.assertEqual(m.returncode, 0, m.stderr)
            again = subprocess.run([sys.executable, str(_PATH), "plan", str(res), "--surface", "app",
                                    "--page", "40:3", "--section", "Surfaces"], capture_output=True, text=True)
            self.assertEqual(again.stdout, "")             # 다 세웠으면 다시 낼 스크립트가 없다


if __name__ == "__main__":
    unittest.main()
