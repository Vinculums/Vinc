# -*- coding: utf-8 -*-
"""실행이 사람 앞에서 대기에 들어선 순간을 계정 백엔드에 알린다(웹 푸시).

역할 웹훅(`events.py`)은 구독이 역할마다 따로라서, 소유자의 모든 대기를 받으려면 역할마다
구독해야 한다. 계정은 역할을 모르고 알 필요도 없다. 그래서 실행 프로토콜이 대기를 **저장한
직후** 한 번, 이미 있는 허브→계정 서명 길(`entitlement.py` 와 같은 Ed25519 어서션, 같은
`VINC_ACCOUNT_INTERNAL_URL`)로 `POST /internal/mcp/run-waiting` 을 보낸다. 새 공개 주소는 없다.

싣는 것은 소유자(`user:` 또는 `team:`)·실행 id·질문 번호·제목·만료 다섯뿐이다. 행동 설명·질문·
작업·호스트는 **절대 싣지 않는다**(사람이 승인함을 열어야 보이는 것이고, 푸시는 남의 서버를
지난다). 어서션은 `typ: run-waiting` 과 본문 해시(`bh`)에 묶여서 다른 내부 호출의 어서션으로
이 알림을 지어낼 수 없다.

알림은 실행의 일부가 아니다: 별도 스레드에서 한 번 보내고, 실패는 범주만 로그에 남긴다.
재시도하지 않는다(놓친 알림은 승인함 목록이 메운다). 서명키(`VINC_MCP_ED25519_SEED`)가 없는
프로세스(데스크톱의 로컬 MCP)에서는 아무것도 하지 않는다.
계획: vinc.plan.agent-approvals-in-the-vinc-app-2026-11-07 §4. 이벤트 계약: [[vinc.plan.mcp-events-and-webhooks]]
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
import uuid

from store import run_state as _run_rule

from . import edverify, hubhttp

PATH = "/internal/mcp/run-waiting"
TYP = "run-waiting"
TITLE_MAX = 200
TIMEOUT_S = 5.0

_log = logging.getLogger("vinc.mcp.run_notify")


def payload(owner: str, eid: str, props: dict) -> dict | None:
    """알림 본문. 대기 중인 실행이 아니면 None. 여기 없는 키는 계정에 가지 않는다."""
    props = _run_rule.readable(props)
    pending = props.get("pending") if isinstance(props.get("pending"), dict) else {}
    if props.get("state") != "waiting" or not owner.startswith(("user:", "team:")):
        return None
    from .run_protocol import _title, expires_at
    return {"owner": owner, "run": eid, "ask": props["asks"],
            "title": _title(props)[:TITLE_MAX], "expires_at": expires_at(pending)}


def _assertion(seed: bytes, owner: str, raw: bytes) -> str:
    now = int(time.time())
    return edverify.sign_assertion(seed, {
        "iss": "mcp", "aud": "account", "typ": TYP, "sub": owner,
        "iat": now, "exp": now + 120, "jti": uuid.uuid4().hex,
        "bh": base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()})


def send(body: dict) -> str:
    """한 번 보낸다. 결과 범주(sent·disabled·http_<code>·unreachable)를 돌려주고 올리지 않는다."""
    from . import entitlement
    seed = entitlement._seed()
    if seed is None:
        return "disabled"
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    try:
        # 본문 해시가 서명에 묶여 있으므로 바이트를 다시 인코딩하지 않고 그대로 보낸다.
        hubhttp.request(entitlement._account_url() + PATH, data=raw, timeout=TIMEOUT_S,
                        headers={"X-Vinc-Assertion": _assertion(seed, body["owner"], raw)})
        return "sent"
    except hubhttp.HubHTTPError as exc:
        return f"http_{exc.status}" if exc.status is not None else "unreachable"
    except Exception:  # noqa: BLE001 — 알림 실패가 실행을 멈추면 안 된다
        return "unreachable"


def _deliver(body: dict) -> None:
    outcome = send(body)
    if outcome not in ("sent", "disabled"):
        # 소유자·실행 id·제목은 로그에 남기지 않는다. 범주만.
        _log.warning("run waiting notice not delivered: %s", outcome)


def waiting(owner: str, eid: str, props: dict, *, background: bool = True) -> bool:
    """대기에 들어선 실행을 알린다. 보낼 것이 있으면 True(보냈는지는 묻지 않는다)."""
    from . import entitlement
    body = payload(owner, eid, props)
    if body is None or not entitlement.enabled():
        return False
    if background:
        threading.Thread(target=_deliver, args=(body,), name="run-waiting-notice",
                         daemon=True).start()
    else:
        _deliver(body)
    return True
