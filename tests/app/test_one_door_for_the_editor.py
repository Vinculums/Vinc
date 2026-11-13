# -*- coding: utf-8 -*-
"""편집의 문은 한 벌이다 — 오염이 들어오는 문 · 대기 중 편집이 나가는 문.

2026-09-07 하네스 실측 셋을 못 박는다:

  ① 편집기에 포커스가 있는데 `1`·`2`·`f`·`?` 가 앱을 이동시켰다. 가드가 두 벌인데
     한 벌만 `contenteditable` 을 보고 있었고, CM6 의 편집면이 바로 그것이다.
  ② 마운트 지점을 `innerHTML = ""` 로 비우는 곳들이 핸들에 닿지 않아, 2초 디바운스가
     **화면 없는 곳에서** 터졌다(저장 문구가 나갈 노드도 함께 떨어져 나갔다).
  ③ 그래서 같은 레일의 출구 넷이 결과 셋을 냈다 — `뒤로`·`전체폭` 은 버리고
     `✕`·표면이동은 몰래 저장했다.

⚠ 이 파일이 닿는 범위: 앱에는 JS 테스트 러너가 없다(`app/package.json` 에 `test` 가
없다). **①의 판정 자체는 여기서 재지 않는다** — DOM 을 안 쓰게 떼어 낸
`app/src/keys.ts` 를 `app/dev/keys-check.ts` 가 실제로 돌리고 CI 가 그것을 돌린다.
여기서 잠그는 것은 그 판정이 **쓰이는 자리**와, DOM 없이는 못 도는 ②·③의 순서다.
순서가 곧 동작인 자리들이라(확정보다 먼저 비우면 잃는다) 존재가 아니라 **차례**를 본다.
"""
from __future__ import annotations

import re
import unittest

from _src import ROOT, SRC, braced, read

MAIN = read("main")
VIEW = read("view")
EDITOR = read("doc-editor")
KEYS = read("keys")


class OneGuardTest(unittest.TestCase):
    """① 오염이 들어오는 문 — 판정은 한 곳에서만 산다."""

    def test_전역_단축키_핸들러가_공용_판정을_쓴다(self):
        """이걸 안 쓰면 `1`·`2` 가 본문에 글자를 넣으면서 표면을 갈아엎는다.

        집는 자리는 **표면을 옮기는 줄 바로 앞의 keydown 리스너**다. 예전에는
        `const inField` 로 집었는데 그 이름이 사라지면서 테스트만 멈췄다 — 이름이
        아니라 이 파일이 지키려는 동작(표면 이동)에 걸어 둔다."""
        head = MAIN.rindex('document.addEventListener("keydown"', 0,
                           MAIN.index("location.hash = `#${ORDER"))
        guard = braced(MAIN[head:], 'document.addEventListener("keydown"', inner=True)
        self.assertIn('shortcutsRest("surface", e.target', guard)
        # 그 판정이 실제로 표면 이동을 막는 자리 앞에 있어야 한다.
        self.assertLess(guard.index("shortcutsRest(\"surface\""),
                        guard.index("location.hash = `#${ORDER"))

    def test_판정의_구현은_리포에_한_벌뿐이다(self):
        """이 결함의 형태가 "같은 계약을 두 곳이 각자 구현했고 벌어졌다"였다.

        두 번째 사본이 생기는 순간 다시 갈리므로, 사본이 없다는 것 자체를 잠근다."""
        copies = sorted(
            p.relative_to(ROOT).as_posix()
            for p in SRC.rglob("*.ts")
            if re.search(r'tagName\s*===\s*"(INPUT|TEXTAREA)"', p.read_text(encoding="utf-8"))
        )
        self.assertEqual(copies, ["app/src/keys.ts"], f"판정 사본이 늘었다: {copies}")

    def test_공용_판정이_편집면을_본다(self):
        """CM6 의 편집면은 `div[contenteditable]` 이다 — tagName 으로는 안 잡힌다."""
        self.assertIn("isContentEditable", KEYS)

    def test_편집_중에도_Esc_는_레일을_닫는다(self):
        """가드가 옳게 비켜서는 바람에 나오는 문이 ✕ 하나로 줄었던 자리.

        이제 어느 문으로 나가도 확정되므로(아래 ExitsTest) 이 문을 열어 둘 수 있다."""
        # 가드가 두 줄로 접혀 있어 공백을 눌러 놓고 본다 — 잠그는 것은 줄바꿈이 아니라
        # "Esc 는 예외"라는 계약이다.
        onkey = re.sub(r"\s+", " ", braced(VIEW, "const onKey = (e: KeyboardEvent)", inner=True))
        self.assertIn('shortcutsRest("surface", e.target, document.activeElement,'
                      ' docEditorOpen()) && e.key !== "Escape"', onkey)
        self.assertIn("closeRail();", onkey)


