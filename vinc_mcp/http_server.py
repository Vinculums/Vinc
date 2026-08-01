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

from . import edverify
from .bridge import Bridge
from .oauth import CONNECT_TTL, OAuthGate
from .rpc import Dispatcher

MAX_BODY = 4 * 1024 * 1024
MAX_SESSIONS = 256      # Mcp-Session-Id 별 Dispatcher 상한(무한 증식 차단)

# 웹 Vinc 그래프 프록시 표면(op → (method, 사이드카 경로)). 계정 세션→서명 어서션이
# 이 op 만 요청할 수 있고, 화이트리스트 외는 거부한다. **제외**: themes/types(전역
# vocab, owner 차원 없음)·sync/*(데스크탑↔계정 동기화 기계)·backup(로컬 파일)·
# purge(전용 서명 경로). owner 는 항상 서명된 sub 에서 강제 주입된다(SEC-3 + 프록시).
_PROXY_OPS: dict[str, tuple[str, str | None]] = {
    # 읽기(GET)
    "atlas": ("GET", "/atlas"), "ledger": ("GET", "/ledger"), "graph": ("GET", "/graph"),
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
                 connect_url: str | None = None):
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
        # Dispatcher 는 상태가 initialized 플래그뿐 — 세션 간 공유해도 무해하지만
        # 스펙의 세션 관념에 맞춰 Mcp-Session-Id 별로 분리한다. 세션 id 는 **클라이언트
        # 가 정한다**(우리는 발급하지 않는다) → 임의 헤더로 무한 증식할 수 있으므로
        # 상한을 두고 오래된 것부터 버린다(FIFO). Dispatcher 는 상태가 사실상 없어
        # 축출돼도 다음 요청에서 재생성될 뿐 손실이 없다.
        self._sessions: dict[str, Dispatcher] = {}
        self._lock = threading.Lock()

    def quota_check(self, owner: str, plan: str | None) -> dict | None:
        """소비 1회 시도. 허용이면 None, 초과·잠금이면 **tools/call 결과 dict**.

        카운터는 owner(public_id) 단위 — 키를 여러 개 만들어도 합산된다(우회 불가).
        """
        if not self.quota_config or self.store is None or not owner:
            return None                              # 미설정/로컬 = 무제한
        cfg = self.quota_config
        limit = cfg["pro_limit"] if plan == "pro" else cfg["free_limit"]
        r = self.store.quota_check(owner, limit=limit, window_s=cfg["window_s"],
                                   lock_s=cfg["lock_s"])
        if r["allowed"]:
            return None
        secs = r["retry_after_s"]
        hh, mm = secs // 3600, (secs % 3600) // 60
        tier = "Pro" if plan == "pro" else "Free"
        text = (f"{tier} tier limit reached ({limit} tool calls per "
                f"{int(cfg['window_s'] // 3600)}h). Access resumes in ~{hh}h{mm:02d}m."
                + ("" if plan == "pro" else " Upgrade for a higher limit: https://vincs.io/account"))
        return {"content": [{"type": "text", "text": text}], "isError": True,
                "_meta": {"vinc_quota": {"limit": limit, "retry_after_s": secs,
                                         "upgrade_url": "https://vincs.io/account"}}}

    def dispatcher(self, session_id: str) -> Dispatcher:
        with self._lock:
            d = self._sessions.get(session_id)
            if d is None:
                while len(self._sessions) >= MAX_SESSIONS:
                    self._sessions.pop(next(iter(self._sessions)))
                d = Dispatcher(self.bridge)
                self._sessions[session_id] = d
            return d

    def drop_session(self, session_id: str) -> bool:
        """세션 종료(DELETE /mcp) — 있으면 버리고 True. 멱등."""
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def resolve_auth(self, header: str | None) -> dict:
        """Bearer 를 해석해 {ok, owner, plan, source}. owner 강제 스탬핑의 출처.

        - static: 정적 토큰 = 단일 테넌트 하위호환(owner=None → bridge.owner 사용).
        - apikey: `vinc_sk_*` → verify_key 루프백 → owner=user:{public_id}, plan.
        - oauth : 발급 토큰 → 코드에 스탬핑된 owner/plan(회원 경로면 채워짐).
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
                return {"ok": True, "owner": rec.get("owner"),
                        "plan": rec.get("plan"), "source": "oauth"}
        return miss

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
        plan = payload.get("plan") if payload.get("plan") in ("free", "pro") else "free"
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

    # ── 웹 Vinc 그래프 프록시(계정 세션 → 서명 어서션 → owner 강제 read/write) ─────
    def graph_enabled(self) -> bool:
        """계정 공개키 핀 시에만 활성 — purge 와 같은 게이트(계정→우리 신뢰 방향)."""
        return bool(self.account_public)

    def graph_owner_request(self, assertion: str, args: dict) -> dict:
        """계정 서명 proxy 어서션(typ=proxy·iss=account·aud=mcp·op·≤2분)을 검증해 `sub`
        로 owner 를 **강제**하고, op 를 사이드카 read/write 라우트로 프록시한다.

        owner 는 서명된 `sub` 에서만 온다 — args 의 owner 는 pop 해 버린다(클라/계정
        바디 어느 쪽도 owner 를 못 정한다 → 교차테넌트 봉쇄, SEC-3 와 이중). op 는
        화이트리스트(_PROXY_OPS)로만. 유효한 A 어서션을 B 스코프에 못 쓴다(sub 고정)."""
        if not self.graph_enabled():
            raise PermissionError("graph proxy is not configured")
        payload = edverify.verify_assertion(self.account_public, assertion or "", max_age=120)
        if not payload or payload.get("typ") != "proxy" \
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
        owner = f"user:{sub}"
        args = dict(args or {})
        args.pop("owner", None)   # 클라/바디 owner 는 절대 신뢰하지 않는다

        if op == "job":   # /jobs/{id} 폴링 — id 는 토큰꼴만(경로주입·타 라우트 차단)
            jid = str(args.get("id") or "")
            if not _JOB_ID_RE.fullmatch(jid):
                raise ValueError("invalid job id")
            return self.bridge.request("GET", f"/jobs/{jid}")
        if op == "activate":   # 클라가 호스트 임의 파일경로 지정 못 하게(활성 팩 인라인)
            args.pop("out", None)
        if method == "GET":
            return self.bridge.request("GET", path, params={**args, "owner": owner})
        # POST: owner 를 바디 최상위에 강제 — fragment.owner·write_* 전부 최상위 owner 를 읽음.
        return self.bridge.request("POST", path, body={**args, "owner": owner})


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

    def do_OPTIONS(self):  # noqa: N802
        """프리플라이트 — 허용 오리진에만 CORS 를 붙여 204."""
        self.send_response(204)
        for k, v in self._cors_headers().items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── 응답 헬퍼 ───────────────────────────────────────────────────────────
    def _json(self, code: int, obj: dict, headers: dict | None = None) -> None:
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
            return {}
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        raw = self.rfile.read(n).decode("utf-8")
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
            return self._json(200, {"status": "ok"})
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
            auth = self.app.resolve_auth(self.headers.get("Authorization"))
            if not auth["ok"]:
                return self._unauthorized()
            msg = self._body()
            if not isinstance(msg, dict) or not msg:
                return self._json(400, {"error": "expected a JSON-RPC message"})
            session = self.headers.get("Mcp-Session-Id") or "default"
            # forced_owner: 원격 인증경로가 해석한 소유자 → tools 가 args.owner 를
            # 무시하고 강제 스탬핑(멀티테넌트 봉쇄, B-B2). static/None 이면 하위호환.
            # quota: tools/call 에서만 소비(initialize/tools_list/ping 무료, B-D2).
            reply = self.app.dispatcher(session).handle(
                msg, forced_owner=auth["owner"], plan=auth["plan"],
                quota=self.app.quota_check)
            if reply is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            return self._json(200, reply, {"Mcp-Session-Id": session})
        if url.path in ("/sync/push", "/sync/pull"):
            return self._sync(url.path)
        if url.path == "/internal/purge-owner":
            return self._internal_purge()
        if url.path == "/internal/graph-owner":
            return self._internal_graph()
        return self._json(404, {"error": f"no such route: POST {url.path}"})

    def do_DELETE(self):  # noqa: N802
        """`DELETE /mcp` — 클라이언트 세션 종료. 스펙상 선택이지만 종료를 보내는
        호스트(Codex·Cursor 등)가 501 을 오류로 취급하지 않도록 명시 처리한다.
        인증은 POST 와 동일하게 요구한다 — 남의 세션을 지우게 둘 이유가 없다."""
        url = urllib.parse.urlparse(self.path)
        if url.path != "/mcp":
            return self._json(404, {"error": f"no such route: DELETE {url.path}"})
        if not self.app.authorized(self.headers.get("Authorization")):
            return self._unauthorized()
        self.app.drop_session(self.headers.get("Mcp-Session-Id") or "default")
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
               connect_url: str | None = None) -> int:
    app = MCPHTTPApp(bridge, static_token=static_token, oauth=oauth,
                     verify_key=verify_key, quota_config=quota_config,
                     mcp_seed=mcp_seed, account_public=account_public,
                     connect_url=connect_url)
    httpd = make_server(app, host, port)
    modes = [m for m, on in (("static", static_token), ("oauth", oauth),
                             ("apikey", verify_key),
                             ("member", app.member_connect_enabled())) if on]
    print(json.dumps({"host": host, "port": httpd.server_address[1],
                      "auth": "+".join(modes) or "none"}), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        bridge.close()
    return 0
