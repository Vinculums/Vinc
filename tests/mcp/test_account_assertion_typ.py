# -*- coding: utf-8 -*-
"""허브가 계정 내부 경로로 보내는 어서션마다 그 경로의 `typ` 가 실리는가.

계정은 `typ` 로 어서션을 경로에 묶는다. 경로마다 값이 하나여야 한 경로에 서명한 어서션이 다른
경로에서 통하지 않는다. 그래서 허브 호출을 하나씩 실제로 돌려 나가는 요청을 가로채고, 요청이 간
경로와 어서션의 `typ` 를 아래 표와 대조한다. 표는 계정이 경로마다 받을 값을 정할 때 읽는 목록이다.
"""
from __future__ import annotations

import io
import json
import sys
import tokenize
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from vinc_mcp import community as C            # noqa: E402
from vinc_mcp import edverify as ev            # noqa: E402
from vinc_mcp import entitlement as E          # noqa: E402
from vinc_mcp import hubhttp, run_notify       # noqa: E402
from tests.mcp.test_internal_assertion_table import ACCEPTED as INBOUND  # noqa: E402

SEED = bytes(range(40, 72))
PUBLIC = ev.secret_to_public(SEED)
ACCOUNT = "http://account.test"
TEAM = "0198c0de-1111-7222-8333-444455556666"
COMMUNITY = "community:0198c0de-2222-7333-8444-555566667777"

#: 허브 호출 → (그 호출이 가는 계정 경로, 어서션의 typ).
TABLE: dict[str, tuple[str, str]] = {
    "entitlement.verify_key": ("/internal/mcp/verify-key", "verify-key"),
    "entitlement.verify_key(team)": ("/internal/mcp/verify-key", "verify-key"),
    "entitlement.verify_member": ("/internal/mcp/team-membership", "team-membership"),
    "entitlement.list_spaces": ("/internal/mcp/spaces", "spaces"),
    "entitlement.list_communities": ("/internal/mcp/spaces", "spaces"),
    "entitlement.lookup_plan": ("/internal/mcp/plan", "plan"),
    "community.resolve": ("/internal/mcp/community-resolve", "community-read"),
    "community.write": ("/internal/mcp/community", "community-write"),
    "community.read": ("/internal/mcp/community-read", "community-read"),
    "run_notify.send": ("/internal/mcp/run-waiting", "run-waiting"),
}

#: 두 경로가 한 typ 를 나눠 쓰는 곳. 계정이 커뮤니티 읽기 두 경로를 같은 `community-read` 로
#: 대조하므로(`account/src/routes/internalCommunity.ts`) 허브만 바꾸면 계정이 거절한다.
#: 여기 없는 공유는 실패다.
SHARED = {"community-read": {"/internal/mcp/community-resolve", "/internal/mcp/community-read"}}

_MEMBER = {"owner": COMMUNITY, "member": "user:u1", "scope": "full"}
_WAITING = {"owner": "user:u1", "run": "episode:typ", "ask": 1, "title": "t",
            "expires_at": "2026-11-13T00:00:00Z"}

