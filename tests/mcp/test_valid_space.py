# -*- coding: utf-8 -*-
"""고른 스페이스가 있을 수 있는 값인가를 네 자리가 같은 술어(`spaces.valid_space`)로 보고, 그 답이
main 의 네 자리와 값마다 같은가.

네 자리: `/mcp` 의 Events 구독과 도구 호출(지금은 `mcp_post`, 기대값을 뽑은 main 리비전에서는
`http_server`), REST v1 의 쿼리 `space` 와 도구
게이트(`rest_api`). 자리는 원문에서 모양으로 찾는다: `valid_space=` 로 넘기는 술어 셋과, REST 가
`space_not_found` 로 거절하는 조건 하나.

기대값은 손으로 쓰지 않는다. `fixtures/valid_space_main.json` 은 main 리비전의 원문에서 네 자리와
그 자리가 부르는 정의(`_UUID_RE`, `community.is_space`)를 뽑아 그 리비전의 것만으로 값마다 돌린
답이다. 다시 쓰려면:

    python tests/mcp/test_valid_space.py --write <main 리비전>
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import threading
import types
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from vinc_mcp import http_server as hs, mcp_post, rest_api, spaces   # noqa: E402

FIXTURE = Path(__file__).with_name("fixtures") / "valid_space_main.json"
#: 기대값을 뽑는 main 리비전에서 네 자리가 있는 파일(`--write`).
FILES = ("vinc_mcp/http_server.py", "vinc_mcp/rest_api.py")
#: 지금 트리에서 네 자리가 있는 파일과 그 모듈. `/mcp` 의 두 자리는 `mcp_post.answer` 안에 있다.
HEAD_FILES = (("vinc_mcp/mcp_post.py", mcp_post), ("vinc_mcp/rest_api.py", rest_api))
U = "0198c0de-1111-7222-8333-444455556666"

#: 네 자리가 받거나 거절할 수 있는 값의 모양. 문자열은 부르는 쪽의 정규화(공백 제거·소문자)를
#: 거친 꼴도 표에 넣는다(아래 `_values`). 문자열이 아닌 값은 네 자리에 닿지 않지만 예외까지 같은지 본다.
BASE = [
    U, U.upper(), "0198c0DE-1111-7222-8333-444455556666",
    "community:" + U, "community:" + U.upper(), "COMMUNITY:" + U, "Community:" + U,
    "team:" + U, "user:u1", "personal", "PERSONAL", " personal ", "local", "", " ", "*", "null",
    " " + U + " ", "\t" + U, U + "\n", "\n" + U, "community:" + U + "\n",
    U.replace("-", ""), "{" + U + "}", "urn:uuid:" + U, "t/" + U, "/t/" + U + "/mcp",
    U[:-1], U + "0", U[:-1] + "g", "０" + U[1:], U.replace("-", "‐"),
    "community:", "community:" + U + "x", "community: " + U, "community:community:" + U,
    "community:" + U.replace("-", ""), "team:" + U + "\n",
    None, 7, U.encode(),
]


def _values() -> list:
    out = list(BASE)
    for v in BASE:
        if isinstance(v, str) and v.strip().lower() not in out:
            out.append(v.strip().lower())
    return out


def _sites(src: str, path: str) -> list[tuple[str, str, str]]:
    """원문에서 자리를 찾는다: (자리 이름, 종류, 식 원문). 종류는 `predicate`(받으면 참)와
    `refusal`(참이면 `space_not_found`)."""
    tree = ast.parse(src)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "valid_space":
            found.append((node.value.lineno, "predicate", ast.get_source_segment(src, node.value)))
        if isinstance(node, ast.If) and any(
                isinstance(c, ast.Call) and getattr(c.func, "id", None) == "fail"
                and any(isinstance(a, ast.Constant) and a.value == "space_not_found" for a in c.args)
                for stmt in node.body for c in ast.walk(stmt)):
            found.append((node.test.lineno, "refusal", ast.get_source_segment(src, node.test)))
    return [(f"{path}:{line}", kind, text) for line, kind, text in sorted(found)]


def _outcome(fn, value):
    try:
        return bool(fn(value))
    except Exception as exc:   # noqa: BLE001 — 예외의 종류까지 답으로 적는다
        return type(exc).__name__


def _accepts(kind: str, text: str, scope: dict):
    """자리 하나를 '이 값을 받는가' 함수로. 거절 조건은 뒤집는다."""
    if kind == "predicate":
        return eval(text, scope)                                      # noqa: S307
    code = compile(text, "<site>", "eval")
    return lambda v: not eval(code, {**scope, "space": v})           # noqa: S307


def _main_scope(rev: str) -> dict:
    """main 리비전의 원문에서 네 자리가 부르는 정의만 뽑아 그 리비전의 것으로 이름 공간을 만든다."""
    def show(path):
        return subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout

    def pick(src, names):
        tree = ast.parse(src)
        keep = [n for n in tree.body if (isinstance(n, ast.Assign) and any(
            getattr(t, "id", None) in names for t in n.targets))
            or (isinstance(n, ast.FunctionDef) and n.name in names)]
        return ast.unparse(ast.Module(body=keep, type_ignores=[]))

    hub, comm = {"re": re}, {"re": re}
    exec(pick(show("vinc_mcp/http_server.py"), {"_UUID", "_UUID_RE"}), hub)       # noqa: S102
    exec(pick(show("vinc_mcp/community.py"), {"OWNER_RE", "is_space"}), comm)    # noqa: S102
    return {"_UUID_RE": hub["_UUID_RE"], "community": types.SimpleNamespace(is_space=comm["is_space"])}


def generate(rev: str) -> dict:
    scope = _main_scope(rev)
    sites = []
    for path in FILES:
        src = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout
        sites += _sites(src, path)
    answers = {repr(v): [_outcome(_accepts(kind, text, scope), v) for _w, kind, text in sites]
               for v in _values()}
    return {"generated_from": rev,
            "sites": [{"where": w, "kind": k, "source": t} for w, k, t in sites],
            "answers": answers}


def _head_sites() -> list[tuple[str, str, object]]:
    out = []
    for path, module in HEAD_FILES:
        for where, kind, text in _sites((ROOT / path).read_text(encoding="utf-8"), path):
            out.append((where, kind, _accepts(kind, text, vars(module))))
    return out


def _main() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_main_had_four_copies_that_agree_except_personal():
    main = _main()
    assert [(s["where"].split(":")[0], s["kind"]) for s in main["sites"]] == [
        ("vinc_mcp/http_server.py", "predicate"), ("vinc_mcp/http_server.py", "predicate"),
        ("vinc_mcp/rest_api.py", "refusal"), ("vinc_mcp/rest_api.py", "predicate")]
    for value, row in main["answers"].items():
        a, b, refusal_site, d = row
        assert a == b == d, value
        assert refusal_site == (True if value == "'personal'" else a), value


def test_the_four_sites_now_use_one_predicate():
    sites = _head_sites()
    assert [kind for _w, kind, _f in sites] == ["predicate", "predicate", "refusal", "predicate"]
    assert all(fn is spaces.valid_space for _w, kind, fn in sites if kind == "predicate")
    src = (ROOT / "vinc_mcp/rest_api.py").read_text(encoding="utf-8")
    refusal = _sites(src, "vinc_mcp/rest_api.py")[0][2]
    assert "spaces.valid_space(space)" in refusal and "_UUID_RE" not in refusal


def test_every_site_answers_every_value_as_on_main():
    main = _main()
    sites = _head_sites()
    diffs = []
    for value, want in main["answers"].items():
        v = ast.literal_eval(value)
        got = [_outcome(fn, v) for _w, _k, fn in sites]
        if got != want:
            diffs.append(f"{value}: main {want}, now {got}")
        if _outcome(spaces.valid_space, v) != want[0]:
            diffs.append(f"{value}: spaces.valid_space {_outcome(spaces.valid_space, v)!r}, main {want[0]!r}")
    assert not diffs, "\n".join(diffs)


class _Bridge:
    owner = "user:local"

    def request(self, method, path, params=None, body=None, **_kw):
        return {"executed": True, "rows": [], "has_more": False, "next_cursor": None}


def _serve(app):
    httpd = hs.make_server(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _call(req):
    try:
        resp = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        resp = exc
    with resp:
        return resp.status, json.loads(resp.read() or b"{}")


def test_the_rest_query_space_is_refused_as_on_main():
    """REST 는 키를 보기 전에 쿼리의 스페이스부터 본다: 거절이면 404, 지나가면 키가 없어 401."""
    main = _main()["answers"]
    httpd, base = _serve(hs.MCPHTTPApp(_Bridge(), verify_key=lambda *_a, **_k: None))
    try:
        for value in (v for v in _values() if isinstance(v, str)):
            space = value.strip().lower() or "personal"
            want = 401 if main[repr(space)][2] is True else 404
            url = base + "/v1/roles?space=" + urllib.parse.quote(value, safe="")
            status, _body = _call(urllib.request.Request(url))
            assert status == want, (value, status, want)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_mcp_space_argument_reaches_the_account_only_when_main_did(monkeypatch):
    """`/mcp` 는 모양이 맞는 스페이스만 계정에 묻는다. 묻지 않은 값은 그 자리에서 거절이다.

    커뮤니티 스페이스는 키 네임스페이싱이 켜진 허브에서만 계정에 묻으므로 그 모드로 본다."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", "1")
    monkeypatch.delenv("VINC_SELF_HOSTED", raising=False)
    main = _main()["answers"]
    asked = []

    def verify(token, team=None):
        if team is not None:
            asked.append(team)
            return None
        return {"owner": "user:u1", "plan": "pro", "scope": "full"}

    def verify_community(subject, space):
        asked.append(space)

    httpd, base = _serve(hs.MCPHTTPApp(_Bridge(), verify_key=verify, verify_community=verify_community))
    try:
        for value in (v for v in _values() if isinstance(v, str)):
            space = value.strip().lower()
            want = bool(space) and space != "personal" and main[repr(space)][1] is True
            asked.clear()
            msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "vinc_search", "arguments": {"query": "x", "space": value}}}
            _call(urllib.request.Request(base + "/mcp", data=json.dumps(msg).encode(), headers={
                "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer vinc_sk_test"}, method="POST"))
            assert bool(asked) == want, (value, asked, want)
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--write":
        sys.exit("usage: python tests/mcp/test_valid_space.py --write <main revision>")
    doc = generate(sys.argv[2])
    rows = ",\n".join(f"  {json.dumps(k)}: {json.dumps(v)}" for k, v in doc["answers"].items())
    sites = ",\n".join("  " + json.dumps(s, ensure_ascii=False) for s in doc["sites"])
    FIXTURE.write_text(f'{{\n "answers": {{\n{rows}\n }},\n "generated_from": "{doc["generated_from"]}",'
                       f'\n "sites": [\n{sites}\n ]\n}}\n', encoding="utf-8")
    print(f"wrote {FIXTURE.relative_to(ROOT)} from {doc['generated_from']}: "
          f"{len(doc['sites'])} sites, {len(doc['answers'])} values")
