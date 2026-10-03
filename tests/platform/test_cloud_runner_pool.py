"""A Cloud runner must not expose development credentials or stop live CI."""
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('cloud_runner_pool', ROOT / 'scripts/cloud_runner/pool.py')
pool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pool)


def test_container_does_not_receive_admin_or_development_credentials():
    secrets = {key: 'private-test-value' for key in (
        'VINC_GITHUB_RUNNER_ADMIN_TOKEN', 'GH_TOKEN', 'VINC_MCP_READONLY_KEY',
        'LINEAR_API_KEY', 'VINC_AZURE_CLIENT_SECRET')}
    with mock.patch.dict(pool.os.environ, secrets, clear=True):
        args = pool.container_args('vinc-cloud-test-1', 'test-image')
    assert 'private-test-value' not in '\n'.join(args)
    assert '--privileged' not in args
    assert '--volume' not in args and '--mount' not in args
    assert '--pid' not in args


def test_credential_bearing_proxy_is_rejected():
    with mock.patch.dict(pool.os.environ, {'HTTPS_PROXY': 'http://name:private-test-value@proxy:8080'}, clear=True):
        with pytest.raises(RuntimeError, match='credential-bearing proxy'):
            pool.container_args('vinc-cloud-test-1', 'test-image')


def test_busy_runner_is_never_deleted():
    with mock.patch.object(pool, 'all_runners', return_value=[{'id': 1, 'name': 'vinc-cloud-test-1', 'busy': True}]), \
         mock.patch.object(pool, 'api') as api:
        assert pool.clean_registration('vinc-cloud-test-1') is False
    api.assert_not_called()


def test_cleanup_only_deletes_the_exact_owned_runner():
    runners = [{'id': 1, 'name': 'vinc-wsl-linux', 'busy': False},
               {'id': 2, 'name': 'vinc-cloud-test-1', 'busy': False},
               {'id': 3, 'name': 'vinc-cloud-test-10', 'busy': False}]
    with mock.patch.object(pool, 'all_runners', return_value=runners), mock.patch.object(pool, 'api') as api:
        assert pool.clean_registration('vinc-cloud-test-1') is True
    api.assert_called_once_with('DELETE', '/actions/runners/2')


def test_registration_becoming_busy_during_cleanup_blocks_stop():
    runners = [{'id': 2, 'name': 'vinc-cloud-test-1', 'busy': False}]
    with mock.patch.object(pool, 'all_runners', return_value=runners), \
         mock.patch.object(pool, 'api', side_effect=pool.APIError(409)):
        assert pool.clean_registration('vinc-cloud-test-1') is False


def test_disable_preserves_selector_changed_by_someone_else(tmp_path):
    (tmp_path / 'previous-selector.json').write_text(json.dumps({
        'Vinculums/Vinc': {'exists': False, 'desired': '["self-hosted","linux","vinc-cloud-test"]'}}))
    with mock.patch.object(pool, 'RUNTIME', tmp_path), \
         mock.patch.object(pool, 'api', return_value={'value': '["ubuntu-latest"]'}) as api:
        with pytest.raises(RuntimeError, match='changed elsewhere'):
            pool.disable({})
    api.assert_called_once_with('GET', '/actions/variables/CI_LINUX_RUNNERS', repo='Vinculums/Vinc')


def test_stop_refuses_active_ci_even_if_no_runner_is_busy(tmp_path):
    with mock.patch.object(pool, 'RUNTIME', tmp_path), \
         mock.patch.object(pool, 'state', return_value={}), \
         mock.patch.object(pool, 'own_runners', return_value=[]), \
         mock.patch.object(pool, 'disable'), \
         mock.patch.object(pool, 'api', return_value={'workflow_runs': [{'id': 99, 'status': 'queued'}]}), \
         mock.patch.object(pool.os, 'kill') as kill, \
         mock.patch.object(pool.sys, 'argv', ['pool.py', 'stop']):
        with pytest.raises(RuntimeError, match='active runs/jobs'):
            pool.main()
    kill.assert_not_called()


def test_vfs_capacity_refuses_space_for_only_one_image_copy():
    calls = [subprocess.CompletedProcess([], 0, stdout='vfs\n'),
             subprocess.CompletedProcess([], 0, stdout=str(3 * 1024 ** 3))]
    with mock.patch.object(pool, 'docker', side_effect=calls), \
         mock.patch.object(pool.shutil, 'disk_usage', return_value=mock.Mock(free=8 * 1024 ** 3)):
        with pytest.raises(RuntimeError, match='Four runners require'):
            pool.capacity('test-image')


def test_drain_inspects_ci_in_both_repositories():
    with mock.patch.object(pool, 'api', return_value={'workflow_runs': []}) as api:
        assert pool.active_runs() == []
    assert {(call.kwargs['repo'], call.args[1].split('/')[3]) for call in api.call_args_list} == {
        ('Vinculums/Vinc', 'ci.yml'), ('Vinculums/Vinc-homepage', 'account-ci.yml'),
        ('Vinculums/Vinc-homepage', 'site-ci.yml')}


def test_partial_enable_failure_restores_both_prior_values(tmp_path):
    values = {'Vinculums/Vinc': 'old-vinc', 'Vinculums/Vinc-homepage': 'old-homepage'}
    record = {'prefix': 'vinc-cloud-12345678'}
    def fake_api(method, path, body=None, repo=None):
        if method == 'GET':
            return {'value': values[repo]}
        if method == 'PATCH':
            if repo.endswith('homepage') and body['value'] == pool.selector(record):
                raise pool.APIError(403)
            values[repo] = body['value']
    with mock.patch.object(pool, 'RUNTIME', tmp_path), \
         mock.patch.object(pool, 'verify_group'), \
         mock.patch.object(pool, 'same_process', return_value=True), \
         mock.patch.object(pool, 'own_runners', return_value=[{'status': 'online'}] * 4), \
         mock.patch.object(pool, 'api', side_effect=fake_api):
        with pytest.raises(pool.APIError):
            pool.enable(record)
    assert values == {'Vinculums/Vinc': 'old-vinc', 'Vinculums/Vinc-homepage': 'old-homepage'}
    assert not (tmp_path / 'previous-selector.json').exists()


def test_group_rejects_an_extra_repository():
    replies = [{'name': 'vinc-cloud-12345678', 'visibility': 'selected', 'allows_public_repositories': False},
               {'repositories': [{'id': 1}, {'id': 2}, {'id': 3}], 'total_count': 3},
               {'id': 1}, {'id': 2}]
    with mock.patch.object(pool, 'api', side_effect=replies):
        with pytest.raises(RuntimeError, match='two selected private repositories'):
            pool.verify_group({'prefix': 'vinc-cloud-12345678', 'group_id': 10})
