#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""View 마크 대비 게이트 — WCAG 2.x 와 APCA 를 **둘 다** 통과해야 한다.

왜 둘 다인가. 두 기준은 극성에 따라 서로 다른 쪽이 더 엄격하다(실측):

  * 다크 배경에서는 WCAG 가 낙관 편향이다. `#6E7189` 는 WCAG 4.05:1 로 "통과"
    하지만 APCA 로는 Lc 28.2 이라 solid 최소치(30)에 못 미친다.
  * 라이트 배경에서는 반대다. `#8F96D9` 는 APCA Lc 45.8 로 통과하지만 WCAG 는
    2.49:1 로 미달이다.

그래서 "더 엄격한 쪽을 고른다"는 규칙은 성립하지 않고(어느 쪽이 엄격한지가
색마다 뒤집힌다) **AND 결합**만이 정확하다. 법적 수용 기준은 WCAG 2.x 를
유지하고(APCA 는 2026-08 현재 규범이 아니며 WCAG 3 는 알고리즘 미정), APCA 는
다크에서 WCAG 가 놓치는 함정을 거르는 **진단 렌즈**로 병기한다.

무엇을 재는가:

  * `viewScene.node.<tier>` — 티어마다 **채널 하나 이상**이 통과하면 된다.
    fill 이 어두운 것은 깊이 인상이라 그 자체가 결함이 아니고, 그 경우 링
    (stroke)이 가시성을 진다. 다만 **링은 가는 선**이라 APCA 세부 기준(Lc 45)을
    적용한다. 다크 전용 분기이므로 다크 배경으로만 잰다.
  * `linkRamp.{heavy,mid,base,cross}` — 엣지 본선. 두 모드 각각 검사.
  * `linkRamp.fade` — **의도적 후퇴 채널**이라 3:1 을 강제하지 않는다(WCAG 1.4.11
    의 비활성 예외). 대신 "완전 소멸 금지"를 위해 APCA 불가시 하한 Lc 15 만 본다.
  * 본문 잉크(text) — 텍스트 기준 4.5:1.

exit 0 = 통과 · 1 = 위반 · 3 = 내부 오류(파싱 실패 등).

APCA 는 공식 0.98G-4g 상수를 그대로 인라인했다(외부 의존 0 — CSP·오프라인 불변식).
자체 검증: `#888888` on `#FFFFFF` = 63.06(공식 keystone 값).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ── 임계 ─────────────────────────────────────────────────────────────────────
WCAG_NONTEXT = 3.0      # 비텍스트 그래픽 객체(WCAG 1.4.11)
WCAG_TEXT = 4.5         # 본문 텍스트(1.4.3)
LC_SOLID = 30.0         # APCA — 큰/solid 비텍스트 최소
LC_THIN = 45.0          # APCA — 가는 선·세부(링·헤어라인) 최소
LC_INVISIBLE = 15.0     # APCA — 이 아래는 "안 보이는 것"으로 취급(후퇴 채널 하한)


# ── 색 계산 ──────────────────────────────────────────────────────────────────
def _rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"6자리 hex 가 아니다: {h!r}")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def wcag_ratio(fg: str, bg: str) -> float:
    def lum(c: str) -> float:
        def ch(v: float) -> float:
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b = (ch(x) for x in _rgb(c))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def apca_lc(fg: str, bg: str) -> float:
    """APCA 0.98G-4g. 반환은 **크기**(부호 없는 Lc) — 극성은 임계에 무관하다."""
    def y(c: str) -> float:
        r, g, b = _rgb(c)
        return 0.2126729 * r ** 2.4 + 0.7151522 * g ** 2.4 + 0.0721750 * b ** 2.4

    def soft(v: float) -> float:                      # 흑점 연화
        return v if v > 0.022 else v + (0.022 - v) ** 1.414

    yt, yb = soft(y(fg)), soft(y(bg))
    if abs(yb - yt) < 0.0005:
        return 0.0
    if yb > yt:                                       # 밝은 배경 · 어두운 전경
        s = (yb ** 0.56 - yt ** 0.57) * 1.14
        c = 0.0 if s < 0.1 else s - 0.027
    else:                                             # 어두운 배경 · 밝은 전경
        s = (yb ** 0.65 - yt ** 0.62) * 1.14
        c = 0.0 if s > -0.1 else s + 0.027
    return abs(c * 100)


# ── theme.ts 파싱 ────────────────────────────────────────────────────────────
_HEX6 = r"#[0-9A-Fa-f]{6}"


