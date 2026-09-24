/** 열린 화면이 저절로 새로워진다(VIN-129).
 *
 *  계정 모드의 데스크톱과 웹은 같은 계정 그래프를 본다. 그런데 화면은 열 때만 새로웠다:
 *  MCP 가 쓴 것은 다시 열거나 새로 고쳐야 보였다. 이 모듈이 그 갱신 버튼의 자리를 없앤다.
 *
 *  방법은 폴링이다. 사이드카가 쓰기 관문에서 세는 **변경 표식**(`/changes`, `api/changes.py`)을
 *  창이 보일 때 12초마다, 그리고 창으로 돌아올 때 읽는다. 지난번과 다르면 지금 화면을
 *  다시 부른다. 값은 비교만 한다(크기에는 뜻이 없다).
 *
 *  ⚠ **사람이 무언가 하는 중이면 기다린다**(`busy`). 다시 그리기는 지도를 새로 세우고
 *  열린 상세를 닫는다. 읽거나 쓰거나 끌고 있는 사람의 화면을 바꾸면 그것은 갱신이 아니라
 *  방해다. 그래서 바뀐 것을 기억해 두고, 손을 뗀 뒤(3초 간격으로 다시 본다) 한 번 적용한다.
 *
 *  ⚠ 자기 쓰기도 표식을 바꾼다. 그래서 앱에서 핀을 꽂으면 조금 뒤 한 번 더 조용히 다시
 *  읽는다. 자기 쓰기를 빼려고 기준을 몰래 옮기면, 같은 순간의 MCP 쓰기도 함께 삼킨다.
 *  한 번 더 읽는 편이 놓치는 편보다 낫다.
 */
import { api, cfg } from "./api";
import { docEditorOpen } from "./doc-editor";
import { isTypingTarget } from "./keys";
import { refreshLnb } from "./lnb";

/** 10~15초. 짧으면 허브 왕복이 늘고(창마다 분당 5회), 길면 "MCP 가 쓴 것이 안
 *  들어갔다"로 읽힌다. 계정 프록시의 회원별 한도는 분당 300 이다. */
const EVERY_MS = 12_000;
/** 바쁜 동안 다시 볼 간격. 네트워크를 쓰지 않는다(바뀐 것은 이미 알고 있다). */
const BUSY_RETRY_MS = 3_000;

/** 지금 표면이 자기를 다시 부르는 법. `false` 를 돌려주면 "지금은 아니다"(재생 중 등). */
type Refresher = () => Promise<boolean | void> | boolean | void;
let surfaceRefresher: Refresher | null = null;

/** 표면이 마운트될 때 건다. 라우터가 표면을 갈아 끼울 때 `null` 로 푼다. */
export function setLiveRefresher(fn: Refresher | null): void {
  surfaceRefresher = fn;
}

let baseline: string | null = null;
/** 기준이 가리키는 그래프. 스페이스를 옮기면 다른 그래프의 표식이라 비교할 수 없다. */
let baselineOf = "";
let pending = false;
let checking = false;
let applying = false;
let pointerDown = false;
let retryTimer = 0;
let mounted = false;

/** 사람이 무언가 하는 중인가. 이때 다시 그리면 하던 일을 빼앗는다. */
function busy(): boolean {
  if (pointerDown) return true;                   // 지도를 끌거나 올가미를 긋는 중
  if (docEditorOpen()) return true;               // 문서를 쓰는 중
  // 시트·확인창·우클릭 메뉴·팔레트. 무엇을 고르던 중이다.
  if (document.querySelector(".sheet-scrim, .ctx-menu")) return true;
  const pal = document.getElementById("palette");
  if (pal && !pal.hidden) return true;
  // 검색어·이름을 치는 중. 판정은 `keys.ts` 한 벌을 쓴다(사본이 생기면 갈린다).
  if (isTypingTarget(document.activeElement)) return true;
  // 문서 열이 열려 있다 = 무언가를 읽는 중이다. 다시 그리기가 그 열을 닫는다.
  const pane = document.getElementById("docpane");
  if (pane && !pane.hidden) return true;
  return false;
}

async function check(): Promise<void> {
  if (checking || document.hidden) return;
  checking = true;
  try {
    const of = `${cfg.owner}|${cfg.team}`;
    const r = await api.changes(cfg.owner);
    const m = String(r?.marker || "");
    if (!m) return;
    // 닿았다. 앞서 "계정에 닿지 않는다"를 띄웠다면 거둔다(account-signals).
    dispatchEvent(new CustomEvent("vinc:account-reachable"));
    if (baseline === null || of !== baselineOf) {
      baseline = m;
      baselineOf = of;
      return;
    }
    if (m !== baseline) {
      baseline = m;
      pending = true;
    }
  } catch {
    // 조용히 넘긴다. 계정 모드의 접속 실패와 로그인 만료는 api 가 이미 알린다.
  } finally {
    checking = false;
  }
  if (pending) void apply();
}

async function apply(): Promise<void> {
  if (!pending || applying || document.hidden) return;   // 돌아오면 check 가 다시 부른다
  if (busy()) {
    clearTimeout(retryTimer);
    retryTimer = window.setTimeout(() => void apply(), BUSY_RETRY_MS);
    return;
  }
  pending = false;
  applying = true;
  try {
    const [surface] = await Promise.allSettled([
      Promise.resolve(surfaceRefresher?.()),
      // 조용히: 로딩 표시 없이 받아서 갈아 끼운다. 실패하면 있던 트리를 그대로 둔다.
      refreshLnb({ quiet: true }),
      import("./conventions").then((m) => m.refreshBadge()),
    ]);
    if (surface.status === "fulfilled" && surface.value === false) {
      // 표면이 지금은 아니라고 했다. 바뀐 것은 여전히 안 보였으므로 다시 기다린다.
      pending = true;
      clearTimeout(retryTimer);
      retryTimer = window.setTimeout(() => void apply(), BUSY_RETRY_MS);
    }
  } finally {
    applying = false;
  }
}

/** 부팅 때 한 번 건다. **첫 화면을 그리기 전에** 부른다: 기준을 데이터보다 먼저 읽어야
 *  그 사이의 쓰기가 "이미 본 것"으로 묻히지 않는다(늦게 읽으면 한 번 더 다시 읽을 뿐이다). */
export function mountLiveRefresh(): void {
  if (mounted) return;
  mounted = true;
  void check();
  window.setInterval(() => void check(), EVERY_MS);
  // 창으로 돌아왔다. 숨어 있는 동안은 읽지 않았으므로 바로 한 번 본다.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void check();
  });
  addEventListener("focus", () => void check());
  // 끄는 동안은 바쁘다. 캡처 단계로 듣는다: 지도가 자기 이벤트를 막아도 여기엔 온다.
  addEventListener("pointerdown", () => { pointerDown = true; }, true);
  addEventListener("pointerup", () => { pointerDown = false; }, true);
  addEventListener("pointercancel", () => { pointerDown = false; }, true);
  addEventListener("blur", () => { pointerDown = false; });
}
