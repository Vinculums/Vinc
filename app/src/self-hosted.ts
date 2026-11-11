/** 셀프호스트 허브에서 도는 웹 Vinc(계획 `vinc.plan.self-hosted-hub-web-2026-11-11` S3).
 *
 *  두 군데서 안다:
 *  - 빌드 상수 `VITE_SELF_HOSTED=1`: 허브 이미지에 들어가는 번들(Dockerfile.hub)이 단다. 허브를
 *    localhost 로 열어도 웹 Vinc 로 돌게 하고(`api.ts` `WEB`), 첫 페인트 전에 사이트
 *    머리(GNB)를 안 부르게 한다.
 *  - 허브의 `/api/me` 가 싣는 `self_hosted: true`: 세션을 확인할 때 받는다(`ensureWebAuth`).
 *
 *  둘 중 하나라도 참이면 vincs.io 계정이 필요한 것(팀·커뮤니티·팀으로 옮기기·요금제·알림·
 *  사이트 머리)을 그리지 않는다. 이 모듈은 아무것도 import 하지 않는다(api.ts 가 먼저 읽는다). */

export const SELF_HOSTED_BUILD: boolean = import.meta.env.VITE_SELF_HOSTED === "1";

let answeredByHub = false;

/** 셀프호스트 허브에서 도는가. */
export function selfHosted(): boolean {
  return SELF_HOSTED_BUILD || answeredByHub;
}

/** `/api/me` 의 답을 본다. 허브가 셀프호스트라고 했으면 기억한다(되돌리지 않는다). */
export function noteMe(body: unknown): void {
  if (body && typeof body === "object" && (body as { self_hosted?: unknown }).self_hosted === true) {
    answeredByHub = true;
  }
}

/** 이 허브의 MCP 주소. 셀프호스트면 지금 연 주소의 `/mcp`, 아니면 호스티드 허브다. */
export function mcpUrl(): string {
  return selfHosted() ? `${location.origin}/mcp` : "https://mcp.vincs.io/mcp";
}
