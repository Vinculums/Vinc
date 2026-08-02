# -*- coding: utf-8 -*-
"""P0 등급 배관 — 값 공간 확장·조용한 강등 금지·OAuth 동결 해소.

지키려는 것 셋(vinc.c.account-policy §2.2·§9 P0):

1. 계정이 새 등급 문자열(`team`)을 먼저 내보내도 허브가 **무료로 강등하지 않는다.**
   미지 값은 여전히 free 로 떨어지지만 **경고 로그를 남긴다** — 조용한 강등은
   "결제했는데 무료로 취급"으로 나타나고 원인(배포 순서)이 증상에서 안 보인다.
2. OAuth 토큰에 동결된 등급을 TTL 주기로 계정에 되묻는다.
3. 계정이 불통이면 **마지막으로 알던 값을 유지**한다(free 강등 금지).
   인증이 아니라 자격이라 fail-closed 가 아니라 fail-open to last-known 이 옳다.
"""
from __future__ import annotations

import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp import entitlement                     # noqa: E402
from vinc_mcp.bridge import Bridge                   # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp          # noqa: E402
from vinc_mcp.store import Store                     # noqa: E402


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")

    def request(self, method, path, params=None, body=None, timeout=60):
        return {"executed": True}


# ── 1. 값 공간 · 조용한 강등 금지 ────────────────────────────────────────────
def test_known_plans_pass_through():
    for p in ("free", "pro", "team"):
        assert entitlement.normalize_plan(p, source="t") == p


def test_unknown_plan_degrades_to_free_but_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="vinc.mcp.entitlement"):
        assert entitlement.normalize_plan("enterprise", source="verify-key") == "free"
    assert any("enterprise" in r.getMessage() for r in caplog.records), \
        "미지 등급 강등이 조용하면 안 된다"


def test_missing_plan_is_free_without_noise(caplog):
    """plan 자체가 없는 것은 정상(구 토큰·정적 경로) — 경고를 내면 로그가 무의미해진다."""
    with caplog.at_level(logging.WARNING, logger="vinc.mcp.entitlement"):
        assert entitlement.normalize_plan(None, source="oauth") == "free"
    assert not caplog.records


# ── 2. 한도 분기 — team 은 유료 취급 ─────────────────────────────────────────
_QCFG = {"free_limit": 3, "pro_limit": 10, "window_s": 86400, "lock_s": 43200}


def _app(cfg=None):
    return MCPHTTPApp(_StubBridge(), static_token="S", store=Store(":memory:"),
                      quota_config=cfg or _QCFG)


def test_team_gets_the_paid_ceiling_not_the_free_one():
    app = _app()
    for _ in range(10):
        assert app.quota_check("team:t1", "team") is None
    denial = app.quota_check("team:t1", "team")
    assert denial["_meta"]["vinc_quota"]["limit"] == 10
    assert "Team tier limit reached" in denial["content"][0]["text"]
    assert "Upgrade" not in denial["content"][0]["text"]


def test_upsell_text_stops_lying_when_free_equals_pro():
    """AP-1 P1 이후 무료 한도가 Pro 와 같아진다 — '더 높은 한도로 업그레이드'는 거짓이 된다."""
    same = {"free_limit": 10, "pro_limit": 10, "window_s": 86400, "lock_s": 43200}
    app = _app(same)
    for _ in range(10):
        app.quota_check("user:u1", "free")
    text = app.quota_check("user:u1", "free")["content"][0]["text"]
    assert "higher limit" not in text
    assert "topic limit" in text                      # 업셀 근거가 주제 무제한으로 이동


def test_upsell_still_mentions_limit_while_tiers_differ():
    app = _app()                                       # free 3 < pro 10
    for _ in range(3):
        app.quota_check("user:u1", "free")
    assert "higher limit" in app.quota_check("user:u1", "free")["content"][0]["text"]


# ── 3. OAuth 동결 해소 ───────────────────────────────────────────────────────
def test_fresh_plan_uses_cache_within_ttl(monkeypatch):
    app = _app()
    app.store.set_owner_plan("user:u1", "pro")
    calls = []
    monkeypatch.setattr(entitlement, "lookup_plan", lambda sub: calls.append(sub) or "free")
    # 토큰에 동결된 값은 free 지만, 캐시가 신선하면 계정을 때리지 않고 pro 를 쓴다.
    assert app._fresh_plan("user:u1", "free") == "pro"
    assert calls == []


def test_fresh_plan_requeries_after_ttl(monkeypatch):
    app = _app()
    app.store.set_owner_plan("user:u1", "free", now=0.0)   # 아주 오래된 값
    monkeypatch.setattr(entitlement, "lookup_plan", lambda sub: "pro")
    assert app._fresh_plan("user:u1", "free") == "pro"
    # 갱신분이 저장돼 다음 호출은 캐시에서 답한다(계정 부하 상한).
    assert app.store.get_owner_plan("user:u1") == "pro"


def test_fresh_plan_keeps_last_known_when_account_is_down(monkeypatch):
    """계정 장애가 유료 사용자 전원 강등이 되면 안 된다."""
    app = _app()
    app.store.set_owner_plan("user:u1", "pro", now=0.0)
    monkeypatch.setattr(entitlement, "lookup_plan", lambda sub: None)
    assert app._fresh_plan("user:u1", "pro") == "pro"


def test_fresh_plan_falls_back_to_token_when_nothing_cached(monkeypatch):
    app = _app()
    monkeypatch.setattr(entitlement, "lookup_plan", lambda sub: None)
    assert app._fresh_plan("user:u1", "pro") == "pro"


def test_fresh_plan_leaves_non_user_owners_alone(monkeypatch):
    """팀 소유자는 개인 등급 조회 대상이 아니다(sub 를 잘라내면 안 된다)."""
    app = _app()
    monkeypatch.setattr(entitlement, "lookup_plan",
                        lambda sub: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")))
    assert app._fresh_plan("team:abc", "team") == "team"


def test_fresh_plan_is_noop_without_store(monkeypatch):
    app = MCPHTTPApp(_StubBridge(), static_token="S")   # store 없음
    monkeypatch.setattr(entitlement, "lookup_plan",
                        lambda sub: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")))
    assert app._fresh_plan("user:u1", "pro") == "pro"


# ── 4. 저장소 — 나이 판독 ────────────────────────────────────────────────────
def test_owner_plan_age_reports_missing_and_fresh():
    s = Store(":memory:")
    assert s.get_owner_plan_age("user:nobody") == (None, None)
    s.set_owner_plan("user:u1", "pro")
    plan, age = s.get_owner_plan_age("user:u1")
    assert plan == "pro" and age is not None and age < 5.0
