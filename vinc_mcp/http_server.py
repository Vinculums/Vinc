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
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import edverify, entitlement, tools
from .bridge import Bridge
from .oauth import CONNECT_TTL, OAuthGate
from .rpc import PROTOCOL_VERSIONS, Dispatcher

MAX_BODY = 4 * 1024 * 1024

#: 주제 상한이 걸리는 프록시 op — 새 주제를 만들 수 있는 쓰기 경로만(AP-1 §3.3).
#: 주제 이름 바꾸기(domain-rename)는 전량 개명이라 **개수를 늘릴 수 없어** 제외한다.
_CAPPED_PROXY_OPS = ("ingest-fragment", "ingest-pack")

# 웹 Vinc 그래프 프록시 표면(op → (method, 사이드카 경로)). 계정 세션→서명 어서션이
# 이 op 만 요청할 수 있고, 화이트리스트 외는 거부한다. **제외**: themes/types(전역
# vocab, owner 차원 없음)·sync/*(데스크탑↔계정 동기화 기계)·backup(로컬 파일)·
# purge(전용 서명 경로). owner 는 항상 서명된 sub 에서 강제 주입된다(SEC-3 + 프록시).
_PROXY_OPS: dict[str, tuple[str, str | None]] = {
    # 읽기(GET)
    "view": ("GET", "/view"), "ledger": ("GET", "/ledger"), "graph": ("GET", "/graph"),
    "recall": ("GET", "/recall"), "search": ("GET", "/search"),
    "coupling": ("GET", "/coupling"), "suggest-ties": ("GET", "/suggest-ties"),
    "decide": ("GET", "/decide"), "domains": ("GET", "/domains"),
    "promote-candidates": ("GET", "/promote-candidates"),
    "learner": ("GET", "/learner"), "share": ("GET", "/share"),
    # 문서 정본·뷰별 원문(D45 — Chunk=SSoT). 웹 Vinc 완전 패리티. 전부 owner 강제
    # (핸들러 _req(owner) 또는 프록시 sub 스탬프 + query.py/docstore owner_id WHERE 필터).
    # 노드/엣지 클릭 상세·Reader 밑줄·합계 드릴다운·타임라인 기간·문서 리스트/복원.
    "doc": ("GET", "/doc"), "docs": ("GET", "/docs"),
    "doc-highlights": ("GET", "/doc/highlights"),
    "doc-context": ("GET", "/doc/context"),   # 리더 머리(개념 칩·목차·청크 오프셋)
    "node-context": ("GET", "/node-context"),
    "edge-context": ("GET", "/edge-context"),
    "ledger-drilldown": ("GET", "/ledger-drilldown"),
    "docs-in-range": ("GET", "/docs-in-range"),
    "nav-tree": ("GET", "/nav-tree"),   # 좌측 문서 트리(D69) — 주제→문서 깊이순
    # 관례(GRAPH.md) — 읽기와 재생성을 **짝으로** 연다. 웹 Vinc 도 같은 것을 봐야 한다
    # (계정 GET_OPS/POST_OPS 와 1:1). 읽기만 열면 [다시 생성]만 404 로 죽는다.
    "conventions": ("GET", "/conventions"),
    "job": ("GET", None),   # /jobs/{id} — id 검증 후 특수 처리
    # 쓰기(POST) — SEC-3 하드닝된 owner-스코프 경로
    "pin": ("POST", "/pin"), "tag": ("POST", "/tag"), "touch": ("POST", "/touch"),
    "activate": ("POST", "/activate"),
    "ingest-fragment": ("POST", "/ingest/fragment"),
    "ingest-pack": ("POST", "/ingest/pack"),
    "delete": ("POST", "/delete"), "delete-edge": ("POST", "/delete-edge"),
    "merge-nodes": ("POST", "/merge-nodes"),
    # 문서 정본 쓰기(D45 — 앱 내 편집). owner 필수(put/extract 는 미전송 시 400).
    # put=적재/편집(단일 writer 직렬화), extract=정본→개념 파생(남의 개념 불가침),
    # reembed=편집 후 벡터 재생성(허브는 자체호스팅 임베딩 워커가 채운다).
    "doc-put": ("POST", "/doc/put"),
    "conventions-build": ("POST", "/conventions/build"),   # 관례 재생성(GM-8)
    # 파일 바이트 입구(텍스트·PDF) — 본문은 base64 라 4/3 로 부푼다. 아래 MAX_BODY
    # (4MB)가 실효 상한을 결정하므로 웹에서 올릴 수 있는 원본은 대략 2.8MB 다.
    "doc-put-file": ("POST", "/doc/put-file"),
    "doc-extract": ("POST", "/doc/extract"),
    "doc-reembed": ("POST", "/doc/reembed"),
    # 관계 근거 저작(U2) — 정본 청크에서 결정론 추출, owner 스코프.
    "evidence-backfill": ("POST", "/evidence/backfill"),
    # 문서 삭제 — 앱 UI 삭제 배선(S1). delete/merge-nodes 와 동일 안전모델:
    # owner 는 프록시가 sub 로 강제하고, docstore.delete_doc 가 owner 불일치를 거부한다.
    "doc-delete": ("POST", "/doc/delete"),
    # 도메인 관리(S2) — owner 노드의 domain 문자열 일괄 변경(전역 Domain 노드 불변).
    "domain-rename": ("POST", "/domain/rename"),
}
_JOB_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

