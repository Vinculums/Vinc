# -*- coding: utf-8 -*-
"""스페이스마다 같은 분당 상한: 모든 플랜에 같고, 하루 한도보다 먼저 보며, 창이 지나면 풀린다.

REST 는 429 `rate_limited` 와 Retry-After, MCP 는 같은 뜻의 도구 오류 결과를 낸다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_rest_v1 import Server                      # noqa: E402
from vinc_mcp.bridge import Bridge                   # noqa: E402
from vinc_mcp.burst import BurstLimiter              # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp          # noqa: E402
from vinc_mcp.store import Store                     # noqa: E402


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")


def _app(burst=3, free_limit=100):
    cfg = {"free_limit": free_limit, "burst_per_minute": burst,
           "exempt_owners": frozenset({"user:me"})}
    return MCPHTTPApp(_StubBridge(), static_token="s", store=Store(":memory:"),
                      quota_config=cfg)


# ── 창 ────────────────────────────────────────────────────────────────────────
def test_the_window_slides_and_frees_the_oldest_call_first():
    lim = BurstLimiter(3, window_s=60)
    assert [lim.take("o", now=t) for t in (0, 10, 20)] == [0.0, 0.0, 0.0]
    assert lim.take("o", now=30) == pytest.approx(30)      # 0초 호출이 60초에 빠진다
    assert lim.take("o", now=60) == 0.0
    assert lim.take("o", now=61) == pytest.approx(9)       # 이제 가장 오래된 것은 10초 호출


def test_owners_do_not_share_a_window():
    lim = BurstLimiter(1)
    assert lim.take("a", now=0) == 0.0
    assert lim.take("b", now=0) == 0.0
    assert lim.take("a", now=1) > 0


def test_idle_owners_are_forgotten_when_the_table_is_full():
    lim = BurstLimiter(1, window_s=60, max_owners=2)
    lim.take("a", now=0)
    lim.take("b", now=0)
    assert lim.take("c", now=100) == 0.0
    assert set(lim._hits) == {"c"}


# ── 허브 ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("plan", ["free", "genius"])
def test_every_plan_gets_the_same_per_minute_limit(plan):
    app = _app(burst=3)
    for _ in range(3):
        assert app.quota_check("user:u1", plan) is None
    denial = app.quota_check("user:u1", plan)
    assert denial["isError"] is True
    rate = denial["_meta"]["vinc_rate"]
    assert rate["limit"] == 3 and rate["window_s"] == 60 and 1 <= rate["retry_after_s"] <= 60
    assert "3 tool calls a minute on every plan" in denial["content"][0]["text"]


def test_a_burst_denial_does_not_spend_the_daily_allowance():
    app = _app(burst=2, free_limit=100)
    for _ in range(5):
        app.quota_check("user:u1", "free")
    assert app.quota_snapshot("user:u1", "free")["count"] == 2


def test_exempt_owners_and_a_zero_setting_skip_the_limit():
    app = _app(burst=1)
    for _ in range(5):
        assert app.quota_check("user:me", "free") is None
    off = _app(burst=0)
    assert off.burst is None
    for _ in range(5):
        assert off.quota_check("user:u1", "free") is None


def test_rest_answers_rate_limited_with_retry_after():
    srv = Server(quota={"free_limit": 100, "burst_per_minute": 2})
    try:
        for _ in range(2):
            assert srv.request("GET", "/v1/domains")[0] == 200
        status, headers, body = srv.request("GET", "/v1/domains")
        assert status == 429 and body["error"]["code"] == "rate_limited"
        assert 1 <= int(headers["Retry-After"]) <= 60
        assert body["error"]["details"]["limit"] == 2
    finally:
        srv.close()
