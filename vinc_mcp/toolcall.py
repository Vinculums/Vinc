"""MCP와 REST가 공유하는 도구 호출의 인증 후 게이트와 실행 순서."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import community, selfhost, tools, viewurl


@dataclass(frozen=True)
class Outcome:
    kind: str
    result: dict | None = None
    auth: dict | None = None
    message: str = ""
    error: Exception | None = None


def preflight(name: str, args: dict, *, base_auth: dict,
              address_space: str | None, authenticate: Callable[[str], dict],
              valid_space: Callable[[str], bool]) -> Outcome:
    """한 도구 메시지의 스페이스·역할·키 범위를 차례로 판정한다."""
    auth = base_auth
    if not address_space:
        want = str(args.get("space") or "").strip().lower()
        if not want and args.get("url"):
            want = viewurl.space_of(str(args["url"]))
            if want == viewurl.LOCAL:
                return Outcome("on_device", message=(
                    "that address points at a graph kept on one device (the desktop app "
                    "in local mode), which this hosted connector cannot read. Use the "
                    "desktop app's own connector on that device."))
        if want and want != viewurl.PERSONAL:
            if selfhost.enabled():
                return Outcome("space_not_found", message=selfhost.NO_SPACES)
            if not valid_space(want):
                return Outcome("space_not_found", message=(
                    "no such space. Call vinc_whoami to see the spaces you can reach."))
            auth = authenticate(want)
            if not auth["ok"]:
                return Outcome("space_not_found", message=(
                    "no such space. Call vinc_whoami to see the spaces you can reach."))
    is_community = community.is_space(auth.get("owner"))
    if is_community and tools.tool_tier(name) != "viewer" and name != community.WRITE_TOOL:
        return Outcome("role_denied", auth=auth,
                       message="Community mutations require vinc_community_write and an explicit community space.")
    if str(auth.get("owner") or "").startswith("team:"):
        role = auth.get("role") or ""
        if not tools.role_allows(role, name):
            return Outcome("role_denied", auth=auth,
                           message=f"role {role!r} may not call {name}")
    if auth.get("scope") != "full" and tools.tool_tier(name) != "viewer":
        return Outcome("read_only", auth=auth,
                       message=(f"this key is read-only: {name} writes to the graph. "
                                "Mint a full-access key at https://vincs.io/account"))
    if name == community.WRITE_TOOL:
        creating = args.get("action") == "community_create"
        if ((creating and not str(auth.get("owner") or "").startswith("user:"))
                or (not creating and not is_community)):
            return Outcome("role_denied", auth=auth,
                           message="Community creation uses personal identity; all other actions require community:<uuid> space.")
    if name == community.READ_TOOL and not is_community:
        return Outcome("role_denied", auth=auth,
                       message="Community Agent reads require an explicit community:<uuid> space.")
    return Outcome("ok", auth=auth)


def _whoami_quota(result: dict, owner: str | None, plan: str | None,
                  view: Callable[[str, str | None], dict | None]) -> dict:
    """`vinc_whoami`(`/v1/me`)에 스페이스마다 남은 호출 수를 싣는다. 세는 단위는 그래프
    owner 다: 개인은 이 연결의 등급, 팀은 계정이 실어 준 그 팀의 등급(없으면 허브가 정한
    기본). 커뮤니티 주소는 멤버 개인 한도를 쓰므로 그 owner 로는 싣지 않는다.
    ⚠ owner 는 인증이 정한 값(`forced_owner`)만 쓰고 인자의 `owner` 는 보지 않는다. 팀 줄은
    계정이 이 사람의 활성 멤버십으로 돌려준 목록뿐이라, 다른 계정의 숫자는 나갈 길이 없다."""
    if not owner or community.is_space(owner):
        return result
    out = dict(result)
    here = view(owner, plan)
    if here is not None:
        out["quota"] = here
    if isinstance(out.get("spaces"), list):
        rows = []
        for row in out["spaces"]:
            q = (here if row.get("id") == "personal"
                 else view("team:" + str(row.get("id")), row.get("plan")))
            rows.append({**row, "quota": q} if q is not None else row)
        out["spaces"] = rows
    return out


def execute(name: str, args: dict | None, bridge: Any, *,
            forced_owner: str | None = None, plan: str | None = None,
            quota: Callable[[str, str | None], dict | None] | None = None,
            quota_view: Callable[[str, str | None], dict | None] | None = None,
            entitlements: dict | None = None, hub: bool = False,
            community_write=None, community_read=None) -> Outcome:
    """인자 검증은 한도 소비보다 앞선다. 실행 결과 봉투는 호출 표면이 만든다."""
    args = args or {}
    try:
        tools.validate_args(name, args)
    except tools.InvalidArgument as exc:
        return Outcome("invalid_argument", result=exc.result(), error=exc)
    if quota is not None and forced_owner:
        denial = quota(forced_owner, plan)
        if denial is not None:
            return Outcome("quota", result=denial)
    try:
        result = tools.call(name, args, bridge, forced_owner=forced_owner,
                            entitlements=entitlements, hub=hub,
                            community_write=community_write, community_read=community_read)
    except tools.InvalidArgument as exc:
        return Outcome("invalid_argument", result=exc.result(), error=exc)
    except (KeyError, ValueError) as exc:
        return Outcome("protocol_error", error=exc)
    except Exception as exc:  # noqa: BLE001 — MCP의 도구 오류 봉투로 내린다.
        return Outcome("error", result={"content": [{"type": "text",
                "text": f"{type(exc).__name__}: {exc}"}], "isError": True}, error=exc)
    if name == "vinc_whoami" and quota_view is not None and isinstance(result, dict):
        result = _whoami_quota(result, forced_owner, plan, quota_view)
    return Outcome("ok", result=result)
