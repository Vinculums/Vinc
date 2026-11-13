// 나침반 지도의 뿌리: 최상위 주제가 'vinc' 여야만 열리던 가정을 잡는다(VIN-927).
// 근거: [[episode:compass-map-assumed-vinc-as-the-only-root-topic-2026-11-06]]
// 실제 view-compass.ts 를 mini-dom 위에서 그대로 마운트한다. 문구·색 표·테마는 대역이고 레이아웃은 실제 코드다.
// 끝의 절은 그 뿌리 규칙과 주제 사슬을 짓는 데이터 모델(view-compass-model.ts)을 DOM 없이 바로 부른다.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installDom, El } from './mini-dom.mjs';
import { loadCompass, compassModules } from './helpers/compass-load.mjs';

installDom({ hash: '' });
Object.defineProperty(El.prototype, 'isConnected', { get: () => true });
const winOn = [];
globalThis.addEventListener = (type, fn) => winOn.push([type, fn]);
globalThis.cancelAnimationFrame ??= clearTimeout;
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
globalThis.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });

const { mountCompass, compassDataFrom } = await loadCompass();

/** [domain, 제목, 종류?] 목록 → 마운트. 허브가 주는 모양(kind·domain·ts·degree)으로 만든다.
 *  마운트마다 다른 그래프라 스페이스도 따로 준다: 주제 색 표는 스페이스마다 하나라 같은 스페이스면 앞 그래프의 주제가 남는다. */
let spaces = 0;
function mount(spec, edges = [], ctx = {}) {
  ctx = { store: `roots|${++spaces}`, ...ctx };
  const nodes = spec.map(([domain, t, kind = 'concept']) => ({ id: `${kind}:${t}`, kind, title: t, label: t, domain, ts: '2026-11-01', degree: 1 }));
  const host = document.createElement('div');
  document.body.append(host);
  const h = mountCompass(host, compassDataFrom(nodes, edges), ctx);
  return { h, host, drawn: (w) => host.innerHTML.includes(`>${w}<`) };
}
const escape = () => { for (const [t, f] of winOn) if (t === 'keydown') f({ key: 'Escape', target: { closest: () => null } }); };

test('every domain under vinc: the map still starts at vinc', () => {
  const { h, drawn } = mount([['vinc/design', 'a'], ['vinc/eng', 'b'], ['vinc/design/tokens', 'c']]);
  assert.equal(h.centre(), 'topic:vinc');
  for (const w of ['vinc', 'design', 'eng']) assert.ok(drawn(w), `${w} is drawn`);
  assert.equal(h.colour('vinc'), '', 'the root wears no area colour');
  assert.notEqual(h.colour('vinc/design'), '', 'an area below the root does');
});

test('a single top-level topic with another name is the root', () => {
  const { h, drawn } = mount([['notes', 'a'], ['notes/daily', 'b']]);
  assert.equal(h.centre(), 'topic:notes');
  assert.ok(drawn('notes') && drawn('daily'));
  assert.equal(h.colour('notes'), '');
});

test('several top-level topics, none of them vinc: a nameless root holds them all', () => {
  const { h, drawn } = mount([['work/alpha', 'a'], ['work/zebra', 'b'], ['research', 'c']]);
  assert.equal(h.centre(), 'topic:', 'the nameless root is the centre');
  for (const w of ['All topics', 'work', 'research']) assert.ok(drawn(w), `${w} is drawn from the start`);
  assert.equal(h.colour(''), '');
  assert.notEqual(h.colour('work'), '', 'a top-level topic is an area and wears a colour');
  assert.notEqual(h.colour('research'), '');
  assert.deepEqual(h.tree().map((g) => g.domain), ['research', 'work/alpha', 'work/zebra']);
});

test('vinc beside another top-level topic (the default Agent roles live under agent)', () => {
  const { h, drawn } = mount([['vinc/design', 'a'], ['agent/maker', 'b']]);
  assert.equal(h.centre(), 'topic:');
  assert.ok(drawn('vinc') && drawn('agent'), 'both reach the start screen');
  h.open('topic:vinc');
  assert.equal(h.centre(), 'topic:vinc');
  escape();
  assert.equal(h.centre(), 'topic:', 'Escape goes back to the root');
});

