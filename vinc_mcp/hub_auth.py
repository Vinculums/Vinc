# -*- coding: utf-8 -*-
"""허브가 받은 베어러를 신원으로 푼다: 정적 토큰, 회원 키(`vinc_sk_` · `vinc_ro_`), OAuth 토큰,
그리고 팀 주소와 커뮤니티 주소의 멤버십.

`MCPHTTPApp` 의 `resolve_auth` · `_resolve_self_hosted` · `_resource_ok` · `_fresh_plan` 은 여기 같은
이름의 함수로 한 줄씩 넘긴다. 함수는 앱을 첫 인자로 받아 앱의 `static_token` · `oauth` ·
`verify_key` · `verify_member` · `verify_community` · `store` 를 읽는다.

⚠ 이 함수들이 서로를 부를 때는 모듈 함수를 바로 부르지 않고 앱의 메서드를 거친다
(`app.resolve_auth` · `app._resolve_self_hosted` · `app._resource_ok` · `app._fresh_plan`). 앱
인스턴스에서 그 메서드를 갈아 끼운 쪽(테스트)이 요청 경로 안에서도 그대로 닿아야 한다.
"""
from __future__ import annotations

import secrets

from . import community, entitlement, selfhost, tools
from .hub_quota import _LEGACY_TEAM_PLAN


def _team_plan(resp: dict) -> str:
    """팀 멤버십 응답(verify-key·verify-member) → 그 팀 스페이스의 등급."""
    raw = resp.get("team_plan")
    if raw is None:
        return _LEGACY_TEAM_PLAN
    return entitlement.normalize_plan(raw, source="team-plan")


