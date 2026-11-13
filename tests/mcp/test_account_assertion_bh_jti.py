# -*- coding: utf-8 -*-
"""허브가 계정 `gate()` 경로로 보내는 어서션의 `bh` 와 `jti` 가 계정의 엄격 검증기 모양인가.

`bh` 는 요청 본문 바이트의 sha256 을 패딩 없는 base64url 로 적은 것이고(계정 `edsign.ts` 의
`bodyHash`), `jti` 는 호출마다 새로 뽑은 소문자 16진 32자다(계정 `routes/internal.ts` 의
run-waiting 이 받는 모양). 요청은 `hubhttp.urlopen` 에서 가로채므로 여기서 보는 바이트가 소켓으로
나가는 바이트다. 해시는 허브 코드를 빌리지 않고 이 파일이 따로 계산한다.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from vinc_mcp import edverify as ev            # noqa: E402
from vinc_mcp import entitlement as E          # noqa: E402
from vinc_mcp import hubhttp                   # noqa: E402

SEED = bytes(range(80, 112))
PUBLIC = ev.secret_to_public(SEED)
ACCOUNT = "http://account.test"
TEAM = "0198c0de-1111-7222-8333-444455556666"

#: 계정 검증기가 jti 에 거는 정규식. run-waiting 은 첫째만, 커뮤니티는 둘 중 하나를 받는다.
RUN_WAITING_JTI = re.compile(r"^[a-f0-9]{32}$")
COMMUNITY_JTI = re.compile(r"^[a-f0-9]{32}$|^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
                           re.IGNORECASE)

#: 허브 호출 → (호출, 계정의 답, 경로, typ, 본문으로 나가야 할 payload).
CALLS = {
    "verify_key": (lambda: E.verify_key("vinc_sk_bh"),
                   {"sub": "u1", "plan": "free", "scope": "full"},
                   "/internal/mcp/verify-key", "verify-key", {"key": "vinc_sk_bh"}),
    "verify_key(team)": (lambda: E.verify_key("vinc_sk_bh", TEAM),
                         {"sub": "u1", "plan": "free", "scope": "full",
                          "graph_owner": "team:" + TEAM, "role": "editor"},
                         "/internal/mcp/verify-key", "verify-key", {"key": "vinc_sk_bh", "team": TEAM}),
    "verify_member": (lambda: E.verify_member("u1", TEAM),
                      {"graph_owner": "team:" + TEAM, "role": "editor"},
                      "/internal/mcp/team-membership", "team-membership", {"sub": "u1", "team": TEAM}),
    "list_spaces": (lambda: E.list_spaces("u1"), {"spaces": [], "communities": []},
                    "/internal/mcp/spaces", "spaces", {"sub": "u1"}),
    "list_communities": (lambda: E.list_communities("u1"), {"spaces": [], "communities": []},
                         "/internal/mcp/spaces", "spaces", {"sub": "u1"}),
    "lookup_plan": (lambda: E.lookup_plan("u1"), {"sub": "u1", "plan": "free"},
                    "/internal/mcp/plan", "plan", {"sub": "u1"}),
}


class _Resp(io.BytesIO):
    status = 200
    headers: dict = {}

    def __init__(self, body):
        super().__init__(json.dumps(body).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _clear():
    for cache in (E._cache, E._spaces_cache, E._communities_cache):
        cache.clear()


def _wire_hash(data: bytes) -> str:
    """계정 `bodyHash` 를 따로 옮긴 것: sha256 → base64url, 패딩 없음."""
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")


def _post_json_bytes(payload) -> bytes:
    """`hubhttp.post_json` 이 같은 payload 로 만드는 본문. 본문 바이트가 바뀌지 않았음을 대조한다."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@pytest.fixture
def account(monkeypatch):
    """서명키를 켜고, 계정으로 나가는 요청을 urllib 의 Request 그대로 적는다."""
    monkeypatch.setattr(E, "_seed", lambda: SEED)
    monkeypatch.setenv("VINC_ACCOUNT_INTERNAL_URL", ACCOUNT)
    state = SimpleNamespace(sent=[], answer={})

    def urlopen(req, *, timeout):
        state.sent.append(req)
        return _Resp(state.answer)

    monkeypatch.setattr(hubhttp, "urlopen", urlopen)
    _clear()
    yield state
    _clear()


def _run(account, name):
    call, account.answer, route, typ, payload = CALLS[name]
    before = len(account.sent)
    call()
    (req,) = account.sent[before:]
    claims = ev.verify_assertion(PUBLIC, req.get_header("X-vinc-assertion"), max_age=120)
    assert claims is not None, "허브 공개키로 검증되지 않는다"
    return req, claims, route, typ, payload


@pytest.mark.parametrize("name", sorted(CALLS))
def test_bh_is_the_hash_of_the_bytes_on_the_wire(account, name):
    req, claims, route, typ, payload = _run(account, name)
    assert req.full_url == ACCOUNT + route
    assert req.get_method() == "POST"
    assert isinstance(req.data, bytes)
    assert claims["bh"] == _wire_hash(req.data), (name, claims["bh"])
    # 본문 바이트와 헤더는 post_json 이 보내던 그대로다. 계정의 지금 경로가 읽는 것이 바뀌지 않는다.
    assert req.data == _post_json_bytes(payload)
    assert dict(req.header_items()).keys() == {"Content-type", "Accept", "X-vinc-assertion"}
    assert req.get_header("Content-type") == req.get_header("Accept") == "application/json"
    # gate() 가 대조하는 클레임은 그대로고, 더해진 것은 bh 와 jti 둘뿐이다.
    assert set(claims) == {"iss", "aud", "typ", "iat", "exp", "bh", "jti"}
    assert (claims["iss"], claims["aud"], claims["typ"]) == ("mcp", "account", typ)
    assert claims["exp"] - claims["iat"] == 120


def test_bh_covers_the_utf8_bytes_of_a_non_ascii_body(account):
    """ensure_ascii 를 끈 본문이라 한글은 이스케이프가 아니라 UTF-8 바이트로 나가고, bh 도 그 바이트의 해시다."""
    account.answer = {"sub": "u1", "plan": "free", "scope": "full"}
    E.verify_key("vinc_sk_한글é")
    (req,) = account.sent
    assert "한글é".encode("utf-8") in req.data and b"\\u" not in req.data
    claims = ev.verify_assertion(PUBLIC, req.get_header("X-vinc-assertion"), max_age=120)
    assert claims["bh"] == _wire_hash(req.data)


def test_jti_is_fresh_per_call_and_in_the_account_format(account):
    seen = []
    for _round in range(2):
        for name in sorted(CALLS):
            _clear()
            _req, claims, *_ = _run(account, name)
            seen.append(claims["jti"])
    assert len(seen) == 2 * len(CALLS)
    assert len(set(seen)) == len(seen), "두 호출이 같은 jti 를 실었다"
    for jti in seen:
        assert RUN_WAITING_JTI.match(jti) and COMMUNITY_JTI.match(jti), jti
        # uuid4 의 hex 다: 버전 자리는 4, 변형 자리는 8·9·a·b.
        assert jti[12] == "4" and jti[16] in "89ab", jti


def test_the_same_call_twice_signs_two_jti_over_the_same_body(account):
    """jti 만 다르고 bh 는 같다: 같은 본문이면 같은 해시이고, 재생 판정은 jti 가 한다."""
    _req1, first, *_ = _run(account, "lookup_plan")
    _req2, second, *_ = _run(account, "lookup_plan")
    assert first["bh"] == second["bh"]
    assert first["jti"] != second["jti"]