def _mode_block(src: str, mode: str) -> str:
    m = re.search(rf"\b{mode}:\s*\{{(.*?)\n  \}},", src, re.S)
    if not m:
        raise ValueError(f"theme.ts 에서 palette.{mode} 블록을 못 찾았다")
    return m.group(1)


def _hex_of(block: str, key: str) -> str:
    """`key: 0xRRGGBB, // #RRGGBB ←…` 또는 `key: '#RRGGBB'` 둘 다 받는다."""
    m = re.search(rf"\b{key}:\s*0x([0-9A-Fa-f]{{6}})", block)
    if m:
        return "#" + m.group(1)
    m = re.search(rf"\b{key}:\s*'({_HEX6})'", block)
    if m:
        return m.group(1)
    raise ValueError(f"키를 못 찾았다: {key}")


def _link_ramp(block: str) -> dict[str, str]:
    m = re.search(r"linkRamp:\s*\{(.*?)\}", block, re.S)
    if not m:
        raise ValueError("linkRamp 를 못 찾았다")
    return {k: v for k, v in re.findall(rf"(\w+):\s*'({_HEX6})'", m.group(1))}


def _view_scene(src: str) -> dict[str, dict[str, str]]:
    m = re.search(r"export const viewScene = \{(.*?)\n\} as const;", src, re.S)
    if not m:
        raise ValueError("viewScene 을 못 찾았다")
    out: dict[str, dict[str, str]] = {}
    for tier, body in re.findall(r"(focus|near|mid|far):\s*\{([^}]*)\}", m.group(1)):
        chans = {k: "#" + v for k, v in
                 re.findall(r"(fill|stroke):\s*0x([0-9A-Fa-f]{6})", body)}
        out[tier] = chans
    return out


# ── 검사 ─────────────────────────────────────────────────────────────────────
class Finding(str):
    pass


def _check(label: str, fg: str, bg: str, *, wcag_min: float, lc_min: float,
           out: list[Finding]) -> bool:
    w, lc = wcag_ratio(fg, bg), apca_lc(fg, bg)
    bad = []
    if w < wcag_min:
        bad.append(f"WCAG {w:.2f}:1 < {wcag_min}")
    if lc < lc_min:
        bad.append(f"APCA Lc {lc:.1f} < {lc_min}")
    if bad:
        out.append(Finding(f"{label}: {fg} on {bg} — " + " · ".join(bad)))
        return False
    return True


def run(theme_path: Path) -> tuple[int, list[Finding]]:
    src = theme_path.read_text(encoding="utf-8")
    findings: list[Finding] = []

    bgs = {}
    for mode in ("light", "dark"):
        blk = _mode_block(src, mode)
        bgs[mode] = _hex_of(blk, "background")
        ramp = _link_ramp(blk)
        for key in ("heavy", "mid", "base", "cross"):
            if key in ramp:
                _check(f"[{mode}] linkRamp.{key}", ramp[key], bgs[mode],
                       wcag_min=WCAG_NONTEXT, lc_min=LC_SOLID, out=findings)
        # 후퇴 채널 — 3:1 면제(1.4.11 비활성), 소멸만 막는다.
        if "fade" in ramp:
            lc = apca_lc(ramp["fade"], bgs[mode])
            if lc < LC_INVISIBLE:
                findings.append(Finding(
                    f"[{mode}] linkRamp.fade: {ramp['fade']} on {bgs[mode]} — "
                    f"APCA Lc {lc:.1f} < {LC_INVISIBLE}(불가시 하한). "
                    "후퇴 채널은 3:1 면제이나 완전 소멸은 안 된다"))
        _check(f"[{mode}] text", _hex_of(blk, "text"), bgs[mode],
               wcag_min=WCAG_TEXT, lc_min=LC_SOLID, out=findings)
        # 포인트색(2026-09-25): point 는 링·선택·초점 노드라 비텍스트 3:1 + 가는 선
        # 기준(Lc 45), point-text 는 작은 글자에도 쓰므로 본문 4.5:1 + Lc 60.
        _check(f"[{mode}] point", _hex_of(blk, "point"), bgs[mode],
               wcag_min=WCAG_NONTEXT, lc_min=LC_THIN, out=findings)
        _check(f"[{mode}] pointText", _hex_of(blk, "pointText"), bgs[mode],
               wcag_min=WCAG_TEXT, lc_min=60.0, out=findings)

    # viewScene — 다크 전용 분기(view3d 의 glow 브랜치)라 다크로만 잰다.
    dark_bg = bgs["dark"]
    for tier, chans in _view_scene(src).items():
        if not chans:
            findings.append(Finding(f"[dark] viewScene.node.{tier}: 채널이 없다"))
            continue
        # 티어마다 **하나 이상**이 통과하면 된다. 링(stroke)은 가는 선이라 Lc 45.
        ok, tried = False, []
        for chan, hexv in chans.items():
            lc_min = LC_THIN if chan == "stroke" else LC_SOLID
            sub: list[Finding] = []
            if _check(f"[dark] viewScene.node.{tier}.{chan}", hexv, dark_bg,
                      wcag_min=WCAG_NONTEXT, lc_min=lc_min, out=sub):
                ok = True
            tried += sub
        if not ok:
            findings.append(Finding(
                f"[dark] viewScene.node.{tier}: 통과 채널이 하나도 없다 — "
                + " / ".join(str(t).split(" — ", 1)[-1] for t in tried)))
    return (1 if findings else 0), findings


