from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_topic_transfer import RawStore, SOURCE, TARGET, TOPIC, STAMP, seed, insert
from store.embedded_schema import render_embedded_ddl
from store.topic_transfer import TransferRefused, fingerprint, plan_transfer, read_snapshot, transformed_snapshot
from api import topic_conversion as tc

RUN = "00000000-0000-4000-8000-000000000099"


@pytest.fixture
def native(tmp_path):
    store = RawStore(tmp_path / "synthetic.db")
    store._FTS_INDEX, store._VEC_INDEX, store._NODE_VEC_INDEX = "chunk_ft", "chunk_vec", "node_vec"
    for stmt in render_embedded_ddl(dim=4):
        store.run(stmt)
    insert(store, seed())
    yield store
    store.close()


def stage(store, **kwargs):
    expected = tc.preflight(store, SOURCE, TOPIC)["fingerprint"]
    return tc.stage(store, source=SOURCE, target=TARGET, topic=TOPIC, run=RUN,
                    expected=expected, stamp=STAMP, **kwargs)


def test_commit_fences_both_scopes_until_account_activation(native):
    check = tc.preflight(native, SOURCE, TOPIC)
    assert check["ready"] and check["documents"] == 1
    args = dict(source=SOURCE, target=TARGET, topic=TOPIC, run=RUN)
    assert stage(native)["status"] == "graph_committed"
    old = next(r for r in tc.journals(native)[0]["prior_sync_metadata"] if r["id"] == "concept:a" and r["label"] == "Concept")
    assert old == {"id": "concept:a", "label": "Concept", "owner_id": SOURCE,
                   "rev": 7, "sync_seq": 40, "updated_at": "2020-01-01T00:00:00.000Z"}
    for owner in (SOURCE, TARGET, None):
        with pytest.raises(TransferRefused, match="pending"):
            tc.check_read(native, owner)
        with pytest.raises(TransferRefused, match="pending"):
            tc.check_write(native, owner)
    tc.check_read(native, "user:other")
    assert tc.stage(native, **args, expected=check["fingerprint"], stamp=STAMP)["status"] == "graph_committed"
    assert tc.release(native, **args)["status"] == "active"
    tc.check_read(native, SOURCE)
    tc.check_read(native, TARGET)
    with pytest.raises(TransferRefused, match="moved"):
        tc.check_write(native, SOURCE, {"nested": {"from": "concept:a"}})
    with pytest.raises(TransferRefused, match="moved"):
        tc.check_write(native, SOURCE, {"title": "See [[concept:a|Moved item]]"})
    with pytest.raises(TransferRefused, match="moved"):
        tc.check_write(native, SOURCE, {"topic": TOPIC + "/child"})
    tc.check_write(native, SOURCE, {"id": "concept:other"})
    tc.check_write(native, TARGET, {"id": "concept:a"})
    assert tc.release(native, **args)["status"] == "active"


@pytest.mark.parametrize("point", ["nodes", "edges", "delete", "journal"])
def test_failure_before_commit_preserves_original(native, point):
    before = fingerprint(read_snapshot(native))
    with pytest.raises(RuntimeError, match="injected"):
        stage(native, fail_at=point)
    after = read_snapshot(native)
    after["meta"]["_SyncMeta"] = [r for r in after["meta"]["_SyncMeta"] if not r["key"].startswith(tc.PREFIX)]
    assert fingerprint(after) == before
    assert tc.fenced(native, SOURCE)
    assert tc.cancel(native, source=SOURCE, target=TARGET, topic=TOPIC, run=RUN)["status"] == "cancelled"
    assert not tc.fenced(native, SOURCE)
    tc.check_write(native, SOURCE, {"topic": TOPIC})


def test_cancel_barrier_beats_an_outstanding_signed_stage(native):
    expected = tc.preflight(native, SOURCE, TOPIC)["fingerprint"]
    args = dict(source=SOURCE, target=TARGET, topic=TOPIC, run=RUN)
    tc.cancel(native, **args)
    assert tc.stage(native, **args, expected=expected, stamp=STAMP)["status"] == "cancelled"
    assert read_snapshot(native)["meta"]["_DocMeta"][0]["owner_id"] == SOURCE


def test_commit_cannot_be_cancelled_as_if_source_were_unchanged(native):
    stage(native)
    with pytest.raises(TransferRefused, match="resume_required"):
        tc.cancel(native, source=SOURCE, target=TARGET, topic=TOPIC, run=RUN)