class TeardownReachesTheHandleTest(unittest.TestCase):
    """② 마운트 지점을 비우는 곳은 전부 핸들에 닿는다 — **비우기 전에.**"""

    def test_라우터가_문서_열을_비우기_전에_확정한다(self):
        route = braced(MAIN, "async function route()", inner=True)
        self.assertIn("disposeEditorsIn(docpaneBodyEl)", route)
        self.assertLess(route.index("disposeEditorsIn(docpaneBodyEl)"),
                        route.index('docpaneBodyEl.innerHTML = ""'),
                        "비운 뒤에 부르면 확정할 편집기를 이미 못 찾는다")

    def test_레일을_닫을_때_비우기_전에_확정한다(self):
        close = braced(VIEW, "function closeRail()", inner=True)
        self.assertIn("disposeEditorsIn(detail)", close)
        self.assertLess(close.index("disposeEditorsIn(detail)"),
                        close.index('detail.innerHTML = ""'))

    def test_레일을_다시_열_때도_확정한다(self):
        """`detail.innerHTML` 대입은 스무 곳이 넘고 전부 openRail 을 먼저 지난다.

        그래서 문 앞에서 한 번 걷는다 — 호출부마다 적으면 다음에 느는 하나가 샌다."""
        self.assertIn("disposeEditorsIn(detail)", braced(VIEW, "function openRail()", inner=True))
        # 실제로 모든 대입이 그 문 뒤에 있는지 본다 — 앞에 있어도 되는 것은 스스로
        # 확정하는 closeRail 하나뿐이다(위 검사가 그 순서를 이미 잠갔다).
        # 문은 레일 모듈(`createRail`)에 있고 표면은 레일을 만든 줄부터 그 문을 쥔다:
        # 표면의 대입은 그 줄 뒤에, 레일 모듈 안의 대입은 문 뒤에 있어야 한다.
        held = VIEW.index("= createRail(")
        rail_from = VIEW.index("export function createRail(")
        gate = VIEW.index("function openRail()")
        close_body = braced(VIEW, "function closeRail()", inner=True)
        close_from = VIEW.index(close_body)
        for m in re.finditer(r"^\s+detail\.innerHTML\s*=", VIEW, re.M):
            if close_from <= m.start() < close_from + len(close_body):
                continue
            self.assertGreater(m.start(), gate if m.start() > rail_from else held,
                               "openRail 보다 앞에서 레일 내용을 갈아치우는 자리가 생겼다")

    def test_전체폭_승격은_확정을_기다린다(self):
        """시트가 곧바로 같은 문서를 서버에서 다시 읽는다 — 안 기다리면 방금 친
        글자가 없는 본문이 뜨고, 그 뒤 편집은 해시 충돌로 튄다."""
        self.assertIn("await handle.dispose();", VIEW)
        self.assertLess(VIEW.index("await handle.dispose();"),
                        VIEW.index("await openDocSheet({ owner: cfg.owner, docId })"))


class ExitsTest(unittest.TestCase):
    """③ 문 넷이 같은 답을 낸다 — 나가면서 확정한다."""

    # doc-editor 에는 dispose 가 둘이다(실패 stub · 진짜 편집기). 확정하는 쪽은
    # 뒤엣것이므로 그 블록만 집는다 — stub 을 보면 전부 통과해 버린다.
    DISPOSE = braced(EDITOR[EDITOR.index("const handle: DocEditorHandle = {"):],
                     "dispose: async (discard = false) =>", inner=True)

    def test_dispose_가_대기_중_편집을_버리지_않고_저장한다(self):
        """여태 `clearTimeout` 만 했다. 그게 `뒤로`·`전체폭` 이 마지막 2초를
        소리 없이 잃던 이유다."""
        dispose = self.DISPOSE
        self.assertIn("window.clearTimeout(timer)", dispose)
        self.assertIn("await save()", dispose)
        self.assertLess(dispose.index("window.clearTimeout(timer)"), dispose.index("await save()"))

    def test_확정이_편집기_해제보다_먼저다(self):
        """`save()` 는 첫 줄에서 글자를 동기적으로 집는다 — 순서를 뒤집으면
        확정할 본문이 이미 없다."""
        dispose = self.DISPOSE
        self.assertLess(dispose.index("await save()"), dispose.index("cm?.destroy()"))

    def test_두_번_불러도_저장이_두_번_안_나간다(self):
        """레일의 출구는 겹쳐 눌린다(전체폭 → closeRail). 타이머를 비워 두지 않으면
        같은 본문이 두 번 올라간다."""
        self.assertIn("timer = undefined", self.DISPOSE)

    def test_사문이던_편집_토글은_사라졌다(self):
        """호출부가 0이었는데 옆 주석은 살아 있는 보호로 읽혔다 — 그게 ③의 절반이다.

        되살릴 자리가 없으므로(레일은 상시 편집) 지운다. 다시 생기면 여기가 빨개진다."""
        for p in SRC.rglob("*.ts"):
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("setEditable", text, p.name)
            self.assertNotIn("isEditable", text, p.name)


if __name__ == "__main__":
    unittest.main()
