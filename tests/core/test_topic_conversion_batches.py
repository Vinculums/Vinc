"""Real0.21.2 transactions and durable generations, synthetic data only."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_topic_conversion import native
from test_topic_transfer import SOURCE, TARGET, TOPIC, STAMP, RawStore
from test_topic_batch_manifest import RUN, prepared
from api import topic_conversion as legacy
from api.topic_conversion_batches import Batches
from store import durable_generation as dg
from store.topic_transfer import TransferRefused, read_snapshot, plan_transfer, transformed_snapshot, fingerprint


class Publication:
    def __init__(self, store, root):
        self.store, self.root, self.failed = store, root, False
    def __call__(self, journal):
        if self.failed:
            raise RuntimeError("synthetic_publication_failure")
        # The synthetic fixture has one writer and no indexes/readers. Production
        # integration must use DurableAck's existing guarded snapshot method.
        self.store.run("CHECKPOINT", raw=True)
        return dg.publish(self.root, self.store.path, sequence=dg.next_sequence(self.root))["sequence"]


def fixture(native,tmp_path,conventions_policy="retain",**limits):
    disk,_,plan,header,path,sealed=prepared(native,tmp_path,conventions_policy=conventions_policy)
    disk.close()
    root=tmp_path/"durable.db.generations"
    publisher=Publication(native,root)
    flow=Batches(native,root=root,publisher=publisher,run=RUN,source=SOURCE,target=TARGET,topic=TOPIC,**limits)
    from store.topic_batch_manifest import authorized_hash
    flow.initialize(path,sealed["sha256"],authorized_hash(plan.before_hash,conventions_policy),
                    conventions_policy=conventions_policy)
    return flow,publisher,header


def finish(flow):
    for _ in range(300):
        if flow.status()["phase"]=="verifying":
            return flow.verify()
        flow.step()
    pytest.fail("bounded synthetic run did not finish")


def add_rejections(native):
    from test_topic_rejection_history import rejection, outside_rejection
    from test_topic_transfer import seed, insert
    data = seed()
    rejection(data)
    outside_rejection(data)
    extra = data["nodes"]["Concept"][-1]
    data["nodes"] = {"Concept": [extra]}
    data["meta"] = {"_MergeRejection": data["meta"]["_MergeRejection"]}
    data["edges"] = []
    insert(native, data)


@pytest.mark.parametrize("with_rejections", [False, True])
def test_native_bounded_rows_preserve_every_field_duplicate_and_other_source_row(native,tmp_path,with_rejections):
    if with_rejections:
        add_rejections(native)
    before=read_snapshot(native)
    expected=transformed_snapshot(before,plan_transfer(before,source=SOURCE,target=TARGET,topic=TOPIC),stamp=STAMP)
    flow,_,_=fixture(native,tmp_path,max_rows=2)
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)
    assert finish(flow)["status"]=="graph_committed"
    observed=read_snapshot(native)
    observed["meta"]["_SyncMeta"]=[r for r in observed["meta"]["_SyncMeta"] if not r["key"].startswith(legacy.PREFIX)]
    assert fingerprint(observed)==fingerprint(expected)
    assert flow.status()["batches"]>3
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)


def test_native_bounded_move_preserves_import_provenance(native,tmp_path):
    origin = "https://synthetic.invalid/import/original.pdf"
    native.run("MATCH (n:Chunk) WHERE n.doc_id='doc1' SET n.source_file=$origin", {"origin": origin})
    native.run("MATCH (n:_DocMeta) WHERE n.doc_id='doc1' SET n.origin=$origin", {"origin": origin})
    before = read_snapshot(native)
    expected = transformed_snapshot(before, plan_transfer(before, source=SOURCE, target=TARGET, topic=TOPIC), stamp=STAMP)
    flow, _, _ = fixture(native, tmp_path, max_rows=2)
    assert finish(flow)["status"] == "graph_committed"
    observed = read_snapshot(native)
    observed["meta"]["_SyncMeta"] = [r for r in observed["meta"]["_SyncMeta"] if not r["key"].startswith(legacy.PREFIX)]
    assert fingerprint(observed) == fingerprint(expected)
    assert observed["nodes"]["Chunk"][0]["source_file"] == origin
    assert observed["meta"]["_DocMeta"][0]["origin"] == origin


@pytest.mark.parametrize("terminal", ["active", "cancelled"])
def test_startup_restores_only_verified_terminal_visibility_without_writing(native,tmp_path,terminal):
    from api.topic_conversion_batches import restore_terminal_visibility
    flow, _, _ = fixture(native, tmp_path)
    if terminal == "active":
        finish(flow)
        flow.release(account_active=True)
    else:
        flow.cancel()
    native._topic_conversion_terminals.clear()
    assert legacy.fenced(native, SOURCE)
    before = fingerprint(read_snapshot(native))
    assert restore_terminal_visibility(native, flow.root) == {"verified": 1, "blocked": 0}
    assert not legacy.fenced(native, SOURCE)
    assert not legacy.fenced(native, TARGET)
    assert fingerprint(read_snapshot(native)) == before
    flow._receipt_path(flow._journal()).write_text("corrupt")
    assert restore_terminal_visibility(native, flow.root) == {"verified": 0, "blocked": 1}
    assert legacy.fenced(native, SOURCE) and legacy.fenced(native, TARGET)
    assert not legacy.fenced(native, "user:unrelated")


def test_startup_does_not_resume_prepared_business_batches(native,tmp_path):
    from api.topic_conversion_batches import restore_terminal_visibility
    flow, _, _ = fixture(native, tmp_path)
    before = fingerprint(read_snapshot(native))
    assert restore_terminal_visibility(native, flow.root) == {"verified": 0, "blocked": 0}
    assert legacy.fenced(native, SOURCE) and legacy.fenced(native, TARGET)
    assert fingerprint(read_snapshot(native)) == before


def test_app_startup_restores_cancelled_run_when_new_conversion_feature_is_off(native,tmp_path,monkeypatch):
    from api.server import VincApp
    from store.config import load_config
    flow, _, _ = fixture(native, tmp_path)
    flow.cancel()
    native._topic_conversion_terminals.clear()
    monkeypatch.setenv("VINC_STORE_SNAPSHOT", str(tmp_path / "durable.db"))
    monkeypatch.delenv("VINC_TOPIC_CONVERSION", raising=False)
    monkeypatch.delenv("VINC_DURABLE_ACK", raising=False)
    app = VincApp(load_config({"data_dir": str(native.path)}), store=native)
    try:
        assert app._conversion_restored == {"verified": 1, "blocked": 0}
        assert not legacy.fenced(native, SOURCE)
    finally:
        app.jobs.shutdown(drain=True)
        app.roots.close()
        app._graph_reads.close()


@pytest.mark.parametrize("point",["before_ledger","before_commit","after_commit"])
def test_native_failure_resumes_from_the_atomic_ledger_without_repeating_creates(native,tmp_path,point):
    flow,_,_=fixture(native,tmp_path,max_rows=1)
    with pytest.raises(RuntimeError,match="injected"):
        flow.step(fail_at=point)
    if point=="after_commit":
        assert flow.status()["status"]=="durability_pending"
        assert legacy._get(native,RUN)["batches"]==1
    else:
        assert legacy._get(native,RUN)["batches"]==0
    assert finish(flow)["status"]=="graph_committed"


def test_publisher_failure_keeps_fences_and_status_pending_then_resumes_same_batch(native,tmp_path):
    flow,publisher,_=fixture(native,tmp_path,max_rows=1)
    publisher.failed=True
    with pytest.raises(RuntimeError,match="publication_failure"):
        flow.step()
    assert flow.status()["status"]=="durability_pending"
    assert legacy._get(native,RUN)["batches"]==1
    with pytest.raises(TransferRefused,match="unsupported_conversion_inventory_version"):
        legacy.cancel(native,source=SOURCE,target=TARGET,topic=TOPIC,run=RUN)
    publisher.failed=False
    assert finish(flow)["status"]=="graph_committed"


def test_corrupt_manifest_and_changed_original_refuse_before_business_batch(native,tmp_path):
    flow,_,_=fixture(native,tmp_path,max_rows=1)
    from store import topic_batch_manifest
    _,first=next(topic_batch_manifest.instructions(flow.root/"topic-manifests"/(RUN+".jsonl"),kind="node"))
    pk=topic_batch_manifest.SPECS[first["label"]]["pk"]
    native.run(f"MATCH (n:{first['label']}) WHERE n.{pk}=$id SET n.title='changed'",{"id":first["old"]},raw=True)
    # The first selected row is checked against its entire persisted digest.
    with pytest.raises(TransferRefused,match="payload_changed"):
        flow.step()
    assert legacy._get(native,RUN)["business_batches"]==0
    (flow.root/"topic-manifests"/(RUN+".jsonl")).write_text("corrupt")
    with pytest.raises(TransferRefused,match="manifest_invalid"):
        flow.status()


@pytest.mark.parametrize("with_rejections", [False, True])
def test_resume_from_a_missing_local_directory_uses_the_sealed_durable_ledger(native,tmp_path,with_rejections):
    if with_rejections:
        add_rejections(native)
    before=read_snapshot(native)
    expected=transformed_snapshot(before,plan_transfer(before,source=SOURCE,target=TARGET,topic=TOPIC),stamp=STAMP)
    flow,_,_=fixture(native,tmp_path,max_rows=4)
    flow.step()
    lost_local=tmp_path/"new-local"/"synthetic.db"
    assert not lost_local.exists()
    assert dg.restore(flow.root,lost_local,skip_invalid=False).startswith("restored:")
    restored=RawStore(lost_local)
    restored._FTS_INDEX,restored._VEC_INDEX,restored._NODE_VEC_INDEX='chunk_ft','chunk_vec','node_vec'
    try:
        resumed=Batches(restored,root=flow.root,publisher=Publication(restored,flow.root),run=RUN,
                        source=SOURCE,target=TARGET,topic=TOPIC,max_rows=4)
        assert resumed.status()["batches"]==1
        assert legacy.fenced(restored,SOURCE) and legacy.fenced(restored,TARGET)
        assert finish(resumed)["status"]=="graph_committed"
        observed=read_snapshot(restored)
        observed["meta"]["_SyncMeta"]=[r for r in observed["meta"]["_SyncMeta"] if not r["key"].startswith(legacy.PREFIX)]
        assert fingerprint(observed)==fingerprint(expected)
    finally:
        restored.close()


def test_single_row_budget_and_final_durability_failure_remain_fenced(native,tmp_path):
    flow,publisher,_=fixture(native,tmp_path,max_rows=128,max_bytes=1)
    with pytest.raises(TransferRefused,match="single_row_too_large"):
        flow.step()
    assert legacy._get(native,RUN)["business_batches"]==0
    flow.max_bytes=4*1024*1024
    for _ in range(30):
        if flow.status()["phase"]=="verifying":break
        flow.step()
    assert flow.status()["phase"]=="verifying"
    publisher.failed=True
    with pytest.raises(RuntimeError,match="publication_failure"):
        flow.verify()
    assert flow.status()["status"]=="durability_pending"
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)
    publisher.failed=False
    assert flow.step()["status"]=="graph_committed"


def test_parallel_relationship_count_changes_refuse_before_rewiring(native,tmp_path):
    flow,_,_=fixture(native,tmp_path,max_rows=128)
    while flow.status()["phase"]=="creating_target_keys":
        flow.step()
    native.run("MATCH (a:Concept)-[r:DERIVES_FROM]->(b:Concept) WHERE r.evidence=$e DELETE r",
               {"e":"[[doc1]]"},raw=True)
    before=legacy._get(native,RUN)["batches"]
    with pytest.raises(TransferRefused,match="relationship_changed"):
        flow.step()
    assert legacy._get(native,RUN)["batches"]==before
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)


def test_untopiced_outside_source_rows_are_verified_before_activation(native,tmp_path):
    for i in range(3):
        native.run("CREATE (n:Episode {episode_id:$id,owner_id:$owner,title:$title})",
                   {'id':SOURCE+'\x1fepisode:untopiced-'+str(i),'owner':SOURCE,'title':'synthetic outside '+str(i)},raw=True)
    flow,_,_=fixture(native,tmp_path,max_rows=128)
    for _ in range(30):
        if flow.status()['phase']=='verifying':break
        flow.step()
    native.run("MATCH (n:Episode) WHERE n.episode_id=$id SET n.title='changed outside'",
               {'id':SOURCE+'\x1fepisode:untopiced-1'},raw=True)
    with pytest.raises(TransferRefused,match='outside_payload_changed'):
        flow.verify()
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)
    assert legacy._get(native,RUN)['phase']=='verifying'


def test_legacy_stage_refuses_a_new_protocol_without_mutation_or_fence_release(native,tmp_path):
    flow,_,_=fixture(native,tmp_path)
    before=fingerprint(read_snapshot(native))
    with pytest.raises(TransferRefused,match='unsupported_conversion_inventory_version'):
        legacy.stage(native,source=SOURCE,target=TARGET,topic=TOPIC,run=RUN,
                     expected=legacy._get(native,RUN)['expected'],stamp=STAMP)
    assert fingerprint(read_snapshot(native))==before
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)


def test_legacy_cancel_cannot_release_a_new_protocol_prepared_fence(native,tmp_path):
    flow,_,_=fixture(native,tmp_path)
    before=fingerprint(read_snapshot(native))
    with pytest.raises(TransferRefused,match='unsupported_conversion_inventory_version'):
        legacy.cancel(native,source=SOURCE,target=TARGET,topic=TOPIC,run=RUN)
    assert fingerprint(read_snapshot(native))==before
    assert flow.status()['status']=='prepared'
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)


def test_cancel_publication_failure_keeps_fence_until_same_run_receipt(native,tmp_path):
    flow,publisher,_=fixture(native,tmp_path)
    publisher.failed=True
    with pytest.raises(RuntimeError,match='publication_failure'):
        flow.cancel()
    assert flow.status()['status']=='durability_pending'
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)
    publisher.failed=False
    assert flow.cancel()['status']=='cancelled'
    assert not legacy.fenced(native,SOURCE) and not legacy.fenced(native,TARGET)
    # Reopening the process loses the ephemeral permission to expose a terminal run.
    native._topic_conversion_terminals.clear()
    assert legacy.fenced(native,SOURCE)
    assert flow.cancel()['status']=='cancelled'
    assert not legacy.fenced(native,SOURCE)


def test_observed_invalid_terminal_receipt_revokes_visibility(native,tmp_path):
    flow,_,_=fixture(native,tmp_path)
    flow.cancel()
    assert not legacy.fenced(native,SOURCE)
    journal=legacy._get(native,RUN)
    receipt=flow._receipt_path(journal)
    receipt.write_text('{}')
    assert flow.status()['status']=='durability_pending'
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)


def test_terminal_artifact_root_symlink_revokes_visibility(native,tmp_path):
    flow,_,_=fixture(native,tmp_path)
    flow.cancel()
    assert not legacy.fenced(native,SOURCE)
    alias=tmp_path/'synthetic-durable-alias'
    alias.symlink_to(flow.root,target_is_directory=True)
    flow.root=alias
    with pytest.raises(TransferRefused,match='manifest_invalid'):
        flow.status()
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)


def test_cancel_after_business_commit_requires_resume(native,tmp_path):
    flow,_,_=fixture(native,tmp_path,max_rows=1)
    flow.step()
    with pytest.raises(TransferRefused,match='resume_required'):
        flow.cancel()
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)


def test_release_requires_account_activation_and_durable_publication(native,tmp_path):
    flow,publisher,_=fixture(native,tmp_path,max_rows=128)
    with pytest.raises(TransferRefused,match='account_activation_required'):
        flow.release()
    with pytest.raises(TransferRefused,match='run_not_committed'):
        flow.release(account_active=True)
    finish(flow)
    publisher.failed=True
    # The existing graph receipt is reused; failure belongs to the new active ledger.
    with pytest.raises(RuntimeError,match='publication_failure'):
        flow.release(account_active=True)
    assert legacy._get(native,RUN)['status']=='active'
    assert flow.status()['status']=='durability_pending'
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)
    publisher.failed=False
    assert flow.release(account_active=True)['status']=='active'
    assert not legacy.fenced(native,SOURCE) and not legacy.fenced(native,TARGET)
    native._topic_conversion_terminals.clear()
    assert legacy.fenced(native,TARGET)
    assert flow.release(account_active=True)['status']=='active'
    assert not legacy.fenced(native,TARGET)
    with pytest.raises(TransferRefused,match='moved_to_team'):
        legacy.check_write(native,SOURCE,{'id':'concept:a'})
    artifact=flow.root/'topic-manifests'/(RUN+'.jsonl')
    artifact.write_text('corrupt')
    with pytest.raises(TransferRefused,match='manifest_invalid'):
        flow.release(account_active=True)
    assert legacy.fenced(native,TARGET)


def test_release_revalidates_every_payload_before_exposing_target(native,tmp_path):
    flow,_,_=fixture(native,tmp_path,max_rows=128)
    finish(flow)
    native.run("MATCH (n:Concept) WHERE n.owner_id=$owner SET n.title='changed after graph receipt'",
               {'owner':TARGET},raw=True)
    with pytest.raises(TransferRefused,match='verification_failed'):
        flow.release(account_active=True)
    assert legacy._get(native,RUN)['status']=='graph_committed'
    assert legacy.fenced(native,SOURCE) and legacy.fenced(native,TARGET)


@pytest.mark.parametrize('terminal',['active','cancelled'])
def test_terminal_run_restored_to_missing_local_store_stays_hidden_until_receipt(native,tmp_path,terminal,monkeypatch):
    flow,_,_=fixture(native,tmp_path,max_rows=128)
    if terminal=='active':
        finish(flow)
        flow.release(account_active=True)
    else:
        flow.cancel()
    destination=tmp_path/'terminal-new-local'/'synthetic.db'
    assert not destination.exists()
    from store.persistence import restore_newest
    monkeypatch.setenv('VINC_TOPIC_CONVERSION','1')
    monkeypatch.setenv('VINC_DURABLE_ACK','')
    assert restore_newest(str(destination),str(tmp_path/'durable.db')).startswith('restored-generation:')
    restored=RawStore(destination)
    restored._FTS_INDEX,restored._VEC_INDEX,restored._NODE_VEC_INDEX='chunk_ft','chunk_vec','node_vec'
    try:
        assert legacy.fenced(restored,SOURCE) and legacy.fenced(restored,TARGET)
        resumed=Batches(restored,root=flow.root,publisher=Publication(restored,flow.root),run=RUN,
                        source=SOURCE,target=TARGET,topic=TOPIC,max_rows=128)
        result=resumed.release(account_active=True) if terminal=='active' else resumed.cancel()
        assert result['status']==terminal
        assert not legacy.fenced(restored,SOURCE) and not legacy.fenced(restored,TARGET)
    finally:
        restored.close()


def test_verification_uses_vector_coordinates_without_repeated_payload_scans(native,tmp_path,monkeypatch):
    for i in range(16):
        logical='record:vector-'+str(i)
        native.run('CREATE (n:Record {record_id:$id,owner_id:$owner,domain:$topic,title:$title})',
                   {'id':SOURCE+'\x1f'+logical,'owner':SOURCE,'topic':TOPIC,'title':'synthetic '+str(i)})
        native.run('CREATE (n:NodeVec {vec_id:$id,node_id:$node,owner_id:$owner,kind:$kind,embedding:$embedding})',
                   {'id':SOURCE+'#'+logical,'node':logical,'owner':SOURCE,'kind':'record','embedding':[1.0,0.0,0.0,0.0]})
    flow,_,_=fixture(native,tmp_path,max_rows=128)
    for _ in range(30):
        if flow.status()['phase']=='verifying':break
        flow.step()
    from store.topic_manifest import Rows
    original=Rows.where
    scans=[]
    def counted(self,**kwargs):
        if self.label=='NodeVec':scans.append(1)
        return original(self,**kwargs)
    monkeypatch.setattr(Rows,'where',counted)
    assert flow.verify()['status']=='graph_committed'
    assert len(scans)<4, 'Each vector must not start another full disk-backed payload scan'
