# -*- coding: utf-8 -*-
"""대기 중인 실행을 MCP 에 싣는 두 길: Tasks 확장(tasks/get·update·cancel)과 MRTR 되묻기.
상태는 실행 기록 Episode 의 props 뿐이라 메모리 브리지가 그 저장을 흉내 낸다.

근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vinc_mcp import modern, rpc, run_tasks  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402

PROFILES = {p["id"]: p for p in json.loads(
    (ROOT / "integrations" / "fixtures" / "output_gate" / "proposed-role-checks.json")
    .read_text(encoding="utf-8"))["profiles"]}
ROLE = "concept:agent-dev-core"
PLAIN = "The change reads correctly; two findings follow."
VERSION = modern.MODERN_VERSIONS[0]
TASKS = {"extensions": {run_tasks.EXTENSION: {}}}
ELICIT = {"elicitation": {}}


class _Bridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:test")
        self.runs = {}

    def request(self, method, path, params=None, body=None, timeout=60):
        if path == "/roles":
            props = dict(PROFILES[ROLE])
            props.update({"mission": "m", "tier": "execute", "rung": "strong", "never": ["merge"],
                          "reads": []})
            return {"executed": True, "found": True, "relations": [], "peers": [],
                    "node": {"id": params["role"], "title": "Agent role",
                             "domain": "vinc/agent/roles", "props": props}}
        if path == "/runs":
            hit = self.runs.get(params["run"])
            return {"kind": "runs", "executed": True,
                    "runs": [{"id": params["run"], "props": json.loads(json.dumps(hit))}] if hit else []}
        if path == "/ingest/fragment":
            node = body["nodes"][0]
            self.runs[node["id"]] = node["props"]
            return {"executed": True, "merged": {"nodes": 1}}
        return {"executed": False, "error": "unexpected path " + path}


def rpc_call(disp, method, params, caps, msg_id=1, **kw):
    params = {**params, "_meta": {modern.META_VERSION: VERSION, modern.META_CAPABILITIES: caps}}
    return disp.handle({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}, **kw)


def tool(disp, name, arguments, caps, **extra):
    return rpc_call(disp, "tools/call", {"name": name, "arguments": arguments, **extra}, caps)


def payload(reply):
    return json.loads(reply["result"]["content"][0]["text"])


def waiting_run(disp, caps, run_id):
    start = payload(tool(disp, "vinc_run_start", {"role": ROLE, "task": "Review.", "run_id": run_id}, caps))
    return start["run"], tool(disp, "vinc_run_submit", {
        "run": start["run"], "output": "Ready to merge once approved.",
        "person_only_requests": [{"action": "merge", "reason": "all checks are green"}]}, caps)


def test_a_waiting_submit_becomes_a_task_for_a_client_that_declares_the_extension():
    disp = rpc.Dispatcher(_Bridge())
    run, reply = waiting_run(disp, TASKS, "t-1")
    result = reply["result"]
    assert result["resultType"] == "task" and result["status"] == "input_required"
    assert result["taskId"] == run + ".q1" and result["ttlMs"] is None
    assert "merge" in result["statusMessage"]
    got = rpc_call(disp, "tasks/get", {"taskId": result["taskId"]}, TASKS)["result"]
    assert got["resultType"] == "complete" and got["status"] == "input_required"
    ask = got["inputRequests"]["decision"]
    assert ask["method"] == "elicitation/create"
    assert ask["params"]["requestedSchema"]["properties"]["answer"]["enum"] == ["approve", "decline"]


def test_an_accepted_answer_resumes_the_run_and_the_task_completes_with_its_view():
    bridge = _Bridge()
    disp = rpc.Dispatcher(bridge)
    run, reply = waiting_run(disp, TASKS, "t-2")
    tid = reply["result"]["taskId"]
    ack = rpc_call(disp, "tasks/update", {"taskId": tid, "inputResponses": {
        "decision": {"action": "accept", "content": {"answer": "approve"}}}}, TASKS)
    assert ack["result"]["resultType"] == "complete"
    assert bridge.runs[run]["answer"] == "approve" and bridge.runs[run]["state"] == "working"
    done = rpc_call(disp, "tasks/get", {"taskId": tid}, TASKS)["result"]
    assert done["status"] == "completed"
    view = json.loads(done["result"]["content"][0]["text"])
    assert view["state"] == "working" and view["run"] == run
    assert done["result"]["isError"] is False


def test_declining_the_form_or_a_wrong_value_is_not_an_answer():
    bridge = _Bridge()
    disp = rpc.Dispatcher(bridge)
    run, reply = waiting_run(disp, TASKS, "t-3")
    tid = reply["result"]["taskId"]
    for responses in ({"decision": {"action": "decline"}}, {"decision": {"action": "cancel"}},
                      {"decision": {"action": "accept", "content": {"answer": "maybe"}}},
                      {"other": {"action": "accept", "content": {"answer": "approve"}}}, "x"):
        rpc_call(disp, "tasks/update", {"taskId": tid, "inputResponses": responses}, TASKS)
        assert bridge.runs[run]["state"] == "waiting" and "answer" not in bridge.runs[run]
    still = rpc_call(disp, "tasks/get", {"taskId": tid}, TASKS)["result"]
    assert still["status"] == "input_required"


def test_cancel_closes_the_task_but_leaves_the_run_waiting_for_a_person():
    bridge = _Bridge()
    disp = rpc.Dispatcher(bridge)
    run, reply = waiting_run(disp, TASKS, "t-4")
    tid = reply["result"]["taskId"]
    assert rpc_call(disp, "tasks/cancel", {"taskId": tid}, TASKS)["result"]["resultType"] == "complete"
    assert rpc_call(disp, "tasks/get", {"taskId": tid}, TASKS)["result"]["status"] == "cancelled"
    assert bridge.runs[run]["state"] == "waiting"
    # 취소된 작업은 답을 받지 않는다. 사람은 도구로 직접 답할 수 있다.
    rpc_call(disp, "tasks/update", {"taskId": tid, "inputResponses": {
        "decision": {"action": "accept", "content": {"answer": "approve"}}}}, TASKS)
    assert bridge.runs[run]["state"] == "waiting"
    out = payload(tool(disp, "vinc_run_submit", {"run": run, "answer": "approve"}, TASKS))
    assert out["state"] == "working"
    assert rpc_call(disp, "tasks/get", {"taskId": tid}, TASKS)["result"]["status"] == "completed"


def test_a_second_question_is_a_new_task_and_the_first_stays_completed():
    bridge = _Bridge()
    disp = rpc.Dispatcher(bridge)
    run, first = waiting_run(disp, TASKS, "t-5")
    tid1 = first["result"]["taskId"]
    tool(disp, "vinc_run_submit", {"run": run, "answer": "approve"}, TASKS)
    second = tool(disp, "vinc_run_submit", {
        "run": run, "output": "Now the migration.",
        "person_only_requests": [{"action": "migration", "reason": "schema change"}]}, TASKS)
    tid2 = second["result"]["taskId"]
    assert tid2 == run + ".q2" and tid1 == run + ".q1"
    assert rpc_call(disp, "tasks/get", {"taskId": tid1}, TASKS)["result"]["status"] == "completed"
    assert rpc_call(disp, "tasks/get", {"taskId": tid2}, TASKS)["result"]["status"] == "input_required"


def test_unknown_tasks_and_missing_capability_are_refused():
    disp = rpc.Dispatcher(_Bridge())
    run, _ = waiting_run(disp, TASKS, "t-6")
    for tid in ("nope", run + ".q9", run + ".q0", "episode:run-missing-00000000.q1", 7):
        err = rpc_call(disp, "tasks/get", {"taskId": tid}, TASKS)["error"]
        assert err["code"] == -32602
    bare = rpc_call(disp, "tasks/get", {"taskId": run + ".q1"}, {})["error"]
    assert bare["code"] == modern.MISSING_CLIENT_CAPABILITY
    assert bare["data"]["requiredCapabilities"] == {"extensions": {run_tasks.EXTENSION: {}}}


def test_mrtr_asks_through_the_original_call_and_resumes_on_the_retry():
    bridge = _Bridge()
    disp = rpc.Dispatcher(bridge)
    run, reply = waiting_run(disp, ELICIT, "t-7")
    result = reply["result"]
    assert result["resultType"] == "input_required"
    assert result["inputRequests"]["decision"]["method"] == "elicitation/create"
    assert bridge.runs[run]["state"] == "waiting"
    retry = tool(disp, "vinc_run_submit", {"run": run, "output": "Ready to merge once approved."},
                 ELICIT, inputResponses={"decision": {"action": "accept",
                                                      "content": {"answer": "approve"}}})
    assert payload(retry)["state"] == "working" and bridge.runs[run]["answer"] == "approve"


def test_a_closed_form_on_the_retry_returns_the_run_unchanged():
    bridge = _Bridge()
    disp = rpc.Dispatcher(bridge)
    run, _ = waiting_run(disp, ELICIT, "t-8")
    retry = tool(disp, "vinc_run_submit", {"run": run, "output": "Ready to merge once approved."},
                 ELICIT, inputResponses={"decision": {"action": "cancel"}})
    assert retry["result"]["isError"] is False
    assert bridge.runs[run]["state"] == "waiting" and "answer" not in bridge.runs[run]


def test_a_client_with_neither_capability_gets_the_plain_waiting_view_and_tasks_are_advertised():
    disp = rpc.Dispatcher(_Bridge())
    _, reply = waiting_run(disp, {}, "t-9")
    view = payload(reply)
    assert view["state"] == "waiting" and view["ask"] == 1 and "resultType" in reply["result"]
    assert reply["result"]["resultType"] == "complete"
    found = rpc_call(disp, "server/discover", {}, {})["result"]
    assert run_tasks.EXTENSION in found["capabilities"]["extensions"]


def test_the_routing_header_for_task_methods_is_the_task_id():
    msg = {"method": "tasks/get", "params": {"taskId": "episode:run-x-00000000.q1",
           "_meta": {modern.META_VERSION: VERSION, modern.META_CAPABILITIES: TASKS}}}
    headers = {"MCP-Protocol-Version": VERSION, "Mcp-Method": "tasks/get",
               "Mcp-Name": "episode:run-x-00000000.q1"}
    assert modern.check_headers(headers, msg, VERSION) is None
    assert modern.check_headers({**headers, "Mcp-Name": "other"}, msg, VERSION) is not None
