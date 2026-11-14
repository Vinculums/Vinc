# -*- coding: utf-8 -*-
"""Streamable HTTP 전송 — 원격 프로파일(D36)용 루프백 MCP 서버.

바인딩은 127.0.0.1 고정 기본(F4). ⚠ **허브(Azure)에서는 이 기본이 아니다** —
컨테이너 엔트리포인트가 `--host 0.0.0.0 --port 8790` 으로 띄우고 Container Apps
인그레스가 그대로 공개한다(`packaging/docker-entrypoint.sh`). 예전엔 Caddy 가 앞단에서
경로를 골라 막았지만 **가비아 VM 과 함께 사라졌다** — 지금 앞단은 없다. 엔드포인트:

  POST   /mcp                            JSON-RPC 요청(단건) → JSON 응답
  DELETE /mcp                            세션 종료(선택 — 보내는 호스트만)
  GET  /.well-known/oauth-protected-resource     RFC 9728
  GET  /.well-known/oauth-authorization-server   RFC 8414
  GET  /.well-known/openai-apps-challenge        ChatGPT 디렉터리 도메인 검증
  POST /register    동적 클라이언트 등록
  GET  /authorize   인가(관리 키 + PKCE) → 302 code
  POST /token       코드/리프레시 → bearer

인증 2모드(둘 중 하나만 통과하면 됨):
  - 정적 bearer(--auth-token | env VINC_MCP_TOKEN) — 파일럿·내부용.
  - OAuth 게이트(--oauth) — 원격 커넥터 등록용. MCP 인가 스펙(RFC 9728/8414/7591
    + PKCE)만 구현하므로 특정 벤더 전용이 아니다: claude.ai 커넥터와 ChatGPT
    커스텀 커넥터(개발자 모드)가 같은 요건을 쓴다.
둘 다 미설정이면 기동을 거부한다(무인증 공개 사고 방지).

SSE 스트림은 제공하지 않는다(GET /mcp → 405) — 우리 도구는 전부 단발
요청/응답이라 스트리밍 이득이 없고, 스펙상 405 는 허용된 응답이다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from store import httpbody
from store.envflag import truthy

from . import account_assertion, burst, edverify, entitlement, hub_auth, hub_quota, mcp_post, modern, proxy_ops, spaces, toolcall, tools, viewurl

from .bridge import Bridge
# 베어러 해석은 `hub_auth` 에 있다. 이 모듈과 테스트가 이 이름으로 부르므로 여기서 같은 객체를
# 다시 내보낸다. `hub_auth` 는 이 모듈을 import 하지 않는다(순환 방지).
from .hub_auth import _team_plan  # noqa: F401
# 호출 한도의 계산과 상수는 `hub_quota` 에 있다. 이 모듈과 테스트가 이 이름으로 부르므로 여기서
# 같은 객체를 다시 내보낸다. `hub_quota` 는 이 모듈을 import 하지 않는다(순환 방지).
from .hub_quota import (  # noqa: F401
    _DAY_S, _LEGACY_TEAM_PLAN, _PLANS_URL, _WARN_RATIO, _WINDOW, _burst_denial, _current_plan,
    _reset_phrase, _utc_iso, _with_quota_meta)
# `/mcp` POST 의 JSON-RPC 파이프라인은 `mcp_post` 에 있다. 이 모듈과 테스트가 이 이름으로 부르므로
# 여기서 같은 객체를 다시 내보낸다. `mcp_post` 는 이 모듈을 import 하지 않는다(순환 방지).
from .mcp_post import _ROLE_READS, _events_auth  # noqa: F401
from .oauth import CONNECT_TTL, OAuthGate
# 프록시 op 표와 팀 역할 등급, 커뮤니티 반영기 규칙은 `proxy_ops` 에 있다. 이 모듈과 테스트가 이 이름으로
# 부르므로 여기서 같은 객체를 다시 내보낸다. `proxy_ops` 는 이 모듈을 import 하지 않는다(순환 방지).
from .proxy_ops import (  # noqa: F401
    _ADMIN_OPS, _CAPPED_PROXY_OPS, _COMMUNITY_AGENT_DOC_RE, _COMMUNITY_AGENT_NODE_RE,
    _COMMUNITY_OBJECT_UUID, _COMMUNITY_PACK_DOC_RE, _COMMUNITY_PACK_NODE_RE, _COMMUNITY_PAGE_NODE_RE,
    _COMMUNITY_PROJECT_OPS, _COMMUNITY_PROJECTOR_SUB, _COMMUNITY_READ_OPS, _COMMUNITY_SOCIAL_DOC_RE,
    _COMMUNITY_SOCIAL_NODE_RE, _COMMUNITY_WIKI_RELS, _EDITOR_OPS, _JOB_ID_RE, _PROXY_OPS, _PURE_POST_OPS,
    _TEAM_REFUSED_OPS, _TEAM_ROLES, _community_page_node, _role_allows, team_role_tiers)
from .rpc import PROTOCOL_VERSIONS, Dispatcher
# 본문 상한은 `/v1`(rest_api)과 같은 값 하나다. `http_server.MAX_BODY` 로 읽는 호출자도 같은 객체를 본다.
from .hub_http import MAX_BODY


# 팀 그래프 소유자 형식(AP-1 §4.3). 계정이 서명해 보내지만 **형식은 우리가 다시 본다** —
# 서명이 진짜여도 `team:../../etc` 같은 값이 그대로 owner 가 되면 안 된다.
_TEAM_OWNER_RE = re.compile(
    r"team:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_COMMUNITY_OWNER_RE = re.compile(
    r"community:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


#: `/internal/quota` 한 번에 묻는 스페이스 수의 상한(개인 하나와 팀 몇 개면 충분하다).
_QUOTA_VIEW_MAX = 32
_QUOTA_OWNER_RE = re.compile(r"(user|team):[A-Za-z0-9_-]+")


#: 스페이스 주소. `/t/<uuid>/mcp` 는 팀 그래프, `/mcp` 는 개인.
#:
#: **URL 은 의도, 자격증명은 권한.** 주소가 어느 그래프인지 말하고, 키가 들어갈 수
#: 있는지와 어떤 역할인지를 증명한다. 그래서 팀 id 를 안다는 것만으로는 아무것도
#: 못 하고, 키 하나로 모든 스페이스를 쓸 수 있다(스페이스마다 키를 발급할 필요가 없다).
#: 근거: [[decision:the-space-is-an-argument-and-the-default-is-personal]]
#: 스페이스 id 의 모양은 `spaces.UUID` 한 벌이다. 주소(`/t/<uuid>/mcp`)와 인자(`space`)
#: **양쪽이 같은 것을 쓴다** — 두 벌로 두면 한쪽만 조여도 다른 쪽이 뚫린 채 남는다.
_SPACE_PATH_RE = re.compile(r"\A/t/(" + spaces.UUID + r")(/.*)?\Z")

#: `_auth_for` 의 세 번째 답. 붙여넣은 주소가 `?space=local`(데스크탑 로컬 모드의
#: 그래프)을 가리킨다 — 닿을 수 없는 팀(None)과는 거절 문구가 다르다.
#: 판정은 **동일성**(`is`)으로 한다. dict 인 것은 뒤의 게이트들이 `a.get(...)` 로
#: 읽는 자리와 타입을 맞추려는 것뿐이다 — 거기 닿기 전에 돌려보낸다.
_ON_DEVICE: dict = {"ok": False, "on_device": True}


def profile_query(query: str) -> tuple[str | None, bool]:
    """MCP 주소의 `?profile=` → (프로파일, 잘못됨). 없으면 (None, False) 이고 목록은 `all` 그대로다.

    값은 `tools.TOOL_PROFILES` 의 글자 그대로만 받는다. 모르는 값을 `all` 로 넘기면 조용히 전체 목록이
    나가므로 거절한다. 주소로 받는 이유는 claude.ai 커넥터가 주소만 받기 때문이다(헤더를 못 넣는다)."""
    vals = urllib.parse.parse_qs(query or "", keep_blank_values=True).get("profile")
    if not vals:
        return None, False
    if len(vals) != 1 or vals[0] not in tools.TOOL_PROFILES:
        return None, True
    return vals[0], False


def _with_profile(meta: dict, profile: str | None) -> dict:
    """RFC 9728 문서의 `resource` 에 클라이언트가 쓴 `?profile=` 을 되비춘다. 없으면 문서 그대로다."""
    if not profile:
        return meta
    return {**meta, "resource": meta["resource"] + "?profile=" + profile}


def split_space(path: str) -> tuple[str | None, str]:
    """`/t/<uuid>/mcp` → (uuid, "/mcp"). 스페이스가 없으면 (None, path).

    ⚠ **모양이 틀린 주소는 여기서 스페이스가 아니게 되고, 뒤에서 404 가 된다.**
    400 이 아니라 404 인 것이 요점이다 — 형식 오류와 "그런 팀 없음"이 구분되면
    팀 id 를 추측해 조직 구조를 훑을 수 있다."""
    m = _SPACE_PATH_RE.match(path or "")
    if not m:
        return None, path
    return m.group(1).lower(), (m.group(2) or "/")


# 커넥터 아이콘 래스터 — 정본: app/src-tauri/icons/128x128.png(앱 아이콘과 동일 파일,
# scripts/design/assets/logo 의 링 플레이트에서 `tauri icon` 이 뽑는다).
# base64 로 심는 이유는 SVG 와 같다: 사이드카/MCP 는 단일 바이너리라 외부 자산 경로가
# 없다. **이 리포에서 로고를 인라인해도 되는 유일한 자리다.**
# ⚠ 그래서 사본이 조용히 낡는 것을 주석이 아니라 테스트가 막는다 —
# tests/mcp/test_mcp_icons.py 의 EmbeddedIconMatchesCanon 이 이 둘을 정본과 바이트로 대조한다.
# 다시 뽑는 법: scripts/design 에서 npm run logo:build → python -X utf8 scripts/design/sync_out.py --apply
_FAVICON_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAAIP0lEQVR4nO3a63HbSBpG4bcjMCYCQxEYjGDACExFMGQEhCJYKgJSERiKQHQEgiIgHIGoDMAM9vT0aGtraqxxN0mgcTlVT8F/XJDQH26kjKZG3TQAI28agJE3DcDImwZg5E0DMPKmARh50wCMvGkARt40ACNvGoCRNw3AyJsGYORNAzDyxj4ACWwNRtnYBiDBGrmkDAlsDWpUkh7QYBSNZQAS2IUvkOCjGuwwikEYwwAsJW2RwKcGdyg14IY+AGvscE4FHjDIhjwA37DUZSolrTC4DIbYJRf/vVIDHAKDobWDvfRfI3srKDCYhjYAS7mz/5qtUGogGQylDAdcuwZz1Oh9BkMowSvsto0a3MBue53BEHpGrnar5K4Evc6g7xXYoovusENv6/sAZDigy2ao0cv6PAAJ7KU/Q5fVsEPQy/o8ADusEUMPKNC7+joAudzZH1NzVOpZBn0rwSvsNqYa3MBue5NB33rCAqG9YSN3th7lSuWuKhtJnxHaHrfoTQZ9qsAWod1hh48qcM4+VijVkwz6Uir3ypfAtxNyuSf2XymXO5s/wbcGMxzVgwz60gEZfDsh168v/nsZKoUNQY0Zoq8vA7CR9B+EZBfCLkhIucLfNu6xUeQZxF6u8EVYodR5LRX+FfMclSLOIOYSHJDKv0csdZlKSX/At6PcFahBlBnE3Dcs5d8bMjS4RAlqfIZvpdyVKMoMYm2BJ4Rkz7oalyzDASHdYo/oinUAUrmDncC3e2x0nTYKexhtMMNRkWUQY8/I5d8Lcl23StLv8K2SeyiMKoPY2ijsLDshw1HXLZW7vXyCb3fYIZoMYirDASGtUKqdlnIPqCHNUCOKDGIpwTMy+PYdC7TZHl/hW405GnSeQSztsIZvb8jQoM0S1PgM3x5QoPMMYmiBJ4Q0R6VuyuWuWiHdYo9OM+i6BK+wW9/usVG37bCGbw1uYLedZdB1T1jAtx/IEEM1vsC3PW7RWV0PQIEtfDshlzvwMZShUg9fDbscgAzPSOBbpwftJxXYwrcGc9RoPYOuOiCDb9+xQIzt8RW+1Zih9boagAJb+HZCKnfWxFiCo3p0KzBou1Tu7E/g2y32OKcMf8BuE9ga1HiE3Z7TAk/wrcEN7La1DNqulFsA3x5QILRc7juGXB9Xyb1eVgpvhzV8e8RSLWbQZqncO79vb8jQIKSl/D+7X6FUWAlqfIZvv6FBKxm02UbuLPRthVJhLeW/+O91sd97bNRSBm32ilR+vSFVWLncq+Y5zVAjpKP8rwJHuWeBVjJoqwwH+DZHpbCekeu8KrmfIaQFnuDbDY5qIYO2KrCFT29IFVaGAy5R21eBO+xw9QzaqpT/0/8LcoVlD+Aal+gBBUKq5P8nZOfszyuDtqrkfyDusVFYlfz397NekCusjfwffM/Zn1cGbVXJf0FusUdIB2S4RDVmCGkp/7eBF+RqIYO2qtTuANT4gkv0AxlCWmoagD+r5D8A99gorEr++/tZL8gV1kbTLeDPSk0Pgb/aI5ZqIYO2sgdwC5+Ocu/EIWU44BLNUCOkV6Ty6w47XD2DtsoQsiBzVAqrkv/Z9/dekCusBZ7g2w2OaiGDNjvK/0ORo9wBCSmX+zTwnGaoEdIrUvn1hlQtZdBmG/k/ENlWKBXWUv5P4e91sd97bNRSBm2Wyp0Vvh3lzsQGIS3lvxgrlAorwQGp/PsNDVrJoO1K+b8N2B5QILRc7sz6HR/1ArufGqHtsIZvj1iqxQzaLpU7uJ/g2y32OKcMS7ltAluDGqXc9pwWeIJvJ6RyP0trGXRRgS18a3ADu42xBK+wW9/usEOrGXRVjS/wbY9bxNgzcvn3Axlar8sBsL9wpbBbwR12iKkCW/h2Qi53QrRelwNgKxBy0BrMUSOGMtizP4Fvd9ihkwy6bo+v8K3GDDF0QAbfvmOBzophABIcFXYreECBLtthDd9OSOWuZp1lEEMLPCGkOSp1Uy536Q+py5/7fxnE0g5r+HaUuxU0aLMEB6Ty7wEFOs8glhJU6s+r4RMW8O0HcrU/sP9YTANgy3BASCuUaqel/L9beG+GGlFkEFsbhX1j2MAe3KOuWyo3pAl8u8MO0WQQY5X+/Uubf6qSe7i6Zs/I5d8LckWWQYylcpfJT/DtHhtdp43Crk4nZDgqsgxibYEnhDRDjUuW4YCQbrFHdMU8ALZSYX87cJQbggaXKIFd/FT+PWKpSDOIOXvga3yGb6Xcm8El+oal/HtDhgZRZhB7udyDV0grlDqvpdwAhDRHpYgz6EMbhT182eaoFFau8OG7x0aRZ9CXanyBbw3mqOFTBrv4CXz7gQzRZ9CXUrlF/ATfGtyi0q+Vy72BJPDthAxH9SCDPlVgi9Ds/3/AR62xQ2grlOpJBn1rj68I7Sh3b37BUa5U7pPHjdy/Q/uOBXqTQd9KcFTYreCanZDK3W56k0Efy+Ue0GJqjko9y6Cv7bBGDD2gQO/q8wAkqBT2anjJfiBDL+vzANjsgT+gy2ao0cv6PgC2Alt00R126G1DGABbJfca12YvyNXzDIZQgqPaezV8Q4YGvc5gKGWodP0hOCFXj+/7/9+QBsC2VPhXt7/aCqUGksHQ2mGNa/SAAoNpiANgKxX2p2Qf9YilBpbBUCt1uSF4xFIDzGDI2cv1Fud0hx0G2dAHwLaUW8BP8OmEAqUG3BgGwJag+Mu/DYJd+N1fGgy6sQzAewkK5HKfG7wPg130GpVGsvDvjW0A/l4CW4NRNvYBGH3TAIy8aQBG3jQAI28agJE3DcDImwZg5E0DMPKmARh50wCMvGkARt40ACNvGoCRNw3AyPsvA3OokHcwhDYAAAAASUVORK5CYII="
)

# 커넥터/브라우저 아이콘 — 정본: scripts/design/assets/logo/vinc-ring-favicon.svg
# (판 없는 링. 밝은 바탕엔 검정, 어두운 바탕엔 `prefers-color-scheme` 으로 흰색.
# 판은 2026-09-24 에 폐기됐다. 근거: [[vinc.design.logo]])
# 임베드 이유와 드리프트 방지는 위 _FAVICON_PNG 주석과 같다.
_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="-42 38 604 604"><style>@media (prefers-color-scheme: dark){path,circle{fill:#FFFFFF}}</style><path fill-rule="evenodd" fill="#000000" d="M327.9 156.0L419.1 285.0Q458.0 340.0 419.1 395.0L327.9 524.0Q260.0 620.0 192.1 524.0L100.9 395.0Q62.0 340.0 100.9 285.0L192.1 156.0Q260.0 60.0 327.9 156.0Z M298.5 235.7L350.2 308.8Q372.3 340.0 350.2 371.2L298.5 444.3Q260.0 498.8 221.5 444.3L169.8 371.2Q147.7 340.0 169.8 308.8L221.5 235.7Q260.0 181.2 298.5 235.7Z"></path><circle cx="260" cy="340" r="44" fill="#000000"></circle></svg>"""