#: 호출 하나를 돌리는 법과 계정의 답. 답은 호출이 끝까지 가는 모양이면 되고, 표가 보는 것은 요청이다.
CALLS = {
    "entitlement.verify_key": (lambda: E.verify_key("vinc_sk_typ"),
                               {"sub": "u1", "plan": "free", "scope": "full"}),
    "entitlement.verify_key(team)": (lambda: E.verify_key("vinc_sk_typ", TEAM),
                                     {"sub": "u1", "plan": "free", "scope": "full",
                                      "graph_owner": "team:" + TEAM, "role": "editor"}),
    "entitlement.verify_member": (lambda: E.verify_member("u1", TEAM),
                                  {"graph_owner": "team:" + TEAM, "role": "editor"}),
    "entitlement.list_spaces": (lambda: E.list_spaces("u1"), {"spaces": [], "communities": []}),
    "entitlement.list_communities": (lambda: E.list_communities("u1"),
                                     {"spaces": [], "communities": []}),
    "entitlement.lookup_plan": (lambda: E.lookup_plan("u1"), {"sub": "u1", "plan": "free"}),
    "community.resolve": (lambda: C.resolve("u1", COMMUNITY),
                          {"graph_owner": COMMUNITY, "visibility": "public", "role": "reader"}),
    "community.write": (lambda: C.write(_MEMBER, {"action": "react", "payload": {},
                                                  "client_name": "typ test"}),
                        {"executed": True}),
    "community.read": (lambda: C.read(_MEMBER, {"action": "feed", "payload": {}}),
                       {"executed": True}),
    "run_notify.send": (lambda: run_notify.send(dict(_WAITING)), {}),
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


@pytest.fixture
def account(monkeypatch):
    """허브 서명키를 켜고 계정으로 가는 요청을 가로챈다. 요청마다 (주소, 어서션) 을 적는다."""
    monkeypatch.setattr(E, "_seed", lambda: SEED)
    monkeypatch.setenv("VINC_ACCOUNT_INTERNAL_URL", ACCOUNT)
    state = SimpleNamespace(sent=[], answer={})

    def urlopen(req, *, timeout):
        state.sent.append((req.full_url, req.get_header("X-vinc-assertion")))
        return _Resp(state.answer)

    monkeypatch.setattr(hubhttp, "urlopen", urlopen)
    _clear()
    yield state
    _clear()


@pytest.mark.parametrize("name", sorted(TABLE))
def test_each_call_signs_the_typ_of_its_route(account, name):
    call, account.answer = CALLS[name]
    call()
    route, typ = TABLE[name]
    assert [url for url, _token in account.sent] == [ACCOUNT + route]
    claims = ev.verify_assertion(PUBLIC, account.sent[0][1], max_age=120)
    assert claims is not None, "허브 공개키로 검증되지 않는다"
    assert claims.get("typ") == typ, (name, claims)
    # 계정이 이 경로들에서 이미 대조하는 클레임은 그대로다.
    assert (claims["iss"], claims["aud"]) == ("mcp", "account")
    assert claims["exp"] - claims["iat"] == 120


def test_two_routes_never_share_a_typ():
    by_route: dict[str, set[str]] = {}
    for route, typ in TABLE.values():
        by_route.setdefault(route, set()).add(typ)
    assert all(len(typs) == 1 for typs in by_route.values()), by_route
    by_typ: dict[str, set[str]] = {}
    for route, (typ,) in by_route.items():
        by_typ.setdefault(typ, set()).add(route)
    assert {typ: routes for typ, routes in by_typ.items() if len(routes) > 1} == SHARED


def test_no_hub_typ_is_a_typ_the_account_signs_for_the_hub():
    """서명 키가 방향마다 달라 섞여 통하지는 않는다. 이름이 겹치면 표와 로그에서 방향을 잘못 읽는다."""
    account_signs = {typ for typs in INBOUND.values() for typ in typs} | {"aa"}
    hub_signs = {typ for _route, typ in TABLE.values()} | {"cra"}
    assert not account_signs & hub_signs


def _account_routes_in_source() -> set[str]:
    """허브 코드에 문자열 그대로 적힌 계정 내부 경로. 주석·독스트링 속 언급은 세지 않는다."""
    found = set()
    for path in sorted((ROOT / "vinc_mcp").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.STRING and tok.string[:1] in "\"'":
                text = tok.string.strip("\"'")
                if text.startswith("/internal/mcp/") and " " not in text:
                    found.add(text)
    return found


def test_the_table_covers_every_account_route_the_hub_calls():
    assert _account_routes_in_source() == {route for route, _typ in TABLE.values()}, (
        "허브가 부르는 계정 내부 경로와 이 표의 경로가 다르다. 새 호출은 TABLE 과 CALLS 에 줄을 더한다")