def resolve_auth(app, header: str | None, space: str | None = None,
                 *, presented_at: str = "/mcp") -> dict:
    """Bearer 를 해석해 {ok, owner, plan, scope, source[, role, member]}. 스탬핑의 출처.

    `scope`(W18, [[decision:scoped-key-tier]]): 'full' 정확일치만 쓰기를 연다.
    키 경로는 verify_key 가 실어 준 값을 그대로 쓰되 **부재는 'read'** 다
    (fail-closed — 스코프를 모르는 채 쓰기를 여는 것이 최악의 고장이다).
    static·oauth 는 스코프 개념 이전의 신원이라 'full'(기존 동작 무변화).

    ``space``(팀 uuid)가 오면 **키와 OAuth 둘 다** 통과한다(#120). 전에는 키
    전용이었는데, 그 제약의 근거는 `resource_metadata` 가 `issuer + "/mcp"` 로
    하드코딩돼 팀 주소의 커넥터가 자기와 안 맞는 resource 를 발견한다는 것이었다.
    그 메타데이터가 경로 인식이 되면서(`OAuthGate.resource_metadata(path)`) 제약의
    근거가 사라졌다.

    ⚠ 이 제약을 푸는 것이 왜 중요한가: claude.ai 커스텀 커넥터는 임의 헤더를 못
    넣는다. 키 전용이면 팀 그래프는 `--header` 를 쓸 수 있는 Claude Code 에서만
    닿고, 그건 사실상 "팀은 개발자만 쓸 수 있다"가 된다. 그리고 그 불편이
    "개인 커넥터에서 팀도 되게 해 달라"는 요구를 만드는데, 그 요구를 들어주면
    스페이스가 도구 인자가 되어 팀 쓰기가 조용히 개인 그래프에 쌓인다. **주소 =
    스페이스** 불변식을 지키면서 같은 불편을 없애는 길이 이쪽이다.

    OAuth 팀 경로의 규칙은 키 경로와 **같다**:
    - 토큰이 유효해도 그것만으로는 아무것도 아니다. `verify_member` 로 계정에
      "이 사람이 그 팀의 활성 멤버인가"를 따로 묻는다(키 경로가 verify_key 에
      team 을 실어 묻는 것과 같은 질문, 다른 라우트).
    - 실패는 **개인으로 떨어지지 않는다.** 거절이다.
    - `role` 이 함께 와야 하고, 없으면 거절이다(역할 게이트의 입력이다).

    정적 토큰은 여전히 못 쓴다 — owner 가 아예 없어 멤버십을 물을 대상이 없다.
    관리 키 부트스트랩으로 발급된 OAuth 토큰도 같다(owner=None).

    - static: 정적 토큰 = 단일 테넌트 하위호환(owner=None → bridge.owner 사용).
    - apikey: `vinc_sk_*` → verify_key 루프백 → owner=user:{public_id}, plan.
    - oauth : 발급 토큰 → 코드에 스탬핑된 owner/plan(회원 경로면 채워짐).
              등급은 **동결돼 있으므로** `_fresh_plan` 이 주기적으로 되묻는다.
    """
    miss = {"ok": False, "owner": None, "plan": None, "scope": None, "source": None}
    if not header or not header.startswith("Bearer "):
        return miss
    if selfhost.enabled():
        return app._resolve_self_hosted(header, space, presented_at, miss)
    if community.is_space(space):
        from store import pkns
        if not pkns.enabled() or app.verify_community is None:
            return miss
        base = app.resolve_auth(header, presented_at=presented_at)
        actor = base.get("owner") or ""
        if not base.get("ok") or not actor.startswith("user:"):
            return miss
        resolved = app.verify_community(actor[5:], space.lower())
        if (not resolved or resolved.get("owner") != space.lower()
                or resolved.get("visibility") != "public"
                or resolved.get("role") not in ("reader", "operator")):
            return miss
        return {**base, "owner": space.lower(), "role": resolved["role"],
                "member": actor, "visibility": "public"}
    if space:
        # 팀 주소. 키와 OAuth 둘 다 받되 **개인으로 떨어지지 않는다** — 실패는 실패다.
        tok = header[7:].strip()
        if tok.startswith(entitlement.KEY_PREFIXES):
            if app.verify_key is None:
                return miss
            ent = app.verify_key(tok, space)
            if not ent or not str(ent.get("owner") or "").startswith("team:"):
                return miss
            return {"ok": True, "owner": ent["owner"], "plan": _team_plan(ent),
                    "scope": ent.get("scope") or "read",
                    "source": "apikey", "role": ent.get("role"),
                    "member": ent.get("member")}
        # OAuth(#120). 토큰이 유효한 것만으로는 아무것도 아니다 — 그 토큰의 사람이
        # 이 팀의 활성 멤버인지 계정에 **따로** 묻는다. 정적 토큰은 여기 못 온다
        # (아래 static 분기는 space 가 없을 때만 닿는다) — owner 가 없어 물을
        # 대상이 없기 때문이고, 관리 키로 발급된 OAuth 토큰(owner=None)도 같다.
        if app.oauth is None or app.verify_member is None:
            return miss
        rec = app.oauth.resolve(tok)
        owner = (rec or {}).get("owner") or ""
        if not owner.startswith("user:"):
            return miss
        if not app._resource_ok(rec, presented_at):
            return miss
        mem = app.verify_member(owner.split(":", 1)[1], space)
        if not mem or not str(mem.get("owner") or "").startswith("team:"):
            return miss
        if not mem.get("role"):
            return miss   # 역할 없이 통과시키면 role_allows 가 전부 거부하거나
                          # (조용한 고장) 빈 문자열이 표를 비켜 간다.
        # scope 가 'full' 인 것은 OAuth 가 스코프 개념 이전의 신원이라서다(개인
        # 경로와 같다). 팀에서 무엇을 할 수 있는지는 scope 가 아니라 **role** 이
        # 정한다 — 그 게이트는 do_POST 에 있다.
        return {"ok": True, "owner": mem["owner"], "plan": _team_plan(mem),
                "scope": "full", "source": "oauth", "role": mem["role"],
                "member": mem.get("member")}
    token = header[len("Bearer "):].strip()
    if app.static_token and secrets.compare_digest(token, app.static_token):
        return {"ok": True, "owner": None, "plan": None, "scope": "full",
                "source": "static"}
    if token.startswith(entitlement.KEY_PREFIXES) and app.verify_key is not None:
        ent = app.verify_key(token)
        if ent:
            return {"ok": True, "owner": ent["owner"],
                    "plan": ent.get("plan"),
                    "scope": ent.get("scope") or "read",
                    "source": "apikey"}
        return miss
    if app.oauth is not None:
        rec = app.oauth.resolve(token)
        if rec:
            if not app._resource_ok(rec, presented_at):
                return miss
            owner = rec.get("owner")
            return {"ok": True, "owner": owner,
                    "plan": app._fresh_plan(owner, rec.get("plan")),
                    "scope": "full", "source": "oauth"}
    return miss


