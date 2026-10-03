"""Coordinate four JIT containers through encrypted GitHub Actions responses."""
import argparse
import fcntl
import io
import hashlib
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

import jit_crypto
import jit_entrypoint
import pool

ROOT = pool.ROOT
RUNTIME = ROOT / 'jit-runtime'
REPO = 'Vinculums/Vinc'
WORKFLOW = 'verify-hosted.yml'
REF = os.environ.get('VINC_CLOUD_RUNNER_REF', 'ci/cloud-runner-pool')
LIVE = True


class ArtifactRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        source = urllib.parse.urlsplit(request.full_url)
        target = urllib.parse.urlsplit(newurl)
        if (target.scheme != 'https' or target.username or target.password
                or target.port not in (None, 443)
                or not (target.hostname == 'api.github.com'
                        or (target.hostname or '').endswith('.blob.core.windows.net')
                        or (target.hostname or '').endswith('.actions.githubusercontent.com'))):
            raise RuntimeError('Unexpected artifact redirect destination')
        redirected = super().redirect_request(request, response, code, message, headers, newurl)
        if source.hostname != target.hostname:
            # Azure SAS 다운로드에 GitHub 인증 헤더를 전달하지 않는다.
            for collection in (redirected.headers, redirected.unredirected_hdrs):
                for name in list(collection):
                    if name.lower() == 'authorization':
                        del collection[name]
        return redirected


