"""Topic-independent discovery, using only synthetic nodes and isolated stores."""
import pytest

from api import fragment, query, role_contract, roles as api_roles
from vinc_mcp import roles as mcp_roles, tools
from test_decision_type import _fresh_store, _restore

OWNER = 'user:synthetic-role-discovery'


def role(slug, domain='ai/english/voice/girlfriend', **props):
    contract = dict(mission='Synthetic role mission', tier='execute', rung='fast',
                    never=['change production'], person_only=[], job_title=slug,
                    team='Synthetic team', team_role='member')
    contract.update(props)
    return dict(id='concept:agent-' + slug, type='concept', title=slug, domain=domain,
                one_liner='Synthetic role mission', props=contract)


@pytest.fixture(params=[False, True], ids=['plain', 'namespaced'])
def graph(request, monkeypatch):
    monkeypatch.setenv('VINC_PK_NAMESPACED', 'on' if request.param else 'off')
    store, keep = _fresh_store()
    try:
        yield store
    finally:
        store.close()
        _restore(keep)


def ingest(store, nodes, edges=(), owner=OWNER):
    got = fragment.merge_fragment(dict(owner=owner, nodes=nodes, edges=list(edges)), store=store)
    assert got['executed'], got
    return got


class Bridge:
    owner = OWNER
    def __init__(self, store):
        self.store, self.calls = store, []
    def request(self, method, path, params=None, **kwargs):
        self.calls.append((method, path, params))
        assert (method, path) == ('GET', '/roles')
        return api_roles.read(params.get('role'), owner=params['owner'], store=self.store,
                              limit=params.get('limit'), offset=params.get('offset', 0))


def test_topics_contracts_and_legacy_compatibility(graph):
    nodes = [role('english'), role('deep', 'science/math/teaching/advanced'),
             role('legacy', 'agent'), role('old-deep', 'vinc/agent/team/ops'),
             role('inactive', active=False), role('ordinary', mission=''),
             dict(id='concept:agent-document', type='concept', title='Document',
                  domain='ai/english', props={'doc_id': 'synthetic-doc'}),
             dict(id='concept:agent-orchestration', type='concept', title='Roster', domain='agent'),
             dict(id='concept:ordinary', type='concept', title='Ordinary', domain='agent')]
    nodes[2]['props'] = {}  # incomplete legacy contracts keep their existing visibility
    ingest(graph, nodes)
    expected = {'concept:agent-' + x for x in ('english', 'deep', 'legacy', 'old-deep')}
    got = api_roles.read(owner=OWNER, store=graph)
    assert got['executed'] and got['count'] == 4
    assert {r['id'] for r in got['roles']} == expected
    bridge = Bridge(graph)
    assert {'concept:' + p['name'] for p in mcp_roles.list_prompts(bridge)['prompts']} == expected
    for nid in expected:
        assert api_roles.read(nid, owner=OWNER, store=graph)['found']
        result = tools.call('vinc_role', {'role': nid}, bridge)
        assert result['found'] and result['id'] == nid
        assert mcp_roles.get_prompt(nid.split(':', 1)[1], {}, bridge)['messages']
    for slug in ('inactive', 'ordinary', 'document', 'orchestration'):
        assert not api_roles.read('agent-' + slug, owner=OWNER, store=graph)['found']
        with pytest.raises(ValueError, match='unknown prompt'):
            mcp_roles.get_prompt('agent-' + slug, {}, bridge)


def test_relations_peer_contracts_and_owner_isolation(graph):
    ingest(graph, [role('a'), role('b', 'vinc/agent/roles'), role('disabled', active=False)], [
        {'from': 'concept:agent-a', 'to': 'concept:agent-b', 'type': 'prerequisite_of', 'evidence': 'synthetic'},
        {'from': 'concept:agent-a', 'to': 'concept:agent-disabled', 'type': 'relates_to'}])
    ingest(graph, [role('foreign')], owner='user:other')
    got = api_roles.read('agent-a', owner=OWNER, store=graph)
    assert [e['to'] for e in got['relations']] == ['concept:agent-b']
    assert all(p['id'] != 'concept:agent-disabled' for p in got['peers'])
    roster = api_roles.read(owner=OWNER, store=graph, roster=True, limit=1)
    assert roster['relations'] == got['relations']
    assert not api_roles.read('agent-foreign', owner=OWNER, store=graph)['found']
    assert api_roles.read(owner='user:empty-space', store=graph)['roles'] == []
    result = mcp_roles.get_prompt('agent-a', {}, Bridge(graph))
    assert 'agent-b' in result['messages'][0]['content']['text']


