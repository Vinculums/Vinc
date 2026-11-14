# -*- coding: utf-8 -*-
"""사이드카 라우트 표와 디스패치. 경로 → `VincApp` 핸들러 메서드 이름.

HTTP 셸(`api.server._Handler`)과 CLI 의 프로세스 안 브리지(`api.cli_tools._InProcessBridge`)가
같은 :func:`dispatch` 를 부른다. 인증·요청 범위 ContextVar·쓰기 표식·예외의 상태 코드 변환은
HTTP 셸이 이 바깥에서 한다(브리지는 그것을 하지 않는다).

라우트를 더하거나 빼면 `api/contract/sidecar.json` 도 같이 고친다(앱이 안 부르면
`x-internal: true`). 두 쪽은 `tests/core/test_sidecar_contract.py` 가 이 표를 import 해 대조한다.
"""
from __future__ import annotations

#: 인증 전에 HTTP 셸이 직접 받는 라우트 → 응답을 만드는 앱 메서드. None 이면 셸이 그 자리에서
#: 답한다(`/health`, `api.server.health`). 아래 표는 인증 뒤에만 닿는다.
UNAUTHENTICATED_ROUTES: dict[tuple[str, str], str | None] = {
    ("GET", "/health"): None,
    # 디바이스 연결: 브라우저의 루프백 전달. 인증은 start 가 발급한 state 논스가 대신한다.
    ("POST", "/connect/callback"): "connect_callback",
}

GET_ROUTES: dict[str, str] = {
    "/recall": "read_recall", "/domains": "read_domains",
    "/view": "read_view", "/ledger": "read_ledger", "/share": "read_share",
    "/coupling": "read_coupling", "/promote-candidates": "read_promote",
    "/suggest-ties": "read_suggest", "/diagnose": "read_diagnose",
    "/merge-candidates": "read_merge_candidates",
    "/merge-rejections": "read_merge_rejections",   # F10-b 기각 목록
    "/neighbors": "read_neighbors", "/path": "read_path",
    "/node-edges": "read_node_edges",
    "/roles": "read_roles",
    "/roles/export": "read_role_export",
    "/learner": "read_learner", "/graph": "read_graph",
    "/graph/roots": "read_graph_roots",   # 들고 있는 시간 가지 배치(`api.layout_remote`)
    "/search": "read_search", "/decide": "read_decide",
    "/decide-paths": "read_decide_paths",   # 결정 문
    "/themes": "read_themes", "/types": "read_types",
    # 문서 정본(D45 P4) — get(복원+검증) · list. + 노드 클릭 상세(D45-V).
    "/doc": "read_doc", "/docs": "read_docs",
    # 관례(GRAPH.md) — 어시스턴트가 쓰기 전에 읽는 채널([[vinc.canon.graph-conventions]]).
    "/conventions": "read_conventions",
    "/nav-tree": "read_nav_tree",   # 좌측 문서 트리(D69)
    # 변경 표식 — 열린 화면이 몇 초마다 묻는다. 스토어를 안 읽는다.
    "/changes": "read_changes",
    "/diagnostics/writes": "read_write_waits",   # 쓰기 대기 집계(내부, 스토어 없이 답한다)
    "/doc/highlights": "read_doc_highlights",   # Reader 밑줄(D45-V2 2단계)
    "/doc/context": "read_doc_context",   # 리더 머리(개념 칩·목차·청크 오프셋)
    "/node": "read_node",
    "/node-context": "read_node_context",
    "/operation-status": "read_operation_status",
    "/events": "read_events",   # 변경 기록에서 계산한 이벤트 피드
    "/runs": "read_runs",   # 실행 기록 목록, 인용한 주장으로 거른다
    "/runs/waiting": "read_waiting_runs",   # 사람의 답을 기다리는 실행
    "/agent-writes": "read_agent_writes",   # 최근 쓰기를 출처별로 묶은 검토(A5)
    "/eval-cards": "read_eval_cards",   # 역할에 붙은 평가 카드
    "/community-agent/import-result": "read_community_agent_result",
    "/brief": "read_brief",   # W1 — 질문 하나로 답변 재료 조인
    "/edge-context": "read_edge_context",   # 흐름 선 클릭(D45-V 1단계)
    "/ledger-drilldown": "read_ledger_drilldown",   # 합계 구간 클릭(D45-V2 3단계)
    "/docs-in-range": "read_docs_in_range",   # 타임라인 기간 브러시(D45-V2 4단계)
    # 동기화(S1) — 선택 도메인 조회 · delta 번들 export.
    "/sync/domains": "read_sync_domains",
    "/sync/pull-delta": "read_sync_pull_delta",
    "/sync/docs-export": "read_sync_docs_export",   # 문서 정본 delta(D45 P3)
    # 저장 설정 — 스토어 없이 답한다.
    "/config": "read_config", "/config/inspect": "read_config_inspect",
    "/embed/pending": "read_embed_pending",   # 자체호스팅 임베딩 워커(B, 내부)
    "/owner-stats": "read_owner_stats",   # 운영자 사용량 덤프(W1c, 허브 내부 전용)
    # 계정 연결(디바이스 플로우) — 메일박스 1회 수령(인증).
    "/connect/status": "read_connect_status",
}

