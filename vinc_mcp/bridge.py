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
from store import diagnostics, request_timing
# 구현은 한 벌뿐이다(Windows 에서 `os.kill` 이 조회 대상을 죽인 사고의 기록은 그 파일에).
from store.procutil import pid_alive as _pid_alive

DEFAULT_BASE = "http://127.0.0.1:8787"
# 로컬 스토어의 소유자는 하나다: 앱·fragment 기본값·profile.yml·PK 정규화 표가 모두
# `tenant:local` 이다. 여기만 `user:local` 이라 MCP 로 쓴 것이 앱 화면에 영영 안 보였다
# (2026-09-28 재현: 같은 스토어에서 MCP 쓰기는 user:local 에만, 앱 읽기는 0건).
# 이미 user:local 로 쓰인 행은 사이드카가 기동할 때 접는다(`api/owner_fold.py`).
# 근거: [[vinc.plan.desktop-account-mode §6.3]]
DEFAULT_OWNER = "tenant:local"


def _pick_free_port() -> int:
    """OS 가 배정한 빈 포트(선점 후 즉시 close). 사이드카가 그 포트로 재bind 한다 —
    고정 8787 폐기(잔존 사이드카와의 충돌·좀비 근절, A1.9)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _session_path() -> str | None:
    """앱(main.rs)이 기록하는 `~/.vinc/session.json` 경로.

    `MEMORY_DATA_DIR` 로 데이터를 옮긴 개발 환경은 그 DB 의 부모를 쓴다(종전 그대로).
    그 밖에는 늘 데이터 홈이다: 저장 폴더를 옮겨도(VIN-130, `~/.vinc/config.json`)
    옮겨 가는 것은 DB 뿐이고 셸은 세션 파일을 홈에 쓴다. 여기서 DB 의 부모를 따라가면
    MCP 가 앱을 못 찾고 **자기 사이드카를 따로 띄워 같은 스토어를 두 프로세스가 연다**."""
    try:
        explicit = os.environ.get("MEMORY_DATA_DIR")
        if explicit:
            return os.path.join(os.path.dirname(explicit) or ".", "session.json")
        from store.config import vinc_home
        return str(vinc_home() / "session.json")
    except Exception:
        return None


#: 요청이 사이드카에 **닿지 않은 것이 확실한** 실패. 이때만 메서드와 무관하게
#: 재시도해도 안전하다 — 서버는 아무것도 보지 못했다.
_NEVER_ARRIVED = (ConnectionRefusedError, socket.gaierror)


def _retryable(exc: BaseException, method: str) -> bool:
    """이 실패를 다시 보내도 되는가(위험 감사 5.1).

    `URLError` 는 껍데기라 안에 든 `reason` 을 봐야 한다. 연결 거부·DNS 실패는
    요청이 나가지도 못한 것이므로 재시도가 무해하다. 그 밖의 연결 계열 실패
    (타임아웃·중간 끊김)는 **서버가 이미 받았을 수 있다** — GET 은 멱등이라
    그래도 되지만 POST 는 안 된다."""
    inner = getattr(exc, "reason", None)
    if isinstance(exc, _NEVER_ARRIVED) or isinstance(inner, _NEVER_ARRIVED):
        return True
    if method != "GET":
        return False
    return isinstance(exc, (urllib.error.URLError, OSError, TimeoutError)) or \
        isinstance(inner, (OSError, TimeoutError))


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
    @request_timing.timed("bridge")
    def request(self, method: str, path: str, params: dict | None = None,
                body: dict | None = None, timeout: float = 60,
                retries: int = 2, retry_delay: float = 0.3) -> dict:
        """`retries`: **연결 자체가 안 되는 경우만** 짧게 재시도한다(HTTPError 는
        사이드카가 응답은 한 것이므로 아래에서 바로 반환 — 재시도 대상이 아니다).
        2026-08-03 사고 대응: 그때는 사이드카가 몇 분간 죽어 있어 이걸로 못 막았을
        테지만, 수백ms 급 순간 blip 은 이 재시도가 클라이언트(웹·MCP 툴콜 모두
        같은 이 메서드를 탄다)에 아예 안 보이게 흡수한다.

        🔴 **그 독스트링이 코드와 달랐다**(위험 감사 5.1). 실제로는 `except Exception`
        이라 **읽기 타임아웃까지** 재시도했다. 브리지 기본 타임아웃은 60초인데 잡
        타임아웃은 600초다 — 큰 `doc.put`·`ingest:pack`·`sync/apply-delta` 는 사이드카가
        아직 처리 중일 때 60초에 끊기고, 그러면 **이미 큐에 들어간 쓰기를 두 번 더**
        보냈다. MERGE 는 멱등이지만 `_stamp_edge_endpoints` 와 `next_seq` 가 rev·seq 를
        중복 증가시켜 동기화 에코를 만든다.

        이제 판단 기준은 **요청이 서버에 닿았는가**다:

          · 닿지 않은 것이 확실한 실패(연결 거부·DNS)만 재시도한다. 메서드와 무관.
          · GET 은 그 밖의 연결 계열 실패도 재시도한다 — 조회는 멱등이라 안전하고,
            blip 흡수라는 원래 목적이 여기서 성립한다.
          · POST 의 타임아웃·연결 끊김은 **재시도하지 않는다.** 대신 "적용됐을 수도
            있다"를 명시한 오류로 돌려준다. 조용히 두 번 보내느니 한 번 물어보게 한다.
        """
        url = self.base + path
        if params:
            qs = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v not in (None, "")})
            if qs:
                url += "?" + qs
        data = json.dumps(body).encode("utf-8") if body is not None else None

        attempt = 0
        while True:
            started = time.perf_counter()
            status, request_id = "network_error", ""
            req = urllib.request.Request(url, data=data, method=method)
            if method == "GET" and request_timing.active():
                req.add_header(request_timing.HEADER, request_timing.VALUE)
            if self.token:
                req.add_header("Authorization", f"Bearer {self.token}")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if request_timing.active():
                        headers = getattr(resp, "headers", {})
                        request_timing.merge_sidecar(headers.get("Server-Timing", ""))
                    status = getattr(resp, "status", 200)
                    request_id = resp.headers.get("X-Request-Id", "") if hasattr(resp, "headers") else ""
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                status = e.code
                request_id = e.headers.get("X-Request-Id", "") if e.headers else ""
                try:
                    return json.loads(e.read().decode("utf-8"))
                except Exception:
                    return {"error": f"sidecar HTTP {e.code}"}
            except Exception as exc:
                if not _retryable(exc, method):
                    if method != "GET":
                        # 쓰기가 응답 없이 끊겼다. 사이드카가 **이미 적용했을 수도**
                        # 있으므로 다시 보내지 않는다. 사유를 그대로 실어 호출자가
                        # 확인 후 판단하게 한다(조용한 중복 쓰기보다 낫다).
                        return {"error": f"sidecar write unconfirmed: "
                                         f"{type(exc).__name__}: {exc}",
                                "retryable": False,
                                "note": ("the write may already have been applied; "
                                         "verify before retrying")}
                    raise
                attempt += 1
                if attempt > retries:
                    raise
                time.sleep(retry_delay * attempt)
            finally:
                diagnostics.trace(method, url, status, started, request_id=request_id, transport="attached")

    def health(self) -> bool:
        started = time.perf_counter()
        status = "network_error"
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=3) as r:
                status = r.status
                return r.status == 200
        except Exception:
            return False
        finally:
            diagnostics.trace("GET", "/health", status, started, transport="attached")

    def health_detail(self, *, live: bool = False) -> dict | None:
        """사이드카 `/health` **본문**(도달 실패면 None).

        :meth:`health` 는 도달성만 bool 로 돌려주는데, 사이드카가 싣는 진단
        (스토어 상태·임베딩 백로그)이 그 bool 안에서 통째로 버려진다. 허브의 공개
        `/health` 가 그것을 그대로 실어 밖에서 한 번의 curl 로 볼 수 있게 한다 —
        임베딩 정체를 38시간 몰랐던 이유가 정확히 "밖에서 보이는 수치가 없어서"였다.

        ``live=True`` 는 사이드카의 `/health?live=1` 을 부른다. 스토어를 읽지 않아 임베딩
        백로그(`embed`)가 빠지고, 상태 판정·스냅샷처럼 메모리에 있는 값만 온다."""
        try:
            with urllib.request.urlopen(
                    self.base + ("/health?live=1" if live else "/health"), timeout=3) as r:
                if r.status != 200:
                    return None
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

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
            # --no-spawn(허브 attach): 사이드카가 아직 기동 중일 수 있다 — 동시 재시작
            # 레이스(sidecar 가 Kùzu 스토어를 여는 데 잠깐 걸린다). wait 동안 폴링해
            # 준비되면 attach → 첫판 crash 제거(systemd 자동복구되지만 무해한 실패를 없앤다).
            deadline = time.time() + wait
            while time.time() < deadline:
                time.sleep(0.3)
                if self.health():
                    return self
            raise RuntimeError(f"sidecar unreachable: {self.base}")
        if not self.token:
            self.token = secrets.token_urlsafe(24)
        port = _pick_free_port()
        self.base = f"http://127.0.0.1:{port}"
        # 동결 바이너리(D43)면 자기 자신이 serve 를 품고 있다 — 재실행이 곧 spawn.
        cmd = ([sys.executable, "--target", "local", "serve"] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "api.cli", "--target", "local", "serve"])
        # --parent-pid: 어댑터가 죽으면 사이드카도 자동 정리(좀비 근절, cli/serve 지원).
        # URL-safe 토큰은 '-'로 시작할 수 있다. 별도 argv 값이면 argparse가
        # 옵션으로 해석하므로 등호로 묶어 토큰 바이트를 그대로 전달한다.
        cmd += ["--port", str(port), "--token=" + self.token,
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