def github(method, path, body=None, binary=False):
    key = os.environ.get('GH_TOKEN')
    if not key:
        raise RuntimeError('Existing platform GitHub authentication is required')
    headers = {'Authorization': 'Bearer ' + key, 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'vinc-cloud-jit-pool/1.0'}
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request('https://api.github.com/repos/' + REPO + path,
                                     data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(ArtifactRedirect()).open(request, timeout=30) as response:
            if response.status == 204:
                return {}
            return response.read() if binary else json.load(response)
    except urllib.error.HTTPError as error:
        destination = urllib.parse.urlsplit(error.geturl()).hostname
        raise RuntimeError('Actions request returned HTTP ' + str(error.code)
                           + ' at ' + str(destination) + ' for ' + method + ' ' + path) from None


def rpc(operation, record=None, slots=None):
    record = record or {'prefix': 'vinc-cloud-' + uuid.uuid4().hex[:8]}
    private, public = jit_crypto.client_key()
    request_id = uuid.uuid4().hex
    request = {'operation': operation, 'prefix': record['prefix'], 'public_key': public,
               'group_id': record.get('group_id'), 'slots': slots or [],
               'previous': record.get('previous', {})}
    head = github('GET', '/commits/' + REF)['sha']
    github('POST', '/actions/workflows/' + WORKFLOW + '/dispatches', {
        'ref': REF, 'inputs': {'runner_request': json.dumps(request), 'runner_request_id': request_id,
                              'core': 'false', 'core_pkns': 'false', 'app': 'false',
                              'rust_linux': 'false', 'rust_windows': 'false'}})
    deadline = time.monotonic() + 600
    run = None
    while time.monotonic() < deadline:
        if run is None:
            runs = github('GET', '/actions/workflows/' + WORKFLOW + '/runs?event=workflow_dispatch&per_page=100')['workflow_runs']
            candidates = [item for item in runs if item['display_title'] == 'cloud-runner ' + request_id]
            if len(candidates) > 1:
                raise RuntimeError('More than one workflow matches the unique broker request')
            if candidates:
                run = candidates[0]
                if run['head_sha'] != head or run['head_branch'] != REF:
                    raise RuntimeError('Broker workflow ran a different source revision')
        if run is not None:
            artifacts = github('GET', '/actions/runs/' + str(run['id']) + '/artifacts')['artifacts']
            found = [item for item in artifacts if item['name'] == 'runner-response-' + request_id]
            if len(found) > 1:
                raise RuntimeError('Broker response artifact is ambiguous')
            if found:
                artifact = found[0]
                if artifact['expired'] or artifact['size_in_bytes'] > 2 * 1024 * 1024:
                    raise RuntimeError('Invalid encrypted broker artifact')
                raw = github('GET', '/actions/artifacts/' + str(artifact['id']) + '/zip', binary=True)
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    if archive.namelist() != ['response.json'] or archive.getinfo('response.json').file_size > 1024 * 1024:
                        raise RuntimeError('Unexpected encrypted artifact contents')
                    packet = json.loads(archive.read('response.json'))
                result = jit_crypto.open_response(private, request_id, packet)
                expected = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
                if result.pop('request_sha256', None) != expected:
                    raise RuntimeError('Broker response belongs to a different operation or pool')
                # 개인키와 JIT 값은 메모리에만 유지하고 암호문도 사용 후 삭제한다.
                github('DELETE', '/actions/artifacts/' + str(artifact['id']))
                if 'error' in result:
                    raise RuntimeError('GitHub runner broker failed: ' + result['error']
                                       + ' (HTTP ' + str(result.get('status')) + ')')
                return result
            progress = github('GET', '/actions/runs/' + str(run['id']))
            if progress['status'] == 'completed':
                raise RuntimeError('Broker workflow finished without its encrypted response: '
                                   + str(progress['conclusion']))
        time.sleep(3)
    raise RuntimeError('Broker request timed out; inspect its GitHub run before retrying')


def load():
    return json.loads((RUNTIME / 'pool.json').read_text())


def save(record):
    (RUNTIME / 'pool.json').write_text(json.dumps(record))


def container_args(name, image):
    args = pool.container_args(name, image)
    args[-1:-1] = ['--entrypoint', 'python3']
    return args + ['-c', pathlib.Path(jit_entrypoint.__file__).read_text()]


def launch(config, image):
    jit_entrypoint.validate(config)
    name = config['name']
    log_path = RUNTIME / (name + '.log')
    with log_path.open('w') as log:
        log_path.chmod(0o600)
        environment = {key: os.environ[key] for key in pool.ENV_NAMES if key in os.environ}
        process = subprocess.Popen(['docker'] + container_args(name, image), stdin=subprocess.PIPE,
                                   stdout=log, stderr=subprocess.STDOUT, env=environment)
    process.stdin.write((json.dumps(config) + '\n').encode())
    process.stdin.close()
    return process


def supervise():
    global LIVE
    lock = (RUNTIME / 'pool.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    record = load()
    record.update({'pid': os.getpid(), 'boot': pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                   'ticks': pathlib.Path(f'/proc/{os.getpid()}/stat').read_text().split(') ', 1)[1].split()[19]})
    save(record)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop_signal())
    response = rpc('init', record, [{'slot': i, 'generation': 1} for i in range(1, 5)])
    configs = response.pop('configs')
    record.update(response)
    record['generation'] = {str(i): 1 for i in range(1, 5)}
    save(record)
    workers = {int(config['name'].split('-')[-2]): (launch(config, record['image']), config['name'])
               for config in configs}
    del configs, response
    while LIVE:
        missing = []
        for slot, (process, name) in list(workers.items()):
            if process.poll() is not None:
                print(json.dumps({'runner': name, 'exit_code': process.returncode}), flush=True)
                pool.docker(['rm', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                del workers[slot]
                record['generation'][str(slot)] += 1
                missing.append({'slot': slot, 'generation': record['generation'][str(slot)]})
        if missing:
            save(record)
            response = rpc('prepare', record, missing)
            for config in response['configs']:
                slot = int(config['name'].split('-')[-2])
                workers[slot] = launch(config, record['image']), config['name']
            del response
        time.sleep(3)
    # 외부 TERM은 잡을 죽이지 않는다. stop이 등록 해제를 확인한 경우만 정리한다.
    if not (RUNTIME / 'shutdown-approved.json').exists():
        raise RuntimeError('Managed shutdown was not verified; preserving running containers')
    for process, name in workers.values():
        pool.docker(['stop', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.wait()
        pool.docker(['rm', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_signal():
    global LIVE
    LIVE = False


def cleanup_idle_containers(record):
    result = pool.docker(['ps', '-a', '--filter', 'label=vinc.cloud-runner=true',
                          '--format', '{{.Names}}'], check=True, capture_output=True, text=True)
    for name in result.stdout.splitlines():
        if not re.fullmatch(re.escape(record['prefix']) + r'-[1-4]-[1-9][0-9]*', name):
            continue
        metadata = json.loads(pool.docker(['inspect', name], check=True,
                                          capture_output=True, text=True).stdout)[0]
        if metadata['Image'] != record['image']:
            raise RuntimeError('Owned container has an unexpected image; preserving it')
        pool.docker(['stop', name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pool.docker(['rm', name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('probe', 'start', 'run', 'status', 'enable', 'disable', 'stop'))
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    RUNTIME.chmod(0o700)
    if args.command == 'probe':
        print(json.dumps(rpc('probe')))
        return
    if args.command == 'start':
        lock = (RUNTIME / 'start.lock').open('w')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (RUNTIME / 'pool.json').exists():
            raise RuntimeError('Prior JIT pool state exists; inspect it rather than replacing it')
        if (pool.RUNTIME / 'pool.json').exists():
            raise RuntimeError('Direct-registration pool state exists; inspect it first')
        metadata = json.loads((ROOT / 'image.json').read_text())
        pool.capacity(metadata['image_id'])
        if not rpc('probe').get('authorized'):
            raise RuntimeError('Add VINC_CLOUD_RUNNER_ADMIN_TOKEN securely in the Vinc repository Actions secrets')
        record = {'prefix': 'vinc-cloud-' + uuid.uuid4().hex[:8], 'image': metadata['image_id']}
        save(record)
        with (RUNTIME / 'supervisor.log').open('w') as log:
            process = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve()), 'run'],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        print('Started JIT supervisor PID', process.pid, '; verify actual four-runner online status')
        return
    if args.command == 'run':
        supervise()
        return
    record = load()
    if args.command == 'status':
        print(json.dumps({'supervisor_alive': pool.same_process(record), **rpc('status', record)}, indent=2))
    elif args.command in ('enable', 'disable'):
        if args.command == 'enable' and not pool.same_process(record):
            raise RuntimeError('Supervisor is not alive')
        print(json.dumps(rpc(args.command, record)))
    elif args.command == 'stop':
        rpc('disable', record)
        if pool.active_runs():
            raise RuntimeError('Active CI remains; wait for it to finish without cancelling runs')
        response = rpc('stop', record)
        if not response.get('stopped'):
            raise RuntimeError('Broker did not verify shutdown')
        (RUNTIME / 'shutdown-approved.json').write_text(json.dumps({'prefix': record['prefix']}))
        if pool.same_process(record):
            os.kill(record['pid'], signal.SIGTERM)
        else:
            cleanup_idle_containers(record)
        print('Stopped owned idle JIT registrations; supervisor will remove only its containers')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