def test_source_change_invalidates_preflight(native):
    expected = tc.preflight(native, SOURCE, TOPIC)["fingerprint"]
    native.run("MATCH (c:Concept) WHERE c.concept_id=$id SET c.title='edited'", {"id": SOURCE + "\x1fconcept:a"})
    before = fingerprint(read_snapshot(native))
    with pytest.raises(TransferRefused, match="plan_stale"):
        tc.stage(native, source=SOURCE, target=TARGET, topic=TOPIC, run=RUN, expected=expected, stamp=STAMP)
    assert fingerprint(read_snapshot(native)) == before


def test_other_tenant_writes_do_not_invalidate_preflight(native):
    expected = tc.preflight(native, SOURCE, TOPIC)["fingerprint"]
    native.run("CREATE (n:Concept {concept_id:$id, owner_id:'user:other', domain:'other', title:'other'})",
               {"id": "user:other\x1fconcept:other"})
    assert tc.stage(native, source=SOURCE, target=TARGET, topic=TOPIC, run=RUN,
                    expected=expected, stamp=STAMP)["status"] == "graph_committed"


def test_large_unrelated_tenant_is_not_in_the_move_budget(native, monkeypatch):
    import store.scoped_topic_inventory as scoped

    for index in range(150):
        native.run("CREATE (c:Concept {concept_id:$id, owner_id:'user:other', domain:'other', title:'kept'})",
                   {"id": f"user:other\x1fconcept:{index:04d}"})
    with pytest.raises(TransferRefused, match="conversion_inventory_limit"):
        read_snapshot(native, max_rows=100)
    monkeypatch.setattr(scoped, "MAX_INVENTORY_ROWS", 100)
    before = read_snapshot(native)
    expected = transformed_snapshot(before, plan_transfer(before, source=SOURCE, target=TARGET, topic=TOPIC),
                                    stamp=STAMP)
    check = tc.preflight(native, SOURCE, TOPIC)
    assert check["ready"] and check["inventory_rows"] < 100
    assert check["projected_rows"] > check["inventory_rows"]
    assert check["projected_rows"] <= 5000 and check["projected_bytes"] <= 32 * 1024 * 1024
    assert check["documents"] == 1
    assert stage(native)["status"] == "graph_committed"
    assert native.run("MATCH (c:Concept) WHERE c.owner_id='user:other' RETURN count(c) AS n")[0]["n"] == 150
    observed = read_snapshot(native)
    observed["meta"]["_SyncMeta"] = [r for r in observed["meta"]["_SyncMeta"]
                                      if not r["key"].startswith(tc.PREFIX)]
    assert fingerprint(observed) == fingerprint(expected)
    assert tc.release(native, source=SOURCE, target=TARGET, topic=TOPIC, run=RUN)["status"] == "active"


def test_paged_source_dependency_scan_blocks_incoming_reference(native):
    for index in range(130):
        native.run("CREATE (c:Concept {concept_id:$id, owner_id:$owner, domain:'other', title:$title})",
                   {"id": SOURCE + f"\x1fconcept:outside-{index:04d}", "owner": SOURCE,
                    "title": "unrelated"})
    assert tc.preflight(native, SOURCE, TOPIC)["ready"]
    native.run("MATCH (c:Concept) WHERE c.concept_id=$id SET c.title=$title",
               {"id": SOURCE + "\x1fconcept:outside-0129",
                "title": "[" + "[concept:a]]"})
    before = fingerprint(read_snapshot(native))
    check = tc.preflight(native, SOURCE, TOPIC)
    assert not check["ready"] and "incoming_reference" in check["blockers"]
    assert check["inspected_rows"] > 130
    assert fingerprint(read_snapshot(native)) == before
    assert not tc.journals(native)


@pytest.mark.parametrize("budget", [{"MAX_INVENTORY_ROWS": 10}, {"MAX_INVENTORY_BYTES": 100}])
def test_selected_closure_beyond_legacy_cap_has_verified_counts_but_cannot_move(native, monkeypatch, budget):
    import store.scoped_topic_inventory as scoped
    for key, value in budget.items():
        monkeypatch.setattr(scoped, key, value)
    before = fingerprint(read_snapshot(native))
    check = tc.preflight(native, SOURCE, TOPIC)
    assert not check["ready"] and check["blockers"] == ["conversion_bounded_executor_required"]
    assert check["counts_known"] is True and check["diagnostic_only"]
    assert fingerprint(read_snapshot(native)) == before
    assert not tc.journals(native)


