# -*- coding: utf-8 -*-
"""운영자 사용량 덤프(W1c) — `/internal/usage` 의 계약 셋.

  1. **그래프 층**(`q.owner_stats`): 소유자 전체의 노드·문서 수를 라벨별로 세고,
     관례 문서는 빼고, 레거시 무소유('')도 합계에서 버리지 않는다.
  2. **게이트 층**(`MCPHTTPApp.usage_request`): `typ=usage` 계정 서명만 통과한다 —
     위조·만료·다른 유형·지문 없음·재생 전부 거부하고, 거부되면 사이드카에 닿지 않는다.
     통과하면 그래프 수와 쿼터 카운터의 **현재 창**을 합친다.
  3. **노출 층**: 이 집계는 웹 프록시로 새지 않고, HTTP 로는 서명한 요청에만 답한다.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import query as q                     # noqa: E402
from vinc_mcp import edverify as ev            # noqa: E402
from vinc_mcp import http_server as hs         # noqa: E402
from vinc_mcp.bridge import Bridge             # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp    # noqa: E402
from vinc_mcp.store import Store               # noqa: E402

ACCT_SEED = bytes((i * 11 + 5) & 0xFF for i in range(32))
BODY = b"{}"                                    # 계정이 보내는 본문


# ── 1) 그래프 층 ───────────────────────────────────────────────────────────────
class _StatsStore:
    """라벨별 (owner, count) 행과 _DocMeta 행을 돌려주는 대역. run 호출을 기록한다."""

    def __init__(self, by_label: dict[str, list[tuple[str, int]]], docs: list[tuple[str, str]]):
        self.by_label, self.docs = by_label, docs
        self.calls: list[str] = []

    def run(self, cypher: str, params=None):
        self.calls.append(cypher)
        if "_DocMeta" in cypher:
            return [{"owner": o, "doc_id": d} for o, d in self.docs]
        label = cypher.split("(n:", 1)[1].split(")", 1)[0]
        return [{"owner": o, "c": c} for o, c in self.by_label.get(label, [])]


def test_owner_stats_counts_every_owner_per_label():
    store = _StatsStore(
        {"Concept": [("user:a", 5), ("team:t", 2)], "Record": [("user:a", 3)],
         "Episode": [("user:a", 1)], "Decision": [("team:t", 4)],
         "Entity": [("user:a", 7)], "Bridge": [("", 1)]},
        docs=[("user:a", "d1"), ("user:a", "d2"), ("team:t", "d3")])
    out = q.owner_stats(store=store)

    assert out["executed"]
    a, t = out["owners"]["user:a"], out["owners"]["team:t"]
    assert (a["concepts"], a["records"], a["episodes"], a["decisions"]) == (5, 3, 1, 0)
    # `nodes` 는 운영자가 부르는 노드 수 = 넷의 합. Entity·Bridge 는 따로 싣는다
    assert a["nodes"] == 9 and a["entities"] == 7 and a["docs"] == 2
    assert t["nodes"] == 6 and t["docs"] == 1
    # 레거시 무소유는 버리지 않는다 — 버리면 합계가 안 맞고 이유를 아무도 모른다
    assert out["owners"][""]["bridges"] == 1
    # 소유자를 가로지르는 조회다: owner 술어가 없어야 전체가 잡힌다
    assert all("$o" not in cy and "owner_id =" not in cy for cy in store.calls)


def test_owner_stats_leaves_the_conventions_document_out():
    from api import graphmd

    conv = graphmd.LEGACY_CONVENTIONS_DOC_ID
    store = _StatsStore({}, docs=[("user:a", conv), ("user:a", "d1")])
    assert q.owner_stats(store=store)["owners"]["user:a"]["docs"] == 1


def test_owner_stats_reports_a_failed_read_instead_of_zero():
    class _Boom:
        def run(self, *_a, **_k):
            raise RuntimeError("disk gone")

    out = q.owner_stats(store=_Boom())
    assert not out["executed"] and "disk gone" in out["error"]
    assert "owners" not in out, "읽지 못한 것을 빈 목록으로 내면 데이터가 사라진 것처럼 보인다"


# ── 2) 게이트 층 ───────────────────────────────────────────────────────────────
class _StatsBridge(Bridge):
    def __init__(self, reply: dict | None = None):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.calls: list[tuple] = []
        self.reply = reply if reply is not None else {
            "kind": "owner.stats", "executed": True,
            "owners": {"user:a": {"nodes": 9, "docs": 2}, "team:t": {"nodes": 6, "docs": 1}}}

    def request(self, method, path, params=None, body=None, timeout=60, **_kw):
        self.calls.append((method, path))
        return self.reply


def _bh(raw: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()


def _usage_assertion(seed=ACCT_SEED, raw=BODY, **over):
    now = int(time.time())
    body = {"typ": "usage", "iss": "account", "aud": "mcp", "sub": "operator",
            "iat": now, "exp": now + 120, "bh": _bh(raw), "jti": uuid.uuid4().hex}
    body.update(over)
    return ev.sign_assertion(seed, body)


_QCFG = {"free_limit": 2000, "team_limit": 6000, "window_s": 86400.0, "lock_s": 43200.0,
         "exempt_owners": frozenset({"user:me"})}
T0 = 1_760_000_000.0


@pytest.fixture
def app():
    store = Store(":memory:")
    # user:a 는 지금 창 안에서 3번, user:old 는 창이 지난 옛 호출 5번,
    # user:locked 는 창은 지났어도 잠금이 살아 있다.
    for _ in range(3):
        store.quota_check("user:a", limit=10, window_s=86400, lock_s=43200, now=T0 - 60)
    for _ in range(5):
        store.quota_check("user:old", limit=10, window_s=86400, lock_s=43200,
                          now=T0 - 2 * 86400)
    for _ in range(3):   # 한도 2 → 세 번째에서 잠금
        store.quota_check("user:locked", limit=2, window_s=3600, lock_s=86400 * 3,
                          now=T0 - 2 * 86400)
    return MCPHTTPApp(_StatsBridge(), static_token="t", store=store, quota_config=_QCFG,
                      account_public=ev.secret_to_public(ACCT_SEED))


def test_usage_merges_graph_counts_with_the_current_window(app):
    out = app.usage_request(_usage_assertion(), raw_body=BODY, now=T0)

    assert out["executed"] and out["kind"] == "usage.dump"
    assert app.bridge.calls == [("GET", "/owner-stats")]
    rows = {r["owner"]: r for r in out["owners"]}
    assert rows["user:a"]["graph"] == {"nodes": 9, "docs": 2}
    assert rows["user:a"]["calls"]["count"] == 3
    assert rows["user:a"]["calls"]["window_start"] is not None
    # 창이 지난 행은 0 이다 — 표의 5 를 그대로 내면 이틀 전 호출이 오늘 것으로 보인다
    assert rows["user:old"]["calls"]["count"] == 0
    assert rows["user:old"]["calls"]["window_start"] is None
    # 잠금은 창과 무관하게 살아 있는 동안 그 값을 낸다
    assert rows["user:locked"]["calls"]["count"] == 2
    assert rows["user:locked"]["calls"]["locked_until"].endswith("Z")
    # 호출은 없고 노드만 있는 소유자(팀)도 빠지지 않는다
    assert rows["team:t"]["calls"] == {"count": 0, "window_start": None, "locked_until": None}
    # 노드가 없고 호출만 있는 소유자는 빈 graph
    assert rows["user:old"]["graph"] == {}
    q_ = out["quota"]
    assert (q_["free_limit"], q_["team_limit"], q_["window_s"]) == (2000, 6000, 86400.0)
    assert q_["exempt_owners"] == ["user:me"]
    assert out["generated_at"].endswith("Z")
    # 이메일·제목 같은 것은 애초에 싣지 않는다 — owner 와 숫자뿐
    assert set(rows["user:a"]) == {"owner", "graph", "calls"}


@pytest.mark.parametrize("assertion", [
    lambda: _usage_assertion(seed=bytes(32)),             # 공격자 키
    lambda: _usage_assertion(iat=1, exp=2),               # 만료
    lambda: _usage_assertion(typ="purge"),                # 다른 유형의 어서션
    lambda: _usage_assertion(typ="proxy"),
    lambda: _usage_assertion(iss="mcp"),                  # 우리 자신을 사칭
    lambda: _usage_assertion(bh=""),                      # 지문 없음
    lambda: _usage_assertion(jti=""),                     # 1회용 id 없음
    lambda: _usage_assertion(raw=b'{"x":1}'),             # 다른 본문에 서명
])
def test_usage_refuses_anything_but_a_fresh_signed_usage_request(app, assertion):
    with pytest.raises(PermissionError):
        app.usage_request(assertion(), raw_body=BODY, now=T0)
    assert app.bridge.calls == [], "거부된 요청은 사이드카에 닿으면 안 된다"


def test_a_captured_usage_assertion_cannot_be_replayed(app):
    a = _usage_assertion()
    app.usage_request(a, raw_body=BODY, now=T0)
    with pytest.raises(PermissionError):
        app.usage_request(a, raw_body=BODY, now=T0)
    assert len(app.bridge.calls) == 1


def test_usage_disabled_without_account_public():
    app = MCPHTTPApp(_StatsBridge(), static_token="t")
    with pytest.raises(PermissionError):
        app.usage_request(_usage_assertion(), raw_body=BODY)


def test_an_unreadable_graph_is_an_error_not_zero_nodes():
    app = MCPHTTPApp(_StatsBridge({"executed": False, "error": "no reachable store"}),
                     static_token="t", account_public=ev.secret_to_public(ACCT_SEED))
    with pytest.raises(RuntimeError, match="no reachable store"):
        app.usage_request(_usage_assertion(), raw_body=BODY)


# ── 3) 노출 층 ─────────────────────────────────────────────────────────────────
def test_the_web_proxy_cannot_reach_the_all_owner_counts():
    """웹 프록시 허용목록에 올리면 로그인한 누구나 남의 규모를 본다."""
    assert all(path != "/owner-stats" for _m, path in hs._PROXY_OPS.values())


def _post(port: int, assertion: str | None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if assertion is not None:
        headers["X-Vinc-Assertion"] = assertion
    conn.request("POST", "/internal/usage", body=BODY, headers=headers)
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read() or b"{}")


def test_http_route_answers_only_a_signed_request(app):
    httpd = hs.make_server(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        assert _post(port, None)[0] == 401
        assert _post(port, _usage_assertion(typ="purge"))[0] == 401
        assert app.bridge.calls == []
        status, body = _post(port, _usage_assertion())
        assert status == 200 and body["kind"] == "usage.dump"
        assert {r["owner"] for r in body["owners"]} >= {"user:a", "team:t"}
    finally:
        httpd.shutdown()
