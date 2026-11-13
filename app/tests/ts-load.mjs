// 테스트용 TS 적재기: 원본을 옮겨 쓰고, 상대 경로 import 를 넘겨준 대역 모듈로 갈아 끼운다.
// 대역이 없는 값 import 가 남으면 바로 던진다(data: URL 은 상대 경로를 못 푼다: 조용히 깨지지 않게).
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

export const dataUrl = (js) => `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`;
export const srcText = (name) => readFile(new URL(`../src/${name}`, import.meta.url), 'utf8');
export const transpile = (code) => ts.transpileModule(code,
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;

let seq = 0;
/** `name` 은 src 아래 경로. `stubs` 는 { "../api": "export const api = …" } 꼴(값은 JS 원문 또는 data: URL). */
export async function loadTs(name, stubs = {}) {
  return import(await tsUrl(name, stubs));
}

/** `loadTs` 와 같되 data: URL 만 돌려준다: 다른 모듈의 대역으로 진짜 모듈을 끼울 때 쓴다.
 *  `patch` 는 옮겨 쓴 JS 를 대역을 끼우기 전에 고치는 함수다(안쪽 값 하나를 전역으로 내보낼 때). */
export async function tsUrl(name, stubs = {}, { patch } = {}) {
  let js = transpile(await srcText(name));
  if (patch) js = patch(js);
  for (const [spec, code] of Object.entries(stubs)) {
    const url = code.startsWith('data:') ? code : dataUrl(code);
    js = js.split(JSON.stringify(spec)).join(JSON.stringify(url));
  }
  const left = [...js.matchAll(/(?:from\s*|import\()\s*"(\.{1,2}\/[^"]+)"/g)].map((m) => m[1]);
  if (left.length) throw new Error(`${name}: no stub for ${[...new Set(left)].join(', ')}`);
  return dataUrl(`${js}\n// ${++seq}`);
}

/** 카피 대역: 어떤 경로든 읽으면 그 경로 이름(문자열로 쓰일 때)이고, 부르면 `경로(인자)` 다. */
export const COPY_STUB = `
const mk = (path) => new Proxy(function () {}, {
  get: (_, k) => k === Symbol.toPrimitive || k === 'toString' ? () => path
    : k === 'then' ? undefined : mk(path + '.' + String(k)),
  apply: (_, __, args) => path + '(' + args.join(',') + ')',
});
export const COPY = mk('COPY');`;

export const COMPONENTS_STUB = `
export const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));`;

/** copy.ts 에서 `key: { … }` 블록 하나를 원문으로 꺼낸다. `nth` 0 이 영어(앞), 1 이 한국어다. */
export async function copyBlock(key, nth = 0) {
  const src = await srcText('copy.ts');
  const re = new RegExp(`\\b${key}: \\{`, 'g');
  let m; for (let i = 0; i <= nth; i++) { m = re.exec(src); if (!m) throw new Error(`copy.ts: no ${key} #${nth}`); }
  let depth = 0, i = m.index + m[0].length - 1;
  const from = i;
  for (; i < src.length; i++) { if (src[i] === '{') depth++; else if (src[i] === '}' && --depth === 0) break; }
  return transpile(`export default (${src.slice(from, i + 1)});`).replace(/^export default /, '').replace(/;\s*$/, '');
}

/** 카피 대역에 진짜 값을 덮는다. `over` 는 객체 리터럴 원문(예: `{ ui: { viewx: { slash: … } } }`).
 *  덮은 자리는 진짜 값이고, 나머지는 COPY_STUB 처럼 경로 이름이다. */
export const copyStub = (over) => `
const mk = (path, o) => new Proxy(function () {}, {
  get: (_, k) => {
    if (o && Object.prototype.hasOwnProperty.call(o, k)) {
      const v = o[k];
      return v && typeof v === 'object' && !Array.isArray(v) ? mk(path + '.' + String(k), v) : v;
    }
    return k === Symbol.toPrimitive || k === 'toString' ? () => path
      : k === 'then' ? undefined : mk(path + '.' + String(k));
  },
  apply: (_, __, args) => path + '(' + args.join(',') + ')',
});
export const COPY = mk('COPY', ${over});`;
