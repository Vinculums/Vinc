# -*- coding: utf-8 -*-
"""OAuth·쿼터 영속 스토어 — SQLite(stdlib), 재기동에도 커넥터 유지.

왜(B-A): OAuthGate 의 인메모리 4-dict 는 서비스 재시작마다 전 커넥터를 재인가
시킨다(발주자 "제일 힘든 것"의 한 갈래). 상태를 `$VINC_MCP_STATE_DIR/oauth.db`
(WAL)로 옮겨 **재시작에도 인가·토큰·쿼터가 유지**되게 한다. stdlib sqlite3 만
쓰므로 MCP 의 무외부의존(D36)을 지킨다.

프로덕션 경로: systemd `StateDirectory=vinc-mcp` → `/var/lib/vinc-mcp`
(vinc-mcp.service). ProtectHome=read-only 라 홈에는 못 쓴다 — 그래서 env 로
쓰기 가능한 상태 경로를 명시 주입한다. 개발 기본은 `~/.vinc/mcp`.

동시성: ThreadingHTTPServer 라 여러 스레드가 접근한다 → 단일 커넥션
(check_same_thread=False) + 쓰기 Lock + WAL. 인증 빈도(커넥션당 + 60s 캐시)
에서 경합은 무시할 수준이다.
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
  client_id     TEXT PRIMARY KEY,
  redirect_uris TEXT NOT NULL,          -- JSON array
  name          TEXT NOT NULL DEFAULT '',
  created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS codes (
  code         TEXT PRIMARY KEY,
  client_id    TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  challenge    TEXT NOT NULL,
  owner        TEXT,
  plan         TEXT,
  exp          REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
  token     TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  owner     TEXT,
  plan      TEXT,
  exp       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS refresh (
  token     TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  owner     TEXT,
  plan      TEXT
);
CREATE TABLE IF NOT EXISTS connect_requests (
  req_id       TEXT PRIMARY KEY,
  client_id    TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  challenge    TEXT NOT NULL,
  state        TEXT,
  exp          REAL NOT NULL,
  consumed     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage (
  owner        TEXT PRIMARY KEY,
  count        INTEGER NOT NULL DEFAULT 0,
  window_start REAL NOT NULL DEFAULT 0,
  locked_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS owner_plan (
  owner      TEXT PRIMARY KEY,
  plan       TEXT NOT NULL,
  updated_at REAL NOT NULL
);
"""


def resolve_state_dir(create: bool = True) -> str:
    """MCP 상태 경로: env VINC_MCP_STATE_DIR > ~/.vinc/mcp(개발 기본)."""
    explicit = os.environ.get("VINC_MCP_STATE_DIR")
    root = pathlib.Path(explicit) if explicit else (pathlib.Path.home() / ".vinc" / "mcp")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return str(root)