class MCPHTTPApp:
    def __init__(self, bridge: Bridge, *, static_token: str | None = None,
                 oauth: OAuthGate | None = None, verify_key=None,
                 verify_member=None, verify_community=None, community_write=None, community_read=None,
                 store=None, quota_config: dict | None = None,
                 mcp_seed: bytes | None = None, account_public: bytes | None = None,
                 connect_url: str | None = None, free_max_domains: int | None = None):
        if not static_token and oauth is None and verify_key is None:
            raise ValueError("no auth configured: static token, OAuth, or verify_key")
        self.bridge = bridge
        self.static_token = static_token or None
        self.oauth = oauth
        # 회원 승인 흐름(B-B3): 우리 서명키 + 계정 공개키 핀 + 공홈 동의 URL.
        # 셋 다 있어야 활성 — 없으면 /authorize 는 관리 키 경로만 남는다.
        self.mcp_seed = mcp_seed
        self.account_public = account_public
        self.connect_url = (connect_url or "").rstrip("/") or None
        # verify_key(raw)->{owner,plan}|None — 회원 API 키(vinc_sk_*) 검증(B-B1).
        # None 이면 키 경로 비활성. 프로덕션은 cli 가 entitlement.verify_key 를 주입.
        self.verify_key = verify_key
        # verify_member(sub, team)->{owner,role}|None — OAuth 커넥터가 팀 주소에
        # 붙을 때의 멤버십 질문(#120). None 이면 **OAuth 팀 경로만** 꺼지고 키 경로와
        # 개인 OAuth 는 그대로다 — 계정이 그 라우트를 아직 안 배포한 동안에도 허브가
        # 정상으로 도는 형태이고, 꺼진 방향이 닫힌 쪽이다(팀 주소는 404).
        self.verify_member = verify_member
        self.verify_community = verify_community
        self.community_write = community_write
        self.community_read = community_read
        # 쿼터(B-D2): usage 카운터는 store 에 영속. quota_config 없으면 무제한
        # (기존 테스트·로컬 프로파일 무영향) — cli 가 env 로 명시 활성화한다.
        self.store = store if store is not None else (oauth.store if oauth else None)
        self.quota_config = quota_config
        # 분당 상한은 플랜과 무관하게 스페이스마다 같다. 0 이거나 없으면 끈다.
        per_minute = (quota_config or {}).get("burst_per_minute") or 0
        self.burst = burst.BurstLimiter(per_minute) if per_minute > 0 else None
        # 무료 등급 주제 상한(AP-1 §3.4). None = 기능 꺼짐. 등급→숫자 변환이 **여기**
        # 한 곳에서만 일어나고 사이드카는 숫자만 받는다(정책 무지 유지).
        self.free_max_domains = free_max_domains
        # Dispatcher 하나를 공유한다(세션별 dict·락·FIFO 축출은 걷어냈다 — 지키던
        # 상태를 읽는 프로덕션 코드가 0건이었고, 스펙이 요구하는 프로토콜 검증은
        # **무상태**다: `_check_protocol_version`). ⚠ 세션 저장소를 되살리려거든 먼저
        # 레플리카가 둘 이상이라는 것을 볼 것 — 프로세스 로컬이라 같은 세션 id 가
        # 레플리카를 넘나든다([[vinc.plan.mcp-second-pass]]).
        # 허브는 **이미 원격 그래프 자신**이라 `target` 을 내보내지도 받지도 않는다.
        self._dispatcher = Dispatcher(self.bridge, hub=True)
        # 웹훅 전달은 스위치가 켜진 프로세스에서만 만든다. 배경 스레드는 첫 구독에서 뜬다.
        # 근거: [[vinc.plan.mcp-events-and-webhooks]]
        from . import events
        if events.webhook_enabled():
            self._dispatcher.events = events.Runtime(self.bridge)

    # ── 호출 한도. 본체는 `hub_quota` 에 있고 여기서는 넘기기만 한다. rest_api 와 `/mcp` 가
    # 앱 메서드로 부르고, 테스트는 인스턴스에서 `quota_check` 를 갈아 끼운다.
    def quota_snapshot(self, owner: str, plan: str | None) -> dict | None:
        return hub_quota.quota_snapshot(self, owner, plan)

    def quota_view(self, owner: str, plan: str | None) -> dict | None:
        return hub_quota.quota_view(self, owner, plan)

    def quota_meta(self, spent: dict) -> dict | None:
        return hub_quota.quota_meta(self, spent)

    def quota_check(self, owner: str, plan: str | None) -> dict | None:
        return hub_quota.quota_check(self, owner, plan)

    def entitlements_for(self, plan: str | None) -> dict:
        """등급 → 사이드카가 받을 **숫자**. 정책 해석은 여기서 끝난다(AP-1 §3.4).

        유료 등급이거나 상한이 미설정이면 빈 dict = 무제한이다.
        """
        if self.free_max_domains is None or _current_plan(plan) in entitlement.PAID_PLANS:
            return {}
        return {"max_domains": self.free_max_domains}

    def quota_for(self, auth: dict, spent: dict | None = None):
        return hub_quota.quota_for(self, auth, spent)

    def call_context(self, auth: dict, spent: dict | None = None, *,
                     profile: str | None = None) -> toolcall.CallContext:
        """인증을 마친 호출 하나의 값 묶음. `/mcp` 는 메시지마다, REST v1 은 요청마다 이것을 만든다.

        한도는 `spent` 에 센 owner · plan 을 남긴다(답의 한도 표시가 읽는다). 자격(주제 상한)은 owner 가
        있는 인증에만 싣는다. 커뮤니티 쓰기 · 읽기 콜백은 이 인증에 묶어 넘기고, 앱에 콜백이 없으면
        None 이다. `profile` 은 `/mcp` 주소의 `?profile=` 값이다."""
        owner, plan = auth.get("owner"), auth.get("plan")
        return toolcall.CallContext(
            forced_owner=owner, plan=plan, quota=self.quota_for(auth, spent), quota_view=self.quota_view,
            entitlements=self.entitlements_for(plan) if owner else None,
            community_write=(lambda args: self.community_write(auth, args))
            if self.community_write is not None else None,
            community_read=(lambda args: self.community_read(auth, args))
            if self.community_read is not None else None,
            events_auth=_events_auth(auth), profile=profile)

    def dispatcher(self) -> Dispatcher:
        """공유 Dispatcher. 세션별 인스턴스가 없어졌으므로 인자도 없다(위 __init__ 참조)."""
        return self._dispatcher

    # ── 베어러 해석. 본체는 `hub_auth` 에 있고 여기서는 넘기기만 한다. 핸들러 · rest_api ·
    # 테스트가 앱 메서드로 부르고, 테스트는 인스턴스에서 `_fresh_plan` 을 갈아 끼운다.
    def resolve_auth(self, header: str | None, space: str | None = None,
                     *, presented_at: str = "/mcp") -> dict:
        return hub_auth.resolve_auth(self, header, space, presented_at=presented_at)

    def _resolve_self_hosted(self, header: str, space: str | None, presented_at: str,
                             miss: dict) -> dict:
        return hub_auth._resolve_self_hosted(self, header, space, presented_at, miss)

    def _resource_ok(self, rec: dict, presented_at: str) -> bool:
        return hub_auth._resource_ok(self, rec, presented_at)

    def _fresh_plan(self, owner: str | None, frozen: str | None) -> str | None:
        return hub_auth._fresh_plan(self, owner, frozen)

    def authorized(self, header: str | None) -> bool:
        return self.resolve_auth(header)["ok"]

    # ── 회원 승인 흐름(B-B3) ─────────────────────────────────────────────────
    def member_connect_enabled(self) -> bool:
        return bool(self.oauth and self.mcp_seed and self.account_public and self.connect_url)

    def begin_member_connect(self, params: dict) -> str:
        """/authorize(회원 경로) → connect_request 저장 + CRA 서명 → 공홈 동의 URL.

        CRA 는 **fragment** 로 넘긴다(#) — 쿼리스트링과 달리 서버 로그·Referer 에
        남지 않는다. 10분 TTL·단일 소비라 유출돼도 창이 좁다.
        """
        if not self.member_connect_enabled():
            raise PermissionError("member connect is not configured")
        req_id, meta = self.oauth.begin_connect(params)   # 파라미터·PKCE 검증 포함
        now = int(time.time())
        cra = edverify.sign_assertion(self.mcp_seed, {
            "typ": "cra", "iss": "mcp", "aud": "account", "req": req_id,
            "client_id": meta["client_id"], "client_name": meta["client_name"],
            "redirect_uri": meta["redirect_uri"], "iat": now, "exp": now + CONNECT_TTL})
        return f"{self.connect_url}#req={urllib.parse.quote(req_id)}&cra={cra}"

    def complete_member_connect(self, params: dict) -> str:
        """/authorize/complete → 계정 승인(AA) 검증·단일 소비 → 코드 발급 → claude.ai 복귀.

        AA 가 owner/plan 의 유일한 출처다. 서명·수명(≤2분)·req 바인딩을 모두 보고,
        connect_request 는 store 에서 원자적으로 단일 소비된다(재생 불가).
        """
        if not self.member_connect_enabled():
            raise PermissionError("member connect is not configured")
        req = params.get("req") or ""
        payload = edverify.verify_assertion(self.account_public, params.get("aa") or "",
                                            max_age=120)
        # aud 도 검증한다(L8). 계정은 aa 를 aud="mcp" 로 서명한다(connect.ts) — 다른
        # 계정→허브 어서션은 전부 aud 를 보는데 여기만 빠져 있었다. 방향 혼선 방지.
        if (not payload or payload.get("typ") != "aa" or payload.get("iss") != "account"
                or payload.get("aud") != "mcp"):
            raise PermissionError("invalid or expired approval")
        if payload.get("req") != req or not req:
            raise PermissionError("approval does not match this request")
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise ValueError("approval is missing a subject")
        plan = entitlement.normalize_plan(payload.get("plan"), source="account-assertion")
        return self.oauth.complete_connect(req, owner=f"user:{sub}", plan=plan)

    # ── 계정 삭제 캐스케이드(§5.9) ─────────────────────────────────────────────
    def purge_enabled(self) -> bool:
        """계정 공개키가 핀됐을 때만 활성(점진 배포 게이트 — 없으면 /internal/purge 503).

        OAuth·member connect 와 독립: purge 는 신뢰 방향이 verify-key 와 같고(계정→우리)
        account 공개키만 있으면 성립한다."""
        return bool(self.account_public)

    def _purge_oauth_state(self, out, owner: str):
        """그래프가 지워졌으면 이 서버의 OAuth 상태(토큰·리프레시·코드·사용량·플랜
        캐시)도 같이 간다 — 계정 삭제 뒤에도 남던 잔여(2026-09-26 갭 분석 §2.2, 판정
        D11). 실패는 삼키지 않는다: 계정은 5xx 를 받아 재시도하고, 그래프 purge 는
        멱등이라 두 번 돌아도 무해하다. `store` 가 없는 구성(정적 토큰만·테스트)은
        건너뛴다."""
        if isinstance(out, dict) and out.get("executed") and self.store is not None:
            out["oauth"] = self.store.purge_owner(owner)
        return out

    def purge_owner_request(self, assertion: str, raw_body: bytes | None = None) -> dict:
        """계정 서명 purge 어서션(typ=purge·iss=account·aud=mcp·≤2분)을 검증해 `sub`
        로 owner 를 유도하고, 사이드카에 owner 전 노드 삭제를 프록시한다.

        owner 는 **서명된 sub 에서만** 온다(요청 바디 미수용) — 유효한 A 소유자용
        어서션을 B 삭제에 재사용할 수 없다. 멱등 DETACH DELETE 라 만료창(≤2분) 내
        재생도 무해(이미 지워졌으면 0건). public_id 는 uuidv7 로 재사용되지 않아
        재등록 계정을 오삭제할 위험도 없다.

        그 무해함이 지문·일회성 검사를 **면제하지는 않는다.** 계정은 이 경로에도
        `bh`(본문 `"{}"` 의 해시)와 `jti` 를 이미 실어 보내는데 여기서는 읽지 않고
        있었다 — 보내는 쪽이 지불한 값을 받는 쪽이 버리던 상태다. 무해 논증은 오늘의
        핸들러 모양(본문 미사용·멱등)에 기대고 있으므로, 그 모양이 바뀌는 날 조용히
        틀려진다. 검사는 그 논증과 독립으로 세워 둔다."""
        if not self.purge_enabled():
            raise PermissionError("purge is not configured")
        payload = account_assertion.verify(
            self.account_public, assertion, raw_body, ("purge",),
            refusal="invalid or expired purge request", once=self._claim_jti)
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise ValueError("purge request is missing a subject")
        owner = f"user:{sub}"
        out = self.bridge.request("POST", "/sync/purge-owner", body={"owner": owner})
        return self._purge_oauth_state(out, owner)

    def purge_team_request(self, assertion: str, raw_body: bytes | None = None) -> dict:
        """팀 그래프 소거(typ=purge-team) — **팀 삭제 절차 전용**이다.

        개인 purge 와 유형을 가른 이유가 안전이다. 개인 경로는 `sub` 에서 `user:` 소유자
        **만** 유도할 수 있어, 멤버가 자기 계정을 지워도 팀 그래프를 만들 방법이
        구조적으로 없다(AP-1 §4.6). 그 성질을 지키려면 팀 소거는 별도 문이어야 한다 —
        개인 경로에 "팀도 가능" 분기를 더하는 순간 그 보장이 사라진다.

        계정 쪽에서는 팀 삭제 플로만 이 어서션을 서명하고, 그 전에 결제사 해지를 마친다.
        """
        if not self.purge_enabled():
            raise PermissionError("purge is not configured")
        payload = account_assertion.verify(
            self.account_public, assertion, raw_body, ("purge-team",),
            refusal="invalid or expired team purge request", once=self._claim_jti)
        graph_owner = payload.get("graph_owner")
        if not isinstance(graph_owner, str) or not _TEAM_OWNER_RE.fullmatch(graph_owner):
            raise ValueError("team purge request has an invalid graph owner")
        out = self.bridge.request("POST", "/sync/purge-owner", body={"owner": graph_owner})
        return self._purge_oauth_state(out, graph_owner)

    # ── 계정 화면의 남은 호출 수 ──────────────────────────────────────────────────
    def quota_request(self, assertion: str, raw_body: bytes | None = None) -> dict:
        """계정 서명 어서션(typ=quota)을 검증하고 본문의 스페이스마다 `quota_view` 를 낸다.

        본문은 `{"spaces": [{"owner": "user:<id>"|"team:<id>", "plan": ...}]}` 이다. 등급의
        정본은 계정이라 계정이 싣는다. 검증은 계정 서명 · 만료 ≤2분 · 본문 지문까지이고
        jti 는 소비하지 않는다: 읽기라 재생해도 바뀌는 것이 없고, 소비하면 화면을 열 때마다
        스토어에 쓴다. 지문이 본문을 묶으므로 잡은 어서션으로 다른 owner 를 물을 수는 없다."""
        if not self.account_public:
            raise PermissionError("quota view is not configured")
        account_assertion.verify(
            self.account_public, assertion, raw_body, ("quota",),
            refusal="invalid or expired quota request", once=None)
        try:
            spaces = json.loads(raw_body or b"{}").get("spaces")
        except (ValueError, AttributeError):
            raise ValueError("the body must be a JSON object") from None
        if not isinstance(spaces, list) or len(spaces) > _QUOTA_VIEW_MAX:
            raise ValueError(f"spaces must be a list of at most {_QUOTA_VIEW_MAX}")
        out = []
        for space in spaces:
            owner = space.get("owner") if isinstance(space, dict) else None
            if not isinstance(owner, str) or not _QUOTA_OWNER_RE.fullmatch(owner):
                raise ValueError("each space needs an owner of the form user:<id> or team:<id>")
            raw = space.get("plan")
            plan = None if raw is None else entitlement.normalize_plan(raw, source="quota-view")
            out.append({"owner": owner, "quota": self.quota_view(owner, plan)})
        return {"kind": "quota.view", "executed": True, "spaces": out}

    # ── 운영자 사용량 덤프(W1c) ─────────────────────────────────────────────────
    def usage_request(self, assertion: str, raw_body: bytes | None = None,
                      *, now: float | None = None) -> dict:
        """계정 서명 어서션(typ=usage)을 검증하고 **소유자 전체**의 사용량을 돌려준다.

        `/internal` 경로 중 owner 를 좁히지 않는 것은 이것뿐이다. 그래서 받는 유형도
        이것 하나로 가른다(purge·proxy 어서션을 여기에 쓸 수 없다). 검증은 나머지와
        같다: 계정 서명 · 만료 ≤2분 · 본문 지문 · 1회용 jti.

        싣는 것은 owner id 와 숫자뿐이다 — 이메일·제목·질의 원문은 없고, owner 를
        사람으로 잇는 것은 계정 쪽 일이다. 소유자마다:
          · `graph`: 사이드카 `/owner-stats` 의 노드·문서 수(없으면 빈 dict)
          · `calls`: 쿼터 카운터의 **현재 창** 호출 수. 창이 지난 행은 0 으로 읽는다 —
            카운터는 다음 호출 때에야 리셋되므로 표의 값을 그대로 내면 어제 호출이
            오늘 것으로 보인다. 잠긴 행은 창과 무관하게 그 값을 낸다.
        쿼터는 그래프 owner 단위로 센다(팀 호출은 `team:`의 공유 예산). 면제 owner 는 세지
        않으므로 `quota.exempt_owners` 로 따로 알린다.
        근거: [[vinc.plan.backoffice-and-stats-2026-09-12]]"""
        if not self.account_public:
            raise PermissionError("usage dump is not configured")
        account_assertion.verify(
            self.account_public, assertion, raw_body, ("usage",),
            refusal="invalid or expired usage request", once=self._claim_jti)
        stats = self.bridge.request("GET", "/owner-stats")
        if not stats.get("executed"):
            # 그래프를 못 읽은 것을 "노드 0"으로 내면 운영자는 데이터가 사라진 줄 안다.
            raise RuntimeError(stats.get("error") or stats.get("note")
                               or "owner stats unavailable")
        t = time.time() if now is None else now
        cfg = self.quota_config or {}
        # 창 길이는 설정이 아니라 정책 상수다. cfg 에서 읽으면 키가 없어 0 이 되고,
        # 그러면 모든 owner 의 호출 수가 조용히 0 으로 찍힌다.
        window_s = _DAY_S if self.quota_config else 0.0
        calls: dict[str, dict] = {}
        rows = self.store.usage_rows() if self.store is not None else []
        for row in rows:
            start, until = float(row["window_start"] or 0), float(row["locked_until"] or 0)
            active = bool(window_s) and (t - start) < window_s
            locked = until > t
            calls[row["owner"]] = {
                "count": int(row["count"] or 0) if (active or locked) else 0,
                "window_start": _utc_iso(start) if active else None,
                "locked_until": _utc_iso(until) if locked else None}
        graph = stats.get("owners") or {}
        owners = [{"owner": o, "graph": graph.get(o) or {},
                   "calls": calls.get(o) or {"count": 0, "window_start": None,
                                             "locked_until": None}}
                  for o in sorted(set(graph) | set(calls))]
        return {"kind": "usage.dump", "executed": True, "generated_at": _utc_iso(t),
                "quota": {"enabled": bool(self.quota_config),
                          "window_s": window_s or None,
                          "free_limit": cfg.get("free_limit"),
                          # 계정 백오피스가 읽는 키라 남긴다. 유료(Genius)는 세지 않으므로 null.
                          "team_limit": None,
                          "exempt_owners": sorted(cfg.get("exempt_owners") or ())},
                "owners": owners}

    # ── 웹 Vinc 그래프 프록시(계정 세션 → 서명 어서션 → owner 강제 read/write) ─────
    def graph_enabled(self) -> bool:
        """계정 공개키 핀 시에만 활성 — purge 와 같은 게이트(계정→우리 신뢰 방향)."""
        return bool(self.account_public)

    #: 이미 쓴 어서션 id → 만료 시각. **프로세스 안에만 둔다.**
    #: 유효 창이 120초뿐이라 재시작으로 잃는 것은 그 창 하나이고, 파일에 두면 쓰기마다
    #: DB 를 복사하는 경로(store.py)에 무인증 트래픽이 걸린다. 허브가 레플리카 하나로
    #: 고정돼 있어서 성립한다 — 여러 개로 늘리면 이 방어가 레플리카마다 갈라진다.
    _seen_jti: dict

    def _claim_jti(self, payload: dict) -> bool:
        """이 어서션을 처음 쓰는가(`account_assertion.claim_jti`). 표는 앱 하나에 하나이고 jti 를
        소비하는 `/internal/*` 경로가 함께 쓴다: 한 경로에서 쓴 jti 는 다른 경로에서도 거절된다."""
        seen = getattr(self, "_seen_jti", None)
        if seen is None:
            seen = self._seen_jti = {}
        return account_assertion.claim_jti(seen, payload)

    #: 서명된 지문과 받은 본문이 같은가. 검사 본체는 `account_assertion.body_matches` 다.
    _body_matches = staticmethod(account_assertion.body_matches)

    def graph_owner_request(self, assertion: str, args: dict,
                            raw_body: bytes | None = None) -> dict:
        """계정 서명 어서션을 검증해 owner 를 **강제**하고 op 를 사이드카로 프록시한다.

        받는 그래프 유형:
          - ``typ=proxy``      개인 그래프. owner = ``user:{sub}`` (기존, 바이트 동일).
          - ``typ=proxy-team`` 팀 그래프. owner = 서명된 ``graph_owner``, ``role`` 로 op 판정.
          - ``typ=proxy-community`` 공개 커뮤니티. 계정 outbox만 쓰고 일반 요청은 읽는다.

        **팀을 별도 유형으로 가른 것이 이 함수의 핵심 안전장치다.** 기존 ``proxy`` 에
        필드만 덧붙였다면, 구 허브 + 신 계정 조합에서 팀 작업이 조용히 **멤버의 개인
        그래프로** 흘러갔을 것이다(팀 문서가 개인 그래프에 쌓이고 팀 화면은 빈 결과 —
        오류가 아니라 무음 오동작이라 최악이다). 유형이 다르면 구 허브는 아래 검사에서
        그냥 거부하므로, 배포 순서를 틀렸을 때의 결과가 "정확성 문제"에서 "팀 기능이
        아직 안 열림"으로 강등된다.

        owner 는 어느 경우에도 서명 페이로드에서만 온다 — args 의 owner 는 pop 한다."""
        if not self.graph_enabled():
            raise PermissionError("graph proxy is not configured")
        payload = account_assertion.verify(
            self.account_public, assertion, raw_body,
            ("proxy", "proxy-team", "proxy-conversion", "proxy-community"),
            refusal="invalid or expired proxy request", once=self._claim_jti)
        typ = payload.get("typ")
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise ValueError("proxy request is missing a subject")
        op = payload.get("op")
        if typ == "proxy-conversion":
            actions = {"topic-conversion:" + a: a for a in ("preflight", "inventory-status", "stage", "status", "release", "cancel")}
            action = actions.get(op) if isinstance(op, str) else None
            if action is None:
                raise PermissionError("unsupported conversion capability")
            from uuid import UUID
            try:
                UUID(sub)
            except ValueError:
                raise PermissionError("invalid conversion subject") from None
            body = dict(args or {})
            for field in ("owner", "target", "run", "action", "_entitlements"):
                body.pop(field, None)
            body.update(owner=f"user:{sub}", action=action)
            if action not in ("preflight", "inventory-status"):
                target, run = payload.get("target"), payload.get("run")
                if not isinstance(target, str) or not _TEAM_OWNER_RE.fullmatch(target):
                    raise PermissionError("invalid conversion target")
                try:
                    UUID(run)
                except (ValueError, TypeError, AttributeError):
                    raise PermissionError("invalid conversion run") from None
                body.update(target=target, run=run)
            return self.bridge.request("POST", "/topic-conversion", body=body)
        route = _PROXY_OPS.get(op) if isinstance(op, str) else None
        if route is None:
            raise ValueError(f"unsupported proxy op: {op!r}")
        if op == "community-agent-import" and payload.get("scope") != "full":
            raise PermissionError("community Agent imports require full-access account authorization")
        method, path = route

        if typ == "proxy-community":
            from store import pkns
            if not pkns.enabled():
                raise PermissionError("community graphs require owner namespaced node keys")
            graph_owner = payload.get("graph_owner")
            if not isinstance(graph_owner, str) or not _COMMUNITY_OWNER_RE.fullmatch(graph_owner):
                raise ValueError("community proxy request has an invalid graph owner")
            role = payload.get("role")
            if payload.get("visibility") != "public" or role not in ("reader", "operator", "projector"):
                raise PermissionError("invalid public community capability")
            if role == "projector":
                if sub != _COMMUNITY_PROJECTOR_SUB or op not in _COMMUNITY_PROJECT_OPS:
                    raise PermissionError("invalid community projection capability")
                # 위키 페이지는 퇴역으로 남긴다. 페이지 노드를 지우는 것은 종류가 바뀌어 접두가
                # 달라진 옛 노드를 걷을 때뿐이다(그 판단은 계정 반영기 몫이다).
                if op == "delete":
                    node = str(args.get("node") or "")
                    if not any(p.fullmatch(node) for p in (
                            _COMMUNITY_SOCIAL_NODE_RE, _COMMUNITY_AGENT_NODE_RE, _COMMUNITY_PACK_NODE_RE)):
                        if not _community_page_node(node) or args.get("stray_type") is not None:
                            raise PermissionError(
                                "only projected social, Agent metadata or wiki page nodes may be deleted")
                if op == "doc-delete":
                    ident = str(args.get("id") or "")
                    agent = _COMMUNITY_AGENT_DOC_RE.fullmatch(ident) or _COMMUNITY_PACK_DOC_RE.fullmatch(ident)
                    if not _COMMUNITY_SOCIAL_DOC_RE.fullmatch(ident) and not (
                            agent and agent.group(1).lower() == graph_owner[10:].lower()):
                        raise PermissionError("only projected social or owned Agent metadata documents may be deleted")
                if op == "delete-edge":
                    ends = (args.get("from"), args.get("to"))
                    rel = str(args.get("type") or "").strip().lower()
                    # 결과(record) 페이지에서 나가는 ATTACHES_TO 도 위키 관계다. 위키 소유자의 기록 출발
                    # 관계는 rel 표에 쓰므로, 남은 ATTACHES_TO 는 같은 쌍 RELATED_TO 의 사본이다.
                    attach = rel == "attaches_to" and str(ends[0] or "").startswith("record:")
                    if (any(not _community_page_node(n) for n in ends)
                            or (rel not in _COMMUNITY_WIKI_RELS and not attach)):
                        raise PermissionError("only projected wiki page relations may be deleted")
            elif op not in _COMMUNITY_READ_OPS:
                raise PermissionError("community mutations must go through account revisions")
            owner = graph_owner.lower()
        elif typ == "proxy-team":
            graph_owner = payload.get("graph_owner")
            if not isinstance(graph_owner, str) or not _TEAM_OWNER_RE.fullmatch(graph_owner):
                raise ValueError("team proxy request has an invalid graph owner")
            if op in _TEAM_REFUSED_OPS:
                # 역할과 무관한 거부다. 공유 Agent 는 개인 그래프로만 가져온다.
                raise PermissionError(f"{op!r} is not available in a team graph")
            role = payload.get("role")
            if not isinstance(role, str) or not _role_allows(role, op):
                # 역할이 모자란 것도, 역할이 이상한 것도 같은 거부다(fail-closed).
                raise PermissionError(f"role {role!r} may not perform {op!r}")
            owner = graph_owner
        else:
            owner = f"user:{sub}"
        return self.proxy_op(op, args, owner, actor=f"user:{sub}",
                             person_session=payload.get("auth") == "session",
                             plan=payload.get("plan"))

    # 본체는 `proxy_ops.proxy_op` 다. `graph_owner_request` 와 `selfhost_web` 이 앱 메서드로 부른다.
    def proxy_op(self, op: str, args: dict | None, owner: str, *, actor: str,
                 person_session: bool = False, plan: str | None = None) -> dict:
        return proxy_ops.proxy_op(self, op, args, owner, actor=actor,
                                  person_session=person_session, plan=plan)


