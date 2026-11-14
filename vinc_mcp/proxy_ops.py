# -*- coding: utf-8 -*-
"""웹 Vinc 그래프 프록시의 op 표와 그 op 하나를 사이드카로 보내는 `proxy_op`.

두 평면이 이 모듈을 같이 쓴다: 계정 서명 평면(`MCPHTTPApp.graph_owner_request`)과 셀프호스트의 같은
출처 평면(`selfhost_web`). 표는 op → (방법, 사이드카 경로), 팀 역할 등급, 공개 커뮤니티 반영기의
규칙이다. 서명 검증과 owner 결정은 부르는 쪽이 끝낸다.

`MCPHTTPApp.proxy_op` 는 여기 `proxy_op` 로 한 줄 넘긴다. 함수는 앱을 첫 인자로 받아 앱의 `bridge` 와
`entitlements_for` 를 쓴다. 이 모듈은 `http_server` 를 import 하지 않는다.
"""
from __future__ import annotations

import re

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
    "node": ("GET", "/node"), "node-context": ("GET", "/node-context"),
    "operation-status": ("GET", "/operation-status"),
    "node-edges": ("GET", "/node-edges"),
    "roles": ("GET", "/roles"),
    "eval-cards": ("GET", "/eval-cards"),   # 역할 편집 면의 평가 카드(조회 전용, owner 는 프록시가 강제)
    # 승인함: 사람의 답을 기다리는 실행(조회 전용)과 그 답. 답은 사이드카가 아니라 허브의 실행
    # 프로토콜이 받고(`run_protocol.person_answer`), 서명에 `auth: "session"` 이 있을 때만 받는다.
    "runs-waiting": ("GET", "/runs/waiting"),
    "run-answer": ("POST", None),
    # 에이전트 쓰기 검토(A5) — 최근 쓰기를 출처별로 묶은 조회. owner 는 프록시가 강제한다.
    "agent-writes": ("GET", "/agent-writes"),
    "community-agent-export": ("POST", "/community-agent/export"),
    "community-pack-inspect": ("POST", "/community-pack/inspect"),
    "community-agent-import-preview": ("POST", "/community-agent/import-preview"),
    "community-agent-import": ("POST", "/community-agent/import"),
    "community-agent-import-result": ("GET", "/community-agent/import-result"),
    "edge-context": ("GET", "/edge-context"),
    "ledger-drilldown": ("GET", "/ledger-drilldown"),
    "docs-in-range": ("GET", "/docs-in-range"),
    "nav-tree": ("GET", "/nav-tree"),   # 좌측 문서 트리(D69) — 주제→문서 깊이순
    # 변경 표식(VIN-129) — 열린 화면이 몇 초마다 "그 뒤로 쓰기가 있었나"를 묻는다.
    # 사이드카가 메모리에서 답하므로 폴링이 스토어를 안 읽는다. 조회라 열람자도 된다.
    "changes": ("GET", "/changes"),
    # 시간 가지 배치 자리(늘 꺼진 답). 그래프 읽기와 같은 조회라 열람자도 된다.
    "graph-roots": ("GET", "/graph/roots"),
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
    # reembed=편집 후 재임베딩 큐에 되돌림(벡터는 허브의 자체호스팅 임베딩 워커가 채운다).
    "doc-put": ("POST", "/doc/put"),
    "topic-description": ("POST", "/topic-description"),
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

