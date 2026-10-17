"""Production pinned driver/extensions and scoped DurableAck, synthetic only."""
import sys
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_topic_transfer import SOURCE,TARGET,TOPIC,STAMP,seed,insert
from test_topic_batch_manifest import RUN
from test_topic_conversion_batches import finish
from api import topic_conversion as legacy
from api.server import _heal_search_indexes
from api.topic_conversion_batches import Batches
from store import topic_batch_manifest as manifest
from store.backend import open_store
from store.config import load_config
from store.persistence import DurableAck,claim_writer,restore_newest
from store.scoped_topic_inventory import read_scoped_inventory
from store.topic_manifest import TopicManifest
from store.topic_transfer import fingerprint,read_snapshot,plan_transfer,transformed_snapshot,TransferRefused
from store import durable_generation as dg


@pytest.fixture
def indexed(tmp_path,monkeypatch):
    monkeypatch.setenv('VINC_PK_NAMESPACED','on')
    monkeypatch.setenv('VINC_TOPIC_CONVERSION','1')
    monkeypatch.setenv('VINC_DURABLE_ACK','')
    path=tmp_path/'local'/'synthetic.db'
    cfg=load_config({'data_dir':str(path),'buffer_pool_mb':256})
    store=open_store(cfg)
    assert store is not None
    store.provision(dim=4)
    store.drop_vector_index();store.drop_node_vector_index()
    store._load_ext('fts')
    store._drop_index(f"CALL DROP_FTS_INDEX('Chunk','{store._FTS_INDEX}')",'synthetic setup FTS')
    class Raw:
        def run(self,q,params=None):return store.run(q,params,raw=True)
    insert(Raw(),seed())
    for table in ('Chunk','ChunkVec','NodeVec'):store._ensure_index(table)
    snapshot=str(tmp_path/'persistent'/'synthetic.db')
    Path(snapshot).parent.mkdir()
    claim_writer(snapshot)
    durable=DurableAck(lambda:store,str(path),snapshot_path=snapshot)
    def publish(journal):
        durable(SimpleNamespace(kind='topic-conversion.batch'),{})
        return durable.sequence
    yield store,durable,publish
    store.close()


def prepared(store,tmp_path):
    with TopicManifest(tmp_path/'inventory.sqlite') as disk:
        inventory=read_scoped_inventory(store,source=SOURCE,target=TARGET,topic=TOPIC,manifest=disk)
        plan=plan_transfer(inventory.snapshot,source=SOURCE,target=TARGET,topic=TOPIC)
        indexes=legacy._index_inventory(store)
        assert set(indexes)=={'Chunk','ChunkVec','NodeVec'}
        header=manifest.prepare(disk,plan,run=RUN,stamp=STAMP,indexes=indexes,store=store)
        path=tmp_path/'sealed.jsonl'
        sealed=manifest.seal(disk,path,header)
    return plan,path,sealed


def test_queued_native_steps_preserve_payload_and_wait_for_scoped_publication(indexed,tmp_path):
    from api.jobs import JobQueue
    from api.topic_conversion_jobs import ConversionJobs, bounded_step
    store,durable,_=indexed
    before=read_snapshot(store,vocabulary_owner=SOURCE)
    expected=transformed_snapshot(before,plan_transfer(before,source=SOURCE,target=TARGET,topic=TOPIC),stamp=STAMP)
    plan,_,_=prepared(store,tmp_path)
    args=dict(source=SOURCE,target=TARGET,topic=TOPIC,run=RUN,expected=plan.before_hash,stamp=STAMP)
    with pytest.raises(TransferRefused,match='conversion_bounded_executor_required'):
        bounded_step(store,durable,**args)
    assert fingerprint(read_snapshot(store,vocabulary_owner=SOURCE))==fingerprint(before)
    queue=JobQueue()
    coordinator=ConversionJobs(queue,work_seconds=180)
    try:
        coordinator.start(**{k:v for k,v in args.items() if k!='stamp'},
            advance=lambda:bounded_step(store,durable,**args,allow_new=True,qualified_indexes=True))
        coordinator._runs[RUN].thread.join(180)
        assert not coordinator._runs[RUN].thread.is_alive()
        result=coordinator.status(SOURCE,RUN)
        assert result['status']=='graph_committed',result
        assert result['durable_generation']>0
        assert legacy.fenced(store,SOURCE) and legacy.fenced(store,TARGET)
        observed=read_snapshot(store,vocabulary_owner=SOURCE)
        observed['meta']['_SyncMeta']=[r for r in observed['meta']['_SyncMeta'] if not r['key'].startswith(legacy.PREFIX)]
        assert fingerprint(observed)==fingerprint(expected)
        assert set(legacy._index_inventory(store))=={'Chunk','ChunkVec','NodeVec'}
    finally:
        coordinator.close()
        queue.shutdown()


