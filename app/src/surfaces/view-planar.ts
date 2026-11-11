/** 그래프 표식 — 범례가 그리는 3D 실루엣 한 벌.
 *
 *  ⚠ 파일 이름과 `MARK3D_ART` 라는 이름을 바꾸지 않는다. 홈페이지 갤러리가 이 표식의
 *  사본을 들고 있고, 그 리포의 `scripts/gate_node_marks.mjs` 가 이 경로에서 이 상수를
 *  읽어 글자 대조한다(못 읽으면 사이트 배포가 멈춘다). 값을 바꾸면 사이트 사본도 같은
 *  묶음에서 다시 베낀다.
 *
 *  툴팁은 쓰지 않는다. `<svg>` 는 `aria-hidden` 이고 이름은 범례 줄의 글자가 맡는다. */

/** 3D 실루엣 표식 — 걷어 낸 3D 렌더러의 몸통 키트 모양을 정면에서 줄여
 *  그린 것이다: 유리구(개념은 빛 구름, 결정은 안의 결정) · 정사면체(기록) · 정팔면체
 *  (사건) · 유리 조각(다리) · 점 껍질(주제) · 윤곽선(관문). 앱 안에 부르는 곳은 없다.
 *  ⚠ 지우지 말 것: 사이트 갤러리의 사본(Vinc-homepage `site/assets/graph-marks.js`)을
 *  그쪽 `scripts/gate_node_marks.mjs` 가 이 파일의 `MARK3D_ART` 와 대조하고, 못 읽으면 실패한다.
 *  근거: [[vinc.research.graph-vinc-orb-2026-10-28]]
 *
 *  `__GID__` 는 구의 상단 하이라이트가 쓰는 그라디언트 id 자리다(백틱 문자열
 *  안이므로 `${...}` 로 두면 **정의 시점에 보간된다** — 실측으로 물렸다). 호출부가 서로 다른
 *  값을 넣어야 한다 — 같은 문서에 구가 여럿 서면 id 가 겹치고, 그때 브라우저는 첫
 *  정의만 쓴다(지금은 값이 같아 눈에 안 보이지만, 하나를 고치는 날 나머지가 조용히
 *  안 따라온다). */
