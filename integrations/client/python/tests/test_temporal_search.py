import asyncio
import json

import httpx
import pytest

from conftest import RO_KEY, TEAM, Recorder, ok
from vinc_client import AsyncVincClient, VincClient
from vinc_client.context import evidence_metadata, format_search


@pytest.mark.parametrize("async_mode", [False, True])
def test_optional_temporal_params_and_space_preserve_response(env_clean, async_mode):
    state = {"created_at": None, "updated_at": "2026-10-13T00:00:00Z", "valid_from": None,
             "valid_until": None, "validity": "unknown", "superseded_by": "record:new"}
    result = {"node_hits": [{"id": "record:synthetic", "title": "Marker", "temporal": state}],
              "rows": [], "mode": "historical", "vector_note": "synthetic degraded"}
    rec = Recorder({("POST", "/v1/search"): ok(result)})
    params = dict(mode="historical", historical=True, as_of="2026", known_at="2026-10", space=TEAM)
    if async_mode:
        async def run():
            async with AsyncVincClient(RO_KEY, transport=httpx.MockTransport(rec)) as client:
                return await client.search("Marker", **params)
        got = asyncio.run(run())
    else:
        with VincClient(RO_KEY, transport=httpx.MockTransport(rec)) as client:
            got = client.search("Marker", **params)
    assert got == result
    assert rec.requests[0].url.params["space"] == TEAM
    assert rec.body() == {"query": "Marker", "mode": "historical", "historical": True,
                          "as_of": "2026", "known_at": "2026-10"}
    assert evidence_metadata(got["node_hits"][0])["temporal"] == state
    rendered = format_search(got)
    assert '"created_at": null' in rendered and '"validity": "unknown"' in rendered
    assert "synthetic degraded" in rendered
