import json

import pytest

from api import fragment, query, sync
from api.server import VincApp
from store.backend import open_store
from store.config import load_config
from store import pkns

OWNER = 'user:preview'


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('MEMORY_STORE_BACKEND', 'embedded')
    monkeypatch.setenv('MEMORY_DATA_DIR', str(tmp_path / 'store'))
    monkeypatch.setenv('MEMORY_EMBEDDING_MODEL', 'local-en')
    cfg = load_config()
    backend = open_store(cfg)
    backend.provision(dim=cfg.embedding_dim)
    yield backend
    backend.close()


def seed(store, *, owner=OWNER, etype='derives_from'):
    result = fragment.merge_fragment({'owner': owner, 'nodes': [
        {'id':'record:source','type':'record','title':'Evidence','domain':'test'},
        {'id':'concept:target','type':'concept','title':'Target','domain':'test'},
        {'id':'decision:target','type':'decision','title':'Decision','domain':'test'},
    ], 'edges':[{'from':'record:source','to':'concept:target','type':etype},
                {'from':'record:source','to':'decision:target','type':'inherits_from'},
                {'from':'decision:target','to':'concept:target','type':'about'}]}, store=store)
    assert not result.get('partial'), result


def count(store, text, params=None):
    return store.run(text, params or {})[0]['c']


def test_record_edge_removes_one_qualified_link_without_deleting_nodes(store):
    seed(store)
    before = count(store, 'MATCH (n:Record) RETURN count(n) AS c')
    result = query.delete_edge('record:source','derives_from','concept:target',owner=OWNER,store=store)
    assert result['found'] and result['deleted']['edges'] == 1, result
    assert count(store,'MATCH (n:Record) RETURN count(n) AS c') == before
    assert count(store,'MATCH (:Record)-[r:ATTACHES_TO]->(:Decision) RETURN count(r) AS c') == 1
    markers = sync.export_delta(owner=OWNER, since=0, everything=True, store=store)['edge_tombstones']
    assert markers[0]['rel'] == 'ATTACHES_TO:derives_from'


def test_qualified_delete_does_not_remove_a_different_type(store):
    seed(store)
    result = query.delete_edge('record:source','relates_to','concept:target',owner=OWNER,store=store)
    assert not result['found']
    assert count(store,'MATCH (:Record)-[r:ATTACHES_TO]->(:Concept) RETURN count(r) AS c') == 1
    result = query.delete_edge('record:source','ATTACHES_TO','decision:target',owner=OWNER,store=store)
    assert result['deleted']['edges'] == 1


def test_record_tombstone_applies_and_recreation_clears_it(store):
    seed(store)
    marker = {'src':'record:source','dst':'concept:target','rel':'ATTACHES_TO:derives_from',
              'deleted_at':'2026-10-04T01:00:00Z'}
    sync.apply_delta(owner=OWNER,bundle={'nodes': [], 'edges': [], 'edge_tombstones':[marker]},store=store)
    assert count(store,'MATCH (:Record)-[r:ATTACHES_TO]->(:Concept) RETURN count(r) AS c') == 0
    result = fragment.merge_fragment({'owner':OWNER,'nodes':[], 'edges':[
        {'from':'record:source','to':'concept:target','type':'derives_from'}]},store=store)
    assert not result.get('partial'), result
    assert sync.export_delta(owner=OWNER,since=0,everything=True,store=store)['edge_tombstones'] == []


def test_qualified_tombstone_preserves_other_type_and_wrong_owner(store):
    seed(store)
    for owner, rel in [('user:other','ATTACHES_TO:derives_from'), (OWNER,'ATTACHES_TO:relates_to')]:
        sync.apply_delta(owner=owner,bundle={'nodes':[],'edges':[], 'edge_tombstones':[
            {'src':'record:source','dst':'concept:target','rel':rel,'deleted_at':'2026-10-04T01:00:00Z'}]},store=store)
    assert count(store,'MATCH (:Record)-[r:ATTACHES_TO]->(:Concept) RETURN count(r) AS c') == 1
    assert not query.delete_edge('record:source','ATTACHES_TO','concept:target',owner='user:other',store=store)['found']


