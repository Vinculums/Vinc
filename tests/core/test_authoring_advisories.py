"""Native authoring diagnostics: precise scope, dates, bounded lookup and no retirement."""
from copy import deepcopy
import datetime as dt
import json
import os
from types import SimpleNamespace, MethodType

import pytest

from api import authoring, docstore, query
from api.fragment import merge_fragment
from api.server import VincApp
from store.backend import open_store
from store.config import load_config
from store import pkns

OWNER = "user:authoring-test"
NOW = dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc)
CASE = ""


@pytest.fixture(scope="module", params=["0", "1"])
def native_graph(request, tmp_path_factory):
    keys = ("VINC_PK_NAMESPACED", "MEMORY_STORE_BACKEND")
    before = {key: os.environ.get(key) for key in keys}
    os.environ.update(VINC_PK_NAMESPACED=request.param, MEMORY_STORE_BACKEND="embedded")
    st = open_store(load_config({"data_dir": str(tmp_path_factory.mktemp("authoring") / "db"), "embedding_dim": 4}))
    assert st is not None
    st.provision(dim=4)
    try:
        yield st
    finally:
        st.close()
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture
def graph(native_graph, request):
    global CASE
    CASE = request.node.name
    original = native_graph.run
    try:
        yield native_graph
    finally:
        native_graph.run = original


def claim(nid, value="0.1.0", date="2026-10-09", **props):
    props["entity"] = CASE + ":" + props.get("entity", "client")
    return {"id": "record:" + CASE + ":" + nid, "type": "record", "kind": "record",
            "title": "Public release", "domain": "vinc/product/roadmap",
            "valid_from": date, "props": {"entity": "client", "predicate": "release",
                "scope": {"channel": "public"}, "value": value,
                "value_type": "version", **props}}


def write(st, nodes, **kwargs):
    return merge_fragment({"owner": OWNER, "nodes": nodes, "review_claims": True}, store=st,
                          authored=True, read_back=True, **kwargs)


def test_native_review_preserves_old_claim_and_reports_success(graph):
    old = claim("old")
    write(graph, [old])
    before = deepcopy(query.node(old["id"], store=graph, owner=OWNER)["node"])
    result = write(graph, [claim("new", "0.2.0", "2026-10-13")])
    assert result["executed"] and not result["warnings"] and not result.get("partial")
    assert result["advisory_review"]["state"] == "complete"
    advice = result["advisories"][0]
    assert advice["previous_id"] == old["id"]
    assert advice["code"] == "claim_replacement_review"
    assert advice["suggested_valid_until"] == "2026-10-13" and not advice["automatic"]
    assert query.node(old["id"], store=graph, owner=OWNER)["node"] == before
    assert result["retrieval"]["node_vectors"] == "not_checked"


@pytest.mark.parametrize("change", [
    {"scope": {"channel": "preview"}}, {"entity": "other"}, {"predicate": "installed"},
    {"value": "0.1.0"}, {"value": "0.1", "value_type": "version"},
    {"value": "0.1.0", "value_type": "text"},
])
def test_non_comparable_or_equivalent_values_do_not_warn(graph, change):
    write(graph, [claim("old")])
    new = claim("new", "0.2.0", "2026-10-13")
    new["props"].update(change)
    result = write(graph, [new])
    assert not result["advisories"]


def test_other_owner_and_skipped_foreign_id_cannot_leak(graph):
    foreign = claim("private", "0.1.0")
    merge_fragment({"owner": "team:private", "nodes": [foreign]}, store=graph)
    result = write(graph, [claim("new", "0.2.0", "2026-10-13")])
    assert not result["advisories"]
    collided = write(graph, [claim("private", "0.2.0", "2026-10-13")])
    if pkns.enabled():
        assert not collided.get("id_conflicts")
    else:
        assert collided.get("id_conflicts")
    assert not collided.get("advisories")


def test_metadata_only_resend_preserves_props_and_replication_skips_review(graph):
    original = claim("old", retained="keep")
    write(graph, [original])
    result = write(graph, [{"id": original["id"], "type": "record", "title": "Edited title"}])
    assert result["stored"][0]["props"] == original["props"]
    assert not result["advisories"]
    replicated = merge_fragment({"owner": OWNER, "nodes": [claim("new", "0.2.0", "2026-10-13")]},
                                store=graph, read_back=True)
    assert "advisories" not in replicated and "retrieval" not in replicated


