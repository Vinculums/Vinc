import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// space-picker has no imports: everything it needs is passed in, so it loads as is.
const src = await readFile(new URL('../src/space-picker.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { kindOf, teamRows, communityRows, createSpacePicker, currentSpace, menuView, FIND_AT } = await import(
  `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const isCommunitySpace = (t) => Boolean(t && t.startsWith('community:'));

function fixture({ team = '', teamName, teams = [], communities = [], failTeams = false, failCommunities = false, ensureTeam } = {}) {
  const calls = [], cfg = { team, ...(teamName === undefined ? {} : { teamName }) };
  const d = { cfg, isCommunitySpace, ...(ensureTeam ? { ensureTeam: async (id, name) => { calls.push(`team:${id}:${name}`); return ensureTeam(id); } } : {}),
    retainBeforeSwitch: async () => { calls.push('retain'); return true; },
    reload: () => calls.push('reload'),
    teamsApi: { list: async (o) => { assert.equal(o.throwOnError, true); if (failTeams) throw new Error('offline'); return teams; } },
    communitiesApi: { list: async () => { if (failCommunities) throw new Error('offline'); return communities; } } };
  const picker = createSpacePicker(d);
  return { picker, cfg, calls };
}
const TEAMS = [{ id: 'active', name: 'Active', status: 'active' }, { id: 'pending', name: 'Pending', status: 'pending' },
  { id: 'paused', name: 'Paused', status: 'suspended' }, { id: 'deleted', name: 'Deleted', status: 'deleted' }];
const view = (s, here, q = '') => menuView(s.picker.model, q, 'Personal', here);

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
  assert.deepEqual(view(s, 'team').teams.rows.map((r) => r.id), ['active', 'pending', 'paused'], 'the menu shows them');
  s.picker.setKind('team');
  assert.equal(await s.picker.choose('pending'), false);
  assert.equal(await s.picker.choose('paused'), false);
  assert.equal(await s.picker.choose('deleted'), false);
  assert.equal(await s.picker.choose('active'), false, 'the space you are already in is not a switch');
  assert.equal(s.cfg.team, 'active'); assert.deepEqual(s.calls, []);
  assert.equal(view(s, 'team').foot, 'activation');
});

test('choosing personal retains, switches and reloads, and locks the picker meanwhile', async () => {
  const s = fixture({ team: 'active', teamName: 'Active', teams: TEAMS });
  await s.picker.load();
  s.picker.setKind('personal');
  const painted = [];
  assert.equal(await s.picker.choose('', () => painted.push(s.picker.model.busy)), true);
  assert.equal(s.cfg.team, ''); assert.deepEqual(s.calls, ['retain', 'reload']);
  assert.equal(s.cfg.teamName, '', 'personal keeps no remembered name');
  assert.deepEqual(painted, [true], 'repainted locked before the slow retain');
  assert.equal(await s.picker.choose(''), false, 'a second choice while switching is ignored');
});

test('setting the kind only browses: it never changes the space', async () => {
  const s = fixture({ team: 'active', teams: TEAMS, communities: [{ id: 'community:c-1', name: 'Physics' }] });
  await s.picker.load();
  s.picker.setKind('community'); s.picker.setKind('personal');
  assert.equal(s.cfg.team, 'active'); assert.deepEqual(s.calls, []);
});

test('a joined community can become the active space, read only', async () => {
  const s = fixture({ team: 'active', teams: TEAMS, communities: [{ id: 'community:c-1', name: 'Physics' }] });
  await s.picker.load(); s.picker.setKind('community');
  assert.deepEqual(s.picker.rows().map((r) => [r.id, r.name, r.current]), [['community:c-1', 'Physics', false]]);
  assert.equal(await s.picker.choose('community:c-1'), true);
  assert.equal(s.cfg.team, 'community:c-1'); assert.deepEqual(s.calls, ['retain', 'reload']);
  assert.equal(s.cfg.teamName, 'Physics', 'the name is kept next to the id');
  const inside = fixture({ team: 'community:c-1', communities: [{ id: 'community:c-1', name: 'Physics' }] });
  await inside.picker.load();
  assert.deepEqual(currentSpace(inside.picker.model, inside.cfg.team, '', isCommunitySpace),
    { kind: 'community', id: 'community:c-1', name: 'Physics', readOnly: true });
  assert.equal(view(inside, 'community').foot, 'readOnly');
});

test('a failed team list is told apart from an empty membership', async () => {
  const failed = fixture({ failTeams: true }), empty = fixture();
  await failed.picker.load(); await empty.picker.load();
  assert.equal(view(failed, 'personal').teams.state, 'failed');
  assert.equal(view(empty, 'personal').teams.state, 'none');
  // 찾는 중에는 빈 팀 묶음을 "팀 없음"으로 말하지 않는다. 목록 실패는 찾기와 무관하게 보인다.
  assert.equal(view(empty, 'personal', 'x').teams.state, 'ok');
  assert.equal(view(failed, 'personal', 'x').teams.state, 'failed');
  assert.equal(view(failed, 'personal', 'x').empty, false, 'a failure is something to show, not an empty result');
});

test('the community group appears only with a joined community', async () => {
  const none = fixture(), some = fixture({ communities: [{ id: 'community:c-1', name: 'P' }] }), broken = fixture({ failCommunities: true });
  for (const s of [none, some, broken]) await s.picker.load();
  const ids = (s) => view(s, 'personal').communities.map((r) => r.id);
  assert.deepEqual(ids(none), []);
  assert.deepEqual(ids(some), ['community:c-1']);
  assert.deepEqual(ids(broken), [], 'a failed community list leaves the group out');
  assert.equal(broken.picker.model.teams.load, 'ok', 'and the team list still loads');
});

test('the menu finds by name and offers the find field only past FIND_AT spaces', async () => {
  const few = fixture({ team: 'active', teams: TEAMS });
  await few.picker.load();
  assert.equal(view(few, 'team').find, false);
  const many = fixture({ team: 't0', teams: Array.from({ length: FIND_AT }, (_, i) => ({ id: `t${i}`, name: `Team ${i}`, status: 'active' })) });
  await many.picker.load();
  const all = view(many, 'team');
  assert.equal(all.find, true);
  assert.equal(all.personal, true);
  const hit = view(many, 'team', 'team 3');
  assert.deepEqual(hit.teams.rows.map((r) => r.id), ['t3']);
  assert.equal(hit.personal, false, 'personal is matched by its shown name');
  assert.equal(hit.empty, false);
  assert.equal(view(many, 'team', 'zzz').empty, true);
  assert.equal(view(many, 'team', 'PERS').personal, true, 'case does not matter');
});

test('the activation note waits while you search and needs an unavailable team', async () => {
  const s = fixture({ team: 'active', teams: TEAMS });
  await s.picker.load();
  assert.equal(view(s, 'team', 'act').foot, '');
  const calm = fixture({ team: 'active', teams: [TEAMS[0]] });
  await calm.picker.load();
  assert.equal(view(calm, 'team').foot, '');
});

test('the header names the space from the list, then from the remembered name', async () => {
  const listed = fixture({ team: 'active', teamName: 'Old name', teams: TEAMS });
  await listed.picker.load();
  assert.equal(currentSpace(listed.picker.model, 'active', listed.cfg.teamName, isCommunitySpace).name, 'Active');
  assert.equal(listed.cfg.teamName, 'Active', 'a loaded list refreshes the remembered name');
  const offline = fixture({ team: 'active', teamName: 'Active', failTeams: true });
  await offline.picker.load();
  assert.deepEqual(currentSpace(offline.picker.model, 'active', offline.cfg.teamName, isCommunitySpace),
    { kind: 'team', id: 'active', name: 'Active', readOnly: false });
  assert.equal(offline.cfg.teamName, 'Active', 'a failed list leaves the remembered name alone');
  const unknown = fixture({ team: 'gone', failTeams: true });
  await unknown.picker.load();
  assert.equal(currentSpace(unknown.picker.model, 'gone', '', isCommunitySpace).name, '', 'no name: the screen falls back to the kind');
  assert.deepEqual(currentSpace(unknown.picker.model, '', 'stale', isCommunitySpace),
    { kind: 'personal', id: '', name: '', readOnly: false });
});

test('teamRows and communityRows keep only what can be shown', () => {
  assert.equal(teamRows(TEAMS, 'active').length, 3);
  assert.equal(teamRows([{ id: 'p', name: 'P', status: 'pending' }], 'p')[0].current, false, 'a pending team is never current');
  assert.deepEqual(communityRows([{ id: 'c', name: 'C' }], 'c'), [{ id: 'c', name: 'C', state: 'active', current: true }]);
});

test('a team switch waits for the team credential and stays put when it is refused', async () => {
  for (const accepted of [false, true]) {
    let painted = 0;
    const s = fixture({ team: '', teams: TEAMS, ensureTeam: async () => accepted });
    await s.picker.load();
    s.picker.setKind('team');
    assert.equal(await s.picker.choose('active', () => painted++), accepted);
    assert.equal(s.cfg.team, accepted ? 'active' : '');
    assert.equal(s.cfg.teamName, accepted ? 'Active' : undefined, 'the name is kept next to the id, only on a switch');
    assert.deepEqual(s.calls, accepted ? ['team:active:Active', 'retain', 'reload'] : ['team:active:Active']);
    assert.equal(s.picker.model.busy, accepted, 'a refused switch unlocks the picker');
    if (!accepted) assert.equal(painted, 2, 'locked, then unlocked');
  }
});

test('a failing credential check counts as refused, and personal never asks for a team key', async () => {
  const s = fixture({ team: 'active', teams: TEAMS, ensureTeam: async () => { throw new Error('offline'); } });
  await s.picker.load();
  s.picker.setKind('personal');
  assert.equal(await s.picker.choose(''), true);
  assert.deepEqual(s.calls, ['retain', 'reload']);
  const t = fixture({ team: '', teams: [{ id: 'next', name: 'Next', status: 'active' }], ensureTeam: async () => { throw new Error('offline'); } });
  await t.picker.load(); t.picker.setKind('team');
  assert.equal(await t.picker.choose('next'), false);
  assert.equal(t.cfg.team, '');
});
