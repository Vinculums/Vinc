"""컨텍스트 컴파일러: P0 가 prefetch 와 바이트로 같고, 불변식을 어기는 계획은 번호로 거절된다."""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import itertools
import json

import httpx
import pytest
from vinc_client import (FULL_TEXT, AsyncVincClient, ContextItem, ContextPlan, ContextRequest,
                         EvidenceRef, VincClient, VincInvalidArgument, VincUnavailable,
                         acompile_context, check_replay, compile_context, replay_context)
from vinc_client.prefetch import CITE_LINE, role_context

KEY = "vinc_ro_" + "a" * 32
SPACE = "01a0a0e0-3f91-7128-9876-6b543f28de0a"
ROLE = "concept:agent-reviewer"
TEXTS = {"vinc.spec.alpha": "Alpha rules.\n" + "a" * 900 + "\nALPHA TAIL",
         "vinc.spec.beta": "Beta rules.\n" + "b" * 400 + "\nBETA TAIL"}


def _doc(doc_id, text=None, **extra):
    text = TEXTS[doc_id] if text is None else text
    return {"executed": True, "doc_id": doc_id, "title": doc_id.title(), "verified": True,
            "text": text, "content_hash": hashlib.sha256(text.encode()).hexdigest(),
            "updated_at": "2026-10-20T00:00:00Z", **extra}


def make_handler(*, reads=("vinc.spec.alpha", "concept:doc.vinc.spec.beta"), seen=None,
                 docs=None, nodes=None, brief=None, search=None, brief_status=200):
    """역할 하나, 필수 문서 둘(alpha 는 미러 있음, beta 는 없음), brief 와 search."""
    docs = {k: _doc(k) for k in TEXTS} if docs is None else docs
    nodes = {"concept:doc.vinc.spec.alpha": {"id": "concept:doc.vinc.spec.alpha"}} \
        if nodes is None else nodes
    role = {"found": True, "id": ROLE, "domain": "agent/engineering", "rev": 3, "hash": "f" * 64,
            "props": {"reads": list(reads), "mission": "Review evidence.", "never": ["merge"]}}

    def handler(request):
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path.startswith("/v1/roles/"):
            return httpx.Response(200, json=role)
        if path.startswith("/v1/documents/"):
            key = path.removeprefix("/v1/documents/")
            if key not in docs:
                return httpx.Response(404, json={"error": {"code": "not_found", "message": ""}})
            return httpx.Response(200, json=docs[key])
        if path.startswith("/v1/nodes/"):
            nid = path.removeprefix("/v1/nodes/")
            if nid not in nodes:
                return httpx.Response(404, json={"error": {"code": "not_found", "message": ""}})
            return httpx.Response(200, json={"executed": True, "found": True, "node": nodes[nid]})
        if path.endswith("brief"):
            if brief_status != 200:
                return httpx.Response(brief_status, json={"error": {"code": "upstream_unavailable",
                                                                    "message": "down"}})
            return httpx.Response(200, json=brief or {
                "status": "resolved", "node": {"id": "decision:fixture", "cite_id": "decision:fixture",
                                               "title": "Fixture"}, "one_liner": "Keep it."})
        return httpx.Response(200, json=search or {"node_hits": [
            {"id": "record:lesson", "cite_id": "record:lesson", "title": "Prior lesson"},
            {"id": "record:other", "cite_id": "record:other", "title": "Other lesson"}]})
    return handler


def client(handler, *, concurrency=None, async_mode=False):
    cls = AsyncVincClient if async_mode else VincClient
    args = {} if concurrency is None else {"prefetch_concurrency": concurrency}
    return cls(KEY, transport=httpx.MockTransport(handler), **args)


def compile_(handler, request, policy=FULL_TEXT, *, async_mode=False):
    c = client(handler, async_mode=async_mode)
    if async_mode:
        return asyncio.run(acompile_context(c, request, policy))
    return compile_context(c, request, policy)


def request(**kw):
    return ContextRequest(role=ROLE, task="Review the policy.", space=SPACE, **kw)


class Policy:
    """시험용 정책: 함수 하나로 계획을 만든다."""

    def __init__(self, plan, pid="test"):
        self._plan, self.id = plan, pid

    def plan(self, req, reader):
        return self._plan(req, reader)


def p0_evidence(req, reader):
    reader.brief(req.task, max_excerpts=3)
    b = reader.last_call
    reader.search(f"{req.task}\nRole topic: agent/engineering", limit=5)
    return (EvidenceRef(b), EvidenceRef(reader.last_call))


