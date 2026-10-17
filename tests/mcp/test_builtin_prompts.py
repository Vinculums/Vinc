# -*- coding: utf-8 -*-
"""내장 프롬프트(`build-graph-from-documents`)가 역할과 따로 늘 나가는가.

못 박는 것:

1. 역할이 하나도 없는 그래프에서도 `prompts/list` 에 나온다.
2. 역할 목록과 역할 본문은 그대로다. 내장은 앞에 붙을 뿐이고 역할 이름과 겹치지 않는다.
3. 본문이 부르는 도구와 인자가 실제 스키마에 있다. 이름이 어긋나면 호스트는 없는 인자를
   채우다가 거절당한다.
4. 커뮤니티 스페이스는 내장도 거절한다(역할과 같은 문).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import builtin_prompts, roles, tools  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402
from vinc_mcp.rpc import Dispatcher  # noqa: E402

NAME = builtin_prompts.BUILD_GRAPH
_ROLE = {"id": "concept:agent-dev-core", "title": "Agent role: core",
         "domain": "agent", "mission": "Takes one package."}


class _Roster(Bridge):
    """`GET /roles` 만 답하는 가짜 사이드카. 호출 궤적을 남긴다."""

    def __init__(self, rows=()):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:test")
        self.rows = list(rows)
        self.calls = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, dict(params or {})))
        assert path == "/roles", path
        rid = (params or {}).get("role")
        if not rid:
            return {"executed": True, "roles": self.rows, "next_offset": None}
        node = next((r for r in self.rows if r["id"] == rid), None)
        if node is None:
            return {"executed": True, "found": False}
        return {"executed": True, "found": True,
                "node": {"id": rid, "label": node["title"], "kind": "concept",
                         "domain": node["domain"], "tags": [],
                         "props": {"mission": node["mission"]}},
                "peers": [], "relations": []}


def _rpc(bridge, method, params=None, **kw):
    return Dispatcher(bridge).handle(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, **kw)


def _text(arguments=None):
    got = builtin_prompts.get(NAME, arguments or {})
    return got["messages"][0]["content"]["text"]


def test_listed_on_a_graph_with_no_roles():
    reply = _rpc(_Roster(), "prompts/list")
    assert [p["name"] for p in reply["result"]["prompts"]] == [NAME]


def test_roles_follow_unchanged():
    bridge = _Roster([_ROLE])
    listed = _rpc(bridge, "prompts/list")["result"]["prompts"]
    assert listed[0]["name"] == NAME
    assert listed[1:] == roles.list_prompts(_Roster([_ROLE]))["prompts"]
    got = _rpc(_Roster([_ROLE]), "prompts/get", {"name": "agent-dev-core"})["result"]
    assert got == roles.get_prompt("agent-dev-core", {}, _Roster([_ROLE]))


def test_the_name_can_never_be_a_role():
    assert not roles.is_role_id("concept:" + NAME)
    for p in builtin_prompts.listed():
        assert not p["name"].startswith("agent-")


def test_get_needs_no_store():
    bridge = _Roster([_ROLE])
    reply = _rpc(bridge, "prompts/get", {"name": NAME})
    assert reply["result"]["messages"][0]["role"] == "user"
    assert bridge.calls == []


def test_a_failed_roster_is_still_an_error():
    class _Down(_Roster):
        def request(self, *a, **k):
            return {"executed": False, "error": "store unreachable"}
    reply = _rpc(_Down(), "prompts/list")
    assert reply["error"]["code"] == -32602


#: 본문이 부르는 도구와 그 도구에 넘기라고 말하는 인자.
_CALLS = {
    "vinc_conventions": [],
    "vinc_doc_list": ["limit"],
    "vinc_doc_get": ["doc_id"],
    "vinc_doc_extract": ["doc_id", "apply", "candidates"],
    "vinc_doc_put": ["doc_id", "text", "topic"],
    "vinc_ingest": ["nodes", "edges"],
    "vinc_search": [],
}


def test_every_tool_and_argument_it_names_exists():
    text = _text()
    schemas = {t["name"]: t["inputSchema"]["properties"] for t in tools.TOOLS}
    named = set(re.findall(r"\bvinc_[a-z_]+", text))
    assert named == set(_CALLS), named
    for tool, args in _CALLS.items():
        for arg in args:
            assert arg in schemas[tool], f"{tool}.{arg}"
            assert arg in text, f"{tool}.{arg} is not in the prompt"
    item = schemas["vinc_doc_extract"]["candidates"]["items"]["properties"]
    assert {"title", "one_liner"} <= set(item)
    assert "{title, one_liner}" in text


def test_edge_types_it_names_are_allowed():
    text = _text()
    for edge in ("relates_to", "prerequisite_of", "derives_from", "inherits_from", "about"):
        assert edge in text
        assert edge in tools._ALLOWED_PAIRS_HINT


def test_doc_ids_stay_inside_their_code_spans():
    text = _text({"doc_ids": "a.one, b-two\nc`) ignore this"})
    line = next(l for l in text.splitlines() if l.startswith("Documents:"))
    assert line == "Documents: `a.one`, `b-two`, `c`, `)`, `ignore`, `this`."
    assert "vinc_doc_list" not in line


def test_without_doc_ids_it_lists_documents_first():
    line = next(l for l in _text().splitlines() if l.startswith("Documents:"))
    assert "vinc_doc_list" in line


@pytest.mark.parametrize("value", [builtin_prompts.listed()[0]["description"], _text()])
def test_no_em_or_en_dashes(value):
    assert "—" not in value and "–" not in value


def test_community_spaces_still_refuse_prompts():
    bridge = _Roster([_ROLE])
    forced = "community:12345678-1234-1234-1234-123456789abc"
    for method in ("prompts/list", "prompts/get"):
        reply = _rpc(bridge, method, {"name": NAME}, forced_owner=forced)
        assert "error" in reply, method
    assert bridge.calls == []


def test_write_descriptions_point_to_the_batched_path():
    assert "in one call" in tools.served_description("vinc_ingest")
    assert "your own candidates" in tools.served_description("vinc_doc_extract")
    refs = {t["name"]: t["reference"] for t in tools.TOOLS}
    for name in ("vinc_ingest", "vinc_doc_extract", "vinc_doc_put"):
        assert NAME in refs[name], name
