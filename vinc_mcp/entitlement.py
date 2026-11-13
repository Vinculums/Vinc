# -*- coding: utf-8 -*-
"""회원 API 키 검증 클라이언트(B-B1) — MCP → 계정 `/internal/mcp/verify-key`.

발주자 페인("키 복붙")의 핵심 해소: 회원이 공홈에서 발급한 `vinc_sk_*` 키를
claude.ai 커넥터/데스크탑 원격 타깃의 Bearer 로 쓰면, MCP 가 이 루프백으로
소유자를 확인해 `owner=user:{public_id}` 로 강제 스탬핑한다(멀티테넌트 격리).

B2 준수: MCP 는 계정 DB·시크릿에 접근하지 않는다. 우리 Ed25519 개인키로 짧은 어서션
(iss=mcp·aud=account·경로별 typ·본문 해시 bh·호출마다 새 jti·120s)을 서명해 신원을 증명하고, 계정이
**우리 공개키만** 핀해 검증한다(공개키 공유 ≠ 시크릿 공유). 응답(owner·plan)은
sha256(key) 기준 60s 캐시 — 커넥션마다 계정을 때리지 않는다.

미설정(VINC_MCP_ED25519_SEED 없음)이면 이 경로는 비활성 → verify_key 는 None.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from typing import Any

from . import edverify, hubhttp

_POS_TTL = 60.0     # 유효 키 캐시(초)
_NEG_TTL = 5.0      # 무효 키 캐시(짧게 — 방금 만든 키가 오래 막히지 않게)
_TIMEOUT = 4.0

_cache: dict[str, tuple[float, dict | None]] = {}
_lock = threading.Lock()

_log = logging.getLogger("vinc.mcp.entitlement")


class VerificationUnavailable(RuntimeError):
    """자격을 확인하지 못했다. 접근은 닫고, 무효 자격으로 캐시하지 않는다."""

    def __init__(self):
        super().__init__("Account verification is temporarily unavailable.")


def _verification_failure(exc: Exception) -> None:
    # 계정 계약의 명시적 거절은 404뿐이다. 서비스 어서션 401도 키 무효가 아니다.
    status = exc.status if isinstance(exc, hubhttp.HubHTTPError) else None
    if status == 404:
        return
    kind = type(exc.cause if isinstance(exc, hubhttp.HubHTTPError) and status is None else exc).__name__
    _log.warning("account verification unavailable: kind=%s status=%s", kind, status)
    raise VerificationUnavailable() from None

#: 와이어에서 허용되는 등급 값. **Curious(`free`)와 Genius(`genius`) 둘이다**. 화면 이름과
#: 와이어 값이 다른 것은 의도다: 와이어 값은 계정 DB·토큰·캐시에 남아 오래 살고, 화면
#: 이름은 마케팅이 바꿀 수 있다. Enterprise 는 아직 와이어 값이 없다.
#: 근거: [[vinc.canon.account-policy §8]]
PLANS = ("free", "genius")

#: 유료로 취급하는 등급(한도 분기의 단일 기준). 유료는 하루 호출을 세지 않는다.
PAID_PLANS = ("genius",)

#: 폐기된 등급 → 지금 등급. 계정 응답(verify-key·어서션·등급 조회)에 폐기된 값이 오면
#: **조용히** 접는다. 아래 normalize_plan 의 경고는 "배포 순서가 어긋났다"는
#: 신호인데 이건 의도된 폐기라 경고가 거짓 경보가 된다.
#: - `pro` → free: free 가 주제 무제한이 되어 잃는 것이 없다.
#:   제거 조건: 운영 계정 DB 에 Vinc-homepage `020_retire_pro_plan` 이 적용돼 `users.plan` 이 `free` 하나뿐인 것을 확인하면 뺀다.
#: - `team` → genius: Team 의 후속이 Genius 다. 계정이 팀 어서션에 아직 `team` 을
#:   싣고(웹 프록시), OAuth 토큰·등급 캐시에도 남아 있다. 지금 활성인 팀은 전부 체험
#:   (`TRIAL_UNTIL`) 중이라 Genius 로 읽는 것이 맞다.
#:   제거 조건: 계정이 팀 소유자 등급(`team_plan`)을 싣게 된 뒤 `team` 을 내는 곳이 없음을 확인하면 뺀다.
RETIRED_PLANS = {"pro": "free", "team": "genius"}

#: 회원 키 접두 — vinc_sk(전권) · vinc_ro(읽기 전용, W18). 접두는 사람 눈 구분용이고
#: 권한의 정본은 계정 verify-key 응답의 `scope` 다. 근거: [[decision:scoped-key-tier]]
KEY_PREFIXES = ("vinc_sk_", "vinc_ro_")

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
    if isinstance(raw, str) and raw in RETIRED_PLANS:
        return RETIRED_PLANS[raw]
    if raw is not None:
        _log.warning("unknown plan %r from %s — treating as 'free' "
                     "(hub older than account?)", raw, source)
    return "free"


def _team_plan_field(body: dict, *, source: str) -> dict:
    """팀 멤버십 응답의 `team_plan`(팀 소유자 등급) → 결과에 붙일 필드.

    계정이 안 실으면 **빈 dict** 다. 값을 지어내지 않고 부재를 그대로 넘겨, 허브가
    부재를 "팀 소유자 등급을 아직 안 싣는 계정"으로 읽게 한다(`hub_auth._team_plan`).
    멤버 개인 등급(`plan`)과 섞으면 안 된다: Curious 멤버가 Genius 팀에서 일하면 Genius 를
    받아야 한다."""
    raw = body.get("team_plan")
    if raw is None:
        return {}
    return {"team_plan": normalize_plan(raw, source=source)}


def normalize_scope(raw: object, key: str) -> str:
    """verify-key 응답의 scope → 'full' | 'read'. 모르면 **읽기 전용**이다(fail-closed).

    'full' 정확일치만 쓰기를 연다. 유일한 예외는 scope 필드가 아예 없는(구 계정 배포)
    vinc_sk 접두 키 — 그 키들은 늘 전권으로 검증돼 왔고, 부재를 full 로 읽는 쪽은
    계정이 아니라 여기 하나다. vinc_ro 키는 구 계정이 접두 불일치로 아예 404 를 내니
    이 분기에 닿지 않지만, 닿아도 read 로 떨어진다. 미지 값은 read 로 좁히되
    조용히 하지 않는다(normalize_plan 과 같은 이유 — 배포 순서 어긋남의 조기 신호).
    근거: [[decision:scoped-key-tier]]
    """
    if raw == "full":
        return "full"
    if raw is None:
        return "full" if key.startswith("vinc_sk_") else "read"
    if raw != "read":
        _log.warning("unknown key scope %r from verify-key — treating as 'read' "
                     "(hub older than account?)", raw)
    return "read"


def _account_url() -> str:
    return (os.environ.get("VINC_ACCOUNT_INTERNAL_URL") or "http://127.0.0.1:8080").rstrip("/")


def _seed() -> bytes | None:
    # 셀프호스트 허브는 계정과 말하지 않는다: 시드가 없는 것과 같게 두면 키 검증·멤버십·
    # 커뮤니티·실행 알림이 한자리에서 같이 꺼진다.
    from . import selfhost
    if selfhost.enabled():
        return None
    raw = os.environ.get("VINC_MCP_ED25519_SEED")
    if not raw:
        return None
    try:
        return edverify.load_seed(raw)
    except Exception:
        return None


#: 이 모듈이 부르는 계정 내부 경로마다 어서션에 싣는 `typ`. 경로 하나에 값 하나다: 두 경로가
#: 한 값을 나눠 쓰면 한 경로에 서명한 어서션이 다른 경로에서도 통한다. 계정이 경로마다 typ 를
#: 대조하면 바뀐 값은 거절되므로, 값을 바꾸려면 계정이 새 값을 먼저 받아야 한다.
#: 커뮤니티 경로(`community.py`)와 실행 알림(`run_notify.py`)은 자기 typ 를 따로 싣는다.
VERIFY_KEY_TYP = "verify-key"
TEAM_MEMBERSHIP_TYP = "team-membership"
SPACES_TYP = "spaces"
PLAN_TYP = "plan"


def _body_hash(body: bytes) -> str:
    """계정 `bodyHash` 와 같은 값: 보내는 바이트의 sha256 을 패딩 없는 base64url 로."""
    return base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode("ascii")


def _sign_assertion(seed: bytes, typ: str, body: bytes) -> str:
    """경로의 typ 와 그 요청 본문의 bh, 호출마다 새 jti 를 실은 어서션.

    iss·aud·iat·exp 는 계정 `gate()` 가 대조하는 그대로 둔다. `body` 는 실제로 보낼 바이트여야 한다:
    다른 바이트에 서명하면 bh 를 대조하는 계정이 거절한다.
    jti 는 소문자 16진 32자(uuid4 의 hex)다. 계정의 run-waiting 검증기는 이 모양만 받고 커뮤니티
    검증기도 받는다. 허브의 다른 서명(`community.py`·`run_notify.py`)과 같은 모양이다."""
    now = int(time.time())
    return edverify.sign_assertion(seed, {"iss": "mcp", "aud": "account", "typ": typ,
                                          "iat": now, "exp": now + 120,
                                          "jti": uuid.uuid4().hex, "bh": _body_hash(body)})


def _post(seed: bytes, path: str, typ: str, payload: dict) -> Any:
    """계정 내부 경로에 JSON 을 POST 하고 JSON 답을 돌려준다. 서명한 바이트를 그대로 보낸다.

    직렬화는 `hubhttp.post_json` 과 바이트까지 같고 헤더도 같다(Content-Type·Accept 는 JSON,
    Authorization 없음). 직렬화를 여기서 한 번만 하고 그 바이트를 해시와 전송에 함께 쓴다.
    다시 인코딩하면 bh 가 보낸 본문과 어긋날 수 있다."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hubhttp.request(_account_url() + path, data=raw, timeout=_TIMEOUT,
                           headers={"X-Vinc-Assertion": _sign_assertion(seed, typ, raw)}).json()