def test_pagination_filters_before_display_and_does_not_truncate(graph, monkeypatch):
    nodes = [role(f'page-{i:03}') for i in range(205)]
    nodes[0]['props'] = {}  # a page can contain no eligible roles and still have a next page
    ingest(graph, nodes)
    first = api_roles.read(owner=OWNER, store=graph, limit=1)
    assert first['roles'] == [] and first['next_offset'] == 1
    seen = []
    offset = 0
    while True:
        page = api_roles.read(owner=OWNER, store=graph, roster=True, limit=100, offset=offset)
        assert page['executed'], page
        assert len(page['roles']) <= 100
        seen.extend(r['id'] for r in page['roles'])
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    assert len(seen) == len(set(seen)) == 204
    bridge = Bridge(graph)
    assert len(mcp_roles.list_prompts(bridge)['prompts']) == 204
    assert len(bridge.calls) == 3
    assert all(c[1] == '/roles' for c in bridge.calls)
    assert not api_roles.read(owner=OWNER, store=graph, roster=True, limit=101)['executed']
    assert not api_roles.read(owner=OWNER, store=graph, offset=-1)['executed']


def test_delete_and_query_errors_are_explicit(graph, monkeypatch):
    ingest(graph, [role('deleted')])
    assert query.delete_node('concept:agent-deleted', owner=OWNER, store=graph)['executed']
    assert api_roles.read(owner=OWNER, store=graph, roster=True)['roles'] == []
    monkeypatch.setattr(query, 'execute', lambda *a, **k: dict(executed=False, error='synthetic outage'))
    assert not api_roles.read(owner=OWNER, store=graph)['executed']
    with pytest.raises(ValueError, match='synthetic outage'):
        mcp_roles.list_prompts(Bridge(graph))


@pytest.mark.parametrize('props', [dict(mission=''), dict(tier='invalid'), dict(rung='opus'),
    dict(never='not a list'), dict(never=[{}]), dict(person_only=[{}]), dict(active=False)])
def test_invalid_contracts_do_not_become_roles(props):
    node = role('synthetic', **props)
    assert not role_contract.is_role(node['id'], node['domain'], node['props'])


def test_broken_page_cursor_is_an_error():
    class Broken:
        def request(self, *a, **k):
            return dict(executed=True, roles=[], next_offset=0)
    with pytest.raises(ValueError, match='pagination'):
        mcp_roles.discover_roles(Broken(), OWNER)


def test_mcp_tool_pagination_reaches_the_role_endpoint():
    class Paged:
        owner = OWNER
        def request(self, method, path, params=None, **kwargs):
            assert path == '/roles'
            assert params == dict(owner=OWNER, limit=100, offset=100)
            return dict(executed=True, roles=[], next_offset=200)
    assert tools.call('vinc_role', dict(limit=100, offset=100), Paged())['next_offset'] == 200


def test_sync_discovery_pages_and_preserves_the_unchanged_role_data(monkeypatch):
    from scripts import agents_sync
    from vinc_mcp import remote
    calls = []
    node = role('sync')
    disabled = role('disabled', active=False)
    def call(name, args):
        calls.append((name, args))
        if name == 'vinc_role':
            return dict(executed=True, roles=[] if args['offset'] == 0 else [node],
                        next_offset=100 if args['offset'] == 0 else None)
        assert name == 'vinc_graph' and args['seed_id'] == node['id']
        return dict(found=True, nodes=[node, disabled], edges=[
            dict(source=node['id'], target=disabled['id'], type='RELATED_TO')])
    saved = []
    monkeypatch.setattr(remote, 'call', call)
    monkeypatch.setattr(agents_sync, 'write_snapshot', saved.append)
    assert agents_sync.cmd_pull() == 0
    assert [a['offset'] for n, a in calls if n == 'vinc_role'] == [0, 100]
    assert saved[0]['roles'][0]['props'] == node['props']
    assert not saved[0]['roles'][0].get('works_with', {}).get('works_beside')


def test_rest_role_pagination_matches_the_committed_openapi_contract():
    from vinc_mcp import openapi_v1
    assert openapi_v1.document() == openapi_v1.generate()
    params = openapi_v1.document()['paths']['/v1/roles']['get']['parameters']
    assert {p['name'] for p in params} == {'limit', 'offset', 'space'}
