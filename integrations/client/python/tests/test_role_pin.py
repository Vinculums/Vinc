"""역할 고정: 고정한 해시와 다른 계약은 받지 않고, 프리페치는 문서를 읽기 전에 멈춘다."""
from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest
from vinc_client import AsyncVincClient, VincClient, VincInvalidArgument, VincRoleChanged

from conftest import RO_KEY

ROLE = "concept:agent-reviewer"
PIN = "a" * 64
NEW = "b" * 64
CONTRACT = {"kind": "role", "executed": True, "found": True, "id": ROLE, "title": "Reviewer",
            "domain": "agent", "props": {"reads": ["vinc.spec.fixture"], "mission": "Review."},
            "works_with": {}, "rev": 3, "hash": PIN}


def _handler(seen, *, current=PIN, served=None):
    """허브 흉내. 고정이 지금 해시와 다르면 409 role_changed 와 지금 rev·hash 를 준다."""
    def handle(request):
        seen.append(request)
        path = request.url.path
        if path.startswith("/v1/roles/"):
            expected = request.url.params.get("expected_hash")
            if expected is not None and expected != current:
                return httpx.Response(409, json={"error": {
                    "code": "role_changed", "message": "The role changed since it was pinned.",
                    "request_id": "req_test", "details": {"id": ROLE, "rev": 4, "hash": current}}})
            return httpx.Response(200, json=served or {**CONTRACT, "hash": current})
        if path.startswith("/v1/documents/"):
            text = "Keep all rules."
            return httpx.Response(200, json={"executed": True, "doc_id": "vinc.spec.fixture",
                "verified": True, "text": text, "content_hash": hashlib.sha256(text.encode()).hexdigest()})
        if path.startswith("/v1/nodes/"):
            return httpx.Response(200, json={"executed": True, "found": True,
                                             "node": {"id": "concept:doc.vinc.spec.fixture"}})
        if path.endswith("brief"):
            return httpx.Response(200, json={"status": "none"})
        return httpx.Response(200, json={"node_hits": [], "rows": []})
    return handle


def _run(async_mode, call):
    if not async_mode:
        with VincClient(RO_KEY, transport=httpx.MockTransport(call.handler)) as client:
            return call(client)

    async def go():
        async with AsyncVincClient(RO_KEY, transport=httpx.MockTransport(call.handler)) as client:
            return await call(client)
    return asyncio.run(go())


class _Call:
    def __init__(self, handler, fn):
        self.handler, self.fn = handler, fn

    def __call__(self, client):
        return self.fn(client)


@pytest.mark.parametrize("async_mode", [False, True])
def test_role_sends_the_pin_and_returns_the_matching_contract(async_mode):
    seen = []
    got = _run(async_mode, _Call(_handler(seen), lambda c: c.role(ROLE, pin=PIN)))
    assert got["hash"] == PIN and got["rev"] == 3
    assert seen[0].url.params["expected_hash"] == PIN


@pytest.mark.parametrize("async_mode", [False, True])
def test_role_without_a_pin_sends_no_pin(async_mode):
    seen = []
    _run(async_mode, _Call(_handler(seen), lambda c: c.role(ROLE)))
    assert "expected_hash" not in seen[0].url.params


@pytest.mark.parametrize("async_mode", [False, True])
def test_a_changed_role_raises_with_the_current_pin(async_mode):
    seen = []
    with pytest.raises(VincRoleChanged) as caught:
        _run(async_mode, _Call(_handler(seen, current=NEW), lambda c: c.role(ROLE, pin=PIN)))
    assert caught.value.code == "role_changed" and caught.value.status == 409
    assert caught.value.details == {"id": ROLE, "rev": 4, "hash": NEW}
    # 기존 코드가 잡던 클래스로도 잡힌다.
    assert isinstance(caught.value, VincInvalidArgument)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("served", [{k: v for k, v in CONTRACT.items() if k != "hash"},
                                    {**CONTRACT, "hash": NEW}])
def test_a_hub_that_does_not_confirm_the_pin_is_refused(async_mode, served):
    # 고정 인자를 모르는 허브가 200 으로 답해도 해시가 고정과 같지 않으면 받지 않는다.
    seen = []
    with pytest.raises(VincRoleChanged):
        _run(async_mode, _Call(_handler(seen, served=served), lambda c: c.role(ROLE, pin=PIN)))


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("pin", ["A" * 64, "a" * 63, "", 7])
def test_a_malformed_pin_is_refused_before_any_request(async_mode, pin):
    seen = []
    for fn in (lambda c: c.role(ROLE, pin=pin),
               lambda c: c.prefetch_with_contract(ROLE, "Review.", pin=pin)):
        with pytest.raises(VincInvalidArgument, match="pin"):
            _run(async_mode, _Call(_handler(seen), fn))
    assert seen == []


@pytest.mark.parametrize("async_mode", [False, True])
def test_prefetch_with_a_stale_pin_stops_before_any_document(async_mode):
    seen = []
    with pytest.raises(VincRoleChanged):
        _run(async_mode, _Call(_handler(seen, current=NEW),
                               lambda c: c.prefetch(ROLE, "Review.", pin=PIN)))
    assert not any(r.url.path.startswith(("/v1/documents/", "/v1/nodes/")) for r in seen)
    assert [r.url.params.get("expected_hash") for r in seen
            if r.url.path.startswith("/v1/roles/")] == [PIN]


@pytest.mark.parametrize("async_mode", [False, True])
def test_prefetch_with_the_current_pin_builds_the_same_block_as_without(async_mode):
    pinned, plain = [], []
    with_pin = _run(async_mode, _Call(_handler(pinned),
                                      lambda c: c.prefetch_with_contract(ROLE, "Review.", pin=PIN)))
    without = _run(async_mode, _Call(_handler(plain),
                                     lambda c: c.prefetch_with_contract(ROLE, "Review.")))
    assert with_pin.block == without.block and with_pin.contract["hash"] == PIN
    assert len(pinned) == len(plain)
    assert json.loads(json.dumps(with_pin.contract)) == {**CONTRACT}
