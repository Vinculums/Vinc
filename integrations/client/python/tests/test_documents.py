import asyncio

import httpx
import pytest
from vinc_client import AsyncVincClient, VincAuthError, VincClient, VincNotFound

from conftest import RO_KEY, TEAM


@pytest.mark.parametrize("async_mode", [False, True])
def test_document_is_an_exact_read_with_space_and_integrity_metadata(async_mode):
    seen = []
    payload = {"doc_id": "vinc.spec/fixture", "text": "All rules.", "verified": True,
               "content_hash": "returned-verbatim", "executed": True}
    def handler(request):
        seen.append(request)
        assert request.method == "GET"
        assert "/v1/documents/vinc.spec%2Ffixture" in str(request.url)
        assert request.url.params["space"] == TEAM
        return httpx.Response(200, json=payload)
    client = (AsyncVincClient if async_mode else VincClient)(RO_KEY, transport=httpx.MockTransport(handler))
    value = client.document("vinc.spec/fixture", space=TEAM)
    assert (asyncio.run(value) if async_mode else value) == payload
    assert len(seen) == 1


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("status,error", [(404, VincNotFound), (403, VincAuthError), (401, VincAuthError)])
def test_document_missing_or_denied_is_not_an_empty_success(async_mode, status, error):
    client = (AsyncVincClient if async_mode else VincClient)(RO_KEY, transport=httpx.MockTransport(
        lambda r: httpx.Response(status, json={"error": {"code": "not_found"}})))
    with pytest.raises(error):
        value = client.document("vinc.spec.fixture")
        if async_mode:
            asyncio.run(value)