# 일반 열람자는 그래프를 직접 고치지 않는다. 계정의 커밋된 outbox만 현재 값을 반영한다.
_COMMUNITY_PROJECTOR_SUB = "account:community-projector"
_COMMUNITY_READ_OPS = frozenset({
    "view", "ledger", "graph", "recall", "search", "coupling", "suggest-ties",
    "decide", "domains", "promote-candidates", "learner", "share", "doc", "docs",
    "doc-highlights", "doc-context", "node", "node-context", "node-edges", "roles",
    "edge-context", "ledger-drilldown", "docs-in-range", "nav-tree", "changes",
    "graph-roots", "conventions",
    "community-agent-export",
    "community-pack-inspect",
})
_COMMUNITY_PROJECT_OPS = frozenset({
    "ingest-fragment", "doc-put", "conventions-build", "delete", "doc-delete", "delete-edge",
})
# 위키 페이지 노드. 종류가 접두를 정한다(사건 episode·결과 record·결정 decision, 나머지 concept).
_COMMUNITY_PAGE_NODE_RE = re.compile(r"(?:concept|episode|record|decision):[a-z0-9][a-z0-9-]{0,79}")
_COMMUNITY_WIKI_RELS = frozenset({
    "relates_to", "related_to", "prerequisite_of", "derives_from", "inherits_from",
})
_COMMUNITY_OBJECT_UUID = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_COMMUNITY_SOCIAL_NODE_RE = re.compile(r"episode:(?:talk|lounge)-" + _COMMUNITY_OBJECT_UUID)
_COMMUNITY_SOCIAL_DOC_RE = re.compile(
    r"(?:talk|lounge)\." + _COMMUNITY_OBJECT_UUID +
    r"(?:\.comment\." + _COMMUNITY_OBJECT_UUID + r")?")
_COMMUNITY_AGENT_NODE_RE = re.compile(r"concept:agentv-" + _COMMUNITY_OBJECT_UUID + r"-[1-9][0-9]{0,15}")
_COMMUNITY_AGENT_DOC_RE = re.compile(r"agent\.(" + _COMMUNITY_OBJECT_UUID + r")\." + _COMMUNITY_OBJECT_UUID + r"\.[1-9][0-9]{0,15}")
_COMMUNITY_PACK_NODE_RE = re.compile(r"concept:pack-" + _COMMUNITY_OBJECT_UUID)
_COMMUNITY_PACK_DOC_RE = re.compile(r"pack\.(" + _COMMUNITY_OBJECT_UUID + r")\." + _COMMUNITY_OBJECT_UUID)


def _community_page_node(node) -> bool:
    """반영된 위키 페이지 노드 id 인가. 끝·이중 하이픈과 Talk/Lounge 노드는 아니다."""
    return (isinstance(node, str) and bool(_COMMUNITY_PAGE_NODE_RE.fullmatch(node))
            and not node.endswith("-") and "--" not in node
            and not _COMMUNITY_SOCIAL_NODE_RE.fullmatch(node))


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
    "topic-description",
    "evidence-backfill", "conventions-build",
    # 팀 실행의 대기에 답하는 것은 쓰기다. 열람자는 목록만 본다.
    "run-answer",
})
_PURE_POST_OPS = frozenset({"community-agent-export", "community-agent-import-preview", "community-pack-inspect"})
#: 팀 그래프에서는 **어느 역할로도** 못 하는 op. 공유 Agent 가져오기·미리보기·내보내기와
#: 팩 검사는 개인 그래프(`typ=proxy`) 전용이다(2026-10-16 소유자 판정: 팀 관리에서
#: 커뮤니티 기능을 뺀다). 등급표 밖에 두는 것이 요점이다 — editor 에서 빼기만 하면
#: 나머지 op 처럼 조회로 분류돼 열람자에게 열린다.
#: 근거: [[vinc.audit.code-cleanup-community-alignment-2026-10-16]] §2.2
_TEAM_REFUSED_OPS = frozenset(
    op for op in _PROXY_OPS if op.startswith("community-agent-") or op == "community-pack-inspect")
_ADMIN_OPS = frozenset({
    # 되돌릴 수 없는 것들 — 남의 작업을 지우거나 합치는 연산이다.
    "delete", "delete-edge", "merge-nodes", "doc-delete", "domain-rename",
})
_TEAM_ROLES = ("viewer", "editor", "admin")


def _role_allows(role: str, op: str) -> bool:
    """팀 역할이 이 op 를 할 수 있는가. 모르는 역할은 **거부**(fail-closed)."""
    if role not in _TEAM_ROLES or op in _TEAM_REFUSED_OPS:
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
    viewer = frozenset(_PROXY_OPS) - _EDITOR_OPS - _ADMIN_OPS - _TEAM_REFUSED_OPS
    return {"viewer": viewer, "editor": _EDITOR_OPS, "admin": _ADMIN_OPS}