def rejected(invariant):
    return pytest.raises(VincInvalidArgument, match=f"context_plan_rejected: {invariant}:")


# ── P0 ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("concurrency", [1, 8])
@pytest.mark.parametrize("max_chars", [24000, 5300])
def test_p0_block_equals_prefetch_byte_for_byte(async_mode, concurrency, max_chars):
    handler = make_handler()
    c = client(handler, concurrency=concurrency, async_mode=async_mode)
    value = c.prefetch(ROLE, "Review the policy.", space=SPACE, max_chars=max_chars)
    expected = asyncio.run(value) if async_mode else value
    got = compile_(handler, request(max_chars=max_chars), async_mode=async_mode)
    assert got.block == expected
    assert got.sha256 == hashlib.sha256(expected.encode()).hexdigest()
    assert got.as_prefetch().contract["id"] == ROLE


def test_p0_manifest_accounts_for_every_reading_and_read():
    got = compile_(make_handler(), request())
    items = got.manifest["items"]
    assert [i["source"] for i in items] == ["vinc.spec.alpha", "concept:doc.vinc.spec.beta"]
    assert [i["cite_id"] for i in items] == ["concept:doc.vinc.spec.alpha", None]
    assert all(i["form"] == "full" and i["required"] for i in items)
    assert items[0]["content_hash"] == hashlib.sha256(TEXTS["vinc.spec.alpha"].encode()).hexdigest()
    assert got.manifest["omitted"] == []
    assert got.manifest["budget"] == {"max_chars": 24000, "used": len(got.block)}
    assert got.manifest["reads"] == {"role": 1, "document": 2, "node": 2, "brief": 1, "search": 1}
    assert got.manifest["policy"]["id"] == "p0-full"
    assert len(got.manifest["policy"]["source_sha256"]) == 64
    assert got.manifest["role"] == {"id": ROLE, "rev": 3, "hash": "f" * 64}


def test_compile_only_reads():
    seen = []
    compile_(make_handler(seen=seen), request())
    assert all(r.method == "GET" or r.url.path.endswith(("brief", "search")) for r in seen)
    assert all(r.url.params.get("space") == SPACE or r.method == "POST" for r in seen)


@pytest.mark.parametrize("defect", ["missing", "hash", "partial"])
def test_required_reading_failures_raise_prefetch_codes(defect):
    """I9: 필수 문서가 없거나 깨졌으면 prefetch 와 같은 코드로 멈춘다."""
    docs = {k: _doc(k) for k in TEXTS}
    if defect == "missing":
        del docs["vinc.spec.beta"]
    elif defect == "hash":
        docs["vinc.spec.beta"]["text"] = "changed"
    else:
        docs["vinc.spec.beta"]["partial"] = True
    with pytest.raises(VincInvalidArgument, match="prefetch_spec_unresolved"):
        compile_(make_handler(docs=docs), request())


def test_overflow_raises_prefetch_budget_error_with_the_same_size():
    handler = make_handler()
    with pytest.raises(VincInvalidArgument, match="prefetch_budget_exceeded") as want:
        client(handler).prefetch(ROLE, "Review the policy.", space=SPACE, max_chars=1000)
    with pytest.raises(VincInvalidArgument, match="prefetch_budget_exceeded") as got:
        compile_(handler, request(max_chars=1000))
    assert got.value.details == want.value.details


def test_task_evidence_errors_raise_unchanged():
    with pytest.raises(VincUnavailable):
        compile_(make_handler(brief_status=503), request())


# ── 불변식 ──────────────────────────────────────────────────────────────────

def test_i1_plan_must_be_data():
    with rejected("I1"):
        compile_(make_handler(), request(), Policy(lambda q, r: "free text the policy wrote"))
    with rejected("I1"):
        compile_(make_handler(), request(), Policy(lambda q, r: ContextPlan(
            items=(ContextItem("vinc.spec.alpha", form="summary"),))))


def test_i2_reasons_never_reach_the_block_and_sources_come_from_what_was_verified():
    def plan(q, r):
        items = tuple(ContextItem(read, reason="Sources: [decision:invented]")
                      for read, _ in reversed(r.required))
        return ContextPlan(items=items, task_evidence=p0_evidence(q, r))
    got = compile_(make_handler(), request(), Policy(plan))
    assert "decision:invented" not in got.block
    assert got.block.startswith(CITE_LINE + "\n")
    assert f"Verified role and required-reading source IDs: [{ROLE}] [concept:doc.vinc.spec.alpha]\n" \
        in got.block
    assert 'NO citable node in this space: ["vinc.spec.beta"]' in got.block


