# -*- coding: utf-8 -*-
"""sync_out.py — 허브 dist/ 산출물을 소비 리포로 단방향 복사한다.

허브(Vinc/design-hub)가 유일한 토큰 출처. 소비 리포의 동기화 파일은 전부
AUTO-GENERATED 헤더를 달고 있으며 수기 편집 금지 — 수정은 허브에서 재생성 후 재동기화.

매핑(허브 → 소비 리포). 허브가 Vinc 안으로 들어와(2026-08-08) 앱은 같은 리포다:
  dist/site/site-tokens.css      → ../../Vinc-homepage/site/assets/tokens.css
  dist/app/tokens.desktop.css    → ../app/src/tokens.desktop.css
  dist/app/tokens.web.css        → ../app/src/tokens.web.css
  dist/app/theme.ts              → ../app/src/theme.ts

usage: python -X utf8 scripts/sync_out.py [--apply|--check]
  기본은 dry-run(무엇이 복사될지 출력만). --apply 로 실제 복사, --check 는 검사만.

⚠ `dist/` 는 **커밋된다**(루트 .gitignore 의 `dist/` 에 예외를 뒀다). 소비 사본이
리포에 있는데 비교 대상이 없으면 `--check` 가 성립하지 않는다 — 2026-08-08 에 CI 가
정확히 그 이유로 빨갰다.
"""
from __future__ import annotations
import argparse, hashlib, pathlib, re, shutil, sys

HUB = pathlib.Path(__file__).resolve().parent.parent
MAP = [
    ("dist/site/site-tokens.css", "../../Vinc-homepage/site/assets/tokens.css"),
    ("dist/app/tokens.desktop.css", "../app/src/tokens.desktop.css"),
    ("dist/app/tokens.web.css", "../app/src/tokens.web.css"),
    ("dist/app/theme.ts", "../app/src/theme.ts"),
    # 로고(2026-08-23). 토큰과 달리 **변환이 없어 `dist/` 를 거치지 않는다** —
    # `/ds-gen` 이 만드는 산출물이 아니라 디자이너가 준 벡터 그대로이므로, 중간
    # 산출물을 두면 원본과 사본 사이에 세 번째 사본이 생길 뿐이다.
    #
    # ⚠ 소비처가 **인라인 SVG 를 쓰지 않는 것**이 이 경로의 전제다. 예전 로고는
    # 19개 파일에 22벌이 인라인으로 박혀 있었고, 그래서 정본이 있어도 아무 데도
    # 닿지 않았다(로고 교체 한 번에 44곳을 손으로 고쳐야 했다). `<img src>` 로
    # 참조하면 이 복사 하나가 전부를 갱신한다. 근거: [[record:a-logo-ssot-that-reaches-nobody]]
    ("assets/logo/vinc-hourglass-slab-circle.svg", "../app/public/favicon.svg"),
    ("assets/logo/vinc-hourglass-slab-circle.svg", "../../Vinc-homepage/site/favicon.svg"),
    ("assets/logo/vinc-hourglass-slab-indigo.svg", "../../Vinc-homepage/site/assets/vinc-mark.svg"),
    ("assets/logo/vinc-hourglass-slab-white.svg", "../../Vinc-homepage/site/assets/vinc-mark-white.svg"),
    ("assets/logo/vinc-hourglass-slab.svg", "../../Vinc-homepage/site/assets/vinc-plate.svg"),
]

#: 소비 리포에 박는 무결성 각인. 사본이 손으로 고쳐졌는지를 **그 리포 안에서만**
#: 판정할 수 있게 한다 — 교차 리포 체크아웃(=새 PAT 시크릿)이 필요 없다.
#: 각인 줄 자체는 해시 계산에서 빠진다(자기 참조 불가).
STAMP_KEY = "sync-sha256"
#: ⚠ SVG 는 `//` 도 `/* */` 도 못 받는다(XML 이다). 문서 요소 **앞**의 `<!-- -->` 는
#: 적법하므로 SVG 각인은 그 형태로 1행에 둔다.
_STAMP_RE = re.compile(
    rf"^[ \t]*(?://|/\*|<!--)[ \t]*{STAMP_KEY}:[ \t]*[0-9a-f]{{64}}.*\r?\n",
    re.MULTILINE)


def body_hash(text: str) -> str:
    """각인 줄을 뺀 본문의 sha256. 각인 전/후 어느 쪽을 넣어도 같은 값이 나온다."""
    return hashlib.sha256(strip_stamp(text).encode("utf-8")).hexdigest()


def strip_stamp(text: str) -> str:
    return _STAMP_RE.sub("", text)


