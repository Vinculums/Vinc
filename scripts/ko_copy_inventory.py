#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""한국어 카피 재고 조사 — 덱 밖 한글 · 죽은 덱 키.

정본은 그래프 문서 `vinc.spec.ui-copy` §7(덱 밖에 두는 문자열) · §8(죽은 키) 과
`vinc.spec.ui-copy-ko` §8(전수 범위)이다. 이 스크립트는 그 두 규칙의 실행체다.

**목록을 문서로 적어 두지 않는 이유**: 파일에 적은 목록은 다음 커밋에 조용히
틀려진다(§8 이 이미 그렇게 낡았다). 목록을 만드는 장치를 남기고 목록은 그때그때
다시 센다.

**죽은 키 규칙**(`dead_deck_keys`): 덱의 잎은 `app/src` 가 그것을 **속성으로**
읽어야 산다. `.잎`·`?.잎`, 구조 분해 이름, `["잎"]` 중 하나다. 이름이 어디엔가
글자로 있기만 하면 살려 주던 옛 판정은 같은 이름의 지역 변수 하나(`local`·`you`)에
죽은 키를 숨겼다. 예외는 셋이다. 덱 함수가 스스로 찾는 괄호 안 조회표의 키,
부모 객체가 통째로 `표[k]` 로 읽히고 그 키 이름이 `app/src` 에 문자열로 적힌 잎,
그리고 키가 서버에서 오는 표(`SERVER_CODE_TABLES`)의 잎이다. 부모가 대괄호로
읽힌다는 사실만으로 자식 전부를 살리지 않는다. `const T = COPY.ui.tools; T[k]`
하나가 `k` 로 절대 오지 않는 키까지 덮었기 때문이다(VIN-548).

두 소비자가 이 모듈을 읽는다:
  · `tests/design/test_copy_parity.py` — 회귀를 잠근다(덱 밖 한글 0 · 죽은 키 0)
  · `scripts/gate_forbidden.py` — F9(한국어 금지 어휘)가 주석을 걸러 내는 데
    `strip_comments` 를 그대로 쓴다

stdlib 전용.

    python -X utf8 scripts/ko_copy_inventory.py [--root .] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HANGUL = re.compile(r"[가-힣ㄱ-ㆎ]")

# `//` 주석. 문자열 안의 `https://` 를 주석으로 오인하지 않도록 앞이 공백·구두점일
# 때만 자른다(URL 은 `:` 뒤에 붙는다).
_LINE_COMMENT = re.compile(r"(?:^|(?<=[\s;,)}\]]))//.*$")

# 한글이 있어도 카피가 아닌 두 형태. 판정 기준은 §7 의 한 줄이다 —
# "번역하면 소프트웨어의 동작이 달라지는 것은 카피가 아니다".
#   · 정규식 문자 클래스의 한글 음절 범위(`가-힣`)는 문법이지 문장이 아니다.
#   · `console.*` 는 화면이 아니라 개발자 콘솔로 나간다. 이 리포의 주석 언어가
#     한국어이므로(CLAUDE.md) 같은 언어로 적힌 진단문이고, 덱으로 옮길 대상이 아니다.
#   · **언어 선택기의 언어 이름**은 그 언어로 적힌다. `한국어` 를 덱에 넣으면 영어
#     화면에서 "Korean" 으로 바뀌는데, 그러면 영어를 못 읽는 사람이 자기 언어를 못
#     찾는다 — 번역이 동작을 바꾸는 바로 그 경우다. 판별자는 `data-lang-opt` 으로
#     좁힌다(그 속성을 내는 곳은 Settings 의 언어 세그 하나뿐이다).
_SYNTAX_RANGE = re.compile(r"[가-힣]\s*-\s*[가-힣]")
_CONSOLE = re.compile(r"\bconsole\s*\.\s*\w+\s*\(")
_LANG_PICKER = re.compile(r"data-lang-opt")

_SCAN_GLOBS = ("app/src/**/*.ts", "app/src/**/*.css")
_SCAN_FILES = ("app/index.html",)
_DECK = "app/src/copy.ts"
# 덱이 쓰는 한국어 문법(조사 표). 카피가 아니라 덱의 일부라 덱과 같이 훑지 않는다(VIN-259).
_GRAMMAR = ("app/src/josa.ts",)