# 팀 그래프 소유자 형식(AP-1 §4.3). 계정이 서명해 보내지만 **형식은 우리가 다시 본다** —
# 서명이 진짜여도 `team:../../etc` 같은 값이 그대로 owner 가 되면 안 된다.
_TEAM_OWNER_RE = re.compile(
    r"team:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# ── 팀 역할별 op 등급(AP-1 §4.4) ──────────────────────────────────────────────
# 세밀한 주제별·노드별 권한은 전 질의 플랜 개작이 필요해 v1 범위 밖이다. 대신 **이미
# 이름이 붙어 있는** op 목록을 등급으로 가른다. 판정은 여기(우회 불가 지점)에서 하고,
# 계정 프록시가 같은 표로 선차단한다(이중 방어).
#
# 등급은 누적이다: viewer ⊂ editor ⊂ admin.
_EDITOR_OPS = frozenset({
    "pin", "tag", "touch", "activate",
    "ingest-fragment", "ingest-pack",
    "doc-put", "doc-put-file", "doc-extract", "doc-reembed",
    "evidence-backfill", "conventions-build",
})
_ADMIN_OPS = frozenset({
    # 되돌릴 수 없는 것들 — 남의 작업을 지우거나 합치는 연산이다.
    "delete", "delete-edge", "merge-nodes", "doc-delete", "domain-rename",
})
_TEAM_ROLES = ("viewer", "editor", "admin")

#: 팀 그래프에서 일하면 등급은 **Team** 이다 — 사람이 아니라 **공간**이 등급을 정한다.
#: 전에는 팀 주소로 들어와도 계정이 준 개인 등급이 그대로 쓰였고, 그래서 와이어 값
#: `team` 을 실제로 내는 곳이 **한 군데도 없었다**. 두 등급의 한도가 같던 동안에는
#: 아무 증상이 없었지만(Free·Pro 둘 다 2,000), Team 이 6,000 이 되는 순간 그것은
#: "팀이 팀 한도를 못 받는다"가 된다. 소유자가 이미 `team:` 으로 확인된 자리라
#: 여기서 단정해도 틀릴 수 없다. 카운터도 owner 단위라 6,000 은 **팀이 나눠 쓴다**.
#: 근거: [[decision:the-plans-are-free-and-team-and-the-axis-is-the-call-limit]]
_TEAM_PLAN = "team"


def _role_allows(role: str, op: str) -> bool:
    """팀 역할이 이 op 를 할 수 있는가. 모르는 역할은 **거부**(fail-closed)."""
    if role not in _TEAM_ROLES:
        return False
    if op in _ADMIN_OPS:
        return role == "admin"
    if op in _EDITOR_OPS:
        return role in ("editor", "admin")
    return op in _PROXY_OPS      # 나머지는 조회 — 화이트리스트에 있으면 열람자도 가능


#: 스페이스 주소. `/t/<uuid>/mcp` 는 팀 그래프, `/mcp` 는 개인.
#:
#: **URL 은 의도, 자격증명은 권한.** 주소가 어느 그래프인지 말하고, 키가 들어갈 수
#: 있는지와 어떤 역할인지를 증명한다. 그래서 팀 id 를 안다는 것만으로는 아무것도
#: 못 하고, 키 하나로 모든 스페이스를 쓸 수 있다(스페이스마다 키를 발급할 필요가 없다).
#: 근거: [[decision:the-endpoint-carries-the-space-and-the-credential-carries-the-right]]
#: 스페이스 id 의 모양. 주소(`/t/<uuid>/mcp`)와 인자(`space`) **양쪽이 같은 것을
#: 쓴다** — 두 벌로 두면 한쪽만 조여도 다른 쪽이 뚫린 채 남는다.
_UUID = (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
         r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_UUID_RE = re.compile(r"\A" + _UUID + r"\Z")
_SPACE_PATH_RE = re.compile(r"\A/t/(" + _UUID + r")(/.*)?\Z")


def split_space(path: str) -> tuple[str | None, str]:
    """`/t/<uuid>/mcp` → (uuid, "/mcp"). 스페이스가 없으면 (None, path).

    ⚠ **모양이 틀린 주소는 여기서 스페이스가 아니게 되고, 뒤에서 404 가 된다.**
    400 이 아니라 404 인 것이 요점이다 — 형식 오류와 "그런 팀 없음"이 구분되면
    팀 id 를 추측해 조직 구조를 훑을 수 있다."""
    m = _SPACE_PATH_RE.match(path or "")
    if not m:
        return None, path
    return m.group(1).lower(), (m.group(2) or "/")


def team_role_tiers() -> dict[str, frozenset[str]]:
    """역할 표 전량(드리프트 방지 테스트가 _PROXY_OPS 와 대조한다).

    새 op 를 추가하면서 역할을 안 정하면 조회로 조용히 분류된다 — 쓰기 op 가 열람자에게
    열리는 사고가 그렇게 난다. 테스트가 그 침묵을 깨는 역할을 한다."""
    viewer = frozenset(_PROXY_OPS) - _EDITOR_OPS - _ADMIN_OPS
    return {"viewer": viewer, "editor": _EDITOR_OPS, "admin": _ADMIN_OPS}


# 커넥터 아이콘 래스터 — 정본: app/src-tauri/icons/128x128.png(앱 아이콘과 동일 파일,
# design-hub 의 링 플레이트에서 `tauri icon` 이 뽑는다).
# base64 로 심는 이유는 SVG 와 같다: 사이드카/MCP 는 단일 바이너리라 외부 자산 경로가
# 없다. **이 리포에서 로고를 인라인해도 되는 유일한 자리다.**
# ⚠ 그래서 사본이 조용히 낡는 것을 주석이 아니라 테스트가 막는다 —
# tests/test_mcp_icons.py 의 EmbeddedIconMatchesCanon 이 이 둘을 정본과 바이트로 대조한다.
# 다시 뽑는 법: design-hub에서 npm run logo:build → python -X utf8 scripts/sync_out.py --apply
_FAVICON_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAAIVUlEQVR42u1dLVcbTRS+SRGtgjocwdURXB3B4QgOR3Ctgte9rtTVkf4CQBVUgyuKoCqBX0BwOKiro/vkDD1LOrPJ7M6GnZnnOWdOOEl2ssy9c7/n7oyUg0YyVtRrKxlzyWgKYYN+6nWQjAv16hQ1h3OBwFvJaCvCE+4BBugl4ygZV1VhABB9lzt86gADdBUzvAgDYKfvc7dXQir8pyTDVBgABD9Qup2ols2wbWsn1C1/ZCcZlyR+JdFStOmUJQEObCcnXgyHShqMxasJvgMX7mcy1riu3qCpxlkyfheRACD+OS18rz2F1WQ85JUAP0l8rzGfjHfJOMnDAAcU+0HgnfLcTm0YAIGd/7l2QdkEt7roYc3w5XOl/4lwADtgeTROoIsD7JP4QWJOqXXJYoAOgzwSerCobVIB4JAb7n6JIXewqJMAbRI/CjTSEd20BLhhZk9iChAtpyUAizjiDBU/YwAiLnTSKuCe+j9OY3AmVbRJxGcMNmbo98cdF5gRZvuilgJggAWuAyUAESngBTxyGeLFDJeADECQAQgyAEEGIMgAUWBra0uazeZwAFdXV8NxdHQkdAMDRqvVkoODA2k0GtrPB4OBbG9vS7/fj4YB6rH8o58+fZLz83Mj8QF8hu/gu5QAAQG7vtPpWF1zeHg4lAahAwdD9kL+B7vdrnz48MH6OtgHb9++lR8/flAC+Arseuz+IoAUgDQgA3gG7ODLy0sncy0vLw+9BBqBnmBubk6+f//ubD7MhTkpATwBCNZuZ9e5Xl9f/93VkBZLS0uZ3+/1erKxsUEJ4IPezyL+xcWFrK6uDomO72Lgb7yHz0zAnLaeBCXAlAE/HnrfJK4/f/4se3vZTg8+N8UBHh4ehvYAAkZkgAoCxH8K7xax5rO8B6gNMAHjABUDdu7m5qZx5yMeMClA5FqtNgwdj2J+fn74WSjh4iAkAAiFEK4OSPDk1d0w/NbX17WfwWYIgQm8ZwDoe4h+XYwflj6YA7o779wgss5DgB0AVZB3bnoB4i7OryP+r1+/hju/CIFwLebAXDqDs2iUkQxQosu3u7vrJHqHOTBXqK6htyogy+UrovclIzuIQpLQXENvGcDk8hXV+3nsAZ9dw7qvLp+O+C70fh57APcyLsBECTAFl28aqdusIJGPrmHdN5fPtPhfv36dSt4ev4HfMnkkvmUNvZIApiwf9L4pBFwWoPd19oBvpWR1310+6ORxqd8ygN/U2QPjspGUAI5dPuToEbJ9CYDQusITGIyLi4teRAnrvlj9OuJDF9sSH6oCuvr+/l4eHx+HA3/jPVs1gt/W2QO4V5vkEyXAmN1/c3Pzz/u3t7eZNf5iOBswST0Asoc2QBBoYeHfRiuQAlUPEFU+HYydpNuZEP02iwvCTuKrw820TffCINRFHlFW/lLqKQgJAFEK8Syasi5drt5lhbBtJTAYZmVlRcsEVbYF6lUv7TbtZhuYkjkurzHd07Td06AYwLTLbbN8pqIOl9eY7slGUpEBNAagzu+3Fal5onO21+CeTHUDwv4A7gC3jfcWiQTQWfnYmba7U7czXV9juq+qu4GVZgCTK5YnYCM5CkJdGKxVzw566QZiUZF6rZIbiBS1zuCjGyjFijB0fXuw0DbWNQhpE93Dd22Ib7of3HvV8wHehoKhWxFqtfXVx7V/meT42Chwfzprn6FgR1IACzmqY6EebDt4QHWcnp7KmzdvhnO+fv36r8F3cnIyzOMfHx+Lbah6bW1Nu/t9aCzhTToYInl2dlZ8SAeDoXDPTAc7dAdNodmstm9lM6WpPK2swtSoK4IgTiG+y+4GIgW7hkD0Vz0D6G1RKHYW6gB0bt40CzBMKWrcW57EExnAsjZfh52dnakc08Jv4Ld8F/3e9geAPWA6u4/3zs7O5O7uTspKT3/79u2v9zDqPvrYTs7bo2Gmsmy8jyhhGUfDEO0zHUeret4/uNPBprJsEGJ/f9/572FO03E0n8rAg2GALNfwqfuXS71vmg/34HPTKO87hJjauEAFQBUU7RGAXQ/Rr3P54Jb6vPuDaREDIuvKsovaA1l6Hy4f3meLmAq7hkXtgazDIj66fMG2ictyDUHAPG3dkBE0tZn31eULvlGkyTUUh40ifXb5gu8VbHINn8T5JI+CwXdMxPfd5Qv+iSHQyYgCmoj0VLkDA27UdXt6oFSW+/jx48fgHigVZLv4rA6faXWRbhc/TqyH4PJFwwBZrmEehOLyRfPEEBDK5W7FXCESP+jnBkICuOjVgzlCfV5Q8I+NA+FQOPr+/ftc16P7x5cvX0T42Di/YWrzmoUy2s1SArygV2CKFIoh0udbaRclgEx2ggfSwOQdwNrHro/p4dFRMYCkQr26x8eH/IRQMgAhQTSIIMgABBmAIAMQZABCnOQCrrkM0eICEuCB6xC3BOhzGaJFHxJgwHWIFgNEAhvJuOFaRInFupIAt1yL6ACaD54qgnpcj+jQS5eEHXI9osNhmgGuGA+ICteK5s+KQrtcl2jQTdcDPHMLkrHA9Qne+GuIoSx8l+sTPHZHK4JG0U/GCtcpSFwkozWOARrKQJjlegUFHJtujkZ+dSeDBlQFwYr+gWjOBegACbCoOIbwH0em8x81GR8tWuf6eQ102G5nlYVnYU4ZhUtcR/E14NPKqvkYdzr4QU1wyrX0cue3xhX8vJpgot/JOKZN4J3O31S0E1f9ATrJ2FbuBFFdV29b0UomPRpmi4bKJDFYJJUL8nRsK7zydAgZKN2yISwkkYrE9jcUTQaS43BoUXRUkIGewvQt/G7RWo6awxtqKmZoM6NY6m7vKaJfuToeXgYaSiQ1UskH2gz2Ol1UHGaQenWKPyVIuay5Q7JCAAAAAElFTkSuQmCC"
)

# 커넥터/브라우저 아이콘 — 정본: design-hub/assets/logo/vinc-ring-circle.svg
# (원형 플레이트. 스펙이 파비콘·아바타에 지정한 변형이고, 자기 바탕을 들고 있어
# 커넥터 목록의 밝은 배경과 어두운 배경 어디에 놓여도 읽힌다.)
# 임베드 이유와 드리프트 방지는 위 _FAVICON_PNG 주석과 같다.
_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180"><circle cx="90" cy="90" r="90" fill="#000000"/><g transform="translate(39.86 24.43) scale(0.19286)"><path fill-rule="evenodd" fill="#FFFFFF" d="M328.4 155.7L420.5 284.7Q460.0 340.0 420.5 395.3L328.4 524.3Q260.0 620.0 191.6 524.3L99.5 395.3Q60.0 340.0 99.5 284.7L191.6 155.7Q260.0 60.0 328.4 155.7Z M312.2 203.4L383.9 297.8Q416.0 340.0 383.9 382.2L312.2 476.6Q260.0 545.2 207.8 476.6L136.1 382.2Q104.0 340.0 136.1 297.8L207.8 203.4Q260.0 134.8 312.2 203.4Z"></path><circle cx="260" cy="340" r="52" fill="#FFFFFF"></circle></g></svg>"""


class MCPHTTPApp:
    def __init__(self, bridge: Bridge, *, static_token: str | None = None,
                 oauth: OAuthGate | None = None, verify_key=None,
                 verify_member=None,
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
        # 쿼터(B-D2): usage 카운터는 store 에 영속. quota_config 없으면 무제한
        # (기존 테스트·로컬 프로파일 무영향) — cli 가 env 로 명시 활성화한다.
        self.store = store if store is not None else (oauth.store if oauth else None)
        self.quota_config = quota_config
        # 무료 등급 주제 상한(AP-1 §3.4). None = 기능 꺼짐. 등급→숫자 변환이 **여기**
        # 한 곳에서만 일어나고 사이드카는 숫자만 받는다(정책 무지 유지).
        self.free_max_domains = free_max_domains
        # Dispatcher 하나를 공유한다(세션별 dict·락·FIFO 축출은 걷어냈다 — 지키던
        # 상태를 읽는 프로덕션 코드가 0건이었고, 스펙이 요구하는 프로토콜 검증은
        # **무상태**다: `_check_protocol_version`). ⚠ 세션 저장소를 되살리려거든 먼저
        # 레플리카가 둘 이상이라는 것을 볼 것 — 프로세스 로컬이라 같은 세션 id 가
        # 레플리카를 넘나든다([[vinc.plan.mcp-second-pass]]).
        # 허브는 **이미 원격 그래프 자신**이라 `target` 을 내보내지도 받지도 않는다.
        self._dispatcher = Dispatcher(self.bridge, allow_target=False)

    def quota_check(self, owner: str, plan: str | None) -> dict | None:
        """소비 1회 시도. 허용이면 None, 초과·잠금이면 **tools/call 결과 dict**.

        카운터는 owner(public_id) 단위 — 키를 여러 개 만들어도 합산된다(우회 불가).
        `exempt_owners` 에 든 owner 는 세지 않는다(잠금도 보지 않는다).
        """
        if not self.quota_config or self.store is None or not owner:
            return None                              # 미설정/로컬 = 무제한
        cfg = self.quota_config
        if owner in cfg.get("exempt_owners", ()):
            return None                              # 면제 owner — 도그푸딩 계정
        paid = plan in entitlement.PAID_PLANS
        limit = cfg["team_limit"] if paid else cfg["free_limit"]
        r = self.store.quota_check(owner, limit=limit, window_s=cfg["window_s"],
                                   lock_s=cfg["lock_s"])
        if r["allowed"]:
            return None
        secs = r["retry_after_s"]
        hh, mm = secs // 3600, (secs % 3600) // 60
        tier = "Team" if plan == "team" else "Free"
        # 업셀 문구는 **무료와 유료의 한도가 실제로 다를 때만** 쓴다. 2026-09-19 에
        # 차별축이 다시 호출 횟수가 되어(Free 2,000 · Team 6,000) 대개 다르지만, 두
        # 값을 같게 설정한 배포에서는 "더 높은 한도"가 거짓말이 되므로 입을 다문다.
        # 그때 댈 다른 근거도 없다 — 주제 상한이 사라져 Free 와 Team 이 개인 기능에서
        # 같아졌기 때문이다.
        if paid or cfg["free_limit"] >= cfg["team_limit"]:
            upsell = ""
        else:
            upsell = " A team raises the limit: https://vincs.io/pricing"
        text = (f"{tier} tier limit reached ({limit} tool calls per "
                f"{int(cfg['window_s'] // 3600)}h). Access resumes in ~{hh}h{mm:02d}m."
                + upsell)
        return {"content": [{"type": "text", "text": text}], "isError": True,
                "_meta": {"vinc_quota": {"limit": limit, "retry_after_s": secs,
                                         "upgrade_url": "https://vincs.io/account"}}}

    def entitlements_for(self, plan: str | None) -> dict:
        """등급 → 사이드카가 받을 **숫자**. 정책 해석은 여기서 끝난다(AP-1 §3.4).

        유료 등급이거나 상한이 미설정이면 빈 dict = 무제한이다.
        """
        if self.free_max_domains is None or plan in entitlement.PAID_PLANS:
            return {}
        return {"max_domains": self.free_max_domains}

    def dispatcher(self) -> Dispatcher:
        """공유 Dispatcher. 세션별 인스턴스가 없어졌으므로 인자도 없다(위 __init__ 참조)."""
        return self._dispatcher

    def resolve_auth(self, header: str | None, space: str | None = None,
                     *, presented_at: str = "/mcp") -> dict:
        """Bearer 를 해석해 {ok, owner, plan, scope, source[, role, member]}. 스탬핑의 출처.

        `scope`(W18, [[decision:scoped-key-tier]]): 'full' 정확일치만 쓰기를 연다.
        키 경로는 verify_key 가 실어 준 값을 그대로 쓰되 **부재는 'read'** 다
        (fail-closed — 스코프를 모르는 채 쓰기를 여는 것이 최악의 고장이다).
        static·oauth 는 스코프 개념 이전의 신원이라 'full'(기존 동작 무변화).

        ``space``(팀 uuid)가 오면 **키와 OAuth 둘 다** 통과한다(#120). 전에는 키
        전용이었는데, 그 제약의 근거는 `resource_metadata` 가 `issuer + "/mcp"` 로
        하드코딩돼 팀 주소의 커넥터가 자기와 안 맞는 resource 를 발견한다는 것이었다.
        그 메타데이터가 경로 인식이 되면서(`OAuthGate.resource_metadata(path)`) 제약의
        근거가 사라졌다.

        ⚠ 이 제약을 푸는 것이 왜 중요한가: claude.ai 커스텀 커넥터는 임의 헤더를 못
        넣는다. 키 전용이면 팀 그래프는 `--header` 를 쓸 수 있는 Claude Code 에서만
        닿고, 그건 사실상 "팀은 개발자만 쓸 수 있다"가 된다. 그리고 그 불편이
        "개인 커넥터에서 팀도 되게 해 달라"는 요구를 만드는데, 그 요구를 들어주면
        스페이스가 도구 인자가 되어 팀 쓰기가 조용히 개인 그래프에 쌓인다. **주소 =
        스페이스** 불변식을 지키면서 같은 불편을 없애는 길이 이쪽이다.

        OAuth 팀 경로의 규칙은 키 경로와 **같다**:
        - 토큰이 유효해도 그것만으로는 아무것도 아니다. `verify_member` 로 계정에
          "이 사람이 그 팀의 활성 멤버인가"를 따로 묻는다(키 경로가 verify_key 에
          team 을 실어 묻는 것과 같은 질문, 다른 라우트).
        - 실패는 **개인으로 떨어지지 않는다.** 거절이다.
        - `role` 이 함께 와야 하고, 없으면 거절이다(역할 게이트의 입력이다).

        정적 토큰은 여전히 못 쓴다 — owner 가 아예 없어 멤버십을 물을 대상이 없다.
        관리 키 부트스트랩으로 발급된 OAuth 토큰도 같다(owner=None).

        - static: 정적 토큰 = 단일 테넌트 하위호환(owner=None → bridge.owner 사용).
        - apikey: `vinc_sk_*` → verify_key 루프백 → owner=user:{public_id}, plan.
        - oauth : 발급 토큰 → 코드에 스탬핑된 owner/plan(회원 경로면 채워짐).
                  등급은 **동결돼 있으므로** `_fresh_plan` 이 주기적으로 되묻는다.
        """
        miss = {"ok": False, "owner": None, "plan": None, "scope": None, "source": None}
        if not header or not header.startswith("Bearer "):
            return miss
        if space:
            # 팀 주소. 키와 OAuth 둘 다 받되 **개인으로 떨어지지 않는다** — 실패는 실패다.
            tok = header[7:].strip()
            if tok.startswith(entitlement.KEY_PREFIXES):
                if self.verify_key is None:
                    return miss
                ent = self.verify_key(tok, space)
                if not ent or not str(ent.get("owner") or "").startswith("team:"):
                    return miss
                return {"ok": True, "owner": ent["owner"], "plan": _TEAM_PLAN,
                        "scope": ent.get("scope") or "read",
                        "source": "apikey", "role": ent.get("role"),
                        "member": ent.get("member")}
            # OAuth(#120). 토큰이 유효한 것만으로는 아무것도 아니다 — 그 토큰의 사람이
            # 이 팀의 활성 멤버인지 계정에 **따로** 묻는다. 정적 토큰은 여기 못 온다
            # (아래 static 분기는 space 가 없을 때만 닿는다) — owner 가 없어 물을
            # 대상이 없기 때문이고, 관리 키로 발급된 OAuth 토큰(owner=None)도 같다.
            if self.oauth is None or self.verify_member is None:
                return miss
            rec = self.oauth.resolve(tok)
            owner = (rec or {}).get("owner") or ""
            if not owner.startswith("user:"):
                return miss
            if not self._resource_ok(rec, presented_at):
                return miss
            mem = self.verify_member(owner.split(":", 1)[1], space)
            if not mem or not str(mem.get("owner") or "").startswith("team:"):
                return miss
            if not mem.get("role"):
                return miss   # 역할 없이 통과시키면 role_allows 가 전부 거부하거나
                              # (조용한 고장) 빈 문자열이 표를 비켜 간다.
            # scope 가 'full' 인 것은 OAuth 가 스코프 개념 이전의 신원이라서다(개인
            # 경로와 같다). 팀에서 무엇을 할 수 있는지는 scope 가 아니라 **role** 이
            # 정한다 — 그 게이트는 do_POST 에 있다.
            return {"ok": True, "owner": mem["owner"], "plan": _TEAM_PLAN,
                    "scope": "full", "source": "oauth", "role": mem["role"],
                    "member": mem.get("member")}
        token = header[len("Bearer "):].strip()
        if self.static_token and secrets.compare_digest(token, self.static_token):
            return {"ok": True, "owner": None, "plan": None, "scope": "full",
                    "source": "static"}
        if token.startswith(entitlement.KEY_PREFIXES) and self.verify_key is not None:
            ent = self.verify_key(token)
            if ent:
                return {"ok": True, "owner": ent["owner"],
                        "plan": ent.get("plan"),
                        "scope": ent.get("scope") or "read",
                        "source": "apikey"}
            return miss
        if self.oauth is not None:
            rec = self.oauth.resolve(token)
            if rec:
                if not self._resource_ok(rec, presented_at):
                    return miss
                owner = rec.get("owner")
                return {"ok": True, "owner": owner,
                        "plan": self._fresh_plan(owner, rec.get("plan")),
                        "scope": "full", "source": "oauth"}
        return miss

    def _resource_ok(self, rec: dict, presented_at: str) -> bool:
        """RFC 8707 audience — 토큰은 **자기가 발급된 끝점에 제시**돼야 한다.

        ⚠ 여기서 보는 것은 요청이 **도착한 주소**지 `space` 인자가 아니다. 2026-09-22 에
        그 둘을 섞어서, 개인 주소(`/mcp`)로 붙은 커넥터가 `space` 인자로 팀을 고르면
        기대값이 `/t/<uuid>/mcp` 로 잡혀 **모든 팀 호출이 "no such space" 로 떨어졌다**.
        증상이 고약했던 것은 `vinc_whoami` 가 계정에 묻는 다른 경로라 팀 목록을 계속
        보여 줬다는 점이다 — 목록에는 있는데 접근만 안 되니, 스페이스가 사라진 것처럼
        읽힌다. 정적 키는 이 분기를 안 지나서 멀쩡했고, 그 대비가 원인을 갈랐다.

        스페이스는 주소가 아니라 **인자**다
        ([[decision:the-space-is-an-argument-and-the-default-is-personal]]). 그러니
        개인 주소로 발급된 토큰이 팀을 고르는 것은 정상이고, 그때 검사해야 하는 것은
        "이 토큰이 이 끝점에 와도 되는가" 하나뿐이다. 멤버십은 그 다음에 `verify_member`
        가 계정에 따로 묻는다 — 이 검사는 그 자리를 대신하지 않는다.

        빈 `resource` 는 통과한다. 구 커넥터는 이 파라미터를 안 보내고, 없는 것을
        불일치로 읽으면 이미 연결된 것들이 전부 끊긴다.
        """
        bound = (rec.get("resource") or "").strip()
        return not bound or bound == self.oauth.issuer + presented_at

    def _fresh_plan(self, owner: str | None, frozen: str | None) -> str | None:
        """OAuth 토큰에 **동결된** 등급을 주기적으로 계정에 되묻는다.

        승인 시점의 등급이 인가 코드 → 액세스 토큰 → 리프레시 토큰으로 복사되므로,
        결제로 등급이 올라가도 이미 연결된 커넥터는 옛 값을 영원히 들고 있다
        (갱신조차 옛 값을 재동결한다). `owner_plan` 표를 캐시로 쓰고 TTL 이 지나면
        계정에 재조회한다.

        실패는 **마지막으로 알던 값으로 폴백**한다(fail-open to last-known):
        계정이 잠깐 흔들렸다고 유료 사용자를 무료로 떨어뜨리면 안 된다. 이건
        인증이 아니라 자격이라 위험 방향이 반대다.
        """
        if not owner or self.store is None or not owner.startswith("user:"):
            return frozen
        cached, age = self.store.get_owner_plan_age(owner)
        if cached is not None and age is not None and age < entitlement.plan_ttl():
            return cached
        fresh = entitlement.lookup_plan(owner[len("user:"):])
        if fresh is None:
            return cached if cached is not None else frozen
        self.store.set_owner_plan(owner, fresh)
        return fresh

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
        payload = edverify.verify_assertion(self.account_public, assertion or "", max_age=120)
        if not payload or payload.get("typ") != "purge" \
                or payload.get("iss") != "account" or payload.get("aud") != "mcp":
            raise PermissionError("invalid or expired purge request")
        if not self._body_matches(payload, raw_body):
            raise PermissionError("request body does not match the signed digest")
        if not self._claim_jti(payload):
            raise PermissionError("this approval was already used")
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise ValueError("purge request is missing a subject")
        return self.bridge.request("POST", "/sync/purge-owner", body={"owner": f"user:{sub}"})

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
        payload = edverify.verify_assertion(self.account_public, assertion or "", max_age=120)
        if not payload or payload.get("typ") != "purge-team" \
                or payload.get("iss") != "account" or payload.get("aud") != "mcp":
            raise PermissionError("invalid or expired team purge request")
        if not self._body_matches(payload, raw_body):
            raise PermissionError("request body does not match the signed digest")
        if not self._claim_jti(payload):
            raise PermissionError("this approval was already used")
        graph_owner = payload.get("graph_owner")
        if not isinstance(graph_owner, str) or not _TEAM_OWNER_RE.fullmatch(graph_owner):
            raise ValueError("team purge request has an invalid graph owner")
        return self.bridge.request("POST", "/sync/purge-owner", body={"owner": graph_owner})

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
        """이 어서션을 처음 쓰는가. `jti` 는 **필수다**(근거는 `_body_matches` 주석)."""
        jti = payload.get("jti")
        if not isinstance(jti, str) or not jti:
            return False
        now = time.time()
        seen = getattr(self, "_seen_jti", None)
        if seen is None:
            seen = self._seen_jti = {}
        for k, exp in [(k, e) for k, e in seen.items() if e <= now]:
            seen.pop(k, None)
        if jti in seen:
            return False
        # 창보다 조금 길게 잡는다 — 시계 어긋남으로 만료가 앞당겨지면 재생이 열린다.
        seen[jti] = now + 180
        return True

    @staticmethod
    def _body_matches(payload: dict, raw: bytes | None) -> bool:
        """서명된 지문과 받은 본문이 같은가.

        어서션은 owner 와 op 를 묶지만 **인자는 서명되지 않은 본문에서 읽힌다.** 그래서
        잡은 어서션을 유효 창 안에 다른 인자로 재생할 수 있었다(감사 2026-08-18 M2).

        `bh` 는 이제 **필수다.** 이 자리는 예전에 "없으면 통과"였고, 그 주석이 스스로
        "필수로 올리는 것은 별도 변경"이라 적어 두었다 — 이것이 그 변경이다. 넘어온 조건:

          · 계정이 `bh`·`jti` 를 서명에 실은 것은 `bc22584`(2026-08-20)이고, 그 뒤
            account-deploy 가 여러 번 성공했다(최근 2026-09-02). 2주째 라이브다.
          · 이 세 경로(`/internal/purge-owner`·`purge-team`·`graph-owner`)를 부르는
            것은 **계정과 테스트뿐**이다(전 리포 grep). 다른 소비자가 없다.

        그래서 "구 계정이 아직 안 보낼 수 있다"는 전제가 더는 참이 아니고, 통과시키는
        쪽이 오히려 위험하다: `/internal/*` 은 공개 도달 가능하고 남은 관문이 서명
        하나뿐이라(감사 M2), 지문 없는 어서션을 받아 준다는 것은 잡은 어서션을 유효
        창 안에 **다른 인자로** 재생할 여지를 남겨 두는 것이다.

        ⚠ 되돌릴 일이 생기면(계정을 `bc22584` 이전으로 롤백하는 경우뿐이다) 여기를
        `return True` 로 되돌리는 것이 아니라 **계정을 먼저 되돌린다.** 순서를 뒤집으면
        그 사이 창이 곧 구멍이다."""
        want = payload.get("bh")
        if not isinstance(want, str) or not want:
            return False
        got = base64.urlsafe_b64encode(
            hashlib.sha256(raw or b"").digest()).rstrip(b"=").decode("ascii")
        return secrets.compare_digest(got, want)

    def graph_owner_request(self, assertion: str, args: dict,
                            raw_body: bytes | None = None) -> dict:
        """계정 서명 어서션을 검증해 owner 를 **강제**하고 op 를 사이드카로 프록시한다.

        받는 유형 둘(AP-1 §4.3):
          - ``typ=proxy``      개인 그래프. owner = ``user:{sub}`` (기존, 바이트 동일).
          - ``typ=proxy-team`` 팀 그래프. owner = 서명된 ``graph_owner``, ``role`` 로 op 판정.

        **팀을 별도 유형으로 가른 것이 이 함수의 핵심 안전장치다.** 기존 ``proxy`` 에
        필드만 덧붙였다면, 구 허브 + 신 계정 조합에서 팀 작업이 조용히 **멤버의 개인
        그래프로** 흘러갔을 것이다(팀 문서가 개인 그래프에 쌓이고 팀 화면은 빈 결과 —
        오류가 아니라 무음 오동작이라 최악이다). 유형이 다르면 구 허브는 아래 검사에서
        그냥 거부하므로, 배포 순서를 틀렸을 때의 결과가 "정확성 문제"에서 "팀 기능이
        아직 안 열림"으로 강등된다.

        owner 는 어느 경우에도 서명 페이로드에서만 온다 — args 의 owner 는 pop 한다."""
        if not self.graph_enabled():
            raise PermissionError("graph proxy is not configured")
        payload = edverify.verify_assertion(self.account_public, assertion or "", max_age=120)
        typ = payload.get("typ") if payload else None
        if not payload or typ not in ("proxy", "proxy-team") \
                or payload.get("iss") != "account" or payload.get("aud") != "mcp":
            raise PermissionError("invalid or expired proxy request")
        # 서명된 지문과 받은 본문이 같은가 · 이 어서션을 처음 쓰는가(감사 M2).
        if not self._body_matches(payload, raw_body):
            raise PermissionError("request body does not match the signed digest")
        if not self._claim_jti(payload):
            raise PermissionError("this approval was already used")
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise ValueError("proxy request is missing a subject")
        op = payload.get("op")
        route = _PROXY_OPS.get(op) if isinstance(op, str) else None
        if route is None:
            raise ValueError(f"unsupported proxy op: {op!r}")
        method, path = route

        if typ == "proxy-team":
            graph_owner = payload.get("graph_owner")
            if not isinstance(graph_owner, str) or not _TEAM_OWNER_RE.fullmatch(graph_owner):
                raise ValueError("team proxy request has an invalid graph owner")
            role = payload.get("role")
            if not isinstance(role, str) or not _role_allows(role, op):
                # 역할이 모자란 것도, 역할이 이상한 것도 같은 거부다(fail-closed).
                raise PermissionError(f"role {role!r} may not perform {op!r}")
            owner = graph_owner
        else:
            owner = f"user:{sub}"
        args = dict(args or {})
        args.pop("owner", None)   # 클라/바디 owner 는 절대 신뢰하지 않는다
        # 어휘(theme)는 **전역**이라 남의 theme_id 를 실어 보내면 그 사람 어휘에 내
        # 타입이 꽂힌다([[record:risk-themes-global]]). 이 평면은 파일에서 온 조각을
        # 그대로 펼치므로 여기서 떨군다.
        # ⚠ **이것으로 닫히는 것은 웹 평면뿐이다** — 호스티드 MCP 는 이 함수를 아예
        # 지나지 않는다. 여기 한 줄을 근거로 "테넌시 막았다"고 적지 말 것.
        args.pop("theme_id", None)
        # `debug` 는 내부 실행 계획(Cypher·파라미터)을 싣는 **로컬 진단용** 스위치다.
        # 웹 평면은 쿼리 인자를 그대로 흘리므로 여기서 벗긴다 — 허브에서 남에게 스토어
        # 내부를 보여 줄 이유가 없다. 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] S13.
        args.pop("debug", None)

        if op == "job":   # /jobs/{id} 폴링 — id 는 토큰꼴만(경로주입·타 라우트 차단)
            jid = str(args.get("id") or "")
            if not _JOB_ID_RE.fullmatch(jid):
                raise ValueError("invalid job id")
            return self.bridge.request("GET", f"/jobs/{jid}")
        if op == "activate":   # 클라가 호스트 임의 파일경로 지정 못 하게(활성 팩 인라인)
            args.pop("out", None)
        args.pop("_entitlements", None)   # 바디로 들어온 자격은 신뢰하지 않는다
        if method == "GET":
            return self.bridge.request("GET", path, params={**args, "owner": owner})
        body = {**args, "owner": owner}
        # 주제 상한(AP-1 §3.4). **plan 클레임이 없으면 상한을 걸지 않는다** — 클레임을
        # 안 보내는 구 계정 + 이 허브 조합에서 부재를 free 로 읽으면 유료 사용자까지
        # 상한에 걸린다. 배포 순서가 돈 낸 기능을 뺏는 꼴이라, 모를 때는 열어둔다.
        if op in _CAPPED_PROXY_OPS and payload.get("plan") is not None:
            ent = self.entitlements_for(payload.get("plan"))
            if ent:
                body["_entitlements"] = ent
        # POST: owner 를 바디 최상위에 강제 — fragment.owner·write_* 전부 최상위 owner 를 읽음.
        return self.bridge.request("POST", path, body=body)


#: 한 연결이 아무것도 안 보내며 스레드를 붙들 수 있는 시간(초). `StreamRequestHandler`
#: 가 이 값을 소켓에 건다. 없으면 연결만 열어 두는 클라이언트가 스레드를 영원히 잡고,
#: 그것이 slowloris 다. 정상 요청은 본문 4MB 상한 안에서 이보다 훨씬 빨리 끝난다.
READ_TIMEOUT = max(1, int(os.environ.get("VINC_MCP_READ_TIMEOUT") or 30))

#: 동시에 처리할 연결 수. 넘으면 **503 을 적고 닫는다** — 조용히 끊으면 클라이언트가
#: 재시도 폭풍을 만들고, 큐에 쌓으면 결국 같은 자원을 쓴다. 이 프로세스는 레플리카
#: 하나로 공유 그래프 전체를 서비스하므로(단일 라이터), 스레드 무제한은 곧 메모리
#: 무제한이고 그 끝은 OOM 이다 — 이 리포가 이미 네 번 겪은 고장의 형태다.
MAX_CONNECTIONS = max(1, int(os.environ.get("VINC_MCP_MAX_CONNECTIONS") or 64))


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
                    "Authorization, Content-Type, Mcp-Session-Id, Mcp-Protocol-Version",
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

    def _protocol_version_rejected(self) -> str | None:
        """`MCP-Protocol-Version` 검증. 문제 없으면 None, 있으면 오류 문자열.

        2025-06-18 스펙: 초기화 뒤 클라이언트는 이 헤더를 실어야 하고, 서버는 지원
        하지 않는 값에 400 을 준다. 헤더가 없으면 2025-03-26 으로 가정한다(하위호환).
        전에는 이 헤더 이름이 CORS 허용목록 문자열에만 있고 코드가 읽지 않았다.

        ⚠ 대조 대상은 **지원 목록**이지 '이 세션이 협상한 값'이 아니다. 그래서 이
        검증은 무상태이고, 세션 기계를 되살릴 이유가 되지 못한다(MCPHTTPApp.__init__
        참조). 제대로 동작하는 클라이언트는 협상 결과를 그대로 실으므로 정의상 이
        목록 안이다."""
        want = self.headers.get("MCP-Protocol-Version")
        if want is None or want in PROTOCOL_VERSIONS:
            return None
        return (f"unsupported MCP-Protocol-Version: {want!r} "
                f"(supported: {', '.join(PROTOCOL_VERSIONS)})")

    def do_OPTIONS(self):  # noqa: N802
        """프리플라이트 — 허용 오리진에만 CORS 를 붙여 204."""
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

    def _unauthorized(self, resource_path: str = "") -> None:
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
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            # 상한 초과는 본문을 **읽지 않고** 빈 dict 를 돌려준다(읽으면 그게 메모리 공격
            # 통로다). 상태 코드는 이 함수가 정하지 않는다 — 초과를 오류로 알려야 하는
            # 라우트는 부르기 전에 스스로 본다(`_internal_graph`). 미수신 바이트가 남지만 무해하다 — `protocol_version` 이
            # HTTP/1.0(기본)이라 매 응답 뒤 커넥션이 닫히기 때문이다.
            # ⚠ `protocol_version = "HTTP/1.1"` 로 올리면 keep-alive 가 살아나 이 잔여물이
            # 다음 요청으로 파싱된다(요청 밀수). 그때는 여기서 `close_connection = True`.
            return {}
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        # UTF-8 이 아닌 본문은 **JSON 파싱 실패와 같은 자리로 강등**한다(아래 except 와 동일).
        # 그냥 두면 do_POST 가 UnicodeDecodeError 로 죽어 커넥션이 리셋되고, 프록시 뒤에서는
        # 클라이언트가 400 대신 **503** 을 본다(로그엔 매 요청 스택트레이스). 실제로 호스트가
        # CP949 로 인코딩한 질의를 보내 라이브 허브가 이 경로를 밟았다(2026-08-04).
        rawb = self.rfile.read(n)
        # 지문 대조는 **받은 바이트**로 한다(감사 M2). 파싱된 dict 를 다시 직렬화하면
        # 키 순서·공백이 달라져 보낸 쪽과 다른 값이 나온다.
        self._raw_body = rawb
        try:
            raw = rawb.decode("utf-8")
        except UnicodeDecodeError:
            return {}
        if ctype == "application/x-www-form-urlencoded":
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    # ── 라우팅 ──────────────────────────────────────────────────────────────
    def do_GET(self):  # noqa: N802
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
            if not rest or rest == "/":
                return self._json(200, gate.resource_metadata())
            rspace, rpath = split_space(rest)
            # 실재하는 자원만 문서를 준다. 아무 경로에나 답하면 이 엔드포인트가
            # "그 주소는 있다"를 확인해 주는 탐침이 된다 — 팀 주소의 404 규칙과
            # 같은 이유다. 팀 uuid 의 **존재 여부는 여기서 묻지 않는다**(계정에
            # 물으면 그 자체가 유출이다): 모양이 맞으면 문서를 주고, 실제로 들어갈
            # 수 있는지는 자격증명이 판정한다. 그것이 "주소는 의도, 자격증명은 권한"
            # 이라는 이 설계의 갈래 그대로다.
            if rpath != "/mcp":
                return self._json(404, {"error": "no such resource"})
            return self._json(200, gate.resource_metadata(
                f"/t/{rspace}/mcp" if rspace else "/mcp"))
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
                detail = bridge.health_detail() if hasattr(bridge, "health_detail") else None
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
            return self._json(200, body)
        return self._json(404, {"error": f"no such route: GET {url.path}"})

    def do_POST(self):  # noqa: N802
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
                return self._json(400, {"error": bad})
            # 주소로 온 팀이다. 토큰은 **이 주소**에 묶여 있어야 한다.
            auth = self._auth(space, presented_at=f"/t/{space}/mcp" if space else "/mcp")
            if not auth["ok"]:
                # 팀 주소의 실패는 **404** 다. 401 이면 "그 팀은 있는데 너는 아니다"가
                # 되어 팀 id 를 추측해 조직을 훑을 수 있다. 모르는 팀·비멤버·제명·정지·
                # 모양 틀린 주소가 전부 같은 답을 받아야 한다.
                if space:
                    return self._json(404, {"error": "no such route: POST "
                                                     + url.path})
                return self._unauthorized()
            msg = self._body()
            # JSON-RPC 배치. `PROTOCOL_VERSIONS` 가 2025-03-26 을 광고하는데 그 리비전은
            # 배치를 지원한다(2025-06-18 에서 제거됨) — 전에는 dict 가 아니면 400 이라
            # **광고한 리비전으로 협상한 클라이언트가 400 을 받았다.** 광고를 접는
            # 대신 지원하는 쪽을 골랐다: 목록에서 빼면 그 리비전만 말하는 구형
            # 클라이언트가 끊기는데, 받아 주는 쪽은 아무도 안 끊긴다.
            batch = isinstance(msg, list)
            msgs = msg if batch else [msg]
            if not msgs or any(not isinstance(m, dict) or not m for m in msgs):
                return self._json(400, {"error": "expected a JSON-RPC message"})
            session = self.headers.get("Mcp-Session-Id") or "default"
            # forced_owner: 원격 인증경로가 해석한 소유자 → tools 가 args.owner 를
            # 무시하고 강제 스탬핑(멀티테넌트 봉쇄, B-B2). static/None 이면 하위호환.
            # quota: tools/call 에서만 소비(initialize/tools_list/ping 무료, B-D2).
            # entitlements: 등급에서 해석된 숫자(주제 상한). 원격 인증경로에서만
            # 실린다 — 로컬·정적 토큰은 owner 가 없어 자격 개념 자체가 없다.
            # 스페이스 해석 — **메시지마다** 한다. 팀 주소로 붙었으면 주소가 이미
            # 스페이스를 고정했으므로 인자는 무시하고 요청 단위 auth 를 그대로 쓴다.
            # 개인 주소에서만 `space` 인자가 고르고, 안 실으면 개인이다.
            #
            # ⚠ 게이트가 **요청당 하나**였던 전제가 여기서 무너진다: 배치 안의 메시지가
            # 서로 다른 스페이스를 겨눌 수 있으므로 역할·스코프도 메시지마다 갈린다.
            # 하나로 두면 배치 첫 메시지의 역할이 나머지를 통과시킨다.
            #
            # 멤버십 판정은 **새로 안 짠다** — 같은 resolve_auth 를 다시 부를 뿐이라
            # 키 경로·OAuth 경로·fail-closed 가 전부 그대로다. entitlement 캐시가
            # 왕복을 접으므로 같은 스페이스를 겨눈 배치는 계정을 한 번만 때린다.
            # 근거: [[decision:the-space-is-an-argument-and-the-default-is-personal]]
            def _auth_for(m):
                if space or m.get("method") != "tools/call":
                    return auth
                want = ((m.get("params") or {}).get("arguments") or {}).get("space")
                want = str(want or "").strip().lower()
                if not want or want == "personal":
                    return auth
                if not _UUID_RE.match(want):
                    return None          # 모양이 틀린 것은 계정에 묻지도 않는다
                # 인자로 고른 팀: 요청은 **개인 끝점**에 도착했다. 주소가 아니라
                # 인자이므로 토큰의 audience 는 `/mcp` 여야 맞다.
                got = self._auth(want, presented_at="/mcp")
                return got if got["ok"] else None

            pairs = [(m, _auth_for(m)) for m in msgs]
            # 닿을 수 없는 스페이스는 **전부 같은 답**이다 — 모르는 팀·비멤버·제명·
            # 정지·모양 오류가 갈리면 id 를 추측해 조직을 훑을 수 있다(팀 주소가 404
            # 하나로 답하는 것과 같은 이유). 여기서는 도구 결과 봉투라 isError 다.
            unreachable = next((m for m, a in pairs if a is None), None)
            if unreachable is not None:
                return self._json(200, {
                    "jsonrpc": "2.0", "id": unreachable.get("id"),
                    "result": {"isError": True, "content": [{"type": "text",
                               "text": "no such space. Call vinc_whoami to see the "
                                       "spaces you can reach."}]}})
            # 역할 게이트 — 팀 스페이스에서만. 등급은 주석표에서 유도한다(op 표는
            # 프록시 op 로 키가 잡혀 있어 도구 6개를 못 덮는다). 거절은 브리지를
            # **부르지 않는다** — 그게 이 게이트의 성질이지 상태코드가 아니다.
            # 조건이 `space`(주소)에서 **해석된 owner**로 바뀌었다: 인자로 고른 팀도
            # 같은 게이트를 지나야 하고, 주소로 온 것만 검사하면 그게 우회로가 된다.
            blocked = [(m, a) for m, a in pairs
                       if m.get("method") == "tools/call"
                       and str(a.get("owner") or "").startswith("team:")
                       and not tools.role_allows(
                           a.get("role") or "",
                           ((m.get("params") or {}).get("name") or ""))]
            if blocked:
                bm, ba = blocked[0]
                brole = ba.get("role") or ""
                name = ((bm.get("params") or {}).get("name") or "")
                return self._json(200, {
                    "jsonrpc": "2.0", "id": bm.get("id"),
                    "result": {"isError": True, "content": [{"type": "text",
                               "text": f"role {brole!r} may not call {name}"}]}})
            if space:
                # W2 — `prompts/*` 도 같은 게이트를 지난다. 이 메서드들은 스토어를
                # 조회하므로, "모르는 역할은 거부"라는 규칙을 메서드 하나가 비켜 가면
                # 그 메서드가 팀 스페이스의 우회로가 된다. 등급은 세 번째 표를 만들지
                # 않고 주석표에서 유도한다 — prompts 가 읽는 것은 vinc_neighbors 와
                # vinc_graph 가 읽는 것이라 등급도 그것(viewer)이다.
                # 거절 모양이 위와 다른 이유: prompts 에는 tools/call 의 결과 봉투가
                # 없다. 그 봉투로 답하면 호스트가 프롬프트 대신 도구 결과를 읽는다.
                # ⚠ 여기는 **주소 기준 그대로**다 — prompts 에는 인자가 없어서 스페이스를
                # 고를 수단이 애초에 없고, 그래서 고를 수 있는 척하면 안 된다.
                role = auth.get("role") or ""
                if any(str(m.get("method") or "").startswith("prompts/") for m in msgs)                         and not tools.role_allows(role, "vinc_neighbors"):
                    first = next(m for m in msgs
                                 if str(m.get("method") or "").startswith("prompts/"))
                    return self._json(200, {
                        "jsonrpc": "2.0", "id": first.get("id"),
                        "error": {"code": -32603,
                                  "message": f"role {role!r} may not read prompts"}})
            # W18 — 읽기 전용 키 게이트([[decision:scoped-key-tier]]). 등급은 팀 역할
            # 게이트와 같은 주석표 유도(tool_tier)를 쓴다 — viewer 등급만 읽기다.
            # 'full' 정확일치만 쓰기를 연다: scope 부재·미지 값은 전부 거부다
            # (fail-closed — 집행되지 않는 스코프는 무음 전권이다). 미지 도구명도
            # 여기서 막힌다(tool_tier 가 admin 을 주므로) — 뒤의 -32602 보다 먼저
            # 닫히는 것이 안전측이다. 거절은 브리지를 부르지 않는다. prompts/* 는
            # 조회라 통과한다(스토어 읽기 = viewer 와 같은 등급).
            # 스코프도 메시지별이다 — 스페이스마다 다른 답이 올 수 있다.
            ro_blocked = [m for m, a in pairs
                          if m.get("method") == "tools/call"
                          and a.get("scope") != "full"
                          and tools.tool_tier(
                              ((m.get("params") or {}).get("name") or "")) != "viewer"]
            if ro_blocked:
                name = ((ro_blocked[0].get("params") or {}).get("name") or "")
                return self._json(200, {
                    "jsonrpc": "2.0", "id": ro_blocked[0].get("id"),
                    "result": {"isError": True, "content": [{"type": "text",
                               "text": f"this key is read-only: {name} writes "
                                       "to the graph. Mint a full-access key "
                                       "at https://vincs.io/account"}]}})
            d = self.app.dispatcher()

            def _run(m, a):
                # 자격·쿼터도 **해석된 스페이스**를 따른다. 쿼터는 멤버로 센다: 팀
                # owner 로 세면 팀 하나가 카운터를 공유하고, `_fresh_plan` 이 `user:`
                # 로 시작하는 owner 만 갱신하므로 등급이 영구 동결된다(요금제를 올려도
                # 안 풀린다). `c=` 기본인자로 묶는 이유는 배치 안에서 메시지마다 값이
                # 달라서다 — 클로저로 두면 마지막 값 하나가 전부에 쓰인다.
                ents = (self.app.entitlements_for(a["plan"]) if a["owner"] else None)
                counted = a.get("member") or a["owner"]
                return d.handle(m, forced_owner=a["owner"], plan=a["plan"],
                                quota=(lambda o, pl, c=counted:
                                       self.app.quota_check(c, pl)),
                                entitlements=ents)

            replies = [r for r in (_run(m, a) for m, a in pairs) if r is not None]
            # 전부 notification 이면 본문 없이 202 — 배치도 같다(스펙).
            if not replies:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            # `Mcp-Session-Id` 는 **클라이언트가 준 값을 되돌려 줄 뿐**이다. 우리는
            # 세션을 발급하지도 추적하지도 않는다. 그래도 되돌리는 이유는 지금 붙어
            # 있는 호스트의 관측 동작을 바꾸지 않기 위해서다 — 없앨 이득이 없다.
            return self._json(200, replies if batch else replies[0],
                              {"Mcp-Session-Id": session})
        if url.path in ("/sync/push", "/sync/pull", "/sync/docs-push", "/sync/docs-pull"):
            return self._sync(url.path)
        if url.path == "/internal/purge-owner":
            return self._internal_purge()
        if url.path == "/internal/purge-team":
            return self._internal_purge_team()
        if url.path == "/internal/graph-owner":
            return self._internal_graph()
        return self._json(404, {"error": f"no such route: POST {url.path}"})

    def do_DELETE(self):  # noqa: N802
        """`DELETE /mcp` — 클라이언트 세션 종료. 스펙상 선택이지만 종료를 보내는
        호스트(Codex·Cursor 등)가 501 을 오류로 취급하지 않도록 명시 처리한다.
        인증은 POST 와 동일하게 요구한다 — 인증 없이 아무나 두드리게 둘 이유가 없다.

        서버가 세션을 들지 않게 된 뒤로는(MCPHTTPApp.__init__ 참조) 버릴 것이 없어서
        **성공을 뜻하는 204 를 돌려주는 것 말고 하는 일이 없다.** 스펙상 정당하다:
        세션을 쓰지 않는 서버에게 종료는 no-op 이다. 라우트를 지우지 않는 이유는
        그대로다 — 종료를 보내는 호스트가 405/501 을 오류로 취급하지 않게."""
        url = urllib.parse.urlparse(self.path)
        if url.path != "/mcp":
            return self._json(404, {"error": f"no such route: DELETE {url.path}"})
        if self._origin_rejected():
            return self._json(403, {"error": "origin not allowed"})
        if not self.app.authorized(self.headers.get("Authorization")):
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
        if int(self.headers.get("Content-Length") or 0) > MAX_BODY:
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

    # ── 동기화(S1) — 회원 그래프 push/pull ────────────────────────────────────
    def _sync(self, path: str):
        """`/sync/push|pull` — 사이드카 `/sync/*` 로 프록시.

        **인증**: `resolve_auth` 를 여기서 **다시** 부른다(do_POST 의 /mcp 분기 안에만
        있으므로 새 라우트는 자기 책임). owner 없는 인증(static 토큰 = 단일 테넌트)은
        거부 — 동기화는 회원 스코프 전용이다.

        **쿼터 면제**: Dispatcher(rpc)를 우회하므로 `quota_check` 가 아예 안 불린다.
        의도된 설계 — 무료 15회/일은 도구 호출 한도이지, 동기화가 그걸로 막히면
        기능 자체가 성립하지 않는다(delta 한 번에 수백 노드가 오간다)."""
        auth = self._auth()
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
                out = self.app.bridge.request("GET", "/sync/pull-delta", params={
                    "owner": owner, "since": int(body.get("since") or 0)})
            elif path == "/sync/docs-pull":
                # 문서 정본 채널(D45 P3) — 노드 delta 와 별개(레일이 다르다).
                out = self.app.bridge.request("GET", "/sync/docs-export", params={
                    "owner": owner, "since": int(body.get("since") or 0)})
            elif path == "/sync/docs-push":
                out = self.app.bridge.request("POST", "/sync/docs-apply", body={
                    "owner": owner, "payload": body.get("payload") or {}})
            else:
                out = self.app.bridge.request("POST", "/sync/apply-delta", body={
                    "owner": owner, "bundle": body.get("bundle") or {}})
        except Exception as e:
            return self._json(502, {"error": f"sidecar unreachable: {type(e).__name__}"})
        return self._json(200, out)

    # ── 인증 + 로깅 ───────────────────────────────────────────────────────
    def _auth(self, space: str | None = None, *, presented_at: str | None = None) -> dict:
        """`resolve_auth` 를 부르고 **누가 불렀는지를 응답 로그에 실을 수 있게** 남긴다.

        호출부 셋이 각자 `self.app.resolve_auth(...)` 를 부르는데 owner 를 기억하는
        자리가 없어서, 성공한 요청을 계정에 귀속할 방법이 아무 데도 없었다. 쿼터
        카운터는 현재 창만 들고 아무도 읽지 않고, 로그는 실패만 남았다. 근거:
        [[record:per-account-usage-exists-only-as-the-quota-counter-and-nothing-reads-it]]
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
    충분하고 계정 테이블로 되돌아갈 수는 없다 — 계정 앱의 감사 로그가 이메일에 쓰는
    `subjectTag` 와 같은 모양이다.

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


def serve_http(bridge: Bridge, *, host: str = "127.0.0.1", port: int = 8790,
               static_token: str | None = None, oauth: OAuthGate | None = None,
               verify_key=None, verify_member=None,
               quota_config: dict | None = None,
               mcp_seed: bytes | None = None, account_public: bytes | None = None,
               connect_url: str | None = None, free_max_domains: int | None = None) -> int:
    app = MCPHTTPApp(bridge, static_token=static_token, oauth=oauth,
                     verify_key=verify_key, verify_member=verify_member,
                     quota_config=quota_config,
                     mcp_seed=mcp_seed, account_public=account_public,
                     connect_url=connect_url, free_max_domains=free_max_domains)
    httpd = make_server(app, host, port)
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
