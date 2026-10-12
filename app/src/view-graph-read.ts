import { isPlanar, isRootsLayout, serverLayoutFor, type ViewPrefs } from "./view-prefs";

/** 한 번의 그래프 읽기가 무엇을 요청하는가. 서버가 받는 인자와 재사용 키가 같은 값에서
 * 나오므로 한 곳에서 계산한다: 둘이 갈리면 다른 요청이 같은 캐시 항목을 쓴다.
 * roots 배치는 hub 좌표의 같은 인구를 받아 워커에서 다시 배치하므로 원뿔 값은 1 이다. */
export type GraphReadPlan = {
  useRoots: boolean;
  serverLayout: ReturnType<typeof serverLayoutFor>;
  cone: number;
  scopeKey: string;
  options: { domains?: string[]; layout: string; cone?: number };
};

export function graphReadPlan(p: Pick<ViewPrefs, "view" | "layout" | "cone">, domains: string[]): GraphReadPlan {
  const useRoots = !isPlanar(p.view) && isRootsLayout(p.layout);
  const serverLayout = serverLayoutFor(p.layout);
  const cone = useRoots ? 1 : p.cone;
  return {
    useRoots, serverLayout, cone,
    scopeKey: JSON.stringify({ domains: [...domains].sort(), layout: serverLayout, cone }),
    options: { ...(domains.length ? { domains } : {}), layout: serverLayout, ...(cone !== 1 ? { cone } : {}) },
  };
}

/** 첫 읽기를 단계로 나눌 수 있는가. 부분 개요는 아무것도 마운트되지 않은 첫 화면에서
 * 전체 인구를 읽을 때만 의미가 있다. 시간·고아·관계 필터가 걸리면 개요가 최종 장면과
 * 다른 모집단을 보여 주므로 쓰지 않는다. */
export function wantsStagedRead(
  p: Pick<ViewPrefs, "view" | "when" | "unlinked" | "edgeOff">,
  state: { cached: boolean; mounted: boolean; planar: boolean },
): boolean {
  return !state.cached && !state.mounted && !state.planar
    && (p.view === "2d" || p.view === "3d")
    && p.when === 1 && p.unlinked === "show" && !p.edgeOff.length;
}
