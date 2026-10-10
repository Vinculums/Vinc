"""Synthetic, authenticated native /graph handler for staged-vs-full measurements.

Run: python -B scripts/measure_staged_view.py CHECKOUT OWNED_OUTPUT_DIR
Creates fresh synthetic stores only. Loopback SessionAuth is real; account/hub signed hops are not simulated.
Keep running while app/dev/view-staged-measure.mjs runs. No real credentials/data are used.
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

from api.server import VincApp
from api.auth import SessionAuth
import urllib.parse
from collections import OrderedDict
apps = {size:VincApp(cfg=cfg,store=store,auth=SessionAuth('synthetic-view-loading'),quiet_boot=True)
        for size,(store,cfg) in stores.items()}
measures = OrderedDict()
original_graph=q.graph
def graph(**kwargs):
    started=time.perf_counter(); value=original_graph(**kwargs)
    measures[id(value)]={'queryMs':(time.perf_counter()-started)*1000}
    while len(measures)>64:measures.popitem(last=False)
    return value
q.graph=graph
try:
    from api import graph_loading
except ImportError:
    graph_loading=None
if graph_loading is not None:
    original_encode=graph_loading.encode_result
    def encode_result(result):
        started=time.perf_counter();wire=original_encode(result)
        measures[id(wire[1])]={**measures.get(id(result[1]),{}),
                             'preparedSerializeMs':(time.perf_counter()-started)*1000}
        while len(measures)>64:measures.popitem(last=False)
        return wire
    graph_loading.encode_result=encode_result

class Capture:
    def __init__(self):self.wfile,self.headers=io.BytesIO(),{}
    def send_response(self,code):self.code=code
    def send_header(self,key,value):self.headers[key]=value
    def end_headers(self):pass

class Handler(_Handler):
    @property
    def app(self):
        args=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return apps.get(int(args.get('size',['200'])[0]),apps[200])
    def log_message(self,*args):pass
    def _send(self,code,obj):
        captured=Capture();started=time.perf_counter();_Handler._send(captured,code,obj)
        meta={**measures.get(id(obj),{}),'serializeMs':(time.perf_counter()-started)*1000,
              'bytes':len(captured.wfile.getvalue()),'preview':bool(obj.get('partial')) if isinstance(obj,dict) else False}
        body=captured.wfile.getvalue()
        args=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        size=int(args.get('size',['200'])[0])
        if code==200 and (isinstance(obj,bytes) or obj.get('executed')):
            name='preview' if isinstance(obj,dict) and obj.get('partial') else 'full'
            (output/f'http-{name}-{size}.json').write_bytes(body)
        self.send_response(code)
        for key,value in captured.headers.items():self.send_header(key,value)
        self.send_header('X-Synthetic-Measures',json.dumps(meta))
        self.end_headers();self.wfile.write(body)

server=HTTPServer(('127.0.0.1',0),Handler)
(output/'backend.json').write_text(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','prep':preparations}))
print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}'}),flush=True)
try:server.serve_forever()
finally:
    server.server_close()
    for app in apps.values():app.close()
