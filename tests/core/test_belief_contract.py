"""belief의 거절, 이력, 확률 미러와 소유자 격리를 실엔진에서 검증한다."""
from copy import deepcopy
from itertools import count
from types import SimpleNamespace

import pytest

from api import belief_contract as bc, query
from api.fragment import merge_fragment
from store import pkns
from store.backend import open_store
from store.config import load_config

OWNER, FOREIGN = "user:belief-author", "team:other-belief"
CASES = count()


@pytest.fixture(scope="module", params=["0", "1"])
def graph(request, tmp_path_factory):
    import os
    keys = ("VINC_PK_NAMESPACED", "MEMORY_STORE_BACKEND")
    old = {k: os.environ.get(k) for k in keys}
    os.environ.update(VINC_PK_NAMESPACED=request.param, MEMORY_STORE_BACKEND="embedded")
    cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("belief-contract") / "db")})
    store = open_store(cfg)
    try:
        store.provision(dim=cfg.embedding_dim)
        yield store
    finally:
        store.close()
        for k, value in old.items():
            if value is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = value


def payload():
    case = next(CASES)
    a, b, nid = f"concept:a-{case}", f"concept:b-{case}", f"record:belief-{case}-1"
    belief = {"id": nid, "type": "record", "title": "A supports B", "domain": "vinc/test",
              "valid_from": "2026-10-01", "valid_until": "",
              "props": {"belief_schema": bc.SCHEMA,
                        "subject": {"from": a, "predicate": "supports", "to": b},
                        "p": 0.82, "distribution": [{"option": "yes", "p": .82}, {"option": "no", "p": .18}],
                        "judged_by": "laya:fixture@1", "calibration_id": "cal-fixture",
                        "evidence_ids": ["doc#00001", a], "independence_group": "session:fixture",
                        "rev_seen": {a: 1, b: 1}, "grade": "model", "custom": {"retained": "근거"}}}
    return {"owner": OWNER, "nodes": [
        {"id": a, "type": "concept", "title": "A"},
        {"id": b, "type": "concept", "title": "B"}, belief],
        "edges": [{"from": nid, "type": "relates_to", "to": end} for end in (a, b)]}


def write(graph, body, **kw):
    return merge_fragment(body, store=graph, authored=True, read_back=True, **kw)


def read(graph, nid, owner=OWNER):
    return query.node(nid, owner=owner, store=graph)["node"]


def relation(graph, body):
    a = body["nodes"][0]["id"]
    return query.node_edges(a, owner=OWNER, type="RELATED_TO", store=graph)["edges"][0]


def successor(body, *, p=.0, day="2026-10-02", predicate=None):
    body = deepcopy(body)
    node = body["nodes"][2]
    old = node["id"]
    node["id"] = old.rsplit("-", 1)[0] + "-2"
    node["props"]["p"] = p
    node["props"]["distribution"] = None
    if predicate:
        node["props"]["subject"]["predicate"] = predicate
    node["valid_from"] = day
    for edge in body["edges"]:
        edge["from"] = node["id"]
    return body


def test_roundtrip_close_and_retry_cannot_resurrect_old_probability(graph):
    body = payload()
    original = deepcopy(body)
    result = write(graph, body)
    assert result["executed"] and not result["warnings"], result
    assert body == original
    first = body["nodes"][2]
    assert read(graph, first["id"])["props"] == first["props"]
    assert relation(graph, body)["confidence"] == .82
    second = successor(body)
    assert not write(graph, second)["warnings"]
    old = read(graph, first["id"])
    assert old["valid_until"] == "2026-10-02"
    assert old["props"] == first["props"]
    edge = relation(graph, body)
    assert edge["p"] == edge["confidence"] == 0
    assert edge["belief_status"] == "judged" and edge["belief_id"] == second["nodes"][2]["id"]
    assert write(graph, body)["executed"]
    assert read(graph, first["id"])["valid_until"] == "2026-10-02"
    assert relation(graph, body)["p"] == relation(graph, body)["confidence"] == 0
    assert read(graph, first["id"])["props"] == first["props"]


