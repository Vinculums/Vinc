"""외부 서비스 없이 합성 DB로 app/dev/agent-discovery-check.mjs를 검증한다."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 앱 코드를 불러오기 전에 임시 스토어를 정한다. 사용자 데이터는 열지 않는다.
data = Path(tempfile.mkdtemp(prefix='synthetic-agent-discovery-'))
os.environ.update(MEMORY_STORE_BACKEND='embedded', MEMORY_DATA_DIR=str(data / 'store'),
                  MEMORY_EMBEDDING_MODEL='local-en', VINC_PK_NAMESPACED='on')
from store.backend import open_store
from store.config import load_config
from api import fragment
from api.auth import SessionAuth
from api.server import VincApp, make_server

OWNER = 'user:synthetic-ui'
TOKEN = 'synthetic-local-test-token'

def node(slug, topic='ai/english/voice/girlfriend', **props):
    contract = dict(mission='Synthetic mission', tier='execute', rung='fast', never=[],
                    person_only=[], job_title=slug, team='Synthetic team')
    contract.update(props)
    return dict(id='concept:agent-' + slug, type='concept', title=slug,
                domain=topic, props=contract)

cfg = load_config()
store = open_store(cfg)
store.provision(dim=cfg.embedding_dim)
nodes = [node('aaa-ordinary-%03d' % i, mission='') for i in range(101)]
nodes += [node('page-%03d' % i) for i in range(105)]
nodes += [node('english'), node('legacy', 'agent'), node('old', 'vinc/agent/team/ops'),
          node('disabled', active=False),
          dict(id='concept:agent-orchestration', type='concept', title='Roster', domain='agent')]
result = fragment.merge_fragment(dict(owner=OWNER, nodes=nodes, edges=[]), store=store)
assert result['executed'], result
assert fragment.merge_fragment(dict(owner='user:foreign', nodes=[node('foreign')], edges=[]),
                               store=store)['executed']
app = VincApp(cfg=cfg, store=store, auth=SessionAuth(TOKEN),
              profile_path=data / 'synthetic-profile.yml', quiet_boot=True)
server = make_server(app, host='127.0.0.1', port=0)
print(json.dumps(dict(base='http://127.0.0.1:%s' % server.server_address[1], owner=OWNER,
                      token=TOKEN, expected_count=108, store=str(data))), flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
    app.close()