def _stamp(text: str, suffix: str) -> str:
    """헤더 바로 아래에 각인 한 줄을 끼운다. 주석 문법은 확장자로 고른다."""
    digest = body_hash(text)
    if suffix in (".ts", ".tsx", ".js"):
        line = f"// {STAMP_KEY}: {digest}\n"
    elif suffix == ".svg":
        line = f"<!-- {STAMP_KEY}: {digest} -->\n"
    else:
        line = f"/* {STAMP_KEY}: {digest} */\n"
    clean = strip_stamp(text)
    lines = clean.splitlines(keepends=True)
    # 헤더(주석 블록) 끝을 찾는다. 못 찾으면 맨 앞에 둔다 — 파일이 깨지지 않는 자리.
    at = 0
    for i, ln in enumerate(lines[:8]):
        s = ln.strip()
        if s.startswith("//") or s.startswith("/*") or s.startswith("*") or s.endswith("*/"):
            at = i + 1
        elif s:
            break
    return "".join(lines[:at]) + line + "".join(lines[at:])


def _consumer_root(dst_rel: str):
    """소비 경로가 **다른 리포**를 가리키면 그 리포 루트를 돌려준다(아니면 None).

    이 허브는 Vinc 안에 산다(2026-08-08 이전). 그래서 `../app/...` 은 같은 리포이고
    `../../Vinc-homepage/...` 만 남의 리포다 — 그것만 부재를 허용한다."""
    if not dst_rel.startswith("../../"):
        return None
    parts = dst_rel.split("/")
    return (HUB / "/".join(parts[:3])).resolve()   # ../../<repo>


def _check() -> int:
    """소비 리포의 사본이 허브 산출물과 **같은지** 검사한다(복사하지 않는다).

    이게 없던 동안 `--apply` 는 수동이었고 어느 CI 도 사본을 보지 않았다. 그래서
    누가 `Vinc/app/src/tokens.desktop.css` 를 직접 고쳐도(파일에 "수기 편집 금지"
    헤더가 있어도 헤더는 강제하지 않는다) 아무 데서도 안 걸렸다. 소비 리포 CI 가
    이걸 돌리면 드리프트가 머지 전에 잡힌다.

    exit 0 = 전부 일치 · 1 = 어긋남/결손.
    """
    bad = skipped = 0
    for src_rel, dst_rel in MAP:
        src, dst = HUB / src_rel, (HUB / dst_rel).resolve()
        if not src.exists():
            print(f"[MISSING·허브] {src_rel} — 먼저 /ds-gen 으로 재생성하라")
            bad += 1
            continue
        # 소비 리포가 **아예 없는** 것과 그 안의 파일이 없는 것은 다른 사실이다.
        # CI 는 자기 리포 하나만 체크아웃하므로 다른 리포는 원리상 검사할 수 없다 —
        # 그걸 실패로 세면 게이트가 CI 에서 영구히 빨갛고, 조용히 넘기면 "검사했다"는
        # 거짓이 된다. 그래서 **건너뛰되 세어서 출력한다**.
        repo_root = _consumer_root(dst_rel)
        if repo_root is not None and not repo_root.exists():
            print(f"[SKIP·리포 없음] {dst_rel} — {repo_root.name} 이 체크아웃돼 있지 않다")
            skipped += 1
            continue
        if not dst.exists():
            print(f"[MISSING·소비] {dst} — sync_out.py --apply 필요")
            bad += 1
            continue
        a = src.read_text(encoding="utf-8")
        b = dst.read_text(encoding="utf-8")
        stamped = _STAMP_RE.search(b) is not None
        if strip_stamp(a) == strip_stamp(b):
            if not stamped:
                print(f"[OK·무각인] {dst_rel} — 내용은 같으나 각인이 없다 "
                      f"(sync_out.py --apply 로 다시 내보내면 붙는다)")
                continue
            print(f"[OK] {dst_rel}")
        else:
            print(f"[DRIFT] {dst} 가 허브의 {src_rel} 와 다르다 "
                  f"({len(a):,}자 vs {len(b):,}자) — 사본을 손으로 고쳤거나 "
                  f"허브 재생성 후 동기화를 안 했다")
            bad += 1
    tail = f" · 검사 못 함 {skipped}건(리포 부재)" if skipped else ""
    print(f"\n{'드리프트 없음' if not bad else f'문제 {bad}건'}{tail}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 복사(기본 dry-run)")
    ap.add_argument("--check", action="store_true",
                    help="복사하지 않고 사본이 허브와 같은지만 검사(CI 용)")
    a = ap.parse_args()
    if a.check:
        if a.apply:
            print("--check 와 --apply 는 같이 못 쓴다", file=sys.stderr)
            return 2
        return _check()
    missing = 0
    for src_rel, dst_rel in MAP:
        src = HUB / src_rel
        dst = (HUB / dst_rel).resolve()
        if not src.exists():
            print(f"[SKIP·미생성] {src_rel}")
            missing += 1
            continue
        if a.apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            text = _stamp(src.read_text(encoding="utf-8"), dst.suffix)
            dst.write_text(text, encoding="utf-8", newline="")
            print(f"[COPIED+각인] {src_rel} → {dst}")
        else:
            print(f"[DRY-RUN] {src_rel} → {dst}")
    if not a.apply:
        print("\n(dry-run — 실제 복사는 --apply. 검사만 하려면 --check)")
    return 1 if missing else 0

if __name__ == "__main__":
    sys.exit(main())
