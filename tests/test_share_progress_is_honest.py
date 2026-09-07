# -*- coding: utf-8 -*-
"""공유·백업 동선의 기다림은 **한 벌**이고, 그 게이지는 **불확정**이다.

두 가지를 지킨다.

1. 기다림 자리가 전부 `sheetProgress` 를 지난다. 여섯이다 — 공유 시트 넷(팩 준비 ·
   스페이스로 보내기 · 파일로 받기 · 커뮤니티 게시)과 Tools 공유 탭 둘(탭 진입 ·
   전체 백업). 전에는 자리마다 `<p class="hint">` 한 줄이라, 같은 동선에서 같은 일을
   기다리는데 모양이 자리마다 달랐다.

   ⚠ Tools 의 둘은 P16 이 한 벌을 세울 때 사정거리 밖이었다. 탭 진입은 다른 파일에
   있었고, 전체 백업은 P15 가 P16 **머지 뒤에** Tools 로 들여왔다. 그래서 한 벌을
   세우고도 두 자리가 샜다. 이 파일이 두 파일을 함께 보는 이유가 그것이다.

2. 그 게이지가 **퍼센트를 지어내지 않는다.** 여섯 자리가 지나는 네 경로 어디에도
   보고할 단계가 없다(근거는 `sheetProgress` 주석). 그런데 참조 원본(공홈 커뮤니티
   페이지)은 요청이 하나인데 `setInterval(…, 550)` 로 막대를 8 → 32 → 56 → 80% 로
   밀고 응답이 오면 100% 로 점프한다 — 서버가 어디까지 갔는지와 무관한 수치다.
   그 습관이 앱으로 따라 들어오는 것을 여기서 막는다: 진행 표시에 `aria-valuenow`
   도, 그것을 미는 타이머도 없어야 한다.

   같은 죄가 **카피로도** 나올 수 있다. 전체 백업의 라벨은 `packs.loading(0)`
   ("문서 0개를 팩으로 묶는 중")이었는데 `api.activate` 는 담기는 수를 미리 주지
   않으므로 그 0 은 언제나 거짓이었다. 그래서 아래 검사는 마크업만이 아니라 **기다림
   문구가 걸리는 줄 전부**를 훑는다.

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
TOOLS = ROOT / "app" / "src" / "tools.ts"
COMPONENTS = ROOT / "app" / "src" / "components.ts"

#: 기다림을 알리는 카피가 걸리는 모양. `.loading` 이 들어 있는 것은 `common.loading`
#: 뿐 아니라 **수를 세는 척하던** `packs.loading(n)` 도 잡기 위해서다.
BUSY = re.compile(r"\.(loading|preparing|destWorking)\b")


def _slice(src: str, start: str, end: str) -> str:
    """`start` 로 시작해 `end` 앞에서 끊는다 — 관할 밖 경로를 들이지 않는다."""
    i = src.index(start)
    return src[i: src.index(end, i)]


def _code_only(src: str) -> str:
    """주석을 걷어낸다. **주석이 원본의 가짜 타이머를 인용하므로**, 안 걷으면 설명이
    곧 위반으로 읽힌다(실제로 처음 그렇게 떨어졌다)."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    out = [l for l in src.splitlines() if not l.lstrip().startswith("//")]
    return chr(10).join(out)


class ShareProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.components = COMPONENTS.read_text(encoding="utf-8")
        self.sheets = SHEETS.read_text(encoding="utf-8")
        self.tools = TOOLS.read_text(encoding="utf-8")
        # 공유 시트 경로. 가져오기 시트는 다른 경로라 관할 밖이다.
        self.share = self.sheets[: self.sheets.index("export function openImportSheet")]
        # Tools 는 공유 탭만. 만들기 탭에도 기다림이 있지만 이 동선이 아니다.
        self.tl_share = _slice(self.tools, "async function renderShare", "\n// \u2500\u2500 ")
        self.regions = {"sheets.ts(공유)": self.share, "tools.ts(공유 탭)": self.tl_share}

    def test_한_벌은_두_표면이_함께_쓰는_자리에_있다(self):
        """정의가 `sheets.ts` 안에 있으면 Tools 가 못 쓴다 — 실제로 그래서 샜다."""
        self.assertIn("export function sheetProgress", self.components,
                      "진행 표시 한 벌이 공용 자리(components.ts)에 없다")
        for name, src in self.regions.items():
            self.assertNotIn("function sheetProgress", src,
                             f"{name} 에 한 벌이 또 있다 — 두 벌이면 모양이 다시 갈린다")

    def test_기다림_문구가_전부_한_벌을_지난다(self):
        loose = []
        for name, src in self.regions.items():
            for i, line in enumerate(src.splitlines(), 1):
                if line.lstrip().startswith(("*", "//", "/*")):
                    continue
                if not BUSY.search(line) or "sheetProgress(" in line:
                    continue
                loose.append(f"{name}:{i}: {line.strip()}")
        self.assertEqual(
            loose, [],
            "공유·백업 동선에 한 벌을 안 지나는 기다림 표시가 남았다:\n" + "\n".join(loose))

    def test_여섯_자리_전부_걸려_있다(self):
        """시트 넷(팩 준비 · 목적지 확정 · 목적지 선택 · 커뮤니티) + Tools 둘."""
        self.assertEqual(self.share.count("sheetProgress("), 4,
                         "공유 시트의 네 자리가 아니다")
        self.assertEqual(self.tl_share.count("sheetProgress("), 2,
                         "Tools 공유 탭의 두 자리(탭 진입 · 전체 백업)가 아니다")

    def test_게이지가_퍼센트를_지어내지_않는다(self):
        body = self.components[self.components.index("export function sheetProgress"):]
        body = body[: body.index(chr(10) + "}")]
        self.assertIn('role="progressbar"', body, "진행 표시가 progressbar 가 아니다")
        self.assertNotIn("aria-valuenow", body,
                         "단계가 없는데 퍼센트를 실었다 — 진행을 보여주는 척하는 것이다")
        # 원본이 쓰는 가짜 단계 타이머가 따라 들어오지 않았는가.
        for name, src in self.regions.items():
            self.assertFalse(
                re.search(r"setInterval", _code_only(src)),
                f"{name} 에 타이머가 생겼다 — 시계로 단계를 지어내고 있지 않은지 볼 것")

    def test_알림_영역은_미리_서_있는_자리에만_붙는다(self):
        """라이브 영역은 내용이 **바뀌기 전에** DOM 에 있어야 읽힌다.

        `sheetProgress` 의 마크업은 내용과 함께 삽입되므로, 거기에 `role="status"` 를
        달면 스크린리더가 관찰할 대상이 애초에 없다 — 알리는 척만 하는 것이다. 붙일
        상주 자리가 있는 곳은 커뮤니티 게시(`#pub-progress`)와 전체 백업
        (`#tl-backup-msg`) 둘뿐이고, 나머지 넷은 담는 자리를 통째로 갈아치운다.

        라벨 중복도 같이 막는다. 보이는 `<p>` 와 `aria-label` 로 두 번 실으면 두 번
        읽힌다 — 가리키는 것(`aria-labelledby`)은 한 번이다.
        """
        body = self.components[self.components.index("export function sheetProgress"):]
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
        self.assertIn(
            '<div id="tl-backup-msg" role="status">', self.tl_share,
            "전체 백업의 상주 자리에 라이브 영역이 없다")

    def test_불확정_막대가_토큰과_모션_설정을_지킨다(self):
        css = (ROOT / "app" / "src" / "app.css").read_text(encoding="utf-8")
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
