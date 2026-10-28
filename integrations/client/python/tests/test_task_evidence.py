"""기본 정책: 문서 읽기 도구가 있으면 과제 근거 정책, 없으면 P0. 과제 근거 정책이 무엇을 싣는가."""
from __future__ import annotations

import asyncio
import hashlib
from urllib.parse import unquote_plus

import httpx
import pytest
from vinc_client import (FULL_TEXT, TASK_EVIDENCE, AsyncVincClient, ContextRequest, VincClient,
                         acompile_context, check_replay, compile_context, default_policy,
                         role_prefetch)
from vinc_client.doc_tool import doc_tool_allowed

KEY = "vinc_ro_" + "a" * 32
ROLE = "concept:agent-reviewer"
TEXT = "# Alpha\nAlpha rules.\n\n## Release\n" + "r" * 3000 + "\n\n## Deploy\n" + \
    "The deploy waits for the health check before it moves traffic to the new revision.\n" + "d" * 3000
TEXTS = {"vinc.spec.alpha": "Alpha rules.\n" + "a" * 900, "vinc.ops.deploy": TEXT}
NODES = {
    "decision:health-first": {"id": "decision:health-first", "kind": "decision",
                              "title": "Health check before traffic", "domain": "vinc/ops"},
    "record:old-deploy": {"id": "record:old-deploy", "kind": "record",
                          "title": "Old deploy note", "domain": "vinc/ops"},
    "concept:agent-release": {"id": "concept:agent-release", "kind": "concept",
                              "title": "Release role", "domain": "agent/engineering"},
    "concept:doc.vinc.spec.alpha": {"id": "concept:doc.vinc.spec.alpha"},
    "concept:doc.vinc.ops.deploy": {"id": "concept:doc.vinc.ops.deploy"},
}
SNIPPET = "The deploy waits for the health check before it moves traffic to the new revision."


def _doc(doc_id):
    text = TEXTS[doc_id]
    return {"executed": True, "doc_id": doc_id, "title": doc_id, "verified": True, "text": text,
            "content_hash": hashlib.sha256(text.encode()).hexdigest(),
            "updated_at": "2026-10-20T00:00:00Z"}


def make_handler(seen=None, *, disallow_doc_tool=False):
    role = {"found": True, "id": ROLE, "domain": "agent/engineering", "rev": 3, "hash": "f" * 64,
            "props": {"reads": ["vinc.spec.alpha"], "mission": "Review evidence.", "never": ["merge"],
                      **({"disallowed_tools": ["vinc_doc_get"]} if disallow_doc_tool else {})}}

    def handler(request):
        path = request.url.path
        if seen is not None:
            seen.append((path, request.content.decode() or request.url.query.decode()))
        if path.startswith("/v1/roles/"):
            return httpx.Response(200, json=role)
        if path.startswith("/v1/documents/"):
            key = path.removeprefix("/v1/documents/")
            return httpx.Response(200, json=_doc(key)) if key in TEXTS else \
                httpx.Response(404, json={"error": {"code": "not_found", "message": ""}})
        if path.startswith("/v1/nodes/"):
            nid = path.removeprefix("/v1/nodes/")
            if nid not in NODES:
                return httpx.Response(404, json={"error": {"code": "not_found", "message": ""}})
            return httpx.Response(200, json={"executed": True, "found": True, "node": NODES[nid]})
        if path.endswith("brief"):
            return httpx.Response(200, json={"status": "tie", "candidates": [
                {"id": "concept:agent-release", "title": "Release role", "domain": "agent/engineering"},
                {"id": "record:old-deploy", "kind": "record", "title": "Old deploy note"}]})
        return httpx.Response(200, json={
            "node_hits": [{"id": "decision:health-first", "kind": "decision",
                           "title": "Health check before traffic"},
                          {"id": "record:old-deploy", "kind": "record", "title": "Old deploy note"}],
            "rows": [{"id": "chunk:1", "doc_id": "vinc.ops.deploy", "doc_title": "Deploy",
                      "snippet": SNIPPET}]})
    return handler


def client(handler, async_mode=False):
    cls = AsyncVincClient if async_mode else VincClient
    return cls(KEY, transport=httpx.MockTransport(handler))


def request(**kw):
    return ContextRequest(role=ROLE, task=kw.pop("task", "When does the deploy move traffic?"), **kw)


def test_the_default_follows_the_read_tool():
    assert default_policy(True) is TASK_EVIDENCE and default_policy(False) is FULL_TEXT
    without = compile_context(client(make_handler()), request())
    assert without.manifest["policy"]["id"] == "p0-full"
    assert without.block == compile_context(client(make_handler()), request(), FULL_TEXT).block
    with_tool = compile_context(client(make_handler()), request(read_tool=True))
    assert with_tool.manifest["policy"]["id"] == "task-evidence-1"


def test_a_role_that_disallows_the_document_tool_keeps_p0():
    got = role_prefetch(client(make_handler(disallow_doc_tool=True)), ROLE, "When does the deploy move traffic?",
                        read_tool=doc_tool_allowed)
    assert "Required reading (not included" not in got.block
    allowed = role_prefetch(client(make_handler()), ROLE, "When does the deploy move traffic?",
                            read_tool=doc_tool_allowed)
    assert "Required reading (not included; read it with vinc_doc_get" in allowed.block


@pytest.mark.parametrize("async_mode", [False, True])
def test_task_evidence_points_at_readings_and_shows_what_the_task_reads_agree_on(async_mode):
    c = client(make_handler(), async_mode)
    req = request(read_tool=True)
    got = asyncio.run(acompile_context(c, req)) if async_mode else compile_context(c, req)
    items = {i["source"]: i for i in got.manifest["items"]}
    assert items["vinc.spec.alpha"]["form"] == "pointer"
    # 두 목록에 다 나온 레코드가 먼저, 역할 자신의 토픽에 있는 역할 카드는 빠진다
    nodes = [i["source"] for i in got.manifest["items"] if i["kind"] == "node"]
    assert nodes == ["record:old-deploy", "decision:health-first"]
    assert "concept:agent-release" not in items
    # 검색 행이 가리킨 문서는 일치한 단락 둘레만 싣는다
    assert items["vinc.ops.deploy"]["form"] == "spans"
    assert SNIPPET in got.block and "r" * 3000 not in got.block
    assert len(got.block) < len(compile_context(client(make_handler()), request()).block) + len(TEXT)


def test_task_evidence_replays_from_its_own_reads():
    got = compile_context(client(make_handler()), request(read_tool=True))
    check_replay(got, request(read_tool=True))


def _queries(seen):
    # brief·search 요청의 질의 부분(본문이든 쿼리 문자열이든)을 풀어서 모은다
    return [unquote_plus(body) for path, body in seen if path.endswith(("brief", "search"))]


def test_a_task_in_another_script_also_asks_with_its_latin_terms():
    seen: list = []
    compile_context(client(make_handler(seen)), request(read_tool=True, task="배포할 때 health check 는 언제 봐?"))
    asked = " ".join(_queries(seen))
    assert asked.count("health check") >= 4   # 과제 글 그대로 brief·search, 라틴 낱말만으로 다시 brief·search
    seen.clear()
    compile_context(client(make_handler(seen)), request(read_tool=True, task="When does the health check run?"))
    assert " ".join(_queries(seen)).count("health check") == 2