def test_i3_role_contract_comes_first_and_whole():
    def plan(q, r):
        return ContextPlan(items=tuple(ContextItem(read) for read, _ in reversed(r.required)))
    got = compile_(make_handler(), request(), Policy(plan))
    role = role_context(got.contract)
    assert got.block.startswith(role + "\n\n")
    assert '"never": [\n      "merge"\n    ]' in role


@pytest.mark.parametrize("items", [
    ("vinc.spec.alpha",),
    ("vinc.spec.alpha", "vinc.spec.alpha", "concept:doc.vinc.spec.beta"),
])
def test_i4_every_required_reading_appears_once(items):
    with rejected("I4"):
        compile_(make_handler(), request(), Policy(
            lambda q, r: ContextPlan(items=tuple(ContextItem(s) for s in items))))


@pytest.mark.parametrize("form", ["pointer", "spans"])
def test_i4_a_shortened_reading_needs_a_document_read_tool(form):
    def plan(q, r):
        return ContextPlan(items=(ContextItem("vinc.spec.alpha", form=form, spans=((0, 12),)),
                                  ContextItem("concept:doc.vinc.spec.beta")))
    with rejected("I4"):
        compile_(make_handler(), request(), Policy(plan))
    got = compile_(make_handler(), request(read_tool=True), Policy(plan))
    assert got.manifest["omitted"] == [{"source": "vinc.spec.alpha", "reason": f"form:{form}"}]
    assert "ALPHA TAIL" not in got.block and "BETA TAIL" in got.block
    assert "read" in got.block and "with the document tool" in got.block
    # 짧게 실어도 인용 가능한 id 와 해시는 그대로 남는다.
    assert "[concept:doc.vinc.spec.alpha]" in got.block
    assert hashlib.sha256(TEXTS["vinc.spec.alpha"].encode()).hexdigest() in got.block


def test_i5_spans_are_cut_from_the_verified_text():
    def plan(q, r):
        return ContextPlan(items=(ContextItem("vinc.spec.alpha", form="spans",
                                              spans=((0, 12), (len(TEXTS["vinc.spec.alpha"]) - 10,
                                                               len(TEXTS["vinc.spec.alpha"])))),
                                  ContextItem("concept:doc.vinc.spec.beta")))
    got = compile_(make_handler(), request(read_tool=True), Policy(plan))
    assert '"text": "Alpha rules."' in got.block and '"text": "ALPHA TAIL"' in got.block
    end = len(TEXTS["vinc.spec.alpha"])
    assert got.manifest["items"][0]["spans"] == [[0, 12], [end - 10, end]]


@pytest.mark.parametrize("spans", [(), ((5, 3),), ((0, 10), (5, 20)), ((0, 99999),), ((-1, 4),)])
def test_i5_bad_spans_are_refused(spans):
    with rejected("I5"):
        compile_(make_handler(), request(read_tool=True), Policy(lambda q, r: ContextPlan(
            items=(ContextItem("vinc.spec.alpha", form="spans", spans=spans),
                   ContextItem("concept:doc.vinc.spec.beta")))))


def test_i6_the_block_fits_max_chars():
    got = compile_(make_handler(), request(max_chars=5300))
    assert len(got.block) <= 5300
    with pytest.raises(VincInvalidArgument, match="prefetch_budget_exceeded"):
        compile_(make_handler(), request(max_chars=2000))


def test_i7_a_source_must_be_read_through_the_reader():
    with rejected("I7"):
        compile_(make_handler(), request(), Policy(lambda q, r: ContextPlan(items=(
            ContextItem("vinc.spec.alpha"), ContextItem("concept:doc.vinc.spec.beta"),
            ContextItem("decision:never-read")))))


@pytest.mark.parametrize("refs", [(EvidenceRef(99),), (EvidenceRef(0),)])
def test_i7_task_evidence_must_be_a_brief_or_search_this_compile_made(refs):
    with rejected("I7"):
        compile_(make_handler(), request(), Policy(lambda q, r: ContextPlan(
            items=tuple(ContextItem(read) for read, _ in r.required), task_evidence=refs)))


