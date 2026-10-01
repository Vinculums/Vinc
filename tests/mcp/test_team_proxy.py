# -*- coding: utf-8 -*-
"""P3 팀 그래프 프록시 — AP-1 §4.3·§4.4·§4.6.

지키려는 것:

1. **유형 분리가 fail-closed 를 만든다.** 팀 스코프는 `proxy-team` 이라는 별도 유형이라,
   이 코드를 모르는 허브는 그냥 거부한다. 기존 `proxy` 에 필드만 덧붙였다면 구 허브가
   팀 작업을 **멤버의 개인 그래프에** 조용히 써넣었을 것이다(무음 오동작).
2. **역할은 op 단위로 강제**되고, 모르는 역할은 거부한다.
3. **팀 소거는 별도 문**이다 — 개인 purge 경로로는 팀 소유자를 만들 수 없다.
   이것이 "멤버가 계정을 지워도 팀 그래프는 안전하다"의 근거다.
4. 역할 표는 프록시 op 전량을 덮는다(새 op 를 추가하면서 역할을 안 정하면 CI 가 깨진다).
"""
from __future__ import annotations

import base64
import hashlib
import uuid
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest                                        # noqa: E402

from vinc_mcp import edverify                        # noqa: E402
from vinc_mcp import http_server as hs               # noqa: E402
from vinc_mcp.bridge import Bridge                   # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp          # noqa: E402

ACCOUNT_SEED = bytes(range(32))
TEAM = "team:0191f0aa-1111-7abc-8def-0123456789ab"


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.last = None

    def request(self, method, path, params=None, body=None, timeout=60):
        self.last = (method, path, params, body)
        return {"executed": True}


def _app():
    return MCPHTTPApp(_StubBridge(), static_token="S",
                      account_public=edverify.secret_to_public(ACCOUNT_SEED))


#: 빈 본문의 지문. 이 파일의 호출들은 `raw_body` 를 안 넘기므로 허브가 `b""` 를 본다.
_BH_EMPTY = base64.urlsafe_b64encode(hashlib.sha256(b"").digest()).rstrip(b"=").decode()


def _sign(payload: dict) -> str:
    """계정이 실제로 서명하는 모양 — `bh`·`jti` 포함(감사 M2, 이제 필수).

    `jti` 는 호출마다 새로 만든다. 고정하면 두 번째 호출부터 **재생으로** 거부되고,
    그 실패는 방어가 동작한다는 뜻이라 원인을 오해하기 쉽다."""
    import time
    now = int(time.time())
    return edverify.sign_assertion(ACCOUNT_SEED,
                                   {"iss": "account", "aud": "mcp",
                                    "iat": now, "exp": now + 60,
                                    "bh": _BH_EMPTY, "jti": uuid.uuid4().hex,
                                    **payload})


def _team_assertion(op="search", role="editor", owner=TEAM, sub="u-1", typ="proxy-team"):
    return _sign({"typ": typ, "sub": sub, "op": op, "graph_owner": owner, "role": role})


# ── 1. 유형 분리 ─────────────────────────────────────────────────────────────
def test_team_scope_routes_to_the_team_graph():
    app = _app()
    app.graph_owner_request(_team_assertion(op="search", role="viewer"), {"q": "x"})
    method, path, params, body = app.bridge.last
    assert method == "GET" and path == "/search"
    assert params["owner"] == TEAM          # 개인 그래프로 새지 않았다


def test_personal_proxy_is_unchanged_bytes():
    """개인 경로는 건드리지 않았다 — 팀 작업 때문에 기존 동작이 바뀌면 안 된다."""
    app = _app()
    app.graph_owner_request(_sign({"typ": "proxy", "sub": "u-1", "op": "search"}), {})
    assert app.bridge.last[2]["owner"] == "user:u-1"


def test_team_fields_on_a_plain_proxy_are_ignored():
    """구 유형에 팀 필드를 실어도 개인 그래프로 간다 — 유형이 유일한 스위치다."""
    app = _app()
    app.graph_owner_request(
        _sign({"typ": "proxy", "sub": "u-1", "op": "search",
               "graph_owner": TEAM, "role": "admin"}), {})
    assert app.bridge.last[2]["owner"] == "user:u-1"


