# -*- coding: utf-8 -*-
"""`#packs` 가 없어지면서 함께 사라질 뻔한 것들이 Tools 에 실제로 있는가.

P15 의 위험은 전부 **순서**에 있다. 화면 하나를 지우는 일은 그 화면이 유일한 진입점인
기능을 지우는 일이기도 한데, 이 리포에는 "이 화면에 도달할 수 있는가"를 재는 것이
없어서 **아무것도 빨개지지 않는다.** 그래서 그 자리를 여기서 만든다.

재는 것은 다섯이다. 셋은 무조건 있었고(가져오기 · 공유 · 전체 백업), 둘은 `WEB` 게이트
뒤에 있었다(다른 스페이스에서 불러오기 · 커뮤니티 게시). 뒤의 둘은 **트리에 다른
진입점이 아예 없었으므로** 옮기지 않으면 이전이 아니라 삭제다.

⚠ 소스 검사다. 앱에는 JS 테스트 러너가 없어서(`app/package.json` 에 `test` 없음)
`tests/mcp/test_view_url.py` · `tests/app/test_share_sheet_spec.py` 와 같은 자리에 놓는다.
그래서 이 파일이 재는 것은 **동작이 아니라 배선의 존재**다 — 렌더해 본 적은 없다.

⚠ `unittest.TestCase` 로 둘 것. CI 의 무조건 잡은 `unittest.discover` 라 모듈 수준
`def test_*` 를 아예 수집하지 못한다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app" / "src"


def _body(src: str, head: str) -> str:
    """`head` 로 시작하는 최상위 함수의 본문만 잘라 낸다 — 열 0 의 `}` 가 끝이다."""
    body = src[src.index(head):]
    return body[:body.index("\n}")]


def _inner(src: str, head: str) -> str:
    """두 칸 들여 쓴 안쪽 함수의 본문 — 열 2 의 `}` 가 끝이다."""
    body = src[src.index(head):]
    return body[:body.index("\n  }")]


class ToolsAbsorbsPacksTests(unittest.TestCase):
    def setUp(self):
        self.tools = (APP / "tools.ts").read_text(encoding="utf-8")
        self.sheets = (APP / "sheets.ts").read_text(encoding="utf-8")
        self.share = _body(self.tools, "async function renderShare")

    # ── ① 가져오기 ─────────────────────────────────────────────────────────
    def test_가져오기_탭이_드롭존을_품는다(self):
        """`파일 고르기` 버튼 한 줄로 되돌리면 끌어다 놓기·샘플 팩·여러 파일 일괄
        적재가 앱에서 통째로 사라진다 — `renderIntake` 를 부르는 곳이 여기 하나뿐이고,
        View 의 빈 상태 초대는 그래프가 **빌 때만** 뜬다."""
        imp = _body(self.tools, "function renderImport")
        self.assertIn("renderIntake(", imp, "가져오기 탭이 드롭존을 안 심는다")

    def test_드롭존을_부르는_곳이_사라지지_않았다(self):
        """호출부 0 이면 `renderIntake` 는 살아 있어도 도달 불가다."""
        callers = [p.name for p in sorted(APP.rglob("*.ts"))
                   if p.name != "intake.ts" and "renderIntake(" in p.read_text(encoding="utf-8")]
        self.assertTrue(callers, "renderIntake 를 부르는 파일이 하나도 없다")

    # ── ② 공유 · ③ 소스 스페이스 ────────────────────────────────────────────
    def test_공유_탭이_소스_스페이스를_고르게_한다(self):
        self.assertIn('id="tl-space"', self.share, "소스 스페이스 칸이 없다")
        self.assertIn("teamsApi.list()", self.share,
                      "스페이스 목록의 출처가 계정 라우트가 아니다")

    def test_소스가_고른_스페이스를_따라간다(self):
        """구 Packs 는 `from: {team: null}` 로 개인 하나만 못박아 두었다. 와이어는
        이미 임의의 팀 id 를 실어 보낼 수 있으므로, 널이 남아 있으면 칸만 생기고
        고른 값은 버려진다 — 화면은 아무 말도 하지 않는다."""
        self.assertRegex(self.share, r"from:\s*\{\s*team:\s*src",
                         "팩을 뜨는 스코프가 고른 스페이스를 안 쓴다")
        self.assertNotRegex(self.share, r"from:\s*\{\s*team:\s*null\s*\}",
                            "소스 스페이스가 개인으로 하드코딩돼 있다")

    def test_주제_목록도_고른_스페이스에서_온다(self):
        """하드코딩 널은 **둘**이었다. 팩만 고치고 주제 셀렉트를 두고 오면, 다른
        스페이스를 골라도 고를 수 있는 주제는 여기 것이다."""
        self.assertRegex(self.share, r"api\.domains\([^)]*team:\s*src",
                         "주제 목록이 고른 스페이스를 안 따른다")

    def test_활동_중이_아닌_소속은_지우지_않고_끈다(self):
        """목록에 띄우는 것이 접근을 주지 않는다. 활동 구성원이 아닌 스페이스는
        존재하지 않는 것과 같은 답을 주므로, 조용히 빼면 "안 된다"와 "안 보인다"가
        구별되지 않는다."""
        self.assertRegex(self.share, r'status\s*===\s*"active"',
                         "소속 상태를 안 본다")
        self.assertIn("sourceOff", self.share,
                      "고를 수 없다는 사실을 화면이 말하지 않는다")

    # ── ④ 전체 백업 ────────────────────────────────────────────────────────
    def test_전체_백업이_공유_탭에_있다(self):
        self.assertIn("api.activate(", self.share, "전체 백업 진입점이 없다")

    # ── ⑤ 커뮤니티 게시 ────────────────────────────────────────────────────
    def test_커뮤니티_공유가_공개_맥락_작업으로_넘긴다(self):
        """앱은 팩을 보관해 공개 맥락과 동의를 받는 사이트 작업으로 넘긴다."""
        pub = _body(self.sheets, "async function publishToCommunity")
        # ⚠ 토큰 전수검사로 쓰면 안 된다 — 바로 위 주석이 같은 낱말을 적고 있어서,
        # 분기를 통째로 지워도 초록이었다(변이로 실측). **코드**를 봐야 한다.
        self.assertIn('data.features?.agents !== true', pub)
        self.assertIn('actor: data.draftActor', pub)
        self.assertIn('window.location.assign("/community/share/")', pub)
        self.assertNotIn('community.publish(', pub)
        self.assertIn('downloadPack(out,', pub)
        # 갤러리 시절 게시 클라이언트는 호출부 없이 남아 있었다(VIN-569). 다시 생기지 않게.
        self.assertNotIn('/api/community/publish', (APP / "api.ts").read_text(encoding="utf-8"))

    # ── 표면을 지우면서 조용히 깨지는 것 둘 ────────────────────────────────
    def test_숫자_단축키가_표면_수를_따라간다(self):
        """`"1"`~`"3"` 리터럴이 박혀 있었다. 표면이 둘로 줄면 3 을 눌러 `#undefined`
        로 가는데, 문자열 비교라 타입이 아무 말도 안 하고 게이트도 이 자리를 안 본다.
        표면 수가 다시 바뀌어도 같은 일이 나므로 상한을 `ORDER` 에 맨다."""
        main = (APP / "main.ts").read_text(encoding="utf-8")
        self.assertIn("ORDER.length", main, "단축키 상한이 표면 목록에서 안 온다")
        self.assertNotRegex(main, r'e\.key\s*>=\s*"1"',
                            "숫자 단축키가 아직 리터럴 범위로 잘려 있다")

    def test_옛_주소가_죽지_않는다(self):
        """`#packs`·`#intake` 는 이미 밖에 나가 있는 주소다. 표면만 지우면 링크가
        기본 화면으로 조용히 떨어지고, 그 사람이 찾던 것이 어디 갔는지는 화면 어디에도
        없다. `ALIAS` 가 받고 `ALIAS_TOOLS` 가 그 갈래를 연다."""
        main = (APP / "main.ts").read_text(encoding="utf-8")
        # ⚠ 두 표를 **따로** 잘라야 한다. `ALIAS_TOOLS` 까지 한 덩어리로 보면 별칭을
        # 지워도 뒤 표의 같은 키가 정규식을 만족시켜 초록이었다(변이로 실측).
        alias = main[main.index("const ALIAS:"):main.index("const ALIAS_TOOLS:")]
        tools = main[main.index("const ALIAS_TOOLS:"):main.index("const surfaceEl")]
        for old in ("packs", "intake"):
            with self.subTest(hash=old):
                self.assertRegex(alias, r"\b" + old + r':\s*"\w+"', f"#{old} 를 아무도 안 받는다")
                self.assertRegex(tools, r"\b" + old + r':\s*"(import|share)"',
                                 f"#{old} 가 Tools 를 안 연다")
        # 2026-09-22 scope-back 뒤로 Tools 는 설정 시트의 `정보`(data) 분류다. 라우터는
        # 그 분류를 **별칭이 가리킨 탭으로** 열어야 한다 — 탭을 빼면 `#packs` 가 공유가
        # 아니라 쓰기 탭에 떨어진다.
        route = _body(main, "async function route")
        self.assertRegex(route, r'openSettings\(\s*"data"\s*,\s*ALIAS_TOOLS\[rawHead\]\s*\)',
                         "라우터가 ALIAS_TOOLS 의 탭으로 설정의 정보 분류를 안 연다")
        # 그 탭이 실제로 Tools 까지 닿는가: 시작 탭 → toolsTab → mountTools.
        settings = (APP / "surfaces" / "settings.ts").read_text(encoding="utf-8")
        self.assertRegex(settings, r"let toolsTab: DataTab = dataTab\b",
                         "openSettings 가 받은 탭을 정보 분류가 안 쓴다")
        data = _inner(settings, "  function renderData")
        self.assertRegex(data, r"mountTools\([^;]*toolsTab\s*\)",
                         "정보 분류가 Tools 를 고른 탭으로 안 심는다")

    def test_지운_표면이_트리에_남아_있지_않다(self):
        """반쯤 지우면 죽은 코드가 살아 있는 화면처럼 읽힌다."""
        self.assertFalse((APP / "surfaces" / "packs.ts").exists(), "packs.ts 가 남아 있다")
        hits = [f"{p.name}:{i}" for p in sorted(APP.rglob("*.ts"))
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                if 'location.hash = "#packs"' in line or "renderPacks" in line]
        self.assertEqual([], hits, "#packs 로 보내는 자리가 남아 있다")

    # ── Tools 자체가 도달 가능한가 ─────────────────────────────────────────
    def test_Tools_를_여는_자리가_앱에_남아_있다(self):
        """이 파일의 나머지 전부가 Tools 안을 재는 것이라, Tools 를 여는 손잡이가
        사라지면 전부 초록인 채로 다섯 기능이 함께 도달 불가가 된다."""
        # ⚠ `openTools(` 를 부르는 파일이 있는지로 재면 안 된다 — 숨긴(`hidden`) 상단 바
        # 버튼이 그것을 부르고 있어도 초록이었다. 사람이 누를 수 있는 손잡이에서
        # Tools 까지 사슬을 따라간다: 보이는 레일 설정 버튼 → openSettings → 정보 분류 →
        # mountTools.
        html = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
        tag = re.search(r'<button[^>]*\bid="rail-settings"[^>]*>', html)
        self.assertIsNotNone(tag, "레일 설정 버튼이 셸에 없다")
        self.assertNotRegex(tag.group(0), r"\bhidden\b", "레일 설정 버튼이 숨겨져 있다")

        lnb = (APP / "lnb.ts").read_text(encoding="utf-8")
        self.assertRegex(lnb, r'id\s*===\s*"rail-settings"\)\s*\{[^}]*openSettings\(',
                         "레일 설정 버튼이 설정 시트를 안 연다")

        settings = (APP / "surfaces" / "settings.ts").read_text(encoding="utf-8")
        cats = settings[settings.index("const ALL_CATS"):settings.index("const CATS")]
        data_row = re.search(r'\{\s*id:\s*"data"[^}]*\}', cats)
        self.assertIsNotNone(data_row, "설정에 정보(data) 분류가 없다")
        self.assertNotIn("desktop: true", data_row.group(0),
                         "정보 분류가 데스크탑 전용이다 · 웹에서 Tools 가 사라진다")
        self.assertRegex(_inner(settings, "  function renderBody"),
                         r'active === "data"\) renderData\(',
                         "정보 분류가 자기 본문을 안 그린다")
        self.assertIn("mountTools(", _inner(settings, "  function renderData"),
                      "정보 분류가 Tools 를 안 심는다")


if __name__ == "__main__":
    unittest.main()