def test_http_resumes_existing_v3_run_with_new_conversion_disabled(indexed,tmp_path,monkeypatch):
    from api.server import VincApp
    store,durable,publish=indexed
    plan,path,sealed=prepared(store,tmp_path)
    flow=Batches(store,root=durable.root,publisher=publish,run=RUN,source=SOURCE,
                 target=TARGET,topic=TOPIC,qualified_indexes=True)
    flow.initialize(path,sealed['sha256'],plan.before_hash)
    monkeypatch.delenv('VINC_TOPIC_CONVERSION',raising=False)
    monkeypatch.setenv('VINC_STORE_SNAPSHOT',durable.snapshot_path)
    app=VincApp(load_config({'data_dir':durable.live_path,'buffer_pool_mb':256}),store=store)
    body=dict(owner=SOURCE,target=TARGET,topic=TOPIC,run=RUN,fingerprint=plan.before_hash)
    try:
        assert app._durable is None and app._conversion_durable is not None
        assert app.write_topic_conversion({**body,'action':'preflight'})[0]==503
        code,result=app.write_topic_conversion({**body,'action':'stage'})
        assert code==200 and result['status']=='pending'
        app._conversion_jobs._runs[RUN].thread.join(180)
        assert not app._conversion_jobs._runs[RUN].thread.is_alive()
        code,result=app.write_topic_conversion({**body,'action':'status'})
        assert code==200 and result['status']=='graph_committed',result
        assert legacy.fenced(store,SOURCE)
        code,result=app.write_topic_conversion({**body,'action':'release'})
        assert code==200 and result['status']=='active',result
        assert app.write_topic_conversion({**body,'action':'status'})[1]['status']=='active'
        assert not legacy.fenced(store,SOURCE) and not legacy.fenced(store,TARGET)
    finally:
        app.close()


# 근거: [[decision:large-topic-conversion-admission-2026-10-16]]
def test_http_admits_a_new_large_topic_through_the_bounded_executor(indexed,monkeypatch):
    import store.scoped_topic_inventory as scoped
    from api import revstamp
    from api.server import VincApp
    store,durable,_=indexed
    monkeypatch.setattr(scoped,'MAX_INVENTORY_ROWS',2)
    # 서버 시계를 기대값과 같은 도장으로 고정한다. 비교 대상은 이동이지 시각이 아니다.
    monkeypatch.setattr(revstamp,'hlc_now',lambda store:STAMP)
    monkeypatch.setenv('VINC_STORE_SNAPSHOT',durable.snapshot_path)
    before=read_snapshot(store,vocabulary_owner=SOURCE)
    expected=transformed_snapshot(before,plan_transfer(before,source=SOURCE,target=TARGET,topic=TOPIC),stamp=STAMP)
    check=legacy.preflight(store,SOURCE,TOPIC)
    assert check['ready'] and check['inventory_version']==3,check
    app=VincApp(load_config({'data_dir':durable.live_path,'buffer_pool_mb':256}),store=store)
    body=dict(owner=SOURCE,target=TARGET,topic=TOPIC,run=RUN,fingerprint=check['fingerprint'])
    try:
        code,result=app.write_topic_conversion({**body,'action':'stage'})
        assert code==200 and result['status']=='pending',result
        app._conversion_jobs._runs[RUN].thread.join(180)
        assert not app._conversion_jobs._runs[RUN].thread.is_alive()
        code,result=app.write_topic_conversion({**body,'action':'status'})
        assert code==200 and result['status']=='graph_committed',result
        assert legacy._get(store,RUN)['inventory_version']==manifest.PROTOCOL
        code,result=app.write_topic_conversion({**body,'action':'release'})
        assert code==200 and result['status']=='active',result
        observed=read_snapshot(store,vocabulary_owner=SOURCE)
        observed['meta']['_SyncMeta']=[r for r in observed['meta']['_SyncMeta'] if not r['key'].startswith(legacy.PREFIX)]
        assert fingerprint(observed)==fingerprint(expected)
    finally:
        app.close()


