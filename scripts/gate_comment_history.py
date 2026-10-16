#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""주석 이력 예산 게이트: 주석 속 이력 표지가 파일마다 기준선을 넘지 않는가.

    python -X utf8 scripts/gate_comment_history.py                   # 검사
    python -X utf8 scripts/gate_comment_history.py --write-baseline  # 기준선 재생성
    BASE=<sha> HEAD=<sha> python -X utf8 scripts/gate_comment_history.py
                                     # 위반 파일에서 이 PR 이 더한 줄만 골라 보인다

exit 0=통과 · 1=초과 · 3=내부 오류.

무엇을 보나:

  - 추적되는(`git ls-files`) 비테스트 소스 파일의 **주석 줄**만 센다. 파이썬 `#` 와
    독스트링, TS/JS·Rust 의 `//` 와 `/* */`, CSS `/* */`, YAML `#`.
  - 그 줄이 `MARKERS` 중 하나라도 담으면 이력 표지 한 줄이다. 날짜·이슈 키·PR 번호와
    "언제 누가 무엇을 재고 정했나"를 말하는 어휘다.
  - 파일의 표지 줄 수가 `scripts/comment-history-baseline.json` 의 값보다 크면 BLOCK.
    기준선에 없는 파일은 `NEW_FILE_ALLOWANCE` 줄까지 허용한다.
  - 기준선은 래칫이다. 내려간 값은 `--write-baseline` 으로 다시 적어 잠근다. 기준선이
    현재 값보다 높으면 통과하고 낮출 수 있다고만 알린다.
  - 기준선에 있는데 리포에 없는 파일은 무시한다(지워진 파일은 위반이 아니다).

주석이 주장만 남기고 논증은 그래프로 보내는 계약: [[vinc.canon.code-annotation]]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

try:
    from scripts._root import ROOT
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT
BASELINE = ROOT / "scripts" / "comment-history-baseline.json"

#: 기준선에 없는 새 파일이 표지 줄을 이만큼까지는 가져도 된다.
NEW_FILE_ALLOWANCE = 3

_WORDS = ("발주자", "소유자 결정", "판정", "실측", "물렸", "재생산", "되감",
          "그때", "그날", "전에는", "종전", "옛 ", "구명", "레거시")
MARKERS = re.compile(
    r"2026-\d\d-\d\d"
    r"|VIN-\d+"
    r"|(?<![\w&])#\d{3,4}\b"
    r"|\bPR \d+"
    r"|" + "|".join(re.escape(w) for w in _WORDS)
)

_PY = {".py"}
_SLASH = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs"}
_CSS = {".css"}
_YAML = {".yml", ".yaml"}
EXTS = _PY | _SLASH | _CSS | _YAML

_SKIP_PREFIX = ("tests/", "_migration/", "design-hub/imports/")
_SKIP_NAMES = {"CHANGELOG.md"}


def wanted(rel: str) -> bool:
    """이 경로를 셀 대상으로 보는가. 테스트·이행 이력·생성 덤프는 뺀다."""
    if rel.startswith(_SKIP_PREFIX) or "/tests/" in f"/{rel}":
        return False
    name = rel.rsplit("/", 1)[-1]
    if name in _SKIP_NAMES or name.endswith(".json"):
        return False
    if name.startswith("test_") or ".test." in name or ".spec." in name:
        return False
    return os.path.splitext(name)[1].lower() in EXTS


# ── 주석 줄 뽑기: 각 함수는 {줄 번호: 그 줄의 주석 텍스트} 를 돌려준다 ──────────

def _add(out: dict[int, str], line: int, text: str) -> None:
    out[line] = (out.get(line, "") + " " + text).strip()


def comments_python(src: str) -> dict[int, str]:
    out: dict[int, str] = {}
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return comments_hash(src)
    prev = tokenize.NEWLINE
    for i, t in enumerate(toks):
        if t.type == tokenize.COMMENT:
            _add(out, t.start[0], t.string)
            continue
        if t.type == tokenize.STRING and prev in (
                tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING):
            nxt = next((u for u in toks[i + 1:]
                        if u.type not in (tokenize.COMMENT, tokenize.NL)), None)
            if nxt is None or nxt.type in (tokenize.NEWLINE, tokenize.ENDMARKER):
                # 문장 자리에 홀로 선 문자열 = 독스트링(또는 그에 준하는 설명문).
                for k, part in enumerate(t.string.splitlines()):
                    _add(out, t.start[0] + k, part)
        if t.type not in (tokenize.NL, tokenize.COMMENT):
            prev = t.type
    return out


