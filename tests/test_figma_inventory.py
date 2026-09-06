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
import json
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

    def test_in_repo_extractors_are_non_empty(self):
        # 이 둘은 Vinc 안에서 읽으므로 어디서 돌든 비면 안 된다.
        self.assertGreater(len(fi.app_dialogs()), 10)
        self.assertGreater(len(fi.code_icons()), 10)

    def test_routes_are_non_empty_only_when_the_site_repo_is_here(self):
        # ⚠ CI 는 Vinc 하나만 체크아웃한다. 이걸 무조건 단언했다가 PR #217 에서
        # 정확히 이 줄로 빨개졌다(로컬 초록 · CI 빨강). 사이트가 있을 때만 센다.
        if fi.site_present():
            self.assertGreater(len(fi.site_routes()), 20)
        else:
            self.assertEqual(fi.site_routes(), set())

    def test_the_helper_definition_file_is_not_counted_as_a_call_site(self):
        # components.ts 는 openSheet 등을 **정의**한다. 세면 유령 모달 넷이 생긴다.
        self.assertFalse([d for d in fi.app_dialogs() if d.startswith("components.ts:")])

    def test_internal_only_routes_are_excluded(self):
        self.assertFalse([r for r in fi.site_routes() if r.startswith("/vt-check")])


class TheUnitIsScreenTimesState(unittest.TestCase):
    """화면 하나에 상태가 여섯이면 시안도 여섯이다. 하나만 있는 것과 구분돼야 한다."""

    LIVE = {"VERIFY · CONFIRM", "VERIFY · ERROR"}

    def test_declared_states_that_exist_pass(self):
        m = fi.check_states({"/v/"}, {"/v/": {"confirm": "VERIFY · CONFIRM"}}, self.LIVE)
        self.assertEqual((m["rotten"], m["gaps"], m["declared"]), ([], [], 1))

    def test_a_declared_state_missing_from_figma_fails(self):
        m = fi.check_states({"/v/"}, {"/v/": {"gone": "VERIFY · GONE"}}, self.LIVE)
        self.assertEqual(m["rotten"], [("/v/#gone", "VERIFY · GONE")])

    def test_a_none_state_is_a_declared_gap(self):
        m = fi.check_states({"/v/"}, {"/v/": {"todo": None}}, self.LIVE)
        self.assertEqual((m["gaps"], m["rotten"]), (["/v/#todo"], []))

    def test_a_route_with_no_matrix_is_counted_not_failed(self):
        # 이게 "시안이 몇 장 더 필요한가"의 답이다. 상태가 하나뿐이라고 거짓 주장하지 않는다.
        m = fi.check_states({"/v/", "/pricing/"}, {"/v/": {"confirm": "VERIFY · CONFIRM"}}, self.LIVE)
        self.assertEqual(m["undeclared_routes"], ["/pricing/"])

    def test_a_matrix_entry_for_a_route_that_no_longer_exists_is_ignored_here(self):
        # 라우트가 사라진 것은 _check 의 stale_map 이 잡는다. 여기서 또 세면 이중 보고다.
        m = fi.check_states(set(), {"/gone/": {"x": "VERIFY · GONE"}}, self.LIVE)
        self.assertEqual((m["rotten"], m["declared"]), ([], 0))

    def test_the_real_matrix_only_names_states_that_exist(self):
        live = json.loads((fi.HUB / "imports" / "figma-inventory.json").read_text(encoding="utf-8"))
        frames, _, _, kids = fi.figma_names(live)
        # ⚠ 자식만 보면 단일 상태 라우트가 전부 부패로 잡힌다 — 그 상태는 곧 최상위
        # 프레임이기 때문이다. main() 과 같은 집합을 봐야 테스트가 실물을 검사한다.
        m = fi.check_states(set(fi.ROUTE_STATES), fi.ROUTE_STATES, kids | frames)
        self.assertEqual(m["rotten"], [], "매트릭스가 없는 프레임을 가리킨다")
        self.assertGreater(m["declared"], 30)