def enabled() -> bool:
    return _seed() is not None


def verify_key(raw: str, team: str | None = None) -> dict | None:
    """`vinc_sk_*`/`vinc_ro_*` 원문 키 → {owner, plan, scope[, role]} 또는
    None(무효·미설정). 확인 장애는 VerificationUnavailable이며 캐시하지 않는다.

    `scope`(W18)는 항상 실린다 — 캐시도 이 dict 통째라 스코프를 잃지 않는다
    (잃으면 TTL 동안 무음 승격이 된다). 부재·미지 값의 해석은 normalize_scope.

    `team` 없음: owner 는 `user:{public_id}`. 기존과 바이트 동일하다.
    `team` 있음(publicId): **스페이스 스코프 질문**이다 — 이 키의 사람이 그 팀의 활성
    멤버인가. 키가 어디에 묶였는지(`key_space`)도 본다: 그 팀에 묶인 키와 개인 키(`"personal"`, 또는 필드 없음)는 받고 다른 팀에 묶인 키는 거절한다.
    개인 키가 멤버십으로 팀에 닿는 것은 채택된 동작이다(아래 스페이스 인자 결정). 팀 키는 따로 발급되고
    자기 팀에만 묶인다. 계정이 엄격 모드(`KEY_GRAPH_SCOPE_STRICT`, 기본 꺼짐)를 켜면 개인 키의 팀 질문을
    나쁜 키와 같이 거절하므로 여기까지 오지 않는다. 켜는 것은 그 결정을 뒤집는 일이라 소유자가 먼저 새로 정해야 한다.
    맞으면 owner 는 계정이 서명해 준 `team:{uuid}` 이고 `role` 이 함께 온다.
    아니면 None 이고, **모르는 팀·비멤버·정지된 팀이 나쁜 키와 구분되지 않는다**
    (계정이 같은 404 를 낸다 — 구분되면 팀의 존재가 샌다).

    ⚠ **캐시 키에 team 이 들어간다.** 안 넣으면 개인 스코프 답이 팀 질문을 TTL 동안
    만족시킨다 — 같은 키로 두 스페이스를 묻는 것이 정상 흐름이라 이건 이론이 아니다. 다른 공간에 대한
    거절도 원래 키의 정상 조회를 막으면 안 된다.
    근거: [[decision:the-space-is-an-argument-and-the-default-is-personal]]
    """
    if not raw or not raw.startswith(KEY_PREFIXES):
        return None
    seed = _seed()
    if seed is None:
        return None

    ckey = "key-space-v2|" + hashlib.sha256(raw.encode("utf-8")).hexdigest() + "|" + (team or "")
    now = time.time()
    with _lock:
        hit = _cache.get(ckey)
        if hit and hit[0] > now:
            return hit[1]

    result: dict | None = None
    try:
        body = _post(seed, "/internal/mcp/verify-key", VERIFY_KEY_TYP,
                     {"key": raw, **({"team": team} if team else {})})
        sub = body.get("sub")
        if isinstance(sub, str) and sub:
            plan = normalize_plan(body.get("plan"), source="verify-key")
            scope = normalize_scope(body.get("scope"), raw)
            if team:
                # 다른 팀에 묶인 키는 멤버여도 이 팀에 들이지 않는다. 개인 키와 필드 없는 답은 받는다
                # (개인 키의 팀 접근은 계정의 멤버십 검사와 엄격 모드가 가른다).
                if (body.get("key_space") or "personal") not in ("personal", team):
                    raise ValueError("key bound to another team")
                # 팀 질문에는 계정이 서명한 graph_owner 와 role 이 함께 와야 한다.
                # 하나라도 없으면 **개인으로 떨어뜨리지 않고 거절한다** — 팀 쓰기가
                # 조용히 개인 그래프에 쌓이는 것이 여기서 가장 나쁜 고장이다.
                go, role = body.get("graph_owner"), body.get("role")
                if (isinstance(go, str) and go.startswith("team:")
                        and isinstance(role, str) and role):
                    result = {"owner": go, "plan": plan, "scope": scope,
                              "role": role, "member": f"user:{sub}",
                              **_team_plan_field(body, source="verify-key")}
            else:
                if (body.get("key_space") or "personal") != "personal":
                    raise ValueError("team credential cannot access personal graph")
                result = {"owner": f"user:{sub}", "plan": plan, "scope": scope}
        if result is None:
            raise ValueError("invalid verification response")
    except Exception as exc:
        _verification_failure(exc)

    with _lock:
        _cache[ckey] = (now + (_POS_TTL if result else _NEG_TTL), result)
    return result


