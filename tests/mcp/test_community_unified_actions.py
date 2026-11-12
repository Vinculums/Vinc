"""합친 공간의 새 동작(post_create·post_reply·topic_save, 읽기 feed·post·topics)이 허브를 지나 계정 내부 라우트로
가는 길. 허브 HTTP 서버와 서명을 검사하는 가짜 계정 서버 사이를 실제 HTTP 로 돈다.

계정의 실제 라우트를 이 저장소에서 함께 띄울 방법은 없다. 그래서 계정이 받는 모양은 아래 CONTRACT 표로 옮겨 두고
요청 본문을 그 표와 바이트로 맞댄다. 표의 출처는 Vinc-homepage account 의 routes/internalCommunity.ts(봉투),
services/communityMutations.ts(쓰기 payload), services/communityReads.ts(읽기 payload), lib/communityInput.ts(형식).
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vinc_mcp import community, edverify, entitlement, http_server as H, tools
from vinc_mcp.bridge import Bridge

A = "community:0191f0aa-1111-7abc-8def-0123456789ab"
USER = "0191f0aa-3333-7abc-8def-0123456789ab"
POST = "0191f0aa-5555-7abc-8def-0123456789ab"
REPLY = "0191f0aa-6666-7abc-8def-0123456789ab"
MUTATION = "0191f0aa-9999-7abc-8def-0123456789ab"
SEED = bytes(range(32))
WRITE, READ = community.WRITE_TOOL, community.READ_TOOL
UNIFIED_ONLY = "This action works in the unified public community only."

#: 계정 내부 라우트가 읽는 봉투. 쓰기는 scope·channel·client_name 을 검사하고, 읽기는 sub·community·action·payload 만 읽는다.
WRITE_ROUTE = ("/internal/mcp/community", "community-write", 30,
               ("sub", "community", "action", "payload", "client_name", "channel", "scope"))
READ_ROUTE = ("/internal/mcp/community-read", "community-read", 4, ("sub", "community", "action", "payload"))
#: 동작 → (라우트, 계정이 payload 에서 읽는 이름, 그 이름을 모두 쓴 예).
CONTRACT = {
    "post_create": (WRITE_ROUTE, ("body", "title", "kind", "topic", "about", "mutationId"),
                    {"body": "Is energy conserved here?", "title": "A question", "kind": "question",
                     "topic": "physics/energy", "about": "energy", "mutationId": MUTATION}),
    "post_reply": (WRITE_ROUTE, ("targetId", "body", "mutationId"),
                   {"targetId": POST, "body": "Yes, by Noether.", "mutationId": MUTATION}),
    "topic_save": (WRITE_ROUTE, ("path", "nameEn", "nameKo", "order", "featured", "status", "mutationId"),
                   {"path": "physics/energy", "nameEn": "Energy", "nameKo": "에너지", "order": 3,
                    "featured": True, "status": "active", "mutationId": MUTATION}),
    "feed": (READ_ROUTE, ("kind", "topic", "about", "sort", "cursor", "limit"),
             {"kind": "note", "topic": "physics", "about": "energy", "sort": "latest",
              "cursor": "eyJ2IjoxfQ", "limit": 20}),
    "post": (READ_ROUTE, ("postId", "afterComment"), {"postId": POST, "afterComment": REPLY}),
    "topics": (READ_ROUTE, (), {}),
}


class MemoryBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:private")
        self.calls = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        raise AssertionError("community actions must never reach the raw graph bridge")


@pytest.fixture
def service(monkeypatch):
    """허브 서버와 가짜 계정. 계정은 서명·본문 해시·nonce 를 검사하고 정해 둔 답(state['answer'])을 준다."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    monkeypatch.setattr(entitlement, "_seed", lambda: SEED)
    state = {"requests": [], "nonces": set(), "quota": [], "answer": None, "bridge": MemoryBridge()}
    lock = threading.Lock()

    class AccountHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data):
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            claims = edverify.verify_assertion(edverify.secret_to_public(SEED),
                                               self.headers.get("X-Vinc-Assertion"), max_age=120)
            digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
            data = json.loads(raw)
            if (not claims or claims.get("iss") != "mcp" or claims.get("aud") != "account"
                    or claims.get("bh") != digest or claims.get("sub") != data.get("sub")):
                return self.reply(401, {"error": {"code": "not_authenticated", "message": "Invalid service assertion."}})
            with lock:
                if claims["jti"] in state["nonces"]:
                    return self.reply(401, {"error": {"code": "not_authenticated", "message": "Service assertion already used."}})
                state["nonces"].add(claims["jti"])
            if self.path == "/internal/mcp/community-resolve":
                return self.reply(200, {"graph_owner": "community:" + data["community"], "visibility": "public", "role": "reader"})
            with lock:
                state["requests"].append({"path": self.path, "raw": raw, "claims": claims})
            status, body = state["answer"] or (200, {"executed": True, "ok": True})
            return self.reply(status, body)

    account = ThreadingHTTPServer(("127.0.0.1", 0), AccountHandler)
    threading.Thread(target=account.serve_forever, daemon=True).start()
    monkeypatch.setenv("VINC_ACCOUNT_INTERNAL_URL", "http://127.0.0.1:" + str(account.server_port))

    def verify_key(token, team=None):
        if team or token != "vinc_sk_user":
            return None
        return {"owner": "user:" + USER, "scope": "full", "plan": "free"}

    app = H.MCPHTTPApp(state["bridge"], static_token="S", verify_key=verify_key, verify_community=community.resolve,
                      community_write=community.write, community_read=community.read)

    def quota(owner, plan):
        state["quota"].append((owner, plan))
        return None
    app.quota_check = quota
    hub = H.make_server(app, "127.0.0.1", 0)
    threading.Thread(target=hub.serve_forever, daemon=True).start()
    state["hub"] = hub
    yield state
    hub.shutdown(); hub.server_close()
    account.shutdown(); account.server_close()