@pytest.mark.parametrize("date,code", [
    ("", "claim_valid_from_missing"), ("2026-02-30", "claim_interval_invalid")])
def test_dates_are_advisory_not_an_automatic_write_failure(graph, date, code):
    result = write(graph, [claim("undated", date=date)])
    assert result["executed"] and not result.get("partial")
    assert result["advisories"][0]["code"] == code


def test_review_limit_is_explicit_and_query_failure_does_not_fail_saved_data(graph):
    write(graph, [claim("old")])
    original_run = graph.run
    def failing(cypher, *args, **kwargs):
        if "n.props CONTAINS" in cypher:
            raise RuntimeError("private backend detail")
        return original_run(cypher, *args, **kwargs)
    graph.run = failing
    result = write(graph, [claim("new", "0.2.0", "2026-10-13")])
    assert result["executed"] and not result.get("partial")
    assert result["advisory_review"]["state"] == "unavailable"
    assert "private backend detail" not in json.dumps(result)


def test_candidate_cap_does_not_claim_complete_review(graph):
    write(graph, [claim("old" + str(i)) for i in range(authoring.MAX_CANDIDATES + 1)])
    result = write(graph, [claim("new", "0.2.0", "2026-10-13")])
    assert result["advisory_review"]["state"] == "limited"
    assert not result["advisories"]


def test_node_cap_and_ordinary_notes(graph):
    count = 0
    original = graph.run
    def counted(cypher, *args, **kwargs):
        nonlocal count
        count += "n.props CONTAINS" in cypher
        return original(cypher, *args, **kwargs)
    graph.run = counted
    result = write(graph, [{"id": "record:note", "type": "record", "props": {"note": "event"}}])
    assert not result.get("advisory_review") and count == 0
    result = write(graph, [claim("new" + str(i)) for i in range(authoring.MAX_CLAIMS + 1)])
    assert result["advisory_review"]["state"] == "limited"
    assert count <= 2
    assert result["advisory_review"]["claims_reviewed"] == authoring.MAX_CLAIMS


def test_default_date_review_performs_no_candidate_query(graph):
    original = graph.run
    def guarded(cypher, *args, **kwargs):
        assert "n.props CONTAINS" not in cypher
        return original(cypher, *args, **kwargs)
    graph.run = guarded
    result = merge_fragment({"owner": OWNER, "nodes": [claim("undated", date="")]},
                            store=graph, authored=True, read_back=True)
    assert result["executed"] and not result.get("partial")
    assert result["advisory_review"]["state"] == "not_requested"
    assert result["advisories"][0]["code"] == "claim_valid_from_missing"


def test_invalid_review_flag_is_rejected_before_writing(graph):
    node = claim("must-not-exist")
    result = merge_fragment({"owner": OWNER, "nodes": [node], "review_claims": "true"},
                            store=graph, authored=True, read_back=True)
    assert not result["executed"] and "review_claims" in result["error"]
    assert not query.node(node["id"], store=graph, owner=OWNER).get("node")


def test_escaped_and_korean_identity_uses_serialized_prefilter(graph):
    for entity in ['client"line\\name', "공개 클라이언트"]:
        old = claim("old" + str(len(entity)), entity=entity)
        write(graph, [old])
        result = write(graph, [claim("new" + str(len(entity)), "0.2.0", "2026-10-13", entity=entity)])
        assert any(a.get("previous_id") == old["id"] for a in result["advisories"])


def test_decision_claim_dates_are_read_from_persisted_props(graph):
    anchor = {"id": "concept:" + CASE, "type": "concept", "title": "Decision subject"}
    results = []
    for suffix, value, date in (("old", "0.1.0", "2026-10-09"), ("new", "0.2.0", "2026-10-13")):
        node = claim(suffix, value, date)
        node.update(id=node["id"].replace("record:", "decision:", 1), type="decision", kind="decision")
        node["props"]["valid_from"] = node.pop("valid_from")
        results.append(merge_fragment({"owner": OWNER, "review_claims": True,
            "nodes": [anchor, node], "edges": [{"from": node["id"], "to": anchor["id"],
                                                "type": "about", "evidence": "Synthetic test"}]},
            store=graph, authored=True, read_back=True))
    assert results[-1]["advisory_review"]["state"] == "complete"
    assert results[-1]["advisories"][0]["code"] == "claim_replacement_review"
    assert results[-1]["advisories"][0]["previous_id"].startswith("decision:")