@dataclass
class Hit:
    file: str
    line: int
    text: str
    verdict: str          # "deck-eligible" | "exempt: <사유>"

    @property
    def eligible(self) -> bool:
        return self.verdict == "deck-eligible"


def strip_comments(text: str):
    """(줄번호, 주석을 걷어 낸 코드) 를 차례로 내놓는다.

    줄 단위 상태기다. 토크나이저를 쓰지 않는 이유는 실측이다: 문자열을 추적하는
    스캐너는 정규식 리터럴(`/[^a-z]+/g`)을 나눗셈으로 읽고 그 뒤를 통째로
    문자열로 오인한다 — 실제로 오탐 400건이 그렇게 나왔다. 블록(`/* */`)과
    HTML(`<!-- -->`) 주석만 상태로 좇으면 그 함정이 없고, 이 게이트가 잡아야 하는
    것(주석 밖 한글)에는 그것으로 충분하다.
    """
    in_block = False
    in_html = False
    for lineno, line in enumerate(text.splitlines(), 1):
        out: list[str] = []
        pos = 0
        while pos < len(line):
            if in_block:
                end = line.find("*/", pos)
                if end < 0:
                    break
                in_block = False
                pos = end + 2
                continue
            if in_html:
                end = line.find("-->", pos)
                if end < 0:
                    break
                in_html = False
                pos = end + 3
                continue
            nb = line.find("/*", pos)
            nh = line.find("<!--", pos)
            cands = [c for c in (nb, nh) if c >= 0]
            # 줄 주석이 먼저 오면 그 뒤의 `/*` 는 주석 글자일 뿐이다. 이걸 안 보면
            # `// 디스커버리(/.well-known/*)` 한 줄이 블록을 열어 다음 `*/` 까지
            # 코드 100줄을 통째로 삼킨다(실측: settings.ts).
            lc = _LINE_COMMENT.search(line, pos)
            if lc and (not cands or lc.start() < min(cands)):
                out.append(line[pos:lc.start()])
                break
            if not cands:
                out.append(line[pos:])
                break
            nxt = min(cands)
            out.append(line[pos:nxt])
            if nxt == nb:
                in_block, pos = True, nxt + 2
            else:
                in_html, pos = True, nxt + 4
        yield lineno, _LINE_COMMENT.sub("", "".join(out))


def _verdict(code: str) -> str:
    if _CONSOLE.search(code):
        return "exempt: console diagnostic, never rendered"
    if _LANG_PICKER.search(code):
        return "exempt: language picker names each language in itself"
    if not HANGUL.search(_SYNTAX_RANGE.sub("", code)):
        return "exempt: regex character class, syntax not copy"
    return "deck-eligible"


def _scan_targets(root: Path):
    seen: set[Path] = set()
    for pat in _SCAN_GLOBS:
        for p in sorted(root.glob(pat)):
            if p.as_posix().endswith((_DECK, *_GRAMMAR)) or p in seen:
                continue
            seen.add(p)
            yield p
    for rel in _SCAN_FILES:
        p = root / rel
        if p.exists():
            yield p


# 이어지는 줄: 앞 줄의 문장이 아직 안 끝났다는 표시로 시작하는 줄.
_CONTINUATION = re.compile(r"^\s*[+,).?:]")


def hangul_outside_deck(root: Path) -> list[Hit]:
    """덱 밖에서 화면에 닿을 수 있는 한글. 주석은 제외한다(주석 언어는 한국어다)."""
    hits: list[Hit] = []
    for path in _scan_targets(root):
        rel = path.relative_to(root).as_posix()
        prev: list[str] = []
        for lineno, code in strip_comments(path.read_text(encoding="utf-8")):
            if HANGUL.search(code):
                # 면제 판정은 **문장** 단위로 본다. `console.warn(` 이 한 줄에 있고
                # 인자가 다음 줄로 넘어가면, 줄만 보는 판정은 그 다음 줄을 화면
                # 문자열로 오독한다(실측: view.ts 의 두 줄짜리 경고 하나).
                subject = code
                if _CONTINUATION.match(code):
                    subject = " ".join(prev[-3:] + [code])
                hits.append(Hit(rel, lineno, code.strip()[:180], _verdict(subject)))
            if code.strip():
                prev.append(code)
    return hits


