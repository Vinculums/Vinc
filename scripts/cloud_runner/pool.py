"""Four isolated, single-job Actions runners for this Cloud task's lifetime."""
import argparse
import concurrent.futures
import fcntl
import json
import os
import pathlib
import signal
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = pathlib.Path('/workspace/.onboarding/actions-runner')
RUNTIME = ROOT / 'runtime'
ORG = 'Vinculums'
REPOS = ('Vinculums/Vinc', 'Vinculums/Vinc-homepage')
WORKFLOWS = {'Vinculums/Vinc': ('ci.yml',),
             'Vinculums/Vinc-homepage': ('account-ci.yml', 'site-ci.yml')}
LABEL = 'vinc-cloud-ci'
VARIABLE = 'CI_LINUX_RUNNERS'
STOP = threading.Event()
ENV_NAMES = ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
             'HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'NO_PROXY',
             'no_proxy', 'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS')

class APIError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__('GitHub API returned HTTP ' + str(status))

def api(method, path, body=None, read_actions=False, repo=None):
    key = (os.environ.get('VINC_GITHUB_RUNNER_PERSONAL_TOKEN')
           or os.environ.get('VINC_GITHUB_RUNNER_ADMIN_TOKEN'))
    if read_actions:
        key = os.environ.get('GH_TOKEN') or key
    if not key:
        raise RuntimeError('Add VINC_GITHUB_RUNNER_PERSONAL_TOKEN as a network secret in Settings > Codex Cloud > Personal vault')
    headers = {'Authorization': 'Bearer ' + key, 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'vinc-cloud-runner-pool/1.0'}
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers['Content-Type'] = 'application/json'
    scope = 'repos/' + repo if repo else 'orgs/' + ORG
    request = urllib.request.Request('https://api.github.com/' + scope + path,
        data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response) if response.status != 204 else {}
    except urllib.error.HTTPError as error:
        raise APIError(error.code) from None

def docker(args, **kwargs):
    environment = {name: os.environ[name] for name in ENV_NAMES if name in os.environ}
    return subprocess.run(['docker'] + args, env=environment, **kwargs)

def capacity(image):
    driver = docker(['info', '--format', '{{.Driver}}'], check=True,
        capture_output=True, text=True).stdout.strip()
    size = int(docker(['image', 'inspect', image, '--format', '{{.Size}}'], check=True,
        capture_output=True, text=True).stdout.strip())
    # vfs copies the complete image filesystem for each container. Reserve
    # space for checkouts and builds rather than exhausting this task's VM.
    # vfs also makes a full init-layer copy for each container.
    required = (8 * size if driver == 'vfs' else 0) + 4 * 1024 ** 3
    free = shutil.disk_usage('/workspace').free
    if free < required:
        raise RuntimeError(f'Four runners require at least {required // 1024**3} GiB free with {driver}; only {free // 1024**3} GiB is available')
    return {'driver': driver, 'required_bytes': required, 'free_bytes': free}

def state():
    return json.loads((RUNTIME / 'pool.json').read_text())

def same_process(record):
    try:
        return (pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip() == record['boot']
            and pathlib.Path(f'/proc/{record["pid"]}/stat').read_text().split(') ', 1)[1].split()[19] == record['ticks'])
    except (OSError, KeyError):
        return False

def all_runners():
    result = []
    for page in range(1, 101):
        data = api('GET', f'/actions/runners?per_page=100&page={page}')
        result.extend(data['runners'])
        if len(result) >= data['total_count']:
            return result
    raise RuntimeError('Runner pagination exceeded the inspection limit')

def own_runners(record):
    return [runner for runner in all_runners() if runner['name'].startswith(record['prefix'] + '-')]

def selector(record):
    return json.dumps(['self-hosted', 'linux', LABEL, record['prefix']], separators=(',', ':'))

def verify_group(record):
    group = api('GET', '/actions/runner-groups/' + str(record['group_id']))
    repositories = api('GET', '/actions/runner-groups/' + str(record['group_id']) + '/repositories?per_page=100')
    ids = {repository['id'] for repository in repositories['repositories']}
    expected = {api('GET', '', read_actions=True, repo=repo)['id'] for repo in REPOS}
    if (group['name'] != record['prefix'] or group['visibility'] != 'selected'
            or group.get('allows_public_repositories', True)
            or ids != expected or repositories['total_count'] != len(expected)):
        raise RuntimeError('Runner group must be restricted to the two selected private repositories')

