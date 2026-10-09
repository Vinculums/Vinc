"""필수 문서는 읽히지만 미러가 없거나 조회에 실패한 경우의 인용 계약."""
from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest

from vinc_client import AsyncVincClient, VincClient, VincError


KEY = "vinc_ro_" + "a" * 32
ROLE = "concept:agent-reviewer"
DOC = "vinc.spec.ui-copy-ko"
MIRROR = "concept:doc." + DOC
SPACE = "01a0a0e0-3f91-7128-9876-6b543f28de0a"
TEXT = "Keep the full required reading, including its last rule."


def handler_for(node_response, seen, read=DOC):
    def handler(request):
        seen.append(request)
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json={"found": True, "id": ROLE,
                "props": {"reads": [read]}})
        if request.url.path.startswith("/v1/documents/"):
            return httpx.Response(200, json={"doc_id": DOC, "executed": True,
                "verified": True, "text": TEXT, "content_hash": hashlib.sha256(TEXT.encode()).hexdigest(),
                "cite_id": MIRROR})
        if request.url.path.startswith("/v1/nodes/"):
            assert request.url.path == "/v1/nodes/" + MIRROR
            return node_response()
        return httpx.Response(200, json={"status": "none"})
    return handler


def prefetch(async_mode, handler, *, concurrency=8):
    cls = AsyncVincClient if async_mode else VincClient
    async def run():
        async with cls(KEY, transport=httpx.MockTransport(handler),
                       prefetch_concurrency=concurrency) as client:
            return await client.prefetch(ROLE, "Check the copy.", space=SPACE)
    if async_mode:
        return asyncio.run(run())
    with cls(KEY, transport=httpx.MockTransport(handler), prefetch_concurrency=concurrency) as client:
        return client.prefetch(ROLE, "Check the copy.", space=SPACE)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("concurrency", [1, 8])
@pytest.mark.parametrize("missing_response", [
    {"executed": True, "found": False, "node": None},
    {"error": {"code": "not_found"}},
])
def test_readable_document_with_missing_mirror_keeps_text_without_inventing_id(
        async_mode, concurrency, missing_response):
    seen = []
    status = 404 if "error" in missing_response else 200
    block = prefetch(async_mode, handler_for(
        lambda: httpx.Response(status, json=missing_response), seen), concurrency=concurrency)
    assert f"{DOC} (no citable id)" in block
    assert MIRROR not in block and TEXT in block
    assert "do not invent one" in block
    assert len(seen) == 5
    assert all(req.url.params["space"] == SPACE for req in seen)
    assert all(req.method == "GET" or req.url.path in ("/v1/brief", "/v1/search") for req in seen)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("read", [DOC, MIRROR])
def test_verified_exact_mirror_is_citable_and_node_body_is_not_copied(async_mode, read):
    seen = []
    block = prefetch(async_mode, handler_for(lambda: httpx.Response(200, json={
        "executed": True, "found": True, "node": {"id": MIRROR, "props": {"private": "hidden-body"}}}),
        seen, read))
    assert f"(cite as {MIRROR})" in block
    assert "hidden-body" not in block and TEXT in block


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("mirror_exists", [False, True])
def test_long_reading_ends_with_exact_verified_citations_and_no_silent_budget_growth(
        async_mode, mirror_exists):
    from vinc_client import VincInvalidArgument
    from vinc_client.context import SOURCES_LINE
    wrong_alias = "concept:concept.agent-reviewer"
    text = "x" * 25000 + "\nExample alias: " + wrong_alias
    def handler(request):
        if request.url.path.startswith("/v1/documents/"):
            return httpx.Response(200, json={"doc_id": DOC, "executed": True,
                "verified": True, "text": text, "content_hash": hashlib.sha256(text.encode()).hexdigest()})
        return handler_for(lambda: httpx.Response(200, json={"executed": True,
            "found": mirror_exists, "node": {"id": MIRROR} if mirror_exists else None}), [])(request)
    async def run():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(VincInvalidArgument) as caught:
                await client.prefetch(ROLE, "Check.")
            size = caught.value.details["required_size"]
            return await client.prefetch(ROLE, "Check.", max_chars=size), size
    if async_mode:
        block, size = asyncio.run(run())
    else:
        with VincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(VincInvalidArgument) as caught:
                client.prefetch(ROLE, "Check.")
            size = caught.value.details["required_size"]
            block = client.prefetch(ROLE, "Check.", max_chars=size)
    footer = block.split("Citation contract for this run:\n")[-1]
    assert len(block) == size and text.replace("\n", "\\n") in block
    assert block.endswith(SOURCES_LINE)
    assert f"[{ROLE}]" in footer and wrong_alias not in footer
    assert (f"[{MIRROR}]" in footer) is mirror_exists
    assert "cannot claim Sources: none" in footer
    assert f"Required document keys with NO citable node in this space: {json.dumps([] if mirror_exists else [DOC])}" in footer
    assert "must not appear in Sources" in footer


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("status,body,code", [
    (401, {"error": {"code": "not_authenticated"}}, "not_authenticated"),
    (403, {"error": {"code": "permission_denied"}}, "permission_denied"),
    (404, {"error": {"code": "space_not_found"}}, "space_not_found"),
    (429, {"error": {"code": "quota_exceeded"}}, "quota_exceeded"),
    (503, {"error": {"code": "upstream_unavailable"}}, "upstream_unavailable"),
    (200, {"executed": False, "found": False, "node": None}, "prefetch_citation_unresolved"),
    (200, {"executed": True, "found": True, "node": {"id": "concept:other"}},
     "prefetch_citation_unresolved"),
])
def test_mirror_errors_are_not_reported_as_absence(async_mode, status, body, code):
    with pytest.raises(VincError) as caught:
        prefetch(async_mode, handler_for(lambda: httpx.Response(status, json=body), []))
    assert caught.value.code == code


@pytest.mark.parametrize("async_mode", [False, True])
def test_mirror_permissions_are_rechecked_on_each_prefetch(async_mode):
    seen, lookups = [], []
    def mirror():
        lookups.append(True)
        if len(lookups) == 1:
            return httpx.Response(200, json={"executed": True, "found": True, "node": {"id": MIRROR}})
        return httpx.Response(404, json={"error": {"code": "not_found"}})
    handler = handler_for(mirror, seen)
    cls = AsyncVincClient if async_mode else VincClient
    async def run():
        async with cls(KEY, transport=httpx.MockTransport(handler)) as client:
            return [await client.prefetch(ROLE, "Check.", space=SPACE) for _ in range(2)]
    if async_mode:
        first, second = asyncio.run(run())
    else:
        with cls(KEY, transport=httpx.MockTransport(handler)) as client:
            first, second = [client.prefetch(ROLE, "Check.", space=SPACE) for _ in range(2)]
    assert f"(cite as {MIRROR})" in first
    assert f"{DOC} (no citable id)" in second and MIRROR not in second
    assert len(lookups) == 2


def test_async_cancellation_drains_mirror_lookups():
    async def run():
        started = asyncio.Event()
        never = asyncio.Event()
        active = 0
        async def handler(request):
            nonlocal active
            if request.url.path.startswith("/v1/nodes/"):
                active += 1
                started.set()
                try:
                    await never.wait()
                finally:
                    active -= 1
            return handler_for(lambda: None, [])(request)
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            task = asyncio.create_task(client.prefetch(ROLE, "Check.", space=SPACE))
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert active == 0 and asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())
