"""JIT bootstrap must keep ownership, secret delivery and CI drain boundaries."""
import base64
import json
import sys
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/cloud_runner'))
import jit_broker
import jit_crypto
import jit_entrypoint
import jit_pool


@pytest.fixture(scope='module')
def client():
    return jit_crypto.client_key()


def test_encrypted_artifact_contains_no_runner_credentials(client):
    private, public = client
    payload = {'jitconfig': 'private-runner-fixture', 'prefix': 'vinc-cloud-12345678'}
    packet = jit_crypto.seal(public, 'a' * 32, payload)
    assert 'private-runner-fixture' not in json.dumps(packet)
    assert jit_crypto.open_response(private, 'a' * 32, packet) == payload


def test_artifact_replay_to_another_request_is_rejected(client):
    private, public = client
    packet = jit_crypto.seal(public, 'a' * 32, {'jitconfig': 'private-runner-fixture'})
    with pytest.raises(ValueError, match='different request'):
        jit_crypto.open_response(private, 'b' * 32, packet)


def test_ciphertext_tampering_is_rejected(client):
    private, public = client
    packet = jit_crypto.seal(public, 'a' * 32, {'jitconfig': 'private-runner-fixture'})
    data = bytearray(jit_crypto.decode(packet['ciphertext']))
    data[0] ^= 1
    packet['ciphertext'] = jit_crypto.encode(bytes(data))
    from cryptography.exceptions import InvalidTag
    with pytest.raises(InvalidTag):
        jit_crypto.open_response(private, 'a' * 32, packet)


def test_only_original_in_memory_private_key_can_open_artifact(client):
    _, public = client
    other, _ = jit_crypto.client_key()
    packet = jit_crypto.seal(public, 'a' * 32, {'jitconfig': 'private-runner-fixture'})
    with pytest.raises(ValueError):
        jit_crypto.open_response(other, 'a' * 32, packet)


def packet():
    settings = {'agentName': 'vinc-cloud-12345678-1-1', 'agentId': 22,
                'poolId': 1, 'poolName': 'vinc-cloud-12345678',
                'gitHubUrl': 'https://github.com/Vinculums',
                'serverUrl': 'https://pipelines.actions.githubusercontent.com/tenant',
                'workFolder': '_work', 'ephemeral': True}
    files = {'.runner': base64.b64encode(json.dumps(settings).encode()).decode(),
             '.credentials': base64.b64encode(b'fixture').decode(),
             '.credentials_rsaparams': base64.b64encode(b'fixture').decode()}
    return {'prefix': 'vinc-cloud-12345678', 'group_id': 500, 'runner_id': 22,
            'name': settings['agentName'],
            'jitconfig': base64.b64encode(json.dumps(files).encode()).decode()}


def test_entrypoint_rejects_jit_configuration_for_another_runner():
    value = packet()
    value['name'] = 'vinc-cloud-12345678-2-1'
    with pytest.raises(ValueError, match='does not belong'):
        jit_entrypoint.validate(value)


def test_entrypoint_rejects_traversal_files_before_listener_writes_them():
    value = packet()
    files = json.loads(base64.b64decode(value['jitconfig']))
    files['../../owned'] = base64.b64encode(b'fixture').decode()
    value['jitconfig'] = base64.b64encode(json.dumps(files).encode()).decode()
    with pytest.raises(ValueError, match='Unexpected JIT files'):
        jit_entrypoint.validate(value)


def test_group_resource_id_need_not_equal_actions_pool_id():
    value = packet()
    assert jit_entrypoint.validate(value) == value['jitconfig']


def test_entrypoint_rejects_a_credential_server_outside_actions():
    value = packet()
    files = json.loads(base64.b64decode(value['jitconfig']))
    settings = json.loads(base64.b64decode(files['.runner']))
    settings['serverUrl'] = 'https://unexpected.example/tenant'
    files['.runner'] = base64.b64encode(json.dumps(settings).encode()).decode()
    value['jitconfig'] = base64.b64encode(json.dumps(files).encode()).decode()
    with pytest.raises(ValueError, match='service destination'):
        jit_entrypoint.validate(value)


def test_jit_container_gets_no_host_credentials_or_mounts():
    with mock.patch.dict(jit_pool.os.environ, {
        'VINC_CLOUD_RUNNER_ADMIN_TOKEN': 'private-test-value',
        'VINC_GITHUB_RUNNER_PERSONAL_TOKEN': 'private-test-value',
        'VINC_GITHUB_RUNNER_ADMIN_TOKEN': 'private-test-value',
        'GH_TOKEN': 'private-test-value'}, clear=True):
        args = jit_pool.container_args('vinc-cloud-12345678-1-1', 'test-image')
    assert 'private-test-value' not in '\n'.join(args)
    assert '--privileged' not in args and '--mount' not in args and '--volume' not in args
    assert args[args.index('--entrypoint') + 1] == 'python3'


