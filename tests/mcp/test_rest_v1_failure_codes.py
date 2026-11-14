"""REST v1 이 도구 실패의 상태를 고르는 법: 기계 코드(`store/failure_codes.py`)가 먼저, 문장은 대체.

사이드카(`api/`)가 문장을 고쳐도 HTTP 상태가 바뀌지 않아야 한다. 여기 표는 진짜 `api/` 함수를 불러 그 답을
`rest_api._failure` 에 넣는다. MCP 답은 코드가 생기기 전과 같은 바이트여야 한다.
"""
from __future__ import annotations

import io
import json
import re
import urllib.error
from pathlib import Path

import pytest

from api import (decide_read, diagnose, docextract, docstore, eval_cards, events, graph_edit,
                 merge_candidates, neighbors, node_read, roles, runs)
from store import failure_codes as fc
from vinc_mcp import bridge, encoding, rest_api, rpc
from vinc_mcp.rest_api import CODE, SHAPE, TEXT, _failure

from test_rest_v1 import Server

ROOT = Path(__file__).resolve().parents[2]
OWNER = "user:u1"
#: 표현이 어떤 낱말과도 겹치지 않는 문장. 코드가 없으면 `_failure` 가 읽을 단서가 없다.
REWORDED = "zzz this sentence matches nothing the table reads"


class _Empty:
    """빈 그래프. `run` 은 늘 빈 목록이다."""

    def run(self, *_args, **_kwargs):
        return []

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _no_store_unless_given(monkeypatch):
    # 스토어를 직접 주지 않은 호출은 "스토어가 없다"로 답한다.
    for module in (graph_edit, merge_candidates, neighbors, roles, diagnose, decide_read, node_read):
        monkeypatch.setattr(module, "open_store", lambda _cfg: None)


_E = _Empty()

