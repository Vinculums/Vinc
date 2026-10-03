"""Run organization management on GitHub; export only encrypted JIT settings."""
import json
import hashlib
import os
import pathlib
import re
import sys

import jit_crypto
import pool

SECRET = 'VINC_CLOUD_RUNNER_ADMIN_TOKEN'


def validate(request, request_id):
    required = {'operation', 'prefix', 'public_key', 'group_id', 'slots', 'previous'}
    if set(request) != required or not re.fullmatch(r'[0-9a-f]{32}', request_id):
        raise ValueError('Unexpected broker request')
    if request['operation'] not in ('probe', 'init', 'prepare', 'status', 'enable', 'disable', 'stop'):
        raise ValueError('Unexpected operation')
    if not re.fullmatch(r'vinc-cloud-[0-9a-f]{8}', request['prefix']):
        raise ValueError('Unexpected pool name')
    if request['group_id'] is not None and (type(request['group_id']) is not int or request['group_id'] < 1):
        raise ValueError('Unexpected group ID')
    slots = request['slots']
    if not isinstance(slots, list) or len(slots) > 4:
        raise ValueError('Unexpected slot list')
    seen = set()
    for item in slots:
        if (set(item) != {'slot', 'generation'} or type(item['slot']) is not int
                or item['slot'] not in range(1, 5) or item['slot'] in seen
                or type(item['generation']) is not int or item['generation'] < 1):
            raise ValueError('Unexpected slot generation')
        seen.add(item['slot'])
    if request['operation'] == 'init' and (request['group_id'] is not None or seen != {1, 2, 3, 4}):
        raise ValueError('Initial registration needs exactly four slots and a new group')
    # 잘못된 공개키는 조직 변경 전에 거절한다.
    jit_crypto.seal(request['public_key'], request_id, {})


def group_record(request):
    record = {'prefix': request['prefix'], 'group_id': request['group_id']}
    pool.verify_group(record)
    return record


def members(record):
    response = pool.api('GET', '/actions/runner-groups/' + str(record['group_id']) + '/runners?per_page=100')
    runners = response['runners']
    if response['total_count'] != len(runners) or len(runners) > 4:
        raise RuntimeError('Unexpected runner group membership')
    for runner in runners:
        if not re.fullmatch(re.escape(record['prefix']) + r'-[1-4]-[1-9][0-9]*', runner['name']):
            raise RuntimeError('Runner group contains a runner outside this pool')
    return runners


def public_runner(runner):
    return {key: runner[key] for key in ('id', 'name', 'status', 'busy')}


def prior_selectors():
    previous = {}
    for repo in pool.REPOS:
        try:
            value = pool.api('GET', '/actions/variables/' + pool.VARIABLE, repo=repo)['value']
            previous[repo] = {'exists': True, 'value': value}
        except pool.APIError as error:
            if error.status != 404:
                raise
            previous[repo] = {'exists': False}
    return previous


def restore(record, previous):
    if set(previous) != set(pool.REPOS):
        raise ValueError('Both repository selector backups are required')
    # 기존 감독기의 비교 후 복원 규칙과 부분 실패 일지를 그대로 사용한다.
    pool.RUNTIME.mkdir(parents=True, exist_ok=True)
    journal = {repo: {**value, 'desired': pool.selector(record)} for repo, value in previous.items()}
    (pool.RUNTIME / 'previous-selector.json').write_text(json.dumps(journal))
    pool.disable(record)


def configure(record, slots):
    existing = members(record)
    configs = []
    for item in slots:
        stem = record['prefix'] + '-' + str(item['slot']) + '-'
        name = stem + str(item['generation'])
        for runner in existing:
            if runner['name'].startswith(stem):
                if runner['busy'] or runner['status'] == 'online':
                    raise RuntimeError('Requested slot still has a live runner')
                pool.api('DELETE', '/actions/runners/' + str(runner['id']))
        result = pool.api('POST', '/actions/runners/generate-jitconfig', {
            'name': name, 'runner_group_id': record['group_id'],
            'labels': ['self-hosted', 'Linux', 'X64', pool.LABEL, record['prefix']],
            'work_folder': '_work'})
        if (result['runner']['name'] != name or not any(
                runner['id'] == result['runner']['id'] and runner['name'] == name
                for runner in members(record))):
            raise RuntimeError('JIT response belongs to another runner or group')
        configs.append({'prefix': record['prefix'], 'group_id': record['group_id'],
                        'runner_id': result['runner']['id'], 'name': name,
                        'jitconfig': result['encoded_jit_config']})
    return configs