def test_document_readiness_and_actual_validity_survive_skip_and_metadata_update(graph):
    text = "---\nvalid_from: 2020-01-01\n---\nCanonical body."
    result = docstore.put_doc(graph, owner=OWNER, doc_id="probe.doc", title="Probe", text=text,
                             valid_from="2026-10-13")
    assert result["valid_from"] == "2026-10-13"
    assert result["retrieval"]["document_vectors"] == "pending"
    assert result["retrieval"]["query_index"] == "not_checked"
    again = docstore.put_doc(graph, owner=OWNER, doc_id="probe.doc", text=text)
    assert again["skipped"] and again["needs_embed"]
    assert again["valid_from"] == "2026-10-13"
    edited = docstore.put_doc(graph, owner=OWNER, doc_id="probe.doc", text=text, valid_until="2026-10-14")
    assert not edited["skipped"] and edited["needs_embed"]
    assert edited["retrieval"]["document_vectors"] == "pending"
    assert edited["valid_until"] == "2026-10-14"
    assert docstore.get_doc(graph, "probe.doc", owner=OWNER)["text"] == text


@pytest.mark.parametrize("route", ["direct", "job"])
def test_public_ingest_routes_preserve_advisories_in_serial_writer(graph, route):
    write(graph, [claim("old")])
    seen = []
    class Jobs:
        def run_sync(self, name, fn):
            seen.append(name)
            return fn()
    app = SimpleNamespace(store=graph, themes=None, jobs=Jobs())
    app._run_mutation = MethodType(VincApp._run_mutation, app)
    body = {"owner": OWNER, "nodes": [claim("new", "0.2.0", "2026-10-13")], "review_claims": True}
    status, result = (VincApp.write_ingest(app, body) if route == "direct"
                      else VincApp.submit_ingest(app, "fragment", body))
    assert status == 200 and result["advisories"][0]["code"] == "claim_replacement_review"
    assert len(seen) == 1


@pytest.mark.parametrize("change", [
    {"valid_from": "2099-01-01"}, {"valid_from": "2026-10-08"}, {"valid_until": "2026-10-12"},
])
def test_future_older_or_expired_incoming_claims_are_not_replacement_candidates(change):
    new, old = claim("new", "0.2.0", "2026-10-13"), claim("old")
    new.update(change)
    assert authoring.compare(new, old, now=NOW) is None


def test_unknown_partial_dates_and_unsupported_versions():
    new, old = claim("new", "0.2.0", "2026-10-13"), claim("old", date="2026")
    assert authoring.compare(new, old, now=NOW)["code"] == "claim_dates_need_review"
    old["props"]["value"] = "0.1.0rc1"
    assert authoring.compare(new, old, now=NOW) is None


def test_typed_numbers_and_complete_intervals_do_not_create_false_replacements():
    new, old = claim("new", "0.2.0", "2026-10-13"), claim("old")
    new["props"].update(value=1.0, value_type="number")
    old["props"].update(value=1, value_type="number")
    assert authoring.compare(new, old, now=NOW) is None
    new["props"]["value"] = 2
    old["valid_until"] = "2026-10-13"
    assert authoring.compare(new, old, now=NOW) is None


def test_unsupported_value_and_oversized_advice_are_explicitly_limited():
    node = claim("large", "1.0rc1")
    class Store:
        def run(self, *args, **kwargs):
            raise AssertionError("unsupported values must not reach candidate lookup")
    assert authoring.review(Store(), [node], owner=OWNER, compare_claims=True)["advisory_review"]["state"] == "limited"
    node = claim("x" * authoring.MAX_ADVISORY_CHARS, date="")
    class Empty:
        def run(self, *args, **kwargs):
            return []
    result = authoring.review(Empty(), [node], owner=OWNER)
    assert result["advisory_review"]["state"] == "limited" and not result["advisories"]
