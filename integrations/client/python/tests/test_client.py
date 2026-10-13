from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from conftest import RESOLVED, RO_KEY, SK_KEY, TEAM, Recorder, fail, ok
from vinc_client import (AsyncVincClient, VincAuthError, VincClient, VincInvalidArgument,
                         VincNotFound, VincPartialWrite, VincQuotaExceeded, VincUnavailable)


def client(recorder: Recorder, key: str = RO_KEY, **kwargs) -> VincClient:
    return VincClient(key, transport=httpx.MockTransport(recorder), **kwargs)


def test_guarded_ingest_and_receipt_keep_the_same_space(env_clean):
    op = "00000000-0000-4000-8000-000000000488"
    rec = Recorder({("POST", "/v1/fragments"): ok({"executed": True}),
                    ("GET", "/v1/operations/" + op): ok({"state": "committed"})})
    c = client(rec, SK_KEY, space=TEAM)
    node = {"id": "concept:a", "type": "concept", "expected_rev": 4, "props_patch": {"removed": None}}
    c.ingest([node], create_only=False, operation_id=op, review_claims=True)
    assert rec.body() == {"nodes": [node], "edges": [], "create_only": False,
                          "operation_id": op, "review_claims": True}
    assert c.operation_status(op)["state"] == "committed"
    assert all(r.url.params["space"] == TEAM for r in rec.requests)


def test_async_guarded_ingest_and_receipt_do_not_retry_conflicts(env_clean):
    op = "00000000-0000-4000-8000-000000000488"
    rec = Recorder({("POST", "/v1/fragments"): fail(409, "write_conflict", details={"current_rev": 5}),
                    ("GET", "/v1/operations/" + op): ok({"state": "unknown", "applied": "unknown"})})
    async def run():
        async with AsyncVincClient(SK_KEY, space=TEAM, transport=httpx.MockTransport(rec)) as c:
            with pytest.raises(VincInvalidArgument) as error:
                await c.ingest([], operation_id=op)
            assert error.value.details["current_rev"] == 5
            assert (await c.operation_status(op))["applied"] == "unknown"
    asyncio.run(run())
    assert len(rec.requests) == 2


# ── 키·주소·스페이스 ────────────────────────────────────────────────────────────

def test_missing_key_is_an_auth_error(env_clean):
    with pytest.raises(VincAuthError) as info:
        VincClient()
    assert info.value.code == "not_authenticated"


def test_key_from_env(env_clean, monkeypatch):
    monkeypatch.setenv("VINC_API_KEY", SK_KEY)
    assert VincClient().read_only is False


def test_a_key_of_another_shape_is_refused_before_any_request(env_clean):
    with pytest.raises(VincAuthError):
        VincClient("sk-live-something")


@pytest.mark.parametrize("url", ["http://mcp.vincs.io", "ftp://localhost"])
def test_plain_http_is_allowed_only_on_loopback(env_clean, url):
    with pytest.raises(VincInvalidArgument):
        VincClient(RO_KEY, base_url=url)


def test_loopback_http_is_allowed(env_clean):
    assert VincClient(RO_KEY, base_url="http://127.0.0.1:8787/").base_url == "http://127.0.0.1:8787"


def test_space_must_be_personal_or_a_uuid(env_clean):
    with pytest.raises(ValueError):
        VincClient(RO_KEY, space="my-team")


def test_team_space_is_sent_as_a_lowercased_query_parameter(env_clean):
    rec = Recorder({("POST", "/v1/brief"): ok(RESOLVED)})
    client(rec, space=TEAM.upper()).brief("tokens")
    assert rec.requests[0].url.params["space"] == TEAM


def test_personal_space_sends_no_space_parameter(env_clean):
    rec = Recorder({("POST", "/v1/brief"): ok(RESOLVED)})
    client(rec).brief("tokens")
    assert "space" not in rec.requests[0].url.params


def test_per_call_space_overrides_the_client_space(env_clean):
    rec = Recorder({("POST", "/v1/brief"): ok(RESOLVED)})
    client(rec).brief("tokens", space=TEAM)
    assert rec.requests[0].url.params["space"] == TEAM


# ── 요청 모양 ──────────────────────────────────────────────────────────────────

def test_brief_posts_the_body_with_a_bearer_key(env_clean):
    rec = Recorder({("POST", "/v1/brief"): ok(RESOLVED)})
    result = client(rec).brief("why oklch", max_excerpts=2)
    request = rec.requests[0]
    assert request.headers["Authorization"] == f"Bearer {RO_KEY}"
    assert rec.body() == {"query": "why oklch", "max_excerpts": 2}
    assert result["node"]["id"] == "decision:tokens-are-oklch"