def test_unknown_assertion_type_is_rejected():
    app = _app()
    with pytest.raises(PermissionError):
        app.graph_owner_request(_team_assertion(typ="proxy-team-v2"), {})


# ── 2. 그래프 소유자 형식 ────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    "user:0191f0aa-1111-7abc-8def-0123456789ab",   # 개인 소유자를 팀인 척
    "team:../../etc",
    "team:not-a-uuid",
    "team:",
    "",
])
def test_malformed_graph_owner_is_rejected(bad):
    """서명이 진짜여도 형식은 다시 본다 — 서명은 발신자를 증명할 뿐 값을 보증하지 않는다."""
    app = _app()
    with pytest.raises(ValueError):
        app.graph_owner_request(_team_assertion(owner=bad), {})


# ── 3. 역할 ──────────────────────────────────────────────────────────────────
def test_viewer_may_read_but_not_write():
    app = _app()
    app.graph_owner_request(_team_assertion(op="search", role="viewer"), {})
    with pytest.raises(PermissionError):
        app.graph_owner_request(_team_assertion(op="ingest-fragment", role="viewer"), {})


def test_editor_may_write_but_not_destroy():
    app = _app()
    app.graph_owner_request(_team_assertion(op="ingest-fragment", role="editor"), {})
    with pytest.raises(PermissionError):
        app.graph_owner_request(_team_assertion(op="delete", role="editor"), {})


def test_admin_may_destroy():
    app = _app()
    app.graph_owner_request(_team_assertion(op="delete", role="admin"), {})
    assert app.bridge.last[3]["owner"] == TEAM


@pytest.mark.parametrize("role", ["owner", "ADMIN", "", "superuser", None])
def test_unknown_roles_are_refused(role):
    app = _app()
    with pytest.raises(PermissionError):
        app.graph_owner_request(_team_assertion(op="search", role=role), {})


# ── 4. 역할 표 망라성(드리프트 방지) ─────────────────────────────────────────
def test_role_tiers_cover_every_proxy_op():
    """새 op 를 추가하면서 역할을 안 정하면 조회로 조용히 분류된다 — 쓰기 op 가
    열람자에게 열리는 사고가 그렇게 난다. 이 테스트가 그 침묵을 깬다."""
    tiers = hs.team_role_tiers()
    covered = tiers["viewer"] | tiers["editor"] | tiers["admin"]
    assert covered == set(hs._PROXY_OPS), (
        "역할 미지정 op: " + ", ".join(sorted(set(hs._PROXY_OPS) - covered))
    )


def test_write_ops_are_never_viewer_tier():
    """사이드카 POST 라우트는 전부 editor 이상이어야 한다 — 조회 등급으로 새면 안 된다."""
    tiers = hs.team_role_tiers()
    writes = {op for op, (method, _p) in hs._PROXY_OPS.items() if method == "POST"}
    leaked = writes & tiers["viewer"]
    assert not leaked, f"열람자 등급에 쓰기 op 가 있다: {sorted(leaked)}"


# ── 5. 팀 소거는 별도 문 ─────────────────────────────────────────────────────
def test_team_purge_uses_the_signed_graph_owner():
    app = _app()
    app.purge_team_request(_sign({"typ": "purge-team", "graph_owner": TEAM}))
    assert app.bridge.last[3] == {"owner": TEAM}


def test_personal_purge_cannot_reach_a_team_graph():
    """멤버 계정 삭제가 팀 그래프를 지울 수 없다는 보장의 근거(AP-1 §4.6)."""
    app = _app()
    app.purge_owner_request(_sign({"typ": "purge", "sub": TEAM}))
    # sub 가 무엇이든 `user:` 가 앞에 붙어 팀 소유자가 될 수 없다.
    assert app.bridge.last[3]["owner"] == f"user:{TEAM}"
    assert not hs._TEAM_OWNER_RE.fullmatch(app.bridge.last[3]["owner"])


def test_team_purge_rejects_the_personal_type():
    app = _app()
    with pytest.raises(PermissionError):
        app.purge_team_request(_sign({"typ": "purge", "sub": "u-1"}))


def test_team_purge_rejects_a_bad_owner():
    app = _app()
    with pytest.raises(ValueError):
        app.purge_team_request(_sign({"typ": "purge-team", "graph_owner": "user:u-1"}))
