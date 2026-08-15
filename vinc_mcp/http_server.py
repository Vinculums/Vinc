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
import json
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import edverify, entitlement
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


def _role_allows(role: str, op: str) -> bool:
    """팀 역할이 이 op 를 할 수 있는가. 모르는 역할은 **거부**(fail-closed)."""
    if role not in _TEAM_ROLES:
        return False
    if op in _ADMIN_OPS:
        return role == "admin"
    if op in _EDITOR_OPS:
        return role in ("editor", "admin")
    return op in _PROXY_OPS      # 나머지는 조회 — 화이트리스트에 있으면 열람자도 가능


def team_role_tiers() -> dict[str, frozenset[str]]:
    """역할 표 전량(드리프트 방지 테스트가 _PROXY_OPS 와 대조한다).

    새 op 를 추가하면서 역할을 안 정하면 조회로 조용히 분류된다 — 쓰기 op 가 열람자에게
    열리는 사고가 그렇게 난다. 테스트가 그 침묵을 깨는 역할을 한다."""
    viewer = frozenset(_PROXY_OPS) - _EDITOR_OPS - _ADMIN_OPS
    return {"viewer": viewer, "editor": _EDITOR_OPS, "admin": _ADMIN_OPS}


# 커넥터 아이콘 래스터 — 정본: app/src-tauri/icons/128x128.png(앱 아이콘과 동일 파일).
# base64 로 심는 이유는 SVG 와 같다: 사이드카/MCP 는 단일 바이너리라 외부 자산 경로가
# 없다. 128px RGBA, 2.5KB. 로고가 바뀌면 그 PNG 에서 다시 뽑을 것:
#   python -c "import base64;print(base64.b64encode(open(P,'rb').read()).decode())"
_FAVICON_PNG = base64.b64decode(
    "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"
)

# 커넥터/브라우저 아이콘(흰 바탕) — 정본: Vinc-design/assets/logo/vinc-favicon-light.svg
# (흰 스퀘어클 + 정본 두획 V, viewBox "1.9 0 66.4 63"). 사이트의 다크 파비콘과 짝.
# 여기 임베드하는 이유: 사이드카/MCP 는 단일 바이너리라 외부 자산 경로가 없다.
_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="img" aria-label="Vinc">
  <defs>
    <linearGradient id="vtip" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#161618"/>
      <stop offset="0.62" stop-color="#161618"/>
      <stop offset="1" stop-color="#32355C"/>
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="22" fill="#FFFFFF"/>
  <svg x="21" y="23" width="58" height="53.5" viewBox="1.9 0 66.4 63">
    <path fill="url(#vtip)" d="M3.86041 7.35091C2.56731 4.8785 1.92075 3.6423 2.04804 2.69691C2.16092 1.85854 2.59746 1.13737 3.28792 0.64862C4.06651 0.097477 5.44973 0.0971137 8.21617 0.0963872L14.291 0.0947918C15.1057 0.0945778 15.513 0.0944709 15.5751 0.0970306C15.852 0.108439 15.6292 0.0869573 15.9032 0.128671C15.9647 0.13803 16.109 0.166174 16.3977 0.22246C16.5558 0.253299 16.5036 0.548182 16.3487 0.592308C9.52905 2.53495 8.30154 9.5045 9.79969 15.8897C10.1438 17.3277 10.6202 18.7291 11.2226 20.0751C12.215 22.3256 13.4312 24.5429 14.5848 26.7124L18.5321 34.1941L21.834 40.4412C23.6168 43.8629 24.9579 46.7259 27.8544 49.3657C34.5318 55.4509 41.8297 52.0686 46.0951 44.9843C46.1322 44.9227 46.2156 44.9074 46.2712 44.9529C46.3134 44.9873 46.3265 45.0461 46.3024 45.095C45.7979 46.1186 45.0331 47.3751 44.4811 48.4279C42.7332 51.8159 40.9544 55.1872 39.1448 58.541C37.9771 60.7226 37.3932 61.8134 36.7769 62.2292C35.7365 62.931 34.4528 62.9292 33.4144 62.2244C32.7993 61.8069 32.2244 60.7257 31.0747 58.5631L12.3646 23.3702C9.79425 18.4665 7.19672 13.5777 4.57219 8.70416C4.33567 8.25768 4.09842 7.80597 3.86041 7.35091Z"/>
    <path fill="url(#vtip)" d="M58.4066 0.025589C59.3877 0.046639 60.6496 0.0130453 61.5999 0.0607452C61.8525 0.0585323 62.1051 0.0576938 62.3577 0.0558624C63.6175 0.04673 64.5644 0.0399937 65.2943 0.104691C66.0242 0.169399 66.5413 0.305844 66.9398 0.586136C67.6198 1.0645 68.09 1.84095 68.1985 2.66524C68.2621 3.1483 68.1436 3.67035 67.8626 4.34688C67.5816 5.02323 67.136 5.8578 66.5443 6.96895C65.7597 8.44203 64.9704 9.91322 64.1829 11.3801L61.8343 15.7668L54.5306 29.634C54.4879 29.7143 54.3781 29.7264 54.3187 29.6574C54.2837 29.6168 54.2768 29.5591 54.3021 29.5119C55.6688 26.9687 56.9708 24.3768 58.3294 21.8283C59.689 19.2778 61.5373 14.8671 62.2064 10.6447C62.8758 6.41976 62.3625 2.39746 59.0159 0.601761C58.6938 0.428894 58.3159 0.349251 57.9485 0.323441C57.6799 0.304563 57.3916 0.283391 57.2659 0.277542C57.2391 0.276295 57.2133 0.266555 57.1927 0.249222C57.1624 0.223694 57.1486 0.186693 57.1536 0.152542C57.1589 0.117599 57.1843 0.0878384 57.2249 0.0783234C57.3771 0.042775 57.6145 0.0305941 57.8411 0.0265656C57.9548 0.0245464 58.0665 0.0252499 58.1644 0.025589C58.2626 0.0259299 58.3479 0.0264659 58.4066 0.025589Z"/>
  </svg>