class WithoutTheSiteRepoTheGateSkipsRatherThanLies(unittest.TestCase):
    """CI 조건 재현: 사이트가 없을 때 라우트를 검사한 척해도, 빨개져도 안 된다.

    빈 집합을 코드 목록으로 쓰면 맵의 모든 라우트가 "코드에서 사라졌다"로 잡혀 게이트가
    영구히 빨갛고, 그렇다고 조용히 통과시키면 검사했다는 거짓이 된다. 그래서 건너뛰고,
    건너뛴 사실을 출력한다.
    """

    def setUp(self):
        self._site = fi.SITE
        fi.SITE = pathlib.Path(__file__).resolve().parent / "__no_such_site__"

    def tearDown(self):
        fi.SITE = self._site

    def test_site_is_reported_absent(self):
        self.assertFalse(fi.site_present())
        self.assertEqual(fi.site_routes(), set())

    def test_the_absent_repo_does_not_produce_stale_map_failures(self):
        # 이 조합이 바로 CI 에서 게이트를 38건 실패로 만들 뻔한 것이다.
        r = fi._check("routes", fi.site_routes(), fi.ROUTES, set())
        self.assertGreater(len(r["stale_map"]), 30, "빈 코드 목록은 맵 전체를 잔여로 만든다")

    def test_the_gate_passes_and_says_it_skipped(self):
        import io as _io
        import contextlib
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fi.main([])
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIP", out)
        self.assertIn("검사 못 함", out)


class ComponentsAreCheckedAgainstCodeMarks(unittest.TestCase):
    """세는 축이 전부 초록인 채로 이름만 코드와 어긋난 적이 있다(2026-09-06). 그 축이다."""

    LIVE = {"pages": {"02 · App Components": {
        "components": ["App / Chip"], "variants": {"App / Chip": ["State=Default", "State=Static"]}}}}

    def test_a_variant_whose_code_mark_is_gone_fails(self):
        # 코드에서 클래스를 개명하면 여기서 잡힌다. 개수는 그대로라 다른 축은 못 본다.
        r = fi.check_components(self.LIVE, {"App / Chip": {"State=Default": "chip",
                                                           "State=Static": "chip-static"}}, ".chip {}")
        self.assertEqual(r["rotten"], [("App / Chip · State=Static", "chip-static")])

    def test_a_new_figma_variant_with_no_declaration_fails(self):
        r = fi.check_components(self.LIVE, {"App / Chip": {"State=Default": "chip"}}, ".chip {}")
        self.assertEqual(r["undeclared"], ["App / Chip · State=Static"])

    def test_a_declared_variant_deleted_from_figma_fails(self):
        r = fi.check_components(self.LIVE, {"App / Chip": {"State=Default": "chip",
                                                           "State=Static": "chip-static",
                                                           "State=Gone": "chip-gone"}},
                                ".chip .chip-static .chip-gone")
        self.assertEqual(r["stale"], ["App / Chip · State=Gone"])

    def test_a_none_mark_is_a_plain_default_not_a_failure(self):
        # 수식자가 없는 기본값은 결손이 아니다. 실패로 세면 게이트가 곧 꺼진다.
        r = fi.check_components(self.LIVE, {"App / Chip": {"State=Default": None,
                                                           "State=Static": "chip-static"}}, "chip-static")
        self.assertEqual((r["plain"], r["rotten"]), (["App / Chip · State=Default"], []))

    def test_the_real_map_matches_the_real_dump_and_the_real_source(self):
        live = json.loads((fi.HUB / "imports" / "figma-inventory.json").read_text(encoding="utf-8"))
        r = fi.check_components(live, fi.APP_COMPONENTS, fi.app_source())
        self.assertEqual((r["undeclared"], r["stale"], r["rotten"]), ([], [], []))
        self.assertGreater(r["declared"], 40)


if __name__ == "__main__":
    unittest.main()
