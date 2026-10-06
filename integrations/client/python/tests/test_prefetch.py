from __future__ import annotations

import asyncio
import json
import hashlib

import httpx
import pytest
from vinc_client import AsyncVincClient, VincClient, VincInvalidArgument, VincNotFound


@pytest.mark.parametrize("async_mode", [False, True])
def test_prefetch_reads_role_specs_and_task_evidence_without_writes_or_cache(async_mode):
    seen = []
    role = {"found": True, "id": "concept:agent-reviewer", "domain": "agent/engineering",
            "props": {"reads": ["vinc.spec.fixture"], "mission": "Review evidence."}}

    def handler(request):
        seen.append(request)
        assert request.url.params.get("space") == "01a0a0e0-3f91-7128-9876-6b543f28de0a"
        if request.url.path.startswith("/v1/documents/"):
            text = "Keep all rules.\n" + "x" * 5000 + "\nMANDATORY TAIL RULE"
            return httpx.Response(200, json={"executed": True, "doc_id": "vinc.spec.fixture",
                "verified": True, "text": text,
                "content_hash": hashlib.sha256(text.encode()).hexdigest()})
        if request.method == "GET":
            return httpx.Response(200, json=role)
        query = json.loads(request.content)["query"]
        if request.url.path.endswith("brief"):
            nid = "concept:spec-fixture" if query == "vinc.spec.fixture" else "decision:fixture"
            return httpx.Response(200, json={"status": "resolved",
                "node": {"id": nid, "cite_id": nid, "title": "Fixture"}, "one_liner": "Keep it."})
        assert query == "Review the policy.\nRole topic: agent/engineering"
        return httpx.Response(200, json={"node_hits": [{"id": "record:lesson",
                                                        "cite_id": "record:lesson",
                                                        "title": "Prior lesson"}]})

    client = (AsyncVincClient if async_mode else VincClient)(
        "vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler))
    for _ in range(2):
        value = client.prefetch("concept:agent-reviewer", "Review the policy.",
                                space="01a0a0e0-3f91-7128-9876-6b543f28de0a")
        block = asyncio.run(value) if async_mode else value
        assert all(n in block for n in ("concept:agent-reviewer", "vinc.spec.fixture",
                                        "decision:fixture", "record:lesson", "DATA",
                                        "MANDATORY TAIL RULE"))
    assert len(seen) == 8
    assert all(r.method == "GET" or r.url.path.endswith(("brief", "search")) for r in seen)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("status", ["none", "choose"])
def test_required_spec_miss_or_tie_stops_prefetch(status, async_mode):
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json={"found": True, "id": "concept:agent-reviewer",
                                            "props": {"reads": ["required-spec", "later-spec"]}})
        return httpx.Response(200, json={"status": status})

    with pytest.raises(VincInvalidArgument, match="prefetch_spec_unresolved"):
        client = (AsyncVincClient if async_mode else VincClient)(
            "vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler), prefetch_concurrency=1)
        value = client.prefetch("concept:agent-reviewer", "Review.")
        if async_mode:
            asyncio.run(value)
    assert len(seen) == 2
    assert seen[-1].url.path == "/v1/documents/required-spec"


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("defect", ["unverified", "hash", "wrong_id", "partial", "missing"])
def test_required_document_integrity_failure_stops_before_later_reads(async_mode, defect):
    seen = []
    doc = {"doc_id": "vinc.spec.fixture", "executed": True, "verified": True, "text": "All rules.",
           "content_hash": hashlib.sha256(b"All rules.").hexdigest()}
    if defect == "unverified":
        doc["verified"] = False
    elif defect == "hash":
        doc["text"] = "Different rules."
    elif defect == "wrong_id":
        doc["doc_id"] = "another"
    elif defect == "partial":
        doc["partial"] = True
    def handler(request):
        seen.append(request)
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json={"found": True, "id": "concept:agent-reviewer",
                "props": {"reads": ["concept:doc.vinc.spec.fixture", "later-spec"]}})
        return httpx.Response(404 if defect == "missing" else 200, json=doc)
    client = (AsyncVincClient if async_mode else VincClient)(
        "vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler), prefetch_concurrency=1)
    with pytest.raises(VincInvalidArgument, match="prefetch_spec_unresolved"):
        value = client.prefetch("concept:agent-reviewer", "Review.")
        if async_mode:
            asyncio.run(value)
    assert len(seen) == 2
    assert seen[-1].url.path == "/v1/documents/vinc.spec.fixture"


@pytest.mark.parametrize("async_mode", [False, True])
def test_required_document_overflow_discards_optional_task_results(async_mode):
    seen = []
    text = "Mandatory rule " * 3000
    def handler(request):
        seen.append(request)
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json={"found": True, "id": "concept:agent-reviewer",
                "props": {"reads": ["vinc.spec.large", "later-spec"]}})
        return httpx.Response(200, json={"executed": True, "doc_id": request.url.path.rsplit("/", 1)[-1],
            "verified": True, "text": text, "content_hash": hashlib.sha256(text.encode()).hexdigest()})
    client = (AsyncVincClient if async_mode else VincClient)(
        "vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler))
    with pytest.raises(VincInvalidArgument, match="prefetch_budget_exceeded") as caught:
        value = client.prefetch("concept:agent-reviewer", "Review.")
        if async_mode:
            asyncio.run(value)
    assert sum(r.method == "GET" for r in seen) == 3
    assert all(r.method == "GET" or r.url.path.endswith(("brief", "search")) for r in seen)
    assert caught.value.details["required_size"] > 2 * len(text)
    assert str(caught.value.details["required_size"]) in caught.value.message


def test_missing_role_does_not_invent_a_contract():
    with pytest.raises(VincNotFound):
        VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(lambda r:
            httpx.Response(404, json={"error": {"code": "not_found"}}))).prefetch(
                "concept:agent-unknown", "Review.")


def test_oversized_prefetch_fails_without_silent_role_truncation():
    def handler(request):
        body = ({"found": True, "id": "concept:agent-reviewer", "props": {"reads": [],
                  "mission": "Keep every contract field."}} if request.method == "GET"
                else {"status": "none"})
        return httpx.Response(200, json=body)

    with pytest.raises(VincInvalidArgument, match="prefetch_budget_exceeded"):
        VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler)).prefetch(
            "concept:agent-reviewer", "Review.", max_chars=100)
