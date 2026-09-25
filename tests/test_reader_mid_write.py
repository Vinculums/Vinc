# -*- coding: utf-8 -*-
"""쓰기 창에 걸린 문서를 앱의 두 리더가 어떻게 말하는가(P17b).

⚠ **먼저 지분을 바로 적는다.** 편집기를 세우지 않는 보호는 이 패키지가 만든 것이
아니다. 두 리더 모두 `!doc.executed || doc.text === undefined` 에서 이른 반환을 하고
있었고(view.ts 는 실패 문구 뒤 `return`, doc-editor.ts 는 무동작 stub 반환), 그것은
P17b 이전 main 에 이미 있었다:

    git show <P17b 이전>:app/src/surfaces/view.ts   # readerFail 뒤 return
    git show <P17b 이전>:app/src/doc-editor.ts      # 같은 조건에서 stub 반환

그러니 P17(#241)이 `doc_get` 을 이 상태에서 `executed:false` 로 돌린 순간, 손실 경로는
그 선행 가드로 이미 닫혔다. **P17b 가 더한 것은 둘뿐이다:**

  ① 그 상태와 "정말 못 열었다"가 **화면에서 다른 문구로 갈린다**  ← 이 패키지의 본체
  ② 재시도는 **사람이 누를 때만** 일어난다(크래시 잔해면 자동 루프는 영원히 돈다)

아래 `EarlyReturnIsInheritedTests` 는 그래서 **특성화 테스트**다 — 물려받은 불변식이
이 패키지의 편집 때문에 깨지지 않았는지만 잰다. 성과가 아니라 안전망이다.

판별 열은 `chunk_count` 다. 서버가 그 열을 안 실으면 위 둘이 조용히 전부 죽으므로 그
계약도 함께 잰다. 서버 쪽 거동 자체는 `test_docstore.py` 가 실제로 돌려서 잰다.

앱 쪽에는 JS 테스트 러너가 없다(`app/package.json` 에 `test` 없음) — `test_lnb_tint.py`
와 같은 자리에 두는 이유가 그것이다. 그래서 여기 단언은 전부 소스 대조인데, 이 릴리스가
세 번 물린 함정이 바로 그것이다: **'함수를 부르는가'만 잠그고 '화면에 나오는가'는 안
잠그는 것.** 그래서 `RenderedCopyTests` 는 표식이 아니라 **렌더 문장 자체**를 본다 —
`innerHTML` 대입 안에 그 문구가 없으면 빨개진다."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app" / "src"
API = (SRC / "api.ts").read_text(encoding="utf-8")
ED = (SRC / "doc-editor.ts").read_text(encoding="utf-8")
VIEW = (SRC / "surfaces" / "view.ts").read_text(encoding="utf-8")
COPY = (SRC / "copy.ts").read_text(encoding="utf-8")
DOCSTORE = (ROOT / "api" / "docstore.py").read_text(encoding="utf-8")


def at(src: str, needle: str) -> int:
    """`str.index` 의 ValueError 대신 무엇이 없어졌는지 말하는 실패를 낸다."""
    i = src.find(needle)
    assert i >= 0, f"소스에서 사라졌다: {needle}"
    return i


def branch(src: str, mount: str) -> str:
    """쓰기 창 분기 전체 — 판별 호출부터 **편집기 마운트 직전까지**.

    ⚠ 범위가 곧 이 파일의 급소다. 처음에는 재시도 버튼 id 뒤 600자만 훑었는데, 그러면
    자동 재시도 타이머를 버튼 **앞**에 넣어도 통과했다(= 막으려던 그 루프가 그대로
    들어온다). 마운트 앵커까지 통째로 잡으면 분기의 모든 줄이 범위 안에 들어온다.
    이 구간은 분기의 진짜 상위집합이라, 지나쳐서 틀릴지언정 놓쳐서 틀리지 않는다."""
    return src[at(src, "docMidWrite(doc)"):at(src, mount)]


ED_BRANCH = branch(ED, 'host.classList.add("doc-render")')
VIEW_BRANCH = branch(VIEW, "await mountDocEditor(")
# 두 리더를 같은 잣대로 잰다 — (이름, 분기 소스, 렌더 싱크, 재시도 버튼 id)
READERS = (("doc-editor", ED_BRANCH, "host.innerHTML", "ed-retry"),
           ("view", VIEW_BRANCH, "detail.innerHTML", "rdr-retry"))


def render_stmt(region: str, sink: str) -> str:
    """`<sink>.innerHTML = ...;` 대입 **한 문장**. 화면에 실제로 나가는 바로 그 문자열."""
    m = re.search(re.escape(sink) + r" = [^;]*", region)
    assert m, f"{sink} 대입이 분기에서 사라졌다 — 이 상태가 화면에 아무것도 안 그린다"
    return m.group(0)


class MidWriteDiscriminantTests(unittest.TestCase):
    def test_서버가_판별_열을_계속_싣는다(self):
        """`chunk_count` 는 이 갈래에만 실리는 표식이고, 앱의 분기 전체가 그것에 달렸다.
        서버가 이 열을 떼면 앱은 조용히 옛 문구로 돌아간다(타입체크도 게이트도 안 운다)."""
        self.assertIn('base["chunks"], base["chunk_count"] = len(rows), expected', DOCSTORE)

    def test_판별은_error_가_아니라_청크_수로_한다(self):
        """`error` 는 모르는 id·소유자 불일치에도 실린다 — 그것으로 가르면 오타 하나가
        "쓰는 중"으로 보인다."""
        m = re.search(r"export const docMidWrite = .*?;\n", API, re.S)
        self.assertIsNotNone(m, "api.ts 가 판별기를 안 내보낸다")
        body = m.group(0)
        self.assertIn("d.executed === false", body)
        self.assertIn("d.chunk_count !== undefined", body)
        self.assertNotIn("error", body)

    def test_일시와_영구를_못_가른다는_사실이_적혀_있다(self):
        """크래시가 남긴 무청크 상태는 며칠 간다. "잠시 뒤"만 말하면 그때 거짓말이 되고,
        응답에는 둘을 가를 열이 없다. 그 이유를 코드가 들고 있어야 다음 사람이 이
        문구를 "쓸데없이 길다"며 반으로 줄이지 않는다."""
        head = API[:API.index("export const docMidWrite")]
        self.assertIn("못 가른다", head[-1200:])


class RenderedCopyTests(unittest.TestCase):
    """① 이 패키지의 본체 — **화면에 나오는가**.

    ⚠ 여기가 이 릴리스에서 세 번 반복된 함정의 자리다(P4·P11b·P16b·P17b). 아래 단언은
    전부 렌더 문장 자체를 읽는다: `${busy ? …readerMidWrite : …readerFail}` 를 그냥
    `${…readerFail}` 로 되돌리면(= 이 패키지가 고친 그 결함으로 정확히 회귀) 빨개진다.
    표식(`docMidWrite` 호출·copy.ts 의 키)만 보면 그 회귀를 통과시킨다."""

    def test_두_리더_모두_그_문구를_실제로_그린다(self):
        for name, region, sink, _ in READERS:
            with self.subTest(name):
                stmt = render_stmt(region, sink)
                self.assertIn("COPY.ui.viewx.readerMidWrite", stmt,
                              f"{sink} 대입이 이 상태에서 readerMidWrite 를 안 쓴다 "
                              "— 화면에서 '못 열었다'와 안 갈린다")

    def test_그_문구가_판별에_걸려_있다(self):
        """항상 그리면 진짜 실패까지 "쓰는 중"이 된다 — 갈래여야 한다."""
        for name, region, sink, _ in READERS:
            with self.subTest(name):
                stmt = render_stmt(region, sink)
                self.assertRegex(stmt, r"busy \?[^:]*COPY\.ui\.viewx\.readerMidWrite")
                self.assertIn("COPY.ui.viewx.readerFail", stmt)

    def test_재시도_버튼도_실제로_그려진다(self):
        """버튼을 배선만 하고 안 그리면 `querySelector` 가 null 이라 조용히 무동작이다."""
        for name, region, sink, btn in READERS:
            with self.subTest(name):
                stmt = render_stmt(region, sink)
                self.assertIn(f'id="{btn}"', stmt, "버튼이 렌더 문장 밖이다")
                self.assertIn("COPY.ui.viewx.readerRetry", stmt)


class RetryIsManualTests(unittest.TestCase):
    """② 자동 재시도 금지 — 크래시 잔해에서는 영원히 돈다."""

    def test_두_리더_모두_사람의_클릭에만_다시_읽는다(self):
        for name, region, _, btn in READERS:
            with self.subTest(name):
                self.assertIn(f'querySelector("#{btn}")', region)
                self.assertIn('addEventListener("click"', region)

    def test_분기_어디에도_타이머가_없다(self):
        """부정 단언의 범위가 분기 전체여야 한다 — `branch()` 독스트링 참조."""
        for name, region, _, _ in READERS:
            with self.subTest(name):
                self.assertNotRegex(region, r"set(?:Timeout|Interval)")


class RailHasAWayOutTests(unittest.TestCase):
    """마우스만 쓰는 사람에게 나가는 문. 레일의 닫기는 `detail.innerHTML` 안에만 있어서,
    통째로 갈아 끼우는 분기는 그것을 함께 지운다(그 상태에서 나가는 길은 Esc 키뿐).

    ⚠ 이것은 **선행 결함**이다 — `readerLoading`·`readerFail`·`drillFail`·`periodFail`
    넷이 P17b 이전부터 같은 모양이다. 그 넷은 이 패키지 밖이라 안 고쳤다. 쓰기 창만
    고치는 이유는 머무는 시간이다: 크래시 잔해는 다음 re-put 까지 며칠 간다."""

    def test_뷰_리더의_쓰기_창에는_닫기가_있다(self):
        """머리는 공용 함수 한 벌이다(`railHeadHTML` · `wireRailHead`, 2026-09-25) —
        상세·리더·리더 실패 셋이 같은 머리를 그린다. 그래서 분기에는 그 호출이 있고,
        닫기 단추와 그 배선은 함수 쪽에 있어야 한다."""
        stmt = render_stmt(VIEW_BRANCH, "detail.innerHTML")
        self.assertIn("railHeadHTML(", stmt, "레일 머리가 없다 — 나가는 길이 Esc 뿐이다")
        self.assertIn("wireRailHead()", VIEW_BRANCH)
        head = VIEW[at(VIEW, "function railHeadHTML("):at(VIEW, "function railChrome(")]
        self.assertIn('id="rail-close"', head)
        self.assertRegex(head, r'querySelector\("#rail-close"\)\?\.addEventListener\("click", closeRail\)')


class EarlyReturnIsInheritedTests(unittest.TestCase):
    """물려받은 불변식의 **특성화 테스트** — 이 패키지의 성과가 아니다(모듈 독스트링).

    편집기를 세우지 않는 보호는 P17b 이전 main 에 이미 있었다. 여기서 재는 것은 그
    보호가 이 패키지의 편집 때문에 깨지지 않았는지뿐이다."""

    def test_편집기_리더는_마운트_앞에서_돌아간다(self):
        branch = at(ED, "docMidWrite(doc)")
        mount = at(ED, 'host.classList.add("doc-render")')
        self.assertLess(branch, mount, "분기가 편집기 마운트 뒤에 있으면 소용이 없다")
        self.assertRegex(ED[branch:mount], r"\n\s*return (stub|\{)",
                         "분기와 마운트 사이에 반환이 없다 — 편집기가 그대로 선다")

    def test_뷰_리더도_마운트_앞에서_돌아간다(self):
        self.assertRegex(VIEW_BRANCH, r"\n\s*return;")

    def test_돌아간_뒤_넘기는_손잡이는_재시도가_세운_편집기에_위임한다(self):
        """이 stub 은 편집기를 세우지 않는다. 그러니 재시도가 성공한 뒤에만
        (=`again` 이 채워진 뒤에만) 손잡이의 동작이 실체를 갖는다 — 위임이 끊기면
        시트를 닫아도 디바운스 타이머와 CM6 인스턴스가 남는다.

        ⚠ 여기는 `setEditable` 로 걸던 자리다. 그 토글은 호출부가 0이라 P18 에서
        걷혔고(레일은 상시 편집이다), 위임 계약은 남은 두 손잡이가 그대로 진다."""
        stub = ED[at(ED, "docMidWrite(doc)"):at(ED, 'host.classList.add("doc-render")')]
        self.assertIn("await again?.dispose()", stub)
        self.assertIn("again?.scrollToOffset(pos)", stub)
        self.assertIn("let again: DocEditorHandle | null = null;", stub)

    def test_왕복_중에_닫으면_도착하는_편집기를_버린다(self):
        """`dispose()` 시점에 `again` 은 아직 null 이라 위임할 대상이 없다. 그대로 두면
        왕복이 끝난 뒤 **이미 떨어져 나간 host** 에 편집기가 서고, 디바운스 타이머와 CM6
        인스턴스가 아무도 안 잡은 채 남는다."""
        self.assertIn("closed = true", ED_BRANCH, "dispose 가 닫힌 사실을 기억하지 않는다")
        self.assertRegex(ED_BRANCH, r"closed \? h\.dispose\(\)",
                         "왕복 중 닫혔는데 도착한 핸들을 버리지 않는다")


class CopyTests(unittest.TestCase):
    """문구 자체의 계약. 화면에 나오는가는 `RenderedCopyTests` 가 잰다."""

    def test_두_덱_모두_새_문구를_갖는다(self):
        for key in ("readerMidWrite", "readerRetry"):
            with self.subTest(key):
                self.assertEqual(COPY.count(f"{key}: "), 2, "EN·KO 한 벌씩이어야 한다")

    def test_빈_문서_문구와_다른_말을_한다(self):
        """같은 문자열을 재사용하면 화면에서 안 갈린다 — 이 패키지가 고친 것이 그것이다."""
        mid = re.findall(r'readerMidWrite: "([^"]+)"', COPY)
        fail = re.findall(r'readerFail: "([^"]+)"', COPY)
        self.assertEqual((len(mid), len(fail)), (2, 2))
        self.assertEqual(set(mid) & set(fail), set())

    def test_여기서_못_하는_처방을_적지_않는다(self):
        """이 화면은 **편집기를 세우지 않는 것이 존재 이유**다. 그런데 문구는 한동안
        "전체 내용을 다시 저장하면 돌아와요 / save the full text again"으로 끝났다 —
        읽는 사람이 그 자리에서 실행할 수단이 없는 처방이다. 끝은 진단이어야 한다."""
        for s in re.findall(r'readerMidWrite: "([^"]+)"', COPY):
            self.assertNotIn("다시 저장", s)
            self.assertNotRegex(s, r"(?i)save the full text")

    def test_화면_문구에_em_dash_가_없다(self):
        for s in re.findall(r'reader(?:MidWrite|Retry): "([^"]+)"', COPY):
            self.assertNotIn("—", s)


if __name__ == "__main__":
    unittest.main()
