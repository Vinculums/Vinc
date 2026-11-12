# -*- coding: utf-8 -*-
"""`vinc_mcp/` 에서 urllib 의 기본 opener 를 쓰는 자리는 아래 허용 목록뿐이다.

기본 urlopen 은 리다이렉트를 따르고, 따라가면서 Authorization 같은 헤더를 Location 의 호스트로
그대로 보낸다. 밖으로 나가는 요청은 `hubhttp` 의 opener 를 지나거나, 확인한 주소로 붙는
전송(`netguard`)을 쓴다. 근거: [[record:hub-http-clients-refuse-redirects-2026-10-16]]

잡는 것: `urllib.request` 의 `urlopen`·`build_opener`·`OpenerDirector`·`urlretrieve`·
`install_opener` 를 가리키는 **모든 참조**(부르기만이 아니라 이름을 넘기거나 담는 것도). 모듈을
다른 이름으로 들이거나(`import urllib.request as x`, `from urllib import request`) 함수를 다른
이름으로 들인 것(`from urllib.request import urlopen as y`, `*`)도 따라간다.

잡지 않는 것(일부러): `getattr(urllib.request, "urlopen")`·`importlib` 같은 동적 접근, 그리고
`requests`·`httpx`·`urllib3` 같은 다른 라이브러리. 정적 검사로는 끝이 없고, 이 패키지는 표준
라이브러리만 쓴다(F5). 그 자리는 리뷰가 본다.

세기만 하고 실패시키지 않는 것: `http.client` 의 연결 클래스와 `socket.create_connection`.
리다이렉트를 따르지 않지만 주소 확인 없이 아무 데나 붙을 수 있다. 쓰는 자리를 경고로 보여 준다.
"""
from __future__ import annotations

import ast
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "vinc_mcp"

#: 리다이렉트를 따르는 기본 opener 로 이어지는 `urllib.request` 의 이름들.
OPENER_NAMES = {"urlopen", "build_opener", "OpenerDirector", "urlretrieve", "install_opener"}

#: 파일 → (허용하는 이름, 한 줄 이유). 그 파일에서도 적힌 이름만 허용한다.
ALLOWED = {
    "bridge.py": ({"urlopen"},
                  "사이드카로만 간다: 주소는 운영자가 VINC_SIDECAR_BASE 로 정하고 기본은 루프백이다"),
    "hubhttp.py": ({"build_opener"},
                   "리다이렉트 처리기를 갈아 끼운 opener 하나를 짓는 자리다"),
}


def _dotted(expr) -> str:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        inner = _dotted(expr.value)
        return f"{inner}.{expr.attr}" if inner else ""
    return ""


