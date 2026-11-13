"""MCP와 REST가 공유하는 도구 호출의 인증 후 게이트와 실행 순서."""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Callable

from . import community, selfhost, tools, viewurl


@dataclass(frozen=True)
class Outcome:
    kind: str
    result: dict | None = None
    auth: dict | None = None
    message: str = ""
    error: Exception | None = None


@dataclass(frozen=True)
class CallContext:
    """인증을 마친 호출 하나가 `Dispatcher.handle` → `_dispatch` → `execute` → `tools.call` 로 들고
    가는 값. 허브는 `MCPHTTPApp.call_context(auth, spent)` 가 `/mcp` 메시지마다, REST v1 요청마다
    한 번 만든다. 로컬(stdio)은 빈 값(`CallContext()`)이다.

    - forced_owner: 인증이 정한 그래프 owner. 있으면 도구 인자의 owner 를 덮는다.
    - plan: 그 owner 의 등급. quota(owner, plan): 한도를 쓰고 거절 결과나 None 을 낸다.
    - quota_view(owner, plan): vinc_whoami 에 싣는 남은 호출 수.
    - entitlements: 등급에서 푼 숫자(주제 상한). 사이드카로 그대로 실린다.
    - community_write / community_read(args): 인증에 묶인 계정 커뮤니티 호출.
    - events_auth: Events · Tasks 의 주체와 쓰기 가능 여부. profile: 연결의 도구 프로파일.
      이 둘은 MCP 연결의 값이고 `execute` 는 읽지 않는다."""
    forced_owner: str | None = None
    plan: str | None = None
    quota: Callable[[str, str | None], dict | None] | None = None
    quota_view: Callable[[str, str | None], dict | None] | None = None
    entitlements: dict | None = None
    community_write: Callable[[dict], Any] | None = None
    community_read: Callable[[dict], Any] | None = None
    events_auth: dict | None = None
    profile: str | None = None


def context_of(ctx: CallContext | None, **keywords) -> CallContext:
    """`ctx` 를 그대로, 없으면 따로 받은 키워드로 만든 CallContext 를 낸다. 키워드는 CallContext 의
    필드 이름이고 None 은 주지 않은 것이다. 둘을 함께 주면 어느 쪽이 이기는지 숨기지 않고 TypeError 다."""
    unknown = set(keywords) - {f.name for f in fields(CallContext)}
    if unknown:
        raise TypeError("unknown call context field: " + ", ".join(sorted(unknown)))
    given = {key: value for key, value in keywords.items() if value is not None}
    if ctx is None:
        return CallContext(**given)
    if given:
        raise TypeError("pass ctx or the separate keywords, not both: " + ", ".join(sorted(given)))
    return ctx


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
    if name == community.WRITE_TOOL and not is_community:
        return Outcome("role_denied", auth=auth,
                       message="Community writes require an explicit community:<uuid> space.")
    if name == community.READ_TOOL and not is_community:
        return Outcome("role_denied", auth=auth,
                       message="Community reads require an explicit community:<uuid> space.")
    return Outcome("ok", auth=auth)


def partial_search(result) -> dict | None:
    """B9 — 실패한 vinc_search 결과에서 노드 팔의 후보를 살린 답. 살리지 않으면 None 이다.

    청크 팔만 실패하고(`error` 가 있거나 `executed` 가 false) 노드 팔이 `node_hits` 를 냈으면
    사유를 `search_error` 로 옮기고(사유가 비었으면 "chunk search did not run") `executed` 를 노드 팔
    기준인 True 로 둔 사본을 돌려준다. vinc_brief 가 같은 실패를 그렇게 싣는다.
    살리지 않는 것: 실패가 아닌 결과, 노드 후보가 없는 결과, 인자 오류(invalid_*·stale_cursor),
    유지보수 거절(store_busy). store_busy 는 스토어 전체가 읽기를 거절한 것이지 청크 팔만의 실패가
    아니다. 호출자가 다시 물을 신호(REST 는 재시도 가능한 503)를 지켜야 한다.
    REST /v1/search(`rest_api._partial_search`)와 호스트 search 도구(`tools._t_host_search`)가 이
    규칙 하나를 쓴다. 살리지 않은 실패를 어떤 봉투로 내는가는 각 표면의 일이다."""
    if not isinstance(result, dict) or not result.get("node_hits"):
        return None
    error = str(result.get("error") or "")
    if not error and result.get("executed") is not False:
        return None
    if error == "store_busy" or error.startswith(("invalid_", "stale_cursor")):
        return None
    out = {key: value for key, value in result.items() if key != "error"}
    out["search_error"] = error or "chunk search did not run"
    out["executed"] = True
    return out


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


def execute(name: str, args: dict | None, bridge: Any, ctx: CallContext | None = None, *,
            hub: bool = False, forced_owner: str | None = None, plan: str | None = None,
            quota: Callable[[str, str | None], dict | None] | None = None,
            quota_view: Callable[[str, str | None], dict | None] | None = None,
            entitlements: dict | None = None,
            community_write=None, community_read=None) -> Outcome:
    """인자 검증은 한도 소비보다 앞선다. 실행 결과 봉투는 호출 표면이 만든다.

    호출의 값은 `ctx` 하나로 받는다. 따로 받는 키워드(forced_owner … community_read)는 그것을
    아직 쓰는 호출자의 길이고, `ctx` 와 함께 주면 TypeError 다(`context_of`)."""
    ctx = context_of(ctx, forced_owner=forced_owner, plan=plan, quota=quota, quota_view=quota_view,
                     entitlements=entitlements, community_write=community_write,
                     community_read=community_read)
    args = args or {}
    try:
        tools.validate_args(name, args)
    except tools.InvalidArgument as exc:
        return Outcome("invalid_argument", result=exc.result(), error=exc)
    if ctx.quota is not None and ctx.forced_owner:
        denial = ctx.quota(ctx.forced_owner, ctx.plan)
        if denial is not None:
            return Outcome("quota", result=denial)
    try:
        result = tools.call(name, args, bridge, hub=hub, ctx=ctx)
    except tools.InvalidArgument as exc:
        return Outcome("invalid_argument", result=exc.result(), error=exc)
    except (KeyError, ValueError) as exc:
        return Outcome("protocol_error", error=exc)
    except Exception as exc:  # noqa: BLE001 — MCP의 도구 오류 봉투로 내린다.
        return Outcome("error", result={"content": [{"type": "text",
                "text": f"{type(exc).__name__}: {exc}"}], "isError": True}, error=exc)
    if name == "vinc_whoami" and ctx.quota_view is not None and isinstance(result, dict):
        result = _whoami_quota(result, ctx.forced_owner, ctx.plan, ctx.quota_view)
    return Outcome("ok", result=result)