# (도구, 이름, 호출, 답이 싣는 코드, REST 상태, REST v1 코드)
SITES = [
    ("vinc_merge_nodes", "not_concepts", lambda: graph_edit.merge_nodes("record:a", "concept:b", owner=OWNER, store=_E),
     fc.MERGE_PAIR_NOT_CONCEPTS, 400, "invalid_argument"),
    ("vinc_merge_nodes", "same_node", lambda: graph_edit.merge_nodes("concept:a", "concept:a", owner=OWNER, store=_E),
     fc.MERGE_PAIR_SAME_NODE, 400, "invalid_argument"),
    ("vinc_merge_nodes", "no_store", lambda: graph_edit.merge_nodes("concept:a", "concept:b", owner=OWNER),
     fc.NO_REACHABLE_STORE, 503, "upstream_unavailable"),
    ("vinc_delete_edge", "unsupported_type",
     lambda: graph_edit.delete_edge("concept:a", "nonsense", "concept:b", owner=OWNER, store=_E),
     fc.UNSUPPORTED_EDGE_TYPE, 400, "invalid_argument"),
    # 지우기의 "스토어 없음"은 `found:false` 가 먼저 읽혀 404 다. 코드를 달아도 상태는 그대로다.
    ("vinc_delete_edge", "no_store", lambda: graph_edit.delete_edge("concept:a", "relates_to", "concept:b", owner=OWNER),
     fc.NO_REACHABLE_STORE, 404, "not_found"),
    ("vinc_delete", "no_store", lambda: graph_edit.delete_node("concept:a", owner=OWNER),
     fc.NO_REACHABLE_STORE, 404, "not_found"),
    ("vinc_merge_reject", "same_ids",
     lambda: merge_candidates.reject_merge_pair(owner=OWNER, a_id="concept:a", b_id="concept:a", store=_E),
     fc.DISTINCT_NODE_IDS_REQUIRED, 400, "invalid_argument"),
    ("vinc_merge_reject", "no_store",
     lambda: merge_candidates.reject_merge_pair(owner=OWNER, a_id="concept:a", b_id="concept:b"),
     fc.NO_REACHABLE_STORE, 503, "upstream_unavailable"),
    ("vinc_merge_rejections", "no_store", lambda: merge_candidates.list_merge_rejections(owner=OWNER),
     fc.NO_REACHABLE_STORE, 503, "upstream_unavailable"),
    ("vinc_neighbors", "domain_no_store", lambda: neighbors.neighbors("domain:a", owner=OWNER),
     fc.NO_REACHABLE_STORE, 503, "upstream_unavailable"),
    ("vinc_edges", "invalid_argument",
     lambda: neighbors.node_edges("concept:a", nodes=["concept:b"], owner=OWNER, store=_E),
     fc.INVALID_EDGES_QUERY, 400, "invalid_argument"),
    ("vinc_edges", "no_store", lambda: neighbors.node_edges("concept:a", owner=OWNER),
     fc.NO_REACHABLE_STORE, 503, "upstream_unavailable"),
    ("vinc_doc_get", "unknown", lambda: docstore.get_doc(_E, "doc:x", owner=OWNER),
     fc.DOCUMENT_NOT_FOUND, 404, "not_found"),
    ("vinc_doc_put", "no_owner", lambda: docstore.put_doc(_E, owner="", text="x"),
     fc.OWNER_REQUIRED, 400, "invalid_argument"),
    ("vinc_doc_put", "bad_valid_from", lambda: docstore.put_doc(_E, owner=OWNER, text="x", valid_from="soon"),
     fc.INVALID_VALIDITY_DATE, 400, "invalid_argument"),
    ("vinc_doc_extract", "unknown", lambda: docextract.extract_doc(_E, "doc:x", owner=OWNER),
     fc.DOCUMENT_NOT_FOUND, 404, "not_found"),
    ("vinc_eval_cards", "no_owner", lambda: eval_cards.list_cards(_E, owner="", role="concept:agent-x"),
     fc.OWNER_REQUIRED, 400, "invalid_argument"),
    ("vinc_eval_cards", "no_role", lambda: eval_cards.list_cards(_E, owner=OWNER, role=""),
     fc.ROLE_REQUIRED, 400, "invalid_argument"),
    ("vinc_role", "no_owner", lambda: roles.read(owner="", store=_E),
     fc.OWNER_REQUIRED, 400, "invalid_argument"),
    ("vinc_role", "bad_pagination", lambda: roles.read(owner=OWNER, store=_E, offset=-1),
     fc.INVALID_ROLE_PAGINATION, 400, "invalid_argument"),
    ("vinc_role", "no_store", lambda: roles.read(owner=OWNER),
     fc.NO_REACHABLE_STORE, 503, "upstream_unavailable"),
    ("vinc_events", "no_owner", lambda: events.scan(_E, owner="", since=None),
     fc.OWNER_REQUIRED, 400, "invalid_argument"),
    ("vinc_runs", "no_owner", lambda: runs.list_runs(_E, owner=""),
     fc.OWNER_REQUIRED, 400, "invalid_argument"),
    ("vinc_diagnose", "no_store", lambda: diagnose.component_diagnosis(owner=OWNER),
     fc.NO_REACHABLE_STORE, 503, "upstream_unavailable"),
    ("vinc_diagnose", "domain_roots_no_owner", lambda: diagnose.domain_root_diagnosis(owner="", store=_E),
     fc.OWNER_REQUIRED, 400, "invalid_argument"),
    ("vinc_decide_paths", "empty_paths", lambda: decide_read.decide_paths([], owner=OWNER, store=_E),
     fc.INVALID_PATHS, 400, "invalid_argument"),
    ("vinc_decide_paths", "too_many",
     lambda: decide_read.decide_paths([f"r:p{i}" for i in range(500)], owner=OWNER, store=_E),
     fc.INVALID_PATHS, 400, "invalid_argument"),
    ("vinc_decide_paths", "no_store", lambda: decide_read.decide_paths(["r:p"], owner=OWNER),
     fc.NO_REACHABLE_STORE, 503, "upstream_unavailable"),
    ("vinc_node", "no_store", lambda: node_read.node_context(node="concept:a", owner=OWNER),
     fc.NO_REACHABLE_STORE, 503, "upstream_unavailable"),
]
_IDS = [f"{tool}-{name}" for tool, name, *_ in SITES]