def test_http_refuses_a_large_topic_with_a_stale_fingerprint(indexed,monkeypatch):
    import store.scoped_topic_inventory as scoped
    from api import revstamp
    from api.server import VincApp
    store,durable,_=indexed
    monkeypatch.setattr(scoped,'MAX_INVENTORY_ROWS',2)
    monkeypatch.setattr(revstamp,'hlc_now',lambda store:STAMP)
    monkeypatch.setenv('VINC_STORE_SNAPSHOT',durable.snapshot_path)
    before=fingerprint(read_snapshot(store,vocabulary_owner=SOURCE))
    app=VincApp(load_config({'data_dir':durable.live_path,'buffer_pool_mb':256}),store=store)
    body=dict(owner=SOURCE,target=TARGET,topic=TOPIC,run=RUN,fingerprint='0'*64)
    try:
        code,result=app.write_topic_conversion({**body,'action':'stage'})
        assert code==200 and result['status']=='pending',result
        app._conversion_jobs._runs[RUN].thread.join(180)
        # 저널을 쓰기 전의 거절은 "prepared" 가 아니라 차단과 사유로 보인다.
        code,result=app.write_topic_conversion({**body,'action':'status'})
        assert code==409 and result=={'status':'blocked','run':RUN,'error':'plan_stale'},result
        assert not legacy.journals(store)
        assert fingerprint(read_snapshot(store,vocabulary_owner=SOURCE))==before
    finally:
        app.close()


def test_scoped_publisher_and_exact_native_indexes_restore_before_graph_receipt(indexed,tmp_path):
    store,durable,publish=indexed
    before=read_snapshot(store,vocabulary_owner=SOURCE)
    expected=transformed_snapshot(before,plan_transfer(before,source=SOURCE,target=TARGET,topic=TOPIC),stamp=STAMP)
    plan,path,sealed=prepared(store,tmp_path)
    blocked=Batches(store,root=durable.root,publisher=publish,run=RUN,source=SOURCE,target=TARGET,topic=TOPIC)
    with pytest.raises(TransferRefused,match='index_execution_unqualified'):
        blocked.initialize(path,sealed['sha256'],plan.before_hash)
    assert not legacy.journals(store)
    flow=Batches(store,root=durable.root,publisher=publish,run=RUN,source=SOURCE,target=TARGET,topic=TOPIC,
                 max_rows=8,qualified_indexes=True)
    flow.initialize(path,sealed['sha256'],plan.before_hash)
    with pytest.raises(RuntimeError,match='after_index_drop'):
        flow.step(fail_at='after_index_drop')
    assert legacy._get(store,RUN)['business_batches']==0
    assert legacy.fenced(store,SOURCE)
    assert finish(flow)['status']=='graph_committed'
    assert set(legacy._index_inventory(store))=={'Chunk','ChunkVec','NodeVec'}
    observed=read_snapshot(store,vocabulary_owner=SOURCE)
    observed['meta']['_SyncMeta']=[r for r in observed['meta']['_SyncMeta'] if not r['key'].startswith(legacy.PREFIX)]
    assert fingerprint(observed)==fingerprint(expected)
    assert durable.publishes>1 and durable.failures==0
    assert store.fulltext_search('synthetic',5,{'owner_id':TARGET})
    assert store.vector_search([0.25,0.5,0.75,-1.0],5,{'owner_id':TARGET})
    flow.release(account_active=True)
    reader = store.reader_view()
    try:
        assert not legacy.fenced(reader, TARGET)
        store._topic_conversion_terminals.clear()
        assert legacy.fenced(reader, TARGET)
        from api.topic_conversion_batches import restore_terminal_visibility
        assert restore_terminal_visibility(store, durable.root) == {'verified': 1, 'blocked': 0}
        assert not legacy.fenced(reader, TARGET)
    finally:
        reader.close()
    assert store.node_vector_search([1.0,-0.5,0.25,0.0],5,owner=TARGET)
    assert not store.fulltext_search('synthetic',5,{'owner_id':SOURCE})
    assert not store.vector_search([0.25,0.5,0.75,-1.0],5,{'owner_id':SOURCE})
    assert not store.node_vector_search([1.0,-0.5,0.25,0.0],5,owner=SOURCE)