#: 핸들러에 덧붙이는 키워드 인자. 그래프는 직렬화된 바이트(`GraphWire`)로 돌려준다.
GET_ROUTE_KWARGS: dict[str, dict] = {"/graph": {"wire": True}}

#: 마지막 칸이 인자인 GET. 핸들러는 `(나머지, 쿼리)` 를 받는다.
GET_PREFIX_ROUTES: dict[str, str] = {"/plan/": "read_plan", "/jobs/": "get_job"}

POST_ROUTES: dict[str, str] = {
    "/pin": "write_pin", "/tag": "write_tag", "/touch": "write_touch",
    "/topic-conversion": "write_topic_conversion",
    "/topic-description": "write_topic_description",
    "/activate": "write_activate", "/backup": "submit_backup",
    "/community-agent/export": "render_community_agent",
    "/community-pack/inspect": "inspect_community_pack",
    "/community-agent/import-preview": "preview_community_agent",
    "/community-agent/import": "import_community_agent",
    "/ingest": "write_ingest",
    "/delete": "write_delete", "/delete-edge": "write_delete_edge",
    "/merge-nodes": "write_merge",
    # F10-b 병합 후보 기각(가역): 비가역 병합의 안전한 반대편. MCP 에서는
    # `vinc_merge_reject`(기각·undo)와 `vinc_merge_rejections`(목록)가 부르고,
    # 구조적으로 뺀 쌍은 `vinc_merge_candidates` 의 `include_structural` 로 되열린다.
    "/merge-reject": "write_merge_reject",
    # 문서 정본(D45 P4) — put(적재/편집) · delete. 단일 writer 직렬화.
    "/doc/put": "write_doc_put", "/doc/delete": "write_doc_delete",
    # 관례 재생성 — 생성 구역만 스토어 값으로 갈아 끼우고 저장한다.
    "/conventions/build": "write_conventions_build",
    "/doc/put-file": "write_doc_put_file",   # 파일 바이트 입구(텍스트·PDF)
    "/doc/extract": "write_doc_extract",   # 정본→노드 파생(D45-V, 확인 흐름)
    "/doc/reembed": "write_doc_reembed",   # 편집 후 벡터 재임베딩(D45-E 후속3)
    "/evidence/backfill": "write_evidence_backfill",   # 관계 근거 저작(U2)
    "/embed/apply": "write_embed_apply",   # 자체호스팅 임베딩 워커(B, 내부)
    # 노드 미러 전량 재구축 — 배포 경계에서 엔트리포인트가 한 번 부른다.
    "/node-mirror/rebuild": "write_node_mirror_rebuild",
    "/domain/rename": "write_domain_rename",   # 도메인 일괄 변경(S2)
    "/themes": "write_theme_create", "/themes/activate": "write_theme_activate",
    "/types/ratify": "write_type_ratify", "/types/reject": "write_type_reject",
    "/types/rename": "write_type_rename",
    # 동기화 데이터플레인(S1) — 사이드카 내부 표면(8787 은 공개 미노출, F4).
    "/sync/domains": "write_sync_domains",
    "/sync/apply-delta": "write_sync_apply_delta",
    "/sync/docs-apply": "write_sync_docs_apply",    # 문서 정본 delta 적용(D45 P3)
    # 계정 삭제 캐스케이드(§5.9) — owner 전 노드 purge. 원격에선 MCP
    # /internal/purge-owner 가 계정 서명 검증 후 프록시(owner 위조 불가).
    "/sync/purge-owner": "write_sync_purge_owner",
    # 동기화 세션 트리거(S2) — pull→push 오케스트레이션(202+job_id).
    "/sync": "submit_sync",
    # 모드 전환의 데이터 이동 — 전량, 한 번, 202+job_id.
    "/sync/push-all": "submit_sync_push_all",
    "/sync/pull-all": "submit_sync_pull_all",
    # 저장 위치 이동(폴더 ↔ 사용자 데이터베이스, 계정 → 데이터베이스) — 202+job_id.
    "/config/move": "submit_config_move",
    "/config": "write_config",   # 저장 설정
    # 데이터베이스를 고르기 전에 살핀다. 비밀번호가 바디에 오므로 GET 이 아니다.
    "/config/inspect-database": "write_config_inspect_database",
    "/shutdown": "write_shutdown",   # 셸의 재기동 전 정상 종료
    # 계정 연결(디바이스 플로우) — state 발급(인증). callback 은 인증 전 라우트다
    # (`UNAUTHENTICATED_ROUTES`, 이 표는 인증 이후에만 닿는다).
    "/connect/start": "write_connect_start",
    # 팀 스페이스 통과로 — 로컬 스토어가 아니라 계정으로 나간다.
    "/space/proxy": "write_space_proxy",
}

