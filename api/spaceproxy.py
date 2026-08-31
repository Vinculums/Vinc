# -*- coding: utf-8 -*-
"""팀 스페이스 통과로 — 데스크탑의 그래프 호출을 계정으로 넘긴다.

## 왜 사이드카가 하나 (앱이 직접 안 하고)

팀 그래프는 계정이 `proxy-team` 어서션을 자기 키로 서명해야 닿는다. 데스크탑은 그
서명을 못 만들고 로컬 스토어에는 팀 그래프가 없으므로, 계정을 거치는 것이 유일한
길이다. 그런데 **앱이 직접 부를 수는 없다**. 두 이유가 각각 독립으로 막는다:

  1. 셸 webview 의 오리진은 `tauri://localhost` 라 `https://vincs.io` 로 가는 fetch 는
     교차출처인데, 계정에는 CORS 가 한 줄도 없다(실측 2026-08-31).
  2. "앱은 계정 API 를 직접 부르지 않는다"가 이미 이 제품의 규칙이다
     (`app/src/account-connect.ts` 머리주석). 키 수령조차 사이드카 루프백을 거친다.

사이드카는 브라우저가 아니라서 1번이 없고, 2번의 "계정을 부르는 쪽"이 원래 여기다.

## 왜 경로를 그대로 넘기나 (op 표를 새로 안 만들고)

계정의 `GET_OPS`/`POST_OPS` 가 이미 REST 경로 → 허브 op 매핑을 들고 있다. 여기서
op 를 다시 해석하면 **네 번째 목록**이 생긴다. 사이드카 · 허브 `_PROXY_OPS` · 계정
두 표가 어긋나면 그 기능만 조용히 죽는다는 것은 이 리포의 실제 사고 이력이다
([[vinc-web-parity-proxy-allowlist]] 가 적어 둔 합계 뷰 사고). 그래서 이 모듈은
**해석하지 않는다** — 경로를 그대로 얹고, 무엇이 허용되는지는 계정이 판정한다.

⚠ 그래도 **아무 데나 못 가게** 한다: 접두를 여기서 붙이고 `..` 를 거절한다. 안 하면
루프백에 닿는 무엇이든 이 사이드카를 임의 호스트로 가는 포워더로 쓸 수 있다.

## 자격

키는 **호출 바디로만** 온다(D36 의 동기화와 같은 규칙 — env·디스크 반입 없음).
사이드카는 키를 저장하지 않고 그 호출 동안만 들고 있는다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

#: 계정 기본 주소. 웹 평면이 같은 오리진으로 쓰는 그 경로를 셸에서 절대주소로 쓴다.
DEFAULT_BASE = "https://vincs.io"

#: 계정의 그래프 평면 접두. 경로는 **여기서만** 붙는다.
PREFIX = "/api/graph"

_TIMEOUT = 30.0


def _safe_path(path: str) -> str | None:
    """`/view` 처럼 슬래시로 시작하는 상대 경로만. 아니면 None.

    `..` 와 스킴·호스트를 막는다. 계정이 op 를 판정하므로 여기서 화이트리스트를 들
    이유는 없지만, **이 사이드카가 임의 호스트 포워더가 되는 것**은 막아야 한다."""
    if not path or not path.startswith("/") or path.startswith("//"):
        return None
    if ".." in path or "\\" in path:
        return None
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc:
        return None
    return path


def call(*, token: str, team: str, method: str, path: str,
         params: dict | None = None, body: object = None,
         base: str | None = None) -> tuple[int, dict]:
    """계정의 그래프 평면으로 한 번 넘긴다. `(status, json)`.

    실패도 **계정이 준 상태코드와 몸통 그대로** 돌려준다. 여기서 다시 포장하면
    호출부가 "무엇이 거절했는가"(계정인가 허브인가 이 통과로인가)를 잃는다.
    """
    if not token:
        return 400, {"error": "missing field: token (member key)"}
    if not team:
        return 400, {"error": "missing field: team"}
    safe = _safe_path(path)
    if safe is None:
        return 400, {"error": f"bad path: {path!r}"}

    q = {k: str(v) for k, v in (params or {}).items() if v not in (None, "")}
    q["team"] = team
    url = (base or DEFAULT_BASE).rstrip("/") + PREFIX + safe
    if q:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(q)

    data = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if method.upper() == "POST":
        data = json.dumps(body if body is not None else {}).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        # 계정이 아니라 **앞문**이 답한 경우가 여기다(빈 본문 401 + WWW-Authenticate).
        # 그 둘을 뭉개면 "키가 나쁘다"와 "인그레스에 막혔다"가 같은 말이 된다 —
        # 2026-08-31 에 정확히 그 구분이 없어서 하루를 썼다.
        #
        # ⚠ **빈 본문을 먼저 가른다.** `json.loads(raw or "{}")` 로 쓰면 빈 본문이
        # `{}` 로 **성공해서** 계정의 거절과 구분이 안 된다(검사가 이걸 잡았다).
        if not raw.strip():
            return e.code, {"error": "account_unreachable", "detail": "empty body"}
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"error": "account_unreachable", "detail": raw[:200]}
    except Exception as e:                 # 네트워크·타임아웃·DNS
        return 502, {"error": "account_unreachable", "detail": f"{type(e).__name__}"}
