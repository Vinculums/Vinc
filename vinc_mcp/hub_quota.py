# -*- coding: utf-8 -*-
"""허브의 호출 한도: Curious 의 하루 한도, 모든 플랜에 같은 분당 상한, 남은 호출 수의 표시.

`MCPHTTPApp` 의 `quota_snapshot` · `quota_view` · `quota_meta` · `quota_check` · `quota_for` 는 여기
같은 이름의 함수로 한 줄씩 넘긴다. 함수는 앱을 첫 인자로 받아 앱의 `quota_config` · `store` ·
`burst` 를 읽는다. 쿼터 설정이나 스토어가 없으면(로컬) 무제한이다.

⚠ 이 함수들이 서로를 부를 때는 모듈 함수를 바로 부르지 않고 앱의 메서드를 거친다
(`app.quota_snapshot` · `app.quota_view` · `app.quota_check`). 앱 인스턴스에서 그 메서드를 갈아
끼운 쪽(테스트·프로브)이 `/mcp` 와 REST 경로 안에서도 그대로 닿아야 한다.
"""
from __future__ import annotations

import math
import time

from . import community, entitlement

#: 팀 그래프의 등급은 **그 팀 소유자의 등급**이다 — 사람이 아니라 공간이 등급을 정한다는
#: 규칙을 넓힌 것이다. 멤버의 개인 등급(`plan`)을 쓰면 안 된다: Curious 멤버가 Genius 팀에서
#: 일하면 Genius 한도를 받아야 한다. 카운터는 owner 단위라 팀 한도는 **팀이 나눠 쓴다**.
#: 계정이 `team_plan` 을 싣지 않으면 Genius 로 읽는다: 지금 활성인 팀은 전부 체험
#: (`TRIAL_UNTIL`) 중이고 Team 의 후속이 Genius 다.
#: 근거: [[vinc.canon.account-policy §8]]
_LEGACY_TEAM_PLAN = "genius"

#: 하루 창은 00:00 UTC 에 리셋되고 한도 뒤에 잠그지 않는다(약관의 "다음 날까지"와 같은 규칙).
_DAY_S = 86400.0
#: 한도 표시(`quota_view`)가 창을 부르는 이름. 굴러가는 24시간이 아니라 UTC 달력의 하루다.
#: 창 길이나 정렬을 바꾸면 이 값과 도구 참조문(`vinc_whoami`)을 같이 바꾼다.
_WINDOW = "utc_day"


def _current_plan(plan: str | None) -> str | None:
    """폐기된 와이어 값(`team`·`pro`)을 지금 등급으로 접는다. 웹 프록시 어서션은 팀에 아직
    `team` 을 싣고 정규화를 거치지 않으므로, 여기서 안 접으면 무료 쪽으로 떨어진다."""
    return entitlement.RETIRED_PLANS.get(plan, plan) if plan is not None else None


#: 하루 사용량이 이 비율에 닿으면 응답에 경고 한 줄과 플랜 주소를 싣는다.
#: 근거: [[vinc.plan.plans-and-limits-redesign-2026-10-17 §10.6]]
_WARN_RATIO = 0.8
_PLANS_URL = "https://vincs.io/pricing"


def _reset_phrase(resets_at: float, now: float) -> str:
    """리셋 시각을 문장 조각으로. 시각은 상수로 쓰지 않는다: 00:00 UTC 정렬보다 먼저 열린
    카운터는 그 창이 끝날 때까지(하루 안) 다른 시각에 리셋된다."""
    secs = max(0, int(math.ceil(resets_at - now)))
    hh, mm = secs // 3600, (secs % 3600) // 60
    return f"at {time.strftime('%H:%M', time.gmtime(resets_at))} UTC, in {hh}h {mm:02d}m"


def _burst_denial(limit: int, window_s: float, wait: float) -> dict:
    """분당 상한 거절. 하루 한도 거절과 같은 tools/call 결과 봉투이고 `_meta` 키만 다르다
    (`vinc_rate`): REST 가 이 키를 보고 `rate_limited` 로 낸다."""
    secs = max(1, int(math.ceil(wait)))
    text = (f"Too many calls in a short time: each space allows {limit} tool calls a minute "
            f"on every plan. Try again in {secs}s.")
    return {"content": [{"type": "text", "text": text}], "isError": True,
            "_meta": {"vinc_rate": {"limit": limit, "window_s": int(window_s),
                                    "retry_after_s": secs}}}