</svg>"""


class MCPHTTPApp:
    def __init__(self, bridge: Bridge, *, static_token: str | None = None,
                 oauth: OAuthGate | None = None, verify_key=None,
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
        # 쿼터(B-D2): usage 카운터는 store 에 영속. quota_config 없으면 무제한
        # (기존 테스트·로컬 프로파일 무영향) — cli 가 env 로 명시 활성화한다.
        self.store = store if store is not None else (oauth.store if oauth else None)
        self.quota_config = quota_config
        # 무료 등급 주제 상한(AP-1 §3.4). None = 기능 꺼짐. 등급→숫자 변환이 **여기**
        # 한 곳에서만 일어나고 사이드카는 숫자만 받는다(정책 무지 유지).
        self.free_max_domains = free_max_domains
        # Dispatcher 하나를 공유한다. 전에는 `Mcp-Session-Id` 별 dict + 락 + 상한 256
        # + FIFO 축출 + drop_session 이 있었는데, **그 기계가 지키던 상태는
        # `Dispatcher.initialized` 불리언 하나였고 그 값을 읽는 프로덕션 코드가
        # 0건이었다**(읽는 것은 테스트 하나뿐). 즉 stateful 의 비용을 다 내고 이득이
        # 0이었다. 세션을 **살릴** 유일한 명분은 협상된 프로토콜 리비전을 담아
        # 후속 요청과 대조하는 것이었는데, 스펙이 실제로 요구하는 검증은 헤더가
        # **지원 목록에 있는가**이지 이 세션이 무엇으로 협상했는가가 아니다
        # (`_check_protocol_version`). 그건 무상태라 세션이 필요 없다.
        #
        # 덤으로 사라진 위험: `_sessions` 는 프로세스 로컬이라 Container Apps 레플리카가
        # 둘 이상이면 같은 세션 id 가 레플리카를 넘나들며 재생성됐다. 담긴 게 없어
        # 무해했지만, 무언가 담기로 결정하는 순간 즉시 버그가 되는 자리였다.
        #
        # Dispatcher 는 `bridge`·`allow_target`·미사용 불리언뿐이라 사실상 불변이고
        # 스레드 안전하다. 허브는 **이미 원격 그래프 자신**이라 `target` 을 내보내지도
        # 받지도 않는다(위험 감사 5.5) — 데스크탑 stdio 만 그 선택을 갖는다.
        self._dispatcher = Dispatcher(self.bridge, allow_target=False)

    def quota_check(self, owner: str, plan: str | None) -> dict | None:
        """소비 1회 시도. 허용이면 None, 초과·잠금이면 **tools/call 결과 dict**.

        카운터는 owner(public_id) 단위 — 키를 여러 개 만들어도 합산된다(우회 불가).
        """
        if not self.quota_config or self.store is None or not owner:
            return None                              # 미설정/로컬 = 무제한
        cfg = self.quota_config
        paid = plan in entitlement.PAID_PLANS
        limit = cfg["pro_limit"] if paid else cfg["free_limit"]
        r = self.store.quota_check(owner, limit=limit, window_s=cfg["window_s"],
                                   lock_s=cfg["lock_s"])
        if r["allowed"]:
            return None
        secs = r["retry_after_s"]
        hh, mm = secs // 3600, (secs % 3600) // 60
        tier = {"pro": "Pro", "team": "Team"}.get(plan or "", "Free")
        # 업셀 문구는 **무료와 유료의 한도가 실제로 다를 때만** 쓴다. AP-1 이후
        # 무료 한도가 Pro 와 같아지므로(차별축이 주제 개수로 옮겨갔다), 같은 값에서
        # "더 높은 한도로 업그레이드"는 거짓말이 된다. 그때는 주제 무제한을 근거로 든다.
        if paid:
            upsell = ""
        elif cfg["free_limit"] < cfg["pro_limit"]:
            upsell = " Upgrade for a higher limit: https://vincs.io/account"
        else:
            upsell = " Pro removes the topic limit: https://vincs.io/account"
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

    def resolve_auth(self, header: str | None) -> dict:
        """Bearer 를 해석해 {ok, owner, plan, source}. owner 강제 스탬핑의 출처.

        - static: 정적 토큰 = 단일 테넌트 하위호환(owner=None → bridge.owner 사용).
        - apikey: `vinc_sk_*` → verify_key 루프백 → owner=user:{public_id}, plan.
        - oauth : 발급 토큰 → 코드에 스탬핑된 owner/plan(회원 경로면 채워짐).
                  등급은 **동결돼 있으므로** `_fresh_plan` 이 주기적으로 되묻는다.
        """
        miss = {"ok": False, "owner": None, "plan": None, "source": None}
        if not header or not header.startswith("Bearer "):
            return miss
        token = header[len("Bearer "):].strip()
        if self.static_token and token == self.static_token:
            return {"ok": True, "owner": None, "plan": None, "source": "static"}
        if token.startswith("vinc_sk_") and self.verify_key is not None:
            ent = self.verify_key(token)
            if ent:
                return {"ok": True, "owner": ent["owner"],
                        "plan": ent.get("plan"), "source": "apikey"}
            return miss
        if self.oauth is not None:
            rec = self.oauth.resolve(token)
            if rec:
                owner = rec.get("owner")
                return {"ok": True, "owner": owner,
                        "plan": self._fresh_plan(owner, rec.get("plan")),
                        "source": "oauth"}
        return miss

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
        if not payload or payload.get("typ") != "aa" or payload.get("iss") != "account":
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

    def purge_owner_request(self, assertion: str) -> dict:
        """계정 서명 purge 어서션(typ=purge·iss=account·aud=mcp·≤2분)을 검증해 `sub`
        로 owner 를 유도하고, 사이드카에 owner 전 노드 삭제를 프록시한다.

        owner 는 **서명된 sub 에서만** 온다(요청 바디 미수용) — 유효한 A 소유자용
        어서션을 B 삭제에 재사용할 수 없다. 멱등 DETACH DELETE 라 만료창(≤2분) 내
        재생도 무해(이미 지워졌으면 0건). public_id 는 uuidv7 로 재사용되지 않아
        재등록 계정을 오삭제할 위험도 없다."""
        if not self.purge_enabled():
            raise PermissionError("purge is not configured")
        payload = edverify.verify_assertion(self.account_public, assertion or "", max_age=120)
        if not payload or payload.get("typ") != "purge" \
                or payload.get("iss") != "account" or payload.get("aud") != "mcp":
            raise PermissionError("invalid or expired purge request")
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise ValueError("purge request is missing a subject")
        return self.bridge.request("POST", "/sync/purge-owner", body={"owner": f"user:{sub}"})

    def purge_team_request(self, assertion: str) -> dict:
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
        graph_owner = payload.get("graph_owner")
        if not isinstance(graph_owner, str) or not _TEAM_OWNER_RE.fullmatch(graph_owner):
            raise ValueError("team purge request has an invalid graph owner")
        return self.bridge.request("POST", "/sync/purge-owner", body={"owner": graph_owner})

    # ── 웹 Vinc 그래프 프록시(계정 세션 → 서명 어서션 → owner 강제 read/write) ─────
    def graph_enabled(self) -> bool:
        """계정 공개키 핀 시에만 활성 — purge 와 같은 게이트(계정→우리 신뢰 방향)."""
        return bool(self.account_public)

    def graph_owner_request(self, assertion: str, args: dict) -> dict:
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
        # 어휘(theme)는 **전역**이다 — `store/theme_store.py` 에 owner 가 0건이다.
        # 그래서 남의 theme_id 를 실어 보내면 그 사람의 어휘에 내 타입이 제안으로
        # 꽂힌다. 여기서 떨구는 이유는 이 평면이 **파일에서 온 조각을 그대로 펼치기**
        # 때문이다(`app/src/surfaces/intake.ts` 의 `{...obj.fragment, owner}`) — 즉
        # 사용자가 연 팩 파일 안의 theme_id 가 아무 확인 없이 여기까지 온다.
        #
        # ⚠ **이것으로 닫히는 것은 웹 평면뿐이다.** 진짜 주입 경로인 호스티드 MCP
        # (`POST /mcp` → Dispatcher.handle → tools.call → _t_ingest → bridge.request)
        # 는 이 함수를 **아예 지나지 않으므로** 그대로 열려 있다. 제대로 닫으려면
        # ThemeStore 에 owner 차원을 넣어야 하고 그건 별도 작업이다. 여기 한 줄을
        # 근거로 "테넌시 막았다"고 적지 말 것.
        args.pop("theme_id", None)

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


class _Handler(BaseHTTPRequestHandler):
    server_version = "vinc-mcp"

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

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    def _unauthorized(self) -> None:
        meta = f'{self._issuer()}/.well-known/oauth-protected-resource'
        self._json(401, {"error": "unauthorized"},
                   {"WWW-Authenticate": f'Bearer resource_metadata="{meta}"'})

    def _issuer(self) -> str:
        if self.app.oauth is not None:
            return self.app.oauth.issuer
        host = self.headers.get("Host") or "127.0.0.1"
        return f"http://{host}"

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            # 상한 초과는 본문을 **읽지 않고** 400 으로 끝낸다(읽으면 그게 메모리 공격
            # 통로다). 미수신 바이트가 남지만 무해하다 — `protocol_version` 이
            # HTTP/1.0(기본)이라 매 응답 뒤 커넥션이 닫히기 때문이다.
            # ⚠ `protocol_version = "HTTP/1.1"` 로 올리면 keep-alive 가 살아나 이 잔여물이
            # 다음 요청으로 파싱된다(요청 밀수). 그때는 여기서 `close_connection = True`.
            return {}
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        # UTF-8 이 아닌 본문은 **JSON 파싱 실패와 같은 자리로 강등**한다(아래 except 와 동일).
        # 그냥 두면 do_POST 가 UnicodeDecodeError 로 죽어 커넥션이 리셋되고, 프록시 뒤에서는
        # 클라이언트가 400 대신 **503** 을 본다(로그엔 매 요청 스택트레이스). 실제로 호스트가
        # CP949 로 인코딩한 질의를 보내 라이브 허브가 이 경로를 밟았다(2026-08-04).
        try:
            raw = self.rfile.read(n).decode("utf-8")
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
        if url.path == "/.well-known/oauth-protected-resource":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            return self._json(200, gate.resource_metadata())
        if url.path == "/.well-known/oauth-authorization-server":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            return self._json(200, gate.as_metadata())
        if url.path == "/authorize":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            try:
                # key= 있으면 관리 키 자동승인(부트스트랩), 없으면 회원 승인 흐름.
                # 회원 경로가 켜져 있으면 이쪽이 정상 경로다(B-B3).
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
            # 사이드카 도달성(2026-08-03 사고 대응) — `/internal/graph-owner`(웹
            # 그래프 프록시)와 실제 MCP 툴콜이 전부 이 사이드카(127.0.0.1:8787)에
            # 물려 있는데, 그게 죽어도 이 프로세스(8790, Azure 가 실제로 찌르는
            # 포트)는 계속 200 을 준다 — 그 사고 때 curl 로 "허브가 죽었나"를 물어도
            # /health 는 정상이라고 답했을 것이다. oauth_state 와 같은 자리에 진단만
            # 싣는다(상태 코드는 안 바꾼다 — 이 앱엔 자동 프로브가 안 걸려 있고
            # <Container Apps 프로브 미설정, 2026-08-04 확인>, 단일 writer 락 특성상
            # 헬스 실패로 자동 재기동을 걸면 그 자체가 더 위험할 수 있다는
            # hub-deploy.yml 의 교훈도 있다 — 사람이 보고 판단하는 신호로 둔다).
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
                return self._json(201, gate.register(self._body()))
            except ValueError as e:
                return self._json(400, {"error": str(e)})
        if url.path == "/token":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            try:
                return self._json(200, gate.token(self._body()))
            except ValueError as e:
                return self._json(400, {"error": "invalid_grant", "error_description": str(e)})
        if url.path == "/mcp":
            if self._origin_rejected():
                return self._json(403, {"error": "origin not allowed"})
            bad = self._protocol_version_rejected()
            if bad:
                return self._json(400, {"error": bad})
            auth = self.app.resolve_auth(self.headers.get("Authorization"))
            if not auth["ok"]:
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
            d = self.app.dispatcher()
            ents = (self.app.entitlements_for(auth["plan"]) if auth["owner"] else None)
            replies = [r for r in (
                d.handle(m, forced_owner=auth["owner"], plan=auth["plan"],
                         quota=self.app.quota_check, entitlements=ents)
                for m in msgs) if r is not None]
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
        if url.path in ("/sync/push", "/sync/pull"):
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
        try:
            out = self.app.purge_owner_request(assertion)
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
        try:
            out = self.app.purge_team_request(assertion)
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
        assertion = self.headers.get("X-Vinc-Assertion") or ""
        args = self._body()
        try:
            out = self.app.graph_owner_request(assertion, args)
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
        auth = self.app.resolve_auth(self.headers.get("Authorization"))
        if not auth["ok"]:
            return self._unauthorized()
        owner = auth["owner"]
        if not owner:
            return self._json(400, {"error": "sync requires a member-scoped key "
                                             "(static-token auth has no owner)"})
        body = self._body()
        try:
            if path == "/sync/pull":
                out = self.app.bridge.request("GET", "/sync/pull-delta", params={
                    "owner": owner, "since": int(body.get("since") or 0)})
            else:
                out = self.app.bridge.request("POST", "/sync/apply-delta", body={
                    "owner": owner, "bundle": body.get("bundle") or {}})
        except Exception as e:
            return self._json(502, {"error": f"sidecar unreachable: {type(e).__name__}"})
        return self._json(200, out)

    # ── 로깅 ──────────────────────────────────────────────────────────────
    # 예전엔 여기서 전부 삼켰다("로그는 systemd/journald 몫"). **가비아 VM 시절 이야기다.**
    # Azure Container Apps 에서는 stdout 이 곧 로그라, 삼키면 남는 게 기동 배너뿐이고
    # 장애 때 컨테이너가 조용하다 = 아무 단서가 없다. 실제로 2026-07-31 커넥터 장애를
    # 진단할 때 "로그가 깨끗하다"가 정상이라는 뜻인지 눈이 먼 것인지조차 알 수 없었다.
    # (임베딩 워커가 조용히 사라졌던 것과 같은 계열의 사고 — 침묵을 성공으로 오독한다.)
    #
    # 그래서 **실패만** 남긴다: 2xx·3xx 는 조용히(정상 트래픽으로 로그를 채우지 않는다),
    # 4xx·5xx 와 예외는 stdout 으로. 컨테이너 로그가 곧 장애 기록이 된다.
    def log_message(self, fmt, *args):
        sys.stdout.write("[http] %s %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()

    def log_request(self, code="-", size="-"):
        try:
            n = int(code)
        except (TypeError, ValueError):
            n = 0
        if n and n < 400:
            return   # 정상 응답은 남기지 않는다
        # 쿼리스트링은 잘라낸다 — /authorize 의 파라미터 등이 로그에 눌러앉지 않도록.
        line = (self.requestline or "").split("?")[0]
        self.log_message('"%s" %s %s', line, code, size)


def make_server(app: MCPHTTPApp, host: str = "127.0.0.1", port: int = 8790) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.app = app  # type: ignore[attr-defined]
    return httpd


def serve_http(bridge: Bridge, *, host: str = "127.0.0.1", port: int = 8790,
               static_token: str | None = None, oauth: OAuthGate | None = None,
               verify_key=None, quota_config: dict | None = None,
               mcp_seed: bytes | None = None, account_public: bytes | None = None,
               connect_url: str | None = None, free_max_domains: int | None = None) -> int:
    app = MCPHTTPApp(bridge, static_token=static_token, oauth=oauth,
                     verify_key=verify_key, quota_config=quota_config,
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
