# -*- coding: utf-8 -*-
"""figma_app_copy.py — 데스크탑 보드가 **앱이 하지 않는 말**을 하고 있는가.

세 게이트가 이미 목록(`figma_inventory`)·토큰 값(`figma_diff`)·사이트 문구(`figma_copy`)를
본다. 앱 보드의 글자만 아무도 안 봤고, 그 자리에서 실제로 물렸다: 2026-09-06 에 오버레이
넷 중 셋이 **다른 시트의 본문**(팩 공유 표와 Cancel/Share 푸터)을 달고 있었다. 화면 개수도
맞고 상태도 선언돼 있어서 어느 게이트도 빨개지지 않았다.

## 무엇이 실패인가

보드의 문자열 하나하나를 `app/src` 의 문자열 리터럴 전량과 대조한다. 셋 중 하나여야 한다:

1. 코드가 실제로 그 말을 한다 — 통과.
2. `ALLOW` 에 **이유와 함께 선언된** 것 — 통과하되 매 실행 분류별로 세어 보여 준다.
3. 둘 다 아니다 — **FAIL**. 앱에 없는 말이 화면에 서 있다는 뜻이다.

## 왜 허용목록이 필요한가

보드는 앱만 그리지 않는다. 표본 데이터("Gauge symmetry" · "field-notes.md"), 코드가
템플릿으로 조립하는 문장("2 / 4 processed · on your device only"), 상태를 가리키는 보드
주석("FIRST RUN"), 글리프와 키캡("✕" · "Ctrl K"), 웹 스킨이 얹는 사이트 GNB. 이것들은
코드 리터럴이 아니고, 아닌 것이 맞다. 그래서 지우지 않고 **분류해서 선언**한다 —
`ICON_GAPS` 와 같은 관례다. 선언되지 않은 새 문자열이 생기면 그때 빨개진다.

## 급소

- 대문자는 코드가 아니라 CSS 가 만든다(`text-transform: uppercase`). Figma 에 대문자를
  박아 두면 여기서 영원히 안 맞는다. 저장은 코드 형태로 하고 `textCase` 로 올릴 것 —
  2026-09-06 에 열넷을 그렇게 옮겼고, 그러자 가려져 있던 진짜 드리프트 둘이 드러났다
  (보드는 "DOMAINS TO SYNC", 코드는 "Topics to sync").
- 덤프는 지문만 싣는다. 글자를 싣으면 리포에 앱 카피의 두 번째 사본이 생긴다.

    python -X utf8 scripts/figma_app_copy.py          # 보고서, FAIL 이면 exit 1
    python -X utf8 scripts/figma_app_copy.py --list   # 선언 후보를 분류 없이 나열
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HUB = pathlib.Path(__file__).resolve().parent.parent
APP = HUB.parent / "app" / "src"
DUMP = HUB / "imports" / "figma-app-copy.json"

#: 코드에 없어도 되는 것들. 값은 **왜** 인지의 분류다.
#:   sample  표본 데이터(그림에 채워 넣은 예시 문서·개념·파일 이름·수치)
#:   interp  코드가 값을 끼워 조립하는 문장. 리터럴로는 없다
#:   board   보드 주석. 앱의 말이 아니라 이 시안이 무엇을 그린 것인지 적은 라벨
#:   glyph   글리프·키캡·구분자
#:   site    웹 스킨이 얹는 사이트 GNB. 정본은 Vinc-homepage
#:   doc     표본 문서 본문(마크다운 조각 포함)
ALLOW: dict[int, str] = {
    # ── 크롬·글리프
    666474: "glyph", 8211: "glyph", 9634: "glyph", 10005: "glyph", 9662: "glyph",
    8594: "glyph", 8592: "glyph", 183: "glyph", 286144: "glyph", 8776: "glyph", 8801: "glyph",
    63: "glyph", 102: "glyph", 69973: "glyph", 817058: "glyph", 968859: "glyph",
    313757: "glyph", 48563: "glyph",
    # ── 표본 데이터
    50: "sample", 52: "sample", 54: "sample", 56: "sample", 1569: "sample", 1602: "sample",
    1723: "sample", 1757: "sample", 54398: "sample", 862469: "sample", 29278: "sample",
    771197: "sample", 683781: "sample", 136842: "sample", 333825: "sample", 698628: "sample",
    991549: "sample", 650956: "sample", 670481: "sample", 616902: "sample", 906304: "sample",
    579293: "sample", 572222: "sample", 745329: "sample", 285964: "sample", 655627: "sample",
    941597: "sample", 344277: "sample", 859719: "sample", 770445: "sample", 998048: "sample",
    676701: "sample", 809406: "sample", 882673: "sample", 690722: "sample", 484176: "sample",
    544550: "sample", 467910: "sample", 606968: "sample", 592561: "sample", 130348: "sample",
    167481: "sample", 575186: "sample", 944582: "sample", 552735: "sample",
    # ── 코드가 조립하는 문장
    416608: "interp", 951834: "interp", 771311: "interp", 161131: "interp", 692890: "interp",
    601994: "interp", 537404: "interp", 195576: "interp", 140644: "interp", 954258: "interp",
    463294: "interp", 145264: "interp", 973615: "interp", 502696: "interp", 640277: "interp",
    781142: "interp", 708070: "interp", 90457: "interp", 225125: "interp", 789519: "interp",
    9093: "interp", 541022: "interp", 140049: "interp", 47759: "interp", 314273: "interp",
    767959: "interp", 994784: "interp", 331035: "interp", 862451: "interp", 958960: "interp",
    266999: "interp", 15444: "interp", 163225: "interp", 685920: "interp", 76104: "interp",
    58962: "interp", 984613: "interp", 354277: "interp", 433359: "interp", 224728: "interp",
    345954: "interp", 925223: "interp", 381963: "interp", 62704: "interp", 100920: "interp",
    # ── 보드 주석
    265037: "board", 941077: "board", 248266: "board", 963180: "board", 911527: "board",
    29323: "board", 638971: "board", 519836: "board", 126849: "board", 156146: "board",
    390175: "board", 258877: "board", 38939: "board", 288594: "board", 917006: "board",
    16277: "board", 176753: "board", 990899: "board", 350245: "board", 245960: "board",
    376276: "board", 52646: "board", 811482: "board", 341887: "board", 354543: "board",
    482051: "board", 67396: "board", 220372: "board", 90956: "board",
    # ── 표본 문서 본문
    503151: "doc", 549868: "doc", 551196: "doc", 950704: "doc", 347134: "doc", 301953: "doc",
    554604: "doc", 537027: "doc", 817316: "doc", 509031: "doc", 907825: "doc", 679024: "doc",
    257479: "doc", 65016: "doc",
    # ── 코드가 값을 끼워 조립하거나, 보드가 한 문장을 두 줄로 나눈 것
    787500: "interp", 812004: "interp", 185089: "interp", 517676: "interp", 458745: "interp", 763182: "interp",
    656753: "interp", 499170: "interp", 321587: "interp", 411884: "interp", 21677: "interp",
    # ── 열린 판정: 보드가 말하는데 코드에 그 말이 없는 것. **지금은 없다.**
    #    2026-09-06 에 여섯이 있었고 전부 판정이 아니라 낡음이었다 — 코드를 읽으니
    #    답이 하나씩 나왔다: 레일의 액션은 Share·Delete 둘뿐이고("Trace from here" 없음),
    #    전문은 버튼이 아니라 발췌 클릭으로 열리고, 플로트 카드 제목은 노드 이름이며,
    #    목적지 목록은 별도 시트가 아니라 "Share as a pack" 시트의 본문이고 그 목록에
    #    팀 행은 없다. 툴팁은 "View and legend" 다. 보드를 그대로 고쳤다.
    #    ⚠ 하나는 **코드 쪽에 남았다**: 코치마크가 "open it, trace from it, or share just
    #    that part" 라고 약속하는데 컨텍스트 메뉴는 Open · Share from here · Delete 셋뿐이다
    #    (view.ts:1486~). 카피를 고칠지 액션을 더할지는 이 게이트가 답할 수 없다.
    # ── 웹 스킨이 얹는 사이트 GNB(정본은 Vinc-homepage)
    605224: "site", 283018: "site", 991179: "site", 305331: "site", 135677: "site",
}

_STR = (re.compile(r'"((?:[^"\\]|\\.)*)"'),
        re.compile(r"'((?:[^'\\]|\\.)*)'"),
        re.compile(r"`([^`]*)`"))


def sig(s: str) -> int:
    """Figma 쪽 JS 와 **같은 함수**여야 한다. 다르면 전부 어긋난 것으로 잡힌다."""
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) % 999983
    return h


def code_sigs() -> set[int]:
    """`app/src` 의 문자열 리터럴 전량의 지문."""
    out: set[int] = set()
    for p in sorted(APP.rglob("*.ts")):
        text = p.read_text(encoding="utf-8")
        for rx in _STR:
            for m in rx.finditer(text):
                v = m.group(1)
                if v:
                    out.add(sig(v.replace('\\"', '"').replace("\\'", "'")))
    return out


def check(dump: dict, sigs: set[int]) -> dict:
    ok, declared, fail, open_rows = 0, {}, [], []
    for s, excerpt, board in dump["strings"]:
        if s in sigs:
            ok += 1
        elif ALLOW.get(s) == "open":
            open_rows.append((s, excerpt, board))
        elif s in ALLOW:
            declared[ALLOW[s]] = declared.get(ALLOW[s], 0) + 1
        else:
            fail.append((s, excerpt, board))
    stale = sorted(set(ALLOW) - {r[0] for r in dump["strings"]})
    return {"ok": ok, "declared": declared, "fail": fail, "open": open_rows, "stale": stale,
            "total": len(dump["strings"])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="앱 보드 문구 ↔ app/src 리터럴 대조")
    ap.add_argument("--list", action="store_true", help="선언 후보를 그대로 나열한다")
    a = ap.parse_args(argv)
    if not DUMP.exists():
        print(f"[ERROR] 덤프가 없다: {DUMP}\n        docstring 의 JS 를 use_figma 로 돌려 저장하라.",
              file=sys.stderr)
        return 3
    if not APP.exists():
        print("[SKIP] 앱 소스가 없다 — 대조 못 함")
        return 0
    dump = json.loads(DUMP.read_text(encoding="utf-8"))
    sigs = code_sigs()
    if a.list:
        # 허용목록을 처음 쓰거나 늘릴 때 쓴다: 코드에 없는 것만, 선언 여부와 무관하게.
        for s2, excerpt, board in dump["strings"]:
            if s2 not in sigs:
                print(f"{s2}: {ALLOW.get(s2, '?'):8} {board} · {excerpt!r}")
        return 0
    r = check(dump, sigs)
    print("── 앱 보드 문구 (보드 글자 ↔ app/src 리터럴)")
    for s, excerpt, board in r["fail"]:
        print(f"  [FAIL] {board} · {excerpt!r} — 앱이 하지 않는 말이다(지문 {s})")
    for s in r["stale"]:
        print(f"  [FAIL·잔여] 지문 {s} 가 ALLOW 에 있는데 보드에서 사라졌다")
    for s2, excerpt, board in r["open"]:
        print(f"  [OPEN] {board} · {excerpt!r} — 코드에 이 말이 없다. 기능인지 시안인지 사람이 정한다")
    if r["declared"]:
        print("  [INFO] 선언된 예외 "
              + " · ".join(f"{k} {v}" for k, v in sorted(r["declared"].items())))
    n = len(r["fail"]) + len(r["stale"])
    print(f"  대조 {r['total']}건 · 코드와 일치 {r['ok']} · 실패 {n}건")
    if n:
        print("  코드가 하는 말로 고치거나, 정당하면 ALLOW 에 분류와 함께 선언할 것",
              file=sys.stderr)
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
