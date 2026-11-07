from __future__ import annotations

import datetime as dt
import logging

import httpx
import pytest

from conftest import SK_KEY, Recorder, ok
from vinc_client import (HARNESS_CONCEPT, MECHANISMS, Shim, VincClient, attached_shims,
                         build_run_fragment, build_shim_fragment, current_shims, read_runs,
                         retirement_drafts, shim_lines)

ROLE = "concept:agent-reviewer"
OTHER = "concept:agent-writer"
INCIDENT = "record:review-claimed-green-tests-without-a-run"


def shim_node(slug, *, mechanism="prompt_line", applies_to=(), valid_from="2026-09-01",
              valid_until=None, text="Say which tests you ran.", **props):
    return {"id": "record:shim-" + slug, "title": "Shim " + slug, "domain": "vinc/agent/harness",
            "valid_from": valid_from, "valid_until": valid_until,
            "props": {"patches": "claims tests passed", "mechanism": mechanism,
                      "applies_to": list(applies_to), "installed_because": INCIDENT,
                      **({"text": text} if text else {}), **props}}


class FakeClient:
    """``edges``·``node``·``recall`` 만 가진 덕 타입 클라이언트."""

    def __init__(self, nodes=(), edges=(), missing=(), truncated=False, pages=()):
        self.nodes = {n["id"]: n for n in nodes}
        self.edge_rows = list(edges)
        self.missing = set(missing)
        self.truncated = truncated
        self.pages = list(pages)
        self.calls = []

    def edges(self, *, nodes, type, dir, limit, space=None):
        self.calls.append(("edges", tuple(nodes), type, dir, limit))
        return {"edges": [e for e in self.edge_rows if e["to"] in nodes],
                "missing_nodes": [n for n in nodes if n in self.missing],
                "edges_truncated": self.truncated}

    def node(self, node_id, *, space=None):
        self.calls.append(("node", node_id))
        found = node_id in self.nodes
        return {"found": found, "node": self.nodes.get(node_id)}

    def recall(self, *, tags, limit, cursor=None, space=None):
        self.calls.append(("recall", tuple(tags), limit, cursor))
        index = int(cursor or 0)
        page = self.pages[index]
        more = index + 1 < len(self.pages)
        return {"rows": page, "has_more": more, "next_cursor": str(index + 1) if more else None}


def attach(source, target):
    return {"from": source, "to": target, "type": "ATTACHES_TO", "subtype": "relates_to"}


def test_the_fragment_is_one_record_related_to_its_target():
    fragment = build_shim_fragment(
        "say-tests-run", target=ROLE, patches="claims tests passed without running them",
        mechanism="prompt_line", applies_to=["claude", "claude"], installed_because=INCIDENT,
        measured_by="task-family:evidence", text="  Say which tests you ran.  ",
        valid_from="2026-10-01")
    (node,), (edge,) = fragment["nodes"], fragment["edges"]
    assert node["id"] == "record:shim-say-tests-run" and node["type"] == "record"
    assert node["valid_from"] == "2026-10-01" and node["tags"] == ["agent-shim"]
    assert node["props"] == {"patches": "claims tests passed without running them",
                             "mechanism": "prompt_line", "applies_to": ["claude"],
                             "installed_because": INCIDENT,
                             "measured_by": "task-family:evidence",
                             "text": "Say which tests you ran."}
    assert edge["from"] == node["id"] and edge["to"] == ROLE and edge["type"] == "relates_to"
    assert INCIDENT in edge["evidence"]
    # 설치일 기본값은 오늘이다.
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    gate = build_shim_fragment("block-merge", target=HARNESS_CONCEPT, patches="merges",
                               mechanism="gate", installed_because=INCIDENT)
    assert gate["nodes"][0]["valid_from"] == today and "text" not in gate["nodes"][0]["props"]


@pytest.mark.parametrize("change", [
    {"slug": "Bad Slug"}, {"slug": "x" * 81}, {"target": "record:x"}, {"target": "concept:"},
    {"mechanism": "banner"}, {"patches": " "}, {"installed_because": "episode:x"},
    {"text": None}, {"valid_from": "2026-10"}, {"applies_to": [""]},
])
def test_a_malformed_shim_is_refused(change):
    args = {"slug": "nudge", "target": ROLE, "patches": "stops early", "mechanism": "nudge",
            "installed_because": INCIDENT, "text": "The user is waiting."}
    with pytest.raises(ValueError):
        build_shim_fragment(**{**args, **change})


def test_validity_and_family_decide_which_shims_apply():
    shim = Shim.from_node(shim_node("a", applies_to=["Claude", "fast"], valid_from="2026-10",
                                    valid_until="2026-11-01"), [ROLE])
    assert shim.valid_from == "2026-10-01" and shim.attached_to == (ROLE,)
    assert not shim.is_current("2026-09-30")
    assert shim.is_current("2026-10-01") and shim.is_current(dt.date(2026, 10, 31))
    assert not shim.is_current("2026-11-01")          # 반열린 구간
    assert shim.families() == ("claude",)
    assert shim.applies("claude") and shim.applies(None, "fast") and shim.applies()
    assert not shim.applies("gpt")
    assert Shim.from_node(shim_node("b"), [ROLE]).applies("gpt")
    for bad in ({**shim_node("c"), "id": "record:other"},
                {**shim_node("d"), "props": {"mechanism": "gate"}},
                shim_node("e", mechanism="banner")):
        with pytest.raises(ValueError):
            Shim.from_node(bad)


