# -*- coding: utf-8 -*-
"""셸 크롬의 크기가 **규격 위에** 있는가.

발주자 지적 2026-09-09: "gnb 와 lnb 에서 패딩값 그리고 크기 맞춤 등이 모두 깨짐,
패널의 크기나 이런게 규격이 있어 정돈된 크기가 되어야 하는데 지금 그냥 각각 크기가
따로 놀고 있다."

그때 실측한 것(1440×900, 계산 스타일):

  · 목록 한 줄이 넷이었다: 내비 32.85 · 절 제목 37.65 · 주제 31.25 · 문서 28
  · 컨트롤 높이가 셋이었다: 아이콘 34.4 · 검색 37.6 · 레일 버튼 44.5
  · 사이드바 머리 패딩 8.8 / 5.6 / 8.8 / 13.6 — 네 변이 다 4px 격자 밖

원인은 하나다: 크기를 **규칙 안에 직접** 적었고(`.55rem` · `.35rem` · `2.15rem` …)
그 값들이 서로를 모른다. 그래서 크기에 이름을 붙이고 격자를 정했다.

여기서 고정하는 것은 그 규격 자체다. 화면을 안 띄우고 CSS 원문만 읽는다 —
브라우저에서 재는 검사는 이 리포에 붙일 자리가 없고(사이드카·번들이 필요하다),
격자를 깨는 편집은 전부 **원문에 값을 다시 적는 형태**로 들어온다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

CSS_PATH = Path(__file__).resolve().parents[1] / "app" / "src" / "app.css"
#: 주석을 먼저 걷는다(test_select_is_one_rule 과 같은 이유 — 주석 본문이 선택자로
#: 딸려 들어오면 검사가 거짓으로 실패한다). 줄 수는 안 쓰므로 통째로 지운다.
CSS = re.sub(r"/\*.*?\*/", "", CSS_PATH.read_text(encoding="utf-8"), flags=re.S)

#: 규격의 이름들. 값은 전부 .25rem(4px)의 배수여야 한다.
SPEC_VARS = ("--ctl-h", "--row-h", "--rail-btn-h", "--titlebar-h", "--topbar-h",
             "--shell-gap", "--kind-rail-w", "--lnb-w")

#: 목록의 한 줄. 넷이 **같은 변수**를 써야 한다 — 그것이 "한 줄은 한 높이"다.
ROW_RULES = (".lnb-navrow", ".lnb-sec", ".lnb-group", ".lnb-doc")


#: (선택자목록, 본문) 전량. 한 번만 훑는다.
RULES = [(m.group(1), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS)]


def decl(selector: str) -> str:
    """그 선택자를 **포함하는 모든 규칙**의 본문을 이어 준다.

    ⚠ "첫 규칙"이 아니다. 이 파일에는 여러 선택자를 한 줄에 묶은 규칙이 있고
    (`.lnb-navrow, .lnb-doc, .lnb-group, .tb-icon, .btn … { transition: … }`),
    그것이 대개 **먼저** 나온다. 첫 규칙만 보면 크기 선언이 아니라 그 전이 규칙을
    읽고 거짓으로 실패한다(첫 판이 정확히 그랬다)."""
    out = []
    for sels, body in RULES:
        parts = [p.strip() for p in sels.split(",")]
        if any(p == selector or p.endswith(" " + selector) for p in parts):
            out.append(body)
    return "\n".join(out)


class ShellSizeSpecTests(unittest.TestCase):
    def test_규격_변수가_전부_있다(self):
        for name in SPEC_VARS:
            self.assertRegex(
                CSS, re.escape(name) + r"\s*:",
                f"{name} 이 사라졌다 — 크기의 이름이 없어지면 값이 규칙으로 흩어진다")

    def test_규격_값이_4px_격자_위에_있다(self):
        for name in SPEC_VARS:
            m = re.search(re.escape(name) + r"\s*:\s*([0-9.]+)rem", CSS)
            if not m:      # 다른 변수를 참조하는 형태(calc·var)는 여기서 못 재고,
                continue   # 참조하는 쪽이 격자 위면 결과도 격자 위다
            rem = float(m.group(1))
            steps = rem / 0.25
            self.assertAlmostEqual(
                steps, round(steps), places=6,
                msg=f"{name}: {rem}rem 은 .25rem(4px) 격자 밖이다 "
                    f"({rem * 16:.1f}px). 격자 밖 값이 하나 들어오면 그 줄 옆에 선 "
                    f"것들과 높이가 갈린다")

    def test_목록_네_줄이_같은_높이_변수를_쓴다(self):
        for sel in ROW_RULES:
            body = decl(sel)
            self.assertTrue(body, f"{sel} 규칙이 없다")
            self.assertIn(
                "var(--row-h)", body,
                f"{sel} 이 --row-h 를 안 쓴다 — 목록의 한 줄이 다시 갈렸다는 뜻이다")

    def test_아이콘_버튼과_입력이_같은_높이_변수를_쓴다(self):
        # `.lens-btn` 이 여기 있는 이유: 그 하나가 2.15rem 으로 남아 있던 동안
        # 보기·범례 묶음의 폭이 2px 모자라 렌즈 패널과 오른쪽 끝이 어긋났다.
        # 정렬이 컨트롤 하나의 크기에 걸려 있으므로 그 크기를 검사가 잡는다.
        for sel in (".tb-icon", "#tb-search", ".lens-btn"):
            body = decl(sel)
            self.assertTrue(body, f"{sel} 규칙이 없다")
            self.assertIn("var(--ctl-h)", body,
                          f"{sel} 이 --ctl-h 를 안 쓴다 — 나란히 선 컨트롤의 높이가 갈린다")

    def test_패널_머리가_없다(self):
        """트리와 문서 패널의 **머리 줄이 없다.**

        각각 셰브론 하나만 담은 52px 짜리 줄이었고, 그만큼 첫 내용을 아래로 밀었다
        (홈이 카드 시작에서 52px 아래에 있었다). 문서 패널은 더해서 자기 제목 줄과
        겹쳐 머리가 두 줄이었다. 손잡이는 상단 바로 갔다.
        근거 이미지: 발주자 `수정항목/lnb.JPG` · `수정항목/문서 패널.JPG`."""
        for sel in (".lnb-head", ".pane-head"):
            self.assertFalse(
                decl(sel),
                f"{sel} 규칙이 되살아났다 — 패널 첫 줄이 다시 52px 아래로 밀린다")
        for el_id in ("lnb-reopen", "docpane-reopen"):
            self.assertNotIn(
                "#" + el_id, CSS,
                f"#{el_id} 규칙이 되살아났다 — 접기 손잡이는 상단 바에 하나여야 한다 "
                "(접힘에 따라 자리가 바뀌면 같은 것이 두 자리를 갖는다)")

    def test_두_층이_같은_여백으로_첫_줄을_맞춘다(self):
        """레일과 트리 본문이 **같은 변수**로 위 여백을 갖는다.

        그것이 지금의 정렬 수단이다: 머리가 없어진 뒤에는 같은 값을 쓰는 것이 곧
        "두 층의 첫 줄이 같은 높이에서 시작한다"다. 한쪽에 상수를 적어 두면 다른
        쪽을 고치는 날 조용히 어긋난다(머리가 있던 시절에는 중심선을 계산으로
        맞춰야 했고, 그 계산은 머리와 함께 사라졌다)."""
        for sel in ("#lnb-rail", ".lnb-body"):
            body = decl(sel)
            self.assertTrue(body, f"{sel} 규칙이 없다")
            self.assertRegex(
                body, r"padding:\s*var\(--pad-card\)",
                f"{sel} 의 위 여백이 --pad-card 가 아니다 — 두 층의 첫 줄이 갈린다")

    def test_앱을_다루는_창_셋이_한_상자다(self):
        """설정 · 도구 · GRAPH.md 가 **같은 규격**으로 뜬다(발주자 지시 2026-09-09).

        여태 셋이 각자 다른 폭·높이·자리였다(설정 42×34rem 중앙 · 도구 68vw×72vh
        상단 · GRAPH.md 52rem×84vh 상단). 그래서 같은 손잡이 묶음에서 여는 창이
        서로 다른 높이에 떴다. 설정을 시트로 합쳐(`openSheet`, size "panel") 셋이
        한 규칙을 읽게 했으므로, 그 규칙이 토큰을 읽는지를 여기서 지킨다 —
        누가 값을 되돌려 적어도 조용히 갈리지 않는다."""
        body = decl('.sheet[data-size="panel"]')
        self.assertTrue(body, '.sheet[data-size="panel"] 규칙이 없다 '
                              "— 설정 시트가 기본 크기로 떨어진다")
        for tok in ("--modal-w", "--modal-h"):
            self.assertIn(tok, body,
                          f"panel 단계가 {tok} 를 안 읽는다 — 셋이 다시 갈린다")
        self.assertNotIn(
            "#settings-popover", CSS,
            "#settings-popover 규칙이 되살아났다 — 설정은 시트다(구현이 둘이면 "
            "상자·자리·딤이 다음에 또 갈린다)")


if __name__ == "__main__":
    unittest.main()
