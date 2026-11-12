# -*- coding: utf-8 -*-
"""`vinc_mcp/` 에서 `urllib.request.urlopen` 을 직접 부르는 곳은 아래 허용 목록뿐이다.

기본 urlopen 은 리다이렉트를 따르고, 따라가면서 Authorization 같은 헤더를 Location 의 호스트로
그대로 보낸다. 밖으로 나가는 요청은 `hubhttp` 의 opener 를 지나거나, 확인한 주소로 붙는
전송(`netguard`)을 쓴다. 근거: [[record:hub-http-clients-refuse-redirects-2026-10-16]]
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "vinc_mcp"

#: 파일 → 허용 이유. 이유는 한 줄이다. `hubhttp.py` 는 자기 opener 를 써서 목록에 없다.
ALLOWED = {
    "bridge.py": "사이드카로만 간다: 주소는 운영자가 VINC_SIDECAR_BASE 로 정하고 기본은 루프백이다",
}


def _urlopen_calls(path: Path) -> list[int]:
    """`urllib.request.urlopen(...)` 과 그것을 다른 이름으로 들인 것의 호출 줄."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_aliases = {"urllib.request"}     # `import urllib.request as x` 의 x
    func_aliases: set[str] = set()          # `from urllib.request import urlopen as y` 의 y
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "urllib.request" and a.asname:
                    module_aliases.add(a.asname)
        elif isinstance(node, ast.ImportFrom) and node.module == "urllib.request":
            for a in node.names:
                if a.name in ("urlopen", "*"):
                    func_aliases.add(a.asname or "urlopen")
        elif isinstance(node, ast.ImportFrom) and node.module == "urllib":
            for a in node.names:
                if a.name == "request":
                    module_aliases.add(a.asname or "request")

    def dotted(expr) -> str:
        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Attribute):
            inner = dotted(expr.value)
            return f"{inner}.{expr.attr}" if inner else ""
        return ""

    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "urlopen" and dotted(f.value) in module_aliases:
            lines.append(node.lineno)
        elif isinstance(f, ast.Name) and f.id in func_aliases:
            lines.append(node.lineno)
    return sorted(lines)


def test_no_default_urlopen_outside_the_allow_list():
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        if rel in ALLOWED:
            continue
        offenders += [f"vinc_mcp/{rel}:{n}" for n in _urlopen_calls(path)]
    assert offenders == [], ("이 호출은 리다이렉트를 따른다. hubhttp.urlopen·hubhttp.request 를 "
                             "쓰거나 허용 목록에 한 줄 이유와 함께 올린다:\n" + "\n".join(offenders))


def test_the_allow_list_has_no_stale_entries():
    # 더는 urlopen 을 부르지 않는 파일이 목록에 남으면 다음 사람이 그 자리를 믿고 쓴다.
    for rel in ALLOWED:
        assert _urlopen_calls(PKG / rel), f"vinc_mcp/{rel} 은 더는 urlopen 을 부르지 않는다"


def test_the_scanner_sees_each_spelling(tmp_path):
    src = tmp_path / "m.py"
    src.write_text(
        "import urllib.request\n"
        "import urllib.request as ur\n"
        "from urllib.request import urlopen\n"
        "from urllib.request import urlopen as uo\n"
        "from urllib import request\n"
        "urllib.request.urlopen('x')\n"
        "ur.urlopen('x')\n"
        "urlopen('x')\n"
        "uo('x')\n"
        "request.urlopen('x')\n"
        "hubhttp.urlopen('x')\n",
        encoding="utf-8")
    assert _urlopen_calls(src) == [6, 7, 8, 9, 10]
