# -*- coding: utf-8 -*-
"""도구 오류의 기계 코드 — 사이드카(`api/`)가 싣고 허브(`vinc_mcp/rest_api.py`)가 읽는다.

`store/` 에 두는 이유는 층이다: `vinc_mcp` 는 `api` 를 import 할 수 없다
(`scripts/gate_ownership.py` 의 `LAYER_BANS`). 코드의 철자는 사이드카와 허브가 이 한 파일에서 받는다.

코드는 결과의 `error_code` 칸에 싣는다. `error`·`note` 의 문장은 그대로 두므로 문장을 읽는 클라이언트는
달라지지 않는다. 허브는 MCP 결과에서 `error_code` 를 떼어 낸다(`vinc_mcp/encoding.py`): MCP 답은 코드가
생기기 전과 같은 바이트다. REST v1 의 `_failure` 가 코드를 먼저 읽고, 코드 없는 답(옛 사이드카)은 문장으로
읽는 줄이 받는다.

이 모듈은 아무것도 import 하지 않는다.
"""
from __future__ import annotations

__all__ = [
    "KEY", "NOT_FOUND", "INVALID", "UNAVAILABLE", "ALL",
    "DOCUMENT_NOT_FOUND", "NO_REACHABLE_STORE", "OWNER_REQUIRED", "ROLE_REQUIRED",
    "DISTINCT_NODE_IDS_REQUIRED", "INVALID_ROLE_PAGINATION", "INVALID_PATHS", "INVALID_EDGES_QUERY",
    "INVALID_VALIDITY_DATE", "MERGE_PAIR_NOT_CONCEPTS", "MERGE_PAIR_SAME_NODE",
    "UNSUPPORTED_EDGE_TYPE", "SIDECAR_SERVER_ERROR", "SIDECAR_WRITE_UNCONFIRMED",
]

#: 결과에서 코드를 싣는 칸.
KEY = "error_code"

# ── 이 파일이 철자를 정하고 `api/`·`bridge` 가 싣는 코드 ──────────────────────────────
#: 문서가 없거나 남의 것이다(둘을 가르지 않는다).
DOCUMENT_NOT_FOUND = "document_not_found"
#: 사이드카가 스토어를 열지 못했다.
NO_REACHABLE_STORE = "no_reachable_store"
#: 호출에 owner 가 없다.
OWNER_REQUIRED = "owner_required"
#: 호출에 role 이 없다.
ROLE_REQUIRED = "role_required"
#: 병합 기각에 서로 다른 노드 id 둘이 없다.
DISTINCT_NODE_IDS_REQUIRED = "distinct_node_ids_required"
#: 역할 목록의 offset·limit 이 정수 범위가 아니다.
INVALID_ROLE_PAGINATION = "invalid_role_pagination"
#: decide_paths 의 paths 가 비었거나 repo:path 문자열 목록이 아니거나 상한을 넘었다.
INVALID_PATHS = "invalid_paths"
#: node_edges 의 인자(owner·node·nodes·dir·limit·type)가 계약을 벗어났다.
INVALID_EDGES_QUERY = "invalid_edges_query"
#: 문서 valid_from·valid_until 이 YYYY[-MM[-DD]] 가 아니다.
INVALID_VALIDITY_DATE = "invalid_validity_date"
#: 병합은 concept 쌍만 받는다.
MERGE_PAIR_NOT_CONCEPTS = "merge_pair_not_concepts"
#: 병합의 keep 과 drop 이 같은 노드다.
MERGE_PAIR_SAME_NODE = "merge_pair_same_node"
#: 지울 수 있는 관계 이름이 아니다.
UNSUPPORTED_EDGE_TYPE = "unsupported_edge_type"
#: 사이드카가 5xx 로 답했다(브리지가 싣는다).
SIDECAR_SERVER_ERROR = "sidecar_server_error"
#: 쓰기가 응답 없이 끊겼다. 적용됐을 수 있다.
SIDECAR_WRITE_UNCONFIRMED = "sidecar_write_unconfirmed"

# ── REST v1 이 상태를 정하는 묶음 ─────────────────────────────────────────────────
#: 404 `not_found`: 찾는 것이 없다.
NOT_FOUND = frozenset({DOCUMENT_NOT_FOUND})

#: 400 `invalid_argument`: 요청이 계약을 벗어났다. 앞의 일곱은 이 파일이 철자를 정하고, 나머지는 `error`
#: 칸에 이미 코드로 있던 것들이라 철자는 생산자(`api/server.py`·`api/operation_receipts.py`·허브 도구)의 것이다.
INVALID = frozenset({
    OWNER_REQUIRED, ROLE_REQUIRED, DISTINCT_NODE_IDS_REQUIRED, INVALID_ROLE_PAGINATION,
    INVALID_PATHS, INVALID_EDGES_QUERY, INVALID_VALIDITY_DATE,
    "invalid_dry_run", "invalid_preview_digest", "invalid_expected_content_hash",
    "invalid_operation_id", "invalid_role_pin", "output_required",
    "task_required", "invalid_run_state",
})

#: 503 `upstream_unavailable`: 뒤쪽이 지금 답하지 못한다. 앞의 하나만 이 파일이 철자를 정하고 나머지는
#: `error` 칸에 이미 코드로 있던 것들이다.
UNAVAILABLE = frozenset({
    NO_REACHABLE_STORE, "write_precondition_unavailable", "receipt_read_unavailable",
    "receipt_capacity_unavailable", "run_not_saved",
})

#: 묶음에 드는 모든 코드. 한 코드는 한 묶음에만 든다.
ALL = NOT_FOUND | INVALID | UNAVAILABLE
