"""실행 기록 목록(api/runs.py): 인용한 주장으로 거르기, 주장의 유효성, 소유자 격리.

근거: [[vinc.plan.mcp-events-and-webhooks]]
"""
from __future__ import annotations

import os

import pytest

from api import docstore, fragment as frag, runs
from store.backend import open_store
from store.config import load_config

OWNER = "user:runs-owner"
OTHER = "user:runs-other"
ROLE = "concept:agent-runs-watcher"
ROLE_B = "concept:agent-runs-other"


@pytest.fixture()
def store(tmp_path):
    keys = ("MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(MEMORY_STORE_BACKEND="embedded", MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path / "db")})
    st = open_store(cfg)
    st.provision(dim=cfg.embedding_dim)
    try:
        yield st
    finally:
        st.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def ingest(store, nodes, edges=(), owner=OWNER):
    res = frag.merge_fragment({"owner": owner, "nodes": list(nodes), "edges": list(edges)},
                              store=store)
    assert res.get("executed") is not False, res
    return res


def run_node(slug, role, cited, date, outcome="success"):
    return {"id": f"episode:run-{slug}-12345678", "type": "episode", "title": f"Run {slug}",
            "domain": "agent", "date": date,
            "props": {"run_id": slug, "role": role, "outcome": outcome, "cited_ids": cited}}


def listing(store, **kw):
    out = runs.list_runs(store, owner=kw.pop("owner", OWNER), **kw)
    assert out["executed"] is True, out
    return out


def test_parse_cited_and_limit():
    assert runs.parse_cited(None) is None and runs.parse_cited("  ") is None
    assert runs.parse_cited(" record:x ") == "record:x"
    with pytest.raises(ValueError):
        runs.parse_cited("no-prefix")
    assert runs.clamp_limit(None) == runs.DEFAULT_LIMIT
    for bad in (0, 201, "x"):
        with pytest.raises(ValueError):
            runs.clamp_limit(bad)


def test_cited_filter_lists_matching_runs_newest_first_with_claim_status(store):
    ingest(store, [
        {"id": "record:claim-x", "type": "record", "title": "X holds", "domain": "agent"},
        run_node("a", ROLE, ["record:claim-x", "record:other"], "2026-10-20"),
        run_node("b", ROLE_B, ["record:claim-x"], "2026-10-24", outcome="failure"),
        run_node("c", ROLE, ["record:other"], "2026-10-25"),
    ])
    live = listing(store, cited="record:claim-x")
    assert [r["run_id"] for r in live["runs"]] == ["b", "a"]
    assert all(r["matched"] == ["record:claim-x"] for r in live["runs"])
    assert live["claim"] == {"id": "record:claim-x", "found": True, "retired": False,
                             "valid_until": "", "superseded_by": ""}
    ingest(store, [{"id": "record:claim-x", "type": "record", "valid_until": "2026-10-25"}])
    retired = listing(store, cited="record:claim-x")
    assert retired["claim"]["retired"] is True and retired["claim"]["valid_until"] == "2026-10-25"
    assert [r["run_id"] for r in retired["runs"]] == ["b", "a"]


def test_decision_successor_and_document_aliases(store):
    ingest(store, [
        {"id": "decision:choice-y", "type": "decision", "title": "Choose Y", "domain": "agent"},
        run_node("d", ROLE, ["decision:choice-y", "concept:doc.plan.b"], "2026-10-21"),
        run_node("e", ROLE, ["doc:plan.b"], "2026-10-22"),
    ])
    ingest(store, [{"id": "decision:choice-z", "type": "decision", "title": "Choose Z",
                    "domain": "agent"}],
           [{"from": "decision:choice-z", "to": "decision:choice-y", "type": "supersedes",
             "evidence": "Z replaces Y"}])
    decision = listing(store, cited="decision:choice-y")
    assert [r["run_id"] for r in decision["runs"]] == ["d"]
    assert decision["claim"]["superseded_by"] == "decision:choice-z"
    docstore.put_doc(store, owner=OWNER, text="# B\n\nplan b\n", doc_id="plan.b",
                     valid_until="2026-10-25")
    for alias in ("doc:plan.b", "concept:doc.plan.b"):
        doc = listing(store, cited=alias)
        assert [r["run_id"] for r in doc["runs"]] == ["e", "d"], alias
        assert doc["claim"]["retired"] is True and doc["claim"]["found"] is True


def test_unknown_claim_is_not_found_and_lists_nothing(store):
    ingest(store, [run_node("a", ROLE, ["record:claim-x"], "2026-10-20")])
    out = listing(store, cited="record:never-written")
    assert out["runs"] == [] and out["claim"]["found"] is False and out["claim"]["retired"] is False


def test_role_filter_limit_and_truncation(store):
    ingest(store, [run_node(f"r{i}", ROLE if i % 2 else ROLE_B, [], f"2026-10-0{i + 1}")
                   for i in range(6)])
    both = listing(store)
    assert both["total"] == 6 and len(both["runs"]) == 6 and both["truncated"] is False
    only = listing(store, role="agent-runs-watcher")
    assert {r["role"] for r in only["runs"]} == {ROLE} and only["total"] == 3
    page = listing(store, limit=2)
    assert len(page["runs"]) == 2 and page["total"] == 6 and page["truncated"] is True
    assert [r["run_id"] for r in page["runs"]] == ["r5", "r4"]


def test_other_owner_runs_and_claims_stay_invisible(store):
    ingest(store, [{"id": "record:claim-x", "type": "record", "title": "X", "domain": "agent"},
                   run_node("mine", ROLE, ["record:claim-x"], "2026-10-20")])
    ingest(store, [{"id": "record:claim-x", "type": "record", "title": "X", "domain": "agent",
                    "valid_until": "2026-10-01"},
                   run_node("theirs", ROLE, ["record:claim-x"], "2026-10-21")], owner=OTHER)
    mine = listing(store, cited="record:claim-x")
    assert [r["run_id"] for r in mine["runs"]] == ["mine"]
    assert mine["claim"]["retired"] is False
    assert [r["run_id"] for r in listing(store, owner=OTHER, cited="record:claim-x")["runs"]] == ["theirs"]


def test_owner_is_required(store):
    assert runs.list_runs(store, owner="")["executed"] is False


def test_one_run_by_episode_id_or_run_id_carries_its_props(store):
    ingest(store, [run_node("solo", ROLE, ["record:claim-x"], "2026-10-20"),
                   run_node("other", ROLE, [], "2026-10-21")])
    by_episode = listing(store, run="episode:run-solo-12345678")
    by_run_id = listing(store, run="solo")
    assert [r["run_id"] for r in by_episode["runs"]] == [r["run_id"] for r in by_run_id["runs"]] == ["solo"]
    assert by_run_id["runs"][0]["props"]["outcome"] == "success"
    assert "props" not in listing(store)["runs"][0]
    assert listing(store, run="never")["runs"] == []


def test_flagged_keeps_the_runs_that_used_a_capability_their_role_did_not_allow(store):
    flag = {"id": "github.pr.merge", "flag": "unapproved", "step": 1}
    clean = run_node("clean", ROLE, [], "2026-10-29")
    flagged = run_node("flagged", ROLE, [], "2026-10-29")
    flagged["props"]["capability_flags"] = [flag]
    ingest(store, [clean, flagged])
    every = listing(store)
    assert {r["run_id"]: r["capability_flags"] for r in every["runs"]} == {"clean": [], "flagged": [flag]}
    only = listing(store, flagged=True)
    assert [r["run_id"] for r in only["runs"]] == ["flagged"] and only["total"] == 1
    assert runs.parse_flagged("true") is True and runs.parse_flagged(None) is False
    with pytest.raises(ValueError, match="invalid_flagged"):
        runs.parse_flagged("yes")