def _reworded(result: dict) -> dict:
    """`error`·`note` 의 문장을 다른 말로 바꾼 사본. 코드 칸은 그대로다."""
    return {key: (REWORDED if key in ("error", "note") and value else value) for key, value in result.items()}


def _without_code(result: dict) -> dict:
    return {key: value for key, value in result.items() if key != fc.KEY}


@pytest.mark.parametrize("tool,name,call,code,status,v1_code", SITES, ids=_IDS)
def test_api_answer_carries_its_code_and_maps_to_the_status(tool, name, call, code, status, v1_code):
    got = call()
    assert got[fc.KEY] == code
    assert _failure(tool, got)[:2] == (status, v1_code)


@pytest.mark.parametrize("tool,name,call,code,status,v1_code", SITES, ids=_IDS)
def test_rewording_the_sentence_does_not_change_the_status(tool, name, call, code, status, v1_code):
    got = call()
    assert _failure(tool, _reworded(got))[:2] == (status, v1_code)


@pytest.mark.parametrize("tool,name,call,code,status,v1_code", SITES, ids=_IDS)
def test_without_the_code_a_reworded_sentence_is_only_what_the_fallback_reads(
        tool, name, call, code, status, v1_code):
    got = call()
    # 코드가 없는 답(옛 사이드카)은 문장으로 읽는다. 같은 문장이면 같은 상태다.
    assert _failure(tool, _without_code(got))[:2] == (status, v1_code)
    if got.get("found") is not False:
        # 코드도 없고 문장도 단서가 없으면 어디에도 안 걸려 500 이다 — 코드가 하는 일이 이것이다.
        assert _failure(tool, _reworded(_without_code(got)))[:2] == (500, "internal")


def test_the_code_wins_over_a_sentence_that_points_elsewhere():
    answer = {"executed": False, "error": "node not found: x", "note": "unavailable",
              fc.KEY: fc.OWNER_REQUIRED}
    assert _failure("vinc_runs", answer)[:2] == (400, "invalid_argument")
    answer = {"executed": True, "error": "invalid", fc.KEY: fc.MERGE_PAIR_SAME_NODE}
    assert _failure("vinc_merge_nodes", answer)[:2] == (400, "invalid_argument")


def test_the_message_stays_the_sentence_the_answer_carries():
    got = graph_edit.merge_nodes("concept:a", "concept:a", owner=OWNER, store=_E)
    assert _failure("vinc_merge_nodes", got)[2] == "keep and drop are the same node"
    got = graph_edit.delete_edge("concept:a", "nonsense", "concept:b", owner=OWNER, store=_E)
    assert _failure("vinc_delete_edge", got)[2] == got["note"]


# ── 줄이 무엇을 읽는지 ─────────────────────────────────────────────────────────

def test_every_row_says_what_it_reads():
    assert {row.basis for row in rest_api._FAILURES} == {CODE, SHAPE, TEXT}
    with pytest.raises(ValueError):
        rest_api._FailureRow("guess", lambda _f: True, 500)


def test_every_text_row_has_a_code_row_before_it_that_answers_the_same():
    rows = rest_api._FAILURES
    for index, row in enumerate(rows):
        if row.basis != TEXT:
            continue
        earlier = [(r.status, r.code) for r in rows[:index] if r.basis == CODE]
        assert (row.status, row.code) in earlier, f"text row {index} has no code row ahead of it"