def test_current_shims_reads_the_role_and_the_harness(caplog):
    nodes = [shim_node("for-claude", applies_to=["claude"]), shim_node("for-all"),
             shim_node("harness-wide", mechanism="nudge", text="The user is waiting."),
             shim_node("retired", valid_until="2026-10-01"),
             shim_node("later", valid_from="2026-12-01"),
             {**shim_node("broken"), "props": {"mechanism": "prompt_line"}}]
    edges = [attach(n["id"], ROLE) for n in nodes if "harness" not in n["id"]]
    edges += [attach("record:shim-harness-wide", HARNESS_CONCEPT),
              attach("record:lesson-not-a-shim", ROLE)]
    client = FakeClient(nodes, edges, missing=[HARNESS_CONCEPT])
    with caplog.at_level(logging.WARNING):
        got = current_shims(client, ROLE, model_family="gpt", today="2026-10-25")
    assert [s.id for s in got] == ["record:shim-for-all", "record:shim-harness-wide"]
    assert "record:shim-broken" in caplog.text
    assert client.calls[0] == ("edges", (ROLE, HARNESS_CONCEPT), "ATTACHES_TO", "in", 5000)
    assert ("node", "record:lesson-not-a-shim") not in client.calls
    every = current_shims(client, ROLE, today="2026-10-25")
    assert [s.id for s in every] == ["record:shim-for-all", "record:shim-for-claude",
                                     "record:shim-harness-wide"]
    assert shim_lines(every) == ["Say which tests you ran.", "The user is waiting."]
    assert shim_lines(every, mechanisms=["nudge"]) == ["The user is waiting."]
    assert shim_lines(every, mechanisms=["gate"]) == []
    with pytest.raises(ValueError):
        shim_lines(every, mechanisms=["banner"])
    assert set(MECHANISMS) == {"prompt_line", "nudge", "tool_filter", "gate"}


def test_a_missing_role_or_a_cut_read_raises_instead_of_an_empty_list():
    with pytest.raises(LookupError):
        current_shims(FakeClient(missing=[ROLE]), ROLE)
    with pytest.raises(RuntimeError):
        current_shims(FakeClient(truncated=True), ROLE)
    with pytest.raises(ValueError):
        current_shims(FakeClient(), "agent-reviewer")


def test_attached_shims_reads_targets_in_batches_of_one_hundred():
    roles = [f"concept:agent-r{i}" for i in range(150)]
    client = FakeClient([shim_node("x")], [attach("record:shim-x", roles[120])])
    got = attached_shims(client, roles, today="2026-10-25")
    assert [s.attached_to for s in got] == [(roles[120],)]
    assert [len(c[1]) for c in client.calls if c[0] == "edges"] == [100, 50]


def test_the_real_client_reads_through_the_v1_routes():
    recorder = Recorder({
        ("POST", "/v1/edges/query"): ok({"edges": [attach("record:shim-x", ROLE)],
                                         "missing_nodes": [HARNESS_CONCEPT]}),
        ("GET", "/v1/nodes/record:shim-x"): ok({"found": True, "node": shim_node("x")}),
    })
    with VincClient(SK_KEY, transport=httpx.MockTransport(recorder)) as vinc:
        got = current_shims(vinc, ROLE, today="2026-10-25")
    assert [s.id for s in got] == ["record:shim-x"]
    assert recorder.body(0) == {"nodes": [ROLE, HARNESS_CONCEPT], "type": "ATTACHES_TO",
                                "dir": "in", "limit": 5000}


def run_node(run_id, *, role=ROLE, family="claude", date="2026-10-20", fired=None):
    fragment = build_run_fragment(run_id, role=role, outcome="success", model_family=family,
                                  shims_fired=fired, date=date)
    node = fragment["nodes"][0]
    return {"id": node["id"], "date": node["date"], "props": node["props"]}


def test_read_runs_pages_until_the_window_start_and_reads_each_run():
    runs = [run_node("r1", date="2026-10-24"), run_node("r2", date="2026-10-10"),
            run_node("r3", date="2026-09-01")]
    rows = [{"id": r["id"], "date": r["date"]} for r in runs]
    pages = [[rows[0], {"id": "episode:2026-10-23-merged", "date": "2026-10-23"}],
             [rows[1], rows[2], {"id": "episode:undated"}], [{"id": "never-read"}]]
    client = FakeClient(nodes=runs, pages=pages)
    got = read_runs(client, since="2026-10-01")
    assert [r["props"]["run_id"] for r in got] == ["r1", "r2"]
    assert [c for c in client.calls if c[0] == "recall"] == [
        ("recall", ("agent-run",), 200, None), ("recall", ("agent-run",), 200, "1")]
    assert ("node", "episode:2026-10-23-merged") not in client.calls
    with pytest.raises(ValueError):
        read_runs(client, since="soon")
    with pytest.raises(ValueError):
        read_runs(client, since="2026-10-01", page_size=500)


