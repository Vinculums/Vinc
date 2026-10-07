"""선택 읽기(brief·search)를 검증 전에 먼저 띄우는 계약(0.1.4).

근거: [[vinc.plan.vin397-prefetch-half-latency-2026-10-07]]
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time

import httpx
import pytest

import vinc_client.client as client_module
from vinc_client import AsyncVincClient, VincClient, VincError, VincInvalidArgument


KEY = "vinc_ro_" + "a" * 32
ROLE_ID = "concept:agent-test"


def role_body(count, mission="Review."):
    return {"found": True, "id": ROLE_ID, "domain": "agent/engineering",
            "props": {"mission": mission, "reads": [f"d{i:02}" for i in range(count)]}}


def document(doc_id, size=40):
    text = f"Full required text for {doc_id}." + "x" * size
    return {"doc_id": doc_id, "executed": True, "verified": True, "text": text,
            "content_hash": hashlib.sha256(text.encode()).hexdigest()}


BRIEF = {"status": "resolved", "node": {"id": "decision:early", "cite_id": "decision:early",
                                        "title": "Early brief"}, "one_liner": "Brief evidence."}
SEARCH = {"node_hits": [{"id": "record:early", "cite_id": "record:early", "title": "Early search"}]}


def label(request):
    path = request.url.path
    if path.startswith("/v1/roles/"):
        return "role"
    if path.startswith("/v1/documents/"):
        return path.rsplit("/", 1)[-1]
    return path.rsplit("/", 1)[-1]


def answer(request, count=2, mission="Review."):
    name = label(request)
    if name == "role":
        return httpx.Response(200, json=role_body(count, mission))
    if name == "brief":
        return httpx.Response(200, json=BRIEF)
    if name == "search":
        assert json.loads(request.content)["query"] == "Review.\nRole topic: agent/engineering"
        return httpx.Response(200, json=SEARCH)
    return httpx.Response(200, json=document(name))


def run(async_mode, transport_handler, **kwargs):
    client = (AsyncVincClient if async_mode else VincClient)(
        KEY, transport=httpx.MockTransport(transport_handler), **kwargs)
    value = client.prefetch(ROLE_ID, "Review.")
    return asyncio.run(value) if async_mode else value


# 1. 순서 ---------------------------------------------------------------------

def test_async_brief_in_flight_before_role_response_and_search_after_it():
    async def go():
        brief_seen = asyncio.Event()
        role_done = False
        order = []

        async def handler(request):
            nonlocal role_done
            name = label(request)
            order.append(name)
            if name == "role":
                # brief 가 역할 응답 전에 이미 나가 있어야 한다.
                await asyncio.wait_for(brief_seen.wait(), timeout=3)
                role_done = True
            elif name == "brief":
                brief_seen.set()
            elif name == "search":
                assert role_done
            return answer(request, count=3)

        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            await client.prefetch(ROLE_ID, "Review.")
        assert sorted(order) == ["brief", "d00", "d01", "d02", "role", "search"]
    asyncio.run(go())


def test_sync_brief_in_flight_before_role_response_and_search_after_it():
    brief_seen = threading.Event()
    role_done = threading.Event()
    order = []
    lock = threading.Lock()

    def handler(request):
        name = label(request)
        with lock:
            order.append(name)
        if name == "role":
            # brief 가 역할 응답 전에 이미 나가 있어야 한다.
            assert brief_seen.wait(timeout=3)
            role_done.set()
        elif name == "brief":
            brief_seen.set()
        elif name == "search":
            assert role_done.is_set()
        return answer(request, count=3)

    with VincClient(KEY, transport=httpx.MockTransport(handler)) as client:
        client.prefetch(ROLE_ID, "Review.")
    assert sorted(order) == ["brief", "d00", "d01", "d02", "role", "search"]


@pytest.mark.parametrize("async_mode", [False, True])
def test_concurrency_one_keeps_the_sequential_order(async_mode):
    order = []

    def handler(request):
        order.append(label(request))
        return answer(request, count=3)

    run(async_mode, handler, prefetch_concurrency=1)
    assert order == ["role", "d00", "d01", "d02", "brief", "search"]


# 2. 동시 요청 상한 ------------------------------------------------------------

@pytest.mark.parametrize("async_mode", [False, True])
def test_in_flight_requests_never_exceed_the_setting(async_mode):
    lock = threading.Lock()
    active = peak = 0

    def enter():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)

    def leave():
        nonlocal active
        with lock:
            active -= 1

    def handler(request):
        enter()
        try:
            time.sleep(0.03)
            return answer(request, count=6)
        finally:
            leave()

    async def async_handler(request):
        enter()
        try:
            await asyncio.sleep(0.03)
            return answer(request, count=6)
        finally:
            leave()

    run(async_mode, async_handler if async_mode else handler, prefetch_concurrency=3)
    assert peak == 3 and active == 0


# 3. 필수 실패 시 선택 결과 폐기 ------------------------------------------------

@pytest.mark.parametrize("mode", ["answered", "optional_fails", "blocked"])
@pytest.mark.parametrize("async_mode", [False, True])
def test_required_failure_discards_optional_results(async_mode, mode, monkeypatch):
    """answered·optional_fails: 선택 읽기가 끝난 뒤 두 번째 문서 404.
    blocked: 선택 읽기가 끝내 응답하지 않는 동안 두 번째 문서가 바로 404."""
    rendered = []
    monkeypatch.setattr(client_module, "render_prefetch",
                        lambda *a, **k: rendered.append(a) or "rendered")
    started, answered = [], []

    def optional_response(request):
        answered.append(label(request))
        if mode == "optional_fails":
            return httpx.Response(503, json={"error": {"code": "upstream_unavailable"}})
        return answer(request)

    def missing():
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    if async_mode:
        async def go():
            both = asyncio.Event()
            never = asyncio.Event()
            notices = []
            asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: notices.append(ctx))

            async def handler(request):
                name = label(request)
                if name in ("brief", "search"):
                    started.append(name)
                    if mode == "blocked":
                        await asyncio.wait_for(never.wait(), timeout=3)
                    response = optional_response(request)
                    if len(answered) == 2:
                        both.set()
                    return response
                if name == "d01":
                    if mode != "blocked":
                        await asyncio.wait_for(both.wait(), timeout=3)
                    return missing()
                return answer(request, count=4)

            async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler)) as client:
                with pytest.raises(VincInvalidArgument) as caught:
                    await asyncio.wait_for(client.prefetch(ROLE_ID, "Review."), timeout=2)
            await asyncio.sleep(0)
            assert asyncio.all_tasks() == {asyncio.current_task()}
            assert not notices
            return caught.value
        error = asyncio.run(go())
    else:
        both = threading.Event()
        never = threading.Event()
        lock = threading.Lock()

        def handler(request):
            name = label(request)
            if name in ("brief", "search"):
                with lock:
                    started.append(name)
                if mode == "blocked":
                    # 동기판은 실행 중인 요청을 풀 종료에서 기다린다(D2 가 받아들인 동작).
                    never.wait(timeout=0.5)
                with lock:
                    response = optional_response(request)
                    if len(answered) == 2:
                        both.set()
                return response
            if name == "d01":
                if mode != "blocked":
                    assert both.wait(timeout=3)
                return missing()
            return answer(request, count=4)

        with VincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(VincInvalidArgument) as caught:
                client.prefetch(ROLE_ID, "Review.")
        assert not any(t.name.startswith("vinc-prefetch") for t in threading.enumerate())
        error = caught.value
    assert error.code == "prefetch_spec_unresolved"
    if mode == "blocked":
        # 어느 선택 읽기가 실패 전에 시작했는지는 스케줄에 달렸다. 결과는 쓰이지 않는다.
        assert set(started) <= {"brief", "search"}
    else:
        assert sorted(answered) == ["brief", "search"]
    assert rendered == []


def test_async_outer_cancel_while_optional_reads_in_flight():
    async def go():
        in_flight = asyncio.Event()
        never = asyncio.Event()
        notices = []
        entered = set()
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: notices.append(ctx))

        async def handler(request):
            name = label(request)
            if name == "role":
                return answer(request, count=2)
            entered.add(name)
            if {"brief", "search"} <= entered:
                in_flight.set()
            await never.wait()

        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            pending = asyncio.create_task(client.prefetch(ROLE_ID, "Review."))
            await asyncio.wait_for(in_flight.wait(), timeout=2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == {asyncio.current_task()}
        assert not notices
    asyncio.run(go())


# 4. 예산 초과 ----------------------------------------------------------------

@pytest.mark.parametrize("async_mode", [False, True])
def test_budget_error_after_optional_reads_completed_is_unchanged(async_mode):
    mission = "m" * 30000

    def plain(request):
        return answer(request, count=2, mission=mission)

    with pytest.raises(VincInvalidArgument) as sequential:
        run(async_mode, plain, prefetch_concurrency=1)

    answered = []
    lock = threading.Lock()
    both = threading.Event()

    def handler(request):
        name = label(request)
        if name in ("brief", "search"):
            with lock:
                answered.append(name)
                if len(answered) == 2:
                    both.set()
        elif name != "role":
            assert both.wait(timeout=3)
        return plain(request)

    async def async_handler(request):
        name = label(request)
        if name in ("brief", "search"):
            answered.append(name)
        elif name != "role":
            for _ in range(300):
                if len(answered) == 2:
                    break
                await asyncio.sleep(0.01)
            assert len(answered) == 2
        return plain(request)

    with pytest.raises(VincInvalidArgument) as concurrent:
        run(async_mode, async_handler if async_mode else handler)
    assert sorted(answered) == ["brief", "search"]
    assert concurrent.value.code == sequential.value.code == "prefetch_budget_exceeded"
    assert concurrent.value.details == sequential.value.details
    assert concurrent.value.message == sequential.value.message


# 5. 렌더 결과 동일 ------------------------------------------------------------

def test_rendered_output_is_identical_between_sequential_and_concurrent():
    def handler(request):
        return answer(request, count=5)

    async def async_handler(request):
        return answer(request, count=5)

    blocks = {(False, 1): run(False, handler, prefetch_concurrency=1),
              (False, 8): run(False, handler, prefetch_concurrency=8),
              (True, 1): run(True, async_handler, prefetch_concurrency=1),
              (True, 8): run(True, async_handler, prefetch_concurrency=8)}
    assert len(set(blocks.values())) == 1
    block = blocks[(False, 1)]
    assert "decision:early" in block and "record:early" in block
