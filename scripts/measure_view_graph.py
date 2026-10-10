"""Loopback synthetic q.graph/_send adapter, not the authenticated production handler.

Run: python -B scripts/measure_view_graph.py CHECKOUT OWNED_OUTPUT_DIR
Creates fresh synthetic stores only; keep running while the browser harness runs.
"""
import io, json, pathlib, sys, tempfile, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

root, output = map(pathlib.Path, sys.argv[1:3])
sys.path.insert(0, str(root))
from store.config import StoreConfig
from store.backend import open_store
from store import pkns
from api import query as q
from api.server import _Handler

output.mkdir(exist_ok=True)
owner = 'user:synthetic-view-loading'
stores, preparations = {}, []
for size in (200, 3000):
    folder = tempfile.mkdtemp(prefix='vin437-http-synthetic-', dir=output)
    cfg = StoreConfig(data_dir=str(pathlib.Path(folder) / 'db'), max_db_size=1024**3, buffer_pool_mb=128)
    start = time.perf_counter()
    store = open_store(cfg)
    assert store is not None
    store.provision(dim=cfg.embedding_dim)
    prep = (time.perf_counter() - start) * 1000
    nodes = [dict(id=pkns.key(owner, f'concept:synthetic-{i}'), label=f'Synthetic node {i}',
                  domain=f'synthetic/topic-{i%8}', props=json.dumps({'note': 'Synthetic detail '*20,
                  'unicode': '합성 ✓', 'nested': {'enabled': True, 'value': None}}),
                  ts=f'2026-10-{i%9+1:02}T00:00:00Z') for i in range(size)]
    store.run('UNWIND $rows AS row CREATE (c:Concept {concept_id:row.id, title:row.label, '
              'domain:row.domain, props:row.props, owner_id:$owner, created:row.ts})', {'rows':nodes, 'owner':owner})
    edges = [dict(a=nodes[i]['id'], b=nodes[(i+j)%size]['id']) for i in range(size) for j in (1,8)]
    store.run('UNWIND $rows AS row MATCH (a:Concept {concept_id:row.a}), '
              '(b:Concept {concept_id:row.b}) CREATE (a)-[:RELATED_TO {evidence:$e}]->(b)',
              {'rows':edges, 'e':'Synthetic relationship evidence 합성'})
    stores[size] = store, cfg
    preparations.append({'size':size, 'openProvisionMs':prep})

class Capture:
    def __init__(self): self.wfile, self.headers = io.BytesIO(), {}
    def send_response(self, code): self.code = code
    def send_header(self, key, value): self.headers[key] = value
    def end_headers(self): pass

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        args = parse_qs(urlparse(self.path).query)
        size = int(args.get('size', ['200'])[0])
        if urlparse(self.path).path != '/graph' or size not in stores:
            self.send_error(404); return
        store, cfg = stores[size]
        stages = []
        original_execute, original_layout = q.execute, q.layout_positions
        def execute(plan, *a, **kw):
            start = time.perf_counter(); result = original_execute(plan, *a, **kw)
            stages.append({'name':plan.kind, 'ms':(time.perf_counter()-start)*1000})
            return result
        def layout(*a, **kw):
            start=time.perf_counter(); result=original_layout(*a, **kw)
            stages.append({'name':'layout', 'ms':(time.perf_counter()-start)*1000}); return result
        q.execute, q.layout_positions = execute, layout
        try:
            start = time.perf_counter()
            payload = q.graph(owner=owner, layout='hub', cone=.7, store=store, cfg=cfg)
            query_ms = (time.perf_counter()-start)*1000
        finally: q.execute, q.layout_positions = original_execute, original_layout
        captured = Capture()
        start = time.perf_counter(); _Handler._send(captured, 200, payload)
        serialization_ms = (time.perf_counter()-start)*1000
        body = captured.wfile.getvalue()
        assert json.loads(body) == payload
        (output/f'http-graph-{size}.json').write_bytes(body)
        meta = {'queryMs':query_ms, 'serializeMs':serialization_ms, 'bytes':len(body), 'stages':stages}
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('X-Synthetic-Measures',json.dumps(meta))
        self.end_headers(); self.wfile.write(body)

server = HTTPServer(('127.0.0.1', 0), Handler)
(output/'backend.json').write_text(json.dumps({'url':f'http://127.0.0.1:{server.server_port}', 'prep':preparations}))
print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}', 'prep':preparations}), flush=True)
try: server.serve_forever()
finally:
    server.server_close()
    for store, _ in stores.values(): store.close()