def comments_hash(src: str) -> dict[int, str]:
    """YAML(그리고 run: 블록 속 셸) 의 `#` 주석. 따옴표 안의 `#` 은 건너뛴다."""
    out: dict[int, str] = {}
    for n, line in enumerate(src.splitlines(), 1):
        quote = ""
        for i, c in enumerate(line):
            if quote:
                if c == quote:
                    quote = ""
            elif c in "'\"":
                quote = c
            elif c == "#" and (i == 0 or line[i - 1] in " \t"):
                _add(out, n, line[i:])
                break
    return out


def comments_c_like(src: str, *, line_comments: bool, quotes: str,
                    js: bool = False) -> dict[int, str]:
    """`/* */` (와 `//`) 주석. 문자열 안의 기호는 건너뛴다.

    ``js=True`` (JS·TS) 면 두 가지를 더 안다. 템플릿 리터럴은 ``${…}`` 안에서 다시 코드가
    되고 그 안에 또 템플릿이 열릴 수 있으므로 스택으로 따라간다. 연산자 뒤의 ``/…/`` 는
    정규식 리터럴로 건너뛴다(안 그러면 ``/[&<>"]/`` 의 따옴표가 문자열을 열어 뒤의 주석을
    삼킨다). 정규식 판별은 앞 글자만 보는 근사라 드물게 틀리지만, 기준선도 같은
    스캐너로 만들므로 비교는 어긋나지 않는다."""
    out: dict[int, str] = {}
    n = len(src)
    i, line, depth = 0, 1, 0
    resume: list[int] = []  # 열린 `${` 하나마다, 그것을 연 자리의 중괄호 깊이

    def template(k: int) -> tuple[int, bool]:
        """템플릿 본문을 걷는다. (다음 위치, `${` 로 코드에 들어갔는가)."""
        nonlocal line
        while k < n:
            ch = src[k]
            if ch == "\\":
                if src[k + 1:k + 2] == "\n":
                    line += 1
                k += 2
                continue
            if ch == "\n":
                line += 1
            elif ch == "`":
                return k + 1, False
            elif ch == "$" and src[k + 1:k + 2] == "{":
                return k + 2, True
            k += 1
        return n, False

    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
        elif js and c == "`":
            i, entered = template(i + 1)
            if entered:
                resume.append(depth)
        elif js and c == "{":
            depth += 1
            i += 1
        elif js and c == "}":
            if resume and resume[-1] == depth:
                resume.pop()
                i, entered = template(i + 1)
                if entered:
                    resume.append(depth)
            else:
                depth -= 1
                i += 1
        elif c in quotes:
            j = i + 1
            while j < n and src[j] not in (c, "\n"):
                j += 2 if src[j] == "\\" and src[j + 1:j + 2] != "\n" else 1
            # 닫히지 않은 따옴표면 줄바꿈 앞에서 멈춘다. 줄바꿈은 위 갈래가 센다.
            i = j + 1 if j < n and src[j] == c else j
        elif line_comments and src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            _add(out, line, src[i:j])
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k, part in enumerate(src[i:j].split("\n")):
                _add(out, line + k, part)
            line += src.count("\n", i, j)
            i = j
        elif js and c == "/" and _regex_context(src, i):
            j, cls = i + 1, False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    cls = True
                elif src[j] == "]":
                    cls = False
                elif src[j] == "/" and not cls:
                    break
                j += 1
            # 줄 끝까지 닫히지 않았으면 정규식이 아니었다(나눗셈). 한 글자만 넘긴다.
            i = j + 1 if j < n and src[j] == "/" else i + 1
        else:
            i += 1
    return out


_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_WORDS = ("return", "typeof", "case", "in", "of", "void", "yield", "await")


def _regex_context(src: str, i: int) -> bool:
    """`/` 가 나눗셈이 아니라 정규식 리터럴을 여는 자리인가(앞의 의미 있는 글자로 본다)."""
    j = i - 1
    while j >= 0 and src[j] in " \t\r\n":
        j -= 1
    if j < 0 or src[j] in _REGEX_PREV:
        return True
    k = j
    while k >= 0 and (src[k].isalnum() or src[k] in "_$"):
        k -= 1
    return src[k + 1:j + 1] in _REGEX_WORDS


def comment_lines(rel: str, src: str) -> dict[int, str]:
    ext = os.path.splitext(rel)[1].lower()
    if ext in _PY:
        return comments_python(src)
    if ext in _YAML:
        return comments_hash(src)
    if ext in _CSS:
        return comments_c_like(src, line_comments=False, quotes="'\"")
    if ext == ".rs":
        # 러스트의 `'` 는 수명 표기(`'a`)라 문자열 경계로 쓰면 줄이 통째로 먹힌다.
        return comments_c_like(src, line_comments=True, quotes='"')
    return comments_c_like(src, line_comments=True, quotes="'\"", js=True)


