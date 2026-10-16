"""재시도·미리보기 계약이 SDK 헬퍼와 래퍼까지 간다(VIN-601).

record_episode·write_package 는 create_only·operation_id 를 ingest 와 같은 본문으로 싣고,
삭제·병합 4종은 dry_run 으로 미리보기를 받고 그 preview_digest 로 적용한다.
edges·role_check 는 REST 의 읽기 라우트를 그대로 감싼다.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from conftest import RO_KEY, SK_KEY, TEAM, Recorder, ok
from vinc_client import AsyncVincClient, VincAuthError, VincClient, VincInvalidArgument

OP = "00000000-0000-4000-8000-000000000601"
DIGEST = "sha256:preview-fixture"
PACKAGE = {"question": "Which policy applies?", "conclusion": "Keep the accepted policy.",
           "claims": [{"claim": "The accepted policy still applies.",
                       "evidence_ids": ["decision:policy"]}],
           "produced_by": "agent:writer", "run_id": "fixture-1", "package_id": "record:handoff"}


def run(async_mode, rec, key, call):
    """같은 호출을 동기·비동기 클라이언트로 돌린다. call(client) 는 값 또는 코루틴을 돌려준다."""
    if not async_mode:
        with VincClient(key, space=TEAM, transport=httpx.MockTransport(rec)) as c:
            return call(c)

    async def go():
        async with AsyncVincClient(key, space=TEAM, transport=httpx.MockTransport(rec)) as c:
            return await call(c)
    return asyncio.run(go())


def body(request):
    return json.loads(request.content) if request.content else None


@pytest.mark.parametrize("async_mode", [False, True])
def test_record_episode_and_write_package_carry_create_only_and_operation_id(env_clean, async_mode):
    rec = Recorder({("POST", "/v1/fragments"): ok({"executed": True, "partial": False}),
                    ("GET", "/v1/nodes/decision:policy"): ok({"found": True})})
    run(async_mode, rec, SK_KEY, lambda c: c.record_episode(
        "Approved", about=["decision:policy"], episode_id="episode:run-one",
        date="2026-10-16", create_only=True, operation_id=OP))
    episode = body(rec.requests[-1])
    assert (episode["create_only"], episode["operation_id"]) == (True, OP)
    assert episode["nodes"][0]["id"] == "episode:run-one"

    result = run(async_mode, rec, SK_KEY, lambda c: c.write_package(
        **PACKAGE, space=TEAM, create_only=True, operation_id=OP))
    package = body(rec.requests[-1])
    assert (package["create_only"], package["operation_id"]) == (True, OP)
    assert result["package_id"] == "record:handoff"
    assert all(r.url.params["space"] == TEAM for r in rec.requests)


@pytest.mark.parametrize("async_mode", [False, True])
def test_helpers_omit_the_options_when_not_given(env_clean, async_mode):
    # 기존 호출은 본문이 바뀌지 않는다: 미지정이면 키 자체가 없다(서버 기본값 그대로).
    rec = Recorder({("POST", "/v1/fragments"): ok({"executed": True})})
    run(async_mode, rec, SK_KEY, lambda c: c.record_episode("Approved", episode_id="episode:x"))
    run(async_mode, rec, SK_KEY, lambda c: c.write_package(**PACKAGE, space=TEAM))
    for request in rec.requests:
        assert not {"create_only", "operation_id"} & set(body(request))


def _preview_routes(seen_digest):
    def answer(request):
        params = request.url.params
        payload = body(request) or {}
        options = {**params, **payload}
        if options.get("dry_run") in ("true", True):
            return httpx.Response(200, json={"executed": True, "dry_run": True,
                                             "preview_digest": DIGEST, "preview": {"edges": 2}})
        seen_digest.append((options.get("preview_digest"), options.get("operation_id")))
        return httpx.Response(200, json={"executed": True, "found": True})
    return answer


CASES = [
    ("delete", ("concept:a",), "DELETE", "/v1/nodes/concept:a", "query", {}),
    ("delete_edge", ("concept:a", "concept:b", "relates_to"), "DELETE", "/v1/edges", "query",
     {"from_id": "concept:a", "to_id": "concept:b", "type": "relates_to"}),
    ("merge_nodes", ("concept:keep", "concept:drop"), "POST", "/v1/nodes/merge", "body",
     {"keep_id": "concept:keep", "drop_id": "concept:drop"}),
    ("doc_delete", ("vinc.spec.fixture",), "DELETE", "/v1/documents/vinc.spec.fixture", "query", {}),
]


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("name,args,method,path,where,fixed", CASES, ids=[c[0] for c in CASES])
def test_preview_then_apply_binds_the_digest(env_clean, async_mode, name, args, method, path,
                                             where, fixed):
    applied = []
    rec = Recorder({(method, path): _preview_routes(applied)})
    preview = run(async_mode, rec, SK_KEY, lambda c: getattr(c, name)(*args, dry_run=True))
    assert preview["preview_digest"] == DIGEST and not applied
    run(async_mode, rec, SK_KEY, lambda c: getattr(c, name)(
        *args, preview_digest=preview["preview_digest"], operation_id=OP))
    assert applied == [(DIGEST, OP)]

    first, second = rec.requests
    if where == "query":
        sent = [dict(r.url.params) for r in rec.requests]
        assert sent[0] == {**fixed, "dry_run": "true", "space": TEAM}
        assert sent[1] == {**fixed, "preview_digest": DIGEST, "operation_id": OP, "space": TEAM}
        assert first.content == second.content == b""
    else:
        assert body(first) == {**fixed, "dry_run": True}
        assert body(second) == {**fixed, "preview_digest": DIGEST, "operation_id": OP}


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("name,args", [(c[0], c[1]) for c in CASES], ids=[c[0] for c in CASES])
def test_delete_and_merge_need_a_write_key_even_for_a_preview(env_clean, async_mode, name, args):
    # 서버도 미리보기를 쓰기 도구 등급으로 거절한다. 보내기 전에 같은 판정을 한다.
    rec = Recorder({})
    with pytest.raises(VincAuthError, match="read_only_key"):
        run(async_mode, rec, RO_KEY, lambda c: getattr(c, name)(*args, dry_run=True))
    assert rec.requests == []


@pytest.mark.parametrize("async_mode", [False, True])
def test_edges_reads_one_node_or_several(env_clean, async_mode):
    rec = Recorder({("GET", "/v1/nodes/concept:a/edges"): ok({"edges": []}),
                    ("POST", "/v1/edges/query"): ok({"edges": []})})
    run(async_mode, rec, RO_KEY, lambda c: c.edges("concept:a", type="relates_to", dir="out", limit=10))
    run(async_mode, rec, RO_KEY, lambda c: c.edges(nodes=("concept:a", "concept:b"), dir="in"))
    one, many = rec.requests
    assert dict(one.url.params) == {"type": "relates_to", "dir": "out", "limit": "10", "space": TEAM}
    assert body(many) == {"nodes": ["concept:a", "concept:b"], "dir": "in"}
    assert many.url.params["space"] == TEAM


@pytest.mark.parametrize("kwargs", [{}, {"node": "concept:a", "nodes": ["concept:b"]}])
def test_edges_needs_exactly_one_of_node_or_nodes(env_clean, kwargs):
    rec = Recorder({})
    with pytest.raises(VincInvalidArgument, match="exactly one"):
        run(False, rec, RO_KEY, lambda c: c.edges(**kwargs))
    assert rec.requests == []


@pytest.mark.parametrize("async_mode", [False, True])
def test_role_check_posts_the_output_with_a_read_only_key(env_clean, async_mode):
    verdict = {"verdict": "interrupt", "failed_checks": [], "person_only_actions": ["merge"]}
    rec = Recorder({("POST", "/v1/roles/agent-dev-core/check"): ok(verdict)})
    requests = [{"action": "merge", "reason": "Needs an owner."}]
    result = run(async_mode, rec, RO_KEY, lambda c: c.role_check(
        "agent-dev-core", "Ready for review.", person_only_requests=requests, self_authored=True))
    assert result == verdict
    assert body(rec.requests[0]) == {"output": "Ready for review.", "person_only_requests": requests,
                                     "self_authored": True}
    run(async_mode, rec, RO_KEY, lambda c: c.role_check("agent-dev-core", "Plain."))
    assert body(rec.requests[1]) == {"output": "Plain."}