def test_unexpected_target_row_blocks_release_under_fence(native):
    assert stage(native)["status"] == "graph_committed"
    native.run("CREATE (c:Concept {concept_id:$id, owner_id:$owner, domain:'outside'})",
               {"id": TARGET + "\x1fconcept:unexpected", "owner": TARGET})
    with pytest.raises(TransferRefused, match="committed_payload_changed"):
        tc.release(native, source=SOURCE, target=TARGET, topic=TOPIC, run=RUN)
    assert tc.fenced(native, TARGET)


def test_foreign_row_at_mapped_target_key_is_refused_before_dml(native):
    native.run("CREATE (v:NodeVec {vec_id:$id, owner_id:'user:foreign', node_id:'concept:a'})",
               {"id": TARGET + "#concept:a"})
    check = tc.preflight(native, SOURCE, TOPIC)
    assert check["ready"]  # The new team's actual id is not known until reserve.
    before = fingerprint(read_snapshot(native))
    with pytest.raises(TransferRefused, match="key_collision"):
        stage(native)
    assert fingerprint(read_snapshot(native)) == before
    assert not tc.journals(native)


def test_scoped_scan_keeps_source_history_blockers(native):
    native.run("CREATE (r:_MergeRejection {pair_key:$key, owner_id:$owner, "
               "a_id:'concept:a', b_id:'concept:other'})", {"owner": SOURCE, "key": SOURCE + "|concept:a|concept:other"})
    check = tc.preflight(native, SOURCE, TOPIC)
    assert not check["ready"] and "merge_rejection_history_not_supported" in check["blockers"]
    assert not tc.journals(native)


def test_unrelated_source_tombstone_is_reported_and_preserved(native):
    native.run("CREATE (t:_Tombstone {node_id:$id, owner_id:$owner, label:'Concept'})",
               {"id": SOURCE + "\x1fconcept:outside", "owner": SOURCE})
    check = tc.preflight(native, SOURCE, TOPIC)
    assert check["ready"] and check["retained_personal_history"]["_Tombstone"] == 1
    assert not tc.journals(native)
    before = native.run("MATCH (t:_Tombstone) WHERE t.node_id=$id RETURN t.*",
                        {"id": SOURCE + "\x1fconcept:outside"})
    stage(native)
    assert native.run("MATCH (t:_Tombstone) WHERE t.node_id=$id RETURN t.*",
                      {"id": SOURCE + "\x1fconcept:outside"}) == before


def test_durable_commit_resume_after_reopen(native):
    expected = tc.preflight(native, SOURCE, TOPIC)["fingerprint"]
    stage(native)
    native.close()
    native.reopen()
    assert tc.fenced(native, SOURCE)
    args = dict(source=SOURCE, target=TARGET, topic=TOPIC, run=RUN)
    native._ensure_index = lambda table: pytest.fail("unindexed fixture unexpectedly rebuilt an index")
    assert tc.stage(native, **args, expected=expected, stamp=STAMP)["status"] == "graph_committed"
    tc.release(native, **args)


@pytest.mark.parametrize("prepared", [False, True])
def test_prior_full_inventory_run_remains_resumable(native, prepared):
    old_expected = tc.scoped_hash(read_snapshot(native), SOURCE, tc.PREFLIGHT_TARGET)
    if prepared:
        tc._save(native, RUN, {"source": SOURCE, "target": TARGET, "topic": TOPIC,
                               "expected": old_expected, "indexes": [], "status": "prepared"})
    args = dict(source=SOURCE, target=TARGET, topic=TOPIC, run=RUN)
    assert tc.stage(native, **args, expected=old_expected, stamp=STAMP)["status"] == "graph_committed"
    assert tc.journals(native)[0]["inventory_version"] == 1
    assert tc.stage(native, **args, expected=old_expected, stamp=STAMP)["status"] == "graph_committed"
    assert tc.release(native, **args)["status"] == "active"


def test_run_cannot_be_retargeted(native):
    stage(native)
    with pytest.raises(TransferRefused, match="run_id_reused"):
        tc.release(native, source=SOURCE, target="team:00000000-0000-4000-8000-000000000002", topic=TOPIC, run=RUN)