#: 한 연결이 아무것도 안 보내며 스레드를 붙들 수 있는 시간(초). `StreamRequestHandler`
#: 가 이 값을 소켓에 건다. 없으면 연결만 열어 두는 클라이언트가 스레드를 영원히 잡고,
#: 그것이 slowloris 다. 정상 요청은 본문 4MB 상한 안에서 이보다 훨씬 빨리 끝난다.
READ_TIMEOUT = max(1, int(os.environ.get("VINC_MCP_READ_TIMEOUT") or 30))

#: 동시에 처리할 연결 수. 넘으면 **503 을 적고 닫는다** — 조용히 끊으면 클라이언트가
#: 재시도 폭풍을 만들고, 큐에 쌓으면 결국 같은 자원을 쓴다. 이 프로세스는 레플리카
#: 하나로 공유 그래프 전체를 서비스하므로(단일 라이터), 스레드 무제한은 곧 메모리
#: 무제한이고 그 끝은 OOM 이다 — 이 리포가 이미 네 번 겪은 고장의 형태다.
MAX_CONNECTIONS = max(1, int(os.environ.get("VINC_MCP_MAX_CONNECTIONS") or 64))
#: 붙잡기 모드에서 요청 하나가 준비를 기다리는 상한(초). 넘으면 503 과 Retry-After 로 답한다 —
#: 실패를 숨기지 않고 재시도할 때를 알려 준다. 허브 writer 인계(구 리비전의 마지막 스냅샷 →
#: 새 리비전의 복원·자격)가 이 안에 끝나야 배포가 지연으로만 보인다.
HOLD_S = max(1.0, float(os.environ.get("VINC_MCP_HOLD_S") or 90))


