"""Exercise four isolated containers with real repository checks in parallel."""
import concurrent.futures
import json
import pathlib
import shutil
import subprocess
import tarfile
import threading
import time
import uuid

from pool import ROOT, capacity, container_args, docker

REPO_ROOT = pathlib.Path('/workspace/Vinc')
RUN = ROOT / ('smoke-' + uuid.uuid4().hex[:8])
RUN.mkdir(parents=True)
ARCHIVE = RUN / 'source.tar'
with ARCHIVE.open('wb') as output:
    subprocess.run(['git', '-C', str(REPO_ROOT), 'archive', 'HEAD'], stdout=output, check=True)
SOURCE = RUN / 'source'
SOURCE.mkdir()
with tarfile.open(ARCHIVE) as archive:
    archive.extractall(SOURCE, filter='data')
HOMEPAGE = RUN / 'homepage'
HOMEPAGE.mkdir()
homepage_archive = RUN / 'homepage.tar'
with homepage_archive.open('wb') as output:
    subprocess.run(['git', '-C', '/workspace/Vinc-homepage', 'archive', 'HEAD'], stdout=output, check=True)
with tarfile.open(homepage_archive) as archive:
    archive.extractall(HOMEPAGE, filter='data')
IMAGE = json.loads((ROOT / 'image.json').read_text())['image_id']
print(json.dumps({'capacity': capacity(IMAGE)}), flush=True)
JOBS = {
    1: 'python -m pytest -q tests/core/test_auth.py tests/core/test_pk_compose_write.py --tb=short',
    2: 'python -m pytest -q tests/mcp/test_mcp_auth.py tests/mcp/test_mcp_scoped_keys.py --tb=short',
    3: 'cd app && npm ci --no-audit --no-fund && npm run typecheck && npm run build',
    4: 'sudo apt-get update -qq && python -m pytest -q tests/platform/test_ci_suite_is_unconditional.py tests/platform/test_gate_runner_move.py tests/platform/test_runner_tools_manifest.py tests/platform/test_cloud_runner_pool.py --tb=short && cd /home/runner/homepage/account && npm ci --no-audit --no-fund && npm run typecheck && npm test && cd .. && node scripts/gate-graph-refs.mjs',
}
started = time.time()
BARRIER = threading.Barrier(4)

def job(number, command):
    name = f'vinc-cloud-smoke-{RUN.name}-{number}'
    checks = '''set -euo pipefail
test "$(id -u)" = 1001
test ! -e /var/run/docker.sock
test ! -e /workspace
test -z "${VINC_GITHUB_RUNNER_ADMIN_TOKEN:-}${GH_TOKEN:-}${VINC_AZURE_CLIENT_SECRET:-}${VINC_MCP_READONLY_KEY:-}${LINEAR_API_KEY:-}"
sudo chown -R runner:runner /home/runner/probe
if [ -d /home/runner/homepage ]; then sudo chown -R runner:runner /home/runner/homepage; fi
for tool in curl git jq gh az docker python node npm cargo rustc; do command -v "$tool"; done
test "$(node --version)" = v22.22.0
/opt/actions-runner/bin/Runner.Listener --version
python -c "import ladybug,argon2,yaml,pytest,pypdf"
rustc --version
curl --fail --silent --show-error https://api.github.com/meta > /tmp/github-meta.json
jq -e 'has("verifiable_password_authentication")' /tmp/github-meta.json
export TMPDIR=/dev/shm/ci-probe
mkdir -p "$TMPDIR"
cd /home/runner/probe
export VINC_LBUG_EXT_DIR="$PWD/packaging/lbug_extensions"
'''
    args = container_args(name, IMAGE)
    args[0] = 'create'
    args[-1:-1] = ['--entrypoint', '/bin/bash']
    try:
        docker(args + ['-c', checks + '\n' + command], check=True, stdout=subprocess.DEVNULL)
        inspect = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
        assert inspect['HostConfig']['NanoCpus'] == 1_000_000_000
        assert inspect['HostConfig']['Memory'] == 6 * 1024 ** 3
        assert not inspect['Mounts'] and not inspect['HostConfig']['Privileged']
        docker(['cp', str(SOURCE) + '/.', name + ':/home/runner/probe'], check=True)
        if number == 4:
            # docker cp can create the destination while the container is stopped.
            docker(['cp', str(HOMEPAGE), name + ':/home/runner/homepage'], check=True)
        log_path = RUN / f'{number}.log'
        BARRIER.wait(timeout=300)
        before = time.time()
        with log_path.open('w') as output:
            result = docker(['start', '-a', name], stdout=output, stderr=subprocess.STDOUT)
        # Docker start -a may return zero even if the process failed: inspect it.
        state = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]['State']
        code = state['ExitCode']
        if result.returncode or code or state.get('OOMKilled'):
            raise RuntimeError(f'Slot {number} failed (Docker={result.returncode}, process={code}); inspect {log_path}')
        return {'slot': number, 'command': command, 'passed': True,
                'started_at': before, 'finished_at': time.time(), 'log': str(log_path)}
    finally:
        docker(['rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

results = []
errors = []
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    futures = {executor.submit(job, number, command): number for number, command in JOBS.items()}
    for future in concurrent.futures.as_completed(futures):
        try:
            result = future.result()
            results.append(result)
            print(json.dumps(result), flush=True)
        except Exception as error:
            errors.append(str(error))
            print(str(error), flush=True)
report = {'source_sha': subprocess.check_output(['git', '-C', str(REPO_ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
          'homepage_sha': subprocess.check_output(['git', '-C', '/workspace/Vinc-homepage', 'rev-parse', 'HEAD'], text=True).strip(),
          'image': IMAGE, 'results': results, 'errors': errors, 'elapsed_seconds': time.time() - started}
(RUN / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
if errors:
    raise SystemExit(1)
assert max(r['started_at'] for r in results) < min(r['finished_at'] for r in results), 'Four jobs did not overlap'
print('PASS: all four isolated containers overlapped and completed repository checks')
