# -*- coding: utf-8 -*-
"""앱이 OS 키체인에 둔 비밀을 **읽기만** 한다(VIN-132 K3).

호스트는 등록된 `vinc mcp serve` 를 엔진 바이너리로 곧장 띄운다(`install.py` 가
`sys.executable` 을 적는다). 앱(Rust)을 거치지 않으므로 앱이 키를 넘겨줄 수 없다. 그래서
엔진이 앱과 같은 항목을 직접 읽는다. 앱은 Rust `keyring` 3 크레이트로 서비스
`io.vincs.app`, 사용자 이름 = 항목 이름(`vinc.mcp.key` 등)에 쓴다(`app/src-tauri/src/
secret.rs`). 그 크레이트의 플랫폼별 자리를 그대로 읽는다:

- Windows: 자격 증명 관리자의 일반 자격 증명, 대상 이름 `<항목>.<서비스>`, 값은 UTF-16LE
- macOS: 로그인 키체인의 일반 암호, 서비스 `io.vincs.app` · 계정 `<항목>` (`security`)
- Linux: Secret Service 의 `service`·`username` 속성 (`secret-tool`)

**어떤 실패도 예외로 올리지 않는다** — 못 읽으면 None 이고, 부르는 쪽이 "로그인하세요"로
답한다. 값은 로그에 싣지 않는다.
근거: [[vinc.plan.mcp-follows-the-app-mode-vin-132-2026-09-29]]
"""
from __future__ import annotations

import shutil
import subprocess
import sys

SERVICE = "io.vincs.app"
#: 기기별 MCP 키. 로그인 때 연결 페이지가 발급하고 앱이 여기에 둔다(K1·K2).
MCP_KEY = "vinc.mcp.key"
_TIMEOUT_S = 5


def read(name: str, *, service: str = SERVICE) -> str | None:
    try:
        if sys.platform == "win32":
            value = _read_windows(f"{name}.{service}")
        elif sys.platform == "darwin":
            value = _run(["security", "find-generic-password", "-s", service, "-a", name, "-w"])
        else:
            value = _run(["secret-tool", "lookup", "service", service, "username", name])
    except Exception:  # noqa: BLE001 — 저장소 없음·거절·잠김은 전부 "없다"로
        return None
    value = (value or "").strip()
    return value or None


def _run(argv: list[str]) -> str | None:
    if not shutil.which(argv[0]):
        return None
    done = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT_S)
    return done.stdout if done.returncode == 0 else None


def _read_windows(target: str) -> str | None:
    import ctypes
    from ctypes import wintypes as wt

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
                    ("Comment", wt.LPWSTR), ("LastWritten", wt.FILETIME),
                    ("CredentialBlobSize", wt.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR),
                    ("UserName", wt.LPWSTR)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                   ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    advapi32.CredReadW.restype = wt.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    pcred = ctypes.POINTER(CREDENTIAL)()
    if not advapi32.CredReadW(target, 1, 0, ctypes.byref(pcred)):   # 1 = CRED_TYPE_GENERIC
        return None
    try:
        size = pcred.contents.CredentialBlobSize
        blob = ctypes.string_at(pcred.contents.CredentialBlob, size) if size else b""
    finally:
        advapi32.CredFree(pcred)
    return _decode_blob(blob)


def _decode_blob(blob: bytes) -> str:
    """`keyring` 크레이트는 Windows 에 UTF-16LE 로 쓴다. 다른 도구가 UTF-8 로 쓴 값도 읽는다.

    키는 ASCII 라서 UTF-16LE 로 쓴 값은 홀수 번째 바이트가 전부 0 이다. 그것으로만 가른다:
    짝수 길이의 UTF-8 도 UTF-16 으로 "풀리기는" 해서(한자처럼 보이는 글자로) 해독 성공
    여부로는 못 가른다."""
    if blob and len(blob) % 2 == 0 and not any(blob[1::2]):
        return blob.decode("utf-16-le")
    return blob.decode("utf-8", errors="replace")
