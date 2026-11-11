# -*- coding: utf-8 -*-
"""sync_out.py — 토큰 생성물과 브랜드 자산을 소비처로 단방향으로 나른다.

값의 정본은 그래프이고 `tokens.json` 은 그 스냅샷이다(`pull_tokens.py`). 토큰 파일 넷은
`build_tokens.py` 가 스냅샷에서 **메모리로** 만들어 여기서 바로 쓴다 — 중간 산출물 디렉터리는
없다. 로고와 로더는 `assets/` 의 원본을 그대로 복사한다. 소비처의 파일은 전부 각인을 달고
수기 편집 금지다.

  gen:tokens.desktop.css   → app/src/tokens.desktop.css
  gen:tokens.web.css       → app/src/tokens.web.css
  gen:theme.ts             → app/src/theme.ts
  gen:site-tokens.css      → ../Vinc-homepage/site/assets/tokens.css

usage: python -X utf8 scripts/design/sync_out.py [--apply|--check]
  기본은 dry-run(무엇이 복사될지 출력만). --apply 로 실제 복사, --check 는 검사만.
  `--check` 는 스냅샷에서 다시 만든 것과 사본을 대조한다: 사본을 손으로 고친 것과,
  스냅샷만 바뀌고 `--apply` 를 안 한 것을 함께 잡는다.

⚠ `../Vinc-homepage` 는 **git 체크아웃이어야** 소비 리포로 친다. worktree
(`_wt/<이름>`) 에서는 그 경로가 `_wt/Vinc-homepage` 로 풀려 리포가 아니므로, 메인
워크트리의 형제를 대신 찾는다(`_locate_repo`). 거기도 없으면 `--apply` 는 아무것도
쓰지 않고 거절한다 — **소비 리포 디렉터리를 새로 만들지 않는다.**
근거: [[record:sync-out-in-a-worktree-wrote-the-homepage-copies-into-a-stray-directory-2026-09-26]]
"""
from __future__ import annotations
import argparse, hashlib, pathlib, re, shutil, subprocess, sys

HUB = pathlib.Path(__file__).resolve().parent
REPO = HUB.parent.parent
sys.path.insert(0, str(HUB))
import build_tokens  # noqa: E402

