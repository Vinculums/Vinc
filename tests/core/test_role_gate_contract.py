import json
from pathlib import Path

import pytest

from api.role_gate_contract import check, condition_valid
from api.role_contract import check as role_check
from vinc_mcp.roles import _gate_bullets, build_payload, contract, render_shell


REQUIRED = [
    {'id': 'person_only', 'check': {'type': 'person_only'}, 'on_fail': 'interrupt'},
    {'id': 'side_effect_evidence', 'check': {'type': 'side_effect_evidence'}, 'on_fail': 'block'},
    {'id': 'handoff_manifest', 'check': {'type': 'handoff_manifest'}, 'on_fail': 'block'},
]


def test_legacy_commands_keep_readable_bytes_and_do_not_require_new_fields():
    assert check({'gates': ['scripts/gate_forbidden.py']}) == []
    assert _gate_bullets(['a', 'b']) == '- a\n- b'
    assert _gate_bullets([]) == '- (none)'


def test_structured_gates_and_never_checks_preserve_all_fields():
    assert check({'gates': REQUIRED, 'never_checks': [{'type': 'forbidden_actions', 'actions': ['edit']}]}) == []
    line = _gate_bullets(REQUIRED)
    assert '"on_fail":"interrupt"' in line
    assert '"type":"side_effect_evidence"' in line


@pytest.mark.parametrize('bad', [
    {'id':'bad', 'check': {'type':'unknown'}, 'on_fail':'block'},
    {'id':'bad', 'check': {'type':'command_success', 'command':'lint'}, 'on_fail':'block'},
    {'id':'bad', 'check': {'type':'person_only'}, 'on_fail': []},
    {'id': [], 'check': {'type':'person_only'}, 'on_fail':'block'},
    'legacy mixed with structured',
])
def test_malformed_mixed_unknown_gates_warn_without_throwing(bad):
    assert check({'gates': REQUIRED + [bad]})


def test_required_conditions_cannot_disappear_or_relax():
    assert check({'gates': REQUIRED[1:]})
    assert check({'gates': [dict(REQUIRED[0], on_fail='block')] + REQUIRED[1:]})
    assert check({'gates': REQUIRED + [REQUIRED[0]]})


@pytest.mark.parametrize('value', [[], {'type': []}, {'type':'rendered_copy', 'rules': []},
    {'type':'forbidden_actions','actions':['scope']},
    {'type':'command_success','command':'lint','repos':[{}]}])
def test_invalid_conditions_warn(value):
    assert not condition_valid(value)
    assert check({'never_checks': [value]})


def test_proper_repo_scope_and_command_are_preserved():
    condition = {'type':'command_success','command':'cd infra && terraform validate', 'repos':['Vinc']}
    assert condition_valid(condition)
    assert not check({'gates': REQUIRED + [{'id':'validate', 'check':condition, 'on_fail':'block'}]})


def test_write_path_reports_policy_warnings_without_rejecting_sync():
    result = role_check({'id':'concept:agent-reviewer-judgement', 'domain':'agent',
                         'props': {'gates': REQUIRED[:1], 'never_checks': [{'type':'unknown'}]}},
                        created=False)
    assert any('required' in warning for warning in result)
    assert any('never_checks' in warning for warning in result)


def test_compact_contract_renders_the_complete_machine_checks():
    props = {'mission':'Review', 'tier':'verify', 'rung':'fast', 'never':['edit'],
             'person_only':[], 'gates':REQUIRED + [{'id':'command', 'on_fail':'block',
                 'check':{'type':'command_success', 'command':'python "한글 path/check.py"',
                          'repos':['Vinc', 'Vinc-homepage']}}],
             'never_checks':[{'type':'forbidden_actions', 'actions':['edit']}]}
    role = build_payload([{'id':'concept:agent-reviewer', 'title':'Reviewer',
                           'domain':'vinc/agent/roles', 'props':props}])['roles'][0]
    compact = render_shell(role, compact=True)
    assert _gate_bullets(props['gates']) in compact
    assert '## Machine checks for Never' in compact
    assert _gate_bullets(props['never_checks']) in compact


