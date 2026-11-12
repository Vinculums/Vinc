# -*- coding: utf-8 -*-
"""셀프호스트 허브의 좌석: 계정에 한 번 연결하고, 하루 한 번 서명된 답을 받아 그 수만큼 사람을 들인다.

계획 `vinc.plan.self-hosted-hub-web-2026-11-11` S8. 계정 쪽 계약은 그래프의
`record:self-hosted-seat-check-contract-2026-11-11` 에 있다(Vinc-homepage VIN-966).

- 연결(`vinc selfhost link`): 기기 코드 흐름. 계정이 한 번만 주는 인스턴스 키(`vsh_…`)를 볼륨에
  0600 파일로 둔다. 키는 어디에도 출력하지 않는다.
- 확인: 허브가 하루 한 번(기동 때 한 번 더) `POST /api/selfhost/check` 를 부르고, 받은 토큰을 내장
  공개키로 검증해(typ·iss·aud·sub·exp) 마지막 좋은 답을 볼륨에 남긴다.
- 자격: 좋은 답이 있고 그 `exp` 가 안 지났으면 그 답의 `users`·`features` 다. 연락이 끊겨도
  `exp`(발급 뒤 30일)까지는 그 답으로 돈다. 그 뒤, 또는 계정이 연결을 끊었다고(`revoked`) 답하면
  무료다: 사람 한 명, 유료 기능 없음. 읽기와 내보내기는 어느 상태에서도 그대로다.
- 사람 한 명으로 쓰는 허브는 연결도 확인도 하지 않는다(밖으로 나가는 호출이 없다).
"""
from __future__ import annotations

import json
import os
import random
import threading
import time

from . import edverify, hubhttp, selfhost

#: 계정의 좌석 서명 공개키(base64, 32바이트). 계정 `SELF_HOSTED_ED25519_SEED` 에서 나온 값을 여기
#: 굽는다. 비어 있으면 `VINC_SEAT_PUBLIC_KEY` 가 있어야 답을 믿는다 — 없으면 늘 무료다.
BUILTIN_PUBLIC_KEY = ""
PUBLIC_KEY_ENV = "VINC_SEAT_PUBLIC_KEY"
ACCOUNT_ENV = "VINC_ACCOUNT_URL"
DEFAULT_ACCOUNT = "https://vincs.io"

INSTANCE_FILE = "selfhost-instance.json"
SEATS_FILE = "selfhost-seats.json"

#: 확인 주기와 실패 뒤 다시 묻는 간격. 계정은 키마다 시간당 60번까지 받는다.
CHECK_EVERY_S = 24 * 3600
JITTER_S = 1800
RETRY_FIRST_S, RETRY_MAX_S = 300, 6 * 3600
#: 마지막 좋은 확인이 이보다 오래면 "유예"로 보인다(답은 아직 유효하지만 연락이 끊겼다).
GRACE_AFTER_S = 2 * CHECK_EVERY_S
FREE_USERS = 1


def _dir() -> str:
    from .selfhost_web import state_dir
    return state_dir()


