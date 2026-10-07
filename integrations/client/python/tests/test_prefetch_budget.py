from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from vinc_client import AsyncVincClient, VincClient, VincInvalidArgument


ROLES = json.loads((Path(__file__).resolve().parents[4] / "agents/roles.json").read_text())["roles"]
SIZES = {d["doc_id"]: d["chars"] for d in json.loads(
    (Path(__file__).parent / "fixtures/prefetch_document_sizes.json").read_text())["documents"]}


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("role", ROLES, ids=lambda r: r["name"])
def test_live_roster_sizes_allow_one_budget_correction(role, async_mode):
    """Replay verified hosted text sizes with synthetic bodies, never copied canonical text."""
    documents = {}
    for key in dict.fromkeys(role["props"].get("reads", [])):
        doc_id = key.removeprefix("concept:doc.")
        tail = '\n"필수 끝 규칙" ```'
        text = "x" * (SIZES[doc_id] - len(tail)) + tail
        documents[doc_id] = {"doc_id": doc_id, "executed": True, "verified": True,
                             "text": text, "content_hash": hashlib.sha256(text.encode()).hexdigest()}
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json={"found": True, "id": role["id"],
                "title": role["label"], "domain": role["domain"], "props": role["props"]})
        if request.url.path.startswith("/v1/documents/"):
            return httpx.Response(200, json=documents[request.url.path.rsplit("/", 1)[-1]])
        if request.url.path.endswith("brief"):
            return httpx.Response(200, json={"status": "resolved", "node": {
                "id": "decision:task-evidence", "cite_id": "decision:task-evidence"}})
        return httpx.Response(200, json={"node_hits": []})

    client = (AsyncVincClient if async_mode else VincClient)(
        "vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler))

    def prefetch(**kwargs):
        value = client.prefetch(role["id"], "Review.", **kwargs)
        return asyncio.run(value) if async_mode else value

    with pytest.raises(VincInvalidArgument) as caught:
        prefetch()
    error = caught.value
    assert error.code == "prefetch_budget_exceeded"
    size = error.details["required_size"]
    assert error.details["max_chars"] == 24000
    assert f"required_size={size}" in error.message
    assert sum(path.startswith("/v1/documents/") for path in seen) == len(documents)
    assert all(path.startswith(("/v1/roles/", "/v1/documents/", "/v1/brief", "/v1/search")) for path in seen)
    assert sorted(p for p in seen if p in ("/v1/brief", "/v1/search")) in ([], ["/v1/brief", "/v1/search"])

    block = prefetch(max_chars=size)
    assert len(block) == size
    assert "decision:task-evidence" not in block
    for document in documents.values():
        assert json.dumps(document["text"], ensure_ascii=False) in block
        assert document["content_hash"] in block

    with pytest.raises(VincInvalidArgument) as too_small:
        prefetch(max_chars=size - 1)
    assert too_small.value.details["required_size"] == size
    recommended = error.details["recommended_max_chars"]
    block = prefetch(max_chars=recommended)
    assert size < len(block) <= recommended
    assert "decision:task-evidence" in block


@pytest.mark.parametrize("async_mode", [False, True])
def test_overflow_does_not_hide_a_later_missing_required_document(async_mode):
    seen = []
    text = "x" * 25000

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json={"found": True, "id": "concept:agent-test",
                "props": {"mission": "x" * 25000, "reads": ["large", "missing", "unread"]}})
        if request.url.path.endswith("large"):
            return httpx.Response(200, json={"doc_id": "large", "executed": True,
                "verified": True, "text": text, "content_hash": hashlib.sha256(text.encode()).hexdigest()})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    client = (AsyncVincClient if async_mode else VincClient)(
        "vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler), prefetch_concurrency=1)
    with pytest.raises(VincInvalidArgument, match="prefetch_spec_unresolved"):
        value = client.prefetch("concept:agent-test", "Review.")
        if async_mode:
            asyncio.run(value)
    assert len(seen) == 3
    assert seen[-1] == "/v1/documents/missing"