def test_boundary_codes_do_not_disclose_foreign_identifiers(native):
    native.run("CREATE (c:Concept {concept_id:'user:foreign\u001fconcept:private', owner_id:'user:foreign', domain:'private'})")
    native.run("MATCH (a:Concept), (b:Concept) WHERE a.concept_id=$src AND b.concept_id=$dst CREATE (a)-[:DERIVES_FROM]->(b)",
               {"src": SOURCE + "\x1fconcept:a", "dst": "user:foreign\x1fconcept:private"})
    result = tc.preflight(native, SOURCE, TOPIC)
    assert not result["ready"]
    assert "foreign" not in str(result) and "private" not in str(result)


def test_live_document_and_global_tombstone_conflict_is_blocked_in_preflight(native):
    native.run("CREATE (t:_DocTombstone {doc_id:'doc1', owner_id:$owner, deleted_at:'2020-01-01', sync_seq:1})",
               {"owner": SOURCE})
    before = fingerprint(read_snapshot(native))
    result = tc.preflight(native, SOURCE, TOPIC)
    assert not result["ready"]
    assert "document_tombstone_conflicts_with_live_doc" in result["blockers"]
    assert fingerprint(read_snapshot(native)) == before


def test_index_remnant_is_refused_before_a_team_can_be_reserved(native):
    native.vector_index_remnant = True
    result = tc.preflight(native, SOURCE, TOPIC)
    assert not result["ready"]
    assert result["blockers"] == ["search_index_repair_required"]


def test_inventory_limits_fail_closed_without_changes(native):
    before = fingerprint(read_snapshot(native))
    for limit in ({"max_rows": 1}, {"max_bytes": 100}):
        with pytest.raises(TransferRefused, match="conversion_inventory_limit"):
            read_snapshot(native, **limit)
    assert fingerprint(read_snapshot(native)) == before


def test_inventory_budget_includes_unrelated_owners(native):
    before = read_snapshot(native)
    base_rows = len(before["edges"]) + sum(len(rows) for group in ("nodes", "meta")
                                            for rows in before[group].values())
    selected = plan_transfer(before, source=SOURCE, target=TARGET, topic=TOPIC).selected
    for index in range(4):
        native.run("CREATE (c:Concept {concept_id:$id, owner_id:'user:other', domain:'other'})",
                   {"id": f"user:other\x1fconcept:extra-{index}"})
    assert plan_transfer(read_snapshot(native), source=SOURCE, target=TARGET, topic=TOPIC).selected == selected
    with pytest.raises(TransferRefused, match="conversion_inventory_limit"):
        read_snapshot(native, max_rows=base_rows + 3)


def test_preflight_does_not_report_an_unknown_topic_as_empty(native, monkeypatch):
    before = fingerprint(read_snapshot(native))
    def over_budget(*_args, **_kwargs):
        raise TransferRefused("conversion_inventory_limit")
    monkeypatch.setattr(tc, "_inventory", over_budget)
    result = tc.preflight(native, SOURCE, TOPIC)
    assert result == {"ready": False, "scope": "topic", "topic": TOPIC,
                      "counts_known": False, "counts": None, "documents": None,
                      "blockers": ["conversion_inventory_limit"]}
    assert fingerprint(read_snapshot(native)) == before
    assert not tc.journals(native)


def test_large_inventory_diagnostic_is_read_only_and_never_reserves(native, monkeypatch):
    import store.scoped_topic_inventory as reader
    before = fingerprint(read_snapshot(native))
    monkeypatch.setattr(reader, "MAX_INVENTORY_ROWS", 2)
    result = tc.preflight(native, SOURCE, TOPIC)
    assert not result["ready"] and result["diagnostic_only"]
    assert result["counts_known"] and result["documents"] == 1
    assert result["counts"]["Concept"] == 3
    assert "conversion_bounded_executor_required" in result["blockers"]
    assert result["inventory_rows"] > 2
    assert len(result["inventory_hash"]) == 64
    assert fingerprint(read_snapshot(native)) == before
    assert not tc.journals(native)