def container_args(name, image):
    args = ['run', '-i', '--name', name, '--label', 'vinc.cloud-runner=true',
            '--cpus', '1', '--memory', '6g', '--memory-swap', '6g', '--pids-limit', '512',
            '--shm-size', '4g', '--cap-drop', 'NET_RAW', '--stop-timeout', '30']
    for key in ['NO_PROXY', 'no_proxy']:
        args += ['--env', key + '=localhost,127.0.0.1,::1']
    # Only non-secret proxy settings are forwarded. No development workspace,
    # credential files, host PID namespace, privileged mode or Docker socket.
    proxy_hosts = set()
    for name in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
        value = os.environ.get(name)
        if value:
            proxy = urllib.parse.urlsplit(value)
            if proxy.username or proxy.password:
                raise RuntimeError('A credential-bearing proxy URL cannot be forwarded')
            proxy_hosts.add(proxy.hostname)
            args += ['--env', name + '=' + value]
    # The platform proxy hostname may exist only in the host's hosts file.
    for host in sorted(proxy_hosts):
        args += ['--add-host', host + ':' + socket.gethostbyname(host)]
    args.append(image)
    return args

def clean_registration(name):
    for runner in all_runners():
        if runner['name'] == name:
            if runner['busy']:
                return False
            try:
                api('DELETE', '/actions/runners/' + str(runner['id']))
            except APIError as error:
                if error.status == 409:
                    return False
                if error.status != 404:
                    raise
    return True