def rpc(service, name, args):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    req = urllib.request.Request("http://127.0.0.1:" + str(service["hub"].server_port) + "/mcp",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer vinc_sk_user", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read())["result"]


def rest(service, path, args):
    req = urllib.request.Request("http://127.0.0.1:" + str(service["hub"].server_port) + path + "?space=" + A,
                                 data=json.dumps(args).encode(),
                                 headers={"Authorization": "Bearer vinc_sk_user", "Content-Type": "application/json"})
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read())


def call(service, action, payload, **over):
    route = CONTRACT[action][0] if action in CONTRACT else WRITE_ROUTE
    args = {"space": A, "action": action, "payload": payload}
    if route is WRITE_ROUTE:
        args["client_name"] = "  Claude Code  "
    return rpc(service, WRITE if route is WRITE_ROUTE else READ, {**args, **over})


def parsed(result):
    assert result["content"][0]["type"] == "text"
    return json.loads(result["content"][0]["text"])


def test_new_actions_are_on_the_allowlists_after_the_existing_ones():
    assert community.ACTIONS[-3:] == ("post_create", "post_reply", "topic_save")
    assert community.READ_ACTIONS[-3:] == ("feed", "post", "topics")
    assert set(community.UNIFIED_WRITES) | set(community.UNIFIED_READS) == set(CONTRACT)


