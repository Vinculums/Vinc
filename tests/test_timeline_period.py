"""타임라인 기간(2026-09-28) — 상한보다 먼저 거르고, 두 입구가 한 상태를 쓴다.

셈(경계 · 알갱이 · 망가진 값)은 `app/dev/period-check.ts` 가 브라우저 없이 직접 돌린다.
여기서는 그 셈이 **어디서** 불리는지를 붙잡는다: 기간이 1,200개 상한보다 뒤에서 걸리면
옛 기간을 골라도 옛 기록이 안 나온다 — 고치기 전의 증상(실측 2026-09-28: 날짜 2,479개 중
상한이 남긴 것이 전부 09-06 이후)이 그대로 돌아온다.

근거: [[vinc.plan.view-card-timeline-period-authoring-2026-09-27]] §2
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = (ROOT / "app" / "src" / "surfaces" / "view.ts").read_text(encoding="utf-8")
PREFS = (ROOT / "app" / "src" / "view-prefs.ts").read_text(encoding="utf-8")
PICKER = (ROOT / "app" / "src" / "date-range.ts").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "src" / "app.css").read_text(encoding="utf-8")


def _timeline_branch() -> str:
    a = VIEW.index('if (p.view === "timeline") {')
    return VIEW[a:VIEW.index('if (p.view === "bundle") {', a)]


class FilterBeforeTheCap(unittest.TestCase):
    def test_period_is_applied_before_the_cap(self):
        branch = _timeline_branch()
        self.assertLess(branch.index("const range = periodRange(p);"),
                        branch.index("const over = dated.length - PLANAR_CAP;"))
        self.assertIn("allDated.filter((n) => inPeriod(n.ts!, range))", branch)

    def test_the_picker_dots_count_before_the_period(self):
        branch = _timeline_branch()
        self.assertLess(branch.index("timelineDays.set("),
                        branch.index("const range = periodRange(p);"))

    def test_the_cap_caption_offers_to_narrow(self):
        self.assertIn("b.textContent = COPY.ui.viewx.narrowPeriod;", VIEW)
        self.assertIn(".hint-act {", CSS)

    def test_an_empty_period_says_so_and_offers_a_reset(self):
        self.assertIn("COPY.ui.viewx.periodNone", VIEW)
        self.assertIn('applyPeriod({ period: "all" })', VIEW)


class OneStateTwoDoors(unittest.TestCase):
    def test_period_leads_the_timeline_options(self):
        self.assertIn('case "timeline": return ["period", "basis", "docs", "lineage"];', VIEW)

    def test_chip_in_the_status_bar(self):
        chrome = VIEW[VIEW.index('<div id="view-chrome">'):VIEW.index('<div id="view-metrics">')]
        self.assertIn('<div id="view-period" hidden></div>', chrome)

    def test_a_chip_change_does_not_pop_the_panel_open(self):
        a = VIEW.index("async function applyPeriod(")
        self.assertIn("if (!viewPanel.hidden) { await applyPrefs(mut, refocus); return; }",
                      VIEW[a:a + 400])

    def test_default_is_all_and_bad_values_fall_back(self):
        self.assertIn('period: "all", periodFrom: "", periodTo: "",', PREFS)
        self.assertIn('period: PERIODS.includes(p.period) ? p.period : "all",', PREFS)

    def test_not_in_the_view_address(self):
        # 주소 문법은 MCP 서버의 파서와 나눠 쓰는 계약이다 — 기간은 기기별 보기 설정이다.
        url = (ROOT / "app" / "src" / "view-url.ts").read_text(encoding="utf-8")
        self.assertNotIn("period", url)


class ThePicker(unittest.TestCase):
    def test_no_native_date_input(self):
        self.assertNotIn('type="date"', PICKER)

    def test_grid_semantics_and_keyboard(self):
        for s in ('role="grid"', 'role="gridcell"', 'role="columnheader"',
                  '"PageUp"', '"PageDown"', '"Home"', '"End"'):
            self.assertIn(s, PICKER)

    def test_escape_closes_only_the_picker(self):
        a = PICKER.index('if (e.key === "Escape") {')
        self.assertIn("e.stopPropagation();", PICKER[a:a + 300])

    def test_point_colour_is_a_ring_not_a_fill(self):
        block = CSS[CSS.index(".drp {"):]
        self.assertNotIn("background: hsl(var(--ring)", block)
        self.assertNotIn("background: hsl(var(--point)", block)
        self.assertIn("outline: 2px solid hsl(var(--ring))", block)

    def test_utc_days_like_the_columns(self):
        self.assertIn("utcDay", PICKER)
        self.assertNotIn("getDate()", PICKER)
        self.assertNotIn("getMonth()", PICKER)


if __name__ == "__main__":
    unittest.main()
