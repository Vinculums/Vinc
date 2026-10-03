"""Register a single-use runner; its short-lived token arrives only on stdin."""
import json
import os
import pathlib
import re
import subprocess
import sys

packet = json.loads(sys.stdin.readline())
required = {'org', 'group', 'prefix', 'name', 'token'}
if set(packet) != required or packet['org'] != 'Vinculums':
    raise SystemExit('Unexpected registration request')
if (not re.fullmatch(r'vinc-cloud-[0-9a-f]{8}', packet['prefix'])
        or packet['group'] != packet['prefix']
        or not re.fullmatch(re.escape(packet['prefix']) + r'-[1-4]-[1-9][0-9]*', packet['name'])):
    raise SystemExit('Unexpected runner name')
env = os.environ.copy()
env['ACTIONS_RUNNER_INPUT_TOKEN'] = packet.pop('token')
command = ['./config.sh', '--unattended', '--url', 'https://github.com/' + packet['org'],
           '--runnergroup', packet['group'], '--name', packet['name'],
           '--labels', 'vinc-cloud-ci,' + packet['prefix'],
           '--work', '_work', '--ephemeral']
# Runner CommandSettings consumes and masks ACTIONS_RUNNER_INPUT_TOKEN before
# any job is accepted. The admin PAT is never passed to this container.
configured = subprocess.run(command, env=env)
env.pop('ACTIONS_RUNNER_INPUT_TOKEN', None)
if configured.returncode:
    raise SystemExit(configured.returncode)
pathlib.Path('/opt/actions-runner/.configured').write_text('ready\n')
os.execv('./run.sh', ['./run.sh'])