class _Handler(BaseHTTPRequestHandler):
    server_version = "vinc-mcp"
    # `Server:` 헤더에 런타임 버전을 싣지 않는다. BaseHTTPRequestHandler 기본값은
    # `vinc-mcp Python/3.x.y` 라 공격자에게 인터프리터 패치 수준을 공짜로 알려 줬다.
    # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] S13.
    # (`sys_version = ""` 만으로는 `"vinc-mcp "` 처럼 꼬리 공백이 남아 version_string 을 덮는다.)
    sys_version = ""
    timeout = READ_TIMEOUT

    def version_string(self) -> str:
        return self.server_version

    @property
    def app(self) -> MCPHTTPApp:
        return self.server.app  # type: ignore[attr-defined]

    def _held(self) -> bool:
        """붙잡기 모드(`serve_http_held`)에서 앱이 서기 전 요청을 기다리게 한다.

        True 면 이미 답했다는 뜻이다. `/health` 는 기다리지 않고 `starting` 을 답한다 —
        살아 있음은 바로 알려야 하고, 준비됐는지는 본문으로 구분한다."""
        ready = getattr(self.server, "ready", None)
        if ready is None or ready.is_set():
            return False
        if urllib.parse.urlparse(self.path).path == "/health":
            self._json(200, {"status": "starting", "hold": True})
            return True
        if ready.wait(getattr(self.server, "hold_s", HOLD_S)):
            return False
        self._json(503, {"error": "starting", "retryable": True, "retry_after_s": 5},
                   {"Retry-After": "5"})
        return True

    # ── CORS ────────────────────────────────────────────────────────────────
    # 브라우저 클라이언트 지원(원격 MCP 요건). 대상은 브라우저에서 도는 MCP 호스트
    # (Claude 웹 · ChatGPT 웹)와 MCP Inspector(로컬 디버깅). 서버측에서 호출하는
    # 호스트(대부분의 커넥터 백엔드)와 데스크톱 앱(Cursor·Codex·Kimi CLI)은 CORS 를
    # 타지 않으므로 여기 없어도 동작한다 — 이 목록은 **브라우저 오리진 전용**이다.
    # **와일드카드를 쓰지 않는다** — 허용 오리진만 반사한다. 인증은 Bearer 헤더이지
    # 쿠키가 아니므로 credentials 는 불필요하고, 그래서 오리진 반사가 CSRF 표면을
    # 열지 않는다(브라우저가 남의 토큰을 실어주지 못한다).
    _CORS_ORIGINS = ("https://claude.ai", "https://claude.com",
                     "https://chatgpt.com", "https://chat.openai.com",
                     "http://localhost:6274", "http://127.0.0.1:6274")

    def _cors_headers(self) -> dict:
        origin = self.headers.get("Origin")
        if not origin or origin not in self._CORS_ORIGINS:
            return {}
        return {"Access-Control-Allow-Origin": origin,
                "Vary": "Origin",
                "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                "Access-Control-Allow-Headers":
                    "Authorization, Content-Type, Mcp-Session-Id, Mcp-Protocol-Version, "
                    "Mcp-Method, Mcp-Name",
                "Access-Control-Expose-Headers": "Mcp-Session-Id, WWW-Authenticate",
                "Access-Control-Max-Age": "86400"}

    def _origin_rejected(self) -> bool:
        """브라우저 오리진 게이트 — `Origin` 이 **있고** 허용목록 밖이면 막는다.

        스펙은 로컬 HTTP 전송에 Origin 검증을 요구하고(DNS 리바인딩), 공식 SDK 도
        `transport_security.py` 로 같은 일을 한다. 여기 없던 것은 `Origin` 이
        `_cors_headers()` 의 반사에만 쓰였기 때문이다 — CORS 는 응답을 **읽지**
        못하게 하지만 요청이 **도달하는** 것 자체는 막지 않는다.

        `Origin` 이 없으면 통과시킨다. 서버측 커넥터·데스크탑 앱(Cursor·Codex 등)은
        이 헤더를 아예 안 보내므로, 없다고 막으면 정상 클라이언트가 전멸한다.
        즉 이 게이트는 **브라우저에서 온 요청에만** 걸린다."""
        origin = self.headers.get("Origin")
        return bool(origin) and origin not in self._CORS_ORIGINS

    def _protocol_version_rejected(self) -> dict | None:
        """`MCP-Protocol-Version` 검증. 문제 없으면 None, 있으면 400 에 실을 JSON-RPC 오류.

        모던 리비전도 받는다. 거절 본문은 UnsupportedProtocolVersion(-32022)이다: 두 세대를
        다 아는 클라이언트는 400 본문을 읽고, 이 오류면 `supported` 로 다시 보내고 아니면
        `initialize` 로 물러난다. 본문을 아직 안 읽은 자리라 `id` 는 null 이다.

        2025-06-18 스펙: 초기화 뒤 클라이언트는 이 헤더를 실어야 하고, 서버는 지원
        하지 않는 값에 400 을 준다. 헤더가 없으면 2025-03-26 으로 가정한다(하위호환).
        전에는 이 헤더 이름이 CORS 허용목록 문자열에만 있고 코드가 읽지 않았다.

        ⚠ 대조 대상은 **지원 목록**이지 '이 세션이 협상한 값'이 아니다. 그래서 이
        검증은 무상태이고, 세션 기계를 되살릴 이유가 되지 못한다(MCPHTTPApp.__init__
        참조). 제대로 동작하는 클라이언트는 협상 결과를 그대로 실으므로 정의상 이
        목록 안이다."""
        want = self.headers.get("MCP-Protocol-Version")
        if want is None or want in PROTOCOL_VERSIONS or want in modern.MODERN_VERSIONS:
            return None
        return modern.unsupported(want, PROTOCOL_VERSIONS).reply(None)

    def do_OPTIONS(self):  # noqa: N802
        """프리플라이트 — 허용 오리진에만 CORS 를 붙여 204."""
        if self._held():
            return
        from . import rest_api
        if rest_api.handle(self, "OPTIONS"):
            return
        self.send_response(204)
        for k, v in self._cors_headers().items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── 응답 헬퍼 ───────────────────────────────────────────────────────────
    def _json(self, code: int, obj: dict | list, headers: dict | None = None) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in self._cors_headers().items():
            self.send_header(k, v)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _plain(self, code: int, text: str, headers: dict | None = None) -> None:
        """text/plain 본문. 디렉터리 도메인 검증은 JSON 을 거절한다."""
        raw = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in self._cors_headers().items():
            self.send_header(k, v)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    def _unauthorized(self, resource_path: str = "", profile: str | None = None) -> None:
        """401 + 이 자원의 메타데이터 주소.

        RFC 9728 의 **경로 삽입형**을 쓴다: 자원이 `/t/<uuid>/mcp` 면 메타데이터는
        `/.well-known/oauth-protected-resource/t/<uuid>/mcp` 다. 루트 형태 하나만
        내주면 팀 주소에 붙은 클라이언트가 개인 자원의 문서를 발견한다 — 그것이 팀
        주소에서 OAuth 를 막아 두었던 이유이고, 여기서 함께 닫는다(#120).

        ⚠ 팀 주소에서는 이 함수가 **안 불린다.** 거기서는 401 이 아니라 404 여야
        하기 때문이다(팀의 존재가 새면 안 된다). 그래도 인자를 받는 이유는 개인
        자원의 경로가 나중에 하나 더 생겨도 이 자리가 거짓말을 안 하게 하려는 것이다.
        """
        base = f'{self._issuer()}/.well-known/oauth-protected-resource'
        meta = base + (resource_path if resource_path.startswith("/") else "")
        if profile:
            # 프로파일을 단 주소의 문서는 그 주소를 자원으로 말한다(GET 쪽 `_with_profile`).
            meta += "?profile=" + profile
        self._json(401, {"error": "unauthorized"},
                   {"WWW-Authenticate": f'Bearer resource_metadata="{meta}"'})

    def _issuer(self) -> str:
        if self.app.oauth is not None:
            return self.app.oauth.issuer
        host = self.headers.get("Host") or "127.0.0.1"
        return f"http://{host}"

    def _basic_client_id(self) -> str:
        """`Authorization: Basic` 에 실려 온 client_id(RFC 6749 §2.3.1).

        client_secret 을 설정한 클라이언트는 **본문에 client_id 를 안 싣고** 헤더로만
        보낸다. PlayMCP(카카오)가 그랬고, 본문만 보던 `/token` 은 그것을
        `client_id mismatch` 로 거절했다 — 동의까지 통과한 뒤 마지막 한 걸음에서
        막히는 고장이라 밖에서는 원인이 안 보인다.

        비밀번호 칸은 **읽지 않는다.** 이 서버는 공개 클라이언트만 발급하고
        (`token_endpoint_auth_methods_supported: ["none"]`), 코드의 소유 증명은
        PKCE S256 이 한다. 여기서 헤더는 신원의 출처일 뿐 인증 수단이 아니므로
        광고도 바꾸지 않는다 — 검증하지 않는 방식을 지원한다고 적으면 거짓말이다."""
        header = self.headers.get("Authorization") or ""
        if header[:6].lower() != "basic ":
            return ""
        try:
            raw = base64.b64decode(header[6:].strip()).decode("utf-8")
        except Exception:
            return ""
        # 자격증명은 base64 전에 form-urlencode 된다(같은 절).
        return urllib.parse.unquote(raw.split(":", 1)[0]) if ":" in raw else ""

    def _body(self) -> dict:
        self._raw_body = b""
        try:
            rawb = httpbody.read_body(self.headers, self.rfile, MAX_BODY)
        except httpbody.BodyTooLarge:
            # 상한 초과는 본문을 **읽지 않고** 빈 dict 를 돌려준다. 상태 코드는 이 함수가 정하지
            # 않는다 — 초과를 오류로 알려야 하는 라우트는 부르기 전에 스스로 본다(`_internal_graph`).
            # 미수신 바이트가 남지만 무해하다 — `protocol_version` 이 HTTP/1.0(기본)이라 매 응답 뒤
            # 커넥션이 닫히기 때문이다.
            # ⚠ `protocol_version = "HTTP/1.1"` 로 올리면 keep-alive 가 살아나 이 잔여물이
            # 다음 요청으로 파싱된다(요청 밀수). 그때는 여기서 `close_connection = True`.
            # 정수가 아니거나 음수인 길이(`httpbody.BadContentLength`)는 강등하지 않고 올린다:
            # `do_POST` 가 400 으로 답한다.
            return {}
        # 지문 대조는 **받은 바이트**로 한다(감사 M2). 파싱된 dict 를 다시 직렬화하면
        # 키 순서·공백이 달라져 보낸 쪽과 다른 값이 나온다.
        self._raw_body = rawb
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        # UTF-8 이 아닌 본문은 **JSON 파싱 실패와 같은 자리로 강등**한다(둘 다 ValueError).
        # 그냥 두면 do_POST 가 UnicodeDecodeError 로 죽어 커넥션이 리셋되고, 프록시 뒤에서는
        # 클라이언트가 400 대신 **503** 을 본다(로그엔 매 요청 스택트레이스). 실제로 호스트가
        # CP949 로 인코딩한 질의를 보내 라이브 허브가 이 경로를 밟았다(2026-08-04).
        try:
            if ctype == "application/x-www-form-urlencoded":
                return {k: v[0] for k, v in urllib.parse.parse_qs(rawb.decode("utf-8")).items()}
            return httpbody.parse_json(rawb)
        except ValueError:
            return {}

    # ── 라우팅 ──────────────────────────────────────────────────────────────
    def do_GET(self):  # noqa: N802
        if self._held():
            return
        # 셀프호스트의 같은 출처 평면(/login·/api/*). 꺼져 있으면 아무것도 안 하고 지나간다.
        from . import selfhost_web
        if selfhost_web.handle(self, "GET"):
            return
        # 셀프호스트의 웹 앱 번들(/·/app/*). 꺼져 있으면 지나간다.
        from . import selfhost_static
        if selfhost_static.handle(self, "GET"):
            return
        from . import rest_api
        if rest_api.handle(self, "GET"):
            return
        url = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        gate = self.app.oauth
        # 커넥터 아이콘 — **무인증** 경로(아이콘은 공개 자산).
        # ⚠ 예전엔 `.ico` 에도 SVG 를 `image/svg+xml` 로 줬다. `.ico` 를 찾는 클라이언트는
        # 대개 래스터를 기대하므로 그 응답을 파싱하지 않고 버린다 — 아이콘을 서빙하는데도
        # 안 뜨는 원인이 될 수 있었다. 이제 확장자별로 실제 그 형식을 준다:
        #   /favicon.png · /favicon.ico → PNG(128px, 앱 아이콘과 동일 래스터)
        #   /favicon.svg               → SVG(벡터, 흰 바탕 커넥터용)
        if url.path in ("/favicon.ico", "/favicon.png"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(_FAVICON_PNG)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(_FAVICON_PNG)
            return
        if url.path == "/favicon.svg":
            raw = _FAVICON_SVG.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(raw)
            return
        # RFC 9728 은 자원 경로를 **well-known 뒤에 삽입**한다:
        #   /mcp              → /.well-known/oauth-protected-resource/mcp
        #   /t/<uuid>/mcp     → /.well-known/oauth-protected-resource/t/<uuid>/mcp
        # 접두 없는 루트 형태도 계속 받는다 — 기존 커넥터가 그것으로 발견하고 있고,
        # 그때 답은 종전과 **바이트가 같은** 개인 자원 문서다(#120 이 개인 경로를
        # 안 건드린다는 것이 계약이다).
        if url.path.startswith("/.well-known/oauth-protected-resource"):
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            rest = url.path[len("/.well-known/oauth-protected-resource"):]
            # `?profile=` 을 단 MCP 주소의 문서. RFC 9728 은 `resource` 가 클라이언트가 쓴 주소와 같기를
            # 요구하므로 맞는 값만 되비춘다. 없거나 틀리면 프로파일 없는 문서와 같다.
            prof = profile_query(url.query)[0]
            if not rest or rest == "/":
                return self._json(200, _with_profile(gate.resource_metadata(), prof))
            rspace, rpath = split_space(rest)
            # 실재하는 자원만 문서를 준다. 아무 경로에나 답하면 이 엔드포인트가
            # "그 주소는 있다"를 확인해 주는 탐침이 된다 — 팀 주소의 404 규칙과
            # 같은 이유다. 팀 uuid 의 **존재 여부는 여기서 묻지 않는다**(계정에
            # 물으면 그 자체가 유출이다): 모양이 맞으면 문서를 주고, 실제로 들어갈
            # 수 있는지는 자격증명이 판정한다. 그것이 "주소는 의도, 자격증명은 권한"
            # 이라는 이 설계의 갈래 그대로다.
            if rpath != "/mcp":
                return self._json(404, {"error": "no such resource"})
            return self._json(200, _with_profile(gate.resource_metadata(
                f"/t/{rspace}/mcp" if rspace else "/mcp"), prof))
        if url.path == "/.well-known/oauth-authorization-server":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            return self._json(200, gate.as_metadata())
        if url.path == "/.well-known/openai-apps-challenge":
            # ChatGPT 제출 포털이 초안마다 토큰을 주고, 이 URL 의 본문과
            # 바이트 단위로 대조한다. JSON·다른 토큰·빈 본문은 검증 실패.
            # 값은 env 라 초안이 바뀔 때 재배포 없이 갈아끼운다.
            token = (os.environ.get("VINC_OPENAI_APPS_CHALLENGE") or "").strip()
            if not token:
                return self._json(404, {"error": "openai apps challenge not configured"})
            return self._plain(200, token)
        if url.path == "/authorize":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            try:
                # key= 있으면 관리 키 자동승인(부트스트랩), 없으면 회원 승인 흐름.
                # 회원 경로가 켜져 있으면 이쪽이 정상 경로다(B-B3).
                # ChatGPT 디렉터리 검토자의 로그인은 여기가 아니라 공홈 /connect 다.
                # 매직링크는 거절 사유라 비밀번호 예외를 그 화면에 둔다.
                # 근거: [[vinc.plan.chatgpt-reviewer-login]]
                if params.get("key") or not self.app.member_connect_enabled():
                    location = gate.authorize(params)
                else:
                    location = self.app.begin_member_connect(params)
            except PermissionError as e:
                return self._json(403, {"error": str(e)})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            return self._redirect(location)
        if url.path == "/authorize/complete":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            try:
                location = self.app.complete_member_connect(params)
            except PermissionError as e:
                return self._json(403, {"error": str(e)})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            return self._redirect(location)
        if url.path == "/mcp":
            # 스펙상 허용된 응답 — 서버가 SSE 스트림을 제공하지 않음을 뜻한다.
            # (Allow 헤더로 어떤 메서드가 되는지 명시: SSE 우선 시도 후 405 를
            #  연결 실패로 오해하는 클라이언트에 다음 수를 알려준다.)
            return self._json(405, {"error": "SSE stream not provided; POST JSON-RPC"},
                              {"Allow": "POST, DELETE, OPTIONS"})
        if url.path == "/health":
            # 상태 저장소 진단을 함께 싣는다(2026-08-03). 이 사고의 급소가 정확히
            # 이것이었다: OAuth 등록이 배포마다 날아가고 있었는데 /health 는 200 만
            # 답해 **아무 데도 드러나지 않았다**. 경로·저널·스냅샷 결과를 여기 노출해
            # "영속이 실제로 되고 있는가"를 한 번의 curl 로 확인할 수 있게 한다.
            # 비밀은 없다 — 경로와 상태 문자열뿐이다.
            body: dict = {"status": "ok"}
            store = getattr(self.app.oauth, "store", None)
            health = getattr(store, "health", None)
            if callable(health):
                try:
                    body["oauth_state"] = health()
                except Exception as exc:   # noqa: BLE001 — 진단이 헬스를 죽이지 않게
                    body["oauth_state"] = {"error": f"{type(exc).__name__}: {exc}"}
            # 사이드카 도달성 — 툴콜과 웹 프록시가 전부 그것에 물려 있는데 그게 죽어도
            # 이 프로세스는 200 을 준다. **진단만** 싣고 상태 코드는 안 바꾼다(자동
            # 프로브가 없고, 단일 writer 락 특성상 헬스 실패로 재기동을 걸면 그게 더
            # 위험할 수 있다 — 사람이 보고 판단하는 신호다):
            # [[record:health-that-does-not-reach-its-dependency-answers-for-the-wrong-process]].
            bridge = getattr(self.app, "bridge", None)
            if bridge is not None:
                t0 = time.time()
                # 본문까지 받아 온다 — 도달성 bool 만 보면 사이드카가 싣는 진단이
                # 통째로 버려진다. 그 진단 중 하나가 임베딩 백로그이고, 그게 안
                # 보여서 워커의 죽음을 38시간 몰랐다(2026-08-05~07).
                # `?live=1` 은 사이드카에도 `live=1` 로 넘겨 스토어를 읽지 않게 한다. 1분마다
                # 찌르는 장애 감시용이고, 빠지는 것은 `embed` 하나다. 없으면 예전 그대로다.
                # 근거: [[vinc.plan.incident-mail-automation-2026-10-18]] §8.1
                if not hasattr(bridge, "health_detail"):
                    detail = None
                elif truthy(urllib.parse.parse_qs(url.query).get("live", [""])[0]):
                    detail = bridge.health_detail(live=True)
                else:
                    detail = bridge.health_detail()
                body["sidecar"] = {"reachable": detail is not None,
                                   "check_ms": round((time.time() - t0) * 1000)}
                if detail:
                    if detail.get("status") and detail["status"] != "ok":
                        body["sidecar"]["store"] = detail["status"]
                        for k in ("provision_error", "error"):
                            if detail.get(k):
                                body["sidecar"][k] = detail[k]
                    # 이행 진행도는 **최상위**에 둔다. 알람이 걸릴 자리라 파묻지 않는다.
                    if isinstance(detail.get("embed"), dict):
                        body["embed"] = detail["embed"]
                    # 영속 스냅샷도 같은 이유로 최상위다. 사이드카는 이 진단을 예전부터
                    # 싣고 있었는데 **여기서 버려지고 있었다** — 그래서 밖에서는
                    # "백업이 도는가"를 볼 방법이 없었다(2026-08-09). `VINC_STORE_LOCAL`
                    # 이 켜진 뒤로는 작업본이 컨테이너와 함께 사라지므로, 이 값이
                    # 곧 **데이터가 남는가**의 유일한 외부 신호다.
                    if detail.get("snapshot") is not None:
                        body["graph_snapshot"] = detail["snapshot"]
                    # 완료 파일을 읽는 쪽이 만든 안전한 상태만 전달한다. 원문은 싣지 않는다.
                    mirror = detail.get("node_mirror")
                    if isinstance(mirror, dict):
                        body["node_mirror"] = {k: mirror[k] for k in
                                               ("state", "reason", "index_ok") if k in mirror}
            return self._json(200, body)
        return self._json(404, {"error": f"no such route: GET {url.path}"})

    def do_POST(self):  # noqa: N802
        # 정수가 아니거나 음수인 `Content-Length` 는 어느 라우트에서든 400 이다. 라우트는 응답을 쓰기
        # 전에 본문을 읽으므로(`_body`, `_internal_graph` 의 상한 검사) 이 자리에서는 아직 아무것도
        # 보내지 않았다. `/v1` 은 자기 봉투로 먼저 답한다(`rest_api`).
        try:
            self._post()
        except httpbody.BadContentLength as exc:
            self._json(400, {"error": str(exc)})

    def _post(self):
        if self._held():
            return
        # 셀프호스트의 같은 출처 평면(/login·/api/*). 꺼져 있으면 아무것도 안 하고 지나간다.
        from . import selfhost_web
        if selfhost_web.handle(self, "POST"):
            return
        from . import rest_api
        if rest_api.handle(self, "POST"):
            return
        url = urllib.parse.urlparse(self.path)
        gate = self.app.oauth
        if url.path == "/register":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            try:
                # key= 가 맞으면 본문의 client_id 로 재등록(redirect 교체) — 운영자 전용.
                key = urllib.parse.parse_qs(url.query).get("key", [""])[0]
                rebind = bool(gate.admin_key) and key == gate.admin_key
                return self._json(201, gate.register(self._body(), rebind=rebind))
            except ValueError as e:
                return self._json(400, {"error": str(e)})
        if url.path == "/token":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            try:
                form = self._body()
                if isinstance(form, dict) and not form.get("client_id"):
                    form["client_id"] = self._basic_client_id()
                return self._json(200, gate.token(form))
            except ValueError as e:
                # 거절 이유를 남긴다. /token 은 **상대 서버가** 부르는 경로라
                # 브라우저 캡처에 안 잡히고, 이 줄이 없으면 접근 로그에 `400` 만
                # 남아 다섯 가지 거절(코드 만료·client_id·redirect_uri·PKCE·grant)을
                # 구분할 길이 없다. 실제로 PlayMCP 등록에서 그 벽을 만났다.
                # 값이 아니라 **키 이름만** 싣는다 — code·code_verifier 는 비밀이고,
                # 어느 필드가 빠졌는지만 알면 원인이 갈린다.
                keys = ",".join(sorted(form)) if isinstance(form, dict) else "?"
                sys.stdout.write("[oauth] token rejected: %s (fields=%s)\n" % (e, keys))
                return self._json(400, {"error": "invalid_grant", "error_description": str(e)})
        space, spath = split_space(url.path)
        if spath == "/mcp":
            if self._origin_rejected():
                return self._json(403, {"error": "origin not allowed"})
            bad = self._protocol_version_rejected()
            if bad:
                return self._json(400, bad)
            # 연결의 도구 프로파일(`?profile=`). 없으면 None 이고 아래는 지금과 같다.
            profile, bad_profile = profile_query(url.query)
            if bad_profile:
                return self._json(400, {"error": "unknown tool profile: use profile=read, "
                                                 "profile=core or profile=all"})
            # 주소로 온 팀이다. 토큰은 **이 주소**에 묶여 있어야 한다.
            try:
                auth = self._auth(space, presented_at=f"/t/{space}/mcp" if space else "/mcp")
            except entitlement.VerificationUnavailable:
                return self._verification_unavailable()
            if not auth["ok"]:
                # 팀 주소의 실패는 **404** 다. 401 이면 "그 팀은 있는데 너는 아니다"가
                # 되어 팀 id 를 추측해 조직을 훑을 수 있다. 모르는 팀·비멤버·제명·정지·
                # 모양 틀린 주소가 전부 같은 답을 받아야 한다.
                if space:
                    return self._json(404, {"error": "no such route: POST "
                                                     + url.path})
                return self._unauthorized(profile=profile)
            # 본문의 JSON-RPC 메시지(단건 · 배치)는 `mcp_post.answer` 가 게이트에 태우고 답한다.
            return mcp_post.answer(self, auth, space, profile)
        if url.path in ("/sync/push", "/sync/pull", "/sync/docs-push", "/sync/docs-pull"):
            return self._sync(url.path)
        if url.path == "/internal/purge-owner":
            return self._internal_purge()
        if url.path == "/internal/purge-team":
            return self._internal_purge_team()
        if url.path == "/internal/graph-owner":
            return self._internal_graph()
        if url.path == "/internal/usage":
            return self._internal_usage()
        if url.path == "/internal/quota":
            return self._internal_quota()
        return self._json(404, {"error": f"no such route: POST {url.path}"})

    def do_PUT(self):  # noqa: N802
        if self._held():
            return
        from . import rest_api
        if rest_api.handle(self, "PUT"):
            return
        # /v1 밖의 PUT 은 전과 같이 표준 라이브러리의 501 이다(CORS 헤더 없음).
        self.send_error(501, "Unsupported method ('PUT')")

    def do_DELETE(self):  # noqa: N802
        """`DELETE /mcp` — 클라이언트 세션 종료. 스펙상 선택이지만 종료를 보내는
        호스트(Codex·Cursor 등)가 501 을 오류로 취급하지 않도록 명시 처리한다.
        인증은 POST 와 동일하게 요구한다 — 인증 없이 아무나 두드리게 둘 이유가 없다.

        서버가 세션을 들지 않게 된 뒤로는(MCPHTTPApp.__init__ 참조) 버릴 것이 없어서
        **성공을 뜻하는 204 를 돌려주는 것 말고 하는 일이 없다.** 스펙상 정당하다:
        세션을 쓰지 않는 서버에게 종료는 no-op 이다. 라우트를 지우지 않는 이유는
        그대로다 — 종료를 보내는 호스트가 405/501 을 오류로 취급하지 않게."""
        if self._held():
            return
        from . import rest_api
        if rest_api.handle(self, "DELETE"):
            return
        url = urllib.parse.urlparse(self.path)
        if url.path != "/mcp":
            return self._json(404, {"error": f"no such route: DELETE {url.path}"})
        if self._origin_rejected():
            return self._json(403, {"error": "origin not allowed"})
        # 계정 검증 장애는 거절이 아니다. 다른 입구처럼 다시 시도할 503 으로 답한다
        # (연결을 답 없이 끊으면 호스트는 이유를 모른다).
        try:
            ok = self.app.authorized(self.headers.get("Authorization"))
        except entitlement.VerificationUnavailable:
            return self._verification_unavailable()
        if not ok:
            return self._unauthorized()
        self.send_response(204)
        for k, v in self._cors_headers().items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    # ── 계정 삭제 캐스케이드(§5.9) — 서명된 내부 purge ───────────────────────────
    def _internal_purge(self):
        """`POST /internal/purge-owner` — 계정 서명 어서션 → owner 전 노드 삭제.

        ⚠ **이 경로는 지금 공개 도달 가능하다.** 원래 설계는 이중 방어였다 — Caddy 가
        `mcp.*` 의 `/internal/*` 를 404 로 끊고(루프백만 허용), 그 위에 계정 Ed25519
        어서션을 얹는 심층방어. 그런데 **Caddy 는 가비아 VM 과 함께 사라졌고** Azure
        인그레스에는 대체 규칙이 없다. 즉 남은 관문은 **서명 하나뿐**이다
        (DEPLOYMENT.md 도 같은 사실을 기록한다). 이 서명 검증을 느슨하게 만드는 변경은
        곧바로 인증 우회가 된다는 뜻이므로, 여기를 고칠 때는 그 전제로 볼 것.

        서명은 verify-key 의 대칭이다(신뢰 방향만 반대: 계정이 서명, 우리가 account
        공개키로 검증). owner 는 서명된 `sub` 에서만 유도 → 스코프 위조 불가."""
        if not self.app.purge_enabled():
            return self._json(503, {"error": "purge_disabled"})
        assertion = self.headers.get("X-Vinc-Assertion") or ""
        # 본문은 안 쓰지만 **읽어야 한다** — 지문 대조가 받은 바이트를 필요로 하고,
        # `_raw_body` 는 `_body()` 가 채운다. 계정이 보내는 것은 `"{}"` 다.
        self._body()
        try:
            out = self.app.purge_owner_request(assertion, raw_body=self._raw_body)
        except PermissionError:
            return self._json(401, {"error": "unauthorized"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:   # 사이드카 도달 불가 — 계정이 재시도(fail-closed)
            return self._json(502, {"error": f"sidecar unreachable: {type(e).__name__}"})
        return self._json(200, out)

    def _internal_purge_team(self):
        """`POST /internal/purge-team` — 팀 그래프 소거(팀 삭제 절차 전용).

        경로를 개인 purge 와 가른 것이 안전장치다: 개인 경로는 `sub` 에서 `user:` 만
        유도할 수 있어 멤버 계정 삭제가 팀 그래프에 닿을 수 없다(AP-1 §4.6). 여기에
        분기를 더하는 대신 문을 따로 냈다."""
        if not self.app.purge_enabled():
            return self._json(503, {"error": "purge_disabled"})
        assertion = self.headers.get("X-Vinc-Assertion") or ""
        self._body()   # 지문 대조가 받은 바이트를 쓴다 — `_internal_purge` 와 같은 이유
        try:
            out = self.app.purge_team_request(assertion, raw_body=self._raw_body)
        except PermissionError:
            return self._json(401, {"error": "unauthorized"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            return self._json(502, {"error": f"sidecar unreachable: {type(e).__name__}"})
        return self._json(200, out)

    def _internal_graph(self):
        """`POST /internal/graph-owner` — 계정 서명 proxy 어서션 → owner 강제 read/write.

        purge 와 동일 신뢰 모델(계정 서명·우리가 account 공개키로 검증·루프백+Caddy 404
        차단). 웹 Vinc 의 데이터 평면: 로그인 계정 세션이 계정 백엔드를 거쳐 여기로 오고,
        owner 는 서명된 sub 에서만 강제된다. 바디 = op 인자(owner 제외), 어서션 = op 지정."""
        if not self.app.graph_enabled():
            return self._json(503, {"error": "graph_proxy_disabled"})
        # 상한을 넘으면 `_body()` 가 빈 본문을 돌려주고, 그러면 서명 지문이 어긋나 401 이
        # 됐다. 계정은 그것을 "세션 만료"로 옮겨 로그인된 사람을 로그인 화면으로 보냈다.
        # 근거: [[record:a-space-transfer-over-4mb-surfaced-as-a-logout-2026-09-18]]
        if httpbody.content_length(self.headers) > MAX_BODY:
            return self._json(413, {"error": "request body is too large"})
        assertion = self.headers.get("X-Vinc-Assertion") or ""
        args = self._body()
        try:
            out = self.app.graph_owner_request(assertion, args, raw_body=self._raw_body)
        except PermissionError:
            return self._json(401, {"error": "unauthorized"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:   # 사이드카 도달 불가
            # 계정 레이어가 이 사유를 "graph_upstream_error" 로 마스킹해 클라에는
            # 안 보인다(의도된 설계) — 그래서 여기서 안 남기면 아무 데도 안 남는다.
            # 2026-08-03 사고 때 access-log 한 줄(502 -)뿐이라 원인을 특정 못 했다.
            sys.stdout.write(f"[graph-owner] sidecar unreachable: {type(e).__name__}: {e}\n")
            sys.stdout.flush()
            return self._json(502, {"error": f"sidecar unreachable: {type(e).__name__}"})
        return self._json(200, out)

    def _internal_quota(self):
        """`POST /internal/quota` — 계정 서명 → 스페이스마다 남은 호출 수(`quota_request`)."""
        if not self.app.account_public:
            return self._json(503, {"error": "quota_view_disabled"})
        assertion = self.headers.get("X-Vinc-Assertion") or ""
        self._body()   # 지문 대조가 받은 바이트를 쓴다 — `_internal_purge` 와 같은 이유
        try:
            out = self.app.quota_request(assertion, raw_body=self._raw_body)
        except PermissionError:
            return self._json(401, {"error": "unauthorized"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        return self._json(200, out)

    def _internal_usage(self):
        """`POST /internal/usage` — 계정 서명 → 소유자 전체의 노드·문서·호출 수(W1c).

        운영자 콘솔의 데이터다. 성공은 owner 표식이 없어 접근 로그가 버리므로 여기서
        한 줄 남긴다(개수만, owner 없음) — 누가 언제 덤프를 받았는지는 계정 쪽 감사가
        맡는다."""
        if not self.app.account_public:
            return self._json(503, {"error": "usage_disabled"})
        assertion = self.headers.get("X-Vinc-Assertion") or ""
        self._body()   # 지문 대조가 받은 바이트를 쓴다 — `_internal_purge` 와 같은 이유
        try:
            out = self.app.usage_request(assertion, raw_body=self._raw_body)
        except PermissionError:
            return self._json(401, {"error": "unauthorized"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:   # 사이드카 도달 불가·그래프 조회 실패
            sys.stdout.write(f"[usage] owner stats unavailable: {type(e).__name__}: {e}\n")
            sys.stdout.flush()
            return self._json(502, {"error": f"owner stats unavailable: {type(e).__name__}"})
        sys.stdout.write(f"[usage] dump served: owners={len(out.get('owners') or [])}\n")
        sys.stdout.flush()
        return self._json(200, out)

    # ── 동기화(S1) — 회원 그래프 push/pull ────────────────────────────────────
    def _sync(self, path: str):
        """`/sync/push|pull` — 사이드카 `/sync/*` 로 프록시.

        **인증**: `resolve_auth` 를 여기서 **다시** 부른다(do_POST 의 /mcp 분기 안에만
        있으므로 새 라우트는 자기 책임). owner 없는 인증(static 토큰 = 단일 테넌트)은
        거부 — 동기화는 회원 스코프 전용이다.

        **쿼터 면제**: Dispatcher(rpc)를 우회하므로 `quota_check` 가 아예 안 불린다.
        의도된 설계 — 무료 15회/일은 도구 호출 한도이지, 동기화가 그걸로 막히면
        기능 자체가 성립하지 않는다(delta 한 번에 수백 노드가 오간다)."""
        try:
            auth = self._auth()
        except entitlement.VerificationUnavailable:
            return self._verification_unavailable()
        if not auth["ok"]:
            return self._unauthorized()
        owner = auth["owner"]
        if not owner:
            return self._json(400, {"error": "sync requires a member-scoped key "
                                             "(static-token auth has no owner)"})
        # W18 — push 는 도구 디스패치를 **우회하는** 그래프 쓰기다. /mcp 만 막고
        # 여기를 열어 두면 읽기 전용 키가 sync 로 쓴다(무음 전권). pull 은 읽기라
        # 통과한다. 근거: [[decision:scoped-key-tier]]
        if path in ("/sync/push", "/sync/docs-push") and auth.get("scope") != "full":
            return self._json(403, {"error": "this key is read-only: sync push "
                                             "writes to the graph"})
        body = self._body()
        try:
            if path == "/sync/pull":
                params = {"owner": owner, "since": int(body.get("since") or 0)}
                # 모드 전환의 전량 받기(VIN-130) — 주제 없는 노드까지.
                if body.get("all") is True:
                    params["all"] = "1"
                out = self.app.bridge.request("GET", "/sync/pull-delta", params=params)
            elif path == "/sync/docs-pull":
                # 문서 정본 채널(D45 P3) — 노드 delta 와 별개(레일이 다르다).
                params = {"owner": owner, "since": int(body.get("since") or 0)}
                if body.get("all") is True:          # 전량 받기(VIN-130)
                    params["all"] = "1"
                out = self.app.bridge.request("GET", "/sync/docs-export", params=params)
            elif path == "/sync/docs-push":
                out = self.app.bridge.request("POST", "/sync/docs-apply", body={
                    "owner": owner, "payload": body.get("payload") or {}})
            else:
                # 실행 기록의 대기·승인·답은 허브가 저장한 값을 남기고, 처음 보는 실행은 질문만
                # 받는다(묶음을 거절하지 않는다). 기기를 믿게 하는 값은 이 요청에서 읽지 않는다.
                from . import run_state
                out = run_state.push(self.app.bridge, owner, body.get("bundle") or {})
        except Exception as e:
            return self._json(502, {"error": f"sidecar unreachable: {type(e).__name__}"})
        return self._json(200, out)

    # ── 인증 + 로깅 ───────────────────────────────────────────────────────
    def _verification_unavailable(self):
        return self._json(503, {"error": "verification_unavailable"},
                          headers={"Retry-After": "1"})

    def _auth(self, space: str | None = None, *, presented_at: str | None = None) -> dict:
        """`resolve_auth` 를 부르고 **누가 불렀는지를 응답 로그에 실을 수 있게** 남긴다.

        호출부 셋이 각자 `self.app.resolve_auth(...)` 를 부르는데 owner 를 기억하는
        자리가 없어서, 성공한 요청을 계정에 귀속할 방법이 아무 데도 없었다. 쿼터
        카운터는 현재 창만 들고 아무도 읽지 않고, 로그는 실패만 남았다. 근거:
        [[record:log-analytics-attributes-hub-mcp-requests-by-owner-hash-since-2026-09-02]]
        """
        # ⚠ `presented_at` 을 `space` 에서 **유도하지 않는다.** 2026-09-22 에 이 자리에서
        # `f"/t/{space}/mcp" if space else "/mcp"` 로 유도했다가 같은 결함을 한 층 아래로
        # 옮겼을 뿐이었다 — `space` 인자로 고른 팀도 그 유도를 타서 여전히 거절됐다.
        # 도착한 주소를 아는 것은 **호출부**다. 그러니 호출부가 말하게 두고 여기서는
        # 개인 끝점을 기본으로 둔다(세 호출부 중 둘이 실제로 그것이다).
        auth = self.app.resolve_auth(self.headers.get("Authorization"), space,
                                     presented_at=presented_at or "/mcp")
        self._owner_tag = _owner_tag(auth.get("owner")) if auth.get("ok") else None
        return auth

    # stdout 은 컨테이너에서 곧 로그다. 삼키면 장애 때 단서가 0 이 된다
    # ([[record:in-a-container-a-swallowed-log-is-no-log]]).
    def log_message(self, fmt, *args):
        sys.stdout.write("[http] %s %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()

    def log_request(self, code="-", size="-"):
        """실패 전부 + **owner 를 아는 성공**을 남긴다.

        예전에는 2xx·3xx 를 통째로 버렸다. 그래서 7일치 로그 612줄이 전부 4xx·5xx 였고,
        실사용이 얼마인지는 지표의 총량으로만 보였다 — 누가 얼마나 썼는지는 어디에도
        없었다. 익명 성공은 지금도 버린다: `/health` 와 discovery 가 하루 1,728회라
        (가용성 프로브 4종 × 3지역 × 5분) 그것까지 남기면 로그가 프로브 로그가 된다.
        """
        # 한 번 쓰고 비운다. keep-alive 는 한 연결로 여러 요청을 처리하므로, 안 비우면
        # 인증 없는 다음 요청이 앞 요청의 owner 를 달고 남는다.
        tag, self._owner_tag = getattr(self, "_owner_tag", None), None
        try:
            n = int(code)
        except (TypeError, ValueError):
            n = 0
        if n and n < 400 and not tag:
            return
        # 쿼리스트링은 잘라낸다 — /authorize 의 파라미터 등이 로그에 눌러앉지 않도록.
        line = (self.requestline or "").split("?")[0]
        self.log_message('"%s" %s %s%s', line, code, size,
                         f" owner={tag}" if tag else "")


def _owner_tag(owner: str | None) -> str | None:
    """로그에 실을 owner 표식 — **해시만** 남긴다.

    owner 는 `user:<uuid>` 로 이미 불투명하지만, 원문을 남기면 로그 한 벌이 곧 계정
    명부가 되고 다른 로그와 조인된다. 절단 해시는 "이 계정이 오늘 몇 번 불렀나"에는
    충분하고, 이메일은 로그에 닿지 않는다.

    ⚠ **키 없는 SHA-256 이다(HMAC 이 아니다).** owner id 를 아는 쪽은 같은 계산으로
    표식을 되짚는다: 계정 테이블의 public_id 를 전부 해시하면 로그와 조인된다. 지원
    조회("이 계정의 어제 호출 수")가 한 줄로 되는 이유가 그것이고, 그래서 **일부러
    키 없이 둔다**(2026-09-25 판정). 계정 앱 감사 로그의 `subjectTag` 는 이메일의
    HMAC 이라 모양(12자 절단)만 같고 성질은 다르다. 이 둘을 같은 것으로 적은 문서가
    있었으니, 키를 넣고 싶어지면 지원 조회 경로부터 다시 설계할 것.

    12자를 쓰는 이유: 오늘 이 그래프의 계정 수는 한 자릿수이고, 48비트면 충돌 확률이
    실질적으로 0 이면서 로그 한 줄이 짧다.
    """
    if not owner:
        return None
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()[:12]


class BoundedHTTPServer(ThreadingHTTPServer):
    """연결 수에 천장이 있는 ThreadingHTTPServer(감사 2026-08-18 M7).

    기본 구현은 연결마다 스레드를 **무제한** 만든다. 공개 엔드포인트에서 그것은
    누구나 이 프로세스의 메모리를 늘릴 수 있다는 뜻이고, 이 프로세스는 레플리카
    하나로 공유 그래프를 서비스한다. 앞단에 레이트리밋도 없다.

    거절은 조용히 하지 않는다 — 503 을 적고 닫는다. 그래야 클라이언트가 재시도를
    조절할 수 있다. 대기 큐를 두지 않는 것도 의도다: 큐에 쌓인 연결도 소켓과
    버퍼를 쓰므로 천장을 옮길 뿐이다."""

    daemon_threads = True

    def __init__(self, *args, max_connections: int = MAX_CONNECTIONS, **kwargs):
        super().__init__(*args, **kwargs)
        self._slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\n"
                    b"Connection: close\r\n\r\n")
            except OSError:
                pass                      # 이미 끊긴 연결 — 거절이 목적이라 무시한다
            self.shutdown_request(request)
            return
        # 슬롯은 아래 스레드가 반납한다. 여기서 반납하면 안 된다 — 스레드가 아직 산다.
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_server(app: MCPHTTPApp, host: str = "127.0.0.1", port: int = 8790,
                *, max_connections: int = MAX_CONNECTIONS) -> ThreadingHTTPServer:
    httpd = BoundedHTTPServer((host, port), _Handler, max_connections=max_connections)
    httpd.app = app  # type: ignore[attr-defined]
    return httpd


class _HeldHTTPServer(BoundedHTTPServer):
    """붙잡기 모드 서버. 내려가면(`shutdown`·`server_close`) 조립 재시도도 멈춘다."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = None
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.hold_s = HOLD_S

    def shutdown(self):
        self.stopping.set()
        super().shutdown()

    def server_close(self):
        self.stopping.set()
        super().server_close()


def make_held_server(bridge: Bridge, build, *, host: str = "127.0.0.1", port: int = 8790,
                     hold_s: float | None = None, retry_s: float = 1.0) -> _HeldHTTPServer:
    """포트를 **먼저** 열고, 사이드카가 준비되면 앱을 조립해 붙잡아 둔 요청을 푸는 서버.

    허브 writer 인계용이다. 새 리비전은 구 리비전이 마지막 스냅샷을 올릴 때까지 스토어를
    열지 않는데, 그동안 포트가 닫혀 있으면 플랫폼이 트래픽을 넘기지 않고 구 리비전도
    내리지 않는다(교착). 그래서 포트는 열고 요청은 붙잡는다.

    ``build()`` 는 사이드카가 아직이면 예외를 내고, 준비되면 `MCPHTTPApp` 인자 dict 를
    돌려준다. OAuth 상태 복원도 그 안에서 한다 — 구 리비전의 MCP 가 멈춘 뒤라야 마지막
    상태를 받는다. 조립은 배경 스레드에서 하므로 반환된 서버는 곧장 받을 수 있다."""
    httpd = _HeldHTTPServer((host, port), _Handler, max_connections=MAX_CONNECTIONS)
    if hold_s is not None:
        httpd.hold_s = hold_s
    started = time.monotonic()

    def assemble() -> None:
        last = None
        while True:
            try:
                kwargs = build()
                break
            except Exception as exc:                          # noqa: BLE001 — 준비될 때까지 다시
                # 같은 이유는 한 번만 적는다. 복원이 몇 분 걸리면 초마다 한 줄이 쌓인다.
                why = f"{type(exc).__name__}: {exc}"
                if why != last:
                    print(f"[mcp] hold — not ready: {why}", file=sys.stderr, flush=True)
                    last = why
                if httpd.stopping.wait(retry_s):
                    return
        httpd.app = MCPHTTPApp(bridge, **kwargs)
        httpd.ready.set()
        print(json.dumps({"host": host, "port": httpd.server_address[1], "hold": True,
                          "ready_after_s": round(time.monotonic() - started, 1)}), flush=True)

    threading.Thread(target=assemble, name="vinc-mcp-hold", daemon=True).start()
    return httpd


def serve_http_held(bridge: Bridge, build, *, host: str = "127.0.0.1", port: int = 8790,
                    hold_s: float | None = None) -> int:
    """`make_held_server` 를 종료 신호까지 돌린다."""
    httpd = make_held_server(bridge, build, host=host, port=port, hold_s=hold_s)
    from . import selfhost_seats
    selfhost_seats.start_checker()
    print(json.dumps({"host": host, "port": httpd.server_address[1], "hold": True,
                      "hold_s": httpd.hold_s}), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        bridge.close()
    return 0


def serve_http(bridge: Bridge, *, host: str = "127.0.0.1", port: int = 8790,
               static_token: str | None = None, oauth: OAuthGate | None = None,
               verify_key=None, verify_member=None, verify_community=None, community_write=None, community_read=None,
               quota_config: dict | None = None,
               mcp_seed: bytes | None = None, account_public: bytes | None = None,
               connect_url: str | None = None, free_max_domains: int | None = None) -> int:
    app = MCPHTTPApp(bridge, static_token=static_token, oauth=oauth,
                     verify_key=verify_key, verify_member=verify_member,
                     verify_community=verify_community, community_write=community_write, community_read=community_read,
                     quota_config=quota_config,
                     mcp_seed=mcp_seed, account_public=account_public,
                     connect_url=connect_url, free_max_domains=free_max_domains)
    httpd = make_server(app, host, port)
    # 셀프호스트이고 계정에 연결돼 있으면 하루 한 번 좌석을 묻는다. 한 사람 허브는 묻지 않는다.
    from . import selfhost_seats
    selfhost_seats.start_checker()
    modes = [m for m, on in (("static", static_token), ("oauth", oauth),
                             ("apikey", verify_key),
                             ("member", app.member_connect_enabled())) if on]
    # 기동 로그에 한도 설정을 드러낸다 — env 를 수동으로 넣는 배포라(컨테이너 앱 갱신이
    # env 를 반영하지 않는다) 빠뜨렸을 때 로그가 유일한 조기 신호다.
    print(json.dumps({"host": host, "port": httpd.server_address[1],
                      "auth": "+".join(modes) or "none",
                      "quota": bool(quota_config),
                      "free_max_domains": free_max_domains}), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        bridge.close()
    return 0