def test_recall_joins_tags_with_commas(env_clean):
    rec = Recorder({("GET", "/v1/episodes"): ok({"rows": []})})
    client(rec).recall(tags=["release", "deploy"], limit=5)
    params = rec.requests[0].url.params
    assert params["tags"] == "release,deploy" and params["limit"] == "5"


def test_node_ids_are_one_path_segment(env_clean):
    rec = Recorder({})
    with pytest.raises(VincNotFound):
        client(rec).node("concept:a/b")
    assert rec.requests[0].url.raw_path.startswith(b"/v1/nodes/concept%3Aa%2Fb")


# ── 실패 봉투 ──────────────────────────────────────────────────────────────────

def test_quota_keeps_retry_after(env_clean):
    rec = Recorder({("POST", "/v1/brief"): fail(429, "quota_exceeded", "Daily limit reached.",
                                               details={"retry_after_s": 30},
                                               headers={"Retry-After": "30"})})
    with pytest.raises(VincQuotaExceeded) as info:
        client(rec).brief("x" * 5)
    assert info.value.retry_after == 30 and info.value.request_id == "req_test"


def test_unauthorized_is_an_auth_error(env_clean):
    rec = Recorder({("POST", "/v1/brief"): fail(401, "not_authenticated")})
    with pytest.raises(VincAuthError):
        client(rec).brief("tokens")


def test_server_error_is_unavailable_with_its_details(env_clean):
    rec = Recorder({("POST", "/v1/fragments"): fail(
        503, "upstream_unavailable", "sidecar write unconfirmed",
        details={"retryable": False, "write_may_have_applied": True})})
    with pytest.raises(VincUnavailable) as info:
        client(rec, SK_KEY).ingest([{"id": "concept:x", "type": "concept"}])
    assert info.value.details["write_may_have_applied"] is True


def test_a_redirect_is_not_followed(env_clean):
    rec = Recorder({("POST", "/v1/brief"): lambda r: httpx.Response(
        302, headers={"Location": "https://elsewhere.example/v1/brief"})})
    with pytest.raises(VincUnavailable):
        client(rec).brief("tokens")
    assert len(rec.requests) == 1


def test_a_non_json_success_is_unavailable(env_clean):
    rec = Recorder({("GET", "/v1/me"): lambda r: httpx.Response(200, text="<html>")})
    with pytest.raises(VincUnavailable):
        client(rec).whoami()


def test_a_network_failure_is_unavailable(env_clean):
    def boom(request):
        raise httpx.ConnectError("refused", request=request)
    with pytest.raises(VincUnavailable):
        VincClient(RO_KEY, transport=httpx.MockTransport(boom)).whoami()


# ── get_context ────────────────────────────────────────────────────────────────

def test_get_context_renders_a_resolved_brief(env_clean):
    rec = Recorder({("POST", "/v1/brief"): ok(RESOLVED)})
    block = client(rec).get_context("why are tokens oklch")
    assert block and "decision:tokens-are-oklch" in block and "DATA" in block


def test_get_context_falls_back_to_search_only_when_asked(env_clean):
    search = {"node_hits": [{"id": "concept:spacing", "title": "Spacing scale",
                             "kind": "concept"}], "rows": []}
    rec = Recorder({("POST", "/v1/brief"): ok({"status": "none"}),
                    ("POST", "/v1/search"): ok(search)})
    assert client(rec).get_context("spacing scale") is None
    assert len(rec.requests) == 1
    block = client(rec).get_context("spacing scale", search_fallback=True)
    assert block and "concept:spacing" in block


def test_a_tie_is_widened_with_search_and_keeps_the_candidates_first(env_clean):
    tie = {"status": "choose", "candidates": [{"id": "concept:design-tokens", "title": "Tokens"},
                                              {"id": "concept:agent-design", "title": "Design role"}]}
    search = {"rows": [{"doc_id": "vinc.design.hub-notes", "doc_title": "Hub notes",
                        "snippet": "values live in design-tokens.json"}], "node_hits": []}
    rec = Recorder({("POST", "/v1/brief"): ok(tie), ("POST", "/v1/search"): ok(search)})
    block = client(rec).get_context("where do token values live", search_fallback=True)
    assert block.index("concept:design-tokens") < block.index("vinc.design.hub-notes")
    assert [r.url.path for r in rec.requests] == ["/v1/brief", "/v1/search"]


def test_a_resolved_brief_is_not_widened(env_clean):
    rec = Recorder({("POST", "/v1/brief"): ok(RESOLVED)})
    client(rec).get_context("why are tokens oklch", search_fallback=True)
    assert [r.url.path for r in rec.requests] == ["/v1/brief"]