def slot(record, number):
    generation = 0
    delay = 5
    while not STOP.is_set():
        generation += 1
        name = f'{record["prefix"]}-{number}-{generation}'
        process = None
        try:
            token = api('POST', '/actions/runners/registration-token')['token']
            log_path = RUNTIME / f'{number}-{generation}.log'
            with log_path.open('w') as log:
                log_path.chmod(0o600)
                environment = {key: os.environ[key] for key in ENV_NAMES if key in os.environ}
                process = subprocess.Popen(['docker'] + container_args(name, record['image']),
                    stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, env=environment)
                packet = {'org': ORG, 'group': record['prefix'], 'prefix': record['prefix'],
                          'name': name, 'token': token}
                process.stdin.write((json.dumps(packet) + '\n').encode())
                process.stdin.close()
                del token, packet
                while process.poll() is None:
                    if STOP.wait(5):
                        if clean_registration(name):
                            docker(['stop', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            time.sleep(5)
                code = process.returncode
            print(json.dumps({'slot': number, 'name': name, 'exit_code': code}), flush=True)
            clean_registration(name)
            # The runner's credential files and job filesystem disappear here.
            docker(['rm', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            delay = 5 if code == 0 else min(delay * 2, 120)
        except (RuntimeError, OSError, KeyError) as error:
            print(json.dumps({'slot': number, 'error': type(error).__name__,
                              'status': getattr(error, 'status', None)}), flush=True)
            # Do not destroy a busy or uninspectable runner after an API error.
            if process and process.poll() is None:
                while process.poll() is None:
                    time.sleep(5)
            delay = min(delay * 2, 120)
        STOP.wait(delay)

def disable(record):
    previous_path = RUNTIME / 'previous-selector.json'
    if not previous_path.exists():
        return
    previous = json.loads(previous_path.read_text())
    for repo, prior in list(previous.items()):
        try:
            current = {'exists': True, 'value': api('GET', '/actions/variables/' + VARIABLE, repo=repo)['value']}
        except APIError as error:
            if error.status != 404:
                raise
            current = {'exists': False}
        original = {key: prior[key] for key in ('exists', 'value') if key in prior}
        if current != original:
            if current != {'exists': True, 'value': prior['desired']}:
                raise RuntimeError('CI selector changed elsewhere in ' + repo + '; refusing to overwrite it')
            if prior['exists']:
                api('PATCH', '/actions/variables/' + VARIABLE,
                    {'name': VARIABLE, 'value': prior['value']}, repo=repo)
            else:
                api('DELETE', '/actions/variables/' + VARIABLE, repo=repo)
        del previous[repo]
        previous_path.write_text(json.dumps(previous))
    previous_path.unlink()
    print('Restored prior CI selectors for the two repositories')

def enable(record):
    verify_group(record)
    runners = own_runners(record)
    if not same_process(record) or len(runners) != 4 or any(r['status'] != 'online' for r in runners):
        raise RuntimeError('Four online pool runners are required before changing CI routing')
    path = RUNTIME / 'previous-selector.json'
    if path.exists():
        raise RuntimeError('CI routing already has a saved prior value; inspect before changing it')
    previous = {}
    desired = selector(record)
    try:
        for repo in REPOS:
            try:
                prior = {'exists': True, 'value': api('GET', '/actions/variables/' + VARIABLE, repo=repo)['value']}
            except APIError as error:
                if error.status != 404:
                    raise
                prior = {'exists': False}
            previous[repo] = dict(prior, desired=desired)
            path.write_text(json.dumps(previous))
            path.chmod(0o600)
            api('PATCH' if prior['exists'] else 'POST',
                '/actions/variables/' + VARIABLE if prior['exists'] else '/actions/variables',
                {'name': VARIABLE, 'value': desired}, repo=repo)
    except (RuntimeError, OSError):
        disable(record)
        raise
    print('Enabled the shared Cloud Linux pool for both repositories')

def active_runs():
    active = []
    for repo, workflows in WORKFLOWS.items():
        for workflow in workflows:
            for page in range(1, 101):
                runs = api('GET', f'/actions/workflows/{workflow}/runs?per_page=100&page={page}',
                           read_actions=True, repo=repo)['workflow_runs']
                active.extend((repo, run['id']) for run in runs if run['status'] != 'completed')
                if len(runs) < 100:
                    break
            else:
                raise RuntimeError('CI run inspection limit reached; refusing to stop')
    return active

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['start', 'run', 'status', 'enable', 'disable', 'stop'])
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    RUNTIME.chmod(0o700)
    if args.command == 'start':
        start_lock = (RUNTIME / 'start.lock').open('w')
        fcntl.flock(start_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (RUNTIME / 'pool.json').exists() and same_process(state()):
            raise RuntimeError('This Cloud task already has a runner pool')
        metadata = json.loads((ROOT / 'image.json').read_text())
        docker(['image', 'inspect', metadata['image_id']], check=True, stdout=subprocess.DEVNULL)
        capacity(metadata['image_id'])
        # The first request checks actual registration permission, not repo.admin.
        api('POST', '/actions/runners/registration-token')
        for repo in REPOS:
            api('GET', '/actions/variables', repo=repo)
        if (RUNTIME / 'previous-selector.json').exists():
            raise RuntimeError('Restore the saved CI routing before creating another pool')
        if (RUNTIME / 'pool.json').exists() and own_runners(state()):
            raise RuntimeError('Prior pool has registered runners; inspect it before creating another pool')
        prefix = 'vinc-cloud-' + uuid.uuid4().hex[:8]
        repositories = [api('GET', '', read_actions=True, repo=repo) for repo in REPOS]
        if any(not repo['private'] for repo in repositories):
            raise RuntimeError('This pool is restricted to private repositories')
        group = api('POST', '/actions/runner-groups', {'name': prefix, 'visibility': 'selected',
                    'selected_repository_ids': [repo['id'] for repo in repositories],
                    'allows_public_repositories': False})
        record = {'prefix': prefix, 'group_id': group['id'], 'image': metadata['image_id'], 'slots': 4}
        (RUNTIME / 'pool.json').write_text(json.dumps(record))
        verify_group(record)
        log_path = RUNTIME / 'supervisor.log'
        with log_path.open('w') as log:
            log_path.chmod(0o600)
            process = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve()), 'run'],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        print('Started supervisor PID', process.pid, '(registration and online status still need checking)')
        return
    if args.command == 'run':
        lock = (RUNTIME / 'pool.lock').open('w')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another supervisor holds this Cloud task runner pool lock')
        record = state()
        verify_group(record)
        record.update({'pid': os.getpid(), 'boot': pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                  'ticks': pathlib.Path(f'/proc/{os.getpid()}/stat').read_text().split(') ', 1)[1].split()[19]})
        (RUNTIME / 'pool.json').write_text(json.dumps(record))
        for signum in [signal.SIGTERM, signal.SIGINT]:
            signal.signal(signum, lambda *_: STOP.set())
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(slot, record, number) for number in range(1, 5)]
            for future in futures:
                future.result()
        if own_runners(record):
            raise RuntimeError('Owned runners remain registered; preserving the group for inspection')
        verify_group(record)
        api('DELETE', '/actions/runner-groups/' + str(record['group_id']))
        return
    record = state()
    runners = own_runners(record)
    if args.command == 'status':
        print(json.dumps({'supervisor_alive': same_process(record), 'runners': [
            {key: runner[key] for key in ['id', 'name', 'status', 'busy']} for runner in runners]}, indent=2))
    elif args.command == 'enable':
        enable(record)
    elif args.command == 'disable':
        disable(record)
    elif args.command == 'stop':
        disable(record)
        # Drain rather than cancel CI. A run can have jobs that are not queued yet.
        active = active_runs()
        if active or any(r['busy'] for r in runners):
            raise RuntimeError('CI still has active runs/jobs; keep the pool running and retry stop after they finish')
        if not same_process(record):
            raise RuntimeError('Saved supervisor identity is no longer live; inspect this pool before cleanup')
        os.kill(record['pid'], signal.SIGTERM)
        print('Requested idle pool shutdown; no workflow was cancelled')

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