@pytest.mark.parametrize("action", sorted(CONTRACT))
def test_each_new_action_reaches_the_account_route_with_exactly_its_body(service, action):
    (path, typ, timeout, envelope), names, payload = CONTRACT[action]
    assert tuple(payload) == names
    result = call(service, action, payload)
    assert not result.get("isError")
    assert parsed(result)["executed"] is True
    (sent,) = service["requests"]
    expected = {"sub": USER, "community": A[10:], "action": action, "payload": payload}
    if path == WRITE_ROUTE[0]:
        expected.update({"client_name": "Claude Code", "channel": "mcp", "scope": "full"})
    assert sent["path"] == path and tuple(json.loads(sent["raw"])) == envelope
    assert sent["raw"] == json.dumps(expected, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    claims = sent["claims"]
    assert (claims["typ"], claims["iss"], claims["aud"], claims["sub"]) == (typ, "mcp", "account", USER)
    assert claims["exp"] - claims["iat"] == 120 and claims["jti"]
    assert service["quota"] == [("user:" + USER, "free")]
    assert not service["bridge"].calls


@pytest.mark.parametrize("action", sorted(CONTRACT))
def test_the_tool_reference_names_every_argument_the_account_reads(action):
    (path, *_), names, _payload = CONTRACT[action]
    text = next(t["reference"] for t in tools.TOOLS if t["name"] == (WRITE if path == WRITE_ROUTE[0] else READ))
    assert action in text and all(name in text for name in names)
    assert "only in the unified community space" in text


#: 계정이 형식으로 거절하는 값. 허브가 먼저 거절하고 요청은 나가지 않는다. (동작, payload, 문장 앞부분)
REFUSED = [
    ("post_create", {}, "payload.body must be text of 1..16000 characters"),
    ("post_create", {"body": "   \n\t"}, "payload.body must be text"),
    ("post_create", {"body": 7}, "payload.body must be text"),
    ("post_create", {"body": "x" * 16001}, "payload.body must be text"),
    ("post_create", {"body": "😀" * 8001}, "payload.body must be text"),
    ("post_create", {"body": "x", "title": "t" * 201}, "payload.title must be at most 200 characters"),
    ("post_create", {"body": "x", "kind": "knowledge"}, "payload.kind must be note or question"),
    ("post_create", {"body": "x", "kind": "Note"}, "payload.kind must be note or question"),
    ("post_create", {"body": "x", "topic": "physics/energy/a/b/c"}, "payload.topic must be empty or a topic path"),
    ("post_create", {"body": "x", "topic": "physics energy"}, "payload.topic must be empty or a topic path"),
    ("post_create", {"body": "x", "topic": 3}, "payload.topic must be empty or a topic path"),
    ("post_create", {"body": "x", "about": "Energy"}, "payload.about must be a page address"),
    ("post_create", {"body": "x", "about": "energy-"}, "payload.about must be a page address"),
    ("post_create", {"body": "x", "about": " "}, "payload.about must be a page address"),
    ("post_create", {"body": "x", "about": ["energy"]}, "payload.about must be a page address"),
    ("post_create", {"body": "x", "parentComment": None}, "payload.parentComment is not accepted"),
    ("post_create", {"body": "x", "mutationId": "not-a-uuid"}, "payload.mutationId must be a UUID"),
    ("post_create", {"body": "x", "mutationId": None}, "payload.mutationId must be a UUID"),
    ("post_reply", {"targetId": POST}, "payload.body must be text"),
    ("post_reply", {"body": "x"}, "payload.targetId must be a post id"),
    ("post_reply", {"body": "x", "targetId": "post-1"}, "payload.targetId must be a post id"),
    ("post_reply", {"body": "x", "targetId": POST, "title": "t" * 201}, "payload.title must be at most 200"),
    ("topic_save", {"nameEn": "Energy"}, "payload.path must be a topic path"),
    ("topic_save", {"path": "", "nameEn": "Energy"}, "payload.path must be a topic path"),
    ("topic_save", {"path": "physics", "newPath": "maths", "nameEn": "P"}, "payload takes only path, nameEn"),
    ("topic_save", {"path": "physics"}, "payload.nameEn must be text of 1..120 characters"),
    ("topic_save", {"path": "physics", "nameEn": "x" * 121}, "payload.nameEn must be text"),
    ("topic_save", {"path": "physics", "nameEn": "P", "nameKo": None}, "payload.nameKo must be text"),
    ("topic_save", {"path": "physics", "nameEn": "P", "order": 1.5}, "payload.order must be an integer"),
    ("topic_save", {"path": "physics", "nameEn": "P", "order": True}, "payload.order must be an integer"),
    ("topic_save", {"path": "physics", "nameEn": "P", "order": "1"}, "payload.order must be an integer"),
    ("topic_save", {"path": "physics", "nameEn": "P", "order": 1_000_001}, "payload.order must be an integer"),
    ("topic_save", {"path": "physics", "nameEn": "P", "featured": "yes"}, "payload.featured must be true or false"),
    ("topic_save", {"path": "physics", "nameEn": "P", "status": "archived"}, "payload.status must be active or hidden"),
    ("feed", {"kind": "post"}, "payload.kind must be note, question or knowledge"),
    ("feed", {"kind": 1}, "payload.kind must be note, question or knowledge"),
    ("feed", {"topic": "Physics!"}, "payload.topic must be empty or a topic path"),
    ("feed", {"about": "a--b"}, "payload.about must be a page address"),
    ("feed", {"sort": "top"}, "payload.sort must be latest"),
    ("feed", {"limit": 0}, "payload.limit must be an integer from 1 to 50"),
    ("feed", {"limit": 51}, "payload.limit must be an integer from 1 to 50"),
    ("feed", {"limit": "1e1"}, "payload.limit must be an integer from 1 to 50"),
    ("feed", {"limit": 2.5}, "payload.limit must be an integer from 1 to 50"),
    ("feed", {"limit": True}, "payload.limit must be an integer from 1 to 50"),
    ("feed", {"cursor": "not a cursor"}, "payload.cursor must be the nextCursor"),
    ("feed", {"cursor": 5}, "payload.cursor must be the nextCursor"),
    ("post", {}, "payload.postId must be a post id"),
    ("post", {"postId": " " + POST}, "payload.postId must be a post id"),
    ("post", {"postId": POST, "afterComment": "next"}, "payload.afterComment must be the nextCommentCursor"),
    ("post", {"postId": POST, "afterComment": 1}, "payload.afterComment must be the nextCommentCursor"),
]


@pytest.mark.parametrize("action,payload,fragment", REFUSED)
def test_shape_the_account_would_refuse_is_refused_in_the_hub_before_quota_or_request(service, action, payload, fragment):
    result = call(service, action, payload)
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert text.startswith("Invalid argument: " + fragment) and text.endswith(f" for {action}. Nothing was run.")
    assert result["_meta"]["vinc_invalid_argument"]["argument"] == "payload"
    assert service["requests"] == [] and service["quota"] == [] and not service["bridge"].calls


#: 계정이 받는 경계값. 허브가 여기서 거절하면 계정보다 좁아진 것이다.
ACCEPTED = [
    ("post_create", {"body": "x" * 16000, "title": "t" * 200}),
    ("post_create", {"body": "😀" * 8000}),
    ("post_create", {"body": "\x1c"}),
    ("post_create", {"body": "x", "title": 5, "kind": None, "topic": None, "about": None}),
    ("post_create", {"body": "x", "kind": "note", "topic": " /Physics/Energy/ ", "about": " energy "}),
    ("post_create", {"body": "x", "topic": "", "about": ""}),
    ("post_create", {"body": "x", "title": "   ", "channel": "web", "clientName": "spoof"}),
    ("post_reply", {"targetId": " " + POST.upper() + " ", "body": "x", "title": "ignored"}),
    ("post_reply", {"targetId": POST.replace("-", ""), "body": "x"}),
    ("topic_save", {"path": "physics", "nameEn": " P ", "nameKo": "", "order": -1_000_000,
                    "featured": False, "status": "hidden"}),
    ("topic_save", {"path": "/Physics/", "nameEn": "P", "order": 3.0}),
    ("topic_save", {"path": "physics", "nameEn": "P", "mutationId": MUTATION.upper()}),
    ("feed", {"kind": " knowledge ", "topic": "", "about": "", "sort": " latest", "limit": " 50 ",
              "cursor": "", "other": "ignored"}),
    ("feed", {"limit": 50.0}),
    ("feed", {"limit": "007"}),
    ("feed", {"kind": None, "topic": None, "about": None, "sort": None, "limit": None, "cursor": None}),
    ("post", {"postId": POST.replace("-", ""), "afterComment": ""}),
    ("post", {"postId": POST, "afterComment": None}),
    ("topics", {"anything": [1, 2]}),
]


@pytest.mark.parametrize("action,payload", ACCEPTED)
def test_values_the_account_accepts_leave_the_hub_unchanged(service, action, payload):
    result = call(service, action, payload)
    assert not result.get("isError"), result
    (sent,) = service["requests"]
    assert json.loads(sent["raw"])["payload"] == payload


def test_a_new_action_in_another_tool_still_gets_the_unknown_action_refusal(service):
    for name, action in ((WRITE, "feed"), (READ, "post_create")):
        args = {"space": A, "action": action, "payload": {"body": 1}}
        if name == WRITE:
            args["client_name"] = "Codex"
        result = rpc(service, name, args)
        allowed = ", ".join(community.ACTIONS if name == WRITE else community.READ_ACTIONS)
        assert result["content"][0]["text"] == (
            f"Invalid argument: action={action!r} is not accepted by {name}. Nothing was run. "
            f"Retry with action set to one of: {allowed}.")
    assert service["requests"] == []


#: 계정의 거절 셋과 호출자가 보는 것. 본문은 계정의 fail() 모양({"error":{code,message,request_id}})이다.
#: 같은 mutationId 를 다른 요청에 다시 쓰면 내부 라우트는 MutationConflict 를 edit_conflict(409)로 낸다.
ACCOUNT_REFUSALS = [
    ("replayed mutation", 409, "edit_conflict",
     "This mutation identifier already belongs to another request.", "conflict"),
    ("not the unified space", 400, "bad_request", UNIFIED_ONLY, "invalid_argument"),
    ("rate limit", 429, "too_many_requests", "Please wait before writing again.", "quota_exceeded"),
]


@pytest.mark.parametrize("why,status,code,message,rest_code", ACCOUNT_REFUSALS)
def test_account_refusals_reach_the_mcp_caller_with_the_existing_mapping(service, why, status, code, message, rest_code):
    error = {"code": code, "message": message, "request_id": "req-1"}
    service["answer"] = (status, {"error": error})
    result = call(service, "post_create", {"body": "x", "mutationId": MUTATION})
    assert result["isError"] is True
    assert parsed(result) == {"executed": False, "isError": True, "status": status, "error": error}
    rest_status, rest_body = rest(service, "/v1/community", {"action": "post_create", "client_name": "Codex",
                                                             "payload": {"body": "x"}})
    assert rest_status == status and rest_body["error"]["code"] == rest_code
    assert len(service["requests"]) == 2


def test_feed_read_in_a_community_that_is_not_the_unified_space(service):
    error = {"code": "bad_request", "message": UNIFIED_ONLY, "request_id": "req-2"}
    service["answer"] = (400, {"error": error})
    result = call(service, "feed", {"limit": 10})
    # 지금의 연결 규칙 그대로다: 읽기 도구는 계정 거절을 봉투의 isError 로 올리지 않고 본문에만 싣는다(쓰기 도구만 올린다).
    assert result["isError"] is False
    assert parsed(result) == {"executed": False, "isError": True, "status": 400, "error": error}
    status, body = rest(service, "/v1/community/read", {"action": "feed", "payload": {"limit": 10}})
    assert status == 400 and body["error"]["code"] == "invalid_argument"


def test_rest_routes_take_the_new_actions_and_refuse_bad_shape_without_a_request(service):
    status, body = rest(service, "/v1/community", {"action": "post_reply", "client_name": "Codex",
                                                   "payload": {"targetId": POST, "body": "x"}})
    assert status == 200 and body["executed"] is True
    status, body = rest(service, "/v1/community/read", {"action": "topics", "payload": {}})
    assert status == 200 and body["executed"] is True
    status, body = rest(service, "/v1/community", {"action": "post_reply", "client_name": "Codex",
                                                   "payload": {"targetId": "x", "body": "x"}})
    assert status == 400 and "payload.targetId must be a post id" in json.dumps(body)
    assert [json.loads(r["raw"])["action"] for r in service["requests"]] == ["post_reply", "topics"]
