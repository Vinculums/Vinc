"""커뮤니티 거절이 REST v1 오류 객체에 닿는 모양.

계정은 {"error": {code, message, request_id, ...}} 로 거절한다. REST 는 상태로 정한 v1 코드를
`code` 에 두고, 계정의 문장은 `message` 에 그대로, 계정의 코드와 나머지 필드는 `details` 에 싣는다.
허브와 가짜 계정 사이는 test_community_unified_actions 의 실제 HTTP 픽스처로 돈다.
"""
from __future__ import annotations

import pytest

from test_community_unified_actions import MUTATION, UNIFIED_ONLY, rest, service  # noqa: F401
from vinc_mcp.rest_api import _failure

FORBIDDEN = "Only community operators can do that."
PICK_HANDLE = "Pick a public handle before writing."
REPLAYED = "This mutation identifier already belongs to another request."

#: (왜, 라우트, 요청 본문, 계정 상태, 계정 오류 객체, 기대 v1 코드)
REFUSALS = [
    ("forbidden topic_save", "/v1/community",
     {"action": "topic_save", "client_name": "Codex", "payload": {"path": "physics", "nameEn": "Physics"}},
     403, {"code": "forbidden", "message": FORBIDDEN, "request_id": "req-f"}, "role_forbidden"),
    ("write without a public handle", "/v1/community",
     {"action": "post_create", "client_name": "Codex", "payload": {"body": "x", "mutationId": MUTATION}},
     400, {"code": "username_required", "message": PICK_HANDLE, "request_id": "req-u"}, "invalid_argument"),
    ("replayed mutation with another body", "/v1/community",
     {"action": "post_create", "client_name": "Codex", "payload": {"body": "y", "mutationId": MUTATION}},
     409, {"code": "edit_conflict", "message": REPLAYED, "request_id": "req-r"}, "conflict"),
    ("feed outside the unified space", "/v1/community/read",
     {"action": "feed", "payload": {"limit": 10}},
     400, {"code": "bad_request", "message": UNIFIED_ONLY, "request_id": "req-b"}, "invalid_argument"),
]


@pytest.mark.parametrize("why,path,body,status,error,rest_code", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_rest_shows_the_account_message_and_code(service, why, path, body, status, error, rest_code):  # noqa: F811
    service["answer"] = (status, {"error": error})
    got_status, got = rest(service, path, body)
    # 상태와 v1 코드는 그대로다. 계정의 문장이 message 이고 파이썬 repr 이 아니다.
    assert got_status == status
    assert got["error"]["code"] == rest_code
    assert got["error"]["message"] == error["message"]
    assert got["error"]["details"] == {"account_code": error["code"],
                                       "account_request_id": error["request_id"]}
    assert got["error"]["request_id"].startswith("req_")
    assert len(service["requests"]) == 1


def test_extra_fields_of_the_account_error_stay_in_details(service):  # noqa: F811
    service["answer"] = (409, {"error": {"code": "edit_conflict", "message": "Someone saved this page while you were editing.",
                                         "request_id": "req-c", "currentRevision": 7}})
    status, got = rest(service, "/v1/community", {"action": "page_save", "client_name": "Codex",
                                                  "payload": {"slug": "energy", "title": "Energy", "body": "x",
                                                              "summary": "Edit", "baseRevision": 6}})
    assert status == 409 and got["error"]["code"] == "conflict"
    assert got["error"]["message"] == "Someone saved this page while you were editing."
    assert got["error"]["details"] == {"account_code": "edit_conflict", "account_request_id": "req-c",
                                       "currentRevision": 7}


@pytest.mark.parametrize("error,message,details", [
    ({"code": "username_required", "message": PICK_HANDLE}, PICK_HANDLE, {"account_code": "username_required"}),
    ({"code": "forbidden"}, "forbidden", {"account_code": "forbidden"}),
    ("share_withdrawn", "share_withdrawn", {"account_code": "share_withdrawn"}),
    ("Community request refused.", "Community request refused.", {}),
])
def test_failure_keeps_the_status_mapping_for_every_error_shape(error, message, details):
    got = _failure("vinc_community_write", {"executed": False, "isError": True, "status": 400, "error": error})
    assert got == (400, "invalid_argument", message, details)
