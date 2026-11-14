# -*- coding: utf-8 -*-
"""P0 등급 배관 — 값 공간 확장·조용한 강등 금지·OAuth 동결 해소.

지키려는 것 셋(vinc.c.account-policy §2.2·§9 P0):

1. 계정이 새 등급 문자열(`genius`)을 먼저 내보내도 허브가 **무료로 강등하지 않는다.**
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import entitlement                     # noqa: E402
from vinc_mcp.bridge import Bridge                   # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp          # noqa: E402
from vinc_mcp.hub_state import Store                     # noqa: E402


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")

    def request(self, method, path, params=None, body=None, timeout=60):
        return {"executed": True}


# ── 1. 값 공간 · 조용한 강등 금지 ────────────────────────────────────────────
def test_known_plans_pass_through():
    for p in ("free", "genius"):
        assert entitlement.normalize_plan(p, source="t") == p


def test_retired_team_folds_to_genius_without_noise(caplog):
    """옛 Team 의 후속은 Genius 다(2026-10-17). 계정이 팀 어서션에 아직 `team` 을 싣고
    토큰·캐시에도 남아 있으므로, 그 값이 무료로 떨어지면 체험 팀이 조용히 강등된다."""
    with caplog.at_level(logging.WARNING, logger="vinc.mcp.entitlement"):
        assert entitlement.normalize_plan("team", source="account-assertion") == "genius"
    assert not caplog.records


def test_team_plan_field_passes_only_what_the_account_sent():
    """팀 소유자 등급은 멤버 개인 등급과 다른 필드다. 없으면 지어내지 않는다."""
    assert entitlement._team_plan_field({}, source="t") == {}
    assert entitlement._team_plan_field({"team_plan": "free"}, source="t") == {"team_plan": "free"}
    assert entitlement._team_plan_field({"team_plan": "team"}, source="t") == {"team_plan": "genius"}


def test_retired_pro_folds_to_free_without_noise(caplog):
    """Pro 폐기(2026-09-19) 후에도 옛 토큰·캐시가 그 값을 실어 온다. 그것은 배포
    순서 어긋남이 아니라 **의도된 폐기**라, 경고를 내면 거짓 경보가 된다."""
    with caplog.at_level(logging.WARNING, logger="vinc.mcp.entitlement"):
        assert entitlement.normalize_plan("pro", source="verify-key") == "free"
    assert not caplog.records


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


# ── 2. 한도 분기 — Curious 만 세고 Genius 는 세지 않는다 ───────────────────────
_QCFG = {"free_limit": 3}
_TEAM_ID = "0191f0aa-1111-7abc-8def-0123456789ab"
_TEAM = f"team:{_TEAM_ID}"


def _app(cfg=None):
    return MCPHTTPApp(_StubBridge(), static_token="S", store=Store(":memory:"),
                      quota_config=cfg or _QCFG)


def test_genius_is_not_metered_and_leaves_no_counter():
    app = _app()
    for _ in range(50):
        assert app.quota_check("user:g1", "genius") is None
    assert app.store.usage_rows() == []


def test_curious_denial_names_the_plan_the_reset_and_the_plans_page():
    """거절 문구는 등급·리셋 시각·플랜 페이지를 말한다. "Genius 는 상한이 없다"고
    약속하지 않는다: 이 코드는 사이트가 Genius 를 팔기 전에 나간다."""
    app = _app()
    for _ in range(3):
        assert app.quota_check("user:u1", "free") is None
    denial = app.quota_check("user:u1", "free")
    text = denial["content"][0]["text"]
    assert "Curious (free) plan" in text and "00:00 UTC" in text
    assert "vincs.io/pricing" in text and "Genius" not in text
    q = denial["_meta"]["vinc_quota"]
    assert q["limit"] == 3 and 0 < q["retry_after_s"] <= 86400
    assert q["resets_at"].endswith("T00:00:00Z")


def _team_app(team_plan=None, member_plan="free"):
    def verify(raw, space=None):
        if not space:
            return None
        resp = {"owner": _TEAM, "plan": member_plan, "scope": "full", "role": "editor"}
        if team_plan is not None:
            resp["team_plan"] = team_plan
        return resp
    return MCPHTTPApp(_StubBridge(), store=Store(":memory:"), quota_config=_QCFG,
                      verify_key=verify)


def test_a_team_space_takes_its_owner_plan_not_the_member_plan():
    """등급을 정하는 것은 공간이고, 팀 공간의 등급은 그 팀 소유자의 등급이다. Genius
    멤버가 Curious 팀에서 일하면 Curious 한도를 받는다(반대도 같다)."""
    app = _team_app(team_plan="free", member_plan="genius")
    auth = app.resolve_auth("Bearer vinc_sk_k", _TEAM_ID)
    assert auth["ok"] and auth["owner"] == _TEAM and auth["plan"] == "free"
    for _ in range(3):
        assert app.quota_check(_TEAM, auth["plan"]) is None
    assert app.quota_check(_TEAM, auth["plan"]) is not None

    app = _team_app(team_plan="genius", member_plan="free")
    assert app.resolve_auth("Bearer vinc_sk_k", _TEAM_ID)["plan"] == "genius"


def test_a_team_space_is_genius_until_the_account_sends_the_owner_plan():
    """VIN-649 전의 계정은 `team_plan` 을 싣지 않는다. 그동안 활성 팀은 전부 체험 중이라
    옛 Team 의 후속인 Genius 로 읽는다 — 멤버 개인 등급(free)으로 떨어지면 안 된다."""
    app = _team_app(team_plan=None, member_plan="free")
    assert app.resolve_auth("Bearer vinc_sk_k", _TEAM_ID)["plan"] == "genius"


# ── 3. OAuth 동결 해소 ───────────────────────────────────────────────────────
def test_fresh_plan_uses_cache_within_ttl(monkeypatch):
    app = _app()
    app.store.set_owner_plan("user:u1", "genius")
    calls = []
    monkeypatch.setattr(entitlement, "lookup_plan", lambda sub: calls.append(sub) or "free")
    # 토큰에 동결된 값은 free 지만, 캐시가 신선하면 계정을 때리지 않고 genius 를 쓴다.
    assert app._fresh_plan("user:u1", "free") == "genius"
    assert calls == []


def test_fresh_plan_requeries_after_ttl(monkeypatch):
    app = _app()
    app.store.set_owner_plan("user:u1", "free", now=0.0)   # 아주 오래된 값
    monkeypatch.setattr(entitlement, "lookup_plan", lambda sub: "genius")
    assert app._fresh_plan("user:u1", "free") == "genius"
    # 갱신분이 저장돼 다음 호출은 캐시에서 답한다(계정 부하 상한).
    assert app.store.get_owner_plan_age("user:u1")[0] == "genius"


def test_fresh_plan_keeps_last_known_when_account_is_down(monkeypatch):
    """계정 장애가 유료 사용자 전원 강등이 되면 안 된다."""
    app = _app()
    app.store.set_owner_plan("user:u1", "genius", now=0.0)
    monkeypatch.setattr(entitlement, "lookup_plan", lambda sub: None)
    assert app._fresh_plan("user:u1", "genius") == "genius"


def test_fresh_plan_falls_back_to_token_when_nothing_cached(monkeypatch):
    app = _app()
    monkeypatch.setattr(entitlement, "lookup_plan", lambda sub: None)
    assert app._fresh_plan("user:u1", "genius") == "genius"


def test_fresh_plan_leaves_non_user_owners_alone(monkeypatch):
    """팀 소유자는 개인 등급 조회 대상이 아니다(sub 를 잘라내면 안 된다)."""
    app = _app()
    monkeypatch.setattr(entitlement, "lookup_plan",
                        lambda sub: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")))
    assert app._fresh_plan("team:abc", "genius") == "genius"


def test_fresh_plan_is_noop_without_store(monkeypatch):
    app = MCPHTTPApp(_StubBridge(), static_token="S")   # store 없음
    monkeypatch.setattr(entitlement, "lookup_plan",
                        lambda sub: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")))
    assert app._fresh_plan("user:u1", "genius") == "genius"


# ── 4. 저장소 — 나이 판독 ────────────────────────────────────────────────────
def test_owner_plan_age_reports_missing_and_fresh():
    s = Store(":memory:")
    assert s.get_owner_plan_age("user:nobody") == (None, None)
    s.set_owner_plan("user:u1", "genius")
    plan, age = s.get_owner_plan_age("user:u1")
    assert plan == "genius" and age is not None and age < 5.0
