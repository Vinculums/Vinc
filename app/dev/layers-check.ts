/** 겹친 층의 맨 위 판정 — 브라우저 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/layers-check.ts
 *
 *  시트의 Tab 덫과 Esc 는 자기가 맨 위 층일 때만 움직인다. 딤(`.sheet-scrim`)만 세면 딤 밖의
 *  모달 화면(로그인 · 기기 그래프 알림)이 위에 떠도 아래 시트가 Tab 을 끌어가므로, 그 판정을
 *  여기서 가짜 요소로 돌려 본다. `src/layers.ts` 가 DOM 을 직접 부르지 않는 것이 조건이다. */
import assert from "node:assert/strict";
import { topLayerOf } from "../src/layers.ts";

let n = 0;
const ok = (label: string) => { n += 1; console.log("  ok", label); };
type Fake = { name: string; scrim: boolean; inScrim: Fake | null;
  matches(s: string): boolean; closest(s: string): unknown };
const el = (name: string, scrim: boolean, inScrim: Fake | null = null): Fake => {
  const f: Fake = { name, scrim, inScrim,
    matches: (s) => s === ".sheet-scrim" && scrim,
    closest: (s) => s === ".sheet-scrim" ? (scrim ? f : inScrim) : null };
  return f;
};

const settings = el("settings-scrim", true);
const settingsBox = el("settings .sheet[aria-modal]", false, settings);
assert.equal(topLayerOf([settings, settingsBox])?.name, "settings-scrim");
ok("시트 상자의 aria-modal 은 따로 세지 않는다 — 그 딤이 맨 위다");

const signin = el("#signin-screen", false);
assert.equal(topLayerOf([settings, settingsBox, signin])?.name, "#signin-screen");
ok("시트 뒤에 붙은 로그인 화면이 맨 위다 — 아래 시트는 Tab 을 가져가지 않는다");

const deviceGraph = el("#device-graph-screen", false);
assert.equal(topLayerOf([settings, settingsBox, deviceGraph])?.name, "#device-graph-screen");
ok("기기 그래프 알림도 같은 규칙이다(아이디가 아니라 딤 밖의 aria-modal 이라서)");

assert.equal(topLayerOf([settings, settingsBox])?.name, "settings-scrim");
ok("로그인 화면이 닫히면(목록에서 빠지면) 시트가 다시 맨 위다");

const confirm = el("confirm-scrim", true);
const confirmBox = el("confirm .sheet", false, confirm);
assert.equal(topLayerOf([settings, settingsBox, confirm, confirmBox])?.name, "confirm-scrim");
ok("시트 위의 확인 다이얼로그가 맨 위다");

const later = el("sheet opened over signin", true);
assert.equal(topLayerOf([signin, later, el("box", false, later)])?.name, "sheet opened over signin");
ok("로그인 화면 뒤에 연 시트는 그 위에 선다(문서 순서)");

assert.equal(topLayerOf([]), null);
ok("층이 없으면 null");

console.log(`\nlayers: ${n}개 검사 통과`);
