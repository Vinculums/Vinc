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
import logging
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

_log = logging.getLogger("vinc.mcp.entitlement")

#: 와이어에서 허용되는 등급 값. DB 열거형(`users.plan`)은 free|pro 뿐이고 `team` 은
#: 어서션·verify-key 응답에만 실리는 와이어 전용 값이다([[vinc.canon.account-policy]] §2.1).
PLANS = ("free", "pro", "team")

#: 유료로 취급하는 등급(한도·업셀 문구 분기의 단일 기준).
PAID_PLANS = ("pro", "team")

_PLAN_TTL_DEFAULT = 300.0


def plan_ttl() -> float:
    """OAuth 토큰에 동결된 등급을 계정에 되묻는 주기(초)."""
    try:
        return float(os.environ.get("VINC_PLAN_TTL_S") or _PLAN_TTL_DEFAULT)
    except ValueError:
        return _PLAN_TTL_DEFAULT


def normalize_plan(raw: object, *, source: str) -> str:
    """미지 등급을 free 로 강등하되 **조용히 하지 않는다**.

    계정이 새 등급 문자열을 먼저 내보내고 허브가 아직 그 값을 모르면, 조용한
    강등은 사용자에게 "결제했는데 무료로 취급된다"로 나타난다. 배포 순서가
    어긋난 것이 유일한 원인인데 증상은 결제 버그처럼 보인다 — 로그가 그걸
    가르는 유일한 조기 신호다.
    """
    if isinstance(raw, str) and raw in PLANS:
        return raw
    if raw is not None:
        _log.warning("unknown plan %r from %s — treating as 'free' "
                     "(hub older than account?)", raw, source)
    return "free"


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


def verify_key(raw: str, team: str | None = None) -> dict | None:
    """`vinc_sk_*` 원문 키 → {owner, plan[, role]} 또는 None(무효·미설정·오류).

    `team` 없음: owner 는 `user:{public_id}`. 기존과 바이트 동일하다.
    `team` 있음(publicId): **스페이스 스코프 질문**이다 — 이 키의 사람이 그 팀의 활성
    멤버인가. 맞으면 owner 는 계정이 서명해 준 `team:{uuid}` 이고 `role` 이 함께 온다.
    아니면 None 이고, **모르는 팀·비멤버·정지된 팀이 나쁜 키와 구분되지 않는다**
    (계정이 같은 404 를 낸다 — 구분되면 팀의 존재가 샌다).

    ⚠ **캐시 키에 team 이 들어간다.** 안 넣으면 개인 스코프 답이 팀 질문을 TTL 동안
    만족시킨다 — 같은 키로 두 스페이스를 묻는 것이 정상 흐름이라 이건 이론이 아니다.
    근거: [[decision:the-endpoint-carries-the-space-and-the-credential-carries-the-right]]
    """
    if not raw or not raw.startswith("vinc_sk_"):
        return None
    seed = _seed()
    if seed is None:
        return None

    ckey = hashlib.sha256(raw.encode("utf-8")).hexdigest() + "|" + (team or "")
    now = time.time()
    with _lock:
        hit = _cache.get(ckey)
        if hit and hit[0] > now:
            return hit[1]

    result: dict | None = None
    try:
        req = urllib.request.Request(
            _account_url() + "/internal/mcp/verify-key",
            data=json.dumps({"key": raw, **({"team": team} if team else {})})
            .encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "X-Vinc-Assertion": _sign_assertion(seed)},
            method="POST")
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        sub = body.get("sub")
        if isinstance(sub, str) and sub:
            plan = normalize_plan(body.get("plan"), source="verify-key")
            if team:
                # 팀 질문에는 계정이 서명한 graph_owner 와 role 이 함께 와야 한다.
                # 하나라도 없으면 **개인으로 떨어뜨리지 않고 거절한다** — 팀 쓰기가
                # 조용히 개인 그래프에 쌓이는 것이 여기서 가장 나쁜 고장이다.
                go, role = body.get("graph_owner"), body.get("role")
                if (isinstance(go, str) and go.startswith("team:")
                        and isinstance(role, str) and role):
                    result = {"owner": go, "plan": plan, "role": role,
                              "member": f"user:{sub}"}
            else:
                result = {"owner": f"user:{sub}", "plan": plan}
    except urllib.error.HTTPError:
        result = None            # 404(invalid_key)·401·503 → 무효 취급
    except Exception:
        result = None            # 네트워크·타임아웃 → 무효(닫힘 실패, 안전측)

    with _lock:
        _cache[ckey] = (now + (_POS_TTL if result else _NEG_TTL), result)
    return result


def lookup_plan(sub: str) -> str | None:
    """publicId → **지금의** 등급. 계정 `/internal/mcp/plan`(B2 동일 신뢰 방향).

    실패(미설정·네트워크·HTTP 오류)면 **None** 이다. 호출자는 마지막으로 알던
    값으로 폴백해야 한다 — 여기서 `"free"` 를 돌려주면 계정이 잠깐 흔들릴 때마다
    유료 사용자가 통째로 무료로 떨어진다. 키 검증(fail-closed)과 달리 등급
    조회는 **fail-open to last-known** 이 옳다. 인증이 아니라 자격이기 때문이다.
    """
    if not sub:
        return None
    seed = _seed()
    if seed is None:
        return None
    try:
        req = urllib.request.Request(
            _account_url() + "/internal/mcp/plan",
            data=json.dumps({"sub": sub}).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "X-Vinc-Assertion": _sign_assertion(seed)},
            method="POST")
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if body.get("sub") != sub:
        return None
    raw = body.get("plan")
    if not isinstance(raw, str) or not raw:
        return None
    return normalize_plan(raw, source="plan-lookup")
