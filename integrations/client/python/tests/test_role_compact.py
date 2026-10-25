"""읽기 예산을 넘는 역할: 자르지 않고, 기본값은 근거 문장을 뺀 모양으로 한 번 더 읽는다."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from vinc_client import AsyncVincClient, VincClient, VincInvalidArgument

from conftest import RO_KEY

ROLE = "concept:agent-qa"
PIN = "c" * 64
COMPACT = {"kind": "role", "executed": True, "found": True, "id": ROLE, "title": "QA",
           "domain": "vinc/agent/roles", "props": {"mission": "Test.", "reads": []},
           "works_with": {"hands_to": [{"from": ROLE, "to": "concept:agent-deploy",
                                        "type": "PREREQUISITE_OF", "confidence": None}]},
           "compact": True, "rev": 49, "hash": PIN}


def _too_large(compact: bool) -> httpx.Response:
    return httpx.Response(400, json={"error": {
        "code": "role_contract_too_large", "message": "The complete contract exceeds the read limit.",
        "request_id": "req_test",
        "details": {"id": ROLE, "bytes": 13021, "limit": 10000, "compact": compact}}})


def _hub(seen, *, compact_fits=True):
    def handle(request):
        seen.append(request)
        if request.url.params.get("compact") == "true":
            return httpx.Response(200, json=COMPACT) if compact_fits else _too_large(True)
        return _too_large(False)
    return handle


def _role(async_mode, handler, **kwargs):
    if not async_mode:
        with VincClient(RO_KEY, transport=httpx.MockTransport(handler)) as client:
            return client.role(ROLE, **kwargs)

    async def go():
        async with AsyncVincClient(RO_KEY, transport=httpx.MockTransport(handler)) as client:
            return await client.role(ROLE, **kwargs)
    return asyncio.run(go())


@pytest.mark.parametrize("async_mode", [False, True])
def test_an_over_limit_role_is_read_again_in_compact_form(async_mode):
    seen = []
    got = _role(async_mode, _hub(seen))
    assert got == COMPACT
    assert [r.url.params.get("compact") for r in seen] == [None, "true"]


@pytest.mark.parametrize("async_mode", [False, True])
def test_the_pin_is_kept_on_the_second_read(async_mode):
    seen = []
    assert _role(async_mode, _hub(seen), pin=PIN)["hash"] == PIN
    assert [r.url.params.get("expected_hash") for r in seen] == [PIN, PIN]


@pytest.mark.parametrize("async_mode", [False, True])
def test_compact_true_asks_once_and_false_raises(async_mode):
    seen = []
    assert _role(async_mode, _hub(seen), compact=True)["compact"] is True
    assert [r.url.params.get("compact") for r in seen] == ["true"]
    seen.clear()
    with pytest.raises(VincInvalidArgument) as caught:
        _role(async_mode, _hub(seen), compact=False)
    assert caught.value.code == "role_contract_too_large" and caught.value.status == 400
    assert caught.value.details["limit"] == 10000
    assert len(seen) == 1


@pytest.mark.parametrize("async_mode", [False, True])
def test_a_role_too_large_even_in_compact_form_raises_after_one_retry(async_mode):
    seen = []
    with pytest.raises(VincInvalidArgument) as caught:
        _role(async_mode, _hub(seen, compact_fits=False))
    assert caught.value.details["compact"] is True
    assert len(seen) == 2