test('a node without a domain stands at the root and is not given a made-up topic', () => {
  const { h, drawn } = mount([['work/alpha', 'a'], ['research', 'b'], ['', 'loose']]);
  assert.equal(h.centre(), 'topic:');
  assert.ok(!drawn('vinc'), 'no vinc topic appears');
  assert.ok(drawn('work') && drawn('research'));
  assert.ok(h.tree().some((g) => g.domain === '' && g.rows.some((r) => r.id === 'concept:loose')), 'the sidebar keeps the node');
});

test('with one top-level topic a node without a domain joins it, as it did under vinc', () => {
  const { h, host } = mount([['notes/daily', 'a'], ['', 'loose']]);
  assert.equal(h.centre(), 'topic:notes');
  assert.ok(h.tree().some((g) => g.domain === 'notes' && g.rows.some((r) => r.id === 'concept:loose')));
  assert.ok(!host.innerHTML.includes('>vinc<'));
});

test('no domain anywhere: the nameless root holds every node', () => {
  const { h, drawn } = mount([['', 'a'], ['', 'b']]);
  assert.equal(h.centre(), 'topic:');
  assert.ok(drawn('All topics'));
});

test('walking down from the nameless root and opening a node works at every depth', () => {
  const { h } = mount([['work/alpha/deep', 'a'], ['research', 'b']]);
  h.open('topic:work');
  assert.equal(h.centre(), 'topic:work');
  h.open('topic:work/alpha/deep');
  assert.equal(h.centre(), 'topic:work/alpha/deep');
  h.open('concept:a');
  assert.equal(h.centre(), 'concept:a');
  h.open('concept:b');
  assert.equal(h.centre(), 'concept:b');
});

test('a start path that names a topic the data lacks falls back to the root', () => {
  const { h } = mount([['work/alpha', 'a'], ['research', 'b']], [], { startPath: ['topic:vinc', 'topic:vinc/design'] });
  assert.equal(h.centre(), 'topic:');
});

test('a start path inside the data is honoured', () => {
  const { h } = mount([['work/alpha', 'a'], ['research', 'b']], [], { startPath: ['topic:work', 'topic:work/alpha'] });
  assert.equal(h.centre(), 'topic:work/alpha');
});

test('the time popup keeps the period and the bars, and invents no vinc', () => {
  const D = '-2026-11-01';
  for (const spec of [[['work/alpha', 'a' + D], ['research', 'b' + D], ['', 'loose' + D]], [['notes/daily', 'a' + D], ['notes/weekly', 'b' + D]]]) {
    const { h } = mount(spec);
    h.tools.querySelector('[data-pop="time"]').click();
    const pop = document.body.querySelectorAll('.cmp-pop').pop();
    assert.ok(pop.querySelector('[data-cmp-r="7"]') && pop.querySelector('[data-bars]'), 'period buttons and bars');
    assert.equal(pop.querySelector('[data-cell]'), null, 'no area by time grid');
    assert.ok(!pop.innerHTML.includes('vinc'), 'no vinc is invented');
    h.destroy();
  }
});

// ── 데이터 모델(view-compass-model.ts): 위의 뿌리 규칙과 주제 사슬을 짓는 모듈을 DOM 없이 바로 부른다 ──────────────
// 나침반이 쓰는 것과 같은 모듈 인스턴스다(같은 data: URL). 그래서 여기서 부르는 rootTopicOf 도 색 표의 그 함수다.
const model = await import((await compassModules()).model);
const C = { rootTopic: 'All topics' };
/** 모델이 읽는 노드 하나(compassDataFrom 의 모양) */
const cn = (id, k, d, x = {}) => ({ id, k, t: id.split(':')[1], d, ts: '2026-11-01', g: 1, m: 0, ...x });