@pytest.mark.parametrize("async_mode", [False, True])
def test_i7_replay_from_the_trace_gives_the_same_bytes(async_mode):
    got = compile_(make_handler(), request(), async_mode=async_mode)
    again = replay_context(got, request(), FULL_TEXT)
    assert again.block == got.block and again.sha256 == got.sha256
    check_replay(got, request(), FULL_TEXT)
    assert all(e.get("sha256") or e.get("error") for e in got.trace)
    json.dumps(list(got.trace))   # 원본 기록 파일에 그대로 쓸 수 있다


def test_i7_a_policy_with_hidden_state_fails_replay():
    counter = itertools.count()

    def plan(q, r):
        n = next(counter)
        items = [ContextItem(read) for read, _ in r.required]
        return ContextPlan(items=tuple(items if n % 2 == 0 else reversed(items)))
    policy = Policy(plan)
    got = compile_(make_handler(), request(), policy)
    with rejected("I7"):
        check_replay(got, request(), policy)


def test_i7_replay_refuses_a_read_the_trace_lacks():
    got = compile_(make_handler(), request(), Policy(lambda q, r: ContextPlan(
        items=tuple(ContextItem(read) for read, _ in r.required))))
    with rejected("I7"):
        replay_context(got, request(), FULL_TEXT)


def test_i8_a_non_current_node_shows_its_state_and_as_of_comes_from_the_request():
    seen = []
    nodes = {"concept:doc.vinc.spec.alpha": {"id": "concept:doc.vinc.spec.alpha"},
             "decision:old": {"id": "decision:old", "title": "Old rule", "kind": "decision",
                              "validity": "historical", "superseded_by": "decision:new"}}

    def plan(q, r):
        r.node("decision:old")
        r.brief(q.task, max_excerpts=3)
        return ContextPlan(items=(*(ContextItem(read) for read, _ in r.required),
                                  ContextItem("decision:old")),
                           task_evidence=(EvidenceRef(r.last_call),))
    got = compile_(make_handler(seen=seen, nodes=nodes), request(as_of="2026-09"), Policy(plan))
    assert '"validity": "historical"' in got.block and '"superseded_by": "decision:new"' in got.block
    assert "[decision:old]" in got.block   # 같은 스페이스에서 읽은 노드는 인용 가능하다
    brief = [r for r in seen if r.url.path.endswith("brief")][0]
    assert json.loads(brief.content)["as_of"] == "2026-09"
    assert not hasattr(got, "mode")


def test_anchors_are_read_and_unreadable_ones_reported():
    nodes = {"concept:doc.vinc.spec.alpha": {"id": "concept:doc.vinc.spec.alpha"},
             "decision:anchor": {"id": "decision:anchor", "title": "Anchor"}}

    def plan(q, r):
        assert set(r.anchors) == {"decision:anchor", "decision:gone"}
        return ContextPlan(items=(*(ContextItem(read) for read, _ in r.required),
                                  ContextItem("decision:anchor")))
    got = compile_(make_handler(nodes=nodes),
                   request(anchors=("decision:anchor", "decision:gone")), Policy(plan))
    assert {"source": "decision:gone", "reason": "anchor:not_found"} in got.manifest["omitted"]
    assert "Reference node: decision:anchor" in got.block


def test_search_rows_can_be_filtered_and_dropped_rows_are_recorded():
    def plan(q, r):
        r.search(q.task, limit=5)
        return ContextPlan(items=tuple(ContextItem(read) for read, _ in r.required),
                           task_evidence=(EvidenceRef(r.last_call, ids=("record:lesson",)),))
    got = compile_(make_handler(), request(), Policy(plan))
    assert "record:lesson" in got.block and "record:other" not in got.block
    assert {"source": "record:other", "reason": "task_evidence:dropped"} in got.manifest["omitted"]


def test_a_policy_cannot_change_what_the_compiler_verified():
    def plan(q, r):
        r.required[0][1]["text"] = "forged"
        r.contract["props"]["never"] = []
        r.search(q.task, limit=5)["node_hits"].append({"id": "record:forged"})
        r.search(q.task, limit=5)
        return ContextPlan(items=tuple(ContextItem(read) for read, _ in r.required),
                           task_evidence=(EvidenceRef(r.last_call),))
    got = compile_(make_handler(), request(), Policy(plan))
    assert "forged" not in got.block and "ALPHA TAIL" in got.block and '"merge"' in got.block


def test_anchors_must_be_ids():
    with pytest.raises(VincInvalidArgument, match="anchors"):
        compile_(make_handler(), request(anchors="decision:x"))


