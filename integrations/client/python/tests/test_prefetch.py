from __future__ import annotations

import asyncio
import json

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
        assert all(n in block for n in ("concept:agent-reviewer", "concept:spec-fixture",
                                        "decision:fixture", "record:lesson", "DATA"))
    assert len(seen) == 8
    assert all(r.method == "GET" or r.url.path.endswith(("brief", "search")) for r in seen)


@pytest.mark.parametrize("status", ["none", "choose"])
def test_required_spec_miss_or_tie_stops_prefetch(status):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"found": True, "id": "concept:agent-reviewer",
                                            "props": {"reads": ["required-spec"]}})
        return httpx.Response(200, json={"status": status})

    with pytest.raises(VincInvalidArgument, match="prefetch_spec_unresolved"):
        VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler)).prefetch(
            "concept:agent-reviewer", "Review.")


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
