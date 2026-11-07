# -*- coding: utf-8 -*-
"""gate_surface.py — 소비 표면 D-S01 게이트를 **생성물 제외**하고 돌린다.

왜 래퍼가 필요한가. 엔진(`_meta/design_rules/governance.py:surface_findings`)은
토큰 파일을 `fp.name == "tokens.css"` **이름 하나로만** 제외한다. 그런데 이 허브는
소비 리포로 세 개를 내보낸다 — `tokens.desktop.css` · `tokens.web.css` · `theme.ts`.
셋 다 이름이 달라서 스캔 대상이 되고, 토큰 정의 파일이니 당연히 hex 가 가득하다.

실측(2026-08-08, `Vinc/app/src`): FIX 128건 중 **122건이 이 자기 산출물**이었고
진짜 하드코딩은 5건이었다. 신호 대 잡음이 1:24 라 게이트가 사실상 안 읽힌다.

그래서 이름 목록 대신 **`AUTO-GENERATED` 헤더**로 거른다. 목록은 파일이 늘면
낡지만 헤더는 생성기가 스스로 붙이므로 낡지 않는다. 엔진의 의도(토큰 정의 파일은
D-S01 대상이 아니다)를 그대로 따르는 것이고 완화가 아니다 — 걸러낸 건수는 항상
보고한다.

    python -X utf8 scripts/design/gate_surface.py app/src
    python -X utf8 scripts/design/gate_surface.py ../Vinc-homepage/site --json

exit 0 = 위반 없음 · 1 = 위반 있음 · 3 = 엔진 오류.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HUB = Path(__file__).resolve().parent
MARKER = "AUTO-GENERATED"
#: 헤더가 있는지 볼 범위. 생성기는 전부 첫 3줄 안에 붙인다.
HEADER_LINES = 5


def _plugin_root() -> Path | None:
    """엔진(lime-design) 위치. **없으면 None** — CI 에는 안 깔려 있다."""
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env and Path(env).exists():
        return Path(env)
    base = Path.home() / ".claude" / "plugins" / "cache" / "lime-design" / "lime-design"
    if not base.exists():
        return None
    vers = sorted(base.iterdir())
    return vers[-1] if vers else None


#: 엔진의 D-S01 을 그대로 옮긴 것(`_meta/design_rules/profile.py`
#: `refgate_hex_pattern` · `governance.surface_findings`). **왜 복제하나**: 이 게이트는
#: CI 에서 돌아야 하는데 lime-design 은 개발 기기의 Claude Code 플러그인이라 러너에
#: 깔 수가 없다(pip 패키지가 아니다). 플러그인이 있으면 그쪽을 쓰고, 없으면 이걸 쓴다 —
#: **어느 쪽으로 돌았는지 항상 출력한다**(조용한 폴백은 "검사했다"는 거짓이 된다).
_VENDOR_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_VENDOR_EXTS = (".tsx", ".ts", ".jsx", ".js", ".css", ".scss")


class _Finding:
    __slots__ = ("id", "severity", "line", "found", "fix_hint")

    def __init__(self, line: int, found: str) -> None:
        self.id, self.severity, self.line, self.found = "D-S01", "FIX", line, found
        self.fix_hint = "토큰을 쓸 것 — hsl(var(--x))"


def _vendor_findings(root: Path) -> list:
    """엔진 부재 시의 D-S01 스캔. 규칙·제외를 엔진과 같게 맞춘다."""
    out = []
    for fp in sorted(root.rglob("*")):
        if not fp.is_file() or fp.suffix not in _VENDOR_EXTS:
            continue
        if "node_modules" in fp.parts or fp.name == "tokens.css":
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines()):
            st = line.strip()
            if st.startswith("/*") or st.startswith("//"):
                continue
            if _VENDOR_HEX.search(line):
                out.append(_Finding(i, f"{fp.name}: {line.strip()[:160]}"))
    return out


#: 엔진의 D-S01 은 `#hex` 만 본다. 그래서 three.js 의 `0xRRGGBB` 와 CSS 폴백의
#: 푸른 `hsl(2xx ...)` 는 게이트 밖이었고, 흑백 전환(2026-09-24) 때 조명·그림자에 남색이
#: 그대로 남아 있었다. 둘을 **이 래퍼가** 더 본다(엔진 유무와 무관). 주석 안은 보지 않는다.
_EXTRA = re.compile(r"\b0x[0-9a-fA-F]{6}\b|hsl\(\s*2[0-4]\d\s")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _extra_findings(root: Path) -> list:
    out = []
    for fp in sorted(root.rglob("*")):
        if not fp.is_file() or fp.suffix not in _VENDOR_EXTS:
            continue
        if "node_modules" in fp.parts or fp.name == "tokens.css":
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = _BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
        for i, line in enumerate(text.splitlines()):
            code = line.split("//", 1)[0]
            if _EXTRA.search(code):
                out.append(_Finding(i, f"{fp.name}: {line.strip()[:160]}"))
    return out


#: 벤더 사본이 사는 디렉터리. 남의 라이브러리는 우리 표면이 아니다 — three.js 하나가
#: 위반 3건과 선택자 의심 17건을 만들어 진짜 6건을 묻어 버렸다(2026-09-06 실측).
#: 이름이 아니라 **경로**로 거른다: 파일명만 보면 우리 파일과 구분이 안 된다.
VENDOR_DIRS = ("vendor", "node_modules", "third_party")


def _is_vendored(root: Path, name: str) -> bool:
    """이 이름의 파일이 벤더 디렉터리 **안에만** 있는가.

    같은 이름이 우리 쪽에도 있으면 거르지 않는다 — 그쪽 위반을 잃지 않기 위해서다.
    """
    hits = list(root.rglob(name))
    if not hits:
        return False
    return all(any(part in VENDOR_DIRS for part in fp.relative_to(root).parts) for fp in hits)

def _is_generated(root: Path, name: str) -> bool:
    """이 이름의 파일이 생성물인가. 같은 이름이 여럿이면 하나라도 생성물이면 참."""
    for fp in root.rglob(name):
        try:
            head = "".join(fp.open(encoding="utf-8", errors="ignore").readlines()[:HEADER_LINES])
        except OSError:
            continue
        if MARKER in head:
            return True
    return False


#: 정당한 비-디자인 색을 위한 **가시적 면제**. 같은 줄이나 바로 윗줄에
#: `ds-allow: <이유>` 를 적으면 위반에서 빠지되 **건수와 이유가 보고된다**.
#: 조용히 빼지 않는 이유: 면제가 안 보이면 다음 사람이 그게 검토된 것인지
#: 빠뜨린 것인지 못 가른다. 실제 사례는 view3d.ts 의 `#ffffff` — 스프라이트
#: 텍스처를 흰색으로 그려 두고 SpriteMaterial 의 color 로 **틴트**하므로
#: 그 흰색은 디자인 색이 아니라 곱셈의 항등원이다.
WAIVER = re.compile(r"ds-allow:\s*(.+?)\s*(?:\*/|$)")


#: `#add-more` 처럼 **CSS 선택자**가 헥사로 오인되는 경우. 엔진 정규식은 `#add` 를
#: 3자리 헥사로 읽는다(a·d·d 가 전부 헥사 숫자다). 진짜 색은 헥사 뒤에 `-` 나 글자가
#: 오지 않는다. 이 판정은 **버리는 게 아니라 따로 세어 보고**한다 — 조용히 지우면
#: 진짜 위반 하나를 놓쳐도 아무도 모른다.
_SELECTOR = re.compile(r"#[0-9a-fA-F]{3,8}[-\w]")


#: 면제를 찾아 올라갈 줄 수. 이유가 한 줄에 안 들어가는 경우가 흔해서
#: `ds-allow:` 가 주석 블록의 **첫 줄**에 오고 설명이 아래로 이어진다. 그래서
#: 바로 윗줄만 보면 못 잡는다(실측: view3d.ts 에서 두 줄 위였다).
WAIVER_LOOKBACK = 4


def _waiver(root: Path, name: str, line: int) -> str | None:
    """이 위반 줄에 면제가 붙어 있으면 그 이유를 돌려준다.

    같은 줄부터 위로 WAIVER_LOOKBACK 줄까지 보되, **주석이 아닌 줄을 만나면
    멈춘다** — 위쪽 무관한 코드의 면제가 아래로 새는 것을 막는다.
    """
    for fp in root.rglob(name):
        try:
            lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for back in range(WAIVER_LOOKBACK + 1):
            idx = line - back                 # 엔진의 line 은 0-기반이다
            if not (0 <= idx < len(lines)):
                break
            m = WAIVER.search(lines[idx])
            if m:
                return m.group(1)
            if back and not lines[idx].lstrip().startswith(("//", "/*", "*")):
                break                          # 주석 블록을 벗어났다
    return None


def _looks_like_selector(excerpt: str) -> bool:
    body = excerpt.split(":", 1)[1] if ":" in excerpt else excerpt
    real = re.search(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![-\w])", body)
    return real is None and bool(_SELECTOR.search(body))


def main() -> int:
    ap = argparse.ArgumentParser(description="소비 표면 D-S01 게이트(생성물 제외)")
    ap.add_argument("surface", help="스캔할 디렉터리 (예: app/src)")
    ap.add_argument("--json", action="store_true", help="JSON 으로 출력")
    args = ap.parse_args()

    root = Path(args.surface).resolve()
    if not root.exists():
        print(f"[gate_surface] 경로 없음: {root}", file=sys.stderr)
        return 3

    plugin = _plugin_root()
    engine = "lime-design"
    surface_findings = None
    if plugin is not None:
        sys.path.insert(0, str(plugin))
        try:
            from _meta.design_rules.governance import surface_findings  # type: ignore
        except Exception as e:                               # noqa: BLE001
            print(f"[gate_surface] 엔진 임포트 실패 — 내장 규칙으로 진행: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    if surface_findings is None:
        engine = "내장(D-S01 복제)"
        surface_findings = _vendor_findings

    raw = list(surface_findings(root)) + _extra_findings(root)
    kept, skipped, suspect, waived = [], [], [], []
    gen_cache: dict[str, bool] = {}
    for f in raw:
        # Finding.found 는 "<파일명>: <발췌>" 꼴이다(엔진이 파일명만 싣는다).
        name = str(f.found).split(":", 1)[0].strip()
        if name not in gen_cache:
            gen_cache[name] = _is_generated(root, name) or _is_vendored(root, name)
        if gen_cache[name]:
            skipped.append(f)
        elif _looks_like_selector(str(f.found)):
            suspect.append(f)
        else:
            why = _waiver(root, name, int(f.line or 0))
            (waived if why else kept).append((f, why) if why else f)

    payload = {
        "kind": "surface",
        "engine": engine,
        "surface": str(root),
        "status": "PASS" if not kept else "FAIL",
        "violations": len(kept),
        "skipped_generated": len(skipped),
        "generated_files": sorted({str(f.found).split(":", 1)[0].strip() for f in skipped}),
        "suspect_selector": [{"line": f.line, "found": f.found} for f in suspect],
        "waived": [{"line": f.line, "found": f.found, "reason": why} for f, why in waived],
        "findings": [{"id": f.id, "severity": f.severity, "line": f.line,
                      "found": f.found, "fix_hint": f.fix_hint} for f in kept],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"[{payload['status']}] {root}  · 엔진 {engine}")
        print(f"  위반 {len(kept)}건 · 생성물 제외 {len(skipped)}건 "
              f"({', '.join(payload['generated_files']) or '없음'})"
              + (f" · 선택자 의심 {len(suspect)}건" if suspect else "")
              + (f" · 면제 {len(waived)}건" if waived else ""))
        for f in kept:
            print(f"  D-S01 {str(f.found).split(':', 1)[0].strip()}:{f.line}  "
                  f"{str(f.found).split(':', 1)[1].strip()[:96]}")
        for f, why in waived:                   # 면제도 항상 보인다
            print(f"  (면제) {str(f.found).split(':', 1)[0].strip()}:{f.line}  {why}")
        for f in suspect:                       # 숨기지 않는다 — 판정만 분리한다
            print(f"  (선택자 의심) {str(f.found).split(':', 1)[0].strip()}:{f.line}  "
                  f"{str(f.found).split(':', 1)[1].strip()[:96]}")
    return 0 if not kept else 1


if __name__ == "__main__":
    raise SystemExit(main())
