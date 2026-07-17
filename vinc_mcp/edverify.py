# -*- coding: utf-8 -*-
"""벤더 Ed25519(RFC 8032) + 크로스서비스 서명 어서션 — stdlib 전용.

왜 벤더인가(B2·D36): MCP 어댑터는 계정 서비스와 **시크릿·DB를 공유하지 않는다**.
신뢰는 각자 Ed25519 키쌍을 갖고 **상대의 공개키만** env 로 핀해서 성립한다(공개키
공유 ≠ 시크릿 공유). MCP 는 stdlib 만 쓰므로(외부 의존 0) 순수 파이썬 구현을
심는다. 인가·검증은 커넥션당 저빈도 + 60s 캐시라 순수 파이썬 속도로 충분하다.

아래 원시 구현(point_*, sign, verify, secret_to_public)은 RFC 8032 §6 의
레퍼런스 구현(public domain)을 그대로 옮긴 것이다 — 상수·좌표계·압축 규칙이
표준과 1:1 이라 Node `crypto`(동일 RFC)와 **바이트 단위로 상호 검증**된다
(tests: Node 서명 ↔ 여기 verify, 그리고 그 역이 동일 seed/메시지에서 일치).

어서션 포맷(계정측 lib/edsign.ts 와 동일):
    token = b64url(payload_json) + "." + b64url(ed25519_sig)
서명 대상은 '.' 앞의 payload 파트 **ASCII 바이트**. 검증자는 수신한 바이트를
그대로 검증하므로 양측 JSON 직렬화가 달라도 무해하다(재직렬화하지 않는다).
"""
from __future__ import annotations

import base64
import hashlib
import json
import time

# ── RFC 8032 §6 레퍼런스 Ed25519 (public domain) ────────────────────────────
p = 2**255 - 19
_q = 2**252 + 27742317777372353535851937790883648493


def _sha512(s: bytes) -> bytes:
    return hashlib.sha512(s).digest()


def _modp_inv(x: int) -> int:
    return pow(x, p - 2, p)


_d = -121665 * _modp_inv(121666) % p
_modp_sqrt_m1 = pow(2, (p - 1) // 4, p)


def _sha512_modq(s: bytes) -> int:
    return int.from_bytes(_sha512(s), "little") % _q


def _point_add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C = 2 * P[3] * Q[3] * _d % p
    D = 2 * P[2] * Q[2] % p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % p, G * H % p, F * G % p, E * H % p)


def _point_mul(s: int, P):
    Q = (0, 1, 1, 0)  # 항등원
    while s > 0:
        if s & 1:
            Q = _point_add(Q, P)
        P = _point_add(P, P)
        s >>= 1
    return Q


def _point_equal(P, Q) -> bool:
    if (P[0] * Q[2] - Q[0] * P[2]) % p != 0:
        return False
    if (P[1] * Q[2] - Q[1] * P[2]) % p != 0:
        return False
    return True


def _recover_x(y: int, sign: int):
    if y >= p:
        return None
    x2 = (y * y - 1) * _modp_inv(_d * y * y + 1) % p
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p != 0:
        x = x * _modp_sqrt_m1 % p
    if (x * x - x2) % p != 0:
        return None
    if (x & 1) != sign:
        x = p - x
    return x


_g_y = 4 * _modp_inv(5) % p
_g_x = _recover_x(_g_y, 0)
_G = (_g_x, _g_y, 1, _g_x * _g_y % p)


def _point_compress(P) -> bytes:
    zinv = _modp_inv(P[2])
    x = P[0] * zinv % p
    y = P[1] * zinv % p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _point_decompress(s: bytes):
    if len(s) != 32:
        raise ValueError("invalid point length")
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % p)


def _secret_expand(secret: bytes):
    if len(secret) != 32:
        raise ValueError("bad seed length (need 32 bytes)")
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return a, h[32:]


def secret_to_public(secret: bytes) -> bytes:
    a, _ = _secret_expand(secret)
    return _point_compress(_point_mul(a, _G))


def _sign(secret: bytes, msg: bytes) -> bytes:
    a, prefix = _secret_expand(secret)
    A = _point_compress(_point_mul(a, _G))
    r = _sha512_modq(prefix + msg)
    R = _point_mul(r, _G)
    Rs = _point_compress(R)
    h = _sha512_modq(Rs + A + msg)
    s = (r + h * a) % _q
    return Rs + int.to_bytes(s, 32, "little")


def _verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _point_decompress(public)
    if not A:
        return False
    Rs = signature[:32]
    R = _point_decompress(Rs)
    if not R:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _q:
        return False
    h = _sha512_modq(Rs + public + msg)
    sB = _point_mul(s, _G)
    hA = _point_mul(h, A)
    return _point_equal(sB, _point_add(R, hA))


# ── base64url ───────────────────────────────────────────────────────────────
def b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def load_seed(b64: str) -> bytes:
    """env 의 base64(std 또는 url) 32바이트 seed → bytes."""
    raw = b64u_decode(b64) if ("-" in b64 or "_" in b64) else base64.b64decode(b64)
    if len(raw) != 32:
        raise ValueError(f"seed must be 32 bytes (got {len(raw)})")
    return raw


def load_public(b64: str) -> bytes:
    raw = b64u_decode(b64) if ("-" in b64 or "_" in b64) else base64.b64decode(b64)
    if len(raw) != 32:
        raise ValueError(f"public key must be 32 bytes (got {len(raw)})")
    return raw


# ── 서명 어서션 ──────────────────────────────────────────────────────────────
def sign_assertion(seed: bytes, payload: dict) -> str:
    """payload 를 우리 키(seed)로 서명한 컴팩트 토큰. iat 없으면 자동 주입."""
    body = dict(payload)
    body.setdefault("iat", int(time.time()))
    part = b64u_encode(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _sign(seed, part.encode("ascii"))
    return part + "." + b64u_encode(sig)


def verify_assertion(public: bytes, token: str, *, now: float | None = None,
                     max_age: int | None = None) -> dict | None:
    """상대 공개키로 토큰 검증. 유효하면 payload dict, 아니면 None.

    exp(있으면) 만료 검사. max_age 주면 iat 기준 상한도 강제(exp 누락 방어).
    단일 소비·req 바인딩은 호출자(스토어) 몫 — 여기선 서명·시간만 본다.
    """
    if not isinstance(token, str) or token.count(".") != 1:
        return None
    part, sig_b64 = token.split(".", 1)
    try:
        sig = b64u_decode(sig_b64)
    except Exception:
        return None
    if not _verify(public, part.encode("ascii"), sig):
        return None
    try:
        payload = json.loads(b64u_decode(part))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    t = time.time() if now is None else now
    exp = payload.get("exp")
    if exp is not None and (not isinstance(exp, (int, float)) or t > exp):
        return None
    if max_age is not None:
        iat = payload.get("iat")
        if not isinstance(iat, (int, float)) or t > iat + max_age:
            return None
    return payload