def test_broker_rejects_a_slot_that_already_has_a_live_runner():
    record = {'prefix': 'vinc-cloud-12345678', 'group_id': 500}
    with mock.patch.object(jit_broker, 'members', return_value=[{
        'name': record['prefix'] + '-1-1', 'id': 22, 'busy': True, 'status': 'online'}]), \
         mock.patch.object(jit_broker.pool, 'api') as api:
        with pytest.raises(RuntimeError, match='live runner'):
            jit_broker.configure(record, [{'slot': 1, 'generation': 2}])
    api.assert_not_called()


def test_broker_rejects_a_foreign_runner_in_the_group():
    with mock.patch.object(jit_broker.pool, 'api', return_value={
        'total_count': 1, 'runners': [{'name': 'vinc-wsl-linux', 'id': 22}]}):
        with pytest.raises(RuntimeError, match='outside this pool'):
            jit_broker.members({'prefix': 'vinc-cloud-12345678', 'group_id': 500})


def test_missing_github_secret_probe_never_creates_a_group():
    with mock.patch.dict(jit_broker.os.environ, {}, clear=True), \
         mock.patch.object(jit_broker.pool, 'api') as api:
        assert jit_broker.operate({'operation': 'probe'}) == {'ready': False, 'operation': 'probe'}
    api.assert_not_called()


def test_broker_stop_preserves_busy_runner():
    request = {'operation': 'stop', 'previous': {}}
    with mock.patch.dict(jit_broker.os.environ, {jit_broker.SECRET: 'private-test-value'}), \
         mock.patch.object(jit_broker, 'group_record', return_value={}), \
         mock.patch.object(jit_broker, 'members', return_value=[{'busy': True}]), \
         mock.patch.object(jit_broker, 'restore'), \
         mock.patch.object(jit_broker.pool, 'api') as api:
        with pytest.raises(RuntimeError, match='busy runner'):
            jit_broker.operate(request)
    api.assert_not_called()


def test_host_stop_refuses_active_ci_before_registration_delete():
    with mock.patch.object(jit_pool, 'load', return_value={}), \
         mock.patch.object(jit_pool, 'rpc') as rpc, \
         mock.patch.object(jit_pool.pool, 'active_runs', return_value=[{'status': 'queued'}]), \
         mock.patch.object(jit_pool.os, 'kill') as kill, \
         mock.patch.object(jit_pool.sys, 'argv', ['jit_pool.py', 'stop']):
        with pytest.raises(RuntimeError, match='Active CI'):
            jit_pool.main()
    assert rpc.call_args_list == [mock.call('disable', {})]
    kill.assert_not_called()


def test_artifact_redirect_never_forwards_github_auth_to_azure_sas():
    request = urllib.request.Request('https://api.github.com/repos/Vinculums/Vinc/actions/artifacts/1/zip',
                                     headers={'Authorization': 'Bearer private-test-value'})
    redirected = jit_pool.ArtifactRedirect().redirect_request(
        request, None, 302, '', {}, 'https://downloads.blob.core.windows.net/file?sig=fixture')
    assert redirected.get_header('Authorization') is None


@pytest.mark.parametrize('url', ['http://downloads.blob.core.windows.net/file',
                                 'https://unexpected.example/file'])
def test_artifact_redirect_rejects_downgrade_and_unexpected_host(url):
    request = urllib.request.Request('https://api.github.com/test')
    with pytest.raises(RuntimeError, match='Unexpected artifact'):
        jit_pool.ArtifactRedirect().redirect_request(request, None, 302, '', {}, url)


def test_official_pascal_case_runner_settings_are_accepted():
    value = packet()
    files = json.loads(base64.b64decode(value['jitconfig']))
    settings = json.loads(base64.b64decode(files['.runner']))
    settings = {key[0].upper() + key[1:]: item for key, item in settings.items()}
    settings.update({'AgentId': '22', 'PoolId': '500', 'PoolName': None, 'Ephemeral': 'True'})
    files['.runner'] = base64.b64encode(json.dumps(settings).encode()).decode()
    value['jitconfig'] = base64.b64encode(json.dumps(files).encode()).decode()
    assert jit_entrypoint.validate(value) == value['jitconfig']


def test_exited_zombie_supervisor_is_not_reported_alive():
    fields = ['Z'] + ['0'] * 18 + ['123']
    def read_text(path):
        if str(path).endswith('/stat'):
            return '42 (supervisor) ' + ' '.join(fields)
        return 'boot-fixture'
    with mock.patch.object(Path, 'read_text', read_text):
        assert not jit_pool.pool.same_process({'pid': 42, 'ticks': '123', 'boot': 'boot-fixture'})


def test_string_false_ephemeral_setting_is_rejected():
    value = packet()
    files = json.loads(base64.b64decode(value['jitconfig']))
    settings = json.loads(base64.b64decode(files['.runner']))
    settings['ephemeral'] = 'False'
    files['.runner'] = base64.b64encode(json.dumps(settings).encode()).decode()
    value['jitconfig'] = base64.b64encode(json.dumps(files).encode()).decode()
    with pytest.raises(ValueError, match='does not belong'):
        jit_entrypoint.validate(value)