def test_diagnostic_reports_dependency_codes_without_foreign_identities(native):
    native.run("CREATE (n:Concept {concept_id:$id, owner_id:'user:foreign', domain:'foreign'})",
               {"id": "user:foreign\x1fconcept:private"})
    native.run("MATCH (a:Concept), (b:Concept) WHERE a.concept_id=$a AND b.concept_id=$b "
               "CREATE (a)-[:DERIVES_FROM]->(b)",
               {"a": SOURCE + "\x1fconcept:a", "b": "user:foreign\x1fconcept:private"})
    result = tc.inventory_diagnostics(native, SOURCE, TOPIC)
    assert result["counts_known"] and not result["ready"]
    assert "boundary_relation" in result["blockers"]
    assert "foreign" not in str(result) and "private" not in str(result)


def test_diagnostic_incomplete_scan_keeps_counts_unknown(native, monkeypatch):
    from store.topic_manifest import ManifestLimit
    def fail(*args, **kwargs):
        raise ManifestLimit("scan_seconds", observed=121, limit=120)
    monkeypatch.setattr(tc, "read_scoped_inventory", fail)
    result = tc.inventory_diagnostics(native, SOURCE, TOPIC)
    assert not result["counts_known"] and result["counts"] is None
    assert result["limit"] == {"reason": "scan_seconds", "observed": 121, "limit": 120}


@pytest.mark.parametrize("diagnostic", [False, True])
def test_foreign_document_payload_does_not_disclose_counts_or_sizes(native, diagnostic):
    native.run("MATCH (c:Chunk) WHERE c.doc_id='doc1' SET c.owner_id='user:private-owner'")
    result = (tc.inventory_diagnostics if diagnostic else tc.preflight)(native, SOURCE, TOPIC)
    assert not result["ready"] and not result["counts_known"]
    assert result["counts"] is None and result["documents"] is None
    assert "conversion_scope_inconsistent" in result["blockers"]
    assert not ({"inventory_hash", "inventory_rows", "inventory_bytes", "fingerprint"} & set(result))
    assert "private-owner" not in str(result)


def test_post_plan_budget_refusal_retains_verified_scope_counts(native, monkeypatch):
    before = fingerprint(read_snapshot(native))
    def over_budget(*_args):
        raise TransferRefused("conversion_inventory_limit")
    monkeypatch.setattr(tc, "_conversion_budget", over_budget)
    result = tc.preflight(native, SOURCE, TOPIC)
    assert not result["ready"] and result["blockers"] == ["conversion_inventory_limit"]
    assert result["counts_known"] and result["documents"] == 1
    assert result["counts"]["Concept"] > 0 and result["fingerprint"]
    assert fingerprint(read_snapshot(native)) == before
    assert not tc.journals(native)


def test_http_preflight_requires_durable_ack_configuration(monkeypatch):
    from types import SimpleNamespace
    from api.server import VincApp
    monkeypatch.setenv("VINC_TOPIC_CONVERSION", "1")
    app = SimpleNamespace(_durable=None)
    code, result = VincApp.write_topic_conversion(app, {"owner": SOURCE, "action": "preflight"})
    assert code == 503 and result["error"] == "conversion_requires_durable_ack"


def test_conversion_ack_does_not_enable_durability_for_ordinary_writes():
    from types import SimpleNamespace
    from api.server import VincApp
    calls = []
    app = SimpleNamespace(_durable=None, _conversion_durable=lambda job, result: calls.append(job.kind))
    for kind in ("ingest", "topic-conversion.preflight", "topic-conversion.status",
                 "topic-conversion.stage", "topic-conversion.release", "topic-conversion.cancel"):
        VincApp._ack_before_done(app, SimpleNamespace(kind=kind), {})
    assert calls == ["topic-conversion.stage", "topic-conversion.release", "topic-conversion.cancel"]


def test_existing_global_ack_is_not_published_twice_for_conversion():
    from types import SimpleNamespace
    from api.server import VincApp
    calls = []
    app = SimpleNamespace(_durable=lambda job, result: calls.append(job.kind),
                          _conversion_durable=lambda *args: pytest.fail("duplicate ACK"))
    for kind in ("ingest", "topic-conversion.stage"):
        VincApp._ack_before_done(app, SimpleNamespace(kind=kind), {})
    assert calls == ["ingest", "topic-conversion.stage"]


def test_failed_conversion_publication_never_reports_done():
    from types import SimpleNamespace
    from api.server import VincApp
    from api.jobs import JobQueue
    from store.persistence import DurableAckError
    def failed_publication(job, result):
        raise DurableAckError("synthetic publication failure")
    app = SimpleNamespace(_durable=None, _conversion_durable=failed_publication)
    queue = JobQueue(on_before_done=lambda job, result: VincApp._ack_before_done(app, job, result))
    try:
        job = queue.submit("topic-conversion.stage", lambda: {"status": "graph_committed"})
        assert job._done.wait(5)
        assert job.status == "error" and job.write_may_have_applied
    finally:
        queue.shutdown()