@pytest.mark.parametrize("field,value", [
    ("p", True), ("p", "0.8"), ("p", -0.1), ("p", 1.1), ("p", float("nan")), ("p", float("inf")),
    ("calibration_id", ""), ("judged_by", None), ("independence_group", []),
    ("rev_seen", {}), ("grade", "trusted"), ("belief_schema", "vinc.belief.v2"),
    ("subject", []), ("evidence_ids", []), ("distribution", [{"option": "yes", "p": .8}]),
])
def test_invalid_belief_rejects_the_whole_fragment_before_writing(graph, field, value):
    body = payload()
    body["nodes"][2]["props"][field] = value
    result = write(graph, body)
    assert result["error"] == "invalid_belief" and not result["executed"]
    assert read(graph, body["nodes"][0]["id"]) is None
    assert read(graph, body["nodes"][2]["id"]) is None


@pytest.mark.parametrize("change", ["entity", "marker", "attachment", "endpoint", "date", "boolean_rev", "duplicate"])
def test_invalid_carrier_and_incomplete_context(graph, change):
    body = payload()
    n = body["nodes"][2]
    if change == "entity":
        n["type"] = "belief"
    elif change == "marker":
        del n["props"]["belief_schema"]
    elif change == "attachment":
        body["edges"].pop()
    elif change == "endpoint":
        body["nodes"].pop(0)
    elif change == "date":
        n["valid_from"] = "2026-02-30"
    elif change == "boolean_rev":
        n["props"]["rev_seen"][body["nodes"][0]["id"]] = True
    else:
        body["nodes"].append(deepcopy(n))
    result = write(graph, body)
    assert not result["executed"] and result["error"] == "invalid_belief", result


def test_props_cannot_be_erased_or_changed_but_stub_preserves_them(graph):
    body = payload()
    assert write(graph, body)["executed"]
    node = body["nodes"][2]
    for props in ({}, {**node["props"], "p": .1}):
        result = write(graph, {"owner": OWNER, "nodes": [{"id": node["id"], "type": "record", "props": props}]})
        assert result["error"] == "invalid_belief"
        assert read(graph, node["id"])["props"] == node["props"]
    result = write(graph, {"owner": OWNER, "nodes": [{"id": node["id"], "type": "record", "title": "Revised title"}]})
    assert result["executed"] and read(graph, node["id"])["props"] == node["props"]


def test_unjudged_is_not_a_zero_probability(graph):
    body = payload()
    a, b = body["nodes"][:2]
    result = write(graph, {"owner": OWNER, "nodes": [a, b],
                           "edges": [{"from": a["id"], "to": b["id"], "type": "relates_to", "confidence": 0.0}]})
    assert result["executed"]
    edge = relation(graph, body)
    assert edge["confidence"] == 0 and "p" not in edge
    assert edge["belief_status"] == "unjudged" and "belief_id" not in edge


def test_reverse_edge_and_two_predicates_share_only_the_mirror(graph):
    body = payload()
    a, b = body["nodes"][:2]
    write(graph, {"owner": OWNER, "nodes": [a, b], "edges": [
        {"from": b["id"], "to": a["id"], "type": "relates_to", "confidence": .9}]})
    assert write(graph, body)["executed"]
    second = successor(body, p=.15, predicate="contradicts")
    assert write(graph, second)["executed"]
    assert read(graph, body["nodes"][2]["id"])["valid_until"] == ""
    edge = relation(graph, body)
    assert edge["from"] == b["id"] and edge["confidence"] == .15
    assert edge["predicate"] == "contradicts"
    # 엣지 숫자만 다시 보내도 확률 정본을 덮지 않는다.
    write(graph, {"owner": OWNER, "edges": [{"from": b["id"], "to": a["id"], "type": "relates_to", "confidence": .99}]})
    assert relation(graph, body)["confidence"] == .15


