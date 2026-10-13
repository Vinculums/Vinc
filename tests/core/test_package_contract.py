"""Package claims survive exact reads; rejected evidence makes no authored write."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace, MethodType

import pytest

from api.fragment import merge_fragment
from api import query
from store.backend import open_store
from store.config import load_config

OWNER = "user:package-author"
FOREIGN = "team:private-source"


@pytest.fixture(scope="module", params=["0", "1"])
def graph(request, tmp_path_factory):
    import os
    keys = ("VINC_PK_NAMESPACED", "MEMORY_STORE_BACKEND")
    old = {key: os.environ.get(key) for key in keys}
    os.environ.update(VINC_PK_NAMESPACED=request.param, MEMORY_STORE_BACKEND="embedded")
    cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("package-contract") / "db")})
    st = open_store(cfg)
    st.provision(dim=cfg.embedding_dim)
    try:
        for owner, nid in [(OWNER, "concept:source"), (FOREIGN, "concept:private")]:
            result = merge_fragment({"owner": owner, "nodes": [
                {"id": nid, "type": "concept", "title": "Evidence", "domain": "vinc/test"}]}, store=st)
            assert result["executed"]
        yield st
    finally:
        st.close()
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def package(nid="record:package"):
    return {"id": nid, "type": "record", "title": "Run conclusion", "domain": "vinc/test",
            "tags": ["package"], "valid_from": "2026-10-04",
            "props": {"package_schema": "vinc.package.v1", "question": "What survived?",
                      "conclusion": "All ten claims. 근거를 보존한다.", "produced_by": "research",
                      "run_id": "deterministic-fixture", "custom": {"retained": True},
                      "claims": [{"claim": f"Claim {i}", "evidence_ids": ["concept:source"]}
                                 for i in range(10)]}}


def authored(st, nodes):
    return merge_fragment({"owner": OWNER, "nodes": nodes}, store=st,
                          authored=True, read_back=True)


def test_ten_claims_round_trip_and_partial_update_preserves_props(graph):
    node = package()
    original = deepcopy(node)
    result = authored(graph, [node])
    assert result["executed"] and not result["warnings"], result
    stored = query.node(node["id"], owner=OWNER, store=graph)["node"]
    assert stored["props"] == original["props"]
    assert stored["tags"] == original["tags"]
    assert stored["valid_from"] == "2026-10-04"
    assert node == original
    assert authored(graph, [{"id": node["id"], "type": "record", "title": "Revised title"}])["executed"]
    assert query.node(node["id"], owner=OWNER, store=graph)["node"]["props"] == original["props"]


@pytest.mark.parametrize("evidence", ["concept:missing", "concept:private", "record:self"])
def test_missing_foreign_and_self_evidence_reject_before_any_write(graph, evidence):
    node = package("record:self")
    node["props"]["claims"][0]["evidence_ids"] = [evidence]
    result = authored(graph, [{"id": "concept:must-not-write", "type": "concept"}, node])
    assert not result["executed"] and result["error"] == "invalid_package"
    assert not query.node("concept:must-not-write", owner=OWNER, store=graph)["found"]
    assert not query.node("record:self", owner=OWNER, store=graph)["found"]


@pytest.mark.parametrize("field,value", [("claims", []), ("claims", [{"claim": "x", "evidence_ids": []}]),
    ("claims", [{"claim": "x", "evidence_ids": ["concept:source"], "evidence_refs": []}]),
    ("question", ""), ("conclusion", 2), ("run_id", None), ("package_schema", "vinc.package.v2")])
def test_malformed_package_keeps_previous_record(graph, field, value):
    node = package("record:unchanged")
    assert authored(graph, [node])["executed"]
    before = query.node(node["id"], owner=OWNER, store=graph)["node"]
    bad = deepcopy(node)
    bad["props"][field] = value
    assert not authored(graph, [bad])["executed"]
    assert query.node(node["id"], owner=OWNER, store=graph)["node"] == before


def test_open_entity_cannot_lose_package_props(graph):
    node = package("package:new")
    node["type"] = "package"
    assert not authored(graph, [node])["executed"]
    assert not query.node(node["id"], owner=OWNER, store=graph)["found"]


def test_existing_free_props_and_replica_restore_are_compatible(graph):
    ordinary = {"id": "record:ordinary", "type": "record", "props": {"package": "VIN-300", "claims": "free"}}
    assert authored(graph, [ordinary])["executed"]
    node = package("record:replicated")
    node["props"]["claims"][0]["evidence_ids"] = ["concept:not-arrived-yet"]
    assert merge_fragment({"owner": OWNER, "nodes": [node]}, store=graph)["executed"]
    assert query.node(node["id"], owner=OWNER, store=graph)["node"]["props"] == node["props"]


@pytest.mark.parametrize("route", ["direct", "fragment"])
def test_both_public_ingest_handlers_validate_in_the_serial_write_queue(graph, route):
    from api.server import VincApp
    seen = []
    def run_sync(name, fn):
        seen.append(name)
        return fn()
    app = SimpleNamespace(store=graph, themes=None, jobs=SimpleNamespace(run_sync=run_sync))
    app._run_mutation = MethodType(VincApp._run_mutation, app)
    node = package("record:http-denied")
    node["props"]["claims"][0]["evidence_ids"] = ["concept:private"]
    body = {"owner": OWNER, "nodes": [node]}
    status, result = (VincApp.write_ingest(app, body) if route == "direct"
                      else VincApp.submit_ingest(app, "fragment", body))
    assert status == 200 and result["error"] == "invalid_package" and not result["executed"]
    assert seen == ["ingest:fragment"]