def test_canonical_indexes_are_restored_after_move(native):
    from store.embedded_backend import EmbeddedBackend
    from store.config import StoreConfig
    backend = EmbeddedBackend(native.db, native.conn, "ladybug", StoreConfig(data_dir=native.path))
    # Existing extension provisioning is used. No embedding model or Docker.
    backend._ensure_index("Chunk")
    backend._ensure_index("ChunkVec")
    backend._ensure_index("NodeVec")
    before = {(r["table_name"], r["index_name"]) for r in backend.run("CALL show_indexes() RETURN *", raw=True)}
    stage(backend)
    after = {(r["table_name"], r["index_name"]) for r in backend.run("CALL show_indexes() RETURN *", raw=True)}
    assert after == before
    args = dict(source=SOURCE, target=TARGET, topic=TOPIC, run=RUN)
    tc.release(backend, **args)
    assert backend.fulltext_search("synthetic", 10, {"owner_id": TARGET})
    assert backend.node_vector_search([1.0, -0.5, 0.25, 0.0], 10, owner=TARGET)


@pytest.mark.parametrize("stop", ["BEGIN TRANSACTION", "COMMIT"])
def test_killed_process_recovers_prepared_or_committed_intent(native, stop):
    import subprocess
    expected = tc.preflight(native, SOURCE, TOPIC)["fingerprint"]
    path = native.path
    native.close()
    code = f'''
import sys,os
sys.path[:0] = {[str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parent)]!r}
from test_topic_transfer import RawStore,SOURCE,TARGET,TOPIC,STAMP
from api.topic_conversion import stage
s=RawStore({path!r})
s._FTS_INDEX,s._VEC_INDEX,s._NODE_VEC_INDEX='chunk_ft','chunk_vec','node_vec'
original=s.run
def run(q,*a,**kw):
    if q == {stop!r}:
        if q == 'COMMIT': original(q,*a,**kw)
        os._exit(87)
    return original(q,*a,**kw)
s.run=run
stage(s,source=SOURCE,target=TARGET,topic=TOPIC,run={RUN!r},expected={expected!r},stamp=STAMP)
'''
    result = subprocess.run([sys.executable,"-c",code],capture_output=True,text=True,timeout=40)
    assert result.returncode == 87, result.stderr
    native.reopen()
    assert tc.fenced(native, SOURCE)
    assert tc.status(native, source=SOURCE,target=TARGET,topic=TOPIC,run=RUN)["status"] == (
        "prepared" if stop == "BEGIN TRANSACTION" else "graph_committed")
    assert tc.stage(native,source=SOURCE,target=TARGET,topic=TOPIC,run=RUN,expected=expected,stamp=STAMP)["status"] == "graph_committed"
    tc.release(native,source=SOURCE,target=TARGET,topic=TOPIC,run=RUN)


def test_jobs_enqueued_before_move_recheck_the_fence_at_execution(native):
    from api.jobs import JobQueue
    from threading import Event
    expected = tc.preflight(native,SOURCE,TOPIC)["fingerprint"]
    entered, release = Event(), Event()
    executed = []
    def guard(job):
        if not job.kind.startswith("topic-conversion."):
            tc.check_write(native, tc.request_owner.get(), tc.request_payload.get())
    queue = JobQueue(on_before_run=guard)
    try:
        queue.submit("hold",lambda: (entered.set(),release.wait(10),{})[-1])
        assert entered.wait(2)
        move = queue.submit("topic-conversion.stage",lambda: tc.stage(native,source=SOURCE,target=TARGET,
            topic=TOPIC,run=RUN,expected=expected,stamp=STAMP))
        owner = tc.request_owner.set(SOURCE);payload=tc.request_payload.set({"id":"concept:a"})
        try: stale=queue.submit("ingest",lambda: executed.append(True) or {})
        finally: tc.request_payload.reset(payload);tc.request_owner.reset(owner)
        release.set();assert move._done.wait(20);assert stale._done.wait(20)
        assert move.status == "done";assert stale.status == "error";assert executed == []
    finally: release.set();queue.shutdown()
