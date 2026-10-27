"""`vinc events tail`: 이벤트 피드(vinc_events)를 한 줄에 하나씩 낸다.

MCP Events 를 못 받는 호스트(터미널이 있는 클라우드 에이전트 등)가 쓰는 대체 문이다.
커서를 넘겨 이어 읽고, `--cursor-file` 이 있으면 매번 그 파일에 적어 재시작해도 이어진다.
같은 변경은 같은 eventId 로 다시 나올 수 있으므로 받는 쪽이 id 로 거른다.

`--forward URL` 은 이벤트 하나를 그 주소에 POST 하나로 보낸다(Grok Bot 루틴의 웹훅 트리거처럼
Vinc 의 서명 웹훅 확인 절차를 못 받는 곳). 토큰은 인자가 아니라 `VINC_FORWARD_TOKEN` 에서 읽는다.
한 쪽의 이벤트를 다 보낸 뒤에만 커서를 넘기므로, 실패하면 그 쪽은 다음에 처음부터 다시 간다.
근거: [[vinc.plan.mcp-events-and-webhooks]]
"""
from __future__ import annotations

import ipaddress
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MIN_INTERVAL = 5.0
FORWARD_TIMEOUT_S = 10.0
FORWARD_TOKEN_ENV = "VINC_FORWARD_TOKEN"
_LOOPBACK_NAMES = ("localhost",)


def read_cursor(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read().strip()
    except FileNotFoundError:
        return None
    return text or None


def write_cursor(path: str, cursor: str) -> None:
    """임시 파일에 쓰고 바꿔 끼운다. 반쯤 쓴 커서는 남기지 않는다."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(cursor + "\n")
    os.replace(tmp, path)


def check_forward_url(url: str) -> str | None:
    """보낼 주소가 https 인가. 평문 http 는 이 기기 안(루프백)만 받는다. 문제가 있으면 그 문장."""
    parts = urllib.parse.urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if not host:
        return "--forward must be an absolute https URL"
    if parts.username or parts.password:
        return "--forward must not carry credentials; put the token in " + FORWARD_TOKEN_ENV
    if parts.scheme == "https":
        return None
    if parts.scheme == "http":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host in _LOOPBACK_NAMES
        if loopback:
            return None
    return "--forward must be an https URL (plain http only to this machine)"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트는 따라가지 않는다. 토큰이 다른 호스트로 넘어가지 않게 한다."""

    def redirect_request(self, *_args, **_kwargs):
        return None


def post_event(url: str, event: dict, token: str | None) -> int:
    """이벤트 하나를 JSON 본문으로 POST 하고 상태 코드를 돌려준다. 연결 실패는 0 이다."""
    body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "vinc-events-forward",
               "Idempotency-Key": str(event.get("eventId") or "")}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=FORWARD_TIMEOUT_S) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError):
        return 0


def _deliver(events: list, url: str, post) -> str | None:
    """한 쪽을 차례로 보낸다. 실패한 첫 이벤트에서 멈추고 그 사유를 돌려준다(본문은 싣지 않는다)."""
    token = os.environ.get(FORWARD_TOKEN_ENV) or None
    for event in events:
        status = post(url, event, token)
        if not 200 <= status < 300:
            return f"forward of {event.get('eventId') or 'an event'} failed with status {status or 'no response'}"
    return None


def _ok(result) -> bool:
    return isinstance(result, dict) and result.get("executed") is True and not result.get("error")


def tail(args, call, emit, *, sleep=time.sleep, out=None, post=None) -> int:
    """`call()` 은 `(결과, 종료 코드)` 를 돌려주는 도구 호출 한 번이다(로컬 또는 계정)."""
    out = out or sys.stdout
    post = post or post_event
    forward = getattr(args, "forward", None)
    if forward:
        problem = check_forward_url(forward)
        if problem:
            sys.stderr.write(json.dumps({"kind": "events", "executed": False,
                                         "error": "invalid_forward_url", "note": problem}) + "\n")
            return 2
    if args.since is None and args.cursor_file:
        args.since = read_cursor(args.cursor_file)
    if args.once:
        result, code = call()
        if result is None:
            return code or 2
        if forward and code is None and _ok(result):
            failed = _deliver(result.get("events") or [], forward, post)
            if failed:
                emit({**result, "executed": False, "error": "forward_failed", "note": failed})
                return 1
            result = {**result, "forwarded": len(result.get("events") or [])}
        if code is None and args.cursor_file and _ok(result) and result.get("cursor"):
            write_cursor(args.cursor_file, result["cursor"])
        rendered = emit(result)
        return code if code is not None else rendered
    interval = max(MIN_INTERVAL, float(args.interval or 30))
    try:
        while True:
            result, code = call()
            if result is None:
                return code or 2
            if code is not None or not _ok(result):
                sys.stderr.write(json.dumps(result, ensure_ascii=False) + "\n")
                return code if code is not None else 1
            if forward:
                failed = _deliver(result.get("events") or [], forward, post)
                if failed:
                    sys.stderr.write(json.dumps({"kind": "events", "executed": False,
                                                 "error": "forward_failed", "note": failed}) + "\n")
                    return 1
            for event in result.get("events") or []:
                out.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            out.flush()
            cursor = result.get("cursor")
            if cursor:
                args.since = cursor
                if args.cursor_file:
                    write_cursor(args.cursor_file, cursor)
            if not result.get("has_more"):
                sleep(interval)
    except KeyboardInterrupt:
        return 0
