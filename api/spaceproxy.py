# -*- coding: utf-8 -*-
"""스페이스 통과로 — 데스크탑의 그래프 호출을 계정으로 넘긴다.

## 팀만이 아니라 개인 그래프도 (계정 모드, 2026-09-24)

처음에는 팀 그래프만 이 길로 갔다(`team` 필수). 데스크톱 계정 모드에서는 개인 그래프도
로컬 스토어가 아니라 계정에서 읽고 쓰므로 `team` 이 선택이 됐다. 없으면 `team` 파라미터를
**아예 싣지 않고**, 계정은 키의 주인(개인 그래프)으로 답한다. 빈 문자열을 실으면 안 된다:
계정의 `teamScope` 가 그것을 팀 id 로 읽으려 들 수 있다.
근거: [[decision:the-desktop-signs-in-and-shows-the-account-graph-live-and-local-mode-is-a-setting]]

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
([[concept:proxy-allowlist]] 가 적어 둔 합계 뷰 사고). 그래서 이 모듈은
**해석하지 않는다** — 경로를 그대로 얹고, 무엇이 허용되는지는 계정이 판정한다.

⚠ 그래도 **아무 데나 못 가게** 한다: 접두를 여기서 붙이고 `..` 를 거절한다. 안 하면
루프백에 닿는 무엇이든 이 사이드카를 임의 호스트로 가는 포워더로 쓸 수 있다.

## 자격

키는 **호출 바디로만** 온다(D36 의 동기화와 같은 규칙 — env·디스크 반입 없음).
사이드카는 키를 저장하지 않고 그 호출 동안만 들고 있는다.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse

from vinc_mcp import hubhttp

#: 계정 기본 주소. 웹 평면이 같은 오리진으로 쓰는 그 경로를 셸에서 절대주소로 쓴다.
DEFAULT_BASE = "https://vincs.io"

#: 계정의 그래프 평면 접두. 경로는 **여기서만** 붙는다.
PREFIX = "/api/graph"

#: 계정 프록시가 무거운 연산에 주는 한도(`account/src/services/mcpProxy.ts`
#: `HEAVY_TIMEOUT_MS` 45초)와 같게 둔다. 짧으면 계정이 답하기 전에 여기서 먼저 끊겨
#: 호출부는 계정이 성공한 쓰기를 실패로 본다.
_TIMEOUT = 45.0


def _safe_path(path: str) -> str | None:
    """`/view` 처럼 슬래시로 시작하는 **경로 부분만**. 아니면 None.

    스킴·호스트·`..` 를 막는다. 계정이 op 를 판정하므로 여기서 화이트리스트를 들
    이유는 없지만, **이 사이드카가 임의 호스트 포워더가 되는 것**은 막아야 한다.

    ⚠ **쿼리(`?`)와 프래그먼트(`#`)도 거절한다.** 경로에 섞여 들어오면 아래 조립이
    조용히 틀린다 — 둘 다 실측이다:

      `/view#frag`         → `…/view#frag?team=T` 가 되고 urllib 이 프래그먼트부터
                             잘라 내보내서 **`team` 이 통째로 사라진다.** 계정은 팀을
                             못 봤으니 개인 그래프로 답한다. 팀에 쓰려던 것이 조용히
                             개인 것이 되는, 이 표면 전체가 막으려는 그 고장이다.
      `/view?team=X`       → `?team=X&team=T` 로 **두 번** 실린다. 어느 쪽이 이기는지는
                             계정의 파서가 정하지 우리가 정하지 않는다.

    쿼리는 `params` 로 온다. 경로에 실을 자리가 애초에 없다.

    ⚠ **퍼센트 인코딩을 풀고 나서** `..` 를 본다. 리터럴만 보면 `%2e%2e` 가 지나가고,
    앞단 인그레스가 정규화하는 순간 `/api/graph` 접두 밖으로 나간다. 접두가 하나뿐인
    것이 이 모듈의 경계이므로, 그 경계를 문자열 검사 하나에 맡기지 않는다."""
    if not path or not path.startswith("/") or path.startswith("//"):
        return None
    # ⚠ `urlsplit(...).query`·`.fragment` 의 참거짓으로 보면 **빈 것을 놓친다** —
    # `/doc/put#` 은 fragment 가 `""` 라 거짓인데 조립하면 `#` 뒤가 잘려 `team` 이
    # 그대로 사라진다(검사가 이걸 잡았다). 문자 자체를 본다.
    if "?" in path or "#" in path:
        return None
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc:
        return None
    decoded = urllib.parse.unquote(path)
    if ".." in decoded or "\\" in decoded:
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
    safe = _safe_path(path)
    if safe is None:
        return 400, {"error": f"bad path: {path!r}"}

    q = {k: str(v) for k, v in (params or {}).items() if v not in (None, "")}
    # 호출부가 params 로 `team` 을 몰래 실어도 여기서 정하는 값 하나만 남긴다.
    q.pop("team", None)
    if team:
        q["team"] = team
    url = (base or DEFAULT_BASE).rstrip("/") + PREFIX + safe
    if q:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(q)

    data = None
    if method.upper() == "POST":
        data = json.dumps(body if body is not None else {}).encode("utf-8")
    started = time.monotonic()

    def log_failure(status: int, failure: str) -> None:
        # 검색 실패의 경계만 남긴다. 키·쿼리·본문·팀 식별자는 싣지 않는다.
        if safe == "/search":
            logging.getLogger(__name__).warning(
                "account proxy failed status=%d path=%s duration_ms=%.1f failure=%s",
                status, PREFIX + safe, (time.monotonic() - started) * 1000, failure)

    try:
        resp = hubhttp.request(url, method=method.upper(), token=token, data=data,
                               timeout=_TIMEOUT)
        return resp.status, json.loads(resp.text or "{}")
    except hubhttp.HubHTTPError as e:
        if e.status is None:                # 네트워크·타임아웃·DNS
            log_failure(502, "transport")
            return 502, {"error": "account_unreachable", "detail": f"{type(e.cause).__name__}"}
        log_failure(e.status, "upstream")
        raw = e.body
        # 계정이 아니라 **앞문**이 답한 경우가 여기다(빈 본문 401 + WWW-Authenticate).
        # 그 둘을 뭉개면 "키가 나쁘다"와 "인그레스에 막혔다"가 같은 말이 된다 —
        # 2026-08-31 에 정확히 그 구분이 없어서 하루를 썼다.
        #
        # ⚠ **빈 본문을 먼저 가른다.** `json.loads(raw or "{}")` 로 쓰면 빈 본문이
        # `{}` 로 **성공해서** 계정의 거절과 구분이 안 된다(검사가 이걸 잡았다).
        if not raw.strip():
            return e.status, {"error": "account_unreachable", "detail": "empty body"}
        try:
            return e.status, json.loads(raw)
        except ValueError:
            return e.status, {"error": "account_unreachable", "detail": raw[:200]}
    except Exception as e:                 # 깨진 응답 본문
        log_failure(502, "response" if isinstance(e, ValueError) else "transport")
        return 502, {"error": "account_unreachable", "detail": f"{type(e).__name__}"}
