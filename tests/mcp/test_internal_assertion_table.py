# -*- coding: utf-8 -*-
"""허브의 `/internal/*` 경로가 계정 서명 어서션의 세 검사를 경로마다 다 하는가.

세 검사는 받는 `typ`, 본문 지문 `bh`, 한 번만 쓰는 `jti` 다. 같은 검사가 경로마다 따로 적혀
있어서 한 경로에서 하나가 빠져도 다른 경로의 테스트는 초록이다. 그래서 경로 × 경우마다 허브가
내는 답(상태 · 거절 본문 · 사이드카에 닿았나)을 표 하나로 고정한다.

`/internal/quota` 는 jti 를 소비하지 않는다(읽기라서, `quota_request` 독스트링). 표에도 그대로
200 으로 남아 있고, 그 답을 바꾸는 변경은 이 표를 함께 고쳐야 한다.

기대값은 손으로 쓰지 않는다. `fixtures/internal_assertion_answers.json` 은 아래 `observe()` 를
main 의 코드에 돌려 쓴 것이다. 답을 일부러 바꾸는 변경은 그 파일을 다시 써서 같이 싣는다:

    python tests/mcp/test_internal_assertion_table.py --write
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import os
import sys
import threading
import time
import tokenize
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from vinc_mcp import edverify as ev            # noqa: E402
from vinc_mcp import http_server as hs         # noqa: E402

FIXTURE = Path(__file__).with_name("fixtures") / "internal_assertion_answers.json"
ACCT_SEED = bytes((i * 11 + 5) & 0xFF for i in range(32))
TEAM = "team:0198c0de-1111-7222-8333-444455556666"
COMMUNITY = "community:0198c0de-2222-7333-8444-555566667777"
SUBJECT = "0198c0de-3333-7444-8555-666677778888"

#: 경로마다 받는 typ 와, 그 typ 로 통과하는 클레임·본문. graph-owner 만 typ 를 넷 받는다.
#: 첫 typ 가 그 경로의 기준 요청이고, 거절 경우는 기준 요청에서 한 가지만 바꾼다.
ACCEPTED: dict[str, dict[str, tuple[dict, bytes]]] = {
    "/internal/purge-owner": {"purge": ({"sub": "u1"}, b"{}")},
    "/internal/purge-team": {"purge-team": ({"sub": "u1", "graph_owner": TEAM}, b"{}")},
    "/internal/graph-owner": {
        "proxy": ({"sub": "u1", "op": "domains"}, b"{}"),
        "proxy-team": ({"sub": "u1", "op": "domains", "graph_owner": TEAM, "role": "viewer"}, b"{}"),
        "proxy-conversion": ({"sub": SUBJECT, "op": "topic-conversion:preflight"}, b"{}"),
        "proxy-community": ({"sub": "u1", "op": "domains", "graph_owner": COMMUNITY,
                             "role": "reader", "visibility": "public"}, b"{}"),
    },
    "/internal/quota": {"quota": ({"sub": "account"}, b'{"spaces":[{"owner":"user:u1"}]}')},
    "/internal/usage": {"usage": ({"sub": "operator"}, b"{}")},
}
#: 계정과 허브 사이에 오가는 typ 전부. 경로가 받지 않는 것은 전부 거절이어야 한다.
TYPS = sorted({t for typs in ACCEPTED.values() for t in typs} | {"aa", "cra"})


class _Bridge:
    """사이드카 대역. 거절된 요청이 여기까지 왔는지가 표의 한 칸이다."""

    def __init__(self):
        self.calls = 0

    def request(self, method, path, params=None, body=None, timeout=60, **_kw):
        self.calls += 1
        return {"executed": True, "owners": {}}


def _bh(raw: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()


def _sign(claims: dict, raw: bytes, *, drop: tuple[str, ...] = (), **over) -> str:
    now = int(time.time())
    body = {"iss": "account", "aud": "mcp", "iat": now, "exp": now + 120,
            "bh": _bh(raw), "jti": uuid.uuid4().hex, **claims, **over}
    for key in drop:
        body.pop(key, None)
    return ev.sign_assertion(ACCT_SEED, body)


def _cases(route: str) -> list[tuple[str, list[tuple[str, bytes]]]]:
    """(경우 이름, 차례로 보낼 (어서션, 본문)) 목록. 답은 마지막 요청의 것을 적는다."""
    accepted = ACCEPTED[route]
    typ, (claims, raw) = next(iter(accepted.items()))
    out = [(f"ok:{t}", [(_sign({**c, "typ": t}, r), r)]) for t, (c, r) in accepted.items()]
    for wrong in [t for t in TYPS if t not in accepted] + ["", typ.upper()]:
        out.append((f"typ:{wrong or '(empty)'}", [(_sign({**claims, "typ": wrong}, raw), raw)]))
    out.append(("typ:(missing)", [(_sign(claims, raw), raw)]))
    out.append(("bh:missing", [(_sign({**claims, "typ": typ}, raw, drop=("bh",)), raw)]))
    out.append(("bh:other-body", [(_sign({**claims, "typ": typ}, raw, bh=_bh(b'{"x":1}')), raw)]))
    out.append(("jti:missing", [(_sign({**claims, "typ": typ}, raw, drop=("jti",)), raw)]))
    once = _sign({**claims, "typ": typ}, raw)
    out.append(("jti:reused", [(once, raw), (once, raw)]))
    return out


def _post(port: int, route: str, assertion: str, raw: bytes) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("POST", route, body=raw, headers={
            "Content-Type": "application/json", "X-Vinc-Assertion": assertion})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


def observe() -> dict[str, dict[str, dict]]:
    """경로 × 경우마다 허브의 답. 거절이면 본문을 싣고, 사이드카에 닿았는지를 함께 적는다.

    키 네임스페이싱은 켠 채로 본다. 커뮤니티 프록시는 그 모드에서만 열리고, 두 모드의 스위트가
    같은 표를 읽어야 한다."""
    saved = {k: os.environ.get(k) for k in ("VINC_PK_NAMESPACED", "VINC_SELF_HOSTED")}
    os.environ["VINC_PK_NAMESPACED"] = "1"
    os.environ.pop("VINC_SELF_HOSTED", None)
    bridge = _Bridge()
    app = hs.MCPHTTPApp(bridge, static_token="t", account_public=ev.secret_to_public(ACCT_SEED))
    httpd = hs.make_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        table: dict[str, dict[str, dict]] = {}
        for route in ACCEPTED:
            row = table[route] = {}
            for name, sends in _cases(route):
                answers = []
                for assertion, raw in sends:
                    before = bridge.calls
                    status, body = _post(port, route, assertion, raw)
                    answer = {"status": status, "sidecar": bridge.calls > before}
                    if status != 200:
                        answer["body"] = body
                    answers.append(answer)
                row[name] = answers[0] if len(answers) == 1 else answers
        return table
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _served_routes() -> set[str]:
    """허브 코드에 문자열 그대로 적힌 `/internal/*` 경로. 주석·독스트링 속 언급은 세지 않는다."""
    src = Path(hs.__file__).read_text(encoding="utf-8")
    found = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING and tok.string[:1] in "\"'":
            text = tok.string.strip("\"'")
            if text.startswith("/internal/") and "/" not in text[len("/internal/"):] and " " not in text:
                found.add(text)
    return found


def test_the_table_covers_every_internal_route_the_hub_serves():
    assert _served_routes() == set(ACCEPTED), (
        "허브의 /internal/* 경로와 이 표의 경로가 다르다. 새 경로는 ACCEPTED 에 줄을 더하고 "
        "기대값을 다시 쓴다")


def test_every_internal_route_answers_as_on_main():
    want = json.loads(FIXTURE.read_text(encoding="utf-8"))["answers"]
    got = observe()
    diffs = [f"{route} {case}: main {want.get(route, {}).get(case)!r}, now {answer!r}"
             for route, row in got.items() for case, answer in row.items()
             if want.get(route, {}).get(case) != answer]
    missing = [f"{route} {case}" for route, row in want.items() for case in row
               if case not in got.get(route, {})]
    assert not diffs and not missing, "\n".join(diffs + ["표에서 빠진 경우: " + m for m in missing])


def test_wrong_typ_and_missing_or_other_digest_are_refused_before_the_sidecar():
    """기대값 파일과 따로 세운 불변식. 기대값을 다시 써서 검사가 빠진 것을 덮지 못하게 한다."""
    for route, row in observe().items():
        for case, answer in row.items():
            if case.startswith(("typ:", "bh:")) or (case.startswith("jti:") and route != "/internal/quota"):
                last = answer[-1] if isinstance(answer, list) else answer
                assert last["status"] == 401 and not last["sidecar"], (route, case, answer)
            if case.startswith("ok:"):
                assert answer["status"] == 200, (route, case, answer)
        first, _second = row["jti:reused"]
        assert first["status"] == 200, (route, row["jti:reused"])


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        sys.exit("usage: python tests/mcp/test_internal_assertion_table.py --write")
    import subprocess
    rev = subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.strip()
    # 경우 하나를 한 줄로 쓴다. 답이 바뀐 경우가 diff 에서 한 줄로 보인다.
    routes = []
    for route, row in sorted(observe().items()):
        cases = ",\n".join(f"   {json.dumps(case)}: {json.dumps(answer, sort_keys=True)}"
                           for case, answer in sorted(row.items()))
        routes.append(f"  {json.dumps(route)}: {{\n{cases}\n  }}")
    FIXTURE.write_text('{\n "answers": {\n' + ",\n".join(routes)
                       + f'\n }},\n "generated_from": "{rev}"\n}}\n', encoding="utf-8")
    print(f"wrote {FIXTURE.relative_to(ROOT)} from {rev}")
