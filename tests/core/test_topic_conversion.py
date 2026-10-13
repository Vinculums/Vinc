from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_topic_transfer import RawStore, SOURCE, TARGET, TOPIC, STAMP, seed, insert
from store.embedded_schema import render_embedded_ddl
from store.topic_transfer import TransferRefused, fingerprint, read_snapshot
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
