import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// space-picker has no imports: everything it needs is passed in, so it loads as is.
const src = await readFile(new URL('../src/space-picker.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { kindOf, teamRows, communityRows, createSpacePicker, currentSpace, menuView, FIND_AT,
  unifiedCommunityId, communityFromAddress } = await import(
  `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const isCommunitySpace = (t) => Boolean(t && t.startsWith('community:'));

function fixture({ team = '', teamName, teams = [], communities = [], failTeams = false, failCommunities = false, ensureTeam, unifiedName } = {}) {
  const calls = [], cfg = { team, ...(teamName === undefined ? {} : { teamName }) };
  const d = { cfg, isCommunitySpace, ...(unifiedName === undefined ? {} : { unifiedName }),
    ...(ensureTeam ? { ensureTeam: async (id, name) => { calls.push(`team:${id}:${name}`); return ensureTeam(id); } } : {}),
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

// 커뮤니티를 하나로 합치기 전과 뒤. 계정 목록(`/api/graph/spaces` 의 `communities`)의 모양이 두 세계를 가른다.
// 합치기 전: 가입한 커뮤니티가 가입 역할(member · operator)로 온다. 분야마다 하나씩 다섯이다.
const FIVE = ['Physics', 'Math', 'Economics', 'Art', 'AI'].map((name, i) => ({ id: `community:c-${i}`, name, role: 'member' }));
// 합친 뒤: 합친 공간이 로그인한 모두의 목록 맨 앞에 가입 행 없이 reader(운영자는 operator)로 오고, 다섯은 보관되어 빠진다.
const COMMONS = { id: 'community:019f-commons', name: 'Vinc Community', role: 'reader' };

test('before the move the joined communities are listed as today, each under its own name', async () => {
  for (const role of ['member', 'operator']) {
    const rows = FIVE.map((c) => ({ ...c, role }));
    assert.equal(unifiedCommunityId(rows), '', `five ${role} rows are not the unified space`);
    const s = fixture({ team: 'active', teams: TEAMS, communities: rows, unifiedName: 'Vinc 커뮤니티' });
    await s.picker.load();
    const v = view(s, 'team');
    assert.deepEqual(v.communities, FIVE.map((c) => ({ id: c.id, name: c.name, state: 'active', current: false })),
      'five rows, names from the account, none marked unified');
    assert.equal(v.unified, false, 'the group keeps its plural name');
  }
  assert.equal(unifiedCommunityId([FIVE[0]]), '', 'a member of one community is not in the unified space');
  assert.equal(unifiedCommunityId([]), '');
  const inside = fixture({ team: FIVE[0].id, communities: FIVE, unifiedName: 'Vinc 커뮤니티' });
  await inside.picker.load();
  assert.equal(currentSpace(inside.picker.model, inside.cfg.team, '', isCommunitySpace).name, 'Physics');
  assert.equal(view(inside, 'community').foot, 'readOnly');
});

test('after the move every signed-in account sees one unified row, named in the screen language', async () => {
  // 팀도 가입 행도 없는 계정이다. 계정이 합친 공간을 그래도 싣는다.
  const s = fixture({ communities: [COMMONS], unifiedName: 'Vinc 커뮤니티' });
  await s.picker.load();
  const v = view(s, 'personal');
  assert.deepEqual(v.communities, [{ id: COMMONS.id, name: 'Vinc 커뮤니티', state: 'active', current: false, unified: true }]);
  assert.equal(v.unified, true, 'one unified row: the group is named in the singular');
  assert.equal(v.teams.state, 'none');
  assert.equal(v.foot, '', 'outside a community there is no read-only foot');
  // 운영자에게는 같은 한 줄이 operator 로 온다.
  assert.equal(unifiedCommunityId([{ ...COMMONS, role: 'operator' }]), COMMONS.id);
  // 이름 카피가 없으면 계정이 보낸 이름이다.
  const plain = fixture({ communities: [COMMONS] });
  await plain.picker.load();
  assert.equal(plain.picker.rows('community')[0].name, 'Vinc Community');
});

test('a reader row is the unified space even beside a joined community', async () => {
  const other = { id: 'community:c-other', name: 'Other', role: 'member' };
  assert.equal(unifiedCommunityId([COMMONS, other]), COMMONS.id);
  assert.equal(unifiedCommunityId([{ ...COMMONS, role: 'operator' }, other]), '', 'an operator row is unified only when it is the only one');
  const s = fixture({ communities: [COMMONS, other], unifiedName: 'Vinc Community' });
  await s.picker.load();
  const v = view(s, 'personal');
  assert.deepEqual(v.communities.map((r) => [r.id, Boolean(r.unified)]), [[COMMONS.id, true], [other.id, false]]);
  assert.equal(v.unified, false, 'two rows keep the plural group name');
});

test('a user with no membership row opens the unified space without a join step', async () => {
  const s = fixture({ team: 'active', teams: TEAMS, communities: [COMMONS], unifiedName: 'Vinc Community' });
  await s.picker.load(); s.picker.setKind('community');
  assert.equal(await s.picker.choose(COMMONS.id), true);
  assert.equal(s.cfg.team, COMMONS.id);
  assert.equal(s.cfg.teamName, 'Vinc Community', 'the shown name is kept next to the id');
  assert.deepEqual(s.calls, ['retain', 'reload'], 'only the usual switch: nothing else is asked or written');
});

test('inside the unified space the header names it, read only, and the foot is the same read-only line', async () => {
  const inside = fixture({ team: COMMONS.id, teamName: 'Vinc Community', communities: [COMMONS], unifiedName: 'Vinc 커뮤니티' });
  await inside.picker.load();
  assert.deepEqual(currentSpace(inside.picker.model, inside.cfg.team, inside.cfg.teamName, isCommunitySpace),
    { kind: 'community', id: COMMONS.id, name: 'Vinc 커뮤니티', readOnly: true });
  assert.equal(inside.cfg.teamName, 'Vinc 커뮤니티', 'the remembered name follows the shown name');
  assert.equal(view(inside, 'community').foot, 'readOnly');
  assert.equal(view(inside, 'community', 'zzz').foot, 'readOnly', 'searching does not hide it');
  const offline = fixture({ team: COMMONS.id, teamName: 'Vinc 커뮤니티', failCommunities: true, unifiedName: 'Vinc 커뮤니티' });
  await offline.picker.load();
  assert.equal(currentSpace(offline.picker.model, offline.cfg.team, offline.cfg.teamName, isCommunitySpace).name, 'Vinc 커뮤니티',
    'a failed list falls back to the remembered name');
});

test('a ?space=community: link opens a listed community only: the unified id after the move, not a closed one', () => {
  // 합친 뒤: 합친 공간의 id 는 가입 행이 없어도 목록에 있어 열린다. 대소문자는 가리지 않는다.
  assert.deepEqual(communityFromAddress(COMMONS.id, [COMMONS]), { id: COMMONS.id, name: 'Vinc Community' });
  assert.deepEqual(communityFromAddress(COMMONS.id.toUpperCase(), [COMMONS]), { id: COMMONS.id, name: 'Vinc Community' });
  // 보관된 다섯의 id 는 목록에서 빠져서 지금과 같이 거절된다(지금 공간을 그대로 연다).
  for (const old of FIVE) assert.equal(communityFromAddress(old.id, [COMMONS]), null);
  // 합치기 전: 가입한 커뮤니티의 링크는 지금처럼 열리고, 가입하지 않은 곳은 거절된다.
  assert.deepEqual(communityFromAddress(FIVE[2].id, FIVE), { id: FIVE[2].id, name: 'Economics' });
  assert.equal(communityFromAddress(COMMONS.id, FIVE), null);
  assert.equal(communityFromAddress('', [COMMONS]), null);
  assert.equal(communityFromAddress(COMMONS.id, []), null, 'a failed lookup reads as no list and refuses');
});

test('the unified row has its words in English and Korean', async () => {
  const copy = await readFile(new URL('../src/copy.ts', import.meta.url), 'utf8');
  for (const key of ['spaceCommunity', 'spaceUnified', 'spaceUnifiedHint', 'spaceCommunityReadOnly'])
    assert.equal(copy.split(`\n      ${key}: "`).length - 1, 2, `${key} is in both decks`);
  assert.match(copy, /\n      spaceUnified: "Vinc 커뮤니티",/);
});