# ── 덱 키 ────────────────────────────────────────────────────────────────────
_IDENT_COLON = re.compile(r"([A-Za-z_$][\w$]*)\s*:")
_INLINE = "\0inline"

# 키가 서버에서 오는 표. 앱 코드에 그 키 이름이 문자열로도 적히지 않으므로, 표를
# 통째로 받아 `표[코드]` 로 읽는 곳이 있으면 자식 전부를 산 것으로 친다. 여기 없는
# 표는 키 이름이 `app/src` 어딘가에 문자열로 적혀 있어야 산다(`dead_deck_keys`).
# 목록이 낡지 않게 `tests/design/test_copy_parity.py` 가 셋 모두 덱에 있고 통째로
# 읽히는지 확인한다.
SERVER_CODE_TABLES = frozenset({
    ("ui", "intakeUi", "docError"),                      # 문서 추출 거부 사유 코드
    ("ui", "settingsUi", "folderBad"),                   # 저장 폴더 검사 사유 코드
    ("ui", "settingsUi", "conversionDocumentEvidence"),  # 주제 이전 점검의 문서 집계 키
    ("ui", "viewx", "seg"),                              # 원장 구간 키(api/query.py _LEDGER_MARKS)
})


def _walk(lines: list[str], start: int, end: int):
    """덱 한 벌을 훑어 (키 경로, 줄번호, 객체인가, 덱 안 조회표인가) 를 내놓는다.

    들여쓰기가 아니라 **중괄호 깊이**로 센다. 들여쓰기로 세면 한 줄에 둘 이상 있는
    키를 못 본다 — `importTab: "Import", shareTab: "Share",` 에서 둘째가 통째로
    시야 밖이었고, 첫째를 지우자 그제서야 죽은 키로 드러났다(실측). 죽은 키가
    남의 줄 뒤에 숨는 것이야말로 이 검사가 잡아야 할 것이다.

    전제 둘을 실측으로 확인하고 쓴다: 덱 안에 블록 본문 화살표 함수(`=> {`)가
    0 건이므로 여기의 모든 `{` 는 객체 리터럴이고, 매개변수 목록은 `(` 안이라
    깊이로 갈린다(`(n: number, m: number)` 의 `m` 을 키로 오인하지 않는다).
    """
    text = "\n".join(lines[start - 1:end])
    base_line = start
    stack: list[str] = []          # 열려 있는 객체의 키 경로
    containers: list[str] = []     # 열려 있는 괄호 종류
    pending: str | None = None     # 바로 다음 `{` 가 이 키의 객체다
    i, n, lineno = 0, len(text), base_line
    while i < n:
        c = text[i]
        if c == "\n":
            lineno += 1
            i += 1
            continue
        if c in "'\"`":
            q, i = c, i + 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == "\n":
                    lineno += 1
                elif text[i] == q:
                    i += 1
                    break
                elif q == "`" and text[i] == "$" and text[i + 1:i + 2] == "{":
                    d, i = 1, i + 2
                    while i < n and d:
                        if text[i] == "{":
                            d += 1
                        elif text[i] == "}":
                            d -= 1
                        elif text[i] == "\n":
                            lineno += 1
                        i += 1
                    continue
                i += 1
            continue
        if c == "/" and text[i + 1:i + 2] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and text[i + 1:i + 2] == "*":
            end_c = text.find("*/", i)
            end_c = n if end_c < 0 else end_c + 2
            lineno += text.count("\n", i, end_c)
            i = end_c
            continue
        if c in "([":
            containers.append(c)
            i += 1
            continue
        if c == "{":
            # 괄호 안에서 이름 없이 열린 객체(`({ code: "…" })[code]`)는 덱의 키가
            # 아니라 덱 함수가 스스로 찾는 조회표다. 경로에는 안 남기고 표시만 한다.
            inline = pending is None and bool(containers) and containers[-1] == "("
            containers.append("{")
            stack.append(_INLINE if inline else (pending or ""))
            pending = None
            i += 1
            continue
        if c in ")]":
            if containers:
                containers.pop()
            i += 1
            continue
        if c == "}":
            if containers:
                containers.pop()
            if stack:
                stack.pop()
            i += 1
            continue
        # 객체 본문(가장 안쪽 컨테이너가 `{`)에서만 키를 읽는다
        if containers and containers[-1] == "{":
            m = _IDENT_COLON.match(text, i)
            if m:
                path = tuple(s for s in stack[1:] if s and s != _INLINE) + (m.group(1),)
                j = m.end()
                while j < n and text[j] in " \t":
                    j += 1
                is_obj = j < n and text[j] == "{"
                yield path, lineno, is_obj, _INLINE in stack
                if is_obj:
                    pending = m.group(1)
                i = m.end()
                continue
        i += 1