def _resolve_self_hosted(app, header: str, space: str | None, presented_at: str,
                         miss: dict) -> dict:
    """셀프호스트 허브: 들어오는 길(정적 토큰·OAuth)과 무관하게 소유자는 하나다.

    소유자를 None 으로 두면 도구가 호출자가 보낸 `owner` 를 그대로 쓴다. 여기서
    고정 소유자를 실어야 `forced_owner` 가 모든 경로에서 그 값을 덮는다. 스페이스를
    겨눈 호출은 인증 전에 거절한다(`reason` 이 사람이 읽을 답이다).
    """
    if space:
        return {**miss, "reason": selfhost.NO_SPACES}
    token = header[len("Bearer "):].strip()
    source = None
    if app.static_token and secrets.compare_digest(token, app.static_token):
        source = "static"
    elif app.oauth is not None:
        rec = app.oauth.resolve(token)
        if rec and app._resource_ok(rec, presented_at):
            source = "oauth"
    if source is None:
        return miss
    return {"ok": True, "owner": selfhost.owner(), "plan": None, "scope": "full",
            "source": source}


def _resource_ok(app, rec: dict, presented_at: str) -> bool:
    """RFC 8707 audience — 토큰은 **자기가 발급된 끝점에 제시**돼야 한다.

    ⚠ 여기서 보는 것은 요청이 **도착한 주소**지 `space` 인자가 아니다. 2026-09-22 에
    그 둘을 섞어서, 개인 주소(`/mcp`)로 붙은 커넥터가 `space` 인자로 팀을 고르면
    기대값이 `/t/<uuid>/mcp` 로 잡혀 **모든 팀 호출이 "no such space" 로 떨어졌다**.
    증상이 고약했던 것은 `vinc_whoami` 가 계정에 묻는 다른 경로라 팀 목록을 계속
    보여 줬다는 점이다 — 목록에는 있는데 접근만 안 되니, 스페이스가 사라진 것처럼
    읽힌다. 정적 키는 이 분기를 안 지나서 멀쩡했고, 그 대비가 원인을 갈랐다.

    스페이스는 주소가 아니라 **인자**다
    ([[decision:the-space-is-an-argument-and-the-default-is-personal]]). 그러니
    개인 주소로 발급된 토큰이 팀을 고르는 것은 정상이고, 그때 검사해야 하는 것은
    "이 토큰이 이 끝점에 와도 되는가" 하나뿐이다. 멤버십은 그 다음에 `verify_member`
    가 계정에 따로 묻는다 — 이 검사는 그 자리를 대신하지 않는다.

    빈 `resource` 는 통과한다. 구 커넥터는 이 파라미터를 안 보내고, 없는 것을
    불일치로 읽으면 이미 연결된 것들이 전부 끊긴다.
    """
    bound = (rec.get("resource") or "").strip()
    if not bound:
        return True
    want = app.oauth.issuer + presented_at
    # `?profile=` 을 붙인 주소로 발급된 토큰도 같은 끝점이다(프로파일은 목록의 크기지 권한이 아니다).
    return bound == want or bound in {f"{want}?profile={p}" for p in tools.TOOL_PROFILES}


def _fresh_plan(app, owner: str | None, frozen: str | None) -> str | None:
    """OAuth 토큰에 **동결된** 등급을 주기적으로 계정에 되묻는다.

    승인 시점의 등급이 인가 코드 → 액세스 토큰 → 리프레시 토큰으로 복사되므로,
    결제로 등급이 올라가도 이미 연결된 커넥터는 옛 값을 영원히 들고 있다
    (갱신조차 옛 값을 재동결한다). `owner_plan` 표를 캐시로 쓰고 TTL 이 지나면
    계정에 재조회한다.

    실패는 **마지막으로 알던 값으로 폴백**한다(fail-open to last-known):
    계정이 잠깐 흔들렸다고 유료 사용자를 무료로 떨어뜨리면 안 된다. 이건
    인증이 아니라 자격이라 위험 방향이 반대다.
    """
    if not owner or app.store is None or not owner.startswith("user:"):
        return frozen
    cached, age = app.store.get_owner_plan_age(owner)
    if cached is not None and age is not None and age < entitlement.plan_ttl():
        return cached
    fresh = entitlement.lookup_plan(owner[len("user:"):])
    if fresh is None:
        return cached if cached is not None else frozen
    app.store.set_owner_plan(owner, fresh)
    return fresh
