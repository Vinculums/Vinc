# -*- coding: utf-8 -*-
"""OAuth·쿼터 영속 스토어 — SQLite(stdlib), 재기동에도 커넥터 유지.

왜(B-A): OAuthGate 의 인메모리 4-dict 는 서비스 재시작마다 전 커넥터를 재인가
시킨다(발주자 "제일 힘든 것"의 한 갈래). 상태를 `$VINC_MCP_STATE_DIR/oauth.db`
로 옮겨 **재시작에도 인가·토큰·쿼터가 유지**되게 한다. stdlib sqlite3 만 쓰므로
MCP 의 무외부의존(D36)을 지킨다.

경로: env `VINC_MCP_STATE_DIR` > `~/.vinc/mcp`(개발 기본).
  · Azure Container Apps(현행): 컨테이너 엔트리포인트가 `/data/mcp` 로 기본값을
    준다 — `/data` 가 유일한 영속 마운트(Azure Files)다. **폴백 경로(`~`)는
    이미지 레이어라 배포마다 지워진다**: 그래서 실제로 배포할 때마다 커넥터가
    `400 {"error":"unknown client_id"}` 로 끊겼다(2026-08-03 수정).
  · 구 가비아 VM(참고): systemd `StateDirectory=vinc-mcp` → `/var/lib/vinc-mcp`.
    ProtectHome=read-only 라 홈에 못 써서 env 주입이 필요했다. 이 경로는 이제
    as-built 가 아니다(2026-07-28/29 Azure 이관).

동시성: ThreadingHTTPServer 라 여러 스레드가 접근한다 → 단일 커넥션
(check_same_thread=False) + 쓰기 Lock. 인증 빈도(커넥션당 + 60s 캐시)에서
경합은 무시할 수준이다.
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import shutil
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
    """SQLite **작업본**이 사는 경로: env VINC_MCP_STATE_DIR > ~/.vinc/mcp(개발 기본).

    ⚠ **여기는 반드시 로컬 파일시스템이어야 한다.** 네트워크 마운트(Azure Files=SMB)를
    가리키면 SQLite 가 첫 쓰기에서 `database is locked` 로 죽는다 — 실측 장애
    (2026-08-03, 프로덕션 리비전 Failed). 영속은 :func:`resolve_snapshot_path` 가
    파일 복사로 담당한다."""
    explicit = os.environ.get("VINC_MCP_STATE_DIR")
    root = pathlib.Path(explicit) if explicit else (pathlib.Path.home() / ".vinc" / "mcp")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return str(root)


def resolve_snapshot_path() -> str | None:
    """영속 스냅샷 파일 경로: env `VINC_MCP_STATE_SNAPSHOT`(미설정이면 스냅샷 없음).

    **여기가 영속 볼륨(SMB)이다.** 이 경로에는 SQLite 를 열지 않는다 — 바이트 단위
    **파일 복사**만 한다. 그래서 네트워크 파일시스템의 락 의미론과 무관하다."""
    p = (os.environ.get("VINC_MCP_STATE_SNAPSHOT") or "").strip()
    return p or None


#: 저널 모드 기본값. 작업본이 로컬이므로 WAL 도 쓸 수 있지만 이득이 없다 — 커넥션
#: 하나를 쓰기 Lock 으로 직렬화하고 허브는 `min=max=1` 이며 DB 는 수 KB 다. 게다가
#: WAL 은 사이드카 파일(-wal/-shm)을 만들어 **스냅샷 한 파일로는 일관 복원이 안 된다**
#: (그 둘까지 복사해야 한다). 롤백 저널은 커밋 후 단일 파일이라 복사가 곧 일관 스냅샷이다.
_JOURNAL_MODES = ("DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF")
_DEFAULT_JOURNAL = "DELETE"


#: `clients` 표의 상한(env `VINC_MCP_MAX_CLIENTS`). RFC 7591 동적 등록은 무인증이라
#: 상한이 없으면 누구나 표를 늘릴 수 있고, 등록 하나가 곧 DB 전체 복사 하나다
#: (:meth:`Store.add_client` 참조). 500 은 실사용(커넥터 수십)보다 넉넉하면서
#: 파일을 수십 KB 로 묶는 값이다 — 정확한 값이 중요한 자리가 아니라, 상한이
#: **있다는 것**이 중요한 자리다.
MAX_CLIENTS = max(1, int(os.environ.get("VINC_MCP_MAX_CLIENTS") or 500))


def resolve_journal_mode() -> str:
    """저널 모드: env `VINC_MCP_SQLITE_JOURNAL` > DELETE. 미지 값은 기본으로 강등한다
    (오타가 SQLite 문법 오류로 번지지 않게 — 상태 DB 는 기동 경로다)."""
    want = (os.environ.get("VINC_MCP_SQLITE_JOURNAL") or "").strip().upper()
    return want if want in _JOURNAL_MODES else _DEFAULT_JOURNAL


class Store:
    """OAuth·쿼터 상태. **로컬 SQLite 작업본 + 영속 볼륨 스냅샷**(2026-08-03 재설계).

    왜 이 모양인가. 상태를 영속 볼륨에 직접 두려고 SQLite 를 Azure Files(SMB) 위에
    올렸더니 첫 스키마 쓰기가 `database is locked` 로 실패해 MCP 가 기동하지 못했다
    (프로덕션 장애). SQLite 는 바이트 범위 락에 기대는데 SMB 가 그것을 제대로 주지
    않는다 — WAL 만의 문제가 아니라 잠금 모델 전반의 문제다.

    그래서 **읽고 쓰는 곳과 남기는 곳을 분리**한다: SQLite 는 로컬 디스크에서 평소대로
    돌고(락 정상), 영속 볼륨에는 커밋된 DB 파일을 **통째로 복사**해 둔다. 복사는 SMB 가
    확실히 지원하는 연산이다(임베딩 모델 캐시가 같은 볼륨에서 이미 그렇게 산다).
    기동 시 작업본이 없으면 스냅샷에서 복원한다 → 배포·재시작을 넘어 커넥터가 유지된다.

    잃을 수 있는 것: 마지막 스냅샷 이후의 쓰기. 그래서 **영속이 필요한 쓰기**(클라이언트
    등록·토큰·코드·승인요청·플랜)마다 즉시 스냅샷한다. 쿼터 카운터는 스냅샷하지 않는다 —
    창(window) 만료로 어차피 리셋되는 연성 상태이고, 툴 호출마다 복사하면 비용만 든다.
    """

    def __init__(self, path: str | None = None, *, snapshot: str | None = None):
        if path is None:
            path = os.path.join(resolve_state_dir(), "oauth.db")
        self.path = path
        self.snapshot_path = snapshot if snapshot is not None else resolve_snapshot_path()
        #: 진단 두 축을 **따로** 든다 — 하나로 뭉치면 기동 끝의 저장이 복원 결과를
        #: 덮어써서 "무엇이 일어났는지"를 잃는다(복원됐는지가 이 사고의 관심사다).
        self.restore_state = "off" if not self.snapshot_path else "pending"
        self.snapshot_state = "off" if not self.snapshot_path else "pending"
        self.snapshot_error = ""
        # 작업본 상위 디렉터리는 **여기서** 보장한다. 호출부가 만들어 주리라 믿으면
        # `unable to open database file` 로 죽는다 — 엔트리포인트가 만들긴 하지만
        # 그 한 줄이 빠지는 것이 바로 이 파일이 낸 사고의 계보다.
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._restore_snapshot()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            # 실제로 적용된 모드를 되읽어 남긴다 — SQLite 는 저널 모드 전환이 막히면
            # **에러 없이 기존 모드를 돌려준다**. 조용한 불일치를 남기지 않는다.
            row = self._db.execute(
                f"PRAGMA journal_mode={resolve_journal_mode()}").fetchone()
            self.journal_mode = (row[0] if row else "").lower()
            self._db.execute("PRAGMA busy_timeout=3000")
            self._db.executescript(_SCHEMA)
            # 구 스냅샷은 CREATE IF NOT EXISTS 로 열이 안 늘어난다. resource 는
            # ChatGPT 가 인가·토큰 요청에 붙이는 audience 라, 없으면 에코할 자리가 없다.
            for table in ("codes", "tokens", "refresh", "connect_requests"):
                self._ensure_column(table, "resource", "TEXT NOT NULL DEFAULT ''")
            self._db.commit()
        self._save_snapshot()   # 첫 기동에 스키마만이라도 남긴다(경로 쓰기 가능 확인)

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        cols = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # ── 영속 스냅샷 ──────────────────────────────────────────────────────────
    # 전 구간 fail-soft 다: 스냅샷은 **증강**이지 서비스의 성립 조건이 아니다.
    # 여기서 예외를 올리면 이 파일이 낸 바로 그 장애(상태 저장 실패 → MCP 전면 정지)를
    # 되풀이한다. 실패는 상태 문자열로 남기고 서비스는 계속 산다.

    def _restore_snapshot(self) -> None:
        """작업본이 없고 스냅샷이 있으면 복원한다(배포 직후 경로)."""
        snap = self.snapshot_path
        if not snap:
            return
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > 0:
                # 작업본이 살아 있으면 **덮지 않는다** — 오래된 스냅샷이 방금 발급한
                # 토큰을 되돌리면 재기동이 아니라 데이터 손실이다.
                self.restore_state = "working-copy-kept"
                return
            if not os.path.exists(snap) or os.path.getsize(snap) == 0:
                self.restore_state = "no-snapshot"
                return
            shutil.copyfile(snap, self.path)
            self.restore_state = "restored"
        except Exception as exc:                     # noqa: BLE001 — fail-soft 가 요점
            self.restore_state = "restore-failed"
            self.snapshot_error = f"{type(exc).__name__}: {exc}"

    def _save_snapshot(self) -> None:
        """작업본을 스냅샷 경로로 복사한다 — 임시 파일에 쓰고 rename(부분 파일 금지).

        rename 이 막히는 파일시스템이 있으므로(교차 장치·SMB 특이) 실패하면 직접
        복사로 물러난다. 어느 쪽도 안 되면 상태만 남기고 넘어간다."""
        snap = self.snapshot_path
        if not snap:
            return
        tmp = f"{snap}.tmp"
        try:
            os.makedirs(os.path.dirname(os.path.abspath(snap)) or ".", exist_ok=True)
            shutil.copyfile(self.path, tmp)
            try:
                os.replace(tmp, snap)
            except OSError:
                shutil.copyfile(tmp, snap)
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            self.snapshot_state = "ok"
            self.snapshot_error = ""
        except Exception as exc:                     # noqa: BLE001
            self.snapshot_state = "save-failed"
            self.snapshot_error = f"{type(exc).__name__}: {exc}"
            try:
                os.remove(tmp)
            except OSError:
                pass

    def health(self) -> dict:
        """상태 저장소 진단(‥/health 노출용). 영속이 실제로 되고 있는지가 요점이다 —
        이 사고의 급소가 '되는 줄 알았는데 안 되고 있었다'였다."""
        return {"path": self.path, "journal_mode": getattr(self, "journal_mode", ""),
                "snapshot": self.snapshot_path or "", "snapshot_state": self.snapshot_state,
                "restore_state": self.restore_state,
                **({"snapshot_error": self.snapshot_error} if self.snapshot_error else {})}

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ── clients ─────────────────────────────────────────────────────────────
    def add_client(self, client_id: str, redirect_uris: list[str], name: str,
                   now: float | None = None) -> None:
        """RFC 7591 동적 등록. **무인증 `POST /register` 에서 도달한다** — 그래서
        여기서 파일 크기를 묶는다.

        왜 여기냐: 영속이 필요한 쓰기마다 DB 를 **통째로 복사**하는데(위 클래스
        독스트링 참조 — SMB 락을 피하려는 옳은 설계다), 만료 청소를 아무도 부르지
        않고 `clients` 에 상한도 없어서 파일이 단조 증가했다. 그러면 복사 비용이
        요청 수에 대해 초선형으로 자란다. 앞단에 레이트리밋도 없고, 그 파일시스템이
        하드다운 4회의 공통 조건이다. 비용 분석은 인증된 고빈도 경로(쿼터 카운터를
        스냅샷에서 뺀 판단)에는 적용됐는데 무인증 경로에는 적용된 적이 없었다.

        스냅샷을 디바운스하지 않는 이유: 그건 이 설계가 막으려던 손실 창을 다시
        연다. 파일을 작게 유지하는 쪽이 안전하다."""
        t = time.time() if now is None else now
        with self._lock:
            self._purge_expired_locked(t)
            self._db.execute(
                "INSERT OR REPLACE INTO clients(client_id,redirect_uris,name,created_at) "
                "VALUES(?,?,?,?)",
                (client_id, json.dumps(redirect_uris), name or "", t))
            # 프루닝은 INSERT **뒤에** — 방금 등록한 클라이언트가 집계에 들어가고,
            # created_at 이 가장 커서 살아남는다.
            self._prune_clients_locked()
            self._db.commit()
            self._save_snapshot()

    def get_client(self, client_id: str) -> dict | None:
        row = self._db.execute(
            "SELECT redirect_uris,name FROM clients WHERE client_id=?", (client_id,)).fetchone()
        if row is None:
            return None
        return {"redirect_uris": json.loads(row["redirect_uris"]), "name": row["name"]}

    # ── codes(단일 소비) ──────────────────────────────────────────────────────
    def add_code(self, code: str, client_id: str, redirect_uri: str, challenge: str,
                 owner: str | None, plan: str | None, exp: float,
                 resource: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO codes(code,client_id,redirect_uri,challenge,owner,plan,exp,resource) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (code, client_id, redirect_uri, challenge, owner, plan, exp, resource or ""))
            self._db.commit()
            self._save_snapshot()

    def pop_code(self, code: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT client_id,redirect_uri,challenge,owner,plan,exp,resource "
                "FROM codes WHERE code=?",
                (code,)).fetchone()
            if row is None:
                return None
            self._db.execute("DELETE FROM codes WHERE code=?", (code,))
            self._db.commit()
            self._save_snapshot()
        return dict(row)

    # ── tokens ────────────────────────────────────────────────────────────────
    def add_token(self, token: str, client_id: str, owner: str | None,
                  plan: str | None, exp: float, now: float | None = None,
                  resource: str = "") -> None:
        with self._lock:
            # 만료 토큰은 get_token 이 **읽힐 때만** 지운다 — 아무도 다시 안 부르는
            # 토큰은 영영 남는다. 발급 시점이 그걸 쓸어 담기 좋은 자리다(같은 락,
            # 같은 스냅샷 한 번). 커넥터가 고정된 허브는 재등록 없이 갱신만 도는데,
            # 그 경우 표를 늘리는 경로가 바로 여기다.
            # ⚠ **INSERT 앞에서** 지운다. 뒤에서 지우면 이미 만료된 exp 로 발급한
            # 토큰을 방금 쓰고 곧바로 지워, 호출자에게는 쓰기가 조용히 실패한 것처럼
            # 보인다.
            self._purge_expired_locked(time.time() if now is None else now)
            self._db.execute(
                "INSERT OR REPLACE INTO tokens(token,client_id,owner,plan,exp,resource) "
                "VALUES(?,?,?,?,?,?)",
                (token, client_id, owner, plan, exp, resource or ""))
            self._db.commit()
            self._save_snapshot()

    def get_token(self, token: str, now: float | None = None) -> dict | None:
        """유효 토큰이면 {client_id,owner,plan,exp}, 만료면 삭제 후 None."""
        t = time.time() if now is None else now
        row = self._db.execute(
            "SELECT client_id,owner,plan,exp,resource FROM tokens WHERE token=?",
            (token,)).fetchone()
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
                    plan: str | None, resource: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO refresh(token,client_id,owner,plan,resource) "
                "VALUES(?,?,?,?,?)",
                (token, client_id, owner, plan, resource or ""))
            self._db.commit()
            self._save_snapshot()

    def pop_refresh(self, token: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT client_id,owner,plan,resource FROM refresh WHERE token=?",
                (token,)).fetchone()
            if row is None:
                return None
            self._db.execute("DELETE FROM refresh WHERE token=?", (token,))
            self._db.commit()
            self._save_snapshot()
        return dict(row)

    # ── connect_requests(회원 승인 흐름, 단일 소비) ────────────────────────────
    def add_connect_request(self, req_id: str, client_id: str, redirect_uri: str,
                            challenge: str, state: str | None, exp: float,
                            resource: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO connect_requests"
                "(req_id,client_id,redirect_uri,challenge,state,exp,resource) "
                "VALUES(?,?,?,?,?,?,?)",
                (req_id, client_id, redirect_uri, challenge, state, exp, resource or ""))
            self._db.commit()
            self._save_snapshot()

    def get_connect_request(self, req_id: str, now: float | None = None) -> dict | None:
        t = time.time() if now is None else now
        row = self._db.execute(
            "SELECT client_id,redirect_uri,challenge,state,exp,consumed,resource "
            "FROM connect_requests WHERE req_id=?", (req_id,)).fetchone()
        if row is None or row["consumed"] or row["exp"] < t:
            return None
        return dict(row)

    def consume_connect_request(self, req_id: str, now: float | None = None) -> dict | None:
        """미소비·미만료면 consumed 표시하고 레코드 반환(원자적). 아니면 None."""
        t = time.time() if now is None else now
        with self._lock:
            row = self._db.execute(
                "SELECT client_id,redirect_uri,challenge,state,exp,consumed,resource "
                "FROM connect_requests WHERE req_id=?", (req_id,)).fetchone()
            if row is None or row["consumed"] or row["exp"] < t:
                return None
            self._db.execute(
                "UPDATE connect_requests SET consumed=1 WHERE req_id=?", (req_id,))
            self._db.commit()
            self._save_snapshot()
        return dict(row)

    # ── owner_plan(연결 플랜 스냅샷) ───────────────────────────────────────────
    def set_owner_plan(self, owner: str, plan: str, now: float | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO owner_plan(owner,plan,updated_at) VALUES(?,?,?)",
                (owner, plan, time.time() if now is None else now))
            self._db.commit()
            self._save_snapshot()

    def get_owner_plan(self, owner: str) -> str | None:
        row = self._db.execute(
            "SELECT plan FROM owner_plan WHERE owner=?", (owner,)).fetchone()
        return row["plan"] if row else None

    def get_owner_plan_age(self, owner: str,
                           now: float | None = None) -> tuple[str | None, float | None]:
        """(plan, 나이 초) — 없으면 (None, None).

        나이가 필요한 이유: OAuth 토큰의 등급은 승인 시점에 동결되므로 이 표가
        유일한 갱신 지점이고, 갱신 여부는 **얼마나 오래됐는가**로만 판단할 수 있다.
        """
        row = self._db.execute(
            "SELECT plan, updated_at FROM owner_plan WHERE owner=?", (owner,)).fetchone()
        if not row:
            return None, None
        age = (time.time() if now is None else now) - float(row["updated_at"])
        return row["plan"], max(0.0, age)

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

    def quota_snapshot(self, owner: str, *, limit: int, window_s: float,
                       now: float | None = None) -> dict:
        """소비·리셋·잠금 생성 없이 한 카운터만 읽는다. 신원과 면제는 호출자가 판정한다.

        만료된 창·풀린 잠금은 다음 quota_check의 리셋을 미리 읽되 저장하지 않는다.
        활성 잠금은 창 만료·상한 변경보다 먼저다. 공개 whoami에는 아직 연결하지 않는다.
        """
        t = time.time() if now is None else now
        with self._lock:
            row = self._db.execute(
                "SELECT count,window_start,locked_until FROM usage WHERE owner=?",
                (owner,)).fetchone()
            count = int(row["count"]) if row else 0
            start = float(row["window_start"]) if row else 0.0
            until = float(row["locked_until"]) if row else 0.0
        locked = bool(until) and t < until
        expired = not row or (t - start) >= window_s or (bool(until) and t >= until)
        if expired and not locked:
            count, start, until = 0, None, None
        return {"count": count, "remaining": 0 if locked else max(0, limit - count),
                "window_start": start, "locked_until": until if locked else None}

    def usage_rows(self) -> list[dict]:
        """usage 표 전량을 **읽기만** 한다 — 운영자 사용량 덤프(`/internal/usage`)의 호출 수.

        창이 지났는지는 여기서 판정하지 않는다(창 길이는 부르는 쪽 설정이다). 쓰기가
        아니므로 스냅샷도 부르지 않는다 — 이 파일은 쓰기마다 전체를 복사한다."""
        with self._lock:
            rows = self._db.execute(
                "SELECT owner,count,window_start,locked_until FROM usage").fetchall()
        return [dict(r) for r in rows]

    # ── 유지보수(만료 청소) ────────────────────────────────────────────────────
    def purge_owner(self, owner: str) -> dict:
        """한 소유자의 OAuth 상태를 전부 지운다 — 계정 삭제 캐스케이드의 허브 몫(판정
        D11). 그래프 purge 뒤에도 토큰·리프레시·코드·사용량·플랜 캐시가 그 소유자
        이름으로 남아 있었다(2026-09-26 갭 분석 §2.2). 영속이 필요한 쓰기라 스냅샷까지
        한다. 표별 건수를 돌려줘 호출자(계정)가 감사할 수 있게 한다."""
        if not owner or not isinstance(owner, str):
            raise ValueError("purge_owner: owner must not be empty")
        with self._lock:
            out: dict[str, int] = {}
            for table in ("tokens", "refresh", "codes", "usage", "owner_plan"):
                out[table] = self._db.execute(
                    f"DELETE FROM {table} WHERE owner=?", (owner,)).rowcount
            self._db.commit()
            self._save_snapshot()
        return out

    def _purge_expired_locked(self, t: float) -> int:
        """만료 행을 지운다 — **락을 이미 쥔 채** 부르고, 커밋·스냅샷은 하지 않는다.

        ⚠ `self._lock` 은 `threading.Lock` 이라 **재진입이 안 된다.** 쓰기 경로에서
        공개 `purge_expired()` 를 그대로 부르면 자기 락을 기다리며 교착한다. 그래서
        알맹이를 여기 떼어 두고 커밋·스냅샷은 부르는 쪽이 자기 것 한 번으로 겸한다
        (안 그러면 쓰기 하나가 전체 파일 복사를 **두 번** 한다)."""
        n = self._db.execute("DELETE FROM codes WHERE exp<?", (t,)).rowcount
        n += self._db.execute("DELETE FROM tokens WHERE exp<?", (t,)).rowcount
        n += self._db.execute(
            "DELETE FROM connect_requests WHERE exp<? OR consumed=1", (t,)).rowcount
        return n

    def _prune_clients_locked(self) -> int:
        """`clients` 를 :data:`MAX_CLIENTS` 로 묶는다(락 보유 전제, 커밋 안 함).

        **살아 있는 커넥터는 지우지 않는다** — 토큰·리프레시·미소비 코드·승인요청
        중 하나라도 그 `client_id` 를 참조하면 후보에서 뺀다. 남은 것 중 오래된
        순으로 초과분만 지운다. 방금 INSERT 한 클라이언트는 `created_at` 이 가장
        커서 살아남는다.

        한계를 분명히: 상한이 **절대적이지 않다.** 모든 클라이언트가 유효 토큰을
        들고 있으면 0건을 지우고 표는 상한을 넘는다. 그래도 막으려는 것은 정확히
        막힌다 — 무인증 `/register` 로 들어온 등록은 정의상 토큰이 없어서 언제나
        후보다. 인증된 커넥터가 수백 개 쌓이는 것은 다른 문제이고, 그때는 지우는
        것이 답이 아니다."""
        total = self._db.execute("SELECT count(*) FROM clients").fetchone()[0]
        excess = int(total) - MAX_CLIENTS
        if excess <= 0:
            return 0
        return self._db.execute(
            "DELETE FROM clients WHERE client_id IN ("
            "  SELECT client_id FROM clients"
            "   WHERE client_id NOT IN (SELECT client_id FROM tokens)"
            "     AND client_id NOT IN (SELECT client_id FROM refresh)"
            "     AND client_id NOT IN (SELECT client_id FROM codes)"
            "     AND client_id NOT IN (SELECT client_id FROM connect_requests)"
            "   ORDER BY created_at ASC LIMIT ?)", (excess,)).rowcount

    def purge_expired(self, now: float | None = None) -> int:
        t = time.time() if now is None else now
        with self._lock:
            n = self._purge_expired_locked(t)
            self._db.commit()
            self._save_snapshot()
        return n