def _references(path: Path, module: str, names: set[str]) -> list[tuple[int, str]]:
    """`module` 의 `names` 를 가리키는 참조의 (줄, 이름). 들이는 줄도 참조로 센다."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parent, _, leaf = module.rpartition(".")
    module_aliases = {module}           # `import a.b as x` 의 x, `from a import b` 의 b
    name_aliases: dict[str, str] = {}   # `from a.b import n as y` 의 y → n
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == module and a.asname:
                    module_aliases.add(a.asname)
        elif isinstance(node, ast.ImportFrom) and node.module == module:
            for a in node.names:
                if a.name == "*":
                    name_aliases.update({n: n for n in names})
                    found.append((node.lineno, "*"))
                elif a.name in names:
                    name_aliases[a.asname or a.name] = a.name
                    found.append((node.lineno, a.name))
        elif isinstance(node, ast.ImportFrom) and parent and node.module == parent:
            for a in node.names:
                if a.name == leaf:
                    module_aliases.add(a.asname or leaf)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in names \
                and _dotted(node.value) in module_aliases:
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.Name) and node.id in name_aliases:
            found.append((node.lineno, name_aliases[node.id]))
    return sorted(set(found))


def _opener_refs(path: Path) -> list[tuple[int, str]]:
    return _references(path, "urllib.request", OPENER_NAMES)


def _raw_connections(path: Path) -> list[tuple[int, str]]:
    return (_references(path, "http.client", {"HTTPConnection", "HTTPSConnection"})
            + _references(path, "socket", {"create_connection"}))


def test_no_default_opener_outside_the_allow_list():
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        allowed = ALLOWED.get(rel, (set(), ""))[0]
        offenders += [f"vinc_mcp/{rel}:{n} {name}" for n, name in _opener_refs(path)
                      if name not in allowed]
    assert offenders == [], ("이 자리는 리다이렉트를 따르는 opener 로 이어진다. hubhttp.urlopen·"
                             "hubhttp.request 를 쓰거나 허용 목록에 한 줄 이유와 함께 올린다:\n"
                             + "\n".join(offenders))


def test_the_allow_list_has_no_stale_entries():
    # 더는 그 이름을 쓰지 않는 파일이 목록에 남으면 다음 사람이 그 자리를 믿고 쓴다.
    for rel, (names, _) in ALLOWED.items():
        used = {name for _, name in _opener_refs(PKG / rel)}
        assert names <= used, f"vinc_mcp/{rel} 은 더는 {sorted(names - used)} 를 쓰지 않는다"


def test_raw_connections_are_counted_and_shown():
    """주소 확인 없이 붙을 수 있는 자리를 센다. 실패시키지 않고 경고로 보여 준다."""
    seen = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        seen += [f"vinc_mcp/{rel}:{n} {name}" for n, name in _raw_connections(path)]
    if seen:
        warnings.warn("raw connections under vinc_mcp/ (counted, not failing):\n  "
                      + "\n  ".join(seen), UserWarning, stacklevel=1)


def test_the_scanner_sees_each_spelling(tmp_path):
    src = tmp_path / "m.py"
    src.write_text(
        "import urllib.request\n"                          # 1
        "import urllib.request as ur\n"                    # 2
        "from urllib.request import urlopen\n"             # 3  들이는 줄도 참조다
        "from urllib.request import urlopen as uo\n"       # 4
        "from urllib import request\n"                     # 5
        "urllib.request.urlopen('x')\n"                    # 6
        "ur.urlopen('x')\n"                                # 7
        "urlopen('x')\n"                                   # 8
        "uo('x')\n"                                        # 9
        "request.urlopen('x')\n"                           # 10
        "hubhttp.urlopen('x')\n"                           # 11 다른 모듈의 같은 이름은 아니다
        "u = urllib.request.urlopen\n"                     # 12 부르지 않고 담기만 해도
        "list(map(urlopen, ['x']))\n"                      # 13 넘기기만 해도
        "urllib.request.build_opener().open('x')\n"        # 14
        "urllib.request.OpenerDirector().open('x')\n"      # 15
        "request.urlretrieve('x', 'f')\n"                  # 16
        "ur.install_opener(None)\n"                        # 17
        "getattr(urllib.request, 'urlopen')('x')\n",       # 18 동적 접근은 일부러 못 본다
        encoding="utf-8")
    assert [n for n, _ in _opener_refs(src)] == [3, 4, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17]


def test_the_scanner_follows_a_star_import(tmp_path):
    src = tmp_path / "m.py"
    src.write_text("from urllib.request import *\nbuild_opener()\n", encoding="utf-8")
    assert _opener_refs(src) == [(1, "*"), (2, "build_opener")]


def test_the_counter_sees_raw_connections(tmp_path):
    src = tmp_path / "m.py"
    src.write_text("import http.client\nimport socket\nfrom http.client import HTTPConnection\n"
                   "http.client.HTTPSConnection('h')\nsocket.create_connection(('h', 1))\n"
                   "HTTPConnection('h')\n", encoding="utf-8")
    assert [n for n, _ in _raw_connections(src)] == [3, 4, 6, 5]
