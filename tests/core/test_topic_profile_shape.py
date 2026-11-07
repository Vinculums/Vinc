"""G0-sized synthetic workload matches observed type counts without operating data."""
from collections import Counter
import importlib.util
from pathlib import Path
import re

path=Path(__file__).resolve().parents[2]/'scripts'/'qa'/'topic_conversion_profile.py'
spec=importlib.util.spec_from_file_location('conversion_profile',path)
profile=importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def test_observed_shape_is_complete_and_synthetic():
    class CountOnly:
        def __init__(self):self.nodes,self.edges=Counter(),Counter()
        def run(self,q,p=None,raw=True):
            node=re.search(r'CREATE \(n:(\w+)',q)
            edge=re.search(r'CREATE \(a\)-\[:(\w+)',q)
            if node:
                label=node.group(1);self.nodes[label]+=1
                if label in ('ChunkVec','NodeVec'):assert len(p['embedding'])==1024
                if label=='_SyncBase':assert p['label'] in ('Concept','Decision','Episode','Record')
                if label=='Chunk':assert p['source_file']=='synthetic' and p['char_end']-p['char_start']==len(p['text'])
                assert not p.get('owner_id') or p['owner_id']==profile.SOURCE
            if edge:self.edges[edge.group(1)]+=1
            return []
    store=CountOnly()
    profile.seed_observed_shape(store,1,1024)
    assert store.nodes=={'Concept':862,'Decision':547,'Episode':858,'Record':1672,'_DocMeta':491,
                        'Chunk':4007,'ChunkVec':6933,'NodeVec':3934,'_SyncBase':843,'Domain':1,'_SyncMeta':1}
    assert sum(store.nodes.values())+sum(store.edges.values())>=profile.OBSERVED['rows']
    assert store.edges=={'IN_DOMAIN':3939,'MENTIONS':4007,'DERIVES_FROM':3575}
