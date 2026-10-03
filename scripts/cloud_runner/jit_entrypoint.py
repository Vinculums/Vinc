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
    # 공식 C# 설정은 PascalCase이며 구버전 fixture의 camelCase도 같은 계약이다.
    if len({key.lower() for key in settings}) != len(settings):
        raise ValueError('Ambiguous JIT setting names')
    settings = {key.lower(): value for key, value in settings.items()}
    # JIT는 ID와 Ephemeral을 문자열로 직렬화하고 PoolName을 생략할 수 있다.
    # 그룹 소유권은 암호화된 응답을 만들기 전 브로커가 실제 멤버십으로 확인한다.
    if (settings.get('agentname') != packet['name']
            or str(settings.get('agentid')) != str(packet['runner_id'])
            or settings.get('poolname') not in (None, packet['prefix'])
            or settings.get('githuburl', '').rstrip('/') != 'https://github.com/Vinculums'
            or settings.get('workfolder') != '_work'
            or settings.get('ephemeral') not in (True, 'True')):
        raise ValueError('JIT configuration does not belong to this single-job runner')
    server = urllib.parse.urlsplit(settings.get('serverurl', ''))
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
