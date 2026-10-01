# -*- coding: utf-8 -*-
"""로컬 스택 — 인코딩 창구 · 사이드카 · 루프백 REST v1 을 스토어 사본 하나 위에 세운다.

CLI 와 v1 경로를 핀 스토어에서 재려면 허브와 같은 모양이 필요하다: 사이드카는 모델을 안
들고(`VINC_EMBED_ENCODE` 창구에 묻는다), v1 은 사이드카 앞의 MCP HTTP 앱이 연다. 여기서는
그 셋을 전부 **이 인터프리터로** 띄운다 — 그래야 질의를 인코딩하는 런타임이 실행 지문
(:mod:`evalkit.runtime`)과 같다. 다른 파이썬으로 창구를 띄우면 지문이 거짓말을 한다.

v1 의 키 검증은 계정 서버 대신 **루프백 전용 스텁**이다(테스트 `tests/mcp/test_rest_v1.py` 와
같은 방식). 게이트 파이프라인·인자 검사·응답 봉투는 그대로 지난다 — 빠지는 것은 계정
조회 한 단계뿐이고, 그 단계는 검색 결과를 바꾸지 않는다.

앱의 세션(`~/.vinc/session.json`)은 건드리지 않는다: `MEMORY_DATA_DIR` 을 스토어 사본으로
두면 CLI 는 그 옆의 `session.json` 을 읽는다(`vinc_mcp/bridge.py::_session_path`).
"""
from __future__ import annotations

import json
import os
import pathlib
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(check, what: str, proc: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{what} 가 먼저 죽었다(exit {proc.returncode})")
        try:
            if check():
                return
        except Exception:  # noqa: BLE001 — 아직 안 떴다
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{what} 가 {timeout:.0f}초 안에 뜨지 않았다")


class LocalStack:
    def __init__(self, data: str, owner: str, model: str, onnx: str, run_dir: pathlib.Path):
        self.data, self.owner, self.model, self.onnx = data, owner, model, onnx
        self.run_dir = run_dir
        self.procs: list[subprocess.Popen] = []
        self.env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
        self.env["PYTHONIOENCODING"] = "utf-8"
        self.encode_token = secrets.token_hex(16)
        self.sidecar_token = secrets.token_hex(16)
        self.api_key = "vinc_sk_eval_" + secrets.token_hex(8)
        self._http = None

    def _spawn(self, args: list[str], env: dict, log: str) -> subprocess.Popen:
        f = open(self.run_dir / log, "w", encoding="utf-8")
        p = subprocess.Popen([sys.executable, "-X", "utf8", *args], cwd=ROOT, env=env,
                             stdout=f, stderr=subprocess.STDOUT)
        self.procs.append(p)
        return p

    def __enter__(self) -> "LocalStack":
        # 1) 인코딩 창구 — 이 인터프리터의 onnxruntime 으로.
        self.encode_port = _free_port()
        enc_env = {**self.env, "VINC_EMBED_MODEL": self.model,
                   "VINC_EMBED_MODEL_PATH": self.onnx, "VINC_EMBED_BIND": "127.0.0.1",
                   "VINC_EMBED_ENCODE_TOKEN": self.encode_token}
        enc = self._spawn(["packaging/embed_worker.py", "--serve-only",
                           "--encode-port", str(self.encode_port)], enc_env, "encode.log")
        url = f"http://127.0.0.1:{self.encode_port}"

        def encode_ok() -> bool:
            req = urllib.request.Request(url + "/encode", data=json.dumps(
                {"texts": ["warmup"], "kind": "query"}).encode(), headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.encode_token}"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return bool(json.loads(r.read()).get("vectors"))
        _wait(encode_ok, "인코딩 창구", enc, 240)

        # 2) 사이드카 — 스토어 사본을 연다. 질의 임베딩은 위 창구에 묻는다.
        self.sidecar_port = _free_port()
        side_env = {**self.env, "VINC_EMBED_ENCODE": url,
                    "VINC_EMBED_ENCODE_TOKEN": self.encode_token,
                    # 콜드 판정 없이 늘 길게 기다린다 — 강등된 답을 재면 측정이 틀린다.
                    "VINC_EMBED_ENCODE_TIMEOUT": "60"}
        side = self._spawn(["-m", "api.cli", "serve", "--data", self.data,
                            "--port", str(self.sidecar_port), "--token", self.sidecar_token],
                           side_env, "sidecar.log")
        base = f"http://127.0.0.1:{self.sidecar_port}"
        sys.path.insert(0, str(ROOT))
        from vinc_mcp.bridge import Bridge            # noqa: PLC0415
        self.bridge = Bridge(base=base, token=self.sidecar_token, owner=self.owner)
        _wait(self.bridge.health, "사이드카", side, 180)
        # CLI 가 붙을 세션 파일 — 스토어 사본 옆(MEMORY_DATA_DIR 의 부모)에만 쓴다.
        (pathlib.Path(self.data).parent / "session.json").write_text(json.dumps(
            {"port": self.sidecar_port, "token": self.sidecar_token, "pid": side.pid}),
            encoding="utf-8")

        # 3) 루프백 REST v1 — 계정 조회만 스텁이다.
        from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: PLC0415
        key, owner = self.api_key, self.owner

        def verify(token, team=None):
            return {"owner": owner, "scope": "read", "plan": "free"} \
                if token == key and not team else None
        app = MCPHTTPApp(self.bridge, verify_key=verify)
        self._http = make_server(app, "127.0.0.1", 0)
        threading.Thread(target=self._http.serve_forever, daemon=True).start()
        self.v1_base = f"http://127.0.0.1:{self._http.server_address[1]}"
        self.encode_url, self.sidecar_base = url, base
        return self

    def __exit__(self, *exc) -> None:
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
        for p in reversed(self.procs):
            p.terminate()
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()
        try:
            (pathlib.Path(self.data).parent / "session.json").unlink()
        except FileNotFoundError:
            pass
