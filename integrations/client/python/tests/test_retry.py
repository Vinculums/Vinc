"""Transient hub errors are retried, so a hub deploy reads as latency, not as an error.

Retried: 502, 503, 504, a dropped connection and a timeout, for reads and for writes that
carry an ``operation_id``. Not retried: everything else, a write without ``operation_id``,
an answer that says ``retryable: false``, and the per-turn helpers (``get_context``).
"""
from __future__ import annotations

import asyncio
from itertools import chain, repeat

import httpx
import pytest

from conftest import RESOLVED, RO_KEY, SK_KEY, Recorder, fail, ok
from vinc_client import (AsyncVincClient, VincClient, VincInvalidArgument, VincQuotaExceeded,
                         VincUnavailable)

OP = "00000000-0000-4000-8000-000000000641"
NODE = {"executed": True, "found": True, "node": {"id": "concept:a"}}


def seq(*handlers):
    """Answer with each handler in turn, then keep answering with the last one."""
    it = chain(handlers, repeat(handlers[-1]))
    return lambda request: next(it)(request)


def client(rec, key=RO_KEY, **kw) -> VincClient:
    return VincClient(key, transport=httpx.MockTransport(rec), **kw)


def test_a_read_is_retried_with_backoff_until_it_succeeds(env_clean, fake_clock):
    rec = Recorder({("GET", "/v1/nodes/concept:a"): seq(fail(503, "upstream_unavailable"),
                                                         fail(503, "upstream_unavailable"),
                                                         ok(NODE))})
    assert client(rec).node("concept:a") == NODE
    assert len(rec.requests) == 3
    assert fake_clock.slept == [0.5, 1.0]


def test_retry_after_from_the_hub_is_followed(env_clean, fake_clock):
    rec = Recorder({("POST", "/v1/brief"): seq(
        fail(503, "starting", headers={"Retry-After": "5"}), ok(RESOLVED))})
    client(rec).brief("tokens")
    assert fake_clock.slept == [5.0]


def test_a_dropped_connection_is_retried(env_clean, fake_clock):
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, json=NODE)

    assert VincClient(RO_KEY, transport=httpx.MockTransport(flaky)).node("concept:a") == NODE
    assert calls["n"] == 2


def test_a_write_without_operation_id_is_not_retried(env_clean, fake_clock):
    rec = Recorder({("POST", "/v1/fragments"): seq(fail(503, "upstream_unavailable"),
                                                   ok({"executed": True}))})
    with pytest.raises(VincUnavailable):
        client(rec, SK_KEY).ingest([{"id": "concept:x", "type": "concept"}])
    assert len(rec.requests) == 1


def test_a_write_with_operation_id_is_retried_with_the_same_body(env_clean, fake_clock):
    rec = Recorder({("POST", "/v1/fragments"): seq(fail(504, "upstream_unavailable"),
                                                   ok({"executed": True}))})
    client(rec, SK_KEY).ingest([{"id": "concept:x", "type": "concept"}], operation_id=OP)
    assert len(rec.requests) == 2
    assert rec.body(0) == rec.body(1) and rec.body(1)["operation_id"] == OP


def test_an_answer_that_says_not_retryable_is_not_retried(env_clean, fake_clock):
    rec = Recorder({("POST", "/v1/fragments"): fail(
        503, "upstream_unavailable", details={"retryable": False, "write_may_have_applied": True})})
    with pytest.raises(VincUnavailable):
        client(rec, SK_KEY).ingest([{"id": "concept:x", "type": "concept"}], operation_id=OP)
    assert len(rec.requests) == 1


@pytest.mark.parametrize("status, code, error", [
    (500, "internal_error", VincUnavailable),
    (429, "quota_exceeded", VincQuotaExceeded),
])
def test_other_errors_are_not_retried(env_clean, fake_clock, status, code, error):
    rec = Recorder({("GET", "/v1/nodes/concept:a"): fail(status, code, headers={"Retry-After": "1"})})
    with pytest.raises(error):
        client(rec).node("concept:a")
    assert len(rec.requests) == 1


def test_retries_stop_at_the_budget(env_clean, fake_clock):
    rec = Recorder({("GET", "/v1/me"): fail(503, "starting", headers={"Retry-After": "30"})})
    with pytest.raises(VincUnavailable):
        client(rec).whoami()
    # 30·60·90초에 다시 보내고, 120초가 될 다음 대기는 상한(90초)을 넘으므로 멈춘다.
    assert fake_clock.slept == [30.0, 30.0, 30.0]
    assert len(rec.requests) == 4


def test_retries_can_be_turned_off(env_clean, fake_clock):
    rec = Recorder({("GET", "/v1/me"): fail(503, "upstream_unavailable")})
    with pytest.raises(VincUnavailable):
        client(rec, retry_for=0).whoami()
    assert len(rec.requests) == 1 and fake_clock.slept == []


@pytest.mark.parametrize("bad", [-1, True, "90"])
def test_retry_for_must_be_seconds(env_clean, bad):
    with pytest.raises(VincInvalidArgument):
        client(Recorder({}), retry_for=bad)


def test_get_context_does_not_wait_for_the_hub(env_clean, fake_clock):
    rec = Recorder({("POST", "/v1/brief"): seq(fail(503, "starting", headers={"Retry-After": "5"}),
                                               ok(RESOLVED))})
    assert client(rec).get_context("why are tokens oklch") is None
    assert len(rec.requests) == 1 and fake_clock.slept == []


def test_the_async_client_retries_too(env_clean, fake_clock):
    rec = Recorder({("GET", "/v1/nodes/concept:a"): seq(fail(502, "upstream_unavailable"), ok(NODE))})

    async def run():
        async with AsyncVincClient(RO_KEY, transport=httpx.MockTransport(rec)) as c:
            return await c.node("concept:a")

    assert asyncio.run(run()) == NODE
    assert len(rec.requests) == 2 and fake_clock.slept == [0.5]


def test_without_retries_shares_the_connection_and_fails_fast(env_clean, fake_clock):
    rec = Recorder({("GET", "/v1/nodes/concept:a"): seq(fail(503, "upstream_unavailable"), ok(NODE))})
    c = client(rec)
    fast = c.without_retries()
    with pytest.raises(VincUnavailable):
        fast.node("concept:a")
    assert fast._http is c._http and c.retry_for == 90.0
    assert c.node("concept:a") == NODE           # the original still retries
    assert len(rec.requests) == 2