#: (원본, 소비 경로). 원본이 `gen:` 이면 `build_tokens.outputs()` 의 그 이름, 아니면 HUB 기준
#: 파일. 소비 경로는 리포 루트 기준이고 `../<리포>/` 로 시작하면 이웃 리포다.
MAP = [
    ("gen:site-tokens.css", "../Vinc-homepage/site/assets/tokens.css"),
    ("gen:tokens.desktop.css", "app/src/tokens.desktop.css"),
    ("gen:tokens.web.css", "app/src/tokens.web.css"),
    ("gen:theme.ts", "app/src/theme.ts"),
    # 로고(2026-08-23). 토큰과 달리 **변환이 없다** — 디자이너가 준 벡터 그대로라
    # 원본을 바로 나른다.
    #
    # ⚠ 소비처가 **인라인 SVG 를 쓰지 않는 것**이 이 경로의 전제다. 예전 로고는
    # 19개 파일에 22벌이 인라인으로 박혀 있었고, 그래서 정본이 있어도 아무 데도
    # 닿지 않았다(로고 교체 한 번에 44곳을 손으로 고쳐야 했다). `<img src>` 로
    # 참조하면 이 복사 하나가 전부를 갱신한다. 근거: [[record:a-logo-ssot-that-reaches-nobody]]
    # 파비콘은 판 없는 링 하나다(2026-09-24). 밝은 탭엔 검정, 어두운 탭엔 흰색으로
    # SVG 안의 `prefers-color-scheme` 이 스스로 바꾼다.
    ("assets/logo/vinc-ring-favicon.svg", "app/public/favicon.svg"),
    ("assets/logo/vinc-ring-mono.svg", "app/public/vinc-mark-mono.svg"),
    ("assets/logo/vinc-ring-favicon.svg", "../Vinc-homepage/site/favicon.svg"),
    ("assets/logo/vinc-ring-favicon.svg", "../Vinc-homepage/site/demo/favicon.svg"),
    ("assets/logo/vinc-ring-black.svg", "../Vinc-homepage/site/assets/vinc-mark.svg"),
    ("assets/logo/vinc-ring-white.svg", "../Vinc-homepage/site/assets/vinc-mark-white.svg"),
    ("assets/logo/vinc-ring-mono.svg", "../Vinc-homepage/site/assets/vinc-mark-mono.svg"),
    # 워드마크(2026-11-11). 사이트 상단 바는 글꼴 서브셋 대신 이 마스크를 쓴다: v·i·c 는 마크의
    # 규칙으로 다시 그린 글리프라 글꼴로는 나오지 않는다.
    # 근거: [[decision:vinc-wordmark-a2o-mark-rule-v-i-c-on-space-grotesk-2026-11-11]]
    ("assets/logo/wordmark/vinc-wordmark-mono.svg", "../Vinc-homepage/site/assets/vinc-wordmark-mono.svg"),
    # 이메일용 래스터. **메일 클라이언트는 SVG 를 못 받는다** — Gmail 은 `<svg>` 를
    # 통째로 걷어내고 Outlook 데스크탑도 안 그린다. 그래서 이 한 자리만 PNG 다.
    # 소스는 흰 램프 마크(이메일 셸 배경이 #0A0B10 로 어둡다), 2배 크기(46x64).
    # 다시 뽑는 법: npx @resvg/resvg-js-cli --fit-height 64 <svg> <png>
    # (resvg 는 `tauri icon` 이 쓰는 엔진과 같아 앱 아이콘과 렌더가 어긋나지 않는다)
    ("assets/logo/vinc-ring-white-email@2x.png",
     "../Vinc-homepage/site/assets/vinc-mark-email@2x.png"),
    # 잉크 마크. 메일이 밝은 배경으로 옮겨 가며 생겼다(2026-09-22).
    # ⚠ **위 흰 마크를 갈아 끼우지 않고 새 경로를 연 이유**: 메일의 이미지는 핫링크라
    # 그 URL 의 파일을 바꾸면 **이미 발송돼 받은편지함에 있는 메일이 전부 같이 바뀐다.**
    # 옛 어두운 메일에 잉크 마크가 얹히면 어두운 바탕에 어두운 마크가 되어 안 보인다.
    # 그래서 흰 마크는 그 자리에 영원히 남기고 새 템플릿만 이쪽을 가리킨다.
    # 흑백 로고 정책(VIN-104)으로 잉크판은 검정 링에서 뽑는다(build_logo.mjs). 같은
    # URL 을 인디고에서 검정으로 바꾸는 것은 안전하다: 이 마크를 받은 메일은 전부 밝은
    # 바탕이라 어두운 마크가 계속 보인다. 위의 경고는 바탕이 뒤집히는 경우의 이야기다.
    ("assets/logo/vinc-ring-ink-email@2x.png",
     "../Vinc-homepage/site/assets/vinc-mark-ink-email@2x.png"),
    # 검색엔진·수집기용 래스터 파비콘. **SVG 파비콘만으로는 부족하다** — 상당수가
    # `<link rel=icon>` 을 안 보고 루트의 `/favicon.ico` 만 집어 가고, 없으면 아이콘을
    # 아예 못 가져가거나 옛 캐시를 계속 쓴다(실측: 검색 결과에 구 로고가 남아 있었다).
    # .ico 는 16·32·48 PNG 를 담은 컨테이너다(scripts 주석 참조).
    ("assets/logo/vinc-ring-favicon.ico", "../Vinc-homepage/site/favicon.ico"),
    ("assets/logo/vinc-ring-favicon-48.png", "../Vinc-homepage/site/favicon-48.png"),
    # 로딩 모션 다섯 가지. 로고처럼 변환 없이 나른다. 앱은 모듈로 가져오고
    # 사이트는 `<script type="module">` 과 `<link>` 로 읽는다. 근거: [[vinc.design.loading-motions]]
    ("assets/motion/vinc-loaders.css", "app/src/vinc-loaders.css"),
    ("assets/motion/vinc-loaders.js", "app/src/vinc-loaders.js"),
    ("assets/motion/vinc-loaders.css", "../Vinc-homepage/site/assets/vinc-loaders.css"),
    ("assets/motion/vinc-loaders.js", "../Vinc-homepage/site/assets/vinc-loaders.js"),
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


def _foreign_repo(dst_rel: str):
    """소비 경로가 **다른 리포**를 가리키면 그 리포 이름을 돌려준다(아니면 None).

    `app/...` 은 같은 리포이고 `../Vinc-homepage/...` 만 남의 리포다 — 그것만 부재를
    허용한다."""
    if not dst_rel.startswith("../"):
        return None
    return dst_rel.split("/")[1]


_GENERATED: dict[str, str] | None = None


def _generated() -> dict[str, str]:
    global _GENERATED
    if _GENERATED is None:
        _GENERATED = build_tokens.outputs(build_tokens.load())
    return _GENERATED


def _source_exists(src_rel: str) -> bool:
    return src_rel.removeprefix("gen:") in _generated() if src_rel.startswith("gen:") \
        else (HUB / src_rel).exists()


def _source_text(src_rel: str) -> str:
    if src_rel.startswith("gen:"):
        return _generated()[src_rel.removeprefix("gen:")]
    return (HUB / src_rel).read_text(encoding="utf-8")


def _is_checkout(root: pathlib.Path) -> bool:
    """git 체크아웃의 루트인가. 메인은 `.git` 디렉터리, worktree 는 `.git` 파일이다.

    디렉터리가 **있다**는 것으로는 부족하다 — 예전 판이 만들어 놓은 빈 껍데기를
    소비 리포로 읽으면 사본이 다시 아무 데도 닿지 않는 자리에 쌓인다."""
    return (root / ".git").exists()


def _main_worktree():
    """허브가 속한 리포의 메인 워크트리 루트. git 이 없거나 리포 밖이면 None."""
    try:
        out = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=HUB,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    # 메인 체크아웃 안에서는 cwd 기준 상대경로(`../.git`)가, worktree 에서는 절대경로가 온다.
    return (HUB / out).resolve().parent


def _locate_repo(name: str):
    """소비 리포 `name` 의 git 체크아웃 루트를 찾는다 → (루트 또는 None, 시도한 자리들).

    1. 리포 루트 기준 `../<name>` — 메인 체크아웃에서는 이것이 맞다.
    2. 메인 워크트리의 형제 — worktree 에서는 1 이 `_wt/<name>` 로 풀린다."""
    tried: list[pathlib.Path] = []

    def candidates():
        yield (REPO / ".." / name).resolve()
        main = _main_worktree()
        if main is not None:
            yield (main.parent / name).resolve()

    for cand in candidates():
        if cand in tried:
            continue
        tried.append(cand)
        if _is_checkout(cand):
            return cand, tried
    return None, tried


def _resolve():
    """MAP 을 실제 경로로 푼다 → (행 목록, 못 찾은 리포 {이름: 시도한 자리들}).

    행은 (src_rel, dst_rel, dst) 이고, 소비 리포를 못 찾은 행의 dst 는 None 이다.
    찾은 리포가 허브 기준 상대경로가 아니면 어디로 풀었는지 한 줄 알린다."""
    found: dict[str, pathlib.Path] = {}
    missing: dict[str, list[pathlib.Path]] = {}
    rows = []
    for src_rel, dst_rel in MAP:
        name = _foreign_repo(dst_rel)
        if name is None:
            rows.append((src_rel, dst_rel, (REPO / dst_rel).resolve()))
            continue
        if name not in found and name not in missing:
            root, tried = _locate_repo(name)
            if root is None:
                missing[name] = tried
            else:
                found[name] = root
                if root != tried[0]:
                    print(f"[소비 리포] {name} → {root} (메인 워크트리 기준. "
                          f"{tried[0]} 는 {_why_not(tried[0])})")
        root = found.get(name)
        rows.append((src_rel, dst_rel,
                     root / dst_rel.split("/", 2)[2] if root is not None else None))
    return rows, missing


def _why_not(path: pathlib.Path) -> str:
    if not path.exists():
        return "없다"
    # 이 문구가 가리키는 것이 바로 그 사고의 잔해다 — 지우는 판단은 사람에게 맡긴다.
    return "있지만 git 체크아웃이 아니다(예전 sync_out 이 만든 껍데기일 수 있다)"


def _write_if_changed(src_rel: str, dst: pathlib.Path) -> bool:
    """내용이 달라졌을 때만 쓴다. 썼으면 True.

    텍스트는 **줄끝을 빼고** 비교한다(`read_text` 가 CRLF 를 LF 로 모은다). Windows
    체크아웃(autocrlf)의 사본은 CRLF 이고 여기서 쓰는 것은 LF 라, 같은 내용을 매번
    다시 쓰면 git 이 줄끝만 바뀐 파일을 수정으로 띄운다."""
    if dst.suffix.lower() in _BINARY_SUFFIXES:
        src = HUB / src_rel
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        return True
    text = _stamp(_source_text(src_rel), dst.suffix)
    if dst.exists() and dst.read_text(encoding="utf-8") == text:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8", newline="")
    return True


def _check() -> int:
    """소비처의 사본이 스냅샷에서 다시 만든 것·자산 원본과 **같은지** 검사한다(쓰지 않는다).

    이게 없던 동안 `--apply` 는 수동이었고 어느 CI 도 사본을 보지 않았다. 그래서
    누가 `Vinc/app/src/tokens.desktop.css` 를 직접 고쳐도(파일에 "수기 편집 금지"
    헤더가 있어도 헤더는 강제하지 않는다) 아무 데서도 안 걸렸다. 소비 리포 CI 가
    이걸 돌리면 드리프트가 머지 전에 잡힌다.

    exit 0 = 전부 일치 · 1 = 어긋남/결손.
    """
    bad = skipped = 0
    rows, _ = _resolve()
    for src_rel, dst_rel, dst in rows:
        if not _source_exists(src_rel):
            print(f"[MISSING·원본] {src_rel} — build_tokens.outputs() 나 assets/ 에 없다")
            bad += 1
            continue
        # 소비 리포가 **아예 없는** 것과 그 안의 파일이 없는 것은 다른 사실이다.
        # CI 는 자기 리포 하나만 체크아웃하므로 다른 리포는 원리상 검사할 수 없다 —
        # 그걸 실패로 세면 게이트가 CI 에서 영구히 빨갛고, 조용히 넘기면 "검사했다"는
        # 거짓이 된다. 그래서 **건너뛰되 세어서 출력한다**.
        if dst is None:
            print(f"[SKIP·리포 없음] {dst_rel} — {_foreign_repo(dst_rel)} 가 "
                  f"체크아웃돼 있지 않다")
            skipped += 1
            continue
        if not dst.exists():
            print(f"[MISSING·소비] {dst} — sync_out.py --apply 필요")
            bad += 1
            continue
        if dst.suffix.lower() in _BINARY_SUFFIXES:
            if (HUB / src_rel).read_bytes() == dst.read_bytes():
                print(f"[OK·바이너리] {dst_rel}")
            else:
                print(f"[DRIFT] {dst} 가 원본 {src_rel} 와 바이트가 다르다 — "
                      f"자산을 다시 뽑고 동기화하지 않았다")
                bad += 1
            continue
        a = _source_text(src_rel)
        b = dst.read_text(encoding="utf-8")
        stamped = _STAMP_RE.search(b) is not None
        if strip_stamp(a) == strip_stamp(b):
            if not stamped:
                print(f"[OK·무각인] {dst_rel} — 내용은 같으나 각인이 없다 "
                      f"(sync_out.py --apply 로 다시 내보내면 붙는다)")
                continue
            print(f"[OK] {dst_rel}")
        else:
            print(f"[DRIFT] {dst} 가 원본 {src_rel} 와 다르다 "
                  f"({len(a):,}자 vs {len(b):,}자) — 사본을 손으로 고쳤거나 "
                  f"스냅샷을 바꾸고 --apply 를 안 했다")
            bad += 1
    tail = f" · 검사 못 함 {skipped}건(리포 부재)" if skipped else ""
    print(f"\n{'드리프트 없음' if not bad else f'문제 {bad}건'}{tail}")
    return 1 if bad else 0


def _refusal(missing: dict) -> str:
    lines = []
    for name, tried in missing.items():
        lines.append(f"[거절] {name} 의 git 체크아웃을 못 찾았다. 찾아본 자리:")
        lines += [f"  - {p} ({_why_not(p)})" for p in tried]
    lines.append("소비 리포 디렉터리를 새로 만들지 않는다 — 리포가 아닌 자리에 쓴 사본은 "
                 "아무 데도 닿지 않는다. Vinc 옆에 클론한 뒤 다시 돌려라.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 복사(기본 dry-run)")
    ap.add_argument("--check", action="store_true",
                    help="복사하지 않고 사본이 허브와 같은지만 검사(CI 용)")
    a = ap.parse_args(argv)
    if a.check:
        if a.apply:
            print("--check 와 --apply 는 같이 못 쓴다", file=sys.stderr)
            return 2
        return _check()
    rows, unresolved = _resolve()
    # 한 행이라도 갈 곳이 없으면 **아무것도 쓰지 않는다** — 반만 나른 상태가 남지 않게.
    if a.apply and unresolved:
        print(_refusal(unresolved) + "\n아무것도 쓰지 않았다.", file=sys.stderr)
        return 2
    missing = copied = same = 0
    for src_rel, dst_rel, dst in rows:
        if not _source_exists(src_rel):
            print(f"[SKIP·미생성] {src_rel}")
            missing += 1
            continue
        if dst is None:
            print(f"[리포 없음] {dst_rel} — --apply 는 거절된다")
            continue
        if not a.apply:
            print(f"[DRY-RUN] {src_rel} → {dst}")
            continue
        binary = dst.suffix.lower() in _BINARY_SUFFIXES
        if _write_if_changed(src_rel, dst):
            copied += 1
            print(f"[COPIED{'·바이너리' if binary else '+각인'}] {src_rel} → {dst}")
        else:
            same += 1
            print(f"[SAME] {dst_rel} — 내용이 같아 쓰지 않았다")
    if a.apply:
        print(f"\n복사 {copied}건 · 같아서 건너뜀 {same}건")
    else:
        if unresolved:
            print("\n" + _refusal(unresolved))
        print("\n(dry-run — 실제 복사는 --apply. 검사만 하려면 --check)")
    return 1 if missing or unresolved else 0

if __name__ == "__main__":
    sys.exit(main())
