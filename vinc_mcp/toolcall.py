"""MCP와 REST가 공유하는 도구 호출의 인증 후 게이트와 실행 순서."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import tools, viewurl


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
            if not valid_space(want):
                return Outcome("space_not_found", message=(
                    "no such space. Call vinc_whoami to see the spaces you can reach."))
            auth = authenticate(want)
            if not auth["ok"]:
                return Outcome("space_not_found", message=(
                    "no such space. Call vinc_whoami to see the spaces you can reach."))
    if str(auth.get("owner") or "").startswith("team:"):
        role = auth.get("role") or ""
        if not tools.role_allows(role, name):
            return Outcome("role_denied", auth=auth,
                           message=f"role {role!r} may not call {name}")
    if auth.get("scope") != "full" and tools.tool_tier(name) != "viewer":
        return Outcome("read_only", auth=auth,
                       message=(f"this key is read-only: {name} writes to the graph. "
                                "Mint a full-access key at https://vincs.io/account"))
    return Outcome("ok", auth=auth)


def execute(name: str, args: dict | None, bridge: Any, *,
            forced_owner: str | None = None, plan: str | None = None,
            quota: Callable[[str, str | None], dict | None] | None = None,
            entitlements: dict | None = None, allow_target: bool = True) -> Outcome:
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
                            entitlements=entitlements, allow_target=allow_target)
    except tools.InvalidArgument as exc:
        return Outcome("invalid_argument", result=exc.result(), error=exc)
    except (KeyError, ValueError) as exc:
        return Outcome("protocol_error", error=exc)
    except Exception as exc:  # noqa: BLE001 — MCP의 도구 오류 봉투로 내린다.
        return Outcome("error", result={"content": [{"type": "text",
                "text": f"{type(exc).__name__}: {exc}"}], "isError": True}, error=exc)
    return Outcome("ok", result=result)
