# -*- coding: utf-8 -*-
"""앱 소스를 글자로 읽는 pytest 파일들이 함께 쓰는 길잡이.

- 어디서: `UNITS` 가 이름 하나를 app/ 아래 파일들에 잇는다. 테스트는 경로가 아니라 이름으로
  읽는다(`read("view")`). 코드가 파일 사이로 옮겨 가면 이 표의 그 줄에 새 파일을 더한다:
  이름의 글은 파일들을 표의 순서대로 이은 것이라, 옮겨 간 코드도 같은 이름 아래서 보인다.
- 무엇을: `func` 는 선언 이름으로, `braced` 는 머리글로 중괄호 짝까지 잘라 낸다. 줄 번호 · 들여쓰기 ·
  옆 함수의 이름에 기대지 않으므로 함수가 다른 파일로 옮겨 가거나 바깥 함수에서 빠져나와도
  같은 조각이 나온다. 문자열 · 주석 · 템플릿 · 정규식 안의 중괄호와 괄호는 세지 않는다.
- 못 찾으면 무엇을 어느 이름(어느 파일들)에서 찾았는지와 고칠 자리를 말하며 실패한다.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
SRC = APP / "src"

#: 이름 → app/ 아래 파일들(이은 순서). 한 파일이 둘로 갈리면 그 이름에 둘을 다 적는다.
UNITS: dict[str, tuple[str, ...]] = {
    # 표면
    "view": ("src/surfaces/view.ts", "src/surfaces/view-host.ts", "src/surfaces/view-props-card.ts",
             "src/surfaces/view-ask.ts", "src/surfaces/view-rail.ts", "src/surfaces/view-cards.ts",
             "src/surfaces/view-edge.ts", "src/surfaces/view-reader.ts"),
    "view-compass": ("src/surfaces/view-compass.ts", "src/surfaces/view-compass-model.ts",
                     "src/surfaces/view-compass-gather.ts", "src/surfaces/view-compass-tree.ts",
                     "src/surfaces/view-compass-shapes.ts", "src/surfaces/view-compass-side.ts",
                     "src/surfaces/view-compass-whole-draw.ts"),
    "home": ("src/surfaces/home.ts",),
    "settings": ("src/surfaces/settings.ts",),
    "agents-board": ("src/surfaces/agents-board.ts",),
    # 셸과 공용 모듈
    "main": ("src/main.ts", "src/theme-apply.ts"),
    "api": ("src/api.ts", "src/api-types.ts", "src/api-config.ts", "src/api-transport.ts", "src/api-space.ts"),
    "copy": ("src/copy.ts",),
    "lnb": ("src/lnb.ts", "src/agent-roster.ts"),
    "lnb-tree": ("src/lnb-tree.ts",),
    "topbar": ("src/topbar.ts",),
    "components": ("src/components.ts",),
    "keys": ("src/keys.ts",),
    "live": ("src/live.ts",),
    "surface-binding": ("src/surface-binding.ts",),
    "theme": ("src/theme.ts",),
    "account-signals": ("src/account-signals.ts",),
    "account-connect": ("src/account-connect.ts",),
    "connect": ("src/connect.ts",),
    "firstrun": ("src/firstrun.ts",),
    "storage-mode": ("src/storage-mode.ts",),
    "device-graph": ("src/device-graph.ts",),
    # 문서 · 시트 · 도구
    "doc-editor": ("src/doc-editor.ts",),
    "doc-options": ("src/doc-options.ts",),
    "doc-delete": ("src/doc-delete.ts",),
    "sheets": ("src/sheets.ts",),
    "share-docs": ("src/share-docs.ts",),
    "tools": ("src/tools.ts",),
    "empty-invite": ("src/empty-invite.ts",),
    "vinc-loaders": ("src/vinc-loaders.js",),
    # 그래프 · 노드 · 역할
    "view-prefs": ("src/view-prefs.ts",),
    "view-tint": ("src/view-tint.ts",),
    "view-layout-spatial": ("src/view-layout-spatial.ts",),
    "topic-tint-table": ("src/topic-tint-table.ts",),
    "props-edit": ("src/props-edit.ts",),
    "node-basics": ("src/node-basics.ts",),
    "role-composer": ("src/role-composer.ts",),
    # 스타일 · 쪽 · 설정
    "app.css": ("src/app.css",),
    "vinc-loaders.css": ("src/vinc-loaders.css",),
    "index.html": ("index.html",),
    "vite.config": ("vite.config.ts",),
    "main.rs": ("src-tauri/src/main.rs",),
    "capabilities": ("src-tauri/capabilities/default.json",),
}

_HINT = "코드가 다른 파일로 옮겨 갔으면 tests/app/_src.py 의 UNITS 에서 그 이름에 새 파일을 더한다"


class Source(str):
    """이름 하나의 글. 보통 문자열이고, 실패 문구가 어디를 찾았는지 말하도록 이름과 파일을 단다."""

    unit: str
    files: tuple[str, ...]

    def __new__(cls, text: str, unit: str, files: tuple[str, ...]) -> "Source":
        self = super().__new__(cls, text)
        self.unit, self.files = unit, files
        return self


def read(*units: str) -> Source:
    """이름들의 글. 파일 하나면 그 파일 그대로이고, 여럿이면 표의 순서대로 줄바꿈 하나로 잇는다."""
    assert units, "읽을 이름이 없다"
    files: list[str] = []
    for unit in units:
        assert unit in UNITS, f"UNITS 에 없는 이름이다: {unit!r}. tests/app/_src.py 의 UNITS 에 그 파일을 적는다"
        files.extend(UNITS[unit])
    texts = []
    for rel in files:
        path = APP / rel
        assert path.is_file(), f"{'+'.join(units)} 의 파일이 없다: app/{rel}. {_HINT}"
        texts.append(path.read_text(encoding="utf-8"))
    return Source("\n".join(texts), "+".join(units), tuple(files))


def _where(src: str) -> str:
    if isinstance(src, Source):
        return f"이름 {src.unit!r}(app/{', app/'.join(src.files)})"
    return "주어진 조각"


def at(src: str, needle: str, start: int = 0) -> int:
    """`str.index` 와 같되, 없으면 무엇이 어디서 사라졌는지 말하며 실패한다."""
    i = src.find(needle, start)
    assert i >= 0, f"{_where(src)} 에서 사라졌다: {needle!r}. {_HINT}"
    return i


def cut(src: str, start: str, end: str) -> str:
    """`start` 부터 그 뒤 첫 `end` 앞까지. 끝 표지가 같은 블록 안의 글일 때 쓴다."""
    a = at(src, start)
    b = src.find(end, a)
    assert b >= 0, f"{_where(src)} 에서 {start!r} 뒤의 끝 표지 {end!r} 가 없다"
    return src[a:b]


# ── 글자 훑기: 코드인 글자의 자리만 내놓는다(문자열 · 주석 · 템플릿 · 정규식은 건너뛴다) ──

#: 이 글자 뒤의 `/` 는 나눗셈이 아니라 정규식의 시작이다.
_REGEX_AFTER = set("(,=:[!&|?{};~+-*%<>^")
_REGEX_WORDS = {"return", "typeof", "case", "do", "else", "in", "of", "void", "yield", "await",
                "delete", "instanceof", "new", "throw"}


def _quoted(src: str, i: int) -> int:
    """따옴표 문자열의 끝 다음 자리. 닫히지 않으면 그 줄 끝에서 멈춘다(한 글자 오판이 파일 끝까지 번지지 않게)."""
    q, j, n = src[i], i + 1, len(src)
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
        elif c == q:
            return j + 1
        elif c == "\n":
            return j
        else:
            j += 1
    return n


def _template(src: str, i: int) -> int:
    """백틱 템플릿의 끝 다음 자리. `${ … }` 안은 코드로 짝을 찾는다."""
    j, n = i + 1, len(src)
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
        elif c == "`":
            return j + 1
        elif src.startswith("${", j):
            j = _match(src, j + 1, "{", "}") + 1
        else:
            j += 1
    return n


def _regex(src: str, i: int) -> int:
    """정규식 글자의 끝 다음 자리(플래그까지). 줄이 끝나도록 닫히지 않으면 정규식이 아니었다고 보고 한 글자만 넘긴다."""
    j, n, cls = i + 1, len(src), False
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            return i + 1
        if c == "[":
            cls = True
        elif c == "]":
            cls = False
        elif c == "/" and not cls:
            j += 1
            while j < n and src[j].isalpha():
                j += 1
            return j
        j += 1
    return i + 1


def _regex_can_start(src: str, i: int, prev: str) -> bool:
    if not prev or prev in _REGEX_AFTER:
        return True
    if prev.isalnum() or prev in "_$":
        m = re.search(r"([A-Za-z_$][\w$]*)\s*$", src[max(0, i - 16):i])
        return bool(m and m.group(1) in _REGEX_WORDS)
    return False


def _code(src: str, i: int, css: bool = False):
    """`i` 부터 코드인 글자의 자리를 차례로 낸다. `css` 이면 `/* */` 와 따옴표만 건너뛴다."""
    n, prev = len(src), ""
    while i < n:
        c = src[i]
        if c in "\"'":
            i, prev = _quoted(src, i), "a"
            continue
        if src.startswith("/*", i):
            k = src.find("*/", i + 2)
            i = n if k < 0 else k + 2
            continue
        if not css:
            if src.startswith("//", i):
                k = src.find("\n", i)
                i = n if k < 0 else k
                continue
            if c == "`":
                i, prev = _template(src, i), "a"
                continue
            if c == "/" and _regex_can_start(src, i, prev):
                k = _regex(src, i)
                if k > i + 1:
                    i, prev = k, "a"
                    continue
        yield i
        if not c.isspace():
            prev = c
        i += 1


def _match(src: str, i: int, open_: str, close: str, css: bool = False) -> int:
    """`src[i]` 의 여는 글자와 짝인 닫는 글자의 자리."""
    depth = 0
    for j in _code(src, i, css):
        c = src[j]
        if c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return j
    raise AssertionError(f"{_where(src)} 의 {src[i:i + 60]!r} 에서 연 {open_!r} 가 닫히지 않는다")


# ── 조각 찾기 ──

def braced(src: str, head: str, *, inner: bool = False) -> str:
    """`head` 가 처음 나오는 자리부터, 그 자리 뒤 첫 `{` 와 짝인 `}` 까지(`inner` 면 중괄호 안쪽만).

    `head` 는 여는 중괄호 앞의 글이다: 콜백을 받는 호출(`document.addEventListener("keydown"`),
    객체(`const handle: DocEditorHandle = {`), 화살표(`const onKey = (e: KeyboardEvent) => {`).
    이름으로 찾는 선언은 `func` 가 낫다(매개변수 안의 `{` 를 건너뛴다)."""
    a = at(src, head)
    for j in _code(src, a):
        if src[j] == "{":
            end = _match(src, j, "{", "}")
            return src[j + 1:end] if inner else src[a:end + 1]
    raise AssertionError(f"{_where(src)} 의 {head!r} 뒤에 여는 중괄호가 없다")


#: `func` 가 아는 선언 꼴: 함수 선언과, 함수 · 화살표를 담는 const/let.
_DECLS = (
    r"(?<![\w$.])(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+{n}\s*(?:<[^>(]*>)?\s*\(",
    r"(?<![\w$.])(?:export\s+)?(?:const|let|var)\s+{n}\s*(?::[^=\n]+)?=\s*(?:async\s+)?(?:function\b[^(]*)?\(",
    r"(?<![\w$.])(?:export\s+)?(?:const|let|var)\s+{n}\s*(?::[^=\n]+)?=\s*(?:async\s+)?[A-Za-z_$][\w$]*\s*=>",
)


def _body_start(src: str, k: int, arrow: bool) -> tuple[str | None, int]:
    """매개변수 뒤에서 반환 형식을 지나 몸통의 시작을 찾는다: ("{", 자리) · ("=>", 자리) · (None, 자리).

    반환 형식 안의 `{ … }`(객체 형식) · `<…>` · `() => T`(함수 형식)는 몸통으로 읽지 않는다."""
    angle, prev = 0, ")"
    while True:
        for j in _code(src, k):
            c = src[j]
            if c.isspace():
                continue
            if src.startswith("=>", j):
                if arrow and angle == 0:
                    return "=>", j
                prev, k = ":", j + 2
                break
            if c in "([" or (c == "{" and (angle > 0 or prev in ":|&,<(")):
                close = {"(": ")", "[": "]", "{": "}"}[c]
                prev, k = close, _match(src, j, c, close) + 1
                break
            if c == "{":
                return "{", j
            if c == ";":
                return None, j
            if c == "<":
                angle += 1
            elif c == ">":
                angle -= 1
            prev = c
        else:
            return None, len(src)


def _decl_end(src: str, m: re.Match) -> int | None:
    """선언의 끝 다음 자리. 몸통이 없는 선언(겹쳐 쓰기 서명)은 None."""
    k = m.end()
    if src[k - 1] == "(":
        arrow = not re.search(r"\bfunction\b", m.group(0))
        kind, j = _body_start(src, _match(src, k - 1, "(", ")") + 1, arrow)
        if kind is None:
            return None
        if kind == "{":
            return _match(src, j, "{", "}") + 1
        k = j + 2
    while k < len(src) and src[k].isspace():
        k += 1
    if src[k:k + 1] == "{":
        return _match(src, k, "{", "}") + 1
    # 식 몸통의 화살표: 깊이 0 의 `;` 까지
    depth = 0
    for j in _code(src, k):
        c = src[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth < 0:
                return j
        elif c == ";" and depth == 0:
            return j + 1
    return len(src)


def func(src: str, name: str) -> str:
    """선언 이름 `name` 의 함수 전체(선언 첫 글자부터 몸통의 닫는 중괄호까지).

    `function name(` · `export async function name(` · `const name = (…) => {` · `const name = x => …`
    어느 꼴이든, 몇 칸 들여 썼든 같다. 이름의 선언이 둘이면 어느 것인지 모르므로 실패한다
    (그때는 `braced` 에 머리글을 준다)."""
    hits = set()
    for pat in _DECLS:
        for m in re.finditer(pat.format(n=re.escape(name)), src):
            end = _decl_end(src, m)
            if end is not None:
                hits.add((m.start(), end))
    assert hits, f"{_where(src)} 에 {name} 의 선언이 없다. {_HINT}"
    assert len(hits) == 1, (
        f"{_where(src)} 에 {name} 의 선언이 {len(hits)} 개다"
        f"(줄 {sorted(src.count(chr(10), 0, a) + 1 for a, _ in hits)}). braced() 에 머리글을 주어 하나를 고른다")
    a, b = hits.pop()
    return src[a:b]


# ── CSS ──

def css_rule(css: str, selector: str) -> str:
    """줄 첫머리가 정확히 `selector {` 인 첫 규칙(선택자부터 닫는 중괄호까지)."""
    m = re.search(r"(?m)^" + re.escape(selector) + r" \{[^}]*\}", css)
    assert m, f"{_where(css)} 에 {selector} 규칙이 없다. {_HINT}"
    return m.group(0)


def css_block(css: str, prelude: str) -> str:
    """`@media …` · `@container …` 처럼 `prelude` 로 여는 블록의 안쪽(중괄호 짝을 센다)."""
    a = at(css, prelude)
    i = css.find("{", a)
    assert i >= 0, f"{_where(css)} 의 {prelude!r} 뒤에 여는 중괄호가 없다"
    return css[i + 1:_match(css, i, "{", "}", css=True)]
