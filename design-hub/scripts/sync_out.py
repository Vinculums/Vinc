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
    # 파비콘은 판 없는 링 하나다(2026-09-24). 밝은 탭엔 검정, 어두운 탭엔 흰색으로
    # SVG 안의 `prefers-color-scheme` 이 스스로 바꾼다.
    ("assets/logo/vinc-ring-favicon.svg", "../app/public/favicon.svg"),
    ("assets/logo/vinc-ring-mono.svg", "../app/public/vinc-mark-mono.svg"),
    ("assets/logo/vinc-ring-favicon.svg", "../../Vinc-homepage/site/favicon.svg"),
    ("assets/logo/vinc-ring-favicon.svg", "../../Vinc-homepage/site/demo/favicon.svg"),
    ("assets/logo/vinc-ring-black.svg", "../../Vinc-homepage/site/assets/vinc-mark.svg"),
    ("assets/logo/vinc-ring-white.svg", "../../Vinc-homepage/site/assets/vinc-mark-white.svg"),
    ("assets/logo/vinc-ring-mono.svg", "../../Vinc-homepage/site/assets/vinc-mark-mono.svg"),
    # 이메일용 래스터. **메일 클라이언트는 SVG 를 못 받는다** — Gmail 은 `<svg>` 를
    # 통째로 걷어내고 Outlook 데스크탑도 안 그린다. 그래서 이 한 자리만 PNG 다.
    # 소스는 흰 램프 마크(이메일 셸 배경이 #0A0B10 로 어둡다), 2배 크기(46x64).
    # 다시 뽑는 법: npx @resvg/resvg-js-cli --fit-height 64 <svg> <png>
    # (resvg 는 `tauri icon` 이 쓰는 엔진과 같아 앱 아이콘과 렌더가 어긋나지 않는다)
    ("assets/logo/vinc-ring-white-email@2x.png",
     "../../Vinc-homepage/site/assets/vinc-mark-email@2x.png"),
    # 잉크 마크. 메일이 밝은 배경으로 옮겨 가며 생겼다(2026-09-22).
    # ⚠ **위 흰 마크를 갈아 끼우지 않고 새 경로를 연 이유**: 메일의 이미지는 핫링크라
    # 그 URL 의 파일을 바꾸면 **이미 발송돼 받은편지함에 있는 메일이 전부 같이 바뀐다.**
    # 옛 어두운 메일에 잉크 마크가 얹히면 어두운 바탕에 어두운 마크가 되어 안 보인다.
    # 그래서 흰 마크는 그 자리에 영원히 남기고 새 템플릿만 이쪽을 가리킨다.
    # 흑백 로고 정책(VIN-104)으로 잉크판은 검정 링에서 뽑는다(build_logo.mjs). 같은
    # URL 을 인디고에서 검정으로 바꾸는 것은 안전하다: 이 마크를 받은 메일은 전부 밝은
    # 바탕이라 어두운 마크가 계속 보인다. 위의 경고는 바탕이 뒤집히는 경우의 이야기다.
    ("assets/logo/vinc-ring-ink-email@2x.png",
     "../../Vinc-homepage/site/assets/vinc-mark-ink-email@2x.png"),
    # 검색엔진·수집기용 래스터 파비콘. **SVG 파비콘만으로는 부족하다** — 상당수가
    # `<link rel=icon>` 을 안 보고 루트의 `/favicon.ico` 만 집어 가고, 없으면 아이콘을
    # 아예 못 가져가거나 옛 캐시를 계속 쓴다(실측: 검색 결과에 구 로고가 남아 있었다).
    # .ico 는 16·32·48 PNG 를 담은 컨테이너다(scripts 주석 참조).
    ("assets/logo/vinc-ring-favicon.ico", "../../Vinc-homepage/site/favicon.ico"),
    ("assets/logo/vinc-ring-favicon-48.png", "../../Vinc-homepage/site/favicon-48.png"),
]

#: 소비 리포에 박는 무결성 각인. 사본이 손으로 고쳐졌는지를 **그 리포 안에서만**
#: 판정할 수 있게 한다 — 교차 리포 체크아웃(=새 PAT 시크릿)이 필요 없다.
#: 각인 줄 자체는 해시 계산에서 빠진다(자기 참조 불가).
#: 각인을 **못 다는** 확장자. 주석 문법이 없으므로 바이트 그대로 나르고
#: `--check` 도 바이트로 대조한다. 사본을 손으로 고쳤는지는 그것으로 충분히
#: 갈린다 — 각인은 "본문은 같은데 각인만 없는" 중간 상태를 가리려고 있는 것이고,
#: 바이너리에는 그런 상태가 없다.
_BINARY_SUFFIXES = {".png", ".ico", ".icns", ".jpg", ".jpeg", ".webp", ".woff2"}

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
        if dst.suffix.lower() in _BINARY_SUFFIXES:
            if src.read_bytes() == dst.read_bytes():
                print(f"[OK·바이너리] {dst_rel}")
            else:
                print(f"[DRIFT] {dst} 가 허브의 {src_rel} 와 바이트가 다르다 — "
                      f"자산을 다시 뽑고 동기화하지 않았다")
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
            if dst.suffix.lower() in _BINARY_SUFFIXES:
                shutil.copyfile(src, dst)
                print(f"[COPIED·바이너리] {src_rel} → {dst}")
            else:
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