test('compassDataFrom: the graph read becomes the compass shape', () => {
  const nodes = [
    { id: 'concept:a', kind: 'concept', label: 'A', title: 'Title A', domain: 'lab/x', ts: '2026-11-01T10:00:00Z', degree: 3 },
    { id: 'concept:doc.notes', kind: 'concept', title: 'Notes', domain: '' },
    { id: 'record:r', kind: 'record', label: '', title: '', domain: 'lab/x', updated_at: '2026-10-02T08:00:00Z' },
    { id: 'decision:pick-2026-09-20', kind: 'decision', label: 'Pick', domain: 'lab', props: { valid_from: '2026-09-21T00:00:00Z' }, valid_until: '2026-12-01' },
    { id: 'episode:e', kind: 'episode', domain: 'lab', content_ts: '2026-08-03T00:00:00Z', valid_from: '2026-08-04' },
    { id: 'domain:lab', kind: 'domain', label: 'lab' },
    { id: 'area:x', kind: 'area', label: 'x' },
  ];
  const edges = [
    { source: 'concept:a', target: { id: 'episode:e' }, type: 'RELATED_TO' },
    { source: { id: 'record:r' }, target: 'concept:a', type: 'ABOUT' },
    { source: 'concept:a', target: 'record:r', type: 'RELATED_TO', presentation: true },
    { source: 'concept:a', target: 'domain:lab', type: 'IN' },
    { source: 'decision:pick-2026-09-20', target: 'concept:a', type: 'DERIVES_FROM' },
  ];
  const out = model.compassDataFrom(nodes, edges);
  assert.deepEqual(out.nodes.map((n) => n.id), ['concept:a', 'concept:doc.notes', 'record:r', 'decision:pick-2026-09-20', 'episode:e'], 'domain and area nodes are left out');
  const [a, doc, rec, dec, epi] = out.nodes;
  assert.deepStrictEqual(a, { id: 'concept:a', k: 'concept', t: 'A', d: 'lab/x', ts: '2026-11-01', ct: undefined, vf: undefined, vu: undefined, g: 3, m: 2 });
  assert.equal(doc.k, 'doc', 'a concept:doc. id is a document mirror');
  assert.equal(doc.t, 'Notes', 'no label: the title');
  assert.equal(doc.d, '', 'no domain stays empty here: the model picks the root');
  assert.equal(rec.t, 'record:r', 'no label and no title: the id');
  assert.equal(rec.ts, '2026-10-02', 'no ts: the last edit');
  assert.equal(dec.ct, '2026-09-20', 'no content time: the date at the end of the id');
  assert.equal(dec.vf, '2026-09-21', 'valid_from from props');
  assert.equal(dec.vu, '2026-12-01');
  assert.equal(epi.ct, '2026-08-03');
  assert.equal(epi.vf, '2026-08-04');
  assert.equal(epi.m, 1, 'a meaning relation counts on both ends');
  assert.equal(dec.m, 1);
  assert.equal(rec.m, 0, 'ABOUT is not a meaning relation');
  assert.deepEqual(out.edges, [['concept:a', 'episode:e', 'RELATED_TO'], ['record:r', 'concept:a', 'ABOUT'], ['decision:pick-2026-09-20', 'concept:a', 'DERIVES_FROM']],
    'presentation edges and edges to nodes outside the read are dropped; object ends are read by id');
});

test('compassModel: one top-level topic is the root and the topic chain counts every node below it', () => {
  const data = { nodes: [cn('concept:a', 'concept', 'lab/ledger/core', { ts: '2026-10-01' }), cn('concept:b', 'concept', 'lab/review'),
    cn('record:loose', 'record', '', { ts: '2026-11-02' })], edges: [['concept:a', 'concept:b', 'RELATED_TO'], ['concept:a', 'concept:gone', 'RELATED_TO']] };
  const before = structuredClone(data);
  const m = model.compassModel(data, C);
  assert.equal(m.ROOT, 'lab'); assert.equal(m.RD, 1);
  assert.deepEqual([...m.topics.keys()], ['lab', 'lab/ledger', 'lab/ledger/core', 'lab/review']);
  const root = m.topics.get('lab');
  assert.equal(root.id, 'topic:lab'); assert.equal(root.k, 'topic'); assert.equal(root.t, undefined, 'a named root wears no label');
  assert.equal(root.count, 3); assert.deepEqual([...root.sub], ['lab/ledger', 'lab/review']);
  assert.deepEqual(root.direct.map((n) => n.id), ['record:loose'], 'a node without a domain stands on the root');
  assert.equal(root.ts, '2026-11-02', 'a topic keeps the newest ts below it');
  assert.equal(m.topics.get('lab/ledger').ts, '2026-10-01');
  assert.deepEqual(m.topics.get('lab/ledger/core').direct.map((n) => n.id), ['concept:a']);
  assert.equal(m.byId.get('record:loose').d, 'lab');
  assert.deepEqual(m.edges.map((e) => [e.a.id, e.b.id, e.t]), [['concept:a', 'concept:b', 'RELATED_TO']], 'an edge to a node outside the data is dropped');
  assert.deepEqual(m.adj.get('concept:b').map((e) => e.a.id), ['concept:a'], 'adj lists an edge at both ends');
  assert.deepEqual(m.adj.get('record:loose'), []);
  assert.deepEqual([...m.ATTACH], ['ABOUT', 'ATTACHES_TO', 'MENTIONS']);
  assert.deepStrictEqual(data, before, 'the data passed in is left as it was');
});