def test_get_context_skips_the_turn_when_vinc_is_down(env_clean, caplog):
    rec = Recorder({("POST", "/v1/brief"): fail(503, "upstream_unavailable")})
    with caplog.at_level(logging.WARNING, logger="vinc_client"):
        assert client(rec).get_context("tokens please") is None
    assert "skipped" in caplog.text


def test_get_context_skips_the_turn_on_quota(env_clean):
    rec = Recorder({("POST", "/v1/brief"): fail(429, "quota_exceeded",
                                               headers={"Retry-After": "60"})})
    assert client(rec).get_context("tokens please") is None


def test_get_context_still_raises_on_a_key_problem(env_clean):
    rec = Recorder({("POST", "/v1/brief"): fail(403, "role_forbidden")})
    with pytest.raises(VincAuthError):
        client(rec).get_context("tokens please")


def test_get_context_skips_very_short_input_without_a_call(env_clean):
    rec = Recorder({})
    assert client(rec).get_context("ok") is None
    assert rec.requests == []


# ── 쓰기 ───────────────────────────────────────────────────────────────────────

def test_a_read_only_key_never_sends_a_write(env_clean):
    rec = Recorder({})
    with pytest.raises(VincAuthError) as info:
        client(rec).record_episode("Merged the contrast fix", about=["decision:tokens-are-oklch"])
    assert info.value.code == "read_only_key" and rec.requests == []
    with pytest.raises(VincAuthError):
        client(rec).ingest([{"id": "concept:x", "type": "concept"}])
    assert rec.requests == []


def test_an_unknown_about_id_stops_before_the_write(env_clean):
    rec = Recorder({("GET", "/v1/nodes/decision:missing"): fail(404, "not_found")})
    with pytest.raises(VincNotFound):
        client(rec, SK_KEY).record_episode("Something happened", about=["decision:missing"])
    assert [r.url.path for r in rec.requests] == ["/v1/nodes/decision:missing"]


def test_record_episode_sends_one_fragment_with_stub_endpoints(env_clean):
    written = {"merged": {"nodes": {"created": 1, "matched": 1}, "edges": {"created": 1}},
               "stored": [], "warnings": []}
    rec = Recorder({("GET", "/v1/nodes/decision:tokens-are-oklch"): ok(RESOLVED),
                    ("POST", "/v1/fragments"): ok(written)})
    result = client(rec, SK_KEY).record_episode(
        "Merged the contrast fix", summary="Dark theme secondary button now passes.",
        about=["decision:tokens-are-oklch"], date="2026-09-28", tags=["design"])
    body = rec.body()
    episode, stub = body["nodes"]
    assert episode["type"] == "episode" and episode["date"] == "2026-09-28"
    assert stub == {"id": "decision:tokens-are-oklch", "type": "decision"}
    assert body["edges"] == [{"from": episode["id"], "to": "decision:tokens-are-oklch",
                              "type": "about"}]
    assert result is written or result == written


def test_a_partial_write_raises_with_the_result(env_clean):
    partial = {"partial": True, "warnings": ["edge skipped"], "stored": []}
    rec = Recorder({("POST", "/v1/fragments"): ok(partial)})
    with pytest.raises(VincPartialWrite) as info:
        client(rec, SK_KEY).ingest([{"id": "concept:x", "type": "concept"}])
    assert info.value.result["warnings"] == ["edge skipped"]


# ── async ─────────────────────────────────────────────────────────────────────

def test_async_client_matches_the_sync_one(env_clean):
    rec = Recorder({("POST", "/v1/brief"): ok(RESOLVED),
                    ("GET", "/v1/nodes/decision:tokens-are-oklch"): ok(RESOLVED),
                    ("POST", "/v1/fragments"): ok({"warnings": [], "stored": []})})

    async def run():
        async with AsyncVincClient(SK_KEY, space=TEAM,
                                   transport=httpx.MockTransport(rec)) as c:
            block = await c.get_context("why are tokens oklch")
            await c.record_episode("Checked", about=["decision:tokens-are-oklch"],
                                   date="2026-09-28")
            return block

    block = asyncio.run(run())
    assert block and "decision:tokens-are-oklch" in block
    assert [r.url.path for r in rec.requests] == [
        "/v1/brief", "/v1/nodes/decision:tokens-are-oklch", "/v1/fragments"]
    assert all(r.url.params["space"] == TEAM for r in rec.requests)


def test_async_get_context_skips_when_down(env_clean):
    rec = Recorder({("POST", "/v1/brief"): fail(502, "upstream_unavailable")})

    async def run():
        async with AsyncVincClient(RO_KEY, transport=httpx.MockTransport(rec)) as c:
            return await c.get_context("tokens please")

    assert asyncio.run(run()) is None