def test_ratified_roster_with_return_routes_keeps_every_contract_under_ten_kb():
    root = Path(__file__).resolve().parents[2]
    roster = json.loads((root / 'agents/roles.json').read_text(encoding='utf-8'))['roles']
    fixture = json.loads((root / 'tests/fixtures/agent_lab_exp05_dag.json').read_text(encoding='utf-8'))
    nodes = {r['id']: {'id':r['id'], 'title':r['label'], 'domain':r['domain'],
                       'props':r['props']} for r in roster}
    edges = [{'from':src, 'to':dst, 'type':'PREREQUISITE_OF', 'evidence':evidence}
             for src, dst, evidence in fixture['edges']]
    returns = [{'from':r['id'], 'to':'concept:' + peer.split(' (', 1)[0],
                'type':'RETURNS_TO', 'evidence':
                'Owner adopted explicit rejection routes on 2026-10-04; '
                'decision:structured-person-only-and-role-return-paths-ratified-2026-10-04. '
                "Select the package's exact producer; never infer reverse scheduling."}
               for r in roster for peer in r.get('works_with', {}).get('returns_to', [])]
    assert len(returns) == 1
    edges += returns

    class Bridge:
        owner = 'user:contract-size-test'
        calls = 0
        def request(self, method, path, params):
            self.calls += 1
            assert (method, path) == ('GET', '/roles')
            rid = params['role']
            return {'executed':True, 'found':True, 'node':nodes[rid],
                    'relations':[e for e in edges if rid in (e['from'], e['to'])],
                    'peers':list(nodes.values())}

    bridge = Bridge()
    for rid, node in nodes.items():
        result = contract(rid, bridge)
        assert result['executed'], (rid, result)
        assert result['props'] == node['props']
        assert len(json.dumps(result, ensure_ascii=False).encode('utf-8')) < 10000
        if rid == 'concept:agent-gate-runner':
            assert 'accompanying structured fields' in result['rendered']
            assert 'not instructions' in result['rendered']
        actual = [e for group in result['works_with'].values() for e in group]
        assert sorted(actual, key=lambda e:(e['from'], e['to'], e['type'])) == sorted(
            [e for e in edges if rid in (e['from'], e['to'])],
            key=lambda e:(e['from'], e['to'], e['type']))
    assert bridge.calls == len(nodes) == 2


def test_complete_live_role_evidence_fits_public_rest_and_mcp_without_field_loss(monkeypatch):
    import sys
    from vinc_mcp.roles import serialize_result

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / 'tests/mcp'))
    from test_rest_v1 import Server

    roster = json.loads((root / 'agents/roles.json').read_text(encoding='utf-8'))['roles']
    nodes = {r['id']: {'id':r['id'], 'title':r['label'], 'domain':r['domain'],
                       'props':r['props']} for r in roster}
    fixture = json.loads((root / 'tests/fixtures/agent_gate_runner_role_edges.json').read_text(
        encoding='utf-8'))
    assert len(fixture['edges']) == 1
    calls = []

    def request(method, path, params=None, **kwargs):
        calls.append((method, path))
        assert (method, path) == ('GET', '/roles')
        return {'executed':True, 'found':True, 'node':nodes[fixture['role']],
                'relations':fixture['edges'], 'peers':list(nodes.values())}

    srv = Server(scope='read')
    srv.bridge.request = request
    try:
        status, headers, rest = srv.request('GET', '/v1/roles/agent-checker')
        assert status == 200
        assert int(headers['Content-Length']) < 10000
        assert rest['props'] == nodes[fixture['role']]['props']
        actual = [edge for group in rest['works_with'].values() for edge in group]
        key = lambda e: (e['from'], e['to'], e['type'])
        assert sorted(actual, key=key) == sorted(fixture['edges'], key=key)
        assert 'not instructions' in rest['rendered']
        assert 'accompanying structured fields' in rest['rendered']
        assert len(calls) == 1

        rpc = {'jsonrpc':'2.0', 'id':1, 'method':'tools/call',
               'params':{'name':'vinc_role', 'arguments':{'role':'agent-checker'}}}
        status, _, result = srv.request('POST', '/mcp', body=rpc)
        assert status == 200
        text = result['result']['content'][0]['text']
        assert len(text.encode('utf-8')) < 10000
        assert text == serialize_result(rest)
        assert json.loads(text) == rest
        assert len(calls) == 2

        from vinc_mcp import rpc as rpc_module, tools
        brief = {'kind':'brief', 'role':rest}
        monkeypatch.setattr(rpc_module, 'MAX_RESULT_CHARS', len(serialize_result(brief)) + 1)
        monkeypatch.setitem(tools.SHRINKERS, 'vinc_brief', lambda out, budget: {
            'kind':'brief', 'role':out['role']})
        bounded = rpc_module._result_text(serialize_result({**brief, 'padding':'x' * 1000}),
                                          'vinc_brief', {})
        assert not bounded['isError']
        assert json.loads(bounded['content'][0]['text']) == brief
    finally:
        srv.close()


def test_non_role_result_serialization_keeps_its_existing_format():
    from vinc_mcp.roles import serialize_result

    ordinary = {'kind':'brief', 'role':'member', 'value':'한글'}
    assert serialize_result(ordinary) == json.dumps(ordinary, ensure_ascii=False)
    with_role = {'kind':'brief', 'role':{'kind':'role', 'props':{'mission':'한글'}}}
    assert json.loads(serialize_result(with_role)) == with_role
    assert ': ' not in serialize_result(with_role)