def test_human_confirmation_survives_model_rejudgment(graph):
    body = payload()
    body["nodes"][2]["props"].update(grade="user-confirmed", judged_by="human")
    assert write(graph, body)["executed"]
    after = successor(body)
    after["nodes"][2]["props"].update(grade="model", judged_by="laya:fixture@1")
    assert write(graph, after)["error"] == "invalid_belief"
    assert read(graph, body["nodes"][2]["id"])["valid_until"] == ""


def test_retry_repairs_an_interruption_before_history_and_mirror(graph, monkeypatch):
    body = payload()
    assert write(graph, body)["executed"]
    second = successor(body, p=.23, day="2026-10-01")
    apply = bc.apply
    def fail(*args, **kwargs):
        raise RuntimeError("injected interruption")
    monkeypatch.setattr(bc, "apply", fail)
    with pytest.raises(RuntimeError, match="injected"):
        write(graph, second)
    monkeypatch.setattr(bc, "apply", apply)
    assert write(graph, second)["executed"]
    assert read(graph, body["nodes"][2]["id"])["valid_until"] == "2026-10-01"
    assert relation(graph, body)["confidence"] == .23
    assert write(graph, body)["executed"]
    assert relation(graph, body)["confidence"] == .23


def test_replication_is_not_revalidated_but_an_unattached_record_is_not_a_judgment(graph):
    body = payload()
    body["edges"] = []
    assert merge_fragment(body, store=graph)["executed"]
    a, b = body["nodes"][:2]
    write(graph, {"owner": OWNER, "edges": [{"from": a["id"], "to": b["id"],
                                             "type": "relates_to", "confidence": 0}]})
    assert relation(graph, body)["belief_status"] == "unjudged"


def test_directed_belief_mirrors_only_its_own_direction(graph):
    body = payload()
    n = body["nodes"][2]
    n["props"]["subject"]["predicate"] = "prerequisite_of"
    a, b = body["nodes"][:2]
    body["edges"].append({"from": b["id"], "to": a["id"], "type": "prerequisite_of", "confidence": .12})
    assert write(graph, body)["executed"]
    edges = query.node_edges(a["id"], owner=OWNER, type="PREREQUISITE_OF", store=graph)["edges"]
    assert len(edges) == 2
    assert next(e for e in edges if e["from"] == a["id"])["confidence"] == .82
    assert next(e for e in edges if e["from"] == b["id"])["confidence"] == .12
    assert next(e for e in edges if e["from"] == b["id"])["belief_status"] == "unjudged"


def test_foreign_endpoint_is_not_taken_and_foreign_belief_does_not_leak(graph):
    body = payload()
    target = body["nodes"][0]
    merge_fragment({"owner": FOREIGN, "nodes": [target]}, store=graph)
    result = write(graph, body)
    if pkns.enabled():
        assert result["executed"]
        assert query.node_edges(target["id"], owner=FOREIGN, store=graph)["edges"] == []
    else:
        assert result["error"] == "invalid_belief"
        assert read(graph, body["nodes"][2]["id"]) is None
    assert read(graph, target["id"], FOREIGN)["title"] == "A"


@pytest.mark.parametrize("route", ["direct", "submit"])
def test_public_ingest_handlers_use_the_serial_validation_path(graph, route):
    from api.server import VincApp
    calls = []
    def run_sync(name, fn):
        calls.append(name)
        return fn()
    app = SimpleNamespace(store=graph, themes=None, jobs=SimpleNamespace(run_sync=run_sync))
    body = payload()
    body["nodes"][2]["props"]["p"] = False
    status, result = (VincApp.write_ingest(app, body) if route == "direct"
                      else VincApp.submit_ingest(app, "fragment", body))
    assert status == 200 and result["error"] == "invalid_belief"
    assert calls == ["ingest:fragment"]
