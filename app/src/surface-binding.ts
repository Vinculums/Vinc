// 표면이 셸(상단 바 · 좌측 트리 · 저절로 새로 고침)에 거는 손잡이 아홉을 한 문으로 건다. 표면은 `bindSurface` 로 걸고,
// 라우터(`main.ts route`)는 표면을 갈아 끼울 때마다 `unbindSurface` 한 번으로 걸린 것을 전부 푼다. 표면 안에서 다시
// 서는 것(그래프의 나침반)은 `bindSurface` 가 돌려준 풀기로 자기 몫만 푼다. 받는 쪽의 상태와 그 부수 효과(트리
// 다시 그리기 · 단추 자리 비우기)는 각 모듈의 setter 가 그대로 진다: 이 파일은 거는 길과 푸는 길만 하나로 모은다.
import { setViewHooks, setSearchFilter, setTopbarTools } from "./topbar";
import { setLnbNavigator, setLnbTopicPicker, setLnbScopeClearer, setLnbDocOpener, setLnbGraphSource } from "./lnb";
import { setLiveRefresher } from "./live";

type Hook<F extends (v: never) => void> = NonNullable<Parameters<F>[0]>;

/** 표면이 거는 것. 이름 하나가 셸 쪽 setter 하나다. 건 것만 적는다. */
export type SurfaceBinding = {
  /** 상단 바 · 만들기 · 묻기가 지도나 문서로 보내는 동작(`topbar.ts` ViewHooks). */
  viewHooks?: Hook<typeof setViewHooks>;
  /** 저절로 새로 고침이 지금 표면을 다시 부르는 법(`live.ts`). */
  liveRefresher?: Hook<typeof setLiveRefresher>;
  /** 좌측 트리에서 고른 노드 · 문서를 지도 위에서 여는 법(그래프). */
  lnbNavigator?: Hook<typeof setLnbNavigator>;
  /** 좌측 트리의 문서를 그 자리에서 여는 법(문서 홈). */
  lnbDocOpener?: Hook<typeof setLnbDocOpener>;
  /** 좌측 트리의 주제 고르기를 지금 표면의 범위로 받는 법. */
  lnbTopicPicker?: Hook<typeof setLnbTopicPicker>;
  /** 좌측 트리의 "전체" 단추를 표면이 먼저 받는 법. 참을 돌려주면 트리는 주소를 바꾸지 않는다. */
  lnbScopeClearer?: Hook<typeof setLnbScopeClearer>;
  /** 좌측 트리가 그릴 그래프 트리(나침반). */
  lnbGraphSource?: Hook<typeof setLnbGraphSource>;
  /** 상단 바의 그래프 단추 묶음(나침반의 시간 · 범례). */
  topbarTools?: Hook<typeof setTopbarTools>;
  /** 상단 바 검색 칸의 글자를 받는 거르개(나침반). */
  searchFilter?: Hook<typeof setSearchFilter>;
};
type Key = keyof SurfaceBinding;

/** 이름 → 받는 setter. `unbindSurface` 는 이 순서로 푼다: 상단 바 · 갱신 · 착지가 먼저다(라우터가 그렇게 풀었다). */
const SETTERS: { [K in Key]-?: (v: NonNullable<SurfaceBinding[K]> | null) => void } = {
  viewHooks: setViewHooks,
  liveRefresher: setLiveRefresher,
  lnbNavigator: setLnbNavigator,
  lnbDocOpener: setLnbDocOpener,
  lnbTopicPicker: setLnbTopicPicker,
  lnbScopeClearer: setLnbScopeClearer,
  lnbGraphSource: setLnbGraphSource,
  topbarTools: setTopbarTools,
  searchFilter: setSearchFilter,
};
const KEYS = Object.keys(SETTERS) as Key[];

/** 지금 걸린 것. 이 문으로 건 값만 든다. */
const bound: SurfaceBinding = {};

function set<K extends Key>(k: K, v: SurfaceBinding[K] | undefined): void {
  if (v === undefined) delete bound[k]; else bound[k] = v;
  // 이름과 setter 의 짝은 표가 맞춘다. 형식은 이름마다 달라 여기서는 한 꼴로 부른다.
  (SETTERS[k] as (v: unknown) => void)(v ?? null);
}

/** 손잡이를 건다. 적은 순서대로 setter 를 부른다. 돌려준 함수는 이 호출이 건 것만 풀고,
 *  그사이 같은 이름에 다른 값이 걸렸으면 그것은 두고 간다. */
export function bindSurface(binding: SurfaceBinding): () => void {
  const keys = Object.keys(binding) as Key[];
  for (const k of keys) set(k, binding[k]);
  return () => {
    for (const k of keys) if (bound[k] !== undefined && bound[k] === binding[k]) set(k, undefined);
  };
}

/** 걸린 것을 전부 푼다. 라우터가 표면을 갈아 끼울 때마다 부른다. */
export function unbindSurface(): void {
  for (const k of KEYS) if (bound[k] !== undefined) set(k, undefined);
}

/** 지금 걸린 이름들(시험이 읽는다). */
export function boundSurfaceKeys(): Key[] {
  return KEYS.filter((k) => bound[k] !== undefined);
}
