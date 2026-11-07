"""운영 연결 없이 워크플로 셸의 정리 범위와 시간 예산을 검증한다."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/topic-copy-rehearsal.yml'
STEPS = {s.get('name'): s for s in yaml.safe_load(WORKFLOW.read_text())['jobs']['audit']['steps']}


class RehearsalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = dict(os.environ, PATH=f'{self.root}:{os.environ["PATH"]}',
                        RUNNER_TEMP=str(self.root), GITHUB_ENV=str(self.root / 'env'),
                        GITHUB_OUTPUT=str(self.root / 'output'),
                        GITHUB_REPOSITORY='example/repo', GITHUB_REPOSITORY_ID='42',
                        GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='2', STORAGE='fake', SHARE='test',
                        REHEARSAL_WORK_DEADLINE='9999999999', MOCK_ROOT=str(self.root))
        self.script('az', '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['MOCK_ROOT'])
a = sys.argv[1:]
with (p / 'calls').open('a') as f: f.write(json.dumps(a) + '\\n')
state = json.loads((p / 'state').read_text())
if a[:3] == ['storage', 'share', 'snapshot']:
    start = a.index('--metadata') + 1
    metadata = dict(v.split('=', 1) for v in a[start:a.index('--query')])
    state.append(dict(name='test', snapshot='created', metadata=metadata))
    (p / 'state').write_text(json.dumps(state))
    if os.environ.get('LOSE_OUTPUT'): sys.exit(0 if os.environ['LOSE_OUTPUT'] == 'empty' else 1)
    print('created')
elif a[:3] == ['storage', 'share', 'list']:
    if os.environ.get('FAIL_LIST'): sys.exit(1)
    print(json.dumps(state))
elif a[:3] == ['storage', 'share', 'delete']:
    assert '--snapshot' in a and '--delete-snapshots' not in a
    if not os.environ.get('KEEP_SNAPSHOT'):
        state = [s for s in state if s['snapshot'] != a[a.index('--snapshot') + 1]]
        (p / 'state').write_text(json.dumps(state))
else: sys.exit(2)
''')
        (self.root / 'state').write_text('[]')
        self.script('gh', '#!/bin/sh\ncat "$MOCK_ROOT/jobs"\n')
        (self.root / 'jobs').write_text(json.dumps({'jobs': [dict(name='audit', status='in_progress', started_at='2026-01-01T00:00:00Z')]}))
        self.assertEqual(self.run_step('Set absolute work deadline with cleanup reserve').returncode, 0)

    def script(self, name, text):
        path = self.root / name
        path.write_text(text)
        path.chmod(0o755)

    def run_step(self, name, **env):
        return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', STEPS[name]['run']],
                              env=dict(self.env, **env), capture_output=True, text=True)

    def snapshot(self, **overrides):
        metadata = dict(rehearsal='topic_copy', repository_id='42', run_id='123', run_attempt='2')
        metadata.update(overrides)
        return dict(name='test', snapshot=str(overrides) or 'own', metadata=metadata)

    def test_cleanup_exact_scope_and_verification(self):
        own = self.snapshot()
        others = [self.snapshot(run_id='124'), self.snapshot(run_attempt='1'),
                  self.snapshot(repository_id='43'), self.snapshot(rehearsal='other'),
                  dict(name='test', snapshot='unmarked', metadata={}),
                  dict(self.snapshot(), name='other-share', snapshot='other-share'),
                  dict(self.snapshot(), snapshot=None)]
        (self.root / 'state').write_text(json.dumps([own] + others))
        result = self.run_step('Remove only this rehearsal snapshot')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / 'state').read_text()), others)
        calls = [json.loads(x) for x in (self.root / 'calls').read_text().splitlines()]
        lists = [a for a in calls if a[2] == 'list']
        self.assertEqual(len(lists), 2)
        for a in lists:
            self.assertIn('--include-snapshots', a)
            self.assertIn('--include-metadata', a)
            self.assertEqual(a[a.index('--num-results') + 1], '*')

    def test_lost_creation_output_is_still_cleaned(self):
        result = self.run_step('Create isolated share snapshot', LOSE_OUTPUT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'output').exists())
        self.assertEqual(len(json.loads((self.root / 'state').read_text())), 1)
        result = self.run_step('Remove only this rehearsal snapshot')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / 'state').read_text()), [])

    def test_successful_creation_with_empty_output_is_cleaned(self):
        self.assertNotEqual(self.run_step('Create isolated share snapshot', LOSE_OUTPUT='empty').returncode, 0)
        self.assertEqual(self.run_step('Remove only this rehearsal snapshot').returncode, 0)
        self.assertEqual(json.loads((self.root / 'state').read_text()), [])

    def test_cleanup_condition_does_not_depend_on_output(self):
        condition = STEPS['Remove only this rehearsal snapshot']['if']
        self.assertIn('always()', condition)
        self.assertIn('steps.snap.outcome', condition)
        self.assertNotIn('outputs', condition)

    def test_delete_not_effective_fails_verification(self):
        (self.root / 'state').write_text(json.dumps([self.snapshot()]))
        self.assertNotEqual(self.run_step('Remove only this rehearsal snapshot', KEEP_SNAPSHOT='1').returncode, 0)

    def test_failed_inventory_never_deletes(self):
        self.assertNotEqual(self.run_step('Remove only this rehearsal snapshot', FAIL_LIST='1').returncode, 0)
        self.assertNotIn('delete', (self.root / 'calls').read_text())

    def test_missing_marker_fails_closed(self):
        self.assertNotEqual(self.run_step('Remove only this rehearsal snapshot', GITHUB_RUN_ID='').returncode, 0)
        self.assertFalse((self.root / 'calls').exists())

    def test_deadline_uses_job_start_and_reserves_ten_minutes(self):
        import datetime
        start = int(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp())
        self.assertEqual((self.root / 'env').read_text(), f'REHEARSAL_WORK_DEADLINE={start + 4800}\n')

    def test_ambiguous_job_start_fails_closed(self):
        (self.root / 'jobs').write_text('{"jobs": []}')
        self.assertNotEqual(self.run_step('Set absolute work deadline with cleanup reserve').returncode, 0)

    def budget(self, deadline, cap):
        self.script('date', '#!/bin/sh\necho 1000\n')
        self.script('timeout', '#!/bin/sh\nprintf "%s\\n" "$@"\n')
        return subprocess.run(['bash', '-e', '-c', f'source "$RUNNER_TEMP/rehearsal-budget.sh"; run_bounded {cap} echo probe'],
                              env=dict(self.env, REHEARSAL_WORK_DEADLINE=str(deadline)), capture_output=True, text=True)

    def test_budget_caps_rehearsal_at_75_minutes(self):
        self.assertIn('4500s', self.budget(10000, 4500).stdout)

    def test_budget_subtracts_elapsed_and_kill_grace(self):
        self.assertIn('910s', self.budget(2000, 4500).stdout)

    def test_insufficient_budget_starts_nothing(self):
        result = self.budget(1149, 4500)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_container_trap_runs_on_failure(self):
        self.script('docker', '#!/bin/sh\necho "$*" >> "$MOCK_ROOT/docker-calls"\nexit 1\n')
        (self.root / 'restored').mkdir()
        self.env['GITHUB_WORKSPACE'] = str(self.root)
        script = 'cd "$MOCK_ROOT"\n' + STEPS['Rehearse only the isolated copy with no network']['run']
        result = subprocess.run(['bash', '-e', '-c', script], env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        calls = (self.root / 'docker-calls').read_text()
        self.assertIn('--network none', calls)
        self.assertIn('rm -f vinc-copy-rehearsal', calls)
        self.assertNotIn('AZURE_', calls)


if __name__ == '__main__':
    unittest.main()