def _with_quota_meta(reply: dict | None, meta: dict | None) -> dict | None:
    """성공한 tools/call 응답에 남은 호출 수를 싣는다. 경고가 있으면 본문에도 한 줄을 더한다:
    `_meta` 는 호스트가 모델에게 안 보여 주는 경우가 많아, 사람에게 전할 문장은 본문에 있어야
    한다. 오류 결과·다른 메서드·세지 않은 스페이스는 그대로 둔다. 인용 쌍(`structuredContent`
    를 싣는 search·fetch)은 본문 모양이 계약이라 `_meta` 에만 싣는다."""
    result = reply.get("result") if isinstance(reply, dict) else None
    if not meta or not isinstance(result, dict) or result.get("isError"):
        return reply
    result.setdefault("_meta", {})["vinc_quota"] = meta
    if (meta.get("warning") and isinstance(result.get("content"), list)
            and "structuredContent" not in result):
        result["content"].append({"type": "text", "text": meta["warning"]})
    return reply


def _utc_iso(ts: float) -> str:
    """epoch 초 → `2026-09-25T08:24:15Z`. 덤프가 내는 시각은 전부 이 모양이다."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def quota_snapshot(app, owner: str, plan: str | None) -> dict | None:
    """사용량 읽기(`quota_view` 를 거쳐 whoami·`_meta`·계정 화면이 쓴다). 소비·리셋·
    잠금 생성은 하지 않고, 세는 스페이스면 카운터 한 줄(owner 기본키)만 읽는다.

    계정 정책의 그래프 owner 기준을 사용한다. 미설정·면제는 유한 잔여량으로
    꾸미지 않는다. 호출자는 인증으로 결정한 owner만 전달해야 한다.
    """
    if not app.quota_config or app.store is None or not owner:
        return None
    cfg = app.quota_config
    if owner in cfg.get("exempt_owners", ()):
        return {"count": 0, "remaining": None, "exempt": True}
    if _current_plan(plan) in entitlement.PAID_PLANS:
        # 유료는 세지 않는다. 남은 수를 지어내지 않고 "계량 안 함"이라고 말한다.
        return {"count": 0, "remaining": None, "limit": None, "exempt": False,
                "unmetered": True}
    limit = cfg["free_limit"]
    return {**app.store.quota_snapshot(owner, limit=limit, window_s=_DAY_S, aligned=True),
            "limit": limit, "exempt": False}


def quota_view(app, owner: str, plan: str | None) -> dict | None:
    """한 스페이스의 한도 표시. `/v1/me`·MCP `_meta`·계정 화면이 같은 모양을 읽는다.

    세지 않는 스페이스는 `metered: False` 로 말하고 수를 지어내지 않는다. 그 이유가
    면제(`exempt: True`)인지 등급(`plan`)인지는 갈라 말한다. 세는 스페이스는 창 이름
    (`window`)·한도·쓴 수·남은 수·리셋 시각을 싣는다.
    ⚠ 싣는 것은 인자로 받은 owner 의 숫자뿐이고 owner id·이메일은 싣지 않는다. 호출자는
    인증으로 정한 owner(또는 계정이 그 사람의 멤버십으로 준 팀)만 넘겨야 한다.
    팀 owner 의 plan 이 None 이면 계정이 팀 등급을 아직 안 실은 것이라 `_team_plan` 과
    같이 읽는다. 쿼터가 꺼져 있으면(로컬·미설정) None.
    """
    if plan is None and owner.startswith("team:"):
        plan = _LEGACY_TEAM_PLAN
    snap = app.quota_snapshot(owner, plan)
    if snap is None:
        return None
    if snap.get("exempt"):
        return {"metered": False, "exempt": True}
    if snap.get("limit") is None:
        return {"metered": False, "exempt": False, "plan": _current_plan(plan)}
    limit, used, left, resets_at = (snap["limit"], snap["count"], snap["remaining"],
                                    snap["resets_at"])
    view = {"metered": True, "exempt": False, "plan": "free", "window": _WINDOW,
            "limit": limit, "used": used, "remaining": left,
            "resets_at": _utc_iso(resets_at) if resets_at else None}
    if used >= limit * _WARN_RATIO:
        when = f" They reset {_reset_phrase(resets_at, time.time())}." if resets_at else ""
        view["warning"] = (f"You have used {used:,} of {limit:,} tool calls today on the "
                           f"Curious (free) plan, {left:,} left.{when} Plans: {_PLANS_URL}")
        view["upgrade_url"] = _PLANS_URL
    return view


def quota_meta(app, spent: dict) -> dict | None:
    """이번 메시지가 센 스페이스의 표시. 안 셌거나 세지 않는 스페이스면 None."""
    if not spent:
        return None
    view = app.quota_view(spent["owner"], spent["plan"])
    return view if view and view.get("metered") else None


def quota_check(app, owner: str, plan: str | None) -> dict | None:
    """소비 1회 시도. 허용이면 None, 초과면 **tools/call 결과 dict**.

    카운터는 owner(public_id) 단위 — 키를 여러 개 만들어도 합산된다(우회 불가).
    `exempt_owners` 에 든 owner 와 유료(Genius) 스페이스는 세지 않는다.
    Curious 는 하루 `free_limit` 회이고 00:00 UTC 에 리셋된다(잠금 없음).
    면제가 아니면 플랜과 무관하게 먼저 분당 상한(`app.burst`)을 거친다.
    """
    if not app.quota_config or app.store is None or not owner:
        return None                              # 미설정/로컬 = 무제한
    cfg = app.quota_config
    if owner in cfg.get("exempt_owners", ()):
        return None                              # 면제 owner — 도그푸딩 계정
    if app.burst is not None:                    # 모든 플랜에 같다. 하루 한도보다 먼저 본다
        wait = app.burst.take(owner)
        if wait:
            return _burst_denial(app.burst.limit, app.burst.window_s, wait)
    if _current_plan(plan) in entitlement.PAID_PLANS:
        return None                              # Genius: 하루 상한 없음(공정 사용)
    limit = cfg["free_limit"]
    r = app.store.quota_check(owner, limit=limit, window_s=_DAY_S, lock_s=0,
                              aligned=True)
    if r["allowed"]:
        return None
    secs = r["retry_after_s"]
    now = time.time()
    resets_at = r.get("resets_at") or now + secs
    # 업셀은 "Genius 는 상한이 없다"가 아니라 플랜 페이지만 가리킨다: 이 코드는 사이트가
    # Genius 를 팔기 전에 배포되므로, 아직 살 수 없는 것을 약속하면 거짓 문구가 된다.
    text = (f"Daily limit reached on the Curious (free) plan: {limit} tool calls a day "
            f"across MCP, CLI and API. It resets {_reset_phrase(resets_at, now)}. "
            f"Plans: {_PLANS_URL}")
    return {"content": [{"type": "text", "text": text}], "isError": True,
            "_meta": {"vinc_quota": {
                "limit": limit, "retry_after_s": secs,
                "resets_at": _utc_iso(resets_at) if resets_at else None,
                "upgrade_url": "https://vincs.io/account"}}}


def quota_for(app, auth: dict, spent: dict | None = None):
    """메시지 하나의 한도 소비 함수. 허용된 소비는 센 owner·plan 을 `spent` 에 남겨서
    응답이 남은 수를 실을 수 있게 한다(`quota_meta`)."""
    # 공개 커뮤니티는 공동 쿼터를 소비하지 않는다. 인증된 사람의 기존 한도를 쓴다.
    in_community = community.is_space(auth.get("owner"))

    def spend(owner, plan):
        if in_community:
            owner, plan = auth.get("member"), auth.get("plan")
        denial = app.quota_check(owner, plan)
        if denial is None and spent is not None and owner:
            spent.update(owner=owner, plan=plan)
        return denial
    return spend