# ── recede 리터럴 게이트 (D32-a 단일 원천) ───────────────────────────────────
_RECEDE_LITERAL = re.compile(r"\*\s*0?\.(15|12|35|40?)\b")
#: 물러남 맥락에서만 잡는다 — 기하 계수(`r * 0.12` 튜브 반경, 곡률 `* 0.12`)는
#: 같은 숫자여도 물러남이 아니다. 불투명도를 만지는 줄에만 규율을 건다.
_RECEDE_CTX = re.compile(r"\b(opacity|fade)\b|userData\.base", re.I)
#: 그래프를 그리는 지금의 표면들. 이름을 바꾸거나 지우면 아래에서 실패로 드러난다(조용히 건너뛰지 않는다).
_VIEW_FILES = ("view.ts", "view-compass.ts", "view-compass-shapes.ts", "view-compass-whole-draw.ts", "view-compass-whole.ts")


def run_recede(app_src: Path) -> list[Finding]:
    """물러남 수치를 뷰 코드에 리터럴로 다시 심는 회귀를 잡는다.

    정본은 `app.css` 의 `--recede-*` 하나이고 읽기는 `recede.ts` 하나다. 실제로
    view3d 가 `base * 0.15` 로 정본(0.40)의 3.75 배를 물러나 비이웃이 소멸한
    적이 있다(가시성 연구 C4)."""
    out: list[Finding] = []
    for name in _VIEW_FILES:
        p = app_src / "surfaces" / name
        if not p.exists():
            # 없는 파일을 건너뛰면 이 검사가 아무것도 읽지 않고 통과한다: 이름 목록을 고치라고 막는다
            out.append(Finding(f"{name}: _VIEW_FILES 가 가리키는 파일이 없다 — 지금 그리는 표면으로 목록을 고쳐라"))
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "recede" in line.lower():
                continue                      # 토큰 경유는 정상
            if _RECEDE_CTX.search(line) and _RECEDE_LITERAL.search(line):
                out.append(Finding(f"{name}:{i} 물러남으로 읽힐 수 있는 곱셈 리터럴 "
                                   f"— `--recede-*` 를 recede() 로 읽어라: {line.strip()}"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="View 대비 게이트(WCAG + APCA 이중)")
    ap.add_argument("--theme", default="app/src/theme.ts")
    ap.add_argument("--app-src", default="app/src")
    args = ap.parse_args(argv)

    # 자체 검증 — 공식 keystone. 상수를 잘못 옮기면 여기서 먼저 터진다.
    ks = apca_lc("#888888", "#FFFFFF")
    if abs(ks - 63.06) > 0.05:
        print(f"[gate_contrast] 내부 오류 — APCA keystone {ks:.2f} != 63.06", file=sys.stderr)
        return 3
    try:
        code, findings = run(Path(args.theme))
        findings += run_recede(Path(args.app_src))
    except Exception as exc:                  # 파싱 실패는 통과로 위장하지 않는다
        print(f"[gate_contrast] 내부 오류 — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    if findings:
        print(f"[gate_contrast] FAIL — {len(findings)}건")
        for f in findings:
            print(f"  · {f}")
        print("\n기준: 비텍스트 WCAG ≥3:1 AND APCA Lc ≥30(가는 선 ≥45) · "
              "텍스트 ≥4.5:1 · 후퇴 채널은 Lc ≥15 만")
        return 1
    print("[gate_contrast] PASS — 마크·엣지 채널이 WCAG·APCA 양쪽을 통과 "
          "(후퇴 채널은 불가시 하한 Lc 15 이상)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
