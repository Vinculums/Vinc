# -*- coding: utf-8 -*-
"""그래프 표면의 innerHTML 이스케이프 게이트 (감사 2026-08-18 M9).

노드 제목·라벨·도메인·태그는 **공격자가 정할 수 있다** — 커뮤니티 팩과 가져온 팩이
그대로 들어오는 값이다. 그것이 `esc()` 없이 `innerHTML` 템플릿에 들어가면 HTML 주입이
되고, 실제로 다섯 자리가 그렇게 열려 있었다(그중 하나는 `data-tip="${...}"` 속성 탈출).

오늘 그것이 코드 실행이 아닌 이유는 CSP 에 `script-src` 가 없고 `withGlobalTauri:false`
이기 때문이다. **즉 그 두 설정이 하중을 받고 있다.** 하중을 받는 것은 게이트로 고정한다.

범위를 `view*.ts` 로 좁힌 것은 의도다: 거기가 그래프 값이 흐르는 표면이고, 넓히면
정적 COPY 라벨(탭·명령·모드)까지 걸려 거짓 양성이 진짜를 덮는다. 무시당하는 게이트는
게이트가 아니다.
  근거: [[decision:escape-once-at-the-boundary]]
"""
from __future__ import annotations

import re
import sys

try:
    from scripts._root import ROOT
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT
SURFACES = sorted((ROOT / "app" / "src" / "surfaces").glob("view*.ts"))

#: 그래프에서 온 값. `.tags`·`.title` 처럼 **필드 접근**만 본다(지역 변수명은 안 본다).
#  \w* 는 접두사를 흡수한다 — 실제로 놓쳤던 것이 s.shared_tags 였다.
FIELD = re.compile(r"\.\s*\w*(label|title|domain|tags|one_liner)\b")
#: 이 조각 안에 이스케이프가 있는가. `underlineTerms` 처럼 내부에서 esc 하는 것도 통과.
#:
#: `renderMarkdown` 은 2026-09-11 에 들어왔다. 노드 상세가 한 줄 요약을 마크다운으로
#: 그리기 시작하면서인데, `esc()` 로 감싸면 마크다운이 글자로 보이므로 감쌀 수가 없다.
#: 목록에 넣기 전에 그 함수를 읽어 확인한 것: **본문은 esc 를 먼저 통과하고**(함수 첫
#: 줄의 지역 `esc`), 태그를 만드는 보간이 전부 `esc(...)` 나 `inline(esc(...))` 를 지나며,
#: 속성 자리 둘(`data-src`)도 `esc()` 다. 즉 이스케이프하는 함수라서 넣는 것이지
#: 마크다운이라서 넣는 것이 아니다.
#: ⚠ 그 신뢰는 `tests/app/test_render_markdown_escapes.py` 가 붙잡는다. 저 함수가 이스케이프를
#: 그만두면 이 줄이 구멍이 되므로, 목록에서 빼기 전에 그 시험부터 볼 것.
SAFE = re.compile(r"\b(esc|escHtml|underlineTerms|textContent|encodeURI|renderMarkdown)\s*\(")
#: `${ ... }` 한 조각. 중첩 한 겹까지 받아 삼항 안의 템플릿도 통째로 본다.
INTERP = re.compile(r"\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")
#: 템플릿 리터럴이 닫히는 줄(대략적이지만 이 파일들에선 충분하다).
CLOSES = re.compile(r"`\s*(\)\s*)?;\s*$")


def scan_lines(label: str, lines: list[str]) -> list[str]:
    """한 파일치를 훑는다. 파일 읽기와 분리해 둔 것은 **테스트가 고치기 전 코드를 먹여**
    이 게이트가 진짜로 그 버그를 잡는지 증명할 수 있게 하려는 것이다."""
    bad: list[str] = []
    live = False
    for n, line in enumerate(lines, 1):
        if "innerHTML" in line:
            live = True
        if live:
            for m in INTERP.finditer(line):
                seg = m.group(0)
                if FIELD.search(seg) and not SAFE.search(seg):
                    bad.append("%s:%d  %s" % (label, n, seg[:70]))
        if live and CLOSES.search(line):
            live = False
    return bad


def scan() -> list[str]:
    bad: list[str] = []
    for path in SURFACES:
        bad += scan_lines(path.relative_to(ROOT).as_posix(),
                          path.read_text(encoding="utf-8").split("\n"))
    return bad


def main() -> int:
    bad = scan()
    if bad:
        print("[gate_escape] BLOCK — 이스케이프 없는 그래프 값 %d건" % len(bad))
        for b in bad:
            print("  " + b)
        print("\n  고치는 법: `esc()` 로 감싼다(components.ts 에서 import). 속성값에도 쓸 수 있다.")
        return 1
    print("[gate_escape] PASS — 그래프 표면 %d개, 이스케이프 누락 0건" % len(SURFACES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
