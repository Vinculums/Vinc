"""Execute the actual upload step against a disposable Azure CLI double."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/hub-store-rebuild.yml'

def upload_step():
    text = WORKFLOW.read_text(encoding='utf-8')
    section = text.split('      - name: Upload the rebuilt store\n', 1)[1].split('\n      - name:', 1)[0]
    return textwrap.dedent(section.split('        run: |\n', 1)[1])

AZ = '''#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
a=sys.argv[1:]
def arg(n):return a[a.index(n)+1]
root=pathlib.Path(os.environ['MOCK_ROOT'])
with (root/'calls').open('a') as f:f.write(' '.join(a[:3])+'\\n')
verb=a[2]
if verb=='exists':print('true' if os.environ['CASE']=='exists' else 'false')
elif verb=='upload':shutil.copyfile(arg('--source'),root/'remote')
elif verb=='show':print((root/'remote').stat().st_size)
elif verb=='download':
    data=(root/'remote').read_bytes()
    if os.environ['CASE']=='corrupt':data=bytes([data[0]^1])+data[1:]
    pathlib.Path(arg('--dest')).write_bytes(data)
else:raise SystemExit('unexpected CLI operation')
'''

@unittest.skipIf(os.name == 'nt' or not shutil.which('bash'), 'Linux shell workflow contract')
class UploadVerificationTests(unittest.TestCase):
    def run_case(self, case):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'bin').mkdir();(root/'work').mkdir()
            az=root/'bin/az';az.write_text(AZ);az.chmod(0o755)
            (root/'work/db.rebuilt-17').write_bytes(b'complete checkpointed store')
            env={**os.environ,'PATH':str(root/'bin')+os.pathsep+os.environ['PATH'],
                 'MOCK_ROOT':temp,'CASE':case,'DST':'db.rebuilt-17','SHARE':'test',
                 'ACCOUNT':'test','AZ_KEY':'fixture','RUNNER_TEMP':temp,'GITHUB_RUN_ID':'17'}
            p=subprocess.run(['bash','-c',upload_step()],cwd=root,env=env,capture_output=True,text=True,timeout=15)
            receipt=root/'work/upload-verification.json'
            return p.returncode, json.loads(receipt.read_text()) if receipt.exists() else None, (root/'calls').read_text()

    def test_complete_uploaded_bytes_are_verified(self):
        code, receipt, _ = self.run_case('match')
        self.assertEqual(code,0);self.assertTrue(receipt['verified'])
        self.assertEqual(receipt['size_bytes'],len(b'complete checkpointed store'))

    def test_same_length_corruption_blocks_switch(self):
        code, receipt, _ = self.run_case('corrupt')
        self.assertNotEqual(code,0);self.assertIsNone(receipt)

    def test_existing_destination_is_never_overwritten(self):
        code, receipt, calls = self.run_case('exists')
        self.assertNotEqual(code,0);self.assertIsNone(receipt)
        self.assertNotIn('storage file upload',calls)

if __name__=='__main__':unittest.main()
