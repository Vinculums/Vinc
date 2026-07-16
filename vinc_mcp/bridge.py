# -*- coding: utf-8 -*-
"""사이드카 HTTP 브리지 — MCP 어댑터의 유일한 하류 표면(스켈레톤 §2).

attach 우선, 죽어 있으면 spawn(스켈레톤 §3 의 MVP 축소: lockfile refcount 는
후속 — 지금은 '우리가 띄운 프로세스만 종료 시 정리'로 안전 측만 유지한다).
F4 불변: 사이드카는 127.0.0.1 + 세션 토큰. 브리지는 그 계약의 클라이언트다.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8787"
DEFAULT_OWNER = "user:local"


def _pick_free_port() -> int:
    """OS 가 배정한 빈 포트(선점 후 즉시 close). 사이드카가 그 포트로 재bind 한다 —
    고정 8787 폐기(잔존 사이드카와의 충돌·좀비 근절, A1.9)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _pid_alive(pid: int) -> bool:
    """pid 생존 여부(빠른 판정 — 최종 확인은 항상 /health). 불확실하면 True 로 두어
    앱 사이드카를 함부로 무시하지 않는다(뒤의 health 가 걸러줌)."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return True


def _session_path() -> str | None:
    """앱(main.rs)이 기록하는 `~/.vinc/session.json` 경로 — `resolve_data_dir` 의 홈과
    정합(db 의 부모 = `.vinc` 루트)."""
    try:
        from store.config import resolve_data_dir
        db = resolve_data_dir(create=False)
        return os.path.join(os.path.dirname(db) or ".", "session.json")
    except Exception:
        return None


class Bridge:
    def __init__(self, base: str | None = None, token: str | None = None,
                 owner: str | None = None):
        # base 를 사용자가 명시했나(원격 등) — 명시 base 는 세션 attach 보다 우선한다.
        self._base_explicit = bool(base or os.environ.get("VINC_SIDECAR_BASE"))
        self.base = (base or os.environ.get("VINC_SIDECAR_BASE") or DEFAULT_BASE).rstrip("/")
        self.token = token or os.environ.get("VINC_SIDECAR_TOKEN") or ""
        self.owner = owner or os.environ.get("VINC_OWNER") or DEFAULT_OWNER
        self._child: subprocess.Popen | None = None

    # ── HTTP ────────────────────────────────────────────────────────────────
    def request(self, method: str, path: str, params: dict | None = None,
                body: dict | None = None, timeout: float = 60) -> dict:
        url = self.base + path
        if params:
            qs = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v not in (None, "")})
            if qs:
                url += "?" + qs
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"error": f"sidecar HTTP {e.code}"}

    def health(self) -> bool:
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    # ── 생명주기(attach/spawn) ───────────────────────────────────────────────
    def _read_session(self) -> dict | None:
        """앱 세션 파일 → `{port, token}`(유효할 때만). pid 죽음/파싱 실패는 None."""
        path = _session_path()
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return None
        port = int(d.get("port") or 0)
        if not port:
            return None
        pid = d.get("pid")
        if pid is not None and not _pid_alive(int(pid)):
            return None
        return {"port": port, "token": d.get("token") or None}

    def ensure(self, *, spawn: bool = True, wait: float = 20.0) -> "Bridge":
        """사이드카 확보. 우선순위(A1.9 — 스토어 이중 개방·좀비 근절):
        ① 사용자가 명시한 base(원격 등)가 살아있으면 그대로.
        ② 앱이 남긴 `~/.vinc/session.json` → **앱 사이드카에 attach**(자기 spawn 안 함,
           `_child=None` 이라 close 시 앱 사이드카를 죽이지 않음).
        ③ 현재 base(기본 8787 등)가 살아있으면 attach.
        ④ 없으면 **빈 포트 선점 + `--parent-pid`** 로 자기 spawn(고정 8787·좀비 폐기)."""
        if self._base_explicit and self.health():
            return self
        if not self._base_explicit:
            sess = self._read_session()
            if sess:
                self.base = f"http://127.0.0.1:{sess['port']}"
                if sess["token"]:
                    self.token = sess["token"]
                if self.health():
                    self._child = None      # 앱 소유 — 우리가 종료하지 않는다
                    return self
                self.base = DEFAULT_BASE     # stale 세션 → 기본값 복원 후 계속
        if self.health():
            return self
        if not spawn:
            raise RuntimeError(f"sidecar unreachable: {self.base}")
        if not self.token:
            self.token = secrets.token_urlsafe(24)
        port = _pick_free_port()
        self.base = f"http://127.0.0.1:{port}"
        # 동결 바이너리(D43)면 자기 자신이 serve 를 품고 있다 — 재실행이 곧 spawn.
        cmd = ([sys.executable, "serve"] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "api.cli", "serve"])
        # --parent-pid: 어댑터가 죽으면 사이드카도 자동 정리(좀비 근절, cli/serve 지원).
        cmd += ["--port", str(port), "--token", self.token,
                "--parent-pid", str(os.getpid())]
        self._child = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.health():
                return self
            time.sleep(0.3)
        self.close()
        raise RuntimeError("sidecar failed to start")

    def close(self) -> None:
        if self._child is not None:
            self._child.terminate()
            self._child = None
