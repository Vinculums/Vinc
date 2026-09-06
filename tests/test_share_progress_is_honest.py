# -*- coding: utf-8 -*-
"""공유 경로의 기다림은 **한 벌**이고, 그 게이지는 **불확정**이다.

두 가지를 지킨다.

1. 공유 시트의 기다림 자리(팩 준비 · 스페이스로 보내기 · 파일로 받기 · 커뮤니티
   게시)가 전부 `sheetProgress` 를 지난다. 전에는 자리마다 `<p class="hint">` 한
   줄이라 같은 시트 안에서 같은 일을 기다리는데 모양이 자리마다 달랐다.

2. 그 게이지가 **퍼센트를 지어내지 않는다.** `api.ts` 의 `call()` 은 `fetch` 하나 +
   `res.json()` 하나라 중간 보고가 물리적으로 없다. 그런데 참조 원본(공홈 커뮤니티
   페이지)은 요청이 하나인데 `setInterval(…, 550)` 로 막대를 8 → 32 → 56 → 80% 로
   밀고 응답이 오면 100% 로 점프한다 — 서버가 어디까지 갔는지와 무관한 수치다.
   그 습관이 앱으로 따라 들어오는 것을 여기서 막는다: 진행 표시에 `aria-valuenow`
   도, 그것을 미는 타이머도 없어야 한다.

⚠ 앱에는 JS 테스트 러너가 없다(`app/package.json` 에 `test` 없음). 소스를 읽어
   대조하는 것이 이 리포의 관례다(`tests/test_view_url.py` ·
   `tests/test_share_sheet_spec.py` 와 같은 자리).

⚠ `unittest.TestCase` 로 둘 것. CI 의 무조건 잡은 `unittest.discover` 라 모듈 수준
   `def test_*` 를 아예 수집하지 못한다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHEETS = ROOT / "app" / "src" / "sheets.ts"
APPCSS = ROOT / "app" / "src" / "app.css"

#: 기다림을 알리는 카피 키. 이것이 화면에 걸리는 자리가 곧 진행 표시 자리다.
BUSY_KEYS = ("preparing", "destWorking")


def _share_path(src: str) -> str:
    """공유 경로만 잘라 낸다 — 가져오기 시트는 다른 경로라 관할 밖이다."""
    return src[: src.index("export function openImportSheet")]


def _code_only(src: str) -> str:
    """주석을 걷어낸다. **이 파일의 주석이 원본의 가짜 타이머를 인용하므로**, 안 걷으면
    설명이 곧 위반으로 읽힌다(실제로 처음 그렇게 떨어졌다)."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    out = [l for l in src.splitlines() if not l.lstrip().startswith("//")]
    return chr(10).join(out)


class ShareProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = SHEETS.read_text(encoding="utf-8")
        self.share = _share_path(self.src)
        self.share_code = _code_only(self.share)

    def test_기다림_문구가_전부_한_벌을_지난다(self):
        """`preparing`·`destWorking` 이 걸리는 자리는 전부 `sheetProgress` 여야 한다."""
        self.assertIn("function sheetProgress", self.src, "진행 표시 한 벌이 없다")
        loose = []
        for i, line in enumerate(self.share.splitlines(), 1):
            if line.lstrip().startswith(("*", "//", "/*")):
                continue
            if not any(k in line for k in BUSY_KEYS):
                continue
            if "sheetProgress(" in line:
                continue
            loose.append(f"{i}: {line.strip()}")
        self.assertEqual(
            loose, [],
            "공유 경로에 한 벌을 안 지나는 기다림 표시가 남았다:\n" + "\n".join(loose))

    def test_네_자리_전부_걸려_있다(self):
        """팩 준비 · 목적지 확정 저장 · 목적지 고른 뒤 저장 · 커뮤니티 게시."""
        self.assertEqual(
            self.share.count("sheetProgress("), 5,
            "정의 1 + 호출 4 여야 한다(팩 준비 · 목적지 확정 · 목적지 선택 · 커뮤니티)")

    def test_게이지가_퍼센트를_지어내지_않는다(self):
        body = self.src[self.src.index("function sheetProgress"):]
        body = body[: body.index("\n}")]
        self.assertIn('role="progressbar"', body, "진행 표시가 progressbar 가 아니다")
        self.assertNotIn("aria-valuenow", body,
                         "단계가 없는데 퍼센트를 실었다 — 진행을 보여주는 척하는 것이다")
        # 원본이 쓰는 가짜 단계 타이머가 따라 들어오지 않았는가.
        self.assertFalse(
            re.search(r"setInterval|setTimeout", self.share_code),
            "공유 경로에 타이머가 생겼다 — 시계로 단계를 지어내고 있지 않은지 볼 것")

    def test_알림_영역은_미리_서_있는_자리에만_붙는다(self):
        """라이브 영역은 내용이 **바뀌기 전에** DOM 에 있어야 읽힌다.

        `sheetProgress` 의 마크업은 내용과 함께 삽입되므로, 거기에 `role="status"` 를
        달면 스크린리더가 관찰할 대상이 애초에 없다 — 알리는 척만 하는 것이다. 붙일
        상주 자리가 있는 곳은 커뮤니티 게시뿐이고(`#pub-progress` 가 폼과 함께 먼저
        선다) 나머지 셋은 `sheet.body` 를 통째로 갈아치운다.

        라벨 중복도 같이 막는다. 보이는 `<p>` 와 `aria-label` 로 두 번 실으면 두 번
        읽힌다 — 가리키는 것(`aria-labelledby`)은 한 번이다.
        """
        body = self.src[self.src.index("function sheetProgress"):]
        body = body[: body.index(chr(10) + "}")]
        self.assertNotIn(
            'role="status"', body,
            "삽입되는 마크업에 라이브 영역을 달았다 — 관찰 대상이 없어 알리지 못한다")
        self.assertIn("aria-labelledby", body, "진행 막대에 이름이 없다")
        self.assertNotIn(
            "aria-label=", body,
            "보이는 문구와 aria-label 로 라벨을 두 번 실었다 — 두 번 읽힌다")
        self.assertIn(
            '<div id="pub-progress" role="status">', self.share,
            "커뮤니티 게시의 상주 자리에 라이브 영역이 없다")

    def test_불확정_막대가_토큰과_모션_설정을_지킨다(self):
        css = APPCSS.read_text(encoding="utf-8")
        self.assertIn(".sheet-progress-track", css, "진행 막대 규칙이 없다")
        block = css[css.index(".sheet-progress {"):]
        block = block[: block.index("\n/* Command palette")] if "\n/* Command palette" in block else block[:1200]
        self.assertIn("hsl(var(--primary))", block, "채움이 토큰에서 오지 않는다")
        self.assertIn("hsl(var(--muted))", block, "트랙이 토큰에서 오지 않는다")
        self.assertIn("prefers-reduced-motion: reduce", block,
                      "모션을 줄인 환경에서 무한 애니메이션이 그대로 돈다")
        self.assertIn("busySweep", block,
                      "지도의 바쁨 표시와 다른 움직임을 새로 만들었다")
        self.assertIn("@keyframes busySweep", css, "쓰는 키프레임이 정의돼 있지 않다")
        # 다크 채움. 실측(브라우저 계산색 → WCAG 대비): 라이트는 --primary 가 트랙 대비
        # 9.58:1 로 통과하지만 다크에서는 1.87:1 로 미달이고, --ring 은 정확히 반대다
        # (라이트 2.24 · 다크 6.74). 어느 한 토큰도 두 모드를 다 통과하지 못하므로
        # 다크 분기가 없으면 막대가 트랙에 잠긴다 — 참조 원본이 그 상태다.
        self.assertIn('[data-theme="dark"] .sheet-progress-track > span', css,
                      "다크에서 채움이 트랙에 잠긴다(WCAG 1.4.11 3:1 미달)")


if __name__ == "__main__":
    unittest.main()