def operate(request):
    if request['operation'] == 'probe':
        result = {'ready': bool(os.environ.get(SECRET)), 'operation': 'probe'}
        if result['ready']:
            pool.api('GET', '/actions/runners?per_page=1')
            for repo in pool.REPOS:
                pool.api('GET', '/actions/variables', repo=repo)
            result['authorized'] = True
        return result
    if not os.environ.get(SECRET):
        raise RuntimeError('Required GitHub Actions secret is missing')
    if request['operation'] == 'init':
        repositories = [pool.api('GET', '', repo=repo) for repo in pool.REPOS]
        if any(not repo['private'] for repo in repositories):
            raise RuntimeError('Only the selected private repositories may use this pool')
        previous = prior_selectors()
        group = pool.api('POST', '/actions/runner-groups', {
            'name': request['prefix'], 'visibility': 'selected',
            'selected_repository_ids': [repo['id'] for repo in repositories],
            'allows_public_repositories': False})
        request = {**request, 'group_id': group['id']}
        record = group_record(request)
        try:
            configs = configure(record, request['slots'])
        except (RuntimeError, OSError, KeyError):
            current = members(record)
            if not any(runner['busy'] for runner in current):
                for runner in current:
                    pool.api('DELETE', '/actions/runners/' + str(runner['id']))
                if not members(record):
                    pool.api('DELETE', '/actions/runner-groups/' + str(record['group_id']))
            raise
        return {**record, 'previous': previous, 'configs': configs}
    record = group_record(request)
    operation = request['operation']
    runners = members(record)
    if operation == 'status':
        return {'runners': [public_runner(runner) for runner in runners]}
    if operation == 'prepare':
        return {'configs': configure(record, request['slots'])}
    if operation == 'enable':
        if len(runners) != 4 or any(runner['status'] != 'online' for runner in runners):
            raise RuntimeError('Four online runners are required before enabling CI')
        if prior_selectors() != request['previous']:
            raise RuntimeError('CI selectors changed before activation')
        try:
            for repo in pool.REPOS:
                body = {'name': pool.VARIABLE, 'value': pool.selector(record)}
                pool.api('PATCH' if request['previous'][repo]['exists'] else 'POST',
                         '/actions/variables/' + pool.VARIABLE if request['previous'][repo]['exists']
                         else '/actions/variables', body, repo=repo)
        except (RuntimeError, OSError):
            restore(record, request['previous'])
            raise
        return {'enabled': True}
    restore(record, request['previous'])
    if operation == 'disable':
        return {'enabled': False}
    if any(runner['busy'] for runner in runners):
        raise RuntimeError('A busy runner blocks shutdown')
    for runner in runners:
        pool.api('DELETE', '/actions/runners/' + str(runner['id']))
    if members(record):
        raise RuntimeError('Runner group is not empty')
    pool.api('DELETE', '/actions/runner-groups/' + str(record['group_id']))
    return {'stopped': True}


def main():
    request_id = os.environ['RUNNER_REQUEST_ID']
    request = json.loads(os.environ['RUNNER_REQUEST'])
    validate(request, request_id)
    os.environ.pop('GH_TOKEN', None)
    os.environ.pop('VINC_GITHUB_RUNNER_PERSONAL_TOKEN', None)
    os.environ.pop('VINC_GITHUB_RUNNER_ADMIN_TOKEN', None)
    pool.RUNTIME = pathlib.Path(os.environ['RUNNER_TEMP']) / ('vinc-runner-journal-' + request_id)
    if os.environ.get(SECRET):
        os.environ['VINC_GITHUB_RUNNER_ADMIN_TOKEN'] = os.environ[SECRET]
    try:
        result = operate(request)
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        result = {'error': type(error).__name__, 'status': getattr(error, 'status', None)}
    result['request_sha256'] = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    packet = jit_crypto.seal(request['public_key'], request_id, result)
    target = pathlib.Path('runner-broker-response')
    target.mkdir(exist_ok=True)
    (target / 'response.json').write_text(json.dumps(packet))
    print('Encrypted runner broker response prepared; no PAT or JIT settings logged')


if __name__ == '__main__':
    main()
