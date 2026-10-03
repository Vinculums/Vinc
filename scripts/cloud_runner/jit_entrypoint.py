"""Start an already registered JIT runner without using the GitHub REST proxy."""
import base64
import json
import os
import re
import sys
import urllib.parse


def validate(packet):
    required = {'prefix', 'group_id', 'runner_id', 'name', 'jitconfig'}
    if set(packet) != required or not re.fullmatch(r'vinc-cloud-[0-9a-f]{8}', packet['prefix']):
        raise ValueError('Unexpected JIT scope')
    if not re.fullmatch(re.escape(packet['prefix']) + r'-[1-4]-[1-9][0-9]*', packet['name']):
        raise ValueError('Unexpected JIT name')
    files = json.loads(base64.b64decode(packet['jitconfig'], validate=True))
    # 공식 Listener가 파일을 쓰기 전에 경로와 풀 소유권을 제한한다.
    if set(files) != {'.runner', '.credentials', '.credentials_rsaparams'}:
        raise ValueError('Unexpected JIT files')
    settings = json.loads(base64.b64decode(files['.runner'], validate=True))
    if (settings.get('agentName') != packet['name']
            or settings.get('agentId') != packet['runner_id']
            or settings.get('poolName') != packet['prefix']
            or settings.get('gitHubUrl', '').rstrip('/') != 'https://github.com/Vinculums'
            or settings.get('workFolder') != '_work'
            or not settings.get('ephemeral')):
        raise ValueError('JIT configuration does not belong to this single-job runner')
    server = urllib.parse.urlsplit(settings.get('serverUrl', ''))
    if (server.scheme != 'https' or not (server.hostname or '').endswith('.actions.githubusercontent.com')
            or server.username or server.password or server.port not in (None, 443)):
        raise ValueError('Unexpected Actions service destination')
    return packet['jitconfig']


def main():
    try:
        value = validate(json.loads(sys.stdin.readline()))
    except (ValueError, KeyError, TypeError):
        raise SystemExit('Rejected invalid JIT configuration') from None
    environment = os.environ.copy()
    environment['ACTIONS_RUNNER_INPUT_JITCONFIG'] = value
    # v2.337.0 CommandSettings는 이 환경변수를 읽고 마스킹한 뒤 제거한다.
    # 값은 명령행에 놓지 않으며 PAT는 컨테이너로 전달하지 않는다.
    os.execve('./run.sh', ['./run.sh'], environment)


if __name__ == '__main__':
    main()
