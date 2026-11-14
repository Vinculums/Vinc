// 문서 읽기 테스트의 적재기: 실제 api.ts(쓰기 창 판별기) · view-reader.ts · view-rail.ts · doc-editor.ts 를 적재한다.
// 실제 코드는 판별기 `docMidWrite` / `storeDownReason`, 그래프 리더, 열의 머리와 닫기, 문서 편집기의 마운트다.
// 문구는 경로 이름 대역이고, 서버 호출과 CM6 편집기는 `globalThis.__doc` 에 넣은 가짜다.
import { tsUrl, COPY_STUB, COMPONENTS_STUB } from '../ts-load.mjs';
import { El } from '../mini-dom.mjs';

const names = (list) => list.split(' ').map((n) => `export const ${n} = () => {};`).join('\n');

let apiUrl;
/** 실제 api.ts 의 data: URL. 안쪽 모듈(전송 · 설정 · 스페이스)은 빈 대역이고, 우리가 읽는 두 함수만 실제다. */
export function realApi() {
  apiUrl ??= tsUrl('api.ts', {
    './view-session-cache': 'export const viewSessionCache = { clear() {} };',
    './packchunks': names('chunkPack'),
    './self-hosted': names('noteMe'),
    './api-error': 'export class ApiError extends Error {}',
    './api-config': `export const cfg = {}; ${names('WEB dataMode accountMode shellInvoke messageOf IN_SHELL remote')}`,
    './api-space': names('isCommunitySpace canWriteHere teamsApi communitiesApi spaceName spaceKey writeSpaceToAddress adoptSpaceFromAddress'),
    './conversion-inventory': names('waitForInventory'),
    './api-transport': names('CSRF_COOKIE csrfToken webFetch sidecarOnly accountCall call searchCall localCall hasTeamCredential bindTeamCredential forgetMemberKey'),
  });
  return apiUrl;
}

/** `api` 대역: 이 읽기들이 부르는 세 호출만 `globalThis.__doc` 로 넘기고, 판별기는 실제 것이다. */
const apiStub = async () => `
export { docMidWrite, storeDownReason } from ${JSON.stringify(await realApi())};
export const api = {
  docGet: (...a) => globalThis.__doc.docGet(...a),
  docHighlights: async () => ({ executed: false }),
  docContext: async () => ({ executed: false }),
};
export const cfg = { owner: 'u', team: '' };
export const viewScope = () => ({ key: 'k' });
export const isCommunitySpace = () => false;
export const canWriteHere = async () => true;
export const shellInvoke = async () => null;`;

/** 편집기 모듈 대역: 그래프 리더와 열이 쓰는 이름. 마운트는 가짜로 넘기고, 열이 비울 때 부르는 확정은 아무 일도 안 한다. */
const EDITOR_STUB = `
export const renderMarkdown = (s) => String(s);
export const mountDocEditor = (...a) => globalThis.__doc.mountEditor(...a);
export const openDocSheet = () => {};
export const disposeEditorsIn = () => Promise.resolve();`;

/** 실제 리더 + 실제 열(머리 · 닫기)을 한 표면처럼 묶는다. `detail` 은 body 에 달린 상세 열이다. */
export async function loadReader(body) {
  const rail = await tsUrl('surfaces/view-rail.ts', {
    '../copy': COPY_STUB,
    '../doc-editor': EDITOR_STUB,
    '../float-panel': 'export const closeFloatPanel = () => {};',
    '../view-prefs': 'export const RAIL_MIN = 16, RAIL_MAX = 120; export const loadPrefs = () => ({ railW: 24 }); export const savePrefs = () => {};',
  });
  const reader = await tsUrl('surfaces/view-reader.ts', {
    '../api': await apiStub(),
    '../copy': COPY_STUB,
    '../doc-title': 'export const docTitle = (t) => t || "";',
    '../components': COMPONENTS_STUB,
    '../doc-editor': EDITOR_STUB,
    '../rich-blocks': 'export const hydrateRich = () => {};',
    '../vinc-loaders.js': 'export const loaderInline = (s) => String(s);',
    '../topic-detail': 'export const detailState = () => "ready"; export const detailFailure = () => "failed";',
    './view-ask': 'export const wireAsk = () => () => {}; export const askRowHTML = () => ""; export const docAskItems = () => [];',
    './view-cards': 'export const detailErrorHTML = () => "";',
    '../node-basics': 'export const drawBasics = () => {};',
  });
  const { createRail } = await import(rail);
  const { createReader } = await import(reader);

  const detail = new El('div');
  body.append(detail);
  const el = new El('div');
  el.style.setProperty = () => {};
  const railGrip = new El('div');
  const host = { detail, isLive: () => true, dropPick() {}, forgetNode() {}, lastNode: () => null };
  const r = createRail(host, { el, shell: null, docpane: null, railGrip });
  Object.assign(host, {
    openRail: () => r.openRail(), closeRail: () => r.closeRail(),
    railHeadHTML: (c) => r.railHeadHTML(c), wireRailHead: () => r.wireRailHead(),
    railRevision: () => r.revision, railFull: () => r.full,
    dropRailAskCounts() {}, railAskCounts: () => null, focusNode: async () => null, openDetail() {},
  });
  const rd = createReader(host, {});
  return { reader: rd, detail, rail: r };
}

/** 실제 doc-editor.ts. 새로 적재하므로 살아 있는 편집기 표(`LIVE`)도 새것이다. */
export async function loadDocEditor() {
  return import(await tsUrl('doc-editor.ts', {
    './api': await apiStub(),
    './copy': COPY_STUB,
    './doc-title': 'export const docTitle = (t) => t || "";',
    './rich-blocks': 'export const hydrateRich = () => {};',
    './components': COMPONENTS_STUB,
    './vinc-loaders.js': 'export const loaderRows = () => "";',
    './cm-insert': names('tableCaret tableMarkdown'),
    './codemirror-editor': 'export const mountEditor = (...a) => globalThis.__doc.mountCm(...a);',
    './authoring': 'export {};',
    './extract-list': 'export {};',
    './node-composer': 'export {};',
    './doc-delete': 'export {};',
    './lnb': 'export {};',
  }));
}

/** 실제 판별기 하나. */
export async function loadDocMidWrite() {
  return (await import(await realApi())).docMidWrite;
}

/** 쓰기 창에 걸린 문서의 `doc_get` 응답(서버가 이 갈래에만 청크 수를 싣는다)과, 못 읽은 문서의 응답. */
export const MID_WRITE = { executed: false, error: 'document is incomplete: 0 of 27 chunks are present', chunks: 0, chunk_count: 27 };
export const FAILED = { executed: false, error: 'unknown document' };
export const GOOD = { executed: true, text: '# T\n\nbody\n', title: 'T', content_hash: 'h1', verified: true };

/** `setTimeout` / `setInterval` 을 기록하는 감시. `stop()` 이 그동안 걸린 타이머 수를 돌려주고 원래대로 돌린다. */
export function watchTimers() {
  const { setTimeout: st, setInterval: si } = globalThis;
  const seen = [];
  globalThis.setTimeout = (f, ms, ...a) => { seen.push(['timeout', ms]); return st(f, ms, ...a); };
  globalThis.setInterval = (f, ms, ...a) => { seen.push(['interval', ms]); return si(f, ms, ...a); };
  return { stop() { globalThis.setTimeout = st; globalThis.setInterval = si; return seen; } };
}