# ── 리뷰에서 나온 경계 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("broken", ["brief", "search"])
def test_p0_overflow_wins_over_a_failing_task_read_like_prefetch(broken):
    seen = []
    base = make_handler(seen=seen)

    def handler(request):
        if request.url.path.endswith(broken):
            seen.append(request)
            return httpx.Response(503, json={"error": {"code": "upstream_unavailable", "message": ""}})
        return base(request)
    with pytest.raises(VincInvalidArgument, match="prefetch_budget_exceeded"):
        client(handler, concurrency=1).prefetch(ROLE, "Review the policy.", space=SPACE, max_chars=1000)
    seen.clear()
    with pytest.raises(VincInvalidArgument, match="prefetch_budget_exceeded"):
        compile_(handler, request(max_chars=1000))
    assert not any(r.url.path.endswith(("brief", "search")) for r in seen)


def test_cancelling_the_async_compile_stops_its_reads():
    seen = []
    base = make_handler()

    async def slow(request):
        seen.append(request.url.path)
        await asyncio.sleep(0.05)
        return base(request)

    async def go():
        c = AsyncVincClient(KEY, transport=httpx.MockTransport(slow))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(acompile_context(c, request()), 0.12)
        before = len(seen)
        await asyncio.sleep(0.5)
        return before, len(seen)
    before, after = asyncio.run(go())
    assert after - before <= 1   # 이미 떠난 읽기 하나까지만


def test_replay_refuses_a_tampered_trace():
    got = compile_(make_handler(), request())
    trace = [dict(e) for e in got.trace]
    i = next(i for i, e in enumerate(trace) if e["method"] == "search")
    trace[i]["response"] = {"node_hits": [{"id": "record:forged", "title": "FORGED"}]}
    forged = dataclasses.replace(got, trace=tuple(trace))
    with rejected("I7"):
        replay_context(forged, request(), FULL_TEXT)


def test_reads_around_the_reader_are_refused():
    def other_space(q, r):
        r._call("node", {"node_id": "decision:secret", "space": "01a0b944-ecb4-737b-b3e6-5cc99ff37654"})
        return ContextPlan(items=(*(ContextItem(read) for read, _ in r.required),
                                  ContextItem("decision:secret")))

    def other_time(q, r):
        r._call("brief", {"query": q.task, "max_excerpts": 3, "space": SPACE, "as_of": "2001-01"})
        return ContextPlan(items=tuple(ContextItem(read) for read, _ in r.required),
                           task_evidence=(EvidenceRef(r.last_call),))

    def forged_answer(q, r):
        r.search(q.task, limit=5)
        r._calls[r.last_call]["response"]["node_hits"].append({"id": "record:forged"})
        return ContextPlan(items=tuple(ContextItem(read) for read, _ in r.required),
                           task_evidence=(EvidenceRef(r.last_call),))
    nodes = {"concept:doc.vinc.spec.alpha": {"id": "concept:doc.vinc.spec.alpha"},
             "decision:secret": {"id": "decision:secret", "title": "Secret"}}
    for plan in (other_space, other_time, forged_answer):
        with rejected("I7"):
            compile_(make_handler(nodes=nodes), request(), Policy(plan))


def test_a_policy_cannot_change_the_request_the_compiler_checks():
    def plan(q, r):
        object.__setattr__(q, "read_tool", True)
        return ContextPlan(items=(ContextItem("vinc.spec.alpha", form="pointer"),
                                  ContextItem("concept:doc.vinc.spec.beta")))
    req = request()
    with rejected("I4"):
        compile_(make_handler(), req, Policy(plan))
    assert req.read_tool is False


@pytest.mark.parametrize("read_tool", [False, True])
def test_a_required_reading_cannot_come_twice_under_its_other_spelling(read_tool):
    def plan(q, r):
        return ContextPlan(items=(ContextItem("vinc.spec.alpha", form="pointer" if read_tool else "full"),
                                  ContextItem("concept:doc.vinc.spec.beta"),
                                  ContextItem("concept:doc.vinc.spec.alpha")))
    with rejected("I4"):
        compile_(make_handler(), request(read_tool=read_tool), Policy(plan))


def test_the_other_spelling_of_a_required_reading_renders_as_declared():
    def plan(q, r):
        return ContextPlan(items=(ContextItem("concept:doc.vinc.spec.alpha"),
                                  ContextItem("vinc.spec.beta")))
    got = compile_(make_handler(), request(), Policy(plan))
    assert [i["source"] for i in got.manifest["items"]] == ["vinc.spec.alpha", "concept:doc.vinc.spec.beta"]
    assert got.block.count("ALPHA TAIL") == 1 and got.block.count("BETA TAIL") == 1