def test_code_rows_read_no_sentence():
    # 코드 줄의 술어는 `reason` 을 읽지 않는다: 같은 코드에 다른 문장을 붙여도 답이 같다.
    for code in fc.ALL | {fc.MERGE_PAIR_SAME_NODE, fc.MERGE_PAIR_NOT_CONCEPTS, fc.UNSUPPORTED_EDGE_TYPE,
                          fc.SIDECAR_SERVER_ERROR, fc.SIDECAR_WRITE_UNCONFIRMED}:
        base = {"executed": False, fc.KEY: code}
        a = _failure("vinc_x", {**base, "error": "a", "note": "b"})
        b = _failure("vinc_x", {**base, "error": "unknown invalid unavailable", "note": "not found"})
        assert a is not None and b is not None and a[:2] == b[:2], code


def test_a_code_in_error_is_read_like_error_code():
    # `error` 가 이미 코드이거나 `코드: 문장` 꼴이면 그 앞쪽이 코드다.
    assert _failure("vinc_x", {"executed": False, "error": "receipt_read_unavailable"})[:2] == (503, "upstream_unavailable")
    assert _failure("vinc_x", {"executed": False, "error": "invalid_dry_run"})[:2] == (400, "invalid_argument")
    assert _failure("vinc_x", {"executed": False, "error": "task_required",
                               "note": "node not found"})[:2] == (400, "invalid_argument")


# ── 코드 철자 ─────────────────────────────────────────────────────────────────

def test_code_groups_are_disjoint_and_spelled_in_snake_case():
    groups = [fc.NOT_FOUND, fc.INVALID, fc.UNAVAILABLE]
    assert sum(len(g) for g in groups) == len(fc.ALL)
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", code) for code in fc.ALL)


def _source(*parts: str) -> str:
    return "\n".join(path.read_text(encoding="utf-8")
                     for part in parts for path in sorted((ROOT / part).rglob("*.py")))


def test_every_code_this_module_spells_has_a_producer_and_every_other_has_its_own_spelling():
    api_source = _source("api") + (ROOT / "vinc_mcp" / "bridge.py").read_text(encoding="utf-8")
    spelled = {name: value for name, value in vars(fc).items()
               if name.isupper() and isinstance(value, str) and name != "KEY"}
    for name in spelled:
        assert re.search(rf"\b(?:fc|failure_codes)\.{name}\b", api_source), f"{name} has no producer"
    # 이 파일이 철자를 정하지 않는 코드는 생산자의 문자열로 따로 있어야 한다(생산자가 이름을 바꾸면 여기서 잡힌다).
    produced = _source("api", "vinc_mcp")
    for code in fc.ALL - set(spelled.values()):
        assert f'"{code}"' in produced, f"{code} is not written by any producer"


def test_the_public_openapi_error_schema_does_not_name_codes():
    from vinc_mcp.openapi_v1 import document
    schema = json.dumps(document(), ensure_ascii=False)
    assert fc.KEY not in schema


# ── MCP 는 코드를 모른다 ───────────────────────────────────────────────────────

class _Bridge:
    owner = "user:local"

    def __init__(self, answer):
        self.answer = answer

    def request(self, *_a, **_k):
        return dict(self.answer)


def _mcp_text(answer: dict, name: str, **args) -> dict:
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    return rpc.Dispatcher(_Bridge(answer), hub=True).handle(request)


def test_mcp_answer_is_the_same_bytes_with_or_without_the_code():
    coded = graph_edit.merge_nodes("concept:a", "concept:a", owner=OWNER, store=_E)
    assert fc.KEY in coded
    args = {"keep_id": "concept:a", "drop_id": "concept:a"}
    with_code = _mcp_text(coded, "vinc_merge_nodes", **args)
    without = _mcp_text(_without_code(coded), "vinc_merge_nodes", **args)
    assert with_code == without
    assert fc.KEY not in json.dumps(with_code)
    text = with_code["result"]["content"][0]["text"]
    assert json.loads(text)["error"] == "keep and drop are the same node"


@pytest.mark.parametrize("tool,name,call,code,status,v1_code", SITES, ids=_IDS)
def test_mcp_text_of_every_site_has_no_code(tool, name, call, code, status, v1_code):
    got = call()
    assert fc.KEY not in encoding.public_result(got)
    assert encoding.serialize_result(encoding.public_result(got)) == encoding.serialize_result(_without_code(got))


