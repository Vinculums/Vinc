"""커뮤니티 주소와 계정 수정 요청의 계약. 그래프 직접 쓰기와 다른 경로다."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid

from . import edverify, hubhttp

OWNER_RE = re.compile(
    r"community:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
WRITE_TOOL = "vinc_community_write"
READ_TOOL = "vinc_community_read"
READ_ACTIONS = ("agent_detail", "agent_export", "agent_take_preview", "agent_take_result")
ACTIONS = (
    "page_save", "page_revert", "talk_create", "talk_comment", "lounge_create",
    "lounge_comment", "react", "report", "moderate", "appoint_operator", "resolve_report",
    "social_edit", "community_join", "community_leave", "community_create",
    "share_create", "share_version", "share_withdraw", "share_moderate", "pack_share", "agent_take",
)


def is_space(value) -> bool:
    return isinstance(value, str) and bool(OWNER_RE.fullmatch(value))


def unavailable_write(args, bridge):
    raise PermissionError("Community writes require an authenticated account connector.")


def unavailable_read(args, bridge):
    raise PermissionError("Community share reads require an authenticated account connector.")


class WriteUnconfirmed(RuntimeError):
    def __init__(self):
        super().__init__("Community write outcome is not confirmed. Keep the mutationId and read history before retrying.")


def _request(path, body, purpose, *, write=False):
    from . import entitlement
    seed = entitlement._seed()
    if seed is None:
        raise entitlement.VerificationUnavailable()
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    now = int(time.time())
    assertion = edverify.sign_assertion(seed, {
        "iss": "mcp", "aud": "account", "typ": purpose, "sub": body["sub"],
        "iat": now, "exp": now + 120, "jti": uuid.uuid4().hex,
        "bh": base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode(),
    })
    try:
        # 본문 해시(bh)가 서명에 묶여 있으므로 바이트를 다시 인코딩하지 않고 그대로 보낸다.
        result = hubhttp.request(entitlement._account_url() + path, data=raw,
                                 headers={"X-Vinc-Assertion": assertion},
                                 timeout=30 if write else 4).json()
        if not isinstance(result, dict):
            raise ValueError("invalid account result")
        return result
    except hubhttp.HubHTTPError as exc:
        if not write and purpose == "community-read" and path.endswith("community-resolve") and exc.status == 404:
            return None
        if exc.status in (400, 401, 403, 404, 409, 429):
            # 명시적 거절만 확정 실패다. 5xx/타임아웃은 커밋 여부를 단정하지 않는다.
            try:
                result = json.loads(exc.body)
            except (ValueError, UnicodeDecodeError):
                result = {}
            return {"executed": False, "isError": True, "status": exc.status,
                    "error": result.get("error", "community_request_refused") if isinstance(result, dict) else "community_request_refused"}
        if write:
            raise WriteUnconfirmed() from None
        raise entitlement.VerificationUnavailable() from None
    except Exception:
        if write:
            raise WriteUnconfirmed() from None
        raise entitlement.VerificationUnavailable() from None


def resolve(sub: str, space: str):
    from . import entitlement
    if not is_space(space) or not sub:
        return None
    result = _request("/internal/mcp/community-resolve", {"sub": sub, "community": space[10:].lower()}, "community-read")
    if result is None:
        return None
    owner = result.get("graph_owner")
    if (not isinstance(owner, str) or owner.lower() != space.lower()
            or result.get("visibility") != "public" or result.get("role") not in ("reader", "operator")):
        raise entitlement.VerificationUnavailable()
    return {"owner": owner.lower(), "role": result["role"], "member": "user:" + sub,
            "visibility": "public"}


def write(auth: dict, args: dict):
    owner, member = auth.get("owner"), auth.get("member")
    creating = args.get("action") == "community_create"
    if creating and isinstance(owner, str) and owner.startswith("user:"):
        member, target = owner, None
    elif not creating and is_space(owner):
        target = owner[10:]
    else:
        raise PermissionError("Community creation requires personal identity; other writes require a community space.")
    if (not isinstance(member, str) or not member.startswith("user:") or auth.get("scope") != "full"):
        raise PermissionError("A full-access authenticated community request is required.")
    return _request("/internal/mcp/community", {
        "sub": member[5:], "community": target, "action": args["action"],
        "payload": args["payload"], "client_name": args["client_name"].strip(),
        "channel": "mcp", "scope": "full",
    }, "community-write", write=True)


def read(auth: dict, args: dict):
    owner, member = auth.get("owner"), auth.get("member")
    if (not is_space(owner) or not isinstance(member, str) or not member.startswith("user:")
            or not member[5:] or args.get("action") not in READ_ACTIONS):
        raise PermissionError("Community share reads require an authenticated explicit community space.")
    return _request("/internal/mcp/community-read", {"sub": member[5:], "community": owner[10:],
        "action": args["action"], "payload": args["payload"]}, "community-read")
