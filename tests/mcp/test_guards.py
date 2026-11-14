# -*- coding: utf-8 -*-
"""`vinc_mcp/guards.py`: 두 입구(`tools.call`, `proxy_ops.proxy_op`)가 같이 쓰는 가드 둘의 표.

권한 인자(owner·theme_id·debug·_entitlements)를 벗기는 함수와 실행 기록의 상태를 고치려는 노드를 찾는
함수를, 함수 하나씩 한 번, 그리고 그것을 쓰는 두 입구로 한 번 더 표로 단정한다. 입구 표는 도구를
기록용으로 바꿔 끼워 도구가 받은 인자를 있는 그대로(순서까지) 본다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from store import run_state  # noqa: E402
from vinc_mcp import guards, proxy_ops, tools  # noqa: E402

FORGED = {"owner": "user:forged", "theme_id": "theme:forged", "debug": True,
          "_entitlements": {"topic_cap": 99}}


# ── strip_client_authority ───────────────────────────────────────────────────

@pytest.mark.parametrize("args, decided, expected", [
    ({}, (), {}),
    (FORGED, (), {}),
    ({"q": "x", **FORGED, "limit": 3}, (), {"q": "x", "limit": 3}),
    # 서버가 이미 덮어쓴 이름은 남는다. 나머지는 벗는다.
    (FORGED, ("owner", "theme_id"), {"owner": "user:forged", "theme_id": "theme:forged"}),
    (FORGED, ("owner", "theme_id", "debug"),
     {"owner": "user:forged", "theme_id": "theme:forged", "debug": True}),
    ({"debug": None, "_entitlements": None, "owner": None, "theme_id": None}, (), {}),
    # 권한 인자가 아닌 이름은 `decided` 에 있든 없든 그대로다.
    ({"q": "x"}, ("q",), {"q": "x"}),
    ({"nodes": [{"id": "concept:a"}], "owner": "o"}, (), {"nodes": [{"id": "concept:a"}]}),
])
def test_strip_removes_the_authority_names_and_keeps_the_rest_in_order(args, decided, expected):
    given = dict(args)
    out = guards.strip_client_authority(given, decided=decided)
    assert list(out.items()) == list(expected.items())
    assert given == args, "the caller's dict must stay as it was"


def test_the_authority_names_are_these_four():
    assert guards.CLIENT_AUTHORITY == ("owner", "theme_id", "debug", "_entitlements")


# ── run_state_writes ─────────────────────────────────────────────────────────

RUN = "episode:run-x"
NODE_TABLE = [
    (None, []), ("x", []), (5, []), ({}, []), ([], []), ([None], []), ([5], []), ([{}], []),
    ([{"id": 7}], []), ([{"id": RUN}], []),
    ([{"id": RUN, "props": {"state": "waiting"}}], [RUN]),
    ([{"id": RUN, "props": {"approved": ["merge"]}}], [RUN]),
    ([{"id": RUN, "props": {"answer": "approve"}}], [RUN]),
    ([{"id": RUN, "props": {"task": "t"}}], []),
    ([{"id": RUN, "props": {"role": "concept:r"}}], []),
    ([{"id": RUN, "props_patch": {"state": "done"}}], [RUN]),
    ([{"id": RUN, "props_patch": {"role": "concept:r"}}], [RUN]),
    ([{"id": RUN, "props_patch": {"role_hash": "h"}}], [RUN]),
    ([{"id": RUN, "props_patch": {"verdict": "fail"}}], []),
    ([{"id": RUN, "props": '{"approved": ["merge"]}'}], [RUN]),
    ([{"id": RUN, "props": "not json"}], []),
    ([{"id": " episode:run-x", "props": {"state": "x"}}], [RUN]),
    ([{"id": " episode:run-x", "props": {"state": "x"}}], [RUN]),
    ([{"id": "Episode:run-x", "props": {"state": "x"}}], []),
    ([{"id": "episode:RUN-x", "props": {"state": "x"}}], []),
    ([{"id": "concept:run-x", "props": {"state": "x"}}], []),
    ([{"id": "episode:run-a", "props": {"state": "x"}},
      {"id": "episode:run-b", "props_patch": {"answer": "y"}},
      {"id": "concept:c", "props": {"state": "x"}},
      {"id": "episode:run-a", "props": {"approved": []}}],
     ["episode:run-a", "episode:run-b", "episode:run-a"]),
]


@pytest.mark.parametrize("nodes, expected", NODE_TABLE)
def test_run_state_writes_finds_the_nodes_that_write_protocol_state(nodes, expected):
    assert guards.run_state_writes(nodes) == expected
    assert guards.run_state_writes(nodes) == run_state.writes(nodes), "one rule, in store/"


def test_every_protected_key_is_found_in_props_and_in_props_patch():
    for key in run_state.STATE_KEYS:
        assert guards.run_state_writes([{"id": RUN, "props": {key: 1}}]) == [RUN], key
        assert guards.run_state_writes([{"id": RUN, "props_patch": {key: 1}}]) == [RUN], key
    for key in run_state.ROLE_KEYS:
        assert guards.run_state_writes([{"id": RUN, "props_patch": {key: 1}}]) == [RUN], key


# ── tools.call ───────────────────────────────────────────────────────────────

class _Recorder:
    """도구의 `run` 자리에 끼워 도구가 받은 인자를 순서까지 있는 그대로 남긴다."""

    def __init__(self, monkeypatch, name: str):
        self.seen: list[list] = []
        monkeypatch.setitem(tools._BY_NAME[name], "run",
                            lambda a, b: self.seen.append(list(a.items())) or {"executed": True})


class _Bridge:
    owner = "tenant:local"

    def __init__(self):
        self.calls = []

    def request(self, method, path, params=None, body=None, **kw):
        self.calls.append((method, path, params, body))
        return {"executed": True}


THEME = tools.TOOLS[[t["name"] for t in tools.TOOLS].index("vinc_ingest")]["inputSchema"]["properties"]
assert "theme_id" in THEME and "theme_id" not in tools._BY_NAME["vinc_search"]["inputSchema"]["properties"]


def _seen(monkeypatch, name, args, **ctx):
    rec = _Recorder(monkeypatch, name)
    tools.call(name, dict(args), _Bridge(), hub=True, **ctx)
    assert len(rec.seen) == 1
    return rec.seen[0]


def test_an_authenticated_call_overwrites_owner_and_theme_and_drops_debug_and_entitlements(
        monkeypatch):
    from store.theme_store import owner_theme_id
    seen = _seen(monkeypatch, "vinc_ingest", {"nodes": [], **FORGED}, forced_owner="user:u-1")
    # owner 와 theme_id 는 클라이언트가 보낸 자리에서 서버의 값으로 바뀌고, debug 와 자격은 없다.
    assert seen == [("nodes", []), ("owner", "user:u-1"), ("theme_id", owner_theme_id("user:u-1"))]


def test_an_authenticated_call_carries_the_resolved_entitlements_only_on_capped_tools(monkeypatch):
    ent = {"topic_cap": 5}
    capped = _seen(monkeypatch, "vinc_ingest", {"nodes": [], **FORGED}, forced_owner="user:u-1",
                   entitlements=ent)
    assert dict(capped)["_entitlements"] == ent and capped[-1][0] == "_entitlements"
    other = _seen(monkeypatch, "vinc_search", {"query": "q", **FORGED}, forced_owner="user:u-1",
                  entitlements=ent)
    assert "_entitlements" not in dict(other) and "debug" not in dict(other)


def test_a_tool_without_a_theme_argument_keeps_what_the_client_sent_in_it(monkeypatch):
    seen = _seen(monkeypatch, "vinc_search", {"query": "q", **FORGED}, forced_owner="user:u-1")
    assert seen == [("query", "q"), ("owner", "user:u-1"), ("theme_id", "theme:forged")]


def test_a_local_call_keeps_owner_theme_and_debug_but_never_the_entitlements(monkeypatch):
    seen = _seen(monkeypatch, "vinc_ingest", {"nodes": [], **FORGED})
    assert seen == [("nodes", []), ("owner", "user:forged"), ("theme_id", "theme:forged"),
                    ("debug", True)]
    seen = _seen(monkeypatch, "vinc_ingest", {"nodes": [], **FORGED}, entitlements={"topic_cap": 5})
    assert seen[-1] == ("_entitlements", {"topic_cap": 5})


def test_the_callers_args_stay_as_they_were(monkeypatch):
    args = {"query": "q", **FORGED}
    snapshot = dict(args)
    _Recorder(monkeypatch, "vinc_search")
    tools.call("vinc_search", args, _Bridge(), forced_owner="user:u-1", hub=True)
    assert args == snapshot


# ── proxy_ops.proxy_op ───────────────────────────────────────────────────────

class _App:
    def __init__(self):
        self.bridge = _Bridge()

    def entitlements_for(self, plan):
        return {"topic_cap": 5, "plan": plan}


def _proxied(op, args, **ctx):
    app = _App()
    proxy_ops.proxy_op(app, op, dict(args), "user:u-1", actor="user:u-1", **ctx)
    return app.bridge.calls


def test_the_web_plane_drops_all_four_and_stamps_its_own_owner_and_entitlements():
    calls = _proxied("ingest-fragment", {"nodes": [], **FORGED}, plan="pro")
    assert calls == [("POST", "/ingest/fragment", None,
                      {"nodes": [], "owner": "user:u-1",
                       "_entitlements": {"topic_cap": 5, "plan": "pro"}})]
    assert list(calls[0][3]) == ["nodes", "owner", "_entitlements"]


def test_the_web_plane_sends_no_entitlements_without_a_plan_claim_and_none_on_reads():
    assert _proxied("ingest-fragment", {"nodes": [], **FORGED}) == [
        ("POST", "/ingest/fragment", None, {"nodes": [], "owner": "user:u-1"})]
    assert _proxied("search", {"q": "x", **FORGED}, plan="pro") == [
        ("GET", "/search", {"q": "x", "owner": "user:u-1"}, None)]


@pytest.mark.parametrize("op", sorted(proxy_ops._PROXY_OPS))
def test_no_proxy_op_forwards_a_client_debug_theme_or_entitlements(op):
    if op in ("run-answer", "job"):
        pytest.skip("answered or fetched by id without forwarding the client's arguments")
    args = {"id": "abc_123", **FORGED}
    for method, path, params, body in _proxied(op, args):
        sent = {**(params or {}), **(body or {})}
        assert sent.get("owner") == "user:u-1", (op, sent)
        assert "debug" not in sent and "theme_id" not in sent, (op, sent)
        assert sent.get("_entitlements") != FORGED["_entitlements"], (op, sent)


@pytest.mark.parametrize("op", ["ingest-fragment", "ingest-pack"])
@pytest.mark.parametrize("where", ["nodes", "pack"])
def test_the_web_plane_refuses_protocol_state_with_the_same_rule(op, where):
    node = {"id": RUN, "props": {"approved": ["merge"]}}
    args = {"nodes": [node]} if where == "nodes" else {"pack": {"fragment": {"nodes": [node]}}}
    app = _App()
    out = proxy_ops.proxy_op(app, op, args, "user:u-1", actor="user:u-1")
    assert out["executed"] is False and out["error"] == run_state.CODE and out["nodes"] == [RUN]
    assert app.bridge.calls == []


# ── 규칙은 이 모듈 한 곳에서만 쓴다 ───────────────────────────────────────────

@pytest.mark.parametrize("path", ["vinc_mcp/tools.py", "vinc_mcp/proxy_ops.py",
                                  "vinc_mcp/tool_handlers.py"])
def test_the_two_entrances_do_not_write_the_strip_or_the_detection_themselves(path):
    source = (ROOT / path).read_text(encoding="utf-8")
    assert not re.search(r'\bargs\.pop\(\s*"(debug|_entitlements|theme_id)"', source), path
    assert "run_state.writes(" not in source and "_run_state_writes" not in source, path
