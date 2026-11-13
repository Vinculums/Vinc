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
# 새 이름은 끝에 붙인다. 앞의 순서는 도구 스키마의 enum 과 거절 문장에 그대로 실린다.
READ_ACTIONS = ("agent_detail", "agent_export", "agent_take_preview", "agent_take_result",
                "feed", "post", "topics")
ACTIONS = (
    "page_save", "page_revert", "react", "report", "moderate", "appoint_operator", "resolve_report",
    "social_edit", "community_edit",
    "share_create", "share_version", "share_withdraw", "share_moderate", "pack_share", "agent_take",
    "post_create", "post_reply", "topic_save",
)

# ── 합친 공간 동작의 payload 형식 검사 ────────────────────────────────────────
# 계정(internal MCP 라우트)이 형식만 보고 거절할 값을 허브가 먼저 거절해 요청을 내보내지 않는다.
# 권한(운영자인가)과 존재(글·주제·페이지가 있는가, 합친 공간인가)는 계정만 본다.
# ⚠ 계정이 받는 값을 여기서 거절하면 안 된다: 길이는 JS 문자열 길이(UTF-16 단위), 공백 걷기는 JS trim 으로 센다.
_JS_SPACE = ("\t\n\v\f\r            "
             "      　﻿")
_TOPIC_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*){0,3}")
_PAGE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")
_MUTATION_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_CURSOR_RE = re.compile(r"[A-Za-z0-9_-]{1,400}")
_TOPIC_KEYS = ("path", "nameEn", "nameKo", "order", "featured", "status")
UNIFIED_WRITES = ("post_create", "post_reply", "topic_save")
UNIFIED_READS = ("feed", "post", "topics")


def _trim(v: str) -> str:
    return v.strip(_JS_SPACE)


def _units(v: str) -> int:
    return len(v.encode("utf-16-le", "surrogatepass")) // 2


def _topic(v, *, empty_ok: bool) -> bool:
    """계정 metadataTopic 과 같은 판정. 앞뒤 공백·빗금을 걷고 소문자로 읽는다."""
    if not isinstance(v, str):
        return False
    t = re.sub(r"^/+|/+$", "", _trim(v).lower())
    return (empty_ok and not t) or (len(t) <= 200 and bool(_TOPIC_RE.fullmatch(t)))


def _page(v: str) -> bool:
    return bool(_PAGE_RE.fullmatch(v)) and not v.endswith("-") and "--" not in v


def _uuid(v) -> bool:
    """계정 uuidToBytes 와 같다: 붙임표를 모두 걷고 16진 32자."""
    return isinstance(v, str) and bool(re.fullmatch(r"[0-9a-fA-F]{32}", v.replace("-", "")))


def _content(p: dict) -> str | None:
    """계정 socialContent: 본문 1..16000자(60000 UTF-8 바이트 안), 제목 200자. 문자열이 아닌 제목은 계정이 버린다."""
    if "parentComment" in p:
        return "payload.parentComment is not accepted: replies are flat"
    body = p["body"] if isinstance(p.get("body"), str) else ""
    if not _trim(body) or _units(body) > 16000 or len(body.encode("utf-8", "surrogatepass")) > 60000:
        return "payload.body must be text of 1..16000 characters (at most 60000 UTF-8 bytes)"
    if isinstance(p.get("title"), str) and _units(_trim(p["title"])) > 200:
        return "payload.title must be at most 200 characters"
    return None


def _post_fields(p: dict) -> str | None:
    """계정 postFields: kind·topic·about 의 형식. null 은 빠진 것과 같다."""
    if p.get("kind") is not None and p["kind"] not in ("note", "question"):
        return "payload.kind must be note or question"
    if p.get("topic") is not None and not _topic(p["topic"], empty_ok=True):
        return "payload.topic must be empty or a topic path such as physics/energy"
    about = p.get("about")
    if about is not None and about != "" and not (isinstance(about, str) and _page(_trim(about))):
        return "payload.about must be a page address such as quantum-entanglement"
    return None


def _target(p: dict) -> str | None:
    target = _trim(p["targetId"]) if isinstance(p.get("targetId"), str) else ""
    return None if _uuid(target) else "payload.targetId must be a post id (a UUID)"


