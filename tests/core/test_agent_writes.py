"""에이전트 쓰기 검토(api/agent_writes.py): 최근 창, 출처 묶기, 사람이 볼 검사, 소유자 격리.

근거: [[vinc.research.graph-pages-and-marks-2026-10-25]] §5
"""
from __future__ import annotations

import datetime
import os

import pytest

from api import agent_writes, fragment as frag
from store.backend import open_store
from store.config import load_config

OWNER = "user:writes-owner"
OTHER = "user:writes-other"


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
    res = frag.merge_fragment({"owner": owner, "nodes": list(nodes), "edges": list(edges)}, store=store)
    assert res.get("executed") is not False, res
    return res


def scan(store, **kw):
    out = agent_writes.scan(store, owner=kw.pop("owner", OWNER), **kw)
    assert out["executed"] is True, out
    return out


def test_days_are_clamped():
    assert agent_writes.clamp_days(None) == agent_writes.DEFAULT_DAYS
    assert agent_writes.clamp_days("7") == 7
    for bad in (0, agent_writes.MAX_DAYS + 1, "x"):
        with pytest.raises(ValueError):
            agent_writes.clamp_days(bad)


def test_checks_are_pure():
    assert agent_writes.shredded({"integration_status": list("a long field cut into letters")})
    assert not agent_writes.shredded({"tags": ["a", "b"], "note": "fine"})
    assert agent_writes.evidence_check("ABOUT", "", True) == "evidence_missing"
    assert agent_writes.evidence_check("RELATED_TO", "", False) is None
    assert agent_writes.evidence_check("ABOUT", "run:7f3a", True) == "evidence_is_an_id"
    assert agent_writes.evidence_check("ABOUT", "The owner chose it on 28 Oct.", True) is None


def test_recent_writes_group_by_run_evidence_and_flag_what_needs_a_person(store):
    ingest(store, [
        {"id": "concept:target", "type": "concept", "title": "Target", "domain": "vinc/design/view"},
        {"id": "decision:by-run", "type": "decision", "title": "By a run", "domain": "vinc/design/view",
         "props": {"ruling": "Chosen by an agent run."}},
        {"id": "decision:by-hand", "type": "decision", "title": "By hand", "domain": "vinc/design/view",
         "props": {"ruling": "Written by a person.", "valid_from": "2026-10-28"}},
        {"id": "record:shred", "type": "record", "title": "Shredded", "domain": "vinc/design/view",
         "props": {"integration_status": list("merged into main and deployed")}},
    ], [
        {"type": "about", "from": "decision:by-run", "to": "concept:target", "evidence": "run:openclaw-7f3a"},
        {"type": "about", "from": "decision:by-hand", "to": "concept:target",
         "evidence": "The owner chose this in the review on 28 Oct."},
    ])
    ingest(store, [{"id": "concept:theirs", "type": "concept", "title": "Not mine", "domain": "vinc/x"}],
           owner=OTHER)
    out = scan(store)
    by = {g["source"]["key"]: g for g in out["groups"]}
    assert set(by) == {"run:openclaw-7f3a", agent_writes.NO_SOURCE}
    run = by["run:openclaw-7f3a"]
    assert run["source"]["kind"] == "run"
    run_ids = {i["id"] for i in run["items"]}
    assert "decision:by-run" in run_ids and "concept:target" in run_ids
    rel = next(i for i in run["items"] if i["kind"] == "relation")
    assert rel["checks"] == ["evidence_is_an_id"]
    dec = next(i for i in run["items"] if i["id"] == "decision:by-run")
    assert dec["checks"] == ["decision_without_start"]
    rest = {i["id"]: i for i in by[agent_writes.NO_SOURCE]["items"]}
    assert rest["decision:by-hand"]["checks"] == []
    assert rest["record:shred"]["checks"] == ["props_shredded"]
    assert all(i["id"] != "concept:theirs" for g in out["groups"] for i in g["items"])
    assert out["groups"][0]["source"]["kind"] == "run", "sourced groups come before the unattributed one"


def test_the_window_leaves_older_writes_out(store):
    ingest(store, [{"id": "concept:old", "type": "concept", "title": "Old", "domain": "vinc/x"}])
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
    assert scan(store, days=14, now=later)["total"] == 0