test('compassModel: several top-level topics hang under a nameless root with the label it is given', () => {
  const m = model.compassModel({ nodes: [cn('concept:a', 'concept', 'work/alpha'), cn('concept:b', 'concept', 'research'), cn('concept:c', 'concept', '')], edges: [] }, C);
  assert.equal(m.ROOT, ''); assert.equal(m.RD, 0);
  const root = m.topics.get('');
  assert.equal(root.id, 'topic:'); assert.equal(root.t, 'All topics');
  assert.deepEqual([...root.sub], ['work', 'research']);
  assert.deepEqual(root.direct.map((n) => n.id), ['concept:c']);
  assert.deepEqual(m.chainTo(m.byAny('concept:a')).map((n) => n.id), ['topic:', 'topic:work', 'topic:work/alpha', 'concept:a']);
});

test('compassModel: a bridge without a topic sits at the nearest topic its ends share, below the root only', () => {
  const nodes = [cn('concept:x', 'concept', 'lab/ledger/core'), cn('concept:y', 'concept', 'lab/ledger/edge'), cn('concept:z', 'concept', 'lab/review'),
    cn('concept:top', 'concept', 'lab'), cn('bridge:in', 'bridge', ''), cn('bridge:across', 'bridge', ''), cn('bridge:own', 'bridge', 'lab/review'),
    cn('bridge:alone', 'bridge', '')];
  const edges = [['bridge:in', 'concept:x', 'CONNECTS'], ['bridge:in', 'concept:y', 'CONNECTS'], ['bridge:in', 'concept:top', 'CONNECTS'], ['bridge:in', 'concept:z', 'RELATED_TO'],
    ['bridge:across', 'concept:x', 'CONNECTS'], ['concept:z', 'bridge:across', 'CONNECTS'], ['bridge:own', 'concept:x', 'CONNECTS'], ['bridge:own', 'concept:y', 'CONNECTS']];
  const m = model.compassModel({ nodes, edges }, C);
  assert.equal(m.byId.get('bridge:in').d, 'lab/ledger', 'ends in one area: that area (an end on the root itself does not count)');
  assert.deepEqual(m.topics.get('lab/ledger').direct.map((n) => n.id), ['bridge:in']);
  assert.equal(m.byId.get('bridge:across').d, 'lab', 'ends across areas: the root');
  assert.equal(m.byId.get('bridge:own').d, 'lab/review', 'a bridge with its own topic keeps it');
  assert.equal(m.byId.get('bridge:alone').d, 'lab', 'no ends: the root');
  assert.deepEqual(m.bridgeEnds(m.byId.get('bridge:in')).map((n) => n.id), ['concept:x', 'concept:y', 'concept:top'], 'only CONNECTS ends');
  assert.deepEqual(m.bridgeEnds(m.byId.get('bridge:across')).map((n) => n.id), ['concept:x', 'concept:z'], 'either direction');
  assert.deepEqual(m.chainTo(m.byId.get('bridge:in')).map((n) => n.id), ['topic:lab', 'topic:lab/ledger', 'bridge:in']);
  const v = model.compassModel({ nodes: [cn('concept:p', 'concept', 'a/x'), cn('concept:q', 'concept', 'a/y'), cn('concept:r', 'concept', 'b'), cn('bridge:j', 'bridge', ''), cn('bridge:k', 'bridge', '')],
    edges: [['bridge:j', 'concept:p', 'CONNECTS'], ['bridge:j', 'concept:q', 'CONNECTS'], ['bridge:k', 'concept:p', 'CONNECTS'], ['bridge:k', 'concept:r', 'CONNECTS']] }, C);
  assert.equal(v.byId.get('bridge:j').d, 'a', 'under a nameless root a shared top-level topic is enough');
  assert.equal(v.byId.get('bridge:k').d, '', 'ends under different top-level topics: the nameless root');
});