def _topic_entry(p: dict) -> str | None:
    """계정 topicEntry(mutationId 를 뺀 나머지). 모르는 키는 경로 바꾸기로 읽힐 수 있어 계정도 거절한다."""
    if any(k not in _TOPIC_KEYS for k in p if k != "mutationId"):
        return "payload takes only path, nameEn, nameKo, order, featured and status; a path cannot be renamed"
    if not _topic(p.get("path"), empty_ok=False):
        return "payload.path must be a topic path such as physics/energy"
    name = p.get("nameEn")
    if not isinstance(name, str) or not 1 <= _units(_trim(name)) <= 120:
        return "payload.nameEn must be text of 1..120 characters"
    if "nameKo" in p and not (isinstance(p["nameKo"], str) and _units(_trim(p["nameKo"])) <= 120):
        return "payload.nameKo must be text of at most 120 characters"
    order = p.get("order", 0)
    if (isinstance(order, bool) or not isinstance(order, (int, float))
            or (isinstance(order, float) and not order.is_integer()) or abs(order) > 1_000_000):
        return "payload.order must be an integer from -1000000 to 1000000"
    if "featured" in p and not isinstance(p["featured"], bool):
        return "payload.featured must be true or false"
    if p.get("status", "active") not in ("active", "hidden"):
        return "payload.status must be active or hidden"
    return None


def _feed_query(p: dict) -> str | None:
    """계정 feedRequest·payloadField. 문자열이 아닌 값(정수 limit 은 예외)은 계정에서 틀린 값이 된다."""
    def text(k):
        v = p.get(k)
        return None if v is None else _trim(v) if isinstance(v, str) else "\0"
    kind, about, sort = text("kind"), text("about"), text("sort")
    if kind and kind not in ("note", "question", "knowledge"):
        return "payload.kind must be note, question or knowledge"
    if p.get("topic") is not None and not _topic(p["topic"], empty_ok=True):
        return "payload.topic must be empty or a topic path such as physics/energy"
    if about and not _page(about):
        return "payload.about must be a page address such as quantum-entanglement"
    if sort and sort != "latest":
        return "payload.sort must be latest"
    limit = p.get("limit")
    if isinstance(limit, float) and limit.is_integer():
        limit = int(limit)
    raw = (None if limit is None else _trim(limit) if isinstance(limit, str)
           else str(limit) if isinstance(limit, int) and not isinstance(limit, bool) else "\0")
    if raw and not (re.fullmatch(r"[0-9]{1,3}", raw) and 1 <= int(raw) <= 50):
        return "payload.limit must be an integer from 1 to 50"
    cursor = p.get("cursor")
    if cursor is not None and not (isinstance(cursor, str) and (cursor == "" or _CURSOR_RE.fullmatch(cursor))):
        return "payload.cursor must be the nextCursor of the previous feed page"
    return None


def payload_problem(action, payload: dict) -> str | None:
    """합친 공간 동작의 payload 가 계정에서 형식으로 거절될 자리를 한 문장으로. 그 밖의 동작은 보지 않는다(None)."""
    if action in UNIFIED_WRITES and "mutationId" in payload and not (
            isinstance(payload["mutationId"], str) and _MUTATION_RE.fullmatch(payload["mutationId"])):
        return "payload.mutationId must be a UUID"
    if action == "post_create":
        return _post_fields(payload) or _content(payload)
    if action == "post_reply":
        return _content(payload) or _target(payload)
    if action == "topic_save":
        return _topic_entry(payload)
    if action == "feed":
        return _feed_query(payload)
    if action == "post":
        if not _uuid(payload.get("postId")):
            return "payload.postId must be a post id (a UUID)"
        after = payload.get("afterComment")
        if after is not None and after != "" and not _uuid(after):
            return "payload.afterComment must be the nextCommentCursor of the previous page"
    return None


def is_space(value) -> bool:
    return isinstance(value, str) and bool(OWNER_RE.fullmatch(value))


def unavailable_write(args, bridge):
    raise PermissionError("Community writes require an authenticated account connector.")


def unavailable_read(args, bridge):
    raise PermissionError("Community reads require an authenticated account connector.")


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
    # 모든 쓰기는 명시한 커뮤니티 주소 하나로 간다. 목록에 없는 동작은 도구 스키마를 지나지 않은 호출이라도 계정으로 보내지 않는다.
    if not is_space(owner) or args.get("action") not in ACTIONS:
        raise PermissionError("Community writes require an explicit community space and an action of vinc_community_write.")
    if (not isinstance(member, str) or not member.startswith("user:") or auth.get("scope") != "full"):
        raise PermissionError("A full-access authenticated community request is required.")
    return _request("/internal/mcp/community", {
        "sub": member[5:], "community": owner[10:], "action": args["action"],
        "payload": args["payload"], "client_name": args["client_name"].strip(),
        "channel": "mcp", "scope": "full",
    }, "community-write", write=True)


def read(auth: dict, args: dict):
    owner, member = auth.get("owner"), auth.get("member")
    if (not is_space(owner) or not isinstance(member, str) or not member.startswith("user:")
            or not member[5:] or args.get("action") not in READ_ACTIONS):
        raise PermissionError("Community reads require an authenticated explicit community space.")
    return _request("/internal/mcp/community-read", {"sub": member[5:], "community": owner[10:],
        "action": args["action"], "payload": args["payload"]}, "community-read")
