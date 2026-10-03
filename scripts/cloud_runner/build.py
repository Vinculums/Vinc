"""Build an isolated CI runner image using verified official release artifacts."""
import hashlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import urllib.parse
import urllib.request

SOURCE = pathlib.Path(__file__).resolve().parent
ROOT = pathlib.Path('/workspace/.onboarding/actions-runner')
BUILD = ROOT / 'build'
PINS = json.loads((SOURCE / 'pins.json').read_text())
IMAGE = 'vinc-cloud-runner:' + PINS['runner_version']
BUILD.mkdir(parents=True, exist_ok=True)

def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'vinc-cloud-runner-setup/1.0'})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()

def artifact(url, path, checksum):
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == checksum:
        print('Verified cached artifact:', path.name, flush=True)
        return
    temporary = path.with_suffix('.download')
    request = urllib.request.Request(url, headers={'User-Agent': 'vinc-cloud-runner-setup/1.0'})
    with urllib.request.urlopen(request, timeout=45) as response, temporary.open('wb') as output:
        shutil.copyfileobj(response, output)
    if hashlib.sha256(temporary.read_bytes()).hexdigest() != checksum:
        temporary.unlink()
        raise RuntimeError('Checksum mismatch: ' + path.name)
    temporary.replace(path)
    print('Downloaded and verified:', path.name, flush=True)

runner = PINS['runner_version']
artifact(f'https://github.com/actions/runner/releases/download/v{runner}/actions-runner-linux-x64-{runner}.tar.gz',
         BUILD / 'runner.tar.gz', PINS['runner_sha256'])
node_file = f'node-v{PINS["node_version"]}-linux-x64.tar.xz'
node_base = f'https://nodejs.org/dist/v{PINS["node_version"]}/'
checks = fetch(node_base + 'SHASUMS256.txt').decode().splitlines()
node_hash = next(line.split()[0] for line in checks if line.split()[-1] == node_file)
artifact(node_base + node_file, BUILD / 'node.tar.xz', node_hash)
rustup_url = f'https://static.rust-lang.org/rustup/archive/{PINS["rustup_version"]}/x86_64-unknown-linux-gnu/rustup-init'
rustup_hash = fetch(rustup_url + '.sha256').decode().split()[0]
artifact(rustup_url, BUILD / 'rustup-init', rustup_hash)
trust_source = pathlib.Path(os.environ.get('SSL_CERT_FILE') or '/etc/ssl/certs/ca-certificates.crt')
trust = trust_source.read_bytes()
if b'PRIVATE KEY' in trust or b'BEGIN CERTIFICATE' not in trust:
    raise RuntimeError('Expected a public CA bundle')
(BUILD / 'trust.pem').write_bytes(trust)
for name in ['Dockerfile', 'entrypoint.py', 'ci_tool.sh']:
    shutil.copyfile(SOURCE / name, BUILD / name)
for name in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']:
    value = os.environ.get(name)
    if value and (urllib.parse.urlsplit(value).username or urllib.parse.urlsplit(value).password):
        raise RuntimeError('Use a credential-free platform proxy URL')
command = ['docker', 'build', '--progress=plain', '-t', IMAGE,
           '--build-arg', 'BASE_IMAGE=' + PINS['ubuntu'],
           '--build-arg', 'RUST_VERSION=' + PINS['rust_version'],
           '--build-arg', 'AZURE_CLI_VERSION=' + PINS['azure_cli_version']]
if shutil.disk_usage('/workspace').free < 4 * 1024 ** 3:
    raise RuntimeError('The image build requires at least 4 GiB of free working disk')
for host in sorted({urllib.parse.urlsplit(os.environ[name]).hostname
                    for name in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']
                    if os.environ.get(name)}):
    command += ['--add-host', host + ':' + socket.gethostbyname(host)]
for name in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'no_proxy']:
    if os.environ.get(name):
        command.extend(['--build-arg', name])
environment = os.environ.copy()
environment['BUILDX_CONFIG'] = str(ROOT / 'buildx')
subprocess.run(command + [str(BUILD)], check=True, env=environment)
metadata = {'image': IMAGE, 'image_id': subprocess.check_output(
    ['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], text=True).strip(),
    'pins': PINS, 'node_sha256': node_hash, 'rustup_sha256': rustup_hash}
(ROOT / 'image.json').write_text(json.dumps(metadata, indent=2) + '\n')
print('Verified runner image built:', IMAGE)
