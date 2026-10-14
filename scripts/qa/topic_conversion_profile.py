"""Bounded conversion resource probe. Only fresh synthetic local stores are accepted."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import tempfile
import time
import tracemalloc
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from api import topic_conversion as legacy
from api.topic_conversion_batches import Batches
from store import topic_batch_manifest as manifest
from store.backend import open_store
from store.config import load_config
from store.embedded_schema import meta_table_specs,node_table_specs
from store.persistence import DurableAck
from store.scoped_topic_inventory import read_scoped_inventory
from store.topic_manifest import TopicManifest
from store.topic_transfer import plan_transfer,TransferRefused

SOURCE='user:synthetic-conversion-profile'
TARGET='team:00000000-0000-4000-8000-000000000007'
TOPIC='vinc/synthetic-profile'
SPECS={**node_table_specs(),**meta_table_specs()}


class SeedWriter:
    def __init__(self,store):self.store,self.pending=store,0
    def run(self,query,params=None,*,raw=True):
        if not self.pending:self.store.run('BEGIN TRANSACTION',raw=True)
        out=self.store.run(query,params,raw=True)
        self.pending+=1
        if self.pending==128:self.finish()
        return out
    def finish(self):
        if self.pending:
            self.store.run('COMMIT',raw=True)
            self.pending=0


def put(store,table,key,**fields):
    spec=SPECS[table]
    value={column:None for column in spec['cols']}
    value.update({spec['pk']:key},**fields)
    setters=', '.join(f'n.{column}=${column}' for column in value if column!=spec['pk'])
    store.run(f"CREATE (n:{table} {{{spec['pk']}:${spec['pk']}}})"+(' SET '+setters if setters else ''),value,raw=True)


def seed(store,scale,dim):
    text=('합성 자료입니다. Unicode 본문과 이력은 보존합니다.\n'*180)
    emb=[((i%23)-11)/23 for i in range(dim)]
    put(store,'Domain',TOPIC)
    for i in range(3001*scale):
        put(store,'Record',SOURCE+'\x1frecord:profile-'+str(i),owner_id=SOURCE,domain=TOPIC,
            title='Synthetic record '+str(i),props=json.dumps({'history':[{'at':'1905','value':'preserve'}]}),
            rev=7,sync_seq=i+1,updated_at='2020-01-01T00:00:00Z')
        if i and i%1000==0:print(json.dumps({'phase':'synthetic-seed-records','completed':i}),flush=True)
    for i in range(1000*scale):
        doc='synthetic-profile-doc-'+str(i)
        logical='concept:doc.'+doc
        key=SOURCE+'\x1f'+logical
        put(store,'Concept',key,owner_id=SOURCE,domain=TOPIC,title='Synthetic document '+str(i),rev=3,sync_seq=i+1)
        put(store,'_DocMeta',doc,owner_id=SOURCE,title='Synthetic document '+str(i),origin='synthetic',
            content_hash=hashlib.sha256((text+text).encode()).hexdigest(),chunk_count=2,needs_embed=0,sync_seq=i+1)
        for index in range(2):
            chunk=doc+'#'+str(index)
            put(store,'Chunk',chunk,owner_id=SOURCE,doc_id=doc,text=text,lex='synthetic document',
                chunk_index=index,char_start=index*len(text),char_end=(index+1)*len(text),char_count=len(text),embedding=emb,source_file='synthetic')
            put(store,'ChunkVec',chunk+'#vec',owner_id=SOURCE,doc_id=doc,chunk_id=chunk,seq=0,
                embedding=emb,encoder_runtime='synthetic-runtime')
        put(store,'NodeVec',SOURCE+'#'+logical,owner_id=SOURCE,node_id=logical,kind='concept',
            text='Synthetic document',model='synthetic-model',embedding=emb,encoder_runtime='synthetic-runtime')
        store.run('MATCH (a:Concept),(b:Domain) WHERE a.concept_id=$id AND b.name=$topic CREATE (a)-[:IN_DOMAIN]->(b)',
                  {'id':key,'topic':TOPIC},raw=True)
        if i%10==0:
            store.run('MATCH (a:Concept),(b:Domain) WHERE a.concept_id=$id AND b.name=$topic CREATE (a)-[:IN_DOMAIN]->(b)',
                      {'id':key,'topic':TOPIC},raw=True)
        if i and i%250==0:print(json.dumps({'phase':'synthetic-seed-documents','completed':i}),flush=True)
    put(store,'_SyncMeta','seq:'+SOURCE,value=str(4001*scale))


def seed_observed_shape(store,scale,dim):
    """Use observed type counts with synthetic content and a declared valid-ownership variant."""
    embedding=[((i%23)-11)/23 for i in range(dim)]
    text='합성 문서 본문과 Unicode 이력은 그대로 보존합니다.\n'*80
    counts={'Concept':371,'Decision':545,'Episode':856,'Record':1669}
    docs,chunk_count,vector_count=489*scale,3997*scale,6922*scale
    logicals=[]
    chunks=[]
    concepts=[]
    put(store,'Domain',TOPIC)
    def member(label,logical):
        key=SOURCE+'\x1f'+logical
        title='Synthetic '+logical
        put(store,label,key,owner_id=SOURCE,domain=TOPIC,title=title,rev=7,sync_seq=len(logicals)+1,
            props=json.dumps({'history':[{'at':'1905','value':'preserve'}]}),updated_at='2020-01-01T00:00:00Z')
        logicals.append((label,logical,title))
        if label=='Concept':concepts.append(logical)
        store.run(f"MATCH (a:{label}),(b:Domain) WHERE a.{SPECS[label]['pk']}=$id AND b.name=$topic CREATE (a)-[:IN_DOMAIN]->(b)",
                  {'id':key,'topic':TOPIC},raw=True)
    for label,count in counts.items():
        for i in range(count*scale):member(label,label.lower()+':shape-'+str(i))
    for i in range(docs):
        doc='synthetic-shape-doc-'+str(i)
        logical='concept:doc.'+doc
        member('Concept',logical)
        number=chunk_count//docs+int(i<chunk_count%docs)
        put(store,'_DocMeta',doc,owner_id=SOURCE,title='Synthetic document '+str(i),origin='synthetic',
            content_hash=hashlib.sha256((text*number).encode()).hexdigest(),chunk_count=number,needs_embed=0,sync_seq=i+1)
        for index in range(number):
            chunk=doc+'#'+str(index)
            put(store,'Chunk',chunk,owner_id=SOURCE,doc_id=doc,text=text,lex='synthetic document',
                chunk_index=index,char_start=index*len(text),char_end=(index+1)*len(text),char_count=len(text),source_file='synthetic')
            chunks.append((chunk,doc,logical))
            store.run('MATCH (a:Chunk),(b:Concept) WHERE a.chunk_id=$chunk AND b.concept_id=$id CREATE (a)-[:MENTIONS {method:$method}]->(b)',
                      {'chunk':chunk,'id':SOURCE+'\x1f'+logical,'method':'title'},raw=True)
    for i in range(vector_count):
        chunk,doc,_=chunks[i%len(chunks)]
        sequence=i//len(chunks)
        put(store,'ChunkVec',chunk+'#vec-'+str(sequence),owner_id=SOURCE,doc_id=doc,chunk_id=chunk,seq=sequence,
            embedding=embedding,encoder_runtime='synthetic-runtime')
    for label,logical,title in logicals[:3928*scale]:
        put(store,'NodeVec',SOURCE+'#'+logical,owner_id=SOURCE,node_id=logical,kind=label.lower(),text=title,
            model='synthetic-model',embedding=embedding,encoder_runtime='synthetic-runtime')
    for label,logical,title in logicals[:843*scale]:
        put(store,'_SyncBase',SOURCE+'\x1f'+logical,label=label,fields=json.dumps({'title':title}),
            base_rev=7,base_updated_at='2020-01-01T00:00:00Z')
    # At least the observed closure size, including identical parallel relations.
    for i in range((11502-3930-3997)*scale):
        left,right=concepts[i%len(concepts)],concepts[(i+1)%len(concepts)]
        store.run('MATCH (a:Concept),(b:Concept) WHERE a.concept_id=$left AND b.concept_id=$right CREATE (a)-[:DERIVES_FROM {evidence:$evidence,confidence:$confidence}]->(b)',
                  {'left':SOURCE+'\x1f'+left,'right':SOURCE+'\x1f'+right,'evidence':'synthetic preserved direction','confidence':0.75},raw=True)
    put(store,'_SyncMeta','seq:'+SOURCE,value=str(len(logicals)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scale',type=int,choices=(1,5,10),required=True)
    parser.add_argument('--indexes',action='store_true')
    parser.add_argument('--trace-python',action='store_true',help='Track Python allocations; report the instrumentation overhead separately')
    parser.add_argument('--shape',choices=('legacy-budget','vinc-g0'),default='legacy-budget')
    args=parser.parse_args()
    os.environ['VINC_PK_NAMESPACED']='on'
    os.environ['VINC_TOPIC_CONVERSION']='1'
    os.environ['VINC_DURABLE_ACK']=''
    started=time.monotonic()
    report={'synthetic':True,'operating_data_opened':False,'scale':args.scale,'dimension':1024,
            'buffer_pool_mb':512,'max_batch_rows':128,'max_batch_bytes':4*1024*1024,'indexes':args.indexes,
            'python_allocation_tracing':args.trace_python,'passed':False}
    report['shape']=args.shape
    if args.shape=='vinc-g0':report['ownership_variant']='synthetic owned metadata for all489 identities; actual11-identity ambiguity remains unmodified'
    cgroup=Path('/sys/fs/cgroup')
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        if line.startswith('0::'):cgroup=cgroup/line[3:].lstrip('/')
    for key in ('memory.max','memory.swap.max','cpu.max'):
        location=cgroup/key
        report[key]=location.read_text().strip() if location.exists() else None
    store=None
    if args.trace_python:tracemalloc.start()
    try:
        with tempfile.TemporaryDirectory(prefix='vinc-synthetic-conversion-profile-') as directory:
            root=Path(directory)
            live=root/'local'/'synthetic.db'
            store=open_store(load_config({'data_dir':str(live),'buffer_pool_mb':512}))
            if store is None:raise RuntimeError('synthetic_store_unavailable')
            store.provision(dim=1024)
            store.drop_vector_index();store.drop_node_vector_index()
            store._load_ext('fts');store._drop_index(f"CALL DROP_FTS_INDEX('Chunk','{store._FTS_INDEX}')",'synthetic profile')
            writer=SeedWriter(store)
            (seed_observed_shape if args.shape=='vinc-g0' else seed)(writer,args.scale,1024)
            writer.finish()
            report['seed_seconds']=round(time.monotonic()-started,3)
            if args.indexes:
                for label in ('Chunk','ChunkVec','NodeVec'):store._ensure_index(label)
            run=str(uuid4())
            sealed_path=root/'sealed.jsonl'
            begin=time.monotonic()
            with TopicManifest(root/'inventory.sqlite',deadline=time.monotonic()+180) as scratch:
                with legacy._inventory_read_transaction(store):
                    inventory=read_scoped_inventory(store,source=SOURCE,target=TARGET,topic=TOPIC,manifest=scratch)
                    plan=plan_transfer(inventory.snapshot,source=SOURCE,target=TARGET,topic=TOPIC)
                    if not plan.ready:raise TransferRefused('synthetic_dependency_blocked')
                    header=manifest.prepare(scratch,plan,run=run,stamp='2026-10-14T10:00:00Z',indexes=legacy._index_inventory(store),store=store)
                    sealed=manifest.seal(scratch,sealed_path,header)
                report.update(selected_rows=len(plan.selected),inventory_rows=inventory.retained_rows,
                              payload_bytes=inventory.retained_bytes,manifest_bytes=sealed['bytes'],scratch_bytes=scratch.path.stat().st_size)
            report['inventory_seconds']=round(time.monotonic()-begin,3)
            snapshot=str(root/'persistent'/'synthetic.db');Path(snapshot).parent.mkdir()
            durable=DurableAck(lambda:store,str(live),snapshot_path=snapshot)
            def publish(journal):
                durable(SimpleNamespace(kind='topic-conversion.batch'),{})
                return durable.sequence
            flow=Batches(store,root=durable.root,publisher=publish,run=run,source=SOURCE,target=TARGET,topic=TOPIC,
                         qualified_indexes=args.indexes)
            begin=time.monotonic()
            flow.initialize(sealed_path,sealed['sha256'],plan.before_hash)
            max_step=0.0
            while flow.status()['phase']!='verifying':
                if time.monotonic()-started>3600:raise RuntimeError('synthetic_work_budget_reached')
                step=time.monotonic();flow.step();max_step=max(max_step,time.monotonic()-step)
            report.update(stage_seconds=round(time.monotonic()-begin,3),max_step_seconds=round(max_step,3),
                          batches=flow.status()['batches'],phase=flow.status()['phase'],durable=durable.detail())
            flow.verify()
            report.update(passed=True,stage_seconds=round(time.monotonic()-begin,3),max_step_seconds=round(max_step,3),
                          batches=flow.status()['batches'],durable=durable.detail(),
                          live_bytes=sum(p.stat().st_size for p in live.parent.glob('*') if p.is_file()),
                          generation_bytes=sum(p.stat().st_size for p in Path(durable.root).rglob('*') if p.is_file()))
            store.close();store=None
    except (Exception,KeyboardInterrupt) as error:
        report['error_class']=type(error).__name__
        report['synthetic_error_detail']=str(error)[:500]
        report['error_code']=str(error).split(':',1)[0] if isinstance(error,TransferRefused) else 'synthetic_profile_failed'
        if hasattr(error,'reason'):report['limit_reason']=error.reason
        if hasattr(error,'details'):report['limit_details']=error.details
    finally:
        if store is not None:store.close()
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        report['process_peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        report['python_tracemalloc_peak_bytes']=tracemalloc.get_traced_memory()[1] if args.trace_python else None
        peak=cgroup/'memory.peak'
        report['cgroup_peak_bytes']=int(peak.read_text()) if peak.exists() else None
        print(json.dumps(report,sort_keys=True),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
