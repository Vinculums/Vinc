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
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8787"
DEFAULT_OWNER = "user:local"


class Bridge:
    def __init__(self, base: str | None = None, token: str | None = None,
                 owner: str | None = None):
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
    def ensure(self, *, spawn: bool = True, wait: float = 20.0) -> "Bridge":
        if self.health():
            return self
        if not spawn:
            raise RuntimeError(f"sidecar unreachable: {self.base}")
        if not self.token:
            self.token = secrets.token_urlsafe(24)
        port = urllib.parse.urlparse(self.base).port or 8787
        # 동결 바이너리(D43)면 자기 자신이 serve 를 품고 있다 — 재실행이 곧 spawn.
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "serve"]
        else:
            cmd = [sys.executable, "-m", "api.cli", "serve"]
        cmd += ["--port", str(port), "--token", self.token]
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