def test_preview_is_complete_and_does_not_mutate_then_matches_delete(store):
    seed(store)
    before = sync.export_delta(owner=OWNER,since=0,everything=True,store=store)
    preview = query.delete_node('record:source',owner=OWNER,store=store,dry_run=True)
    assert preview['executed'] and preview['found'] and 'deleted' not in preview, preview
    expected = preview['preview']
    assert expected['complete'] and expected['edge_count'] == len(expected['edges']) == 3
    assert {e['type'] for e in expected['edges']} == {'ATTACHES_TO','IN_DOMAIN'}
    assert sync.export_delta(owner=OWNER,since=0,everything=True,store=store) == before
    actual = query.delete_node('record:source',owner=OWNER,store=store)
    assert actual['deleted']['edges'] == expected['edge_count']


def test_concept_preview_includes_incoming_and_self_edge_once(store):
    seed(store)
    fragment.merge_fragment({'owner':OWNER,'nodes':[], 'edges':[
        {'from':'concept:target','to':'concept:target','type':'derives_from'}]},store=store)
    preview = query.delete_node('concept:target',owner=OWNER,store=store,dry_run=True)
    assert preview['executed'], preview
    expected = preview['preview']
    assert len(expected['edges']) == expected['edge_count'] == 4
    assert any(e['dir']=='in' and e['type']=='ATTACHES_TO' for e in expected['edges'])
    actual = query.delete_node('concept:target',owner=OWNER,store=store)
    assert actual['deleted']['edges'] == expected['edge_count']


def test_preview_wrong_owner_and_invalid_mode_do_not_write(store):
    seed(store)
    assert not query.delete_node('record:source',owner='user:other',store=store,dry_run=True)['found']
    assert not query.delete_node('record:source',owner=OWNER,store=store,dry_run='false')['executed']
    assert count(store,'MATCH (n:Record) RETURN count(n) AS c') == 1


def test_preview_does_not_enqueue_a_write_job(store):
    seed(store)
    class NoJobs:
        def run_sync(self,*a,**k):
            pytest.fail('Preview must not create a job')
    app = object.__new__(VincApp)
    app.store, app.jobs = store, NoJobs()
    def read(fn):
        reader = store.reader_view()
        try:
            return 200, fn(reader)
        finally:
            reader.close()
    app._read = read
    status, result = app.write_delete({'owner':OWNER,'node':'record:source','dry_run':True})
    assert status == 200 and result['preview']['edge_count'] == 3


def test_exported_record_deletion_replays_on_a_second_real_store(store, tmp_path):
    seed(store)
    replica = open_store(load_config({'data_dir': str(tmp_path / 'replica')}))
    replica.provision(dim=load_config().embedding_dim)
    try:
        initial = sync.export_delta(owner=OWNER, everything=True, store=store)
        sync.apply_delta(owner=OWNER, bundle=initial, store=replica)
        assert count(replica, 'MATCH (:Record)-[r:ATTACHES_TO]->(:Concept) RETURN count(r) AS c') == 1
        query.delete_edge('record:source', 'derives_from', 'concept:target', owner=OWNER, store=store)
        exported = sync.export_delta(owner=OWNER, everything=True, store=store)
        sync.apply_delta(owner=OWNER, bundle=exported, store=replica)
        assert count(replica, 'MATCH (:Record)-[r:ATTACHES_TO]->(:Concept) RETURN count(r) AS c') == 0
        assert count(replica, 'MATCH (:Record)-[r:ATTACHES_TO]->(:Decision) RETURN count(r) AS c') == 1
        assert count(replica, 'MATCH (n:Record) RETURN count(n) AS c') == 1
    finally:
        replica.close()


def test_preview_redacts_a_foreign_endpoint_even_on_a_forged_relation(store):
    seed(store)
    foreign = 'user:foreign'
    secret = 'concept:private-endpoint'
    fragment.merge_fragment({'owner': foreign, 'nodes': [
        {'id': secret, 'type': 'concept', 'title': 'Private endpoint'}]}, store=store)
    store.run('MATCH (a:Record), (b:Concept) WHERE a.record_id=$src AND b.concept_id=$dst '
              'CREATE (a)-[:ATTACHES_TO {type:"derives_from"}]->(b)',
              {'src': pkns.key(OWNER, 'record:source'), 'dst': pkns.key(foreign, secret)})
    preview = query.delete_node('record:source', owner=OWNER, dry_run=True, store=store)
    assert preview['executed'] and preview['preview']['complete'], preview
    assert preview['preview']['edge_count'] == 4
    assert secret not in json.dumps(preview)
    redacted = [e for e in preview['preview']['edges'] if e.get('endpoint_unavailable')]
    assert len(redacted) == 1 and redacted[0]['to'] is None