def _deck_bounds(lines: list[str]) -> tuple[int, int, int, int]:
    en = next(i for i, l in enumerate(lines, 1) if l.startswith("const EN = {"))
    ko = next(i for i, l in enumerate(lines, 1) if l.startswith("const KO: Deck = {"))
    ko_end = next(i for i, l in enumerate(lines, 1) if i > ko and l == "};")
    return en, ko - 3, ko, ko_end


def _code_only(text: str) -> str:
    return "\n".join(code for _, code in strip_comments(text))


_IDENT = r"[A-Za-z_$][\w$]*"


def _names_read(blob: str) -> set[str]:
    """`blob` 이 **속성으로** 읽는 이름들: `.이름`·`?.이름`, 구조 분해, `["이름"]`.

    맨 이름 일치는 증거가 아니다. `local`·`you`·`header` 같은 잎은 같은 이름의
    지역 변수·매개변수·속성 키 하나만 있어도 살아남았다. 잎마다 정규식을 돌리지
    않고 한 번 훑어 집합으로 만든다(잎이 천 개를 넘는다).
    """
    names = set(re.findall(r"(?<![.])\.\s*(" + _IDENT + ")", blob))  # `...x` 는 스프레드
    for body in re.findall(r"\{([^{}=]*)\}\s*=", blob):
        names.update(re.findall(_IDENT, body))
    names.update(re.findall(r"\[\s*[\"'`](" + _IDENT + r")[\"'`]\s*\]", blob))
    return names


def _string_literals(blob: str) -> set[str]:
    """문자열 리터럴 그 자체가 식별자 모양인 것들(`"create"`)."""
    return set(re.findall(r"(?<=[\"'`])(" + _IDENT + r")(?=[\"'`])", blob))


def _read_whole(name: str, sources: list[str]) -> bool:
    """덱 객체 `name` 을 통째로 받아 동적으로 읽는 곳이 있는가.

    세 모양을 센다: `….name[k]`(형 단언 `(….name as T)[k]` 포함), 함수에 통째로
    넘기기(`f(R.tiers)`), 지역 변수에 받아 `X[k]`. 마지막 것은 대입과 대괄호가
    **같은 파일**에 있어야 인정한다. 리포 전체를 한 덩어리로 보면 `P`·`T` 같은
    한 글자 지역명이 남의 파일 대괄호와 우연히 맞아떨어진다(실측: 오탐 3건).
    """
    e = re.escape(name)
    direct = re.compile(
        r"(?<![.])\.\s*" + e + r"\s*(?:as\s+[^;)\n]*\)\s*)?\[(?!\s*\])"
        r"|(?<![.])\.\s*" + e + r"\s*[,)]")
    alias = re.compile(
        r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)[^=\n]*=\s*[^;]*?\." + e
        + r"\s*(?:as\s+[^;]*)?;", re.S)
    for src in sources:
        if direct.search(src):
            return True
        for m in alias.finditer(src):
            if re.search(r"(?<![\w$.])" + re.escape(m.group(1))
                         + r"\s*\[(?!\s*\])", src):
                return True
    return False


