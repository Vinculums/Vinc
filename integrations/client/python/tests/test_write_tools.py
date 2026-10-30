"""역할 기준 쓰기 표면: 쓸 수 있는 역할만 쓰기 도구를 받고, 그 도구는 문장으로 답한다."""
from __future__ import annotations

import json

import httpx
import pytest
from vinc_client import VincClient, may_write
from vinc_client.write_tools import run_doc_put, run_ingest

SK = "vinc_sk_" + "b" * 32


@pytest.mark.parametrize("props, expected", [
    ({"writes_graph": ["record"]}, True),
    ({"writes_graph": []}, False),
    ({}, False),
    ({"writes_graph": ["record"], "capabilities": {"deny": ["vinc.write"]}}, False),
    ({"writes_graph": ["record"], "capabilities": {"deny": ["vinc.*"]}}, False),
    ({"writes_graph": ["record"], "capabilities": {"allow": ["github.*"]}}, False),
    ({"writes_graph": ["record"], "capabilities": {"allow": ["vinc.*"]}}, True),
    ({"writes_graph": ["record"], "capabilities": {"deny": ["vinc.delete"]}}, True),
])
def test_a_role_may_write_when_it_writes_the_graph_and_no_capability_closes_it(props, expected):
    assert may_write({"id": "concept:agent-x", "props": props}) is expected


def _client(handler):
    return VincClient(SK, transport=httpx.MockTransport(handler))


def test_doc_put_edits_by_id_and_creates_without_one():
    seen = []

    def hub(request):
        seen.append(request)
        return httpx.Response(200, json={"doc_id": "vinc.x", "content_hash": "h1"})

    out = run_doc_put(_client(hub), "body", doc_id="vinc.x", expected_content_hash="h0")
    assert out == "Saved document vinc.x (content_hash h1)."
    assert (seen[0].method, seen[0].url.path) == ("PUT", "/v1/documents/vinc.x")
    assert json.loads(seen[0].content) == {"text": "body", "expected_content_hash": "h0"}
    run_doc_put(_client(hub), "new", title="T")
    assert (seen[1].method, seen[1].url.path) == ("POST", "/v1/documents")


def test_ingest_reports_counts_a_partial_write_and_a_refusal_as_sentences():
    ok = run_ingest(_client(lambda r: httpx.Response(200, json={
        "merged": {"nodes": {"created": 1, "matched": 0}, "edges": {"created": 2}}})),
        [{"id": "record:x", "type": "record"}])
    assert ok == "Saved: 1 nodes created, 0 updated, 2 edges created."
    part = run_ingest(_client(lambda r: httpx.Response(200, json={
        "merged": {}, "partial": True, "warnings": ["edge_disallowed_pair"]})),
        [{"id": "record:x", "type": "record"}])
    assert part.startswith("Saved in part.") and "edge_disallowed_pair" in part
    bad = run_ingest(_client(lambda r: httpx.Response(400, json={
        "error": {"code": "invalid_argument", "message": "no id"}})), [{}])
    assert bad.startswith("Vinc refused the write (invalid_argument)")
