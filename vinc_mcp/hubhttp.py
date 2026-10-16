# -*- coding: utf-8 -*-
"""허브·계정으로 가는 HTTP 의 한 자리 — Bearer(또는 호출부가 준 헤더) + JSON 을 urllib 로 보낸다.

하는 것:

  - `urlopen(request, *, timeout)` — 리다이렉트를 따르지 않는 opener **하나**. 이 모듈의
    모든 요청이 여기를 지나고, 요청을 직접 짓는 호출부도 이것을 쓴다. 3xx 는 따라가지 않고
    `urllib.error.HTTPError` 로 올라온다.
  - `request(url, ...)` — 요청 하나를 보내고 `Response(status, headers, text)` 를 준다.
    2xx 가 아닌 답(3xx 포함)과 전송 실패는 전부 `HubHTTPError` **하나**로 올린다.
    `status` 가 None 이면 HTTP 답 자체가 없었다(DNS·연결·시간 초과)는 뜻이다.
  - `post_json`·`get_json` — 위에 JSON 인코딩·디코딩만 얹는다. 2xx 본문이 JSON 이 아니면
    `ValueError` 다(전송 실패와 갈라 두어야 호출부가 "답이 깨졌다"를 따로 말할 수 있다).

하면 안 되는 것:

  - **리다이렉트를 따라가면 안 된다.** 따라가면 Authorization 헤더가 그 주소로 그대로
    가고, POST 가 본문 없는 GET 으로 바뀌어 쓰기가 성공처럼 보인다.
    근거: [[record:hub-http-clients-refuse-redirects-2026-10-16]]
  - 오류를 포장하지 않는다. `{error, isError}`·`SyncError`·`RemoteFailure`·JSON-RPC 오류
    같은 모양은 호출부마다 다르고 그 모양이 각자의 계약이다. 여기서는 상태·본문·주소만 싣는다.
  - 재시도하지 않는다. 쓰기를 다시 보내도 되는지는 호출부만 안다.
  - 표준 라이브러리 밖의 의존을 넣지 않는다(F5, PyInstaller 동결).
"""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None          # None 이면 urllib 가 3xx 를 HTTPError 로 올린다


_OPENER = urllib.request.build_opener(_NoRedirect)


def urlopen(request, *, timeout: float):
    """모든 요청이 지나는 한 자리. 테스트는 이 이름을 바꿔 끼운다."""
    return _OPENER.open(request, timeout=timeout)


class HubHTTPError(urllib.error.URLError):
    """2xx 가 아닌 답, 또는 답이 없었던 전송 실패.

    `status` None = HTTP 답이 없었다. 그때 원래 예외는 `cause`(와 `__cause__`)에 있다.
    `body` 는 오류 본문(utf-8, 깨진 바이트는 치환)이다. 메시지에는 본문을 싣지 않는다.
    `URLError` 의 자식이라 `except URLError`·`except OSError` 로 받던 호출부는 그대로 받는다."""

    def __init__(self, url: str, status: int | None, *, body: str = "",
                 headers: Mapping[str, str] | None = None, cause: BaseException | None = None):
        super().__init__(f"HTTP {status}" if status is not None else type(cause).__name__)
        self.url, self.status, self.body, self.headers, self.cause = url, status, body, headers, cause

    def __str__(self) -> str:
        return f"{self.reason} from {self.url}"


@dataclass(frozen=True)
class Response:
    status: int
    headers: Any
    text: str

    def json(self) -> Any:
        return json.loads(self.text)


def request(url: str, *, method: str = "POST", token: str | None = None,
            data: bytes | None = None, timeout: float = 60.0,
            accept: str | None = "application/json",
            headers: Mapping[str, str] | None = None) -> Response:
    """요청 하나. 토큰이 비면 Authorization 을 싣지 않는다(빈 헤더는 그 자체로 401 사유다).
    본문이 있으면 Content-Type 은 JSON 이고, `headers` 가 같은 이름을 주면 그것이 이긴다."""
    h: dict[str, str] = {}
    if data is not None:
        h["Content-Type"] = "application/json"
    if accept:
        h["Accept"] = accept
    if token:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = getattr(resp, "status", None) or 200
            got = getattr(resp, "headers", None)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — 오류 본문을 못 읽어도 상태는 말한다
            body = ""
        raise HubHTTPError(url, e.code, body=body, headers=e.headers) from None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        # TimeoutError·ConnectionError 는 OSError 다. 읽다 끊긴 것(IncompleteRead)도 여기.
        raise HubHTTPError(url, None, cause=e) from e
    return Response(status, got, raw.decode("utf-8"))


def post_json(url: str, *, token: str | None, payload: Any, timeout: float = 60.0,
              accept: str | None = "application/json",
              headers: Mapping[str, str] | None = None) -> Any:
    """JSON 을 POST 하고 JSON 답을 돌려준다."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return request(url, token=token, data=body, timeout=timeout, accept=accept,
                   headers=headers).json()


def get_json(url: str, *, token: str | None, timeout: float = 60.0,
             accept: str | None = "application/json",
             headers: Mapping[str, str] | None = None) -> Any:
    """GET 하고 JSON 답을 돌려준다."""
    return request(url, method="GET", token=token, timeout=timeout, accept=accept,
                   headers=headers).json()