def proxy_op(app, op: str, args: dict | None, owner: str, *, actor: str,
             person_session: bool = False, plan: str | None = None) -> dict:
    """화이트리스트 op 하나를 정해진 owner 로 사이드카에 보낸다. 인증은 부른 쪽이 끝냈다.

    두 평면이 이 함수를 같이 쓴다: 계정 서명 평면(`graph_owner_request`)과 셀프호스트의
    같은 출처 평면(`selfhost_web`). owner·actor·사람 세션 여부·등급은 부른 쪽이 정하고,
    인자에 실려 온 owner·theme_id·debug·자격은 여기서 벗긴다."""
    route = _PROXY_OPS.get(op)
    if route is None:
        raise ValueError(f"unsupported proxy op: {op!r}")
    method, path = route
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

    if op == "run-answer":
        # 사람만 답하는 대기의 유일한 문이다. 계정의 그래프 프록시는 멤버 키(데스크톱·스크립트)도
        # 받으므로 서명만으로는 사람인지 모른다. 계정은 쿠키 세션으로 온 요청에만
        # `auth: "session"` 을 싣고, 그것이 없으면 여기서 거절한다(fail-closed).
        from . import run_protocol
        if not person_session:
            return {"kind": "run.answer", "executed": False, "error": "person_session_required",
                    "note": ("Refused: only a person's signed-in session can answer a run. "
                             "A key or an agent connection cannot. Nothing changed.")}
        return run_protocol.person_answer(args, app.bridge, owner, actor=actor)
    if op in ("ingest-fragment", "ingest-pack"):
        # 이 평면은 멤버 키도 지난다. 실행 기록의 대기·승인·답은 `run-answer` 로만 바꾼다.
        pack = args.get("pack") if isinstance(args.get("pack"), dict) else {}
        frag = pack.get("fragment") if isinstance(pack.get("fragment"), dict) else {}
        from . import run_state
        blocked = run_state.writes(args.get("nodes")) + run_state.writes(frag.get("nodes"))
        if blocked:
            return {"kind": "ingest", "executed": False, "error": "run_state_protected",
                    "nodes": blocked, "note": "Refused: a run record's protocol state changes "
                    "only through an answer. Nothing was written."}
    if op == "job":   # /jobs/{id} 폴링 — id 는 토큰꼴만(경로주입·타 라우트 차단)
        jid = str(args.get("id") or "")
        if not _JOB_ID_RE.fullmatch(jid):
            raise ValueError("invalid job id")
        # id 는 세어 맞힐 수 있는 카운터다. owner 를 실어야 사이드카가 남의 잡을 없는 잡으로 답한다.
        # 빈 owner 는 브리지가 빼고 보내 사이드카가 소유자 없는 읽기(로컬 셸)로 받으므로 여기서 막는다.
        if not str(owner or "").strip():
            raise ValueError("invalid job owner")
        return app.bridge.request("GET", f"/jobs/{jid}", params={"owner": owner})
    if op == "activate":   # 클라가 호스트 임의 파일경로 지정 못 하게(활성 팩 인라인)
        args.pop("out", None)
    args.pop("_entitlements", None)   # 바디로 들어온 자격은 신뢰하지 않는다
    if method == "GET":
        return app.bridge.request("GET", path, params={**args, "owner": owner})
    body = {**args, "owner": owner}
    # 주제 상한(AP-1 §3.4). **plan 클레임이 없으면 상한을 걸지 않는다** — 클레임을
    # 안 보내는 구 계정 + 이 허브 조합에서 부재를 free 로 읽으면 유료 사용자까지
    # 상한에 걸린다. 배포 순서가 돈 낸 기능을 뺏는 꼴이라, 모를 때는 열어둔다.
    if op in _CAPPED_PROXY_OPS and plan is not None:
        ent = app.entitlements_for(plan)
        if ent:
            body["_entitlements"] = ent
    # POST: owner 를 바디 최상위에 강제 — fragment.owner·write_* 전부 최상위 owner 를 읽음.
    return app.bridge.request("POST", path, body=body)
