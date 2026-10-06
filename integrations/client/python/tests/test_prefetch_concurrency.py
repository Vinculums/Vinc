from __future__ import annotations

import asyncio
import hashlib
import threading

import httpx
import pytest

from vinc_client import AsyncVincClient, VincClient, VincInvalidArgument


KEY = "vinc_ro_" + "a" * 32
ROLE_ID = "concept:agent-test"


def role(count):
    return httpx.Response(200, json={"found": True, "id": ROLE_ID,
        "props": {"reads": [f"d{i:02}" for i in range(count)]}})


def document(doc_id):
    text = f"Full required text for {doc_id}."
    return {"doc_id": doc_id, "executed": True, "verified": True, "text": text,
            "content_hash": hashlib.sha256(text.encode()).hexdigest()}


def test_sync_bounded_document_batches_and_task_pair_overlap():
    lock = threading.Lock()
    barriers = [threading.Barrier(8), threading.Barrier(8), threading.Barrier(2)]
    active = peak = completed = 0
    owner_thread = threading.get_ident()

    def handler(request):
        nonlocal active, peak, completed
        if request.url.path.startswith("/v1/roles/"):
            assert threading.get_ident() == owner_thread
            return role(17)
        is_doc = request.url.path.startswith("/v1/documents/")
        with lock:
            active += 1
            peak = max(peak, active)
            if not is_doc:
                assert completed == 17
        try:
            if is_doc:
                doc_id = request.url.path.rsplit("/", 1)[-1]
                number = int(doc_id[1:])
                if number < 16:
                    barriers[number // 8].wait(timeout=3)
                return httpx.Response(200, json=document(doc_id))
            barriers[2].wait(timeout=3)
            return httpx.Response(200, json={"status": "none"})
        finally:
            with lock:
                active -= 1
                completed += int(is_doc)

    with VincClient(KEY, transport=httpx.MockTransport(handler)) as client:
        block = client.prefetch(ROLE_ID, "Review.")
    assert active == 0 and peak == 8 and completed == 17
    positions = [block.index(f"Required reading (full canonical document): d{i:02}")
                 for i in range(17)]
    assert positions == sorted(positions)


def test_async_bounded_document_batches_and_task_pair_overlap():
    async def go():
        events = [asyncio.Event(), asyncio.Event(), asyncio.Event()]
        entered = [0, 0, 0]
        active = peak = completed = 0

        async def handler(request):
            nonlocal active, peak, completed
            if request.url.path.startswith("/v1/roles/"):
                return role(17)
            is_doc = request.url.path.startswith("/v1/documents/")
            active += 1
            peak = max(peak, active)
            try:
                if is_doc:
                    doc_id = request.url.path.rsplit("/", 1)[-1]
                    number = int(doc_id[1:])
                    group, size = number // 8, 8
                else:
                    assert completed == 17
                    group, size = 2, 2
                if not is_doc or number < 16:
                    entered[group] += 1
                    if entered[group] == size:
                        events[group].set()
                    await events[group].wait()
                return httpx.Response(200, json=document(doc_id) if is_doc else {"status": "none"})
            finally:
                active -= 1
                completed += int(is_doc)

        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            block = await asyncio.wait_for(client.prefetch(ROLE_ID, "Review."), timeout=5)
        assert active == 0 and peak == 8 and completed == 17
        positions = [block.index(f"Required reading (full canonical document): d{i:02}")
                     for i in range(17)]
        assert positions == sorted(positions)
    asyncio.run(go())


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("defect,code", [("missing", "prefetch_spec_unresolved"),
    ("unverified", "prefetch_spec_unresolved"), ("hash", "prefetch_spec_unresolved"),
    ("partial", "prefetch_spec_unresolved"), ("wrong_id", "prefetch_spec_unresolved"),
    ("forbidden", "permission_denied"), ("unavailable", "upstream_unavailable")])
def test_failed_batch_stops_before_later_batches_or_task_queries(async_mode, defect, code):
    from vinc_client import VincError
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.startswith("/v1/roles/"):
            return role(17)
        doc_id = request.url.path.rsplit("/", 1)[-1]
        if doc_id != "d00":
            return httpx.Response(200, json=document(doc_id))
        if defect in ("missing", "forbidden", "unavailable"):
            status = {"missing": 404, "forbidden": 403, "unavailable": 503}[defect]
            return httpx.Response(status, json={"error": {"code": code}})
        data = document(doc_id)
        if defect == "unverified":
            data["verified"] = False
        elif defect == "hash":
            data["text"] = "Altered."
        elif defect == "partial":
            data["partial"] = True
        elif defect == "wrong_id":
            data["doc_id"] = "other"
        return httpx.Response(200, json=data)

    client = (AsyncVincClient if async_mode else VincClient)(KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(VincError) as caught:
        value = client.prefetch(ROLE_ID, "Review.")
        if async_mode:
            asyncio.run(value)
    assert caught.value.code == code
    assert 2 <= len(seen) <= 9
    assert all(p.startswith(("/v1/roles/", "/v1/documents/")) for p in seen)
    assert not any(p.endswith(f"d{i:02}") for p in seen for i in range(8, 17))


def test_async_cancellation_drains_inflight_reads_and_observes_other_errors():
    async def go():
        started = asyncio.Event()
        release = asyncio.Event()
        count = active = 0
        notices = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: notices.append(context))

        async def handler(request):
            nonlocal count, active
            if request.url.path.startswith("/v1/roles/"):
                return role(9)
            active += 1
            count += 1
            if count == 8:
                started.set()
            try:
                if request.url.path.endswith("d01"):
                    return httpx.Response(503, json={"error": {"code": "upstream_unavailable"}})
                await release.wait()
                return httpx.Response(200, json=document(request.url.path.rsplit("/", 1)[-1]))
            finally:
                active -= 1

        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            pending = asyncio.create_task(client.prefetch(ROLE_ID, "Review."))
            await asyncio.wait_for(started.wait(), timeout=2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert active == 0 and count == 8
            assert await client.role(ROLE_ID)
            await asyncio.sleep(0)
            assert asyncio.all_tasks() == {asyncio.current_task()}
            assert not notices
    asyncio.run(go())


@pytest.mark.parametrize("async_mode", [False, True])
def test_optional_error_preserves_brief_precedence_and_drains_search(async_mode):
    from vinc_client import VincAuthError
    barrier = threading.Barrier(2)
    seen = []
    active = 0
    lock = threading.Lock()

    def response(request):
        is_brief = request.url.path.endswith("brief")
        return httpx.Response(403 if is_brief else 503, json={"error": {
            "code": "permission_denied" if is_brief else "upstream_unavailable"}})

    def handler(request):
        nonlocal active
        if request.url.path.startswith("/v1/roles/"):
            return role(0)
        with lock:
            active += 1
            seen.append(request.url.path)
        try:
            barrier.wait(timeout=3)
            return response(request)
        finally:
            with lock:
                active -= 1

    async def go():
        nonlocal active
        ready = asyncio.Event()
        async def async_handler(request):
            nonlocal active
            if request.url.path.startswith("/v1/roles/"):
                return role(0)
            active += 1
            seen.append(request.url.path)
            if len(seen) == 2:
                ready.set()
            try:
                await ready.wait()
                return response(request)
            finally:
                active -= 1
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(async_handler)) as client:
            with pytest.raises(VincAuthError, match="permission_denied"):
                await asyncio.wait_for(client.prefetch(ROLE_ID, "Review."), timeout=5)
        assert asyncio.all_tasks() == {asyncio.current_task()}

    if async_mode:
        asyncio.run(go())
    else:
        with VincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(VincAuthError, match="permission_denied"):
                client.prefetch(ROLE_ID, "Review.")
        assert not any(t.name.startswith("vinc-prefetch") for t in threading.enumerate())
    assert active == 0
    assert sorted(seen) == ["/v1/brief", "/v1/search"]


def test_async_cancellation_drains_optional_task_pair():
    async def go():
        started = asyncio.Event()
        never = asyncio.Event()
        active = count = 0
        async def handler(request):
            nonlocal active, count
            if request.url.path.startswith("/v1/roles/"):
                return role(0)
            active += 1
            count += 1
            if count == 2:
                started.set()
            try:
                await never.wait()
            finally:
                active -= 1
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            pending = asyncio.create_task(client.prefetch(ROLE_ID, "Review."))
            await asyncio.wait_for(started.wait(), timeout=2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert active == 0 and count == 2
            assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(go())


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("value", [0, 9, True, 1.5, "8", None])
def test_concurrency_setting_is_bounded_before_network(async_mode, value):
    with pytest.raises(VincInvalidArgument, match="prefetch_concurrency"):
        (AsyncVincClient if async_mode else VincClient)(KEY, prefetch_concurrency=value)


@pytest.mark.parametrize("async_mode", [False, True])
def test_sequential_setting_preserves_caller_thread_and_brief_failure_boundary(async_mode):
    from vinc_client import VincAuthError
    seen = []
    owner_thread = threading.get_ident()
    def handler(request):
        assert threading.get_ident() == owner_thread
        seen.append(request.url.path)
        if request.url.path.startswith("/v1/roles/"):
            return role(0)
        assert request.url.path.endswith("brief")
        return httpx.Response(403, json={"error": {"code": "permission_denied"}})
    client = (AsyncVincClient if async_mode else VincClient)(
        KEY, transport=httpx.MockTransport(handler), prefetch_concurrency=1)
    with pytest.raises(VincAuthError):
        value = client.prefetch(ROLE_ID, "Review.")
        if async_mode:
            asyncio.run(value)
    assert len(seen) == 2