test('compassModel: byAny reads ids, aliases and topic ids; chainTo walks the topic chain', () => {
  const m = model.compassModel({ nodes: [cn('concept:x', 'concept', 'lab/ledger/core'), cn('concept:y', 'concept', 'lab')], edges: [] }, C);
  assert.equal(m.byAny('concept:x').id, 'concept:x');
  assert.equal(m.byAny('concept:x@@2').id, 'concept:x', "an 'id@@…' alias reads the id before it");
  assert.equal(m.byAny('topic:lab/ledger').path, 'lab/ledger');
  assert.equal(m.byAny('topic:'), undefined, 'a named root has no nameless topic');
  assert.equal(m.byAny('concept:nope'), null);
  assert.deepEqual(m.chainTo(m.byAny('concept:x')).map((n) => n.id), ['topic:lab', 'topic:lab/ledger', 'topic:lab/ledger/core', 'concept:x']);
  assert.deepEqual(m.chainTo(m.byAny('topic:lab/ledger')).map((n) => n.id), ['topic:lab', 'topic:lab/ledger'], 'a topic ends its own chain');
  assert.deepEqual(m.chainTo(m.byAny('concept:y')).map((n) => n.id), ['topic:lab', 'concept:y']);
});

test('node readers: when, kindKey, isMean, topicOf and fresh', () => {
  const N = (k, x = {}) => cn(`${k}:n`, k, 'lab', { ts: '', ...x });
  assert.equal(model.when(N('concept', { ct: '2026-03-04', vf: '2026-01-01' })), '2026-03-04', 'content time first');
  assert.equal(model.when(N('decision', { vf: '2026' })), '2026-01-01', 'then valid_from; a year alone is its first day');
  assert.equal(model.when(N('decision', { vf: '2026-03' })), '2026-03-01', 'a month alone is its first day');
  assert.equal(model.when(N('episode', { ts: '2026-05-06' })), '2026-05-06', 'an episode happened on its ts');
  assert.equal(model.when(N('record', { ts: '2026-05-06' })), '', 'a record with only its last edit has no date');
  assert.equal(model.when({ id: 'topic:lab', k: 'topic', path: 'lab', direct: [], sub: new Set(), count: 0, ts: '2026-05-06' }), '');
  assert.deepEqual(['decision', 'record', 'episode', 'bridge', 'doc', 'concept', 'topic', 'person'].map((k) => model.kindKey(N(k))),
    ['decision', 'record', 'episode', 'bridge', 'doc', 'concept', 'concept', 'concept']);
  assert.deepEqual(['concept', 'doc', 'bridge', 'record', 'decision'].map((k) => model.isMean(N(k))), [true, true, true, false, false]);
  assert.equal(model.isMean(N('record', { m: 2 })), true, 'a node with meaning relations is a meaning node');
  assert.equal(model.topicOf(N('concept', { d: 'lab/x' })), 'lab/x');
  assert.equal(model.topicOf({ id: 'topic:lab/y', k: 'topic', path: 'lab/y', direct: [], sub: new Set(), count: 0 }), 'lab/y');
  const m = model.compassModel({ nodes: [cn('concept:new', 'concept', 'lab', { ct: '2026-11-10' }), cn('concept:week', 'concept', 'lab', { ct: '2026-11-03' }),
    cn('concept:old', 'concept', 'lab', { ct: '2026-11-02' }), cn('record:none', 'record', 'lab')], edges: [] }, C);
  assert.deepEqual(['concept:new', 'concept:week', 'concept:old', 'record:none'].map((id) => m.fresh(m.byId.get(id))), [true, true, false, false],
    'fresh = within seven days of the newest date in the data; no date is never fresh');
});
