"""Synthetic native integration: staged MVCC, auth, scope and query cleanup."""
import json
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest
from api import query as q
from api.auth import SessionAuth
from api.graph_loading import ReadControl
from api.server import VincApp, make_server
from store.backend import open_store
from store.config import StoreConfig
from store import pkns
from store.embedded_backend import _import_driver

pytestmark = pytest.mark.skipif(_import_driver()[0] is None, reason='native driver unavailable')


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('VINC_PK_NAMESPACED', '1')
    cfg = StoreConfig(data_dir=str(tmp_path/'db'), max_db_size=1024**3, buffer_pool_mb=128)
    store = open_store(cfg); store.provision(dim=cfg.embedding_dim)
    for owner in ('user:synthetic', 'team:synthetic'):
        rows = [{'id': pkns.key(owner, f'concept:n-{i}'), 'label': f'Synthetic {i}',
                 'domain': f'topic-{i}', 'props': json.dumps({'unicode': '한글 ❖'})} for i in range(128)]
        store.run('UNWIND $rows AS row CREATE (c:Concept {concept_id:row.id, title:row.label, '
                  'domain:row.domain, props:row.props, owner_id:$owner})', {'rows':rows, 'owner':owner})
    auth = SessionAuth('synthetic-token')
    app = VincApp(cfg=cfg, store=store, auth=auth, quiet_boot=True)
    try:
        yield app
    finally:
        app.close()


def test_native_preview_fanout_is_bounded_and_final_contract_is_exact(app):
    p = {'owner':'user:synthetic', 'layout':'hub'}
    code, preview = app.read_graph({**p, 'stage':'preview'})
    assert code == 200 and len(preview['nodes']) == 128
    token = preview['snapshot']['token']
    assert app.read_graph({**p, 'owner':'team:synthetic', 'stage':'complete', 'snapshot':token})[0] == 404
    assert app.read_graph({**p, 'cone':'0.5', 'stage':'complete', 'snapshot':token})[0] == 404
    code, full = app.read_graph({**p, 'stage':'complete', 'snapshot':token})
    assert code == 200 and full == q.graph(owner=p['owner'], layout='hub', store=app.store)
    assert len(full['nodes']) > 160  # Domain markers belong to the full graph only.


def test_concurrent_write_does_not_change_the_continuation_snapshot(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = q.graph
    def paused(**kwargs):
        entered.set(); assert release.wait(5)
        return original(**kwargs)
    monkeypatch.setattr(q, 'graph', paused)
    p = {'owner':'user:synthetic', 'layout':'hub'}
    try:
        code, preview = app.read_graph({**p, 'stage':'preview'}); assert entered.wait(5)
        app.store.run('CREATE (c:Concept {concept_id:$id,title:$title,owner_id:$owner})',
                      {'id':pkns.key(p['owner'], 'concept:after'), 'title':'After preview', 'owner':p['owner']})
        release.set()
        code, full = app.read_graph({**p, 'stage':'complete', 'snapshot':preview['snapshot']['token']})
        assert code == 200 and all(n['id'] != 'concept:after' for n in full['nodes'])
        assert any(n['id'] == 'concept:after' for n in original(owner=p['owner'], store=app.store)['nodes'])
    finally:
        release.set()


def test_http_requires_current_auth_for_each_stage_and_cancellation(app):
    server = make_server(app, host='127.0.0.1', port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def request(stage, token=None, auth=True):
        params = {'owner':'user:synthetic', 'layout':'hub', 'stage':stage}
        if token: params['snapshot'] = token
        req = Request(f'http://127.0.0.1:{server.server_port}/graph?{urlencode(params)}')
        if auth: req.add_header('Authorization', 'Bearer synthetic-token')
        try:
            with urlopen(req, timeout=5) as response: return response.status, json.load(response)
        except HTTPError as error: return error.code, json.load(error)
    try:
        assert request('preview', auth=False)[0] == 401
        code, preview = request('preview'); token = preview['snapshot']['token']
        assert code == 200
        for stage in ('complete', 'cancel'):
            assert request(stage, token, auth=False)[0] == 401
        code, full = request('complete', token)
        assert code == 200 and full.get('executed') and not full.get('graphSnapshotPending')
        assert full == q.graph(owner='user:synthetic', layout='hub', store=app.store)
        assert request('cancel', token)[0] == 200
        assert request('complete', token)[0] == 404
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def test_reader_query_deadline_rolls_back_and_leaves_writer_and_other_reader_usable(app):
    reader = app.store.reader_view()
    try:
        started = time.monotonic()
        with pytest.raises((TimeoutError, RuntimeError)):
            with reader.graph_snapshot_scope(ReadControl(time.monotonic()+.02)):
                reader.run('MATCH (a:Concept), (b:Concept), (c:Concept), (d:Concept) '
                           'RETURN sum(length(a.title)+length(b.title)+length(c.title)+length(d.title)) AS total')
        assert time.monotonic()-started < 2, 'native query timeout must interrupt, not just reject a late result'
        assert reader.run('RETURN 1 AS n') == [{'n':1}]
        assert app.store.run('RETURN 2 AS n') == [{'n':2}]
        other = app.store.reader_view()
        try:
            assert other.run('RETURN 3 AS n') == [{'n':3}]
        finally:
            other.close()
    finally:
        reader.close()