def verify_member(sub: str, team: str) -> dict | None:
    """`user:{publicId}` 의 publicId + 팀 publicId → {owner, role} 또는 None.

    `verify_key(raw, team)` 의 **OAuth 판**이다. 두 라우트가 따로인 이유는 자격증명의
    모양이 다르기 때문이다 — 키 경로는 원문 키로 사람을 확인하고 같은 왕복에서
    멤버십까지 답하는데, OAuth 토큰에는 키가 없고 허브가 아는 것은 승인 때 스탬핑된
    publicId 뿐이다. 그래서 **신원 확인 없이 멤버십만** 묻는다(`lookup_plan` 과 같은
    갈래다 — 어서션 게이트가 호출자를 이미 증명했다).

    ⚠ **fail-closed 다.** 네트워크·타임아웃·형식 불량은 VerificationUnavailable로
    접근을 닫되 재시도를 허용한다. 명시적 비멤버 거절만 None이다. 그쪽은 자격
    조회고 이쪽은 **인가**이기 때문이다. 흔들릴 때 열어 주면 안 되는 쪽이다.

    ⚠ 계정이 `graph_owner` 를 안 주거나 `team:` 이 아니면 거절한다. 개인 owner 로
    물러서면 팀 쓰기가 조용히 개인 그래프에 쌓인다 — 키 경로가 이미 막아 둔 것과
    같은 고장이고, 이 경로가 그것을 뒤로 열어 주면 안 된다.

    캐시는 `verify_key` 와 **같은 표를 쓰되 접두로 갈라 둔다**. 안 가르면 키 해시와
    publicId 가 같은 이름공간에 섞인다.
    근거: [[decision:the-space-is-an-argument-and-the-default-is-personal]]
    """
    if not sub or not team:
        return None
    seed = _seed()
    if seed is None:
        return None

    ckey = "member|" + sub + "|" + team
    now = time.time()
    with _lock:
        hit = _cache.get(ckey)
        if hit and hit[0] > now:
            return hit[1]

    result: dict | None = None
    try:
        body = _post(seed, "/internal/mcp/team-membership", TEAM_MEMBERSHIP_TYP,
                     {"sub": sub, "team": team})
        go, role = body.get("graph_owner"), body.get("role")
        if (isinstance(go, str) and go.startswith("team:")
                and isinstance(role, str) and role):
            result = {"owner": go, "role": role, "member": f"user:{sub}",
                      **_team_plan_field(body, source="team-membership")}
        if result is None:
            raise ValueError("invalid membership response")
    except Exception as exc:
        _verification_failure(exc)

    with _lock:
        _cache[ckey] = (now + (_POS_TTL if result else _NEG_TTL), result)
    return result