def dead_deck_keys(root: Path) -> list[tuple[str, int, int]]:
    """호출부가 0 인 덱 키. (키 경로, EN 줄, KO 줄).

    잎 하나가 산 것으로 치는 길은 넷이다. 차례로 본다.

    1. **속성 접근**: `app/src` 가 그 잎을 `.잎`·`?.잎`, 구조 분해 이름,
       `["잎"]` 으로 읽는다. 맨 이름이 어디에든 있기만 하면 된다는 옛 판정은
       `ui.common.local`·`ui.ask.you`·`records.actions.pin` 을 같은 이름의 지역
       변수·속성 하나로 살려 두었다.
    2. **덱 안 조회표**: 덱 함수가 괄호 안 객체를 스스로 찾는다
       (`({ 코드: "…" } as Record<string, string>)[code]`). 그 키는 덱 바깥에서
       읽히지 않는 것이 정상이다(`settingsUi` 의 이전 차단 코드).
    3. **부모를 통째로 읽고, 키 이름이 문자열로 적혀 있다**: 부모 객체를
       `표[k]` 로 읽거나 통째로 넘기는 곳이 있고(`_read_whole`), 잎 이름이
       `app/src` 어딘가에 문자열 리터럴로 있다. 키는 어디선가 와야 한다. 옛 판정은
       부모가 대괄호로 읽히기만 하면 자식 전부를 살렸는데, `const T = COPY.ui.tools;
       T[k]` 하나가 `k` 로 절대 오지 않는 `ui.tools.saveFirst` 까지 덮었다.
    4. **서버 코드 표**: 키가 서버에서 오는 표(`SERVER_CODE_TABLES`)는 부모가
       통째로 읽히기만 하면 자식 전부가 산다. 그 키는 앱 코드에 적히지 않는다.

    주석은 걷어 내고 본다. 주석에 이름이 남아 있다고 키가 사는 것은 아니다.
    """
    copy_ts = root / _DECK
    lines = copy_ts.read_text(encoding="utf-8").splitlines()
    en_s, en_e, ko_s, ko_e = _deck_bounds(lines)

    walked_en = list(_walk(lines, en_s, en_e))
    walked_ko = list(_walk(lines, ko_s, ko_e))
    en = {p: i for p, i, _, _ in walked_en}
    ko = {p: i for p, i, _, _ in walked_ko}
    objects = {p for p, _, is_obj, _ in walked_en + walked_ko if is_obj}
    inline = {p for p, _, _, inl in walked_en + walked_ko if inl}

    sources = [
        _code_only(p.read_text(encoding="utf-8"))
        for p in sorted((root / "app" / "src").rglob("*.ts"))
        if p.name != "copy.ts"
    ]
    blob = "\n".join(sources)
    read, literals = _names_read(blob), _string_literals(blob)

    whole: dict[str, bool] = {}

    def parent_read_whole(parent: tuple[str, ...]) -> bool:
        if parent[-1] not in whole:
            whole[parent[-1]] = _read_whole(parent[-1], sources)
        return whole[parent[-1]]

    dead = []
    for path in sorted(set(en) | set(ko)):
        if path in objects:
            continue                                     # 잎만 판정한다
        leaf, parent = path[-1], path[:-1]
        if leaf in read or path in inline:
            continue
        if parent and parent_read_whole(parent) and (
                parent in SERVER_CODE_TABLES
                or leaf in literals):
            continue
        dead.append((".".join(path), en.get(path, 0), ko.get(path, 0)))
    return dead


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="한국어 카피 재고 조사")
    ap.add_argument("--root", default=".", help="리포 루트(기본 cwd)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    hits = hangul_outside_deck(root)
    dead = dead_deck_keys(root)

    if args.json:
        print(json.dumps({
            "hangul_outside_deck": [h.__dict__ for h in hits],
            "dead_deck_keys": [{"key": k, "en": e, "ko": o} for k, e, o in dead],
        }, ensure_ascii=False, indent=2))
        return 0

    eligible = [h for h in hits if h.eligible]
    print(f"## Hangul outside the deck ({len(hits)} hits, "
          f"{len(eligible)} deck-eligible)\n")
    print("| file:line | verdict | text |")
    print("|---|---|---|")
    for h in hits:
        print(f"| `{h.file}:{h.line}` | {h.verdict} | `{h.text[:110]}` |")
    print(f"\n## Dead deck keys ({len(dead)})\n")
    print("| key | EN line | KO line |")
    print("|---|---|---|")
    for k, e, o in dead:
        print(f"| `{k}` | {e} | {o} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
