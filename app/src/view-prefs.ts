/** 그래프 보기의 기기별 설정과 보기가 함께 쓰는 작은 판단들(연결선 갈래, 날짜 머리).
 *
 *  설정은 localStorage 에 기기마다 남고 사이드카 계약은 없다. 남은 칸은 오른쪽 문서 패널의 폭(`railW`)
 *  하나다. 그래프 보기는 나침반 하나라서 보기를 고르는 칸은 없다. 나침반의 펼침·기간·종류·고정은
 *  나침반이 스스로 든다(view-compass.ts). 주소 문법의 `/as/` 칸은 MCP 서버의 파서와 나눠 쓰는 계약이라
 *  그대로 둔다(`view-url.ts`).
 *  근거: [[decision:the-graph-is-one-2d-compass-lens-2026-11-06]] */

/** 연결선 갈래(D74) — **범례가 곧 필터**다(발주자 지시 2026-08-02).
 *
 *  갈래는 렌더러가 이미 쓰던 우선순위 사슬을 그대로 이름 붙인 것이다
 *  (방향 → 다리 → 주제교차 → 나머지). 한 엣지는 정확히 한 갈래에 속하므로
 *  범례 항목 · 필터 스위치 · 실제 그려지는 선이 **같은 어휘 하나**를 쓴다.
 *  갈래를 새로 만들거나 순서를 바꾸면 세 곳이 함께 움직여야 한다 — 그래서
 *  판정 함수(`edgeKindOf`)를 여기 한 곳에 둔다. */
export type EdgeKind = "directed" | "bridge" | "cross" | "related";
/** 끝이 원천(조상)을 가리키는 관계 — "a 가 b 에서 나왔다". */
export const ANCESTRY_RELS = ["DERIVES_FROM", "INHERITS_FROM"];
/** 화살촉이 붙는 방향 3종. `edgeKindOf` 가 읽는다. */
const DIRECTED_RELS = ["PREREQUISITE_OF", ...ANCESTRY_RELS];
export function edgeKindOf(e: { type: string; cross_domain?: boolean }): EdgeKind {
  if (DIRECTED_RELS.includes(e.type)) return "directed";
  if (e.type === "CONNECTS") return "bridge";
  if (e.cross_domain) return "cross";
  return "related";
}

export type ViewPrefs = {
  railW: number;               // D70 — 우측 문서 패널 폭(rem). 끌어서 조절, 기기별 지속
};


/** 시각 문자열의 날짜 머리 `YYYY-MM-DD`(앞 10자). 변환하지 않는다 — 시각이 쓰인 달력의
 *  날을 그대로 자른다. 짧은 값은 그대로 온다. */
export const dayOf = (ts: string): string => ts.slice(0, 10);
/** UTC 달력의 `YYYY-MM-DD`. */
export const utcDay = (t: Date): string => dayOf(t.toISOString());
/** 기록 시각(UTC ISO)을 현지 시각 `YYYY-MM-DDTHH:MM:SS` 로 바꾼다. 접두가 곧 현지의 날이라
 *  상태 줄의 "최근" 날짜가 사람이 보는 달력으로 읽힌다(VIN-262: UTC 로 자르면 한국 00~09시
 *  기록이 전날로 섰다). 시각이 없는 날짜 키나 못 읽는 값은 그대로 돌려준다. */
export function localStamp(t: Date | string): string {
  if (typeof t === "string" && t.length <= 10) return t;
  const d = typeof t === "string" ? new Date(t) : t;
  if (Number.isNaN(d.getTime())) return typeof t === "string" ? t : "";
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
}

export const RAIL_MIN = 16;    // rem — 원문 한 줄이 읽히는 하한
// 상한을 크게 둔다(발주자 지시 2026-08-05) — 문서 패널은 지도 **위에 겹치는** 글라스
// 이므로, 끝까지 끌어 화면을 다 덮는 것이 정당한 상태다(읽는 동안 지도를 잠시 덮는
// 것과 지도를 잃는 것은 다르다 — 접으면 그대로 돌아온다). 실제 한계는 CSS 가
// `min(var(--rail-size), 100%)` 로 잡으므로 여기서는 화면보다 큰 값만 막는다.
export const RAIL_MAX = 120;   // rem — 사실상 화면 폭(CSS 가 100% 로 클램프)

const KEY = "vinc.view.prefs";
/** 저장값의 판. 저장은 늘 이 판(`pv`)을 싣는다: 다음에 값을 옮겨 읽을 일이 생기면 이 판과 견준다.
 *  판 5 까지의 저장값은 `view` 를 포함해 여러 칸을 들고 있고, loadPrefs 는 그중 `railW` 만 읽는다.
 *  6 = 저장하는 칸이 `railW` 하나가 된 뒤. */
const PREFS_VERSION = 6;
const DEFAULTS: ViewPrefs = {
  //: 읽는 면의 기본 폭(rem). `app.css` 의 `#docpane` 폴백과 같은 값이다.
  //: ⚠ 한때 21 이었다 — 왼쪽 끝을 범례 버튼의 시작점에 맞추려고 상단 바 손잡이의
  //: 폭에서 계산한 값이었는데, 그 규율은 철회됐다(발주자 지시 2026-09-09).
  railW: 20,
};

/** 저장값을 읽는다. 어느 판의 저장값이든 읽힌다: 읽는 칸은 `railW` 하나이고 어느 판에서도 옮긴 적이
 *  없다. 읽지 않는 칸(이전 판의 `view` 등)은 무해하고 다음 저장에서 빠진다. 못 읽는 값은 기본으로 간다. */
export function loadPrefs(): ViewPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULTS };
    const p = JSON.parse(raw);
    return { railW: Math.min(RAIL_MAX, Math.max(RAIL_MIN, Number(p.railW) || DEFAULTS.railW)) };
  } catch {
    return { ...DEFAULTS };
  }
}

export function savePrefs(p: ViewPrefs) {
  localStorage.setItem(KEY, JSON.stringify({ ...p, pv: PREFS_VERSION }));
}
