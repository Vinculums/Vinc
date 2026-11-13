// 나침반 테스트의 적재기 하나: 실제 view-compass.ts 를 적재하고, 그것이 들여오는 모듈의 대역과 실제 모듈 목록을 이 파일 한 곳이 갖는다.
// 나침반이 새 모듈을 들여오거나 몸통이 새 파일로 갈라지면 `compassImports` 에만 줄을 더한다(테스트 파일은 고치지 않는다).
// 문구 · 이스케이프 · 테마 토큰은 대역이고, 모델 · 이웃 모으기 · 펼친 나무 · 상태 · 전체 보기 · 키 · 색 계산 · 주제 색 표는 실제 코드다.
import { tsUrl, copyStub, COMPONENTS_STUB } from '../ts-load.mjs';

/** 테마 토큰 대역: 주제 보석 다섯 개와 두 모드의 바탕 · 글자 색 */
export const THEME_STUB = `export const topicJewels = {
  light: [0xb3261e, 0x1a5fb4, 0x2e7d32, 0xb26a00, 0x6a3fa0],
  dark: [0xff8a80, 0x82b1ff, 0x69f0ae, 0xffd180, 0xce93d8] };
export const palette = { light: { background: 0xf2f2f0, surface: 0xe9e9e6, card: 0xfbfbfa, text: 0x17171a, ring: 0x4a4f8c },
  dark: { background: 0x0a0a0a, surface: 0x121212, card: 0x1a1a1a, text: 0xededeb, ring: 0x8f96d9 } };`;

/** 문구 대역에 덮는 진짜 값의 기본: 이름 없는 뿌리의 이름표 */
export const COMPASS_COPY = "{ ui: { compass: { rootTopic: 'All topics' } } }";

let built;
/** 실제 모듈들의 data: URL. 한 프로세스에서 한 번 짓고 함께 쓴다: 같은 URL 은 같은 모듈 인스턴스라,
 *  나침반과 다른 표면(lnb)이 이 `table` 을 대역으로 받으면 한 주제 색 표를 함께 읽는다. */
export function compassModules() {
  built ??= (async () => {
    const tint = await tsUrl('view-tint.ts', { './theme': THEME_STUB, './area-key': await tsUrl('area-key.ts') });
    const table = await tsUrl('topic-tint-table.ts', { './view-tint': tint });
    const model = await tsUrl('surfaces/view-compass-model.ts', { '../topic-tint-table': table });
    const gather = await tsUrl('surfaces/view-compass-gather.ts', { './view-compass-model': model });
    const state = await tsUrl('surfaces/view-compass-state.ts');
    return {
      tint,
      table,
      model,
      gather,
      tree: await tsUrl('surfaces/view-compass-tree.ts', { './view-compass-state': state, './view-compass-gather': gather }),
      state,
      whole: await tsUrl('surfaces/view-compass-whole.ts'),
      keys: await tsUrl('keys.ts'),
    };
  })();
  return built;
}

/** view-compass.ts 의 import 지정자 → 대역 원문 또는 실제 모듈 URL */
async function compassImports(copy) {
  const m = await compassModules();
  return {
    '../copy': copyStub(copy),
    '../components': COMPONENTS_STUB,
    '../view-tint': m.tint,
    '../topic-tint-table': m.table,
    '../theme': THEME_STUB,
    './view-compass-model': m.model,
    './view-compass-gather': m.gather,
    './view-compass-tree': m.tree,
    './view-compass-state': m.state,
    './view-compass-whole': m.whole,
    '../keys': m.keys,
  };
}

/** 실제 view-compass.ts 를 적재한다. `copy` 는 문구 대역에 덮을 진짜 값(객체 리터럴 원문),
 *  `patch` 는 옮겨 쓴 JS 를 적재 전에 고치는 함수(안쪽 값 하나를 읽으려고 전역으로 내보낼 때). */
export async function loadCompass({ copy = COMPASS_COPY, patch } = {}) {
  return import(await tsUrl('surfaces/view-compass.ts', await compassImports(copy), { patch }));
}