def test_public_result_keeps_an_uncoded_answer_as_is():
    answer = {"executed": True, "rows": []}
    assert encoding.public_result(answer) is answer
    assert encoding.public_result(["x"]) == ["x"]


# ── REST 서버 끝에서 끝까지 ────────────────────────────────────────────────────

def test_rest_server_answers_the_status_of_the_code_when_the_sentence_is_reworded():
    srv = Server()
    try:
        srv.bridge.override = {"kind": "merge", "executed": True, "error": REWORDED,
                               fc.KEY: fc.MERGE_PAIR_SAME_NODE}
        status, _, body = srv.request("POST", "/v1/nodes/merge", body={"keep_id": "concept:a", "drop_id": "concept:a"})
        assert status == 400 and body["error"]["code"] == "invalid_argument"
        assert body["error"]["message"] == REWORDED
        assert fc.KEY not in json.dumps(body)
    finally:
        srv.close()


def test_rest_server_still_reads_the_sentence_of_an_answer_with_no_code():
    srv = Server()
    try:
        srv.bridge.override = {"kind": "merge", "executed": True, "error": "keep and drop are the same node"}
        status, _, body = srv.request("POST", "/v1/nodes/merge", body={"keep_id": "concept:a", "drop_id": "concept:a"})
        assert status == 400 and body["error"]["code"] == "invalid_argument"
    finally:
        srv.close()


def test_rest_server_503s_on_a_sidecar_that_cannot_be_reached_by_kind():
    srv = Server()

    def refuse(*_a, **_k):
        raise bridge.SidecarUnreachable("the words here do not matter")

    srv.bridge.request = refuse
    try:
        status, _, body = srv.request("GET", "/v1/domains")
        assert status == 503 and body["error"]["code"] == "upstream_unavailable"
    finally:
        srv.close()


# ── 브리지가 싣는 코드 ─────────────────────────────────────────────────────────

def test_bridge_marks_a_sidecar_5xx_without_json_and_not_a_4xx(monkeypatch):
    def urlopen_with(code):
        def _open(*_a, **_k):
            raise urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b"<html>"))
        return _open

    monkeypatch.setattr(bridge.urllib.request, "urlopen", urlopen_with(502))
    got = bridge.Bridge(base="http://127.0.0.1:1", token="t").request("GET", "/x")
    assert got == {"error": "sidecar HTTP 502", fc.KEY: fc.SIDECAR_SERVER_ERROR}
    assert _failure("vinc_domains", got)[:2] == (503, "upstream_unavailable")
    assert _failure("vinc_domains", {"error": "sidecar HTTP 502"})[:2] == (503, "upstream_unavailable")
    monkeypatch.setattr(bridge.urllib.request, "urlopen", urlopen_with(404))
    got = bridge.Bridge(base="http://127.0.0.1:1", token="t").request("GET", "/x")
    assert got == {"error": "sidecar HTTP 404"}


def test_bridge_marks_a_write_that_ended_without_an_answer(monkeypatch):
    def _open(*_a, **_k):
        raise TimeoutError("late")

    monkeypatch.setattr(bridge.urllib.request, "urlopen", _open)
    got = bridge.Bridge(base="http://127.0.0.1:1", token="t").request("POST", "/x", body={})
    assert got[fc.KEY] == fc.SIDECAR_WRITE_UNCONFIRMED
    status, code, _message, details = _failure("vinc_doc_put", got)
    assert (status, code) == (503, "upstream_unavailable")
    assert details == {"retryable": False, "write_may_have_applied": True}
    # 코드를 떼고 문장을 바꿔도 `write_may_have_applied` 칸이 없으면 대체 줄만 남는다.
    assert _failure("vinc_doc_put", {"error": got["error"]})[:2] == (503, "upstream_unavailable")
