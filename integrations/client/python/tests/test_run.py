from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest

from conftest import RESOLVED, RO_KEY, SK_KEY, Recorder, fail, ok
from vinc_client import (OUTCOMES, AsyncVincClient, VincAuthError, VincClient, VincNotFound,
                         build_run_fragment, run_episode_id, trace_ref)

ROLE = "concept:agent-reviewer"
WRITTEN = {"merged": {"nodes": {"created": 1, "matched": 1}, "edges": {"created": 1}},
           "stored": [], "warnings": []}


def client(recorder: Recorder, key: str = SK_KEY) -> VincClient:
    return VincClient(key, transport=httpx.MockTransport(recorder))


def test_the_same_run_id_always_names_the_same_episode():
    assert run_episode_id("run-42") == run_episode_id("run-42")
    assert run_episode_id("run-42").startswith("episode:run-run-42-")
    # 대소문자·구두점만 다른 두 실행은 슬러그가 같아도 다른 노드다.
    assert run_episode_id("Run_42") != run_episode_id("run-42")
    for bad in ("", " run", "run 42", "x" * 129, None):
        with pytest.raises(ValueError):
            run_episode_id(bad)


def test_trace_ref_hashes_the_file_and_keeps_no_local_path(tmp_path):
    raw = b'{"type":"assistant","text":"done"}\n' * 1000
    path = tmp_path / "secret-dir" / "run-42.jsonl"
    path.parent.mkdir()
    path.write_bytes(raw)
    ref = trace_ref(path)
    assert ref == {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                   "name": "run-42.jsonl"}
    assert "secret-dir" not in str(ref)
    assert trace_ref(path, uri="https://example.test/run-42.jsonl")["uri"].endswith("run-42.jsonl")


def test_the_fragment_carries_the_run_props_and_is_about_the_role(tmp_path):
    trace = tmp_path / "t.jsonl"
    trace.write_text("x", encoding="utf-8")
    fragment = build_run_fragment(
        "run-42", role=ROLE, outcome="success", task_ref="task:7", host="langgraph",
        model_family="claude", steps=3, tool_calls=5, tokens_in=1200, tokens_out=300,
        cost_usd=0.012, gate_results={"side_effect_evidence": "pass"},
        cited_ids=["decision:tokens-are-oklch", "decision:tokens-are-oklch", "record:audit"],
        shims_fired={"record:shim-nudge": 1}, trace=trace_ref(trace),
        about=["decision:tokens-are-oklch"], date="2026-10-25")
    episode, *stubs = fragment["nodes"]
    assert episode["id"] == run_episode_id("run-42") and episode["date"] == "2026-10-25"
    assert episode["tags"] == ["agent-run"]
    props = episode["props"]
    assert props["run_id"] == "run-42" and props["role"] == ROLE and props["outcome"] == "success"
    assert props["cited_ids"] == ["decision:tokens-are-oklch", "record:audit"]
    assert props["trace_ref"]["sha256"] == hashlib.sha256(b"x").hexdigest()
    assert props["cost_usd"] == 0.012 and props["tool_calls"] == 5
    assert [s["id"] for s in stubs] == [ROLE, "decision:tokens-are-oklch"]
    assert {e["to"] for e in fragment["edges"]} == {ROLE, "decision:tokens-are-oklch"}
    assert all(e["type"] == "about" for e in fragment["edges"])


def test_values_left_out_are_not_stored():
    props = build_run_fragment("r1", role=ROLE, outcome="waiting")["nodes"][0]["props"]
    assert props == {"run_id": "r1", "role": ROLE, "outcome": "waiting"}


@pytest.mark.parametrize("kwargs", [
    {"role": "concept:reviewer"},
    {"outcome": "done"},
    {"steps": -1},
    {"tool_calls": True},
    {"cost_usd": -0.1},
    {"cited_ids": ["no-prefix"]},
    {"shims_fired": {"record:shim": -2}},
    {"trace": {"sha256": "abc", "bytes": 1}},
    {"trace": {"sha256": "0" * 64, "bytes": -1}},
])
def test_bad_values_are_refused_before_anything_is_sent(kwargs):
    base = {"role": ROLE, "outcome": "success"}
    with pytest.raises(ValueError):
        build_run_fragment("r1", **{**base, **kwargs})


def test_outcomes_are_the_documented_set():
    assert OUTCOMES == ("success", "failure", "blocked", "waiting", "abandoned", "error")


def test_a_read_only_key_never_sends_a_run(env_clean):
    rec = Recorder({})
    with pytest.raises(VincAuthError):
        client(rec, RO_KEY).record_run("r1", role=ROLE, outcome="success")
    assert rec.requests == []


def test_an_unknown_role_stops_before_the_write(env_clean):
    rec = Recorder({("GET", f"/v1/nodes/{ROLE}"): fail(404, "not_found")})
    with pytest.raises(VincNotFound):
        client(rec).record_run("r1", role=ROLE, outcome="success")
    assert [r.method for r in rec.requests] == ["GET"]


def test_record_run_reads_its_targets_then_sends_one_fragment(env_clean):
    rec = Recorder({("GET", f"/v1/nodes/{ROLE}"): ok(RESOLVED),
                    ("POST", "/v1/fragments"): ok(WRITTEN)})
    result = client(rec).record_run("run-42", role=ROLE, outcome="failure", steps=2,
                                    date="2026-10-25")
    assert [(r.method, r.url.path) for r in rec.requests] == [
        ("GET", f"/v1/nodes/{ROLE}"), ("POST", "/v1/fragments")]
    episode = rec.body()["nodes"][0]
    assert episode["id"] == run_episode_id("run-42") and episode["props"]["outcome"] == "failure"
    assert result == WRITTEN


def test_a_retry_of_the_same_run_sends_the_same_body(env_clean):
    rec = Recorder({("GET", f"/v1/nodes/{ROLE}"): ok(RESOLVED),
                    ("POST", "/v1/fragments"): ok(WRITTEN)})
    for _ in range(2):
        client(rec).record_run("run-42", role=ROLE, outcome="success", date="2026-10-25")
    posts = [r.content for r in rec.requests if r.method == "POST"]
    assert len(posts) == 2 and posts[0] == posts[1]


def test_async_record_run_matches_the_sync_one(env_clean):
    sync_rec = Recorder({("GET", f"/v1/nodes/{ROLE}"): ok(RESOLVED),
                         ("POST", "/v1/fragments"): ok(WRITTEN)})
    async_rec = Recorder({("GET", f"/v1/nodes/{ROLE}"): ok(RESOLVED),
                          ("POST", "/v1/fragments"): ok(WRITTEN)})
    kwargs = {"role": ROLE, "outcome": "success", "tokens_in": 10, "date": "2026-10-25"}
    client(sync_rec).record_run("run-7", **kwargs)

    async def run():
        async with AsyncVincClient(SK_KEY, transport=httpx.MockTransport(async_rec)) as c:
            await c.record_run("run-7", **kwargs)

    asyncio.run(run())
    assert sync_rec.body() == async_rec.body()