#: 마지막 칸이 엔진 이름인 POST. 핸들러는 `(나머지, 바디)` 를 받는다. 스토어가 없으면 503 이다.
POST_PREFIX_ROUTES: dict[str, str] = {"/ingest/": "submit_ingest"}

#: 스토어 없이도 **옳게** 도는 POST. 어휘(themes·types)는 그래프 밖 JSON 파일이고
#: (`ThemeStore`, `<data_dir>.themes.json`), 계정 연결 시작은 인메모리 메일박스다.
#: 그것들은 그래프가 죽어도 답하는 게 맞다 — GET 쪽 `READ_ROUTES` 주석과 같은 갈래다.
#: ⚠ 새 POST 라우트를 더할 때 여기 넣을지 판단할 것. 기본값은 **넣지 않는다**(가드를
#: 받는다) — 빠뜨려도 조용히 새지 않고 503 으로 막히므로 안전측이다.
POST_WITHOUT_STORE = frozenset({
    "/community-agent/export",
    "/community-pack/inspect",
    "/themes", "/themes/activate",
    "/types/ratify", "/types/reject", "/types/rename",
    "/connect/start",
    # 팀 그래프는 로컬에 없다. 스토어가 안 열렸다고 막으면, 로컬이 깨진 기기에서
    # **팀 스페이스까지 같이 죽는다** — 그쪽은 이 스토어와 아무 상관이 없다.
    "/space/proxy",
    # 저장 설정은 그래프 밖 파일이다. 폴더를 잘못 골라 스토어가 안 열렸을 때 다시
    # 고를 길이 이것뿐이다.
    "/config", "/config/inspect-database",
    # 안 열린 사이드카도 닫고 다시 띄울 수 있어야 한다.
    "/shutdown",
})


def store_unavailable() -> dict:
    """스토어 도달 불가의 **사유 봉투** — `/health` 와 조회 503 이 같은 것을 말하게.

    `EmbeddedBackend.open` 은 실패 사유를 `last_open_error` 에 담는다(락 충돌·손상·
    확장 경로). 그 값을 읽는 곳이 `/health` 하나뿐이면, 조회가 거절당한 클라이언트는
    거절만 받고 이유는 다른 엔드포인트에 가서 물어야 한다. 한 벌로 묶는다."""
    out = {"error": "no reachable store", "note": "no reachable store"}
    try:
        from store.backend import last_open_error
        if last_open_error():
            out["open_error"] = last_open_error()
    except Exception:   # noqa: BLE001 — 진단이 응답을 죽이지 않는다
        pass
    return out


def route_get(app, path: str, params: dict):
    """GET 디스패치. 반환은 `(상태, 본문)` 이다."""
    if app.store is not None and getattr(app.store, "_topic_conversion_guard", False):
        # Covers cached staged reads and remote layouts which bypass _read.
        from . import topic_conversion as conversion
        code, result = app._read(lambda r: conversion.check_read(r, params.get("owner")))
        if code != 200:
            return code, result
    name = GET_ROUTES.get(path)
    if name is not None:
        return getattr(app, name)(params, **GET_ROUTE_KWARGS.get(path, {}))
    for prefix, name in GET_PREFIX_ROUTES.items():
        if path.startswith(prefix):
            return getattr(app, name)(path[len(prefix):], params)
    return 404, {"error": f"no such route: GET {path}"}


def route_post(app, path: str, body: dict):
    """POST 디스패치. **스토어가 안 열렸으면 여기서 503 이다**(`POST_WITHOUT_STORE` 는 예외).

    핸들러를 `store=None` 인 채로 부르면 파이썬 내부 사정(`'NoneType' object has no attribute
    'run'`)만 화면에 닿고 진짜 사유는 `/health` 에만 남는다. 봉투는 새로 만들지 않는다 —
    :func:`store_unavailable` 한 벌을 `VincApp._read`·`/health` 와 같이 쓴다. 사유를 말하는 곳이
    하나여야 셋이 같은 말을 한다."""
    name = POST_ROUTES.get(path)
    if name is not None:
        if app.store is None and path not in POST_WITHOUT_STORE:
            return 503, {"executed": False, **store_unavailable()}
        return getattr(app, name)(body)
    for prefix, name in POST_PREFIX_ROUTES.items():
        if path.startswith(prefix):
            if app.store is None:
                return 503, {"executed": False, **store_unavailable()}
            return getattr(app, name)(path[len(prefix):], body)
    return 404, {"error": f"no such route: POST {path}"}


def dispatch(app, method: str, path: str, params: dict):
    """인증 뒤의 라우트 하나를 부른다. GET 이면 ``params`` 가 쿼리, POST 면 바디다."""
    if method == "GET":
        return route_get(app, path, params)
    if method == "POST":
        return route_post(app, path, params)
    raise ValueError(f"unsupported method: {method}")