def _read(name: str) -> dict:
    try:
        with open(os.path.join(_dir(), name), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(name: str, data: dict) -> None:
    path = os.path.join(_dir(), name)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _remove(name: str) -> None:
    try:
        os.remove(os.path.join(_dir(), name))
    except OSError:
        pass


def account_url() -> str:
    return (os.environ.get(ACCOUNT_ENV) or DEFAULT_ACCOUNT).rstrip("/")


def public_key() -> bytes | None:
    raw = (os.environ.get(PUBLIC_KEY_ENV) or BUILTIN_PUBLIC_KEY).strip()
    if not raw:
        return None
    try:
        return edverify.load_public(raw)
    except Exception:   # noqa: BLE001 — 모양이 틀린 키는 없는 키와 같다(늘 무료)
        return None


def instance() -> dict | None:
    """연결돼 있으면 {instance_id, instance_key, name, linked_at}, 아니면 None."""
    rec = _read(INSTANCE_FILE)
    if rec.get("instance_id") and str(rec.get("instance_key") or "").startswith("vsh_"):
        return rec
    return None


# ── 토큰 ────────────────────────────────────────────────────────────────────

def verify(token: str, instance_id: str, now: float | None = None) -> dict | None:
    """서명·종류·발급자·대상·이 허브·만료를 본다. 맞으면 payload, 아니면 None."""
    key = public_key()
    if key is None or not token:
        return None
    payload = edverify.verify_assertion(key, token, now=now)
    if not payload or payload.get("exp") is None:
        return None
    if (payload.get("typ"), payload.get("iss"), payload.get("aud")) != ("selfhost-seats", "account", "selfhost"):
        return None
    if payload.get("sub") != instance_id:
        return None
    users = payload.get("users")
    if not isinstance(users, int) or users < FREE_USERS:
        return None
    return payload


def entitlement(now: float | None = None) -> dict:
    """지금의 자격. state 는 free·paid·grace 중 하나다."""
    now = time.time() if now is None else now
    inst = instance()
    rec = _read(SEATS_FILE)
    out = {"linked": inst is not None, "state": "free", "users_allowed": FREE_USERS,
           "seats": 0, "features": [], "last_check": rec.get("checked_at"),
           "last_attempt": rec.get("attempted_at"), "last_error": rec.get("error"),
           "expires_at": None, "revoked": bool(rec.get("revoked"))}
    if inst is None or rec.get("revoked"):
        return out
    payload = verify(str(rec.get("token") or ""), inst["instance_id"], now=now)
    if payload is None:
        return out
    checked = float(rec.get("checked_at") or 0)
    out.update(state="grace" if now - checked > GRACE_AFTER_S else "paid",
               users_allowed=int(payload["users"]), seats=int(payload.get("seats") or 0),
               features=[f for f in payload.get("features") or [] if isinstance(f, str)],
               expires_at=payload["exp"])
    return out


# ── 계정과 말하기 ───────────────────────────────────────────────────────────

def _post(url: str, body: dict | None = None, *, bearer: str | None = None,
          timeout: float = 20.0) -> tuple[int, dict]:
    """계정에 POST 하고 (상태, JSON 본문)을 준다. 4xx·5xx 도 상태와 본문으로 돌려준다.

    리다이렉트는 따르지 않는다. 따르면 인스턴스 키가 Location 의 호스트로 가고 POST 가 본문 없는
    GET 으로 바뀐다. 3xx 와 답이 없었던 실패는 `HubHTTPError` 로 올라가 호출부에서 계정에 닿지
    않은 것(`unreachable`)과 같게 다뤄진다. 근거: [[record:hub-http-clients-refuse-redirects-2026-10-16]]"""
    data = json.dumps(body or {}).encode("utf-8")
    try:
        resp = hubhttp.request(url, token=bearer, data=data, timeout=timeout,
                               headers={"User-Agent": "vinc-selfhost-hub"})
        return resp.status, json.loads(resp.text or "{}")
    except hubhttp.HubHTTPError as e:
        if e.status is None or 300 <= e.status < 400:
            raise
        try:
            return e.status, json.loads(e.body or "{}")
        except ValueError:
            return e.status, {}


def check_once(now: float | None = None) -> str:
    """한 번 묻는다. 결과 범주: ok·revoked·rejected·unreachable·bad_token·not_linked."""
    inst = instance()
    if inst is None:
        return "not_linked"
    now = time.time() if now is None else now
    rec = _read(SEATS_FILE)
    rec["attempted_at"] = now
    try:
        code, body = _post(account_url() + "/api/selfhost/check", bearer=inst["instance_key"])
    except Exception as exc:   # noqa: BLE001 — 끊긴 연결은 유예로 버틴다
        rec["error"] = f"unreachable: {type(exc).__name__}"
        _write(SEATS_FILE, rec)
        return "unreachable"
    if code == 401 and ((body.get("error") or {}).get("reason") == "revoked"):
        _write(SEATS_FILE, {"revoked": True, "attempted_at": now, "error": "revoked"})
        return "revoked"
    if code != 200:
        rec["error"] = f"http_{code}"
        _write(SEATS_FILE, rec)
        return "rejected"
    token = str(body.get("token") or "")
    if verify(token, inst["instance_id"], now=now) is None:
        # 서명이 틀린 답은 버린다. 마지막 좋은 답은 그대로 둔다.
        rec["error"] = "bad_token"
        _write(SEATS_FILE, rec)
        return "bad_token"
    _write(SEATS_FILE, {"token": token, "checked_at": now, "attempted_at": now, "error": None})
    return "ok"


class Checker(threading.Thread):
    """하루 한 번 묻는 데몬 스레드. 실패하면 5분에서 6시간까지 두 배씩 늘려 다시 묻는다."""

    def __init__(self, *, clock=time.time, sleep=None):
        super().__init__(name="selfhost-seat-check", daemon=True)
        self._clock = clock
        self._halt = threading.Event()
        self._sleep = sleep or (lambda s: self._halt.wait(s))
        self.outcomes: list[str] = []

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        retry = RETRY_FIRST_S
        while not self._halt.is_set():
            try:
                outcome = check_once(self._clock())
            except Exception:   # noqa: BLE001 — 스레드가 죽으면 자격이 영영 안 바뀐다
                outcome = "error"
            self.outcomes.append(outcome)
            if outcome in ("ok", "revoked", "not_linked"):
                retry = RETRY_FIRST_S
                wait = CHECK_EVERY_S + random.uniform(-JITTER_S, JITTER_S)
            else:
                wait, retry = retry, min(retry * 2, RETRY_MAX_S)
            if outcome in ("revoked", "not_linked"):
                return
            self._sleep(wait)


def start_checker() -> Checker | None:
    """셀프호스트이고 연결돼 있을 때만 띄운다. 한 사람 허브는 아무 데도 묻지 않는다."""
    if not selfhost.enabled() or instance() is None:
        return None
    ch = Checker()
    ch.start()
    return ch


# ── 연결 ────────────────────────────────────────────────────────────────────

def link(name: str | None = None, *, say=print, sleep=time.sleep) -> int:
    """기기 코드 흐름. 승인되면 인스턴스를 저장하고 0, 거절·만료면 1."""
    base = account_url()
    code, start = _post(base + "/api/selfhost/link/start", {"name": name} if name else {})
    if code != 200 or not start.get("device_code"):
        say(f"could not start linking ({code}): {(start.get('error') or {}).get('message') or start}")
        return 1
    say(f"Open {start.get('verification_uri_complete') or start.get('verification_uri')} "
        f"and approve the code {start.get('user_code')}.")
    interval = max(1, int(start.get("interval") or 5))
    deadline = time.monotonic() + int(start.get("expires_in") or 600)
    while time.monotonic() < deadline:
        sleep(interval)
        code, poll = _post(base + "/api/selfhost/link/poll", {"device_code": start["device_code"]})
        status = poll.get("status")
        if status == "slow_down":
            interval += 5
            continue
        if status == "pending":
            continue
        if status == "approved" and poll.get("instance_id") and \
                str(poll.get("instance_key") or "").startswith("vsh_"):
            _write(INSTANCE_FILE, {"instance_id": poll["instance_id"],
                                   "instance_key": poll["instance_key"], "name": name,
                                   "linked_at": time.time()})
            _remove(SEATS_FILE)
            say(f"Linked as {poll['instance_id']}.")
            outcome = check_once()
            say(f"First seat check: {outcome}.")
            return 0
        say(f"linking {status or 'failed'}.")
        return 1
    say("linking expired.")
    return 1


def unlink(*, say=print) -> int:
    _remove(INSTANCE_FILE)
    _remove(SEATS_FILE)
    say("Unlinked. This hub is back to one person.")
    return 0
