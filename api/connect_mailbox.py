# -*- coding: utf-8 -*-
"""계정 연결 메일박스(디바이스 연결 플로우 — 회원 키 복붙 종식): `POST /connect/start`·
`POST /connect/callback`·`GET /connect/status` 의 본문.

사이드카 앱(`api.server.VincApp`)이 :class:`ConnectMailbox` 하나를 들고, 라우트 메서드 셋
(`write_connect_start`·`connect_callback`·`read_connect_status`)이 :meth:`~ConnectMailbox.start`·
:meth:`~ConnectMailbox.callback`·:meth:`~ConnectMailbox.status` 로 그대로 넘긴다.

칸은 state 논스 → {expires, [key, email, username, mcp_key]} 이고 메모리에만 있다. 재기동하면 사라지고,
연결 중이었으면 다시 시작한다. 시계(`time.monotonic`)와 논스(`secrets.token_urlsafe`)는 부를 때마다
모듈에서 찾는다.
"""
from __future__ import annotations

import secrets
import threading
import time


class ConnectMailbox:
    # RFC 8252(네이티브 앱 OAuth) 변형: 앱이 state 논스를 발급받아(start, 인증) 시스템
    # 브라우저로 vincs.app/connect-app/#port&state 를 연다. 로그인·승인·키 발급은 전부
    # 브라우저(계정 오리진)에서 일어나고, 페이지가 발급된 vinc_sk_* 를 루프백 콜백
    # (무인증 — state 단일사용 게이트)으로 전달하면 앱이 status(인증)로 1회 수령한다.
    # B1 보존: 앱은 계정 API 를 호출하지 않고 세션도 만들지 않는다 — 키만 받는다.
    # 위협 모델: state 는 256bit 난수·TTL 10분·단일사용이라 드라이브바이 로컬 POST 로
    # 위조 불가. 로컬 악성 프로세스가 브라우저 argv 에서 state 를 훔치는 경로는 존재
    # 하나, 그 수준의 로컬 침해는 localStorage 의 키 자체를 읽을 수 있어 등가 위협.
    TTL = 600.0

    def __init__(self, *, origins):
        """``origins`` 는 콜백을 보낼 수 있는 웹 오리진 목록을 돌려주는 함수다(부를 때마다 읽는다)."""
        self._entries: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._origins = origins

    def start(self):
        state = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            # 만료·사용済 청소 — 진행 중 요청은 최신 start 가 대체한다(동시 1개면 충분).
            self._entries = {s: v for s, v in self._entries.items()
                             if v["expires"] > now and "key" not in v}
            self._entries[state] = {"expires": now + self.TTL}
        return 200, {"state": state, "expires_in": int(self.TTL)}

    def callback(self, body, origin):
        """무인증(브라우저 호출) — 인증은 state 논스가 대신한다. Origin 은 심층방어
        (브라우저 교차오리진 POST 는 항상 Origin 을 실음; curl 등 무 Origin 은 state
        게이트만으로 충분)."""
        if origin and origin not in self._origins():
            return 403, {"error": "origin not allowed"}
        state = (body.get("state") or "").strip()
        key = (body.get("key") or "").strip()
        email = (body.get("email") or "").strip()
        # username(핸들)은 표시 전용(009) — 없으면 빈 문자열. 길이만 방어적으로 클램프,
        # 형식 검증은 계정 백엔드가 이미 함(여긴 그대로 실어 나르기만).
        username = (body.get("username") or "").strip()
        # 기기별 MCP 키. 선택이다: 연결 페이지가 키 한도에 걸려 못 만들었으면
        # 없이 온다. 모양이 틀리면 앱 연결은 살리고 그 키만 버린다.
        mcp_key = (body.get("mcp_key") or "").strip()
        if not (mcp_key.startswith(("vinc_sk_", "vinc_ro_")) and len(mcp_key) <= 200):
            mcp_key = ""
        if not state or not key.startswith("vinc_sk_") or len(key) > 200:
            return 400, {"error": "invalid payload"}
        now = time.monotonic()
        with self._lock:
            ent = self._entries.get(state)
            if not ent or ent["expires"] <= now or "key" in ent:
                return 400, {"error": "unknown, used or expired state"}
            ent["key"] = key
            ent["email"] = email[:200]
            ent["username"] = username[:20]
            ent["mcp_key"] = mcp_key
        return 200, {"status": "ok"}

    def status(self, state):
        now = time.monotonic()
        with self._lock:
            ent = self._entries.get(state)
            if not ent or ent["expires"] <= now:
                self._entries.pop(state, None)
                return 200, {"status": "expired"}
            if "key" not in ent:
                return 200, {"status": "pending"}
            self._entries.pop(state, None)   # 1회 전달 후 즉시 소거(메일박스)
            return 200, {"status": "delivered", "key": ent["key"],
                         "email": ent.get("email", ""), "username": ent.get("username", ""),
                         "mcp_key": ent.get("mcp_key", "")}