class Store:
    def __init__(self, path: str | None = None):
        if path is None:
            path = os.path.join(resolve_state_dir(), "oauth.db")
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=3000")
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ── clients ─────────────────────────────────────────────────────────────
    def add_client(self, client_id: str, redirect_uris: list[str], name: str,
                   now: float | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO clients(client_id,redirect_uris,name,created_at) "
                "VALUES(?,?,?,?)",
                (client_id, json.dumps(redirect_uris), name or "",
                 time.time() if now is None else now))
            self._db.commit()

    def get_client(self, client_id: str) -> dict | None:
        row = self._db.execute(
            "SELECT redirect_uris,name FROM clients WHERE client_id=?", (client_id,)).fetchone()
        if row is None:
            return None
        return {"redirect_uris": json.loads(row["redirect_uris"]), "name": row["name"]}

    # ── codes(단일 소비) ──────────────────────────────────────────────────────
    def add_code(self, code: str, client_id: str, redirect_uri: str, challenge: str,
                 owner: str | None, plan: str | None, exp: float) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO codes(code,client_id,redirect_uri,challenge,owner,plan,exp) "
                "VALUES(?,?,?,?,?,?,?)",
                (code, client_id, redirect_uri, challenge, owner, plan, exp))
            self._db.commit()

    def pop_code(self, code: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT client_id,redirect_uri,challenge,owner,plan,exp FROM codes WHERE code=?",
                (code,)).fetchone()
            if row is None:
                return None
            self._db.execute("DELETE FROM codes WHERE code=?", (code,))
            self._db.commit()
        return dict(row)

    # ── tokens ────────────────────────────────────────────────────────────────
    def add_token(self, token: str, client_id: str, owner: str | None,
                  plan: str | None, exp: float) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO tokens(token,client_id,owner,plan,exp) VALUES(?,?,?,?,?)",
                (token, client_id, owner, plan, exp))
            self._db.commit()

    def get_token(self, token: str, now: float | None = None) -> dict | None:
        """유효 토큰이면 {client_id,owner,plan,exp}, 만료면 삭제 후 None."""
        t = time.time() if now is None else now
        row = self._db.execute(
            "SELECT client_id,owner,plan,exp FROM tokens WHERE token=?", (token,)).fetchone()
        if row is None:
            return None
        if row["exp"] < t:
            with self._lock:
                self._db.execute("DELETE FROM tokens WHERE token=?", (token,))
                self._db.commit()
            return None
        return dict(row)

    # ── refresh(단일 소비) ────────────────────────────────────────────────────
    def add_refresh(self, token: str, client_id: str, owner: str | None,
                    plan: str | None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO refresh(token,client_id,owner,plan) VALUES(?,?,?,?)",
                (token, client_id, owner, plan))
            self._db.commit()

    def pop_refresh(self, token: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT client_id,owner,plan FROM refresh WHERE token=?", (token,)).fetchone()
            if row is None:
                return None
            self._db.execute("DELETE FROM refresh WHERE token=?", (token,))
            self._db.commit()
        return dict(row)

    # ── connect_requests(회원 승인 흐름, 단일 소비) ────────────────────────────
    def add_connect_request(self, req_id: str, client_id: str, redirect_uri: str,
                            challenge: str, state: str | None, exp: float) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO connect_requests(req_id,client_id,redirect_uri,challenge,state,exp) "
                "VALUES(?,?,?,?,?,?)",
                (req_id, client_id, redirect_uri, challenge, state, exp))
            self._db.commit()

    def get_connect_request(self, req_id: str, now: float | None = None) -> dict | None:
        t = time.time() if now is None else now
        row = self._db.execute(
            "SELECT client_id,redirect_uri,challenge,state,exp,consumed "
            "FROM connect_requests WHERE req_id=?", (req_id,)).fetchone()
        if row is None or row["consumed"] or row["exp"] < t:
            return None
        return dict(row)

    def consume_connect_request(self, req_id: str, now: float | None = None) -> dict | None:
        """미소비·미만료면 consumed 표시하고 레코드 반환(원자적). 아니면 None."""
        t = time.time() if now is None else now
        with self._lock:
            row = self._db.execute(
                "SELECT client_id,redirect_uri,challenge,state,exp,consumed "
                "FROM connect_requests WHERE req_id=?", (req_id,)).fetchone()
            if row is None or row["consumed"] or row["exp"] < t:
                return None
            self._db.execute(
                "UPDATE connect_requests SET consumed=1 WHERE req_id=?", (req_id,))
            self._db.commit()
        return dict(row)

    # ── owner_plan(연결 플랜 스냅샷) ───────────────────────────────────────────
    def set_owner_plan(self, owner: str, plan: str, now: float | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO owner_plan(owner,plan,updated_at) VALUES(?,?,?)",
                (owner, plan, time.time() if now is None else now))
            self._db.commit()

    def get_owner_plan(self, owner: str) -> str | None:
        row = self._db.execute(
            "SELECT plan FROM owner_plan WHERE owner=?", (owner,)).fetchone()
        return row["plan"] if row else None

    # ── 쿼터(owner 단위 롤링 창 + 초과 잠금) ───────────────────────────────────
    def quota_check(self, owner: str, *, limit: int, window_s: float, lock_s: float,
                    now: float | None = None) -> dict:
        """1회 소비를 시도한다. 허용되면 소비를 기록하고 allowed:True.

        - 잠금 중(now < locked_until): 소비 없이 거부.
        - 창 만료 또는 직전 잠금 해제: 카운터 리셋 후 새 창.
        - 소비 후 한도 도달분의 **다음** 호출에서 잠금(now+lock_s)·거부.
        로컬 stdio(owner 없음)엔 호출되지 않는다 — 호출자가 remote 만 태운다.
        """
        t = time.time() if now is None else now
        with self._lock:
            row = self._db.execute(
                "SELECT count,window_start,locked_until FROM usage WHERE owner=?",
                (owner,)).fetchone()
            count = row["count"] if row else 0
            window_start = row["window_start"] if row else 0.0
            locked_until = row["locked_until"] if row else 0.0

            if locked_until and t < locked_until:
                return {"allowed": False, "reason": "locked", "limit": limit,
                        "count": count, "retry_after_s": int(math.ceil(locked_until - t)),
                        "locked_until": locked_until}

            window_expired = (t - window_start) >= window_s
            lock_expired = bool(locked_until) and t >= locked_until
            if window_expired or lock_expired:
                count, window_start, locked_until = 0, t, 0.0

            if count >= limit:  # 창 내 한도 소진 → 이번 호출에서 잠금
                locked_until = t + lock_s
                self._db.execute(
                    "INSERT OR REPLACE INTO usage(owner,count,window_start,locked_until) "
                    "VALUES(?,?,?,?)", (owner, count, window_start, locked_until))
                self._db.commit()
                return {"allowed": False, "reason": "exceeded", "limit": limit,
                        "count": count, "retry_after_s": int(math.ceil(lock_s)),
                        "locked_until": locked_until}

            count += 1
            self._db.execute(
                "INSERT OR REPLACE INTO usage(owner,count,window_start,locked_until) "
                "VALUES(?,?,?,?)", (owner, count, window_start, locked_until))
            self._db.commit()
            return {"allowed": True, "limit": limit, "count": count,
                    "remaining": max(0, limit - count)}

    # ── 유지보수(만료 청소, 선택) ──────────────────────────────────────────────
    def purge_expired(self, now: float | None = None) -> int:
        t = time.time() if now is None else now
        with self._lock:
            cur = self._db.execute("DELETE FROM codes WHERE exp<?", (t,))
            n = cur.rowcount
            n += self._db.execute("DELETE FROM tokens WHERE exp<?", (t,)).rowcount
            n += self._db.execute(
                "DELETE FROM connect_requests WHERE exp<? OR consumed=1", (t,)).rowcount
            self._db.commit()
        return n
