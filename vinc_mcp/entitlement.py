# -*- coding: utf-8 -*-
"""회원 API 키 검증 클라이언트(B-B1) — MCP → 계정 `/internal/mcp/verify-key`.

발주자 페인("키 복붙")의 핵심 해소: 회원이 공홈에서 발급한 `vinc_sk_*` 키를
claude.ai 커넥터/데스크탑 원격 타깃의 Bearer 로 쓰면, MCP 가 이 루프백으로
소유자를 확인해 `owner=user:{public_id}` 로 강제 스탬핑한다(멀티테넌트 격리).

B2 준수: MCP 는 계정 DB·시크릿에 접근하지 않는다. 우리 Ed25519 개인키로
짧은 어서션(iss=mcp·aud=account·120s)을 서명해 신원을 증명하고, 계정이
**우리 공개키만** 핀해 검증한다(공개키 공유 ≠ 시크릿 공유). 응답(owner·plan)은
sha256(key) 기준 60s 캐시 — 커넥션마다 계정을 때리지 않는다.

미설정(VINC_MCP_ED25519_SEED 없음)이면 이 경로는 비활성 → verify_key 는 None.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request

from . import edverify

_POS_TTL = 60.0     # 유효 키 캐시(초)
_NEG_TTL = 5.0      # 무효 키 캐시(짧게 — 방금 만든 키가 오래 막히지 않게)
_TIMEOUT = 4.0

_cache: dict[str, tuple[float, dict | None]] = {}
_lock = threading.Lock()


def _account_url() -> str:
    return (os.environ.get("VINC_ACCOUNT_INTERNAL_URL") or "http://127.0.0.1:8080").rstrip("/")


def _seed() -> bytes | None:
    raw = os.environ.get("VINC_MCP_ED25519_SEED")
    if not raw:
        return None
    try:
        return edverify.load_seed(raw)
    except Exception:
        return None


def _sign_assertion(seed: bytes) -> str:
    now = int(time.time())
    return edverify.sign_assertion(seed, {"iss": "mcp", "aud": "account",
                                          "iat": now, "exp": now + 120})


def enabled() -> bool:
    return _seed() is not None


def verify_key(raw: str) -> dict | None:
    """`vinc_sk_*` 원문 키 → {owner, plan} 또는 None(무효·미설정·오류).

    owner 는 `user:{public_id}` 형태 — tools 계층이 그대로 스탬핑한다.
    """
    if not raw or not raw.startswith("vinc_sk_"):
        return None
    seed = _seed()
    if seed is None:
        return None

    ckey = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    now = time.time()
    with _lock:
        hit = _cache.get(ckey)
        if hit and hit[0] > now:
            return hit[1]

    result: dict | None = None
    try:
        req = urllib.request.Request(
            _account_url() + "/internal/mcp/verify-key",
            data=json.dumps({"key": raw}).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "X-Vinc-Assertion": _sign_assertion(seed)},
            method="POST")
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        sub = body.get("sub")
        if isinstance(sub, str) and sub:
            plan = body.get("plan") if body.get("plan") in ("free", "pro") else "free"
            result = {"owner": f"user:{sub}", "plan": plan}
    except urllib.error.HTTPError:
        result = None            # 404(invalid_key)·401·503 → 무효 취급
    except Exception:
        result = None            # 네트워크·타임아웃 → 무효(닫힘 실패, 안전측)

    with _lock:
        _cache[ckey] = (now + (_POS_TTL if result else _NEG_TTL), result)
    return result