#: 스페이스 목록 캐시. `_cache` 와 **따로 둔다** — 저기는 인가 판정(dict|None)을
#: 담는 표고 여기는 목록이라, 한 표에 섞으면 타입이 거짓말을 한다.
_spaces_cache: dict[str, tuple[float, list | None]] = {}
_communities_cache: dict[str, tuple[float, list | None]] = {}


def list_spaces(sub: str) -> list[dict] | None:
    """`user:{publicId}` 의 publicId → 그 사람이 활성 멤버인 팀 목록. 실패면 None.

    ⚠ **이것은 인가가 아니라 발견이다.** 여기서 팀 하나를 빠뜨리든 없는 팀을
    지어내든 권한은 안 변한다 — 실제 판정은 그 id 를 `space` 로 실었을 때
    `resolve_auth` → `verify_key`/`verify_member` 가 다시 한다. 그래서
    `verify_member` 와 달리 fail-closed 를 지킬 대상이 아니고, 실패를 **None 으로
    구분해서** 돌려주는 것이 요점이다: 빈 목록으로 뭉개면 호출자가 "팀이 없다"로
    읽고 사람에게 그렇게 말한다.

    계정 응답을 **그대로 통과시키지 않는다.** 모양이 맞는 행만 추려 담는다 —
    이 값은 도구 응답으로 나가고, 거기로 임의의 계정 JSON 이 흘러가면 안 된다.
    """
    if not sub:
        return None
    seed = _seed()
    if seed is None:
        return None

    ckey = "spaces|" + sub
    now = time.time()
    with _lock:
        hit = _spaces_cache.get(ckey)
        if hit and hit[0] > now:
            return hit[1]

    rows: list[dict] | None = None
    try:
        body = _post(seed, "/internal/mcp/spaces", SPACES_TYP, {"sub": sub})
        raw = body.get("spaces")
        if isinstance(raw, list):
            rows = []
            for r in raw:
                if not isinstance(r, dict):
                    continue
                rid, name, role = r.get("id"), r.get("name"), r.get("role")
                if not (isinstance(rid, str) and rid and isinstance(role, str) and role):
                    continue
                if rid.startswith("community:"):
                    continue
                row = {"id": rid, "name": name if isinstance(name, str) else "", "role": role}
                # 팀 등급은 계정이 실을 때만 싣는다. 없으면 허브가 기본으로 읽는다(`_team_plan`).
                if r.get("plan") is not None:
                    row["plan"] = normalize_plan(r["plan"], source="spaces")
                rows.append(row)
    except Exception:
        rows = None              # 네트워크·HTTP·형식 → "못 물었다"(빈 목록 아님)

    with _lock:
        _spaces_cache[ckey] = (now + (_POS_TTL if rows is not None else _NEG_TTL), rows)
    return rows


