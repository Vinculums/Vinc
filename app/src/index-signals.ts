/** 사이드카가 검색 색인을 새로 만드는 동안 화면 위에 거는 한 줄(VIN-272).
 *
 *  업데이트 뒤 첫 기동에서 사이드카는 전문 색인을 `Chunk.lex` 로 옮긴다(`VINC_LEX_BACKFILL=
 *  background`, 셸이 그 값으로 띄운다). 그동안 사이드카는 스토어를 혼자 쥐어 검색과 저장이
 *  기다리거나 잠깐 실패한다. 말하지 않으면 그게 고장으로 읽힌다.
 *
 *  신호는 `api.ts` 가 `/health` 의 `migrating` 을 보고 낸다(import 순환을 피하려 이벤트다):
 *    `vinc:index-migrating`  이관 중 → 한 줄을 건다
 *    `vinc:index-ready`      끝났다 → 거둔다
 *  이 모듈이 이벤트보다 늦게 걸릴 수 있으므로, 걸릴 때 현재 상태(`indexMigrating`)를 한 번 본다.
 *  근거: [[vinc.plan.korean-retrieval-p1-p2-2026-10-02 §2.2]]
 */
import { COPY } from "./copy";
import { esc } from "./components";
import { indexMigrating } from "./api";

let notice: HTMLElement | null = null;

function show(): void {
  if (notice) return;
  notice = document.createElement("div");
  // 표면(`#surface`)은 이동마다 다시 그려지므로 그 밖에 붙인다. 오류가 아니라 상태라 status 다.
  notice.className = "index-notice";
  notice.setAttribute("role", "status");
  notice.innerHTML = `<span>${esc(COPY.searchIndex.migrating)}</span>`;
  document.body.appendChild(notice);
}

function hide(): void {
  notice?.remove();
  notice = null;
}

/** 부팅 때 한 번 건다(셸 전용). */
export function mountIndexSignals(): void {
  addEventListener("vinc:index-migrating", show);
  addEventListener("vinc:index-ready", hide);
  if (indexMigrating()) show();
}