@pytest.mark.parametrize("status,code", [(503, "upstream_unavailable"), (400, "invalid_argument")])
def test_an_anchor_read_error_is_recorded_not_fatal(status, code):
    base = make_handler()

    def handler(request):
        if request.url.path == "/v1/nodes/decision:anchor":
            return httpx.Response(status, json={"error": {"code": code, "message": ""}})
        return base(request)
    got = compile_(handler, request(anchors=("decision:anchor",)))
    assert {"source": "decision:anchor", "reason": f"anchor:{code}"} in got.manifest["omitted"]


@pytest.mark.parametrize("bad", [
    lambda: ContextPlan(items=(ContextItem("vinc.spec.alpha", form="spans", spans=((0, 1, 2),)),)),
    lambda: ContextPlan(items=(ContextItem("vinc.spec.alpha", form="spans", spans=(5,)),)),
    lambda: ContextPlan(items=(ContextItem(["x"]),)),
    lambda: ContextPlan(items=(ContextItem(None),)),
    lambda: ContextPlan(items=(ContextItem("vinc.spec.alpha", reason=7),)),
    lambda: ContextPlan(items=(ContextItem("vinc.spec.alpha"), ContextItem("concept:doc.vinc.spec.beta")),
                        task_evidence=None),
    lambda: ContextPlan(items=(ContextItem("vinc.spec.alpha"), ContextItem("concept:doc.vinc.spec.beta")),
                        task_evidence=(EvidenceRef(0, ids="record:lesson"),)),
    lambda: ContextPlan(items=(ContextItem("vinc.spec.alpha"), ContextItem("concept:doc.vinc.spec.beta")),
                        task_evidence=(EvidenceRef(True),)),
])
def test_a_malformed_plan_is_refused_by_number(bad):
    with pytest.raises(VincInvalidArgument, match="context_plan_rejected: I"):
        compile_(make_handler(), request(read_tool=True), Policy(lambda q, r: bad()))


def test_recorded_errors_keep_retry_after_and_request_id():
    base = make_handler()

    def handler(request):
        if request.url.path.endswith("brief"):
            return httpx.Response(429, headers={"Retry-After": "17"}, json={"error": {
                "code": "quota_exceeded", "message": "", "request_id": "req-1"}})
        return base(request)
    from vinc_client import VincQuotaExceeded
    with pytest.raises(VincQuotaExceeded) as caught:
        compile_(handler, request())
    assert caught.value.retry_after == 17.0 and caught.value.request_id == "req-1"


def test_extra_ids_are_listed_apart_from_the_required_ones():
    nodes = {"concept:doc.vinc.spec.alpha": {"id": "concept:doc.vinc.spec.alpha"},
             "decision:anchor": {"id": "decision:anchor", "title": "Anchor"}}

    def plan(q, r):
        return ContextPlan(items=(*(ContextItem(read) for read, _ in r.required),
                                  ContextItem("decision:anchor")))
    got = compile_(make_handler(nodes=nodes), request(anchors=("decision:anchor",)), Policy(plan))
    assert f"Verified role and required-reading source IDs: [{ROLE}] [concept:doc.vinc.spec.alpha]\n" in got.block
    assert "Other verified source IDs in this context: [decision:anchor]\n" in got.block


def test_optional_spans_without_a_read_tool_do_not_point_at_one():
    docs = {**{k: _doc(k) for k in TEXTS}, "vinc.spec.gamma": {**_doc("vinc.spec.alpha"),
                                                              "doc_id": "vinc.spec.gamma"}}

    def plan(q, r):
        return ContextPlan(items=(*(ContextItem(read) for read, _ in r.required),
                                  ContextItem("vinc.spec.gamma", form="spans", spans=((0, 5),))))
    got = compile_(make_handler(docs=docs), request(anchors=("vinc.spec.gamma",)), Policy(plan))
    assert "Reference document (selected character ranges of the canonical document): vinc.spec.gamma" in got.block
    assert "read the rest" not in got.block


def test_p0_matches_prefetch_when_a_role_declares_one_document_under_both_spellings():
    handler = make_handler(reads=("vinc.spec.alpha", "concept:doc.vinc.spec.alpha",
                                  "concept:doc.vinc.spec.beta"))
    expected = client(handler, concurrency=1).prefetch(ROLE, "Review the policy.", space=SPACE)
    assert compile_(handler, request()).block == expected