def marker_lines(rel: str, src: str) -> list[tuple[int, str]]:
    """이력 표지를 담은 주석 줄 (줄 번호, 그 주석 텍스트)."""
    return [(ln, txt) for ln, txt in sorted(comment_lines(rel, src).items())
            if MARKERS.search(txt)]


# ── 리포 훑기 ────────────────────────────────────────────────────────────────

def tracked(root: Path) -> list[str]:
    res = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                         capture_output=True, check=True)
    return [p for p in res.stdout.decode("utf-8").split("\0") if p]


def scan(root: Path, files: list[str] | None = None) -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = {}
    for rel in (files if files is not None else tracked(root)):
        if not wanted(rel):
            continue
        p = root / rel
        if not p.is_file():
            continue
        src = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        hits = marker_lines(rel, src)
        if hits:
            out[rel] = hits
    return out


def load_baseline(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8")).get("files", {}))


def write_baseline(path: Path, found: dict[str, list[tuple[int, str]]]) -> None:
    data = {
        "_meta": {
            "what": "files -> history-marker comment lines, counted by scripts/gate_comment_history.py",
            "regenerate": "python -X utf8 scripts/gate_comment_history.py --write-baseline",
            "new_file_allowance": NEW_FILE_ALLOWANCE,
            "total": sum(len(v) for v in found.values()),
        },
        "files": {k: len(found[k]) for k in sorted(found)},
    }
    # newline 고정: 윈도우에서 CRLF 로 쓰면 리눅스 CI 와 줄 끝이 갈려 diff 가 통째로 난다.
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def compare(found: dict[str, list[tuple[int, str]]],
            base: dict[str, int]) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    """(초과, 낮출 수 있음). 각 항목은 (경로, 현재, 한도). 없어진 파일의 기준선은 무시한다."""
    over, lower = [], []
    for rel, hits in sorted(found.items()):
        limit = base.get(rel, NEW_FILE_ALLOWANCE)
        if len(hits) > limit:
            over.append((rel, len(hits), limit))
    for rel, limit in sorted(base.items()):
        now = len(found.get(rel, ()))
        if now < limit:
            lower.append((rel, now, limit))
    return over, lower


def added_lines(root: Path, rel: str) -> set[int] | None:
    """BASE..HEAD 에서 이 파일에 더해진 줄 번호. 알 수 없으면 None."""
    base, head = os.environ.get("BASE", ""), os.environ.get("HEAD", "")
    if not base:
        return None
    cmd = ["git", "diff", "-U0", base] + ([head] if head else []) + ["--", rel]
    try:
        res = subprocess.run(cmd, cwd=root, capture_output=True, check=True)
    except (subprocess.CalledProcessError, OSError):
        return None
    out: set[int] = set()
    for m in re.finditer(rb"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", res.stdout, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        out.update(range(start, start + count))
    return out


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="주석 이력 표지 예산 게이트")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args(argv)

    root = Path(a.root).resolve()
    bpath = Path(a.baseline) if a.baseline else root / "scripts" / "comment-history-baseline.json"
    try:
        found = scan(root)
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"[gate_comment_history] 내부 오류: {e}", file=sys.stderr)
        return 3
    total = sum(len(v) for v in found.values())

    if a.write_baseline:
        write_baseline(bpath, found)
        print(f"[gate_comment_history] 기준선을 썼다: 파일 {len(found)}개 · 표지 줄 {total}줄 → {bpath}")
        return 0

    base = load_baseline(bpath)
    over, lower = compare(found, base)
    if not over:
        print(f"[gate_comment_history] PASS: 표지 줄 {total}줄 (기준선 {sum(base.values())}줄, "
              f"파일 {len(found)}개)")
        if lower:
            print(f"  기준선보다 줄어든 파일 {len(lower)}개. `--write-baseline` 으로 낮춰 잠글 수 있다.")
        return 0

    print(f"[gate_comment_history] BLOCK: 이력 표지 주석이 기준선을 넘은 파일 {len(over)}개")
    for rel, now, limit in over:
        kind = "기준선" if rel in base else "새 파일 허용치"
        print(f"\n  {rel}: {now}줄 > {kind} {limit}줄")
        hits = found[rel]
        added = added_lines(root, rel)
        shown = [h for h in hits if added is None or h[0] in added] or hits
        for ln, txt in shown:
            print(f"    {rel}:{ln}  {txt.strip()[:160]}")
    print("\n날짜·이슈 키·PR 번호·판정 경위는 그래프(record·decision·episode)에 쓰고 주석은"
          " `[[id]]` 로 가리킬 것. 계약: vinc.canon.code-annotation")
    print("표지가 정당하게 줄었거나 옮겨졌다면 `--write-baseline` 으로 기준선을 다시 적는다"
          "(올리는 커밋은 리뷰에서 이유를 묻는다).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