@pytest.mark.parametrize('point',['before_commit','after_commit','after_publish'])
@pytest.mark.parametrize('missing_local',[False,True])
def test_process_exit_restores_atomic_ledger_and_same_run(indexed,tmp_path,point,missing_local):
    store,durable,publish=indexed
    before=read_snapshot(store,vocabulary_owner=SOURCE)
    expected=transformed_snapshot(before,plan_transfer(before,source=SOURCE,target=TARGET,topic=TOPIC),stamp=STAMP)
    plan,path,sealed=prepared(store,tmp_path)
    flow=Batches(store,root=durable.root,publisher=publish,run=RUN,source=SOURCE,target=TARGET,topic=TOPIC,
                 max_rows=8,qualified_indexes=True)
    flow.initialize(path,sealed['sha256'],plan.before_hash)
    live=store._cfg.data_dir
    store.close()
    result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'crash',live,str(durable.root),point],
                          capture_output=True,timeout=60)
    assert result.returncode==87,(result.stdout[-1000:],result.stderr[-1000:])
    destination=tmp_path/'restored-local'/'synthetic.db' if missing_local else Path(live)
    if missing_local:
        assert not destination.exists()
        assert restore_newest(str(destination),str(tmp_path/'persistent'/'synthetic.db')).startswith('restored-generation:')
    resumed_store=open_store(load_config({'data_dir':str(destination),'buffer_pool_mb':256}))
    assert resumed_store is not None
    _heal_search_indexes(resumed_store,4,quiet=True)
    assert resumed_store._fts_recovery_path is None
    assert not resumed_store._remnants
    try:
        snapshot=str(tmp_path/'persistent'/'synthetic.db')
        recovered=DurableAck(lambda:resumed_store,str(destination),snapshot_path=snapshot)
        def recovered_publish(journal):
            recovered(SimpleNamespace(kind='topic-conversion.batch'),{})
            return recovered.sequence
        resumed=Batches(resumed_store,root=durable.root,publisher=recovered_publish,run=RUN,
                        source=SOURCE,target=TARGET,topic=TOPIC,max_rows=8,qualified_indexes=True)
        assert legacy.fenced(resumed_store,SOURCE) and legacy.fenced(resumed_store,TARGET)
        journal=legacy._get(resumed_store,RUN)
        assert journal['batches']==int(point=='after_publish' or point=='after_commit' and not missing_local)
        assert finish(resumed)['status']=='graph_committed'
        observed=read_snapshot(resumed_store,vocabulary_owner=SOURCE)
        observed['meta']['_SyncMeta']=[r for r in observed['meta']['_SyncMeta'] if not r['key'].startswith(legacy.PREFIX)]
        assert fingerprint(observed)==fingerprint(expected)
        assert set(legacy._index_inventory(resumed_store))=={'Chunk','ChunkVec','NodeVec'}
    finally:
        resumed_store.close()


if __name__=='__main__':
    if len(sys.argv)!=5 or sys.argv[1]!='crash':
        raise SystemExit('Synthetic crash harness only')
    live,root,point=sys.argv[2:]
    child=open_store(load_config({'data_dir':live,'buffer_pool_mb':256}))
    assert child is not None
    _heal_search_indexes(child,4,quiet=True)
    snapshot=str(Path(root).parent/'synthetic.db')
    durable=DurableAck(lambda:child,live,snapshot_path=snapshot)
    def publish(journal):
        if point=='after_commit':os._exit(87)
        durable(SimpleNamespace(kind='topic-conversion.batch'),{})
        return durable.sequence
    original_run=child.run
    def crash_run(query,params=None,**kwargs):
        if point=='before_commit' and query=='COMMIT':os._exit(87)
        return original_run(query,params,**kwargs)
    child.run=crash_run
    flow=Batches(child,root=root,publisher=publish,run=RUN,source=SOURCE,target=TARGET,topic=TOPIC,
                 max_rows=8,qualified_indexes=True)
    flow.step()
    os._exit(87)
