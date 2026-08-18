# -*- coding: utf-8 -*-
"""로컬 로그인 코어 — 온보딩 프로필 + 세션 토큰(설계 [[vinc.spec.architecture §4]], F4).

Vinc 는 단일 사용자·오프라인 앱이다. "로그인"은 외부 인증 서버 왕복이 아니라
**로컬 자격 파일에 대한 패스프레이즈 검증**이다(F4 — 네트워크 왕복 없음). 검증에
성공하면 사이드카가 임의 **세션 토큰**을 발급하고, 데스크톱 셸(Tauri)이 이후 모든
HTTP 요청에 ``Authorization: Bearer <token>`` 로 실어 보낸다. 루프백 바인딩
(127.0.0.1) + 토큰이 이중 방어다(§3-1).

자격 해시는 **argon2id**(설계 §3-1 명시)로, ``argon2-cffi`` 옵션 extra(``vinc[server]``)
에서 lazy-import 한다 — 코어는 의존성 0(F5: 키 없이 기동). 미설치 환경에선 온보딩·
패스프레이즈 검증만 불가하고(명확한 오류) 앱 기동·조회는 그대로 동작한다.

토큰 발급·검증은 stdlib ``secrets`` 만 쓴다(argon2 불요) — 어떤 경로도 소켓을 열지
않는다(F4 실측: 소켓 차단 하에서도 동작).
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path

# argon2id 파라미터(자격 파일 헤더에 함께 저장 — 재해시 없이 검증 가능).
# 대화형 로그인 1회 검증 기준의 보수적 기본값. (RFC 9106 2nd 권장 근방.)
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST = 64 * 1024   # 64 MiB
_ARGON2_PARALLELISM = 4

_CRED_KIND = "vinc/local-credential"


class AuthUnavailable(RuntimeError):
    """argon2 extra 미설치 — 온보딩/패스프레이즈 검증 불가(``pip install vinc[server]``)."""


def _argon2():
    """argon2 PasswordHasher 를 lazy-import(코어 의존성 0 유지 — F5). 미설치 시
    :class:`AuthUnavailable`."""
    try:
        from argon2 import PasswordHasher, Type
    except ImportError as exc:   # pragma: no cover - 설치 여부에 따라
        raise AuthUnavailable(
            "argon2id 자격에는 'vinc[server]' extra 가 필요하다 "
            "(pip install argon2-cffi). 코어·조회는 이것 없이 동작한다."
        ) from exc
    return PasswordHasher(time_cost=_ARGON2_TIME_COST, memory_cost=_ARGON2_MEMORY_COST,
                          parallelism=_ARGON2_PARALLELISM, type=Type.ID)


def cred_path(data_dir: str | Path) -> Path:
    """자격 파일 경로. 데이터 디렉토리 옆 ``auth.json``(§3-2 — 데이터·설정 동거)."""
    return Path(data_dir).parent / "auth.json"


# ── 온보딩 / 검증 ────────────────────────────────────────────────────────────

def create_profile(passphrase: str, *, path: str | Path, force: bool = False) -> dict:
    """온보딩: 패스프레이즈를 argon2id 로 해시해 자격 파일을 만든다(1회). 이미
    있으면 ``force`` 없이 덮어쓰지 않는다(계정 유실 방지)."""
    p = Path(path)
    if p.exists() and not force:
        raise FileExistsError(f"자격 파일 이미 존재: {p} (재설정은 force=True)")
    if not passphrase:
        raise ValueError("빈 패스프레이즈 금지")
    ph = _argon2()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"kind": _CRED_KIND, "algo": "argon2id", "hash": ph.hash(passphrase)}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # 자격 파일은 소유자만 읽도록(단일 사용자 데스크톱 — §3-3).
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return {"created": str(p), "algo": "argon2id"}


def verify_passphrase(passphrase: str, *, path: str | Path) -> bool:
    """패스프레이즈를 자격 파일과 대조(로컬만 — 네트워크 왕복 없음, F4). 파일
    부재·불일치 시 False. argon2 미설치 시 :class:`AuthUnavailable`."""
    p = Path(path)
    if not p.exists():
        return False
    from argon2.exceptions import VerifyMismatchError  # lazy — extra 없으면 아래 _argon2 가 먼저 raise
    ph = _argon2()
    try:
        stored = json.loads(p.read_text(encoding="utf-8")).get("hash", "")
    except (OSError, ValueError):
        return False
    try:
        return bool(ph.verify(stored, passphrase))
    except VerifyMismatchError:
        return False
    except Exception:   # noqa: BLE001 - 손상 해시 등은 인증 실패로 취급
        return False


# ── 세션 토큰 ────────────────────────────────────────────────────────────────

def issue_token(nbytes: int = 32) -> str:
    """새 세션 토큰 발급(stdlib secrets — 소켓 없음, F4). 프로세스 수명 동안만 유효."""
    return secrets.token_urlsafe(nbytes)


class SessionAuth:
    """사이드카 세션 토큰 보관·검증. 기동 시 1개 발급, 셸에 전달. 모든 요청의
    ``Authorization: Bearer`` 헤더를 상수시간 비교한다."""

    def __init__(self, token: str | None = None):
        self.token = token or issue_token()

    def check_header(self, header_value: str | None) -> bool:
        """``Authorization`` 헤더 값이 세션 토큰과 일치하는지(Bearer 스킴). 상수시간
        비교(secrets.compare_digest)로 타이밍 누출 방지."""
        if not header_value:
            return False
        parts = header_value.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False
        return secrets.compare_digest(parts[1], self.token)
