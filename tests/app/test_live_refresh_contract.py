# -*- coding: utf-8 -*-
"""열린 화면이 저절로 새로워지는 배선(VIN-129)의 계약.

JS 러너가 없어 소스를 텍스트로 못 박는다(`test_account_mode_contract` 와 같은 방식).
동작 자체는 브라우저로 확인했다(열린 View 가 뒤에서 쓴 개념을 8.5초 만에 받고, 시트가
열린 동안은 기다렸다가 닫은 뒤 적용하고, `/touch` 에는 다시 그리지 않는다). 여기는 그
동작을 받치는 줄들이 조용히 빠지지 않게 한다: 빠지면 화면은 오류 없이 낡는다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "app/src"
LIVE = (SRC / "live.ts").read_text(encoding="utf-8")
API = (SRC / "api.ts").read_text(encoding="utf-8")
MAIN = (SRC / "main.ts").read_text(encoding="utf-8")
VIEW = (SRC / "surfaces/view.ts").read_text(encoding="utf-8")
LNB = (SRC / "lnb.ts").read_text(encoding="utf-8")
SIGNALS = (SRC / "account-signals.ts").read_text(encoding="utf-8")
VITE = (ROOT / "app/vite.config.ts").read_text(encoding="utf-8")


def _fn(src: str, name: str) -> str:
    m = re.search(rf"^(?:export )?(?:async )?function {name}\(", src, re.M)
    assert m, name
    rest = src[m.start():]
    nxt = re.search(r"^(?:export )?(?:async )?function |^const |^let |^export const ", rest[1:], re.M)
    return rest[: nxt.start() + 1] if nxt else rest


class CadenceTest(unittest.TestCase):

    def test_간격은_10초에서_15초(self):
        m = re.search(r"const EVERY_MS = ([\d_]+);", LIVE)
        self.assertIsNotNone(m)
        self.assertTrue(10_000 <= int(m.group(1).replace("_", "")) <= 15_000)

    def test_숨은_창은_묻지_않고_돌아오면_바로_묻는다(self):
        self.assertIn("if (checking || document.hidden) return;", _fn(LIVE, "check"))
        mount = _fn(LIVE, "mountLiveRefresh")
        self.assertIn('"visibilitychange"', mount)
        self.assertIn('addEventListener("focus"', mount)

    def test_표식은_사이드카_한_곳에서_읽는다(self):
        self.assertIn('call("/changes", { owner })', API)
        # 브라우저 dev 프록시가 빠뜨리면 index.html 을 받아 조용히 안 돈다.
        self.assertIn('"|changes"', VITE)


class DeferTest(unittest.TestCase):

    def test_바쁘면_기다린다(self):
        body = _fn(LIVE, "busy")
        for probe in ("docEditorOpen()", ".sheet-scrim", "docpane", "pointerDown",
                      "isTypingTarget(document.activeElement)"):
            self.assertIn(probe, body, probe)
        apply = _fn(LIVE, "apply")
        self.assertLess(apply.index("if (busy())"), apply.index("pending = false"))

    def test_표면이_아니라고_하면_다시_기다린다(self):
        apply = _fn(LIVE, "apply")
        self.assertIn("surface.value === false", apply)
        self.assertIn("pending = true", apply[apply.index("surface.value === false"):])


class WiringTest(unittest.TestCase):

    def test_기준은_첫_화면보다_먼저_읽는다(self):
        self.assertLess(MAIN.index("mountLiveRefresh();"), MAIN.rindex("route();"))

    def test_표면을_떠나면_갱신기를_푼다(self):
        route = _fn(MAIN, "route")
        self.assertIn("setLiveRefresher(null);", route)

    def test_View_는_카메라를_잇고_다시_받는다(self):
        i = VIEW.index("setLiveRefresher(async () => {")
        block = VIEW[i:i + 400]
        self.assertIn("keepCamera: true", block)
        self.assertIn("keepPanel: true", block)
        self.assertIn("return false", block)   # 재생·카드·상세가 떠 있으면 지금은 아니다

    def test_좌측_트리는_조용히_갈아_끼운다(self):
        body = _fn(LNB, "refreshLnb")
        self.assertIn("if (!opts.quiet)", body)
        self.assertIn("if (opts.quiet) return;", body)
        self.assertIn("refreshLnb({ quiet: true })", LIVE)

    def test_다시_닿으면_접속_불가_줄을_거둔다(self):
        self.assertIn('dispatchEvent(new CustomEvent("vinc:account-reachable"))', LIVE)
        self.assertIn('addEventListener("vinc:account-reachable", hideUnreachable)', SIGNALS)


if __name__ == "__main__":
    unittest.main()