def shims_for(*nodes_and_targets):
    return [Shim.from_node(node, targets) for node, targets in nodes_and_targets]


def test_zero_firings_on_every_family_draft_a_retirement():
    shims = shims_for((shim_node("quiet"), [ROLE]), (shim_node("busy"), [ROLE]))
    runs = ([run_node(f"c{i}", fired={"record:shim-busy": 1} if i == 0 else None)
             for i in range(3)]
            + [run_node(f"g{i}", family="gpt") for i in range(2)]
            + [run_node("other-role", role=OTHER, fired={"record:shim-quiet": 4})])
    drafts = retirement_drafts(shims, runs, window_start="2026-10-01", min_runs=2,
                               families_in_use=["Claude", "gpt"])
    assert len(drafts) == 1
    (node,), (edge,) = drafts[0]["nodes"], drafts[0]["edges"]
    assert node["id"] == "decision:retire-shim-quiet" and node["type"] == "decision"
    props = node["props"]
    assert props["state"] == "draft" and props["shim"] == "record:shim-quiet"
    assert props["counts"] == {"claude": {"runs": 3, "firings": 0},
                               "gpt": {"runs": 2, "firings": 0}}
    assert props["window_start"] == "2026-10-01" and props["min_runs"] == 2
    assert "ablation" in props["ratify_when"]
    assert node["domain"] == "vinc/agent/harness" and node["title"].startswith("Draft:")
    assert edge == {"from": node["id"], "to": ROLE, "type": "about",
                    "evidence": edge["evidence"]}
    assert "0 times in 5 runs (claude 3, gpt 2)" in edge["evidence"]


def test_a_family_short_of_runs_or_a_firing_blocks_the_draft():
    shims = shims_for((shim_node("quiet"), [ROLE]))
    runs = [run_node(f"c{i}") for i in range(3)] + [run_node("g0", family="gpt")]
    assert retirement_drafts(shims, runs, window_start="2026-10-01", min_runs=2,
                             families_in_use=["claude", "gpt"]) == []
    # 슬러그로 보고한 발동도 센다.
    fired = runs[:3] + [run_node("c9", fired={"shim-quiet": 1})]
    assert retirement_drafts(shims, fired, window_start="2026-10-01", min_runs=2,
                             families_in_use=["claude"]) == []
    # 창 시작 전의 실행과 설치 전의 실행은 세지 않는다.
    late = shims_for((shim_node("quiet", valid_from="2026-10-15"), [ROLE]))
    early = [run_node(f"e{i}", date="2026-10-05") for i in range(5)] + [run_node("in")]
    assert retirement_drafts(late, early, window_start="2026-10-01", min_runs=2,
                             families_in_use=["claude"]) == []
    drafts = retirement_drafts(late, early + [run_node("in2")], window_start="2026-10-01",
                               min_runs=2, families_in_use=["claude"])
    assert drafts[0]["nodes"][0]["props"]["window_start"] == "2026-10-15"


def test_harness_and_family_limited_shims_count_the_right_runs():
    shims = shims_for((shim_node("everyone"), [HARNESS_CONCEPT]),
                      (shim_node("claude-only", applies_to=["claude", "fast"]), [ROLE]),
                      (shim_node("unused-family", applies_to=["gemini"]), [ROLE]))
    runs = ([run_node("a"), run_node("b", role=OTHER)]
            + [run_node(f"g{i}", family="gpt", role=OTHER) for i in range(2)])
    drafts = retirement_drafts(shims, runs, window_start="2026-10-01", min_runs=1,
                               families_in_use=["claude", "gpt"])
    by_id = {d["nodes"][0]["props"]["shim"]: d for d in drafts}
    assert set(by_id) == {"record:shim-everyone", "record:shim-claude-only"}
    assert by_id["record:shim-everyone"]["nodes"][0]["props"]["counts"] == {
        "claude": {"runs": 2, "firings": 0}, "gpt": {"runs": 2, "firings": 0}}
    assert by_id["record:shim-everyone"]["edges"][0]["to"] == HARNESS_CONCEPT
    assert by_id["record:shim-claude-only"]["nodes"][0]["props"]["counts"] == {
        "claude": {"runs": 1, "firings": 0}}


@pytest.mark.parametrize("change", [{"window_start": "later"}, {"min_runs": 0},
                                    {"min_runs": True}, {"families_in_use": []}])
def test_the_watcher_refuses_an_unusable_window(change):
    args = {"window_start": "2026-10-01", "min_runs": 1, "families_in_use": ["claude"]}
    with pytest.raises(ValueError):
        retirement_drafts([], [], **{**args, **change})
