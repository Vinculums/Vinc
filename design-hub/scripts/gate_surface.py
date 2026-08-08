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

    python -X utf8 scripts/gate_surface.py ../app/src
    python -X utf8 scripts/gate_surface.py ../../Vinc-homepage/site --json

exit 0 = 위반 없음 · 1 = 위반 있음 · 3 = 엔진 오류.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HUB = Path(__file__).resolve().parent.parent
MARKER = "AUTO-GENERATED"
#: 헤더가 있는지 볼 범위. 생성기는 전부 첫 3줄 안에 붙인다.
HEADER_LINES = 5


def _plugin_root() -> Path:
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return Path(env)
    base = Path.home() / ".claude" / "plugins" / "cache" / "lime-design" / "lime-design"
    if not base.exists():
        raise SystemExit("[gate_surface] lime-design 플러그인을 못 찾았다 — "
                         "CLAUDE_PLUGIN_ROOT 를 지정하라")
    return sorted(base.iterdir())[-1]                      # 최신 버전 디렉터리


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


#: `#add-more` 처럼 **CSS 선택자**가 헥사로 오인되는 경우. 엔진 정규식은 `#add` 를
#: 3자리 헥사로 읽는다(a·d·d 가 전부 헥사 숫자다). 진짜 색은 헥사 뒤에 `-` 나 글자가
#: 오지 않는다. 이 판정은 **버리는 게 아니라 따로 세어 보고**한다 — 조용히 지우면
#: 진짜 위반 하나를 놓쳐도 아무도 모른다.
_SELECTOR = re.compile(r"#[0-9a-fA-F]{3,8}[-\w]")


def _looks_like_selector(excerpt: str) -> bool:
    body = excerpt.split(":", 1)[1] if ":" in excerpt else excerpt
    real = re.search(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![-\w])", body)
    return real is None and bool(_SELECTOR.search(body))


def main() -> int:
    ap = argparse.ArgumentParser(description="소비 표면 D-S01 게이트(생성물 제외)")
    ap.add_argument("surface", help="스캔할 디렉터리 (예: ../app/src)")
    ap.add_argument("--json", action="store_true", help="JSON 으로 출력")
    args = ap.parse_args()

    root = Path(args.surface).resolve()
    if not root.exists():
        print(f"[gate_surface] 경로 없음: {root}", file=sys.stderr)
        return 3

    sys.path.insert(0, str(_plugin_root()))
    try:
        from _meta.design_rules.governance import surface_findings
    except Exception as e:                                   # noqa: BLE001
        print(f"[gate_surface] 엔진 임포트 실패: {type(e).__name__}: {e}", file=sys.stderr)
        return 3

    raw = surface_findings(root)
    kept, skipped, suspect = [], [], []
    gen_cache: dict[str, bool] = {}
    for f in raw:
        # Finding.found 는 "<파일명>: <발췌>" 꼴이다(엔진이 파일명만 싣는다).
        name = str(f.found).split(":", 1)[0].strip()
        if name not in gen_cache:
            gen_cache[name] = _is_generated(root, name)
        if gen_cache[name]:
            skipped.append(f)
        elif _looks_like_selector(str(f.found)):
            suspect.append(f)
        else:
            kept.append(f)

    payload = {
        "kind": "surface",
        "surface": str(root),
        "status": "PASS" if not kept else "FAIL",
        "violations": len(kept),
        "skipped_generated": len(skipped),
        "generated_files": sorted({str(f.found).split(":", 1)[0].strip() for f in skipped}),
        "suspect_selector": [{"line": f.line, "found": f.found} for f in suspect],
        "findings": [{"id": f.id, "severity": f.severity, "line": f.line,
                      "found": f.found, "fix_hint": f.fix_hint} for f in kept],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"[{payload['status']}] {root}")
        print(f"  위반 {len(kept)}건 · 생성물 제외 {len(skipped)}건 "
              f"({', '.join(payload['generated_files']) or '없음'})"
              + (f" · 선택자 의심 {len(suspect)}건" if suspect else ""))
        for f in kept:
            print(f"  D-S01 {str(f.found).split(':', 1)[0].strip()}:{f.line}  "
                  f"{str(f.found).split(':', 1)[1].strip()[:96]}")
        for f in suspect:                       # 숨기지 않는다 — 판정만 분리한다
            print(f"  (선택자 의심) {str(f.found).split(':', 1)[0].strip()}:{f.line}  "
                  f"{str(f.found).split(':', 1)[1].strip()[:96]}")
    return 0 if not kept else 1


if __name__ == "__main__":
    raise SystemExit(main())
