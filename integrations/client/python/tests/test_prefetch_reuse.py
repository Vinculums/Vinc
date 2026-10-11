"""Synthetic contract checks for the shared required-context budget."""
import asyncio
import hashlib
import json

import httpx
import pytest

from vinc_client import AsyncVincClient, VincAuthError, VincClient, VincInvalidArgument
from vinc_client.prefetch import render_prefetch


KEY = "vinc_ro_" + "a" * 32
SPACE = "00000000-0000-0000-0000-000000000001"
ROLE = {"found": True, "id": "concept:synthetic-role", "domain": "synthetic/topic",
        "props": {"reads": ["synthetic.one", "synthetic.two"]}}


def document(doc_id, revision):
    text = f"Synthetic revision {revision}: a full reading with ``` and 끝."
    return {"executed": True, "doc_id": doc_id, "verified": True, "text": text,
            "content_hash": hashlib.sha256(text.encode()).hexdigest(),
            "updated_at": f"2026-01-0{revision}T00:00:00Z"}


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("concurrency", [1, 8])
def test_required_budget_and_renderer_agree_before_optional_failure(async_mode, concurrency):
    seen = []
    specs = [(key, {**document(key, 1), "cite_id": "concept:doc." + key
                   if key == "synthetic.one" else None}) for key in ROLE["props"]["reads"]]
    required = render_prefetch(ROLE, "Synthetic task", specs, {}, {}, max_chars=24000)

    def handler(request):
        seen.append(request.url.path)
        assert request.url.params["space"] == SPACE
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json=ROLE)
        if request.url.path.startswith("/v1/documents/"):
            return httpx.Response(200, json=document(request.url.path.rsplit("/", 1)[-1], 1))
        if request.url.path.startswith("/v1/nodes/"):
            found = request.url.path.endswith("synthetic.one")
            return httpx.Response(200, json={"executed": True, "found": found,
                "node": {"id": "concept:doc.synthetic.one"} if found else None})
        return httpx.Response(503, json={"error": {"code": "upstream_unavailable"}})

    async def run_async():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler),
                                   prefetch_concurrency=concurrency) as client:
            await client.prefetch(ROLE["id"], "Synthetic task", space=SPACE,
                                  max_chars=len(required) - 1)

    with pytest.raises(VincInvalidArgument) as caught:
        if async_mode:
            asyncio.run(run_async())
        else:
            with VincClient(KEY, transport=httpx.MockTransport(handler),
                            prefetch_concurrency=concurrency) as client:
                client.prefetch(ROLE["id"], "Synthetic task", space=SPACE,
                                max_chars=len(required) - 1)
    assert caught.value.code == "prefetch_budget_exceeded"
    assert caught.value.details == {"required_size": len(required),
        "max_chars": len(required) - 1, "recommended_max_chars": len(required) + 6002}
    assert sum(path.startswith("/v1/documents/") for path in seen) == 2
    if concurrency == 1:
        assert "/v1/brief" not in seen and "/v1/search" not in seen
    assert "cite as concept:doc.synthetic.one" in required
    assert "synthetic.two (no citable id)" in required
    assert "concept:doc.synthetic.two" not in required
    for _, doc in specs:
        assert json.dumps(doc["text"], ensure_ascii=False) in required
        assert doc["content_hash"] in required and doc["updated_at"] in required


@pytest.mark.parametrize("async_mode", [False, True])
def test_prefetch_rereads_changed_documents_and_refuses_revoked_access(async_mode):
    revision = [1]
    denied = [False]
    seen = []

    def handler(request):
        seen.append(request.url.path)
        assert request.url.params["space"] == SPACE
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json=ROLE)
        if request.url.path.startswith("/v1/documents/"):
            if denied[0]:
                return httpx.Response(403, json={"error": {"code": "permission_denied"}})
            return httpx.Response(200, json=document(request.url.path.rsplit("/", 1)[-1], revision[0]))
        if request.url.path.startswith("/v1/nodes/"):
            return httpx.Response(200, json={"executed": True, "found": False, "node": None})
        return httpx.Response(200, json={"status": "none"})

    async def run():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler),
                                   prefetch_concurrency=1) as client:
            first = await client.prefetch(ROLE["id"], "Synthetic task", space=SPACE)
            revision[0] = 2
            second = await client.prefetch(ROLE["id"], "Synthetic task", space=SPACE)
            denied[0] = True
            with pytest.raises(VincAuthError, match="permission_denied"):
                await client.prefetch(ROLE["id"], "Synthetic task", space=SPACE)
            return first, second

    if async_mode:
        first, second = asyncio.run(run())
    else:
        with VincClient(KEY, transport=httpx.MockTransport(handler), prefetch_concurrency=1) as client:
            first = client.prefetch(ROLE["id"], "Synthetic task", space=SPACE)
            revision[0] = 2
            second = client.prefetch(ROLE["id"], "Synthetic task", space=SPACE)
            denied[0] = True
            with pytest.raises(VincAuthError, match="permission_denied"):
                client.prefetch(ROLE["id"], "Synthetic task", space=SPACE)
    assert "Synthetic revision 1" in first
    assert "Synthetic revision 2" in second and "Synthetic revision 1" not in second
    assert document("synthetic.one", 2)["content_hash"] in second
    assert seen.count("/v1/documents/synthetic.one") == 3
    assert seen.count("/v1/documents/synthetic.two") == 2
    assert seen.count("/v1/brief") == seen.count("/v1/search") == 2
