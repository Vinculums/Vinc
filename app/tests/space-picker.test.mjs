import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// space-picker has no imports: everything it needs is passed in, so it loads as is.
const src = await readFile(new URL('../src/space-picker.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { kindOf, teamRows, communityRows, createSpacePicker, spacePickerHtml, noteOf } = await import(
  `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const U = { space: 'Space', spacePersonal: 'Personal', spacePersonalHint: 'Only you see this graph.',
  spaceTeams: 'Teams', spaceCommunities: 'Communities', spaceInUse: 'In use', spaceNone: 'none',
  spacePending: 'pending', spaceSuspended: 'paused', spaceActivationHint: 'activate on account',
  spaceListFailed: 'failed', spaceCommunityReadOnly: 'read only' };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const isCommunitySpace = (t) => Boolean(t && t.startsWith('community:'));

function fixture({ team = '', teams = [], communities = [], failTeams = false, failCommunities = false } = {}) {
  const calls = [], cfg = { team };
  const d = { cfg, isCommunitySpace,
    retainBeforeSwitch: async () => { calls.push('retain'); return true; },
    reload: () => calls.push('reload'),
    teamsApi: { list: async (o) => { assert.equal(o.throwOnError, true); if (failTeams) throw new Error('offline'); return teams; } },
    communitiesApi: { list: async () => { if (failCommunities) throw new Error('offline'); return communities; } } };
  const picker = createSpacePicker(d);
  return { picker, cfg, calls };
}
const TEAMS = [{ id: 'active', name: 'Active', status: 'active' }, { id: 'pending', name: 'Pending', status: 'pending' },
  { id: 'paused', name: 'Paused', status: 'suspended' }, { id: 'deleted', name: 'Deleted', status: 'deleted' }];

test('the kind follows where you are now', () => {
  assert.equal(kindOf('', isCommunitySpace), 'personal');
  assert.equal(kindOf(null, isCommunitySpace), 'personal');
  assert.equal(kindOf('team-1', isCommunitySpace), 'team');
  assert.equal(kindOf('community:c-1', isCommunitySpace), 'community');
});

test('the picker opens on the kind you are in', () => {
  assert.equal(fixture({ team: 'active' }).picker.model.kind, 'team');
  assert.equal(fixture({ team: 'community:c-1' }).picker.model.kind, 'community');
  assert.equal(fixture().picker.model.kind, 'personal');
});

test('pending and suspended teams are listed but cannot become the active graph', async () => {
  const s = fixture({ team: 'active', teams: TEAMS });
  await s.picker.load();
  const rows = s.picker.rows('team');
  assert.deepEqual(rows.map((r) => [r.id, r.state, r.current]), [['active', 'active', true], ['pending', 'pending', false], ['paused', 'suspended', false]]);
  s.picker.setKind('team');
  assert.equal(await s.picker.choose('pending'), false);
  assert.equal(await s.picker.choose('paused'), false);
  assert.equal(await s.picker.choose('deleted'), false);
  assert.equal(await s.picker.choose('active'), false, 'the space you are already in is not a switch');
  assert.equal(s.cfg.team, 'active'); assert.deepEqual(s.calls, []);
  assert.equal(noteOf(s.picker.model, U), 'activate on account');
});

test('choosing personal retains, switches and reloads, and locks the picker meanwhile', async () => {
  const s = fixture({ team: 'active', teams: TEAMS });
  await s.picker.load();
  s.picker.setKind('personal');
  const painted = [];
  assert.equal(await s.picker.choose('', () => painted.push(s.picker.model.busy)), true);
  assert.equal(s.cfg.team, ''); assert.deepEqual(s.calls, ['retain', 'reload']);
  assert.deepEqual(painted, [true], 'repainted locked before the slow retain');
  assert.equal(await s.picker.choose(''), false, 'a second choice while switching is ignored');
});

test('tabs only browse: switching the kind never changes the space', async () => {
  const s = fixture({ team: 'active', teams: TEAMS, communities: [{ id: 'community:c-1', name: 'Physics' }] });
  await s.picker.load();
  s.picker.setKind('community'); s.picker.setKind('personal');
  assert.equal(s.cfg.team, 'active'); assert.deepEqual(s.calls, []);
});

test('a joined community can become the active space, read only', async () => {
  const s = fixture({ team: 'active', teams: TEAMS, communities: [{ id: 'community:c-1', name: 'Physics' }] });
  await s.picker.load(); s.picker.setKind('community');
  assert.deepEqual(s.picker.rows().map((r) => [r.id, r.name, r.current]), [['community:c-1', 'Physics', false]]);
  assert.equal(noteOf(s.picker.model, U), 'read only');
  assert.equal(await s.picker.choose('community:c-1'), true);
  assert.equal(s.cfg.team, 'community:c-1'); assert.deepEqual(s.calls, ['retain', 'reload']);
});

test('a failed team list is told apart from an empty membership', async () => {
  const failed = fixture({ failTeams: true }), empty = fixture();
  await failed.picker.load(); await empty.picker.load();
  failed.picker.setKind('team'); empty.picker.setKind('team');
  assert.equal(noteOf(failed.picker.model, U), 'failed');
  assert.equal(noteOf(empty.picker.model, U), 'none');
});

test('the community tab appears only with a joined community, or when you are in one', async () => {
  const none = fixture(), some = fixture({ communities: [{ id: 'community:c-1', name: 'P' }] }), broken = fixture({ failCommunities: true });
  for (const s of [none, some, broken]) await s.picker.load();
  const tabs = (s) => [...spacePickerHtml(s.picker.model, s.picker.rows(), U, esc).matchAll(/data-kind="(\w+)"/g)].map((m) => m[1]);
  assert.deepEqual(tabs(none), ['personal', 'team']);
  assert.deepEqual(tabs(some), ['personal', 'team', 'community']);
  assert.deepEqual(tabs(broken), ['personal', 'team']);
  const inside = fixture({ team: 'community:c-9', failCommunities: true }); await inside.picker.load();
  assert.deepEqual(tabs(inside), ['personal', 'team', 'community']);
});

test('the list marks the current row, disables the unavailable ones and escapes names', async () => {
  const s = fixture({ team: 'active', teams: [...TEAMS, { id: 'x', name: '<b>X</b>', status: 'active' }] });
  await s.picker.load();
  const html = spacePickerHtml(s.picker.model, s.picker.rows(), U, esc);
  assert.match(html, /aria-checked="true"\s+data-space="active"[^>]*>[\s\S]*?In use/);
  assert.match(html, /data-space="pending" disabled>[\s\S]*?pending/);
  assert.match(html, /data-space="paused" disabled>[\s\S]*?paused/);
  assert.doesNotMatch(html, /<b>X/);
  const personal = fixture(); await personal.picker.load();
  assert.match(spacePickerHtml(personal.picker.model, personal.picker.rows(), U, esc), /Only you see this graph\.[\s\S]*In use/);
});

test('teamRows and communityRows keep only what can be shown', () => {
  assert.equal(teamRows(TEAMS, 'active').length, 3);
  assert.equal(teamRows([{ id: 'p', name: 'P', status: 'pending' }], 'p')[0].current, false, 'a pending team is never current');
  assert.deepEqual(communityRows([{ id: 'c', name: 'C' }], 'c'), [{ id: 'c', name: 'C', state: 'active', current: true }]);
});