const MARK3D_ART: Record<string, string> = {
    // ds-allow: 이 흰색은 팔레트가 아니라 **조명**이다 — 구 실루엣의 상단 하이라이트
    // (램버트 음영의 밝은 면)를 30% 투명 백색으로 흉내낸다. 잉크는 currentColor 다.
    concept: `<defs><radialGradient id="__GID__" cx="52%" cy="26%" r="72%"><stop offset="0" stop-color="#fff" stop-opacity=".30"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs><circle cx="12" cy="12" r="8.4" fill="currentColor" opacity=".55"/><ellipse cx="10.6" cy="13" rx="4.6" ry="3.4" fill="currentColor" opacity=".7"/><ellipse cx="13.6" cy="11.2" rx="2.6" ry="1.9" fill="#fff" opacity=".28"/><circle cx="12" cy="12" r="8.4" fill="url(#__GID__)"/><circle cx="12" cy="12" r="8.1" fill="none" stroke="currentColor" stroke-width=".7"/>`,
    // ds-allow: 결정 안의 결정도 같은 **조명**이다 — 빛을 받은 결정 면을 백색으로 흉내낸다.
    decision: `<defs><radialGradient id="__GID__" cx="52%" cy="26%" r="72%"><stop offset="0" stop-color="#fff" stop-opacity=".30"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs><circle cx="12" cy="12" r="8.4" fill="currentColor" opacity=".42"/><polygon points="12,8 15.4,12 12,16.2 8.6,12" fill="#fff" opacity=".85"/><polygon points="12,8 15.4,12 12,12" fill="#fff" opacity=".55"/><circle cx="12" cy="12" r="8.4" fill="url(#__GID__)"/><circle cx="12" cy="12" r="8.1" fill="none" stroke="currentColor" stroke-width=".7"/>`,
    record: `<polygon points="7.39,16.96 16.31,17.88 18.39,6.51" fill="currentColor" opacity=".55"/><polygon points="5.9,6.66 7.39,16.96 18.39,6.51" fill="currentColor" opacity=".85"/><path d="M5.9 6.66 7.39 16.96 16.31 17.88 18.39 6.51Z M7.39 16.96 18.39 6.51" fill="none" stroke="currentColor" stroke-width=".7"/>`,
    episode: `<polygon points="20.9,11.94 12.16,15.16 12,20.32" fill="currentColor" opacity=".5"/><polygon points="3.1,12.06 12,20.32 12.16,15.16" fill="currentColor" opacity=".5"/><polygon points="3.1,12.06 12.16,15.16 12,3.68" fill="currentColor" opacity=".82"/><polygon points="20.9,11.94 12,3.68 12.16,15.16" fill="currentColor" opacity=".9"/><path d="M12 3.68 20.9 11.94 12 20.32 3.1 12.06Z" fill="none" stroke="currentColor" stroke-width=".7"/>`,
    bridge: `<polygon points="2.6,14.2 13.4,8.4 21.4,9.6 12.6,15.8" fill="currentColor" opacity=".55"/><polygon points="2.6,14.2 13.4,8.4 21.4,9.6" fill="currentColor" opacity=".85"/><path d="M2.6 14.2 13.4 8.4 21.4 9.6 12.6 15.8Z M2.6 14.2 21.4 9.6" fill="none" stroke="currentColor" stroke-width=".7"/>`,
    topic: `<g fill="currentColor">${[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map((k) => { const t = k / 12 * Math.PI * 2;
      return `<circle cx="${(12 + Math.cos(t) * 8.6).toFixed(2)}" cy="${(12 + Math.sin(t) * 8.6).toFixed(2)}" r="1.15"/>`; }).join("")}${[0, 1, 2, 3, 4, 5].map((k) => { const t = (k + .5) / 6 * Math.PI * 2;
      return `<circle cx="${(12 + Math.cos(t) * 4.4).toFixed(2)}" cy="${(12 + Math.sin(t) * 4.4).toFixed(2)}" r=".85" opacity=".5"/>`; }).join("")}</g>`,
    // 영역 = 시간을 따라 늘인 유리 부피. 가장자리만 짙다(셰이더의 뒷면 프레넬과 같은 읽힘).
    area: `<ellipse cx="12" cy="12" rx="10.6" ry="6.4" fill="currentColor" opacity=".08"/><ellipse cx="12" cy="12" rx="10.6" ry="6.4" fill="none" stroke="currentColor" stroke-width="1.4" opacity=".45"/><ellipse cx="12" cy="12" rx="9.8" ry="5.6" fill="none" stroke="currentColor" stroke-width=".6" opacity=".25"/>`,
    gateway: `<path d="M8.09 4.59L15.32 5.62M15.32 5.62L7.72 9.43M8.09 4.59L7.72 9.43M7.72 9.43L3.62 12.21M8.09 17.95L7.72 9.43M8.09 17.95L3.62 12.21M15.32 13.88L15.32 5.62M15.32 13.88L7.72 9.43M15.32 5.62L20.38 11.79M8.09 17.95L8.68 18.38M8.09 17.95L15.91 19.41M8.09 17.95L15.32 13.88M15.91 19.41L15.32 13.88M15.32 13.88L20.38 11.79M15.91 19.41L20.38 11.79" fill="none" stroke="currentColor" stroke-width=".7" opacity=".22"/><path d="M8.09 4.59L3.62 12.21M8.68 10.12L3.62 12.21M8.09 4.59L8.68 10.12M15.91 6.05L8.68 10.12M8.09 4.59L15.91 6.05M8.68 10.12L16.28 14.57M15.91 6.05L16.28 14.57M8.68 18.38L3.62 12.21M8.68 18.38L8.68 10.12M15.91 6.05L20.38 11.79M15.91 19.41L16.28 14.57M8.68 18.38L16.28 14.57M15.91 19.41L8.68 18.38M20.38 11.79L16.28 14.57" fill="none" stroke="currentColor" stroke-width="1" opacity=".55"/>`,
};

/** 노드 종 → 3D art 키. 걷어 낸 3D 몸통 셰이더와 같은 갈래로 나눴다. 이름 없는 종은 기록이다. */
const ART_KEY = (kind: string | undefined): string =>
  kind === "bridge" || kind === "episode" || kind === "decision" || kind === "area" ? kind
  : kind === "domain" || kind === "topic" ? "topic"
  : kind === "concept" || !kind ? "concept"
  : "record";

/** 3D 표식 한 벌. `gid` 는 문서 안에서 유일해야 한다(위 주석). */
export function mark3dSvg(kind: string | undefined, px: number, gid: string,
                          cls = ""): string {
  const art = (MARK3D_ART[ART_KEY(kind)] || MARK3D_ART.concept!)
    .replace(/__GID__/g, gid);
  return `<svg class="${cls}" width="${px}" height="${px}" viewBox="0 0 24 24"`
    + ` aria-hidden="true">${art}</svg>`;
}

/** 관문(실체 없음)은 종이 아니라 상태라 따로 부른다 — 범례만 쓴다. */
export function gateway3dSvg(px: number, cls = ""): string {
  return `<svg class="${cls}" width="${px}" height="${px}" viewBox="0 0 24 24"`
    + ` aria-hidden="true">${MARK3D_ART.gateway}</svg>`;
}
