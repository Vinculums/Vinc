# -*- coding: utf-8 -*-
"""공유·백업 동선의 기다림은 **두 벌뿐**이고, 셀 단계가 없는 곳은 **불확정**이다.

두 가지를 지킨다.

1. 기다림 자리가 전부 `sheetProgress` 나 `mountGauges` 를 지난다. `sheetProgress`
   (불확정)는 다섯이다 — 공유 시트 셋(팩 준비 · 파일로 받기 · 커뮤니티 게시)과 Tools
   공유 탭 둘(탭 진입 · 전체 백업). 전에는 자리마다 `<p class="hint">` 한 줄이라, 같은
   동선에서 같은 일을 기다리는데 모양이 자리마다 달랐다.

   **스페이스로 보내기만 `mountGauges`(확정 막대 셋)를 쓴다.** 팩을 조각으로 나눠
   보내게 되면서(`app/src/packchunks.ts`) 셀 단계가 실제로 생겼기 때문이다. 그 퍼센트는
   `api.importPack` 이 조각 하나를 끝낼 때마다 주는 보고에서만 온다 — 아래 2번의
   금지(시계로 지어낸 단계)는 그 자리에도 그대로 걸린다.
   근거: [[decision:space-transfer-sends-size-bounded-chunks-nodes-then-edges-then-documents]]

   ⚠ Tools 의 둘은 P16 이 한 벌을 세울 때 사정거리 밖이었다. 탭 진입은 다른 파일에
   있었고, 전체 백업은 P15 가 P16 **머지 뒤에** Tools 로 들여왔다. 그래서 한 벌을
   세우고도 두 자리가 샜다. 이 파일이 두 파일을 함께 보는 이유가 그것이다.

   ⚠ **여섯은 이 동선의 전부이지 앱의 전부가 아니다.** 가져오기 동선(`openImportSheet`
   하나 · `openDocImportSheet` 둘)에는 `packs.importing` 한 줄이 아직 옛 모양으로
   남아 있고, 그중 첫째는 아래 2번이 근거로 드는 바로 그 `api.importPack` 을 기다린다.
   셋을 한 번에 옮기는 것은 별건이라(하나는 `<p>` 안의 `<span>` 자리다) 여기서는
   관할 밖으로 둔다 — `setUp` 의 슬라이스가 그 경계다.

2. 그 게이지가 **퍼센트를 지어내지 않는다.** 불확정 다섯 자리가 지나는 경로 어디에도
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
   대조하는 것이 이 리포의 관례다(`tests/mcp/test_view_url.py` ·
   `tests/app/test_share_sheet_spec.py` 와 같은 자리).

⚠ `unittest.TestCase` 로 둘 것. CI 의 무조건 잡은 `unittest.discover` 라 모듈 수준
   `def test_*` 를 아예 수집하지 못한다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHEETS = ROOT / "app" / "src" / "sheets.ts"
TOOLS = ROOT / "app" / "src" / "tools.ts"
COMPONENTS = ROOT / "app" / "src" / "components.ts"
#: 진행 막대의 마크업은 로딩 모션 모듈(디자인 허브 사본)이 그린다. `sheetProgress` 는 그것을 부른다.
LOADERS = ROOT / "app" / "src" / "vinc-loaders.js"

#: 기다림을 알리는 카피가 걸리는 모양. `.loading` 이 들어 있는 것은 `common.loading`
#: 뿐 아니라 **수를 세는 척하던** `packs.loading(n)` 도 잡기 위해서다.
BUSY = re.compile(r"\.(loading|preparing|destWorking)\b")

#: 한 벌이 **화면에 붙는** 모양. `sheetProgress` 는 문자열을 돌려줄 뿐이라 부르기만
#: 하고 버리면 아무 일도 일어나지 않는다 — 그런데 위의 검사들은 **부르는 것만** 세므로
#: `void sheetProgress(...); out.innerHTML = "";` 로 바꿔도 통과했다(실측 6 passed).
#: 그래서 호출 수와 이 모양의 수가 **같은지**를 함께 잠근다.
DRAWN = re.compile(r"\.innerHTML\s*=\s*sheetProgress\(")


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


def _progress_markup(components: str) -> str:
    """`sheetProgress` 가 그리는 마크업의 원문. 로딩 모션의 `loaderTrack` 에 맡겼으면
    **개수 없이**(label 하나만) 부르는지 보고 그 함수의 원문을 돌려준다. 개수 없이 부른
    track 은 `aria-valuenow` 를 싣지 않는다(total 이 있을 때만 싣는 분기를 지운 원문)."""
    body = components[components.index("export function sheetProgress"):]
    body = body[: body.index(chr(10) + "}")]
    if "loaderTrack(" not in body:
        return body
    call = body[body.index("loaderTrack("):]
    call = call[: call.index(")") + 1]
    assert call == "loaderTrack(label)", f"sheetProgress 가 개수를 실어 track 을 부른다: {call}"
    src = LOADERS.read_text(encoding="utf-8")
    fn = src[src.index("export function loaderTrack"):]
    fn = fn[: fn.index(chr(10) + "}")]
    return fn.replace('${total ? ` aria-valuenow="${done}"` : ""}', "")


class ShareProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.components = COMPONENTS.read_text(encoding="utf-8")
        self.sheets = SHEETS.read_text(encoding="utf-8")
        self.tools = TOOLS.read_text(encoding="utf-8")
        # 공유 시트 경로. 가져오기 시트는 다른 경로라 관할 밖이다.
        self.share = self.sheets[: self.sheets.index("export function openImportSheet")]
        # Tools 는 공유 탭만. 만들기 탭에도 기다림이 있지만 이 동선이 아니다.
        # renderShare \ub294 tools.ts \uc758 \ub9c8\uc9c0\ub9c9 \ud568\uc218\ub2e4(\ub9cc\ub4e4\uae30 \ud0ed\uc774 \ube60\uc9c0\uba74\uc11c \ub4a4\uc758 \uad6c\ud68d \uc8fc\uc11d\ub3c4 \uc5c6\uc5b4\uc84c\ub2e4).
        self.tl_share = self.tools[self.tools.index("async function renderShare"):]
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
                if not BUSY.search(line) or "sheetProgress(" in line or "mountGauges(" in line:
                    continue
                loose.append(f"{name}:{i}: {line.strip()}")
        self.assertEqual(
            loose, [],
            "공유·백업 동선에 한 벌을 안 지나는 기다림 표시가 남았다:\n" + "\n".join(loose))

    def test_한_벌이_실제로_화면에_붙는다(self):
        """부르는 것과 그리는 것은 다르다 — 이 가드가 실제로 여기서 갈렸다."""
        for name, src in self.regions.items():
            self.assertEqual(
                len(DRAWN.findall(src)), src.count("sheetProgress("),
                f"{name} 에 결과를 화면에 안 붙이는 sheetProgress 호출이 있다 "
                "— 버튼을 눌러도 아무것도 안 뜬다")

    def test_자리가_전부_걸려_있다(self):
        """불확정: 시트 넷(팩 준비 · 목적지 확인 · 파일로 받기 · 커뮤니티) + Tools 셋.
        확정: 스페이스로 보내기 한 자리(목적지 확정 · 내 공간 선택이 함께 쓴다)."""
        self.assertEqual(self.share.count("sheetProgress("), 4,
                         "공유 시트의 불확정 네 자리가 아니다")
        self.assertEqual(self.tl_share.count("sheetProgress("), 3,
                         "Tools 공유 탭의 세 자리(탭 진입 · 미리보기 · 전체 백업)가 아니다")
        code = _code_only(self.share)
        self.assertEqual(code.count("mountGauges(sheet.body"), 1,
                         "스페이스로 보내기의 확정 막대가 한 자리가 아니다")
        self.assertEqual(code.count("sendToSpace(sheet,"), 3,
                         "목적지 확정과 내 공간 신규/병합 확인이 같은 보내기를 쓰지 않는다")

    def test_확정_막대의_수치는_조각_보고에서만_온다(self):
        """확정 막대는 `aria-valuenow` 를 싣는다 — 그 값이 참이려면 **조각을 끝낼 때마다
        오는 보고**가 유일한 출처여야 한다. 타이머 금지는 아래 검사가 함께 본다."""
        code = _code_only(self.share)
        self.assertIn("api.importPack(out, cfg.owner, scope, (p) =>", code,
                      "막대가 importPack 의 조각 보고를 받지 않는다")
        api_src = (ROOT / "app" / "src" / "api.ts").read_text(encoding="utf-8")
        body = api_src[api_src.index("async function importPackInChunks"):]
        body = body[: body.index(chr(10) + "}")]
        self.assertIn("onStep?.(", body, "조각 보고가 없다")
        self.assertIn("for (const c of chunks)", body, "보고가 조각 단위가 아니다")

    def test_막대를_세운_뒤의_기다림에는_끝이_있다(self):
        """불확정 막대는 **끝을 그리지 않는다** — 끝나지 않는 실패를 `살아 있음`으로
        그린다. 공유 탭 진입은 막대를 세운 뒤 요청 둘을 이어 기다리는데, 호출부가
        `void renderShare(...)` 라 여기서 던지면 잡을 곳이 없다(unhandled rejection).
        그래서 둘 다 상한이 있어야 하고, 실패해도 화면은 서야 한다.
        """
        head = _code_only(self.tl_share)
        head = head[: head.index("pane.innerHTML = " + chr(96))]
        awaits = re.findall(r"await" + chr(92) + "s+([A-Za-z_.]+)" + chr(92) + "(", head)
        self.assertEqual(
            [a for a in awaits if a != "withTimeout"], [],
            "막대를 세운 뒤 상한 없는 기다림이 있다 — 끊기면 영원히 돈다: " + repr(awaits))
        self.assertEqual(head.count("catch"), 2,
                         "둘 중 하나가 실패하면 화면이 안 선다(try/catch 둘)")

    def test_전체_백업이_파일을_남긴다(self):
        """`/activate` 는 `out_dir` 없이는 **아무것도 쓰지 않는다**(api/activate.py
        `activate()`). 파일은 화면이 만든다 — 안 만들면 성공만 알리고 남는 것이 없다.
        `res.out` 은 응답에 없는 키다(tests/core/test_activate.py 가 계약을 잡는다)."""
        code = _code_only(self.tl_share)      # 주석이 옛 결함을 인용한다
        tail = code[code.index("api.activate("):]
        self.assertIn(".download =", tail, "전체 백업이 파일을 안 남긴다")
        self.assertNotIn("res.out", code, "응답에 없는 키를 읽고 있다")

    def test_게이지가_퍼센트를_지어내지_않는다(self):
        body = _progress_markup(self.components)
        self.assertIn('role="progressbar"', body, "진행 표시가 progressbar 가 아니다")
        self.assertNotIn("aria-valuenow", body,
                         "단계가 없는데 퍼센트를 실었다 — 진행을 보여주는 척하는 것이다")
        # 원본이 쓰는 가짜 단계 타이머가 따라 들어오지 않았는가.
        for name, src in self.regions.items():
            self.assertFalse(
                re.search(r"setInterval|setTimeout", _code_only(src)),
                f"{name} 에 타이머가 생겼다 — 시계로 단계를 지어내고 있지 않은지 볼 것")

    def test_알림_영역은_미리_서_있는_자리에만_붙는다(self):
        """라이브 영역은 내용이 **바뀌기 전에** DOM 에 있어야 읽힌다.

        `sheetProgress` 의 마크업은 내용과 함께 삽입되므로, 거기에 `role="status"` 를
        달면 스크린리더가 관찰할 대상이 애초에 없다 — 알리는 척만 하는 것이다. 붙일
        상주 자리가 있는 곳은 전체 백업(`#tl-backup-msg`) 하나뿐이고, 나머지는 담는
        자리를 통째로 갈아치운다.

        라벨 중복도 같이 막는다. 보이는 `<p>` 와 `aria-label` 로 두 번 실으면 두 번
        읽힌다 — 가리키는 것(`aria-labelledby`)은 한 번이다.
        """
        body = _progress_markup(self.components)
        self.assertNotIn(
            'role="status"', body,
            "삽입되는 마크업에 라이브 영역을 달았다 — 관찰 대상이 없어 알리지 못한다")
        self.assertIn("aria-labelledby", body, "진행 막대에 이름이 없다")
        self.assertNotIn(
            "aria-label=", body,
            "보이는 문구와 aria-label 로 라벨을 두 번 실었다 — 두 번 읽힌다")
        # 이름은 **호출마다 새로** 만든다. `openSheet` 의 close 는 퇴장 애니메이션을
        # 기다린 뒤 지우므로 닫히는 시트가 아직 DOM 에 있고, 그때 다음 막대가 서면
        # 고정 id 는 겹친다 — `aria-labelledby` 가 사라지는 시트의 라벨을 가리킨다.
        self.assertNotIn(
            'id="sheet-progress-label"', body,
            "id 가 고정이다 — 두 벌이 겹쳐 서면 라벨이 엇갈린다")
        self.assertRegex(
            body, r"const id = .*\$\{\+\+",
            "이름이 호출마다 달라지지 않는다 — 올라가는 수에서 와야 한다")
        self.assertNotIn('id="pub-progress"', self.share,
                         "앱은 공개 작업으로 넘기며 게시 진행 상태를 선언하지 않는다")
        self.assertIn(
            '<div id="tl-backup-msg" role="status">', self.tl_share,
            "전체 백업의 상주 자리에 라이브 영역이 없다")

    def test_불확정_막대가_토큰과_모션_설정을_지킨다(self):
        """막대는 로딩 모션의 track 이다(scripts/design/assets/motion 사본). 색은 토큰에서만 오고,
        모션을 줄인 환경에서는 왕복을 멈춘다. 채움은 --foreground, 트랙은 --border 라 두 모드
        모두 비텍스트 대비 3:1 을 넉넉히 넘는다(예전 --primary/--muted 막대는 다크에서 1.87:1
        이라 --ring 분기가 따로 있었다)."""
        css = (ROOT / "app" / "src" / "vinc-loaders.css").read_text(encoding="utf-8")
        app = (ROOT / "app" / "src" / "app.css").read_text(encoding="utf-8")
        self.assertIn('@import "./vinc-loaders.css";', app, "앱이 로딩 모션 스타일을 읽지 않는다")
        self.assertIn("hsl(var(--foreground", css, "채움이 토큰에서 오지 않는다")
        self.assertIn("hsl(var(--border", css, "트랙이 토큰에서 오지 않는다")
        self.assertIn(".vl-track.vl-indef .head", css, "개수를 모를 때의 움직임이 없다")
        reduce = css[css.index("@media (prefers-reduced-motion: reduce)"):]
        self.assertIn("animation: none", reduce,
                      "모션을 줄인 환경에서 무한 애니메이션이 그대로 돈다")


if __name__ == "__main__":
    unittest.main()