def list_communities(sub: str) -> list[dict] | None:
    """가입 목록은 발견 정보다. 읽기 권한은 요청마다 공개 상태로 다시 확인한다."""
    from . import community
    seed = _seed()
    if not sub or seed is None:
        return None
    now = time.time()
    with _lock:
        hit = _communities_cache.get(sub)
        if hit and hit[0] > now:
            return hit[1]
    rows = None
    try:
        body = _post(seed, "/internal/mcp/spaces", SPACES_TYP, {"sub": sub})
        raw = body.get("communities")
        if isinstance(raw, list):
            rows = [{"id": r["id"].lower(), "name": r.get("name") if isinstance(r.get("name"), str) else "",
                     "role": r["role"]} for r in raw if isinstance(r, dict)
                    and community.is_space(r.get("id")) and r.get("role") in ("reader", "operator")]
    except Exception:
        rows = None
    with _lock:
        _communities_cache[sub] = (now + (_POS_TTL if rows is not None else _NEG_TTL), rows)
    return rows


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
        body = _post(seed, "/internal/mcp/plan", PLAN_TYP, {"sub": sub})
    except Exception:
        return None
    if body.get("sub") != sub:
        return None
    raw = body.get("plan")
    if not isinstance(raw, str) or not raw:
        return None
    return normalize_plan(raw, source="plan-lookup")
