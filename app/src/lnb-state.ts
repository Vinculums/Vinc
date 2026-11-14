import type { AgentMapLite } from "./lnb-agents";
import type { LnbGraphSource } from "./lnb-graph";
import type { ThemeMode } from "./theme";
import { topicTint } from "./topic-tint-table";

/** 좌측 트리의 입구(`lnb.ts`)와 세 갈래 — 문서(`lnb-docs.ts`) · 그래프(`lnb-graph.ts`) · Agent(`lnb-agents.ts`) — 가
 *  함께 읽고 쓰는 상태 `st` 하나와, 그들이 같이 쓰는 작은 읽기 도구.
 *  둘 이상이 쥐는 값은 모두 여기에 선다. 한 곳에서만 쓰는 값은 그 파일의 지역 변수로 남는다. 여기는 어느 갈래도 값으로
 *  가져오지 않는다(형식만 가져온다): 그래서 갈래와 입구가 모두 여기를 가져와도 고리가 생기지 않는다. */

export const KEY_KIND = "vinc.shell.lnb.kind";   // 맨 위 줄이 고른 것(홈 = 빈 값 · Agent)

type LnbState = {
  active: { domain?: string; nodeId?: string; docId?: string };
  picked: Set<string>;
  expanded: Set<string>;
  seenDefault: Set<string>;
  railKey: string;
  gsrc: LnbGraphSource | null;
  gActive: string;
  gExpanded: Set<string>;
  gSeen: Set<string>;
  agentMap: AgentMapLite | null;
  agentPick: string;
  agentCol: Map<string, string>;
  polCol: string;
};

export const st: LnbState = {
  active: {},
  /** 지금 스코프가 걸고 있는 주제들 — 트리 줄의 선택 표시가 이걸 읽는다. */
  picked: new Set<string>(),
  /** 펼친 주제(와 `own:` 접두의 항목 묶음). **저장하지 않는다**: 앱을 켤 때마다 기본
   *  펼침(`lnb-tree.ts`)에서 시작한다. `seenDefault` 는 기본을 이미 한 번 받은 경로다 —
   *  세션 안에서 사용자가 접은 주제를 다시 그릴 때마다 되살리지 않으려는 것이다. */
  expanded: new Set<string>(),
  seenDefault: new Set<string>(),
  /** 맨 위 줄의 선택. **빈 문자열 = 홈**(아무것도 안 고름 = 전체, 발주자 지시 2026-09-09).
   *  다른 값은 `agent` 하나다. 종 레일이 쓰던 저장값(`concept` 등)은 홈으로 읽는다. */
  railKey: localStorage.getItem(KEY_KIND) === "agent" ? "agent" : "",
  gsrc: null,
  gActive: "",
  gExpanded: new Set<string>(),
  gSeen: new Set<string>(),
  agentMap: null,
  agentPick: "",
  // Agent 화면의 색 = Agent 단위(보드와 같은 표, agent-map.agentColours)
  agentCol: new Map<string, string>(),
  polCol: "",
};

export const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T | null;

/** 주제 색(보석색, 늘 켜짐)은 앱 전체의 표 하나(`topic-tint-table`)에서 읽는다. 트리는 자기가 그리는 주제를 그 표에 더하고,
 *  표가 바뀌면(다른 표면이 주제를 더함) 다시 그린다. 그래서 같은 주제가 트리 · 홈 목록 · 그래프에서 같은 색이다. */
export const themeMode = (): ThemeMode =>
  document.documentElement.dataset.theme === "dark" ? "dark" : "light";
/** 줄의 style 에 얹을 조각. ⚠ 깊이 잉크는 CSS 가 **부분문자열**로 읽는다
 *  (app.css 의 `[style*="--ink:0"]`) — `--ink:N` 리터럴을 깨지 않도록 **뒤에** 붙인다. */
export const tintStyle = (domain: string): string => {
  const c = topicTint(domain, themeMode());
  return c ? `;--tint:${c}` : "";
};

/** 지금 트리가 쓰는 펼침 집합 — 그래프면 그래프 것, 아니면 문서 것. */
export const exp = (): Set<string> => (st.gsrc ? st.gExpanded : st.expanded);
