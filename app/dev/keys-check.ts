/** 전역 단축키가 비켜설 자리 — 브라우저도 프레임워크도 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/keys-check.ts
 *
 *  ⚠ **왜 이게 있어야 하나**: 이 판정은 여태 거울이 둘이었다 — `main.ts` 의 전역
 *  핸들러와 `view.ts` 의 표면 핸들러가 각자 구현했고, 한쪽만 `contenteditable` 을
 *  보게 고쳐져 있었다. CodeMirror 6 의 편집면이 `div.cm-content[contenteditable]`
 *  이라 나머지 한쪽은 절대 비켜서지 않았고, 편집기에 포커스가 있어도 `1`·`2`·`?`
 *  가 새어 나갔다(2026-09-07 실측 — `1`·`2` 는 본문에 글자를 넣으면서 동시에 표면을
 *  갈아엎었다). 지금은 `src/keys.ts` 한 벌이고, 그것이 **실제로 무엇을 막는지**를
 *  여기서 돌려 본다. 소스에 그 이름이 적혀 있는지가 아니라 판정 자체를 건다.
 *
 *  `keys.ts` 가 DOM 을 안 쓰는 것(오리 타이핑)이 이 검사가 성립하는 조건이다.
 *  잡기와 물음은 모듈에 하나뿐인 상태라 순서대로 쌓고 걷는다: 끝마다 "아무것도 안 떠 있으면 다 돈다"를 본다. */
import assert from "node:assert/strict";
import { closeMenuLayers, escapeTop, isTypingTarget, pushLayer, restWhile, shortcutsRest } from "../src/keys.ts";
const holdShortcuts = (alive: () => boolean = () => true) => pushLayer({ alive });

let n = 0;
const ok = (label: string) => { n += 1; console.log("  ok", label); };
const surface = (target: unknown = null, active: unknown = null, editor = false) =>
  shortcutsRest("surface", target as EventTarget, active as EventTarget, editor);
const command = (target: unknown = null, active: unknown = null) =>
  shortcutsRest("command", target as EventTarget, active as EventTarget);
/** 아무것도 안 떠 있고 입력 중도 아니면 표의 단축키가 전부 돈다. */
const allLive = (label: string) => {
  assert.equal(surface(), false, `${label}: 1·2·3·? 이 쉰다`);
  assert.equal(command(), false, `${label}: ⌘/Ctrl 단축키가 쉰다`);
};

// ── 1. 이 결함 자체 — CM6 의 편집면은 DIV 다 ────────────────────────────────
assert.equal(isTypingTarget({ tagName: "DIV", isContentEditable: true }), true);
ok("contenteditable 인 DIV 는 타이핑이다(CM6 편집면)");

// 여태의 판정(`tagName === "INPUT" || "TEXTAREA"`)이 살아 있으면 여기서 떨어진다.
assert.notEqual(isTypingTarget({ tagName: "DIV", isContentEditable: true }), false);
ok("tagName 만 보던 판정으로 되돌아가면 떨어진다");

// ── 2. 원래 막던 것들은 계속 막는다 ────────────────────────────────────────
for (const tag of ["INPUT", "TEXTAREA"]) {
  assert.equal(isTypingTarget({ tagName: tag }), true, tag);
}
ok("INPUT·TEXTAREA 는 그대로 타이핑이다");

// ── 3. 지도 위의 마크·캔버스는 단축키가 살아 있어야 한다 ────────────────────
for (const el of [{ tagName: "DIV" }, { tagName: "CANVAS" }, { tagName: "BUTTON" },
                  { tagName: "DIV", isContentEditable: false }]) {
  assert.equal(isTypingTarget(el), false, el.tagName);
}
ok("보통 요소는 단축키를 막지 않는다");

// ── 4. 대상이 없을 수 있다(document 로 오는 키) ────────────────────────────
assert.equal(isTypingTarget(null), false);
assert.equal(isTypingTarget({} as EventTarget), false);
ok("target 이 없거나 요소가 아니면 단축키가 산다");

// ── 5. 층이 없을 때: 표면 키는 입력 칸 · 편집기에서 쉬고, ⌘/Ctrl 키는 어디서나 돈다 ──────
const cm = { tagName: "DIV", isContentEditable: true }, field = { tagName: "INPUT" };
assert.equal(surface(cm), true);
ok("타이핑 중이면 표면 단축키를 막는다");
assert.equal(surface(null, { tagName: "TEXTAREA" }), true);
ok("포커스가 입력에 있으면 target 이 비어도 막는다");
assert.equal(surface(null, null, true), true);
ok("문서 열이 떠 있으면 포커스가 어디 있든 막는다");
allLive("층도 입력도 없음");
ok("문서 열이 닫혀 있고 타이핑도 아니면 단축키가 산다");
assert.equal(command(cm), false);
assert.equal(command(field, field), false);
ok("⌘/Ctrl K · ⇧K · N · ⇧N 은 입력 칸 · 편집기 안에서도 돈다");

// ── 6. 시트가 떠 있으면 표의 단축키가 전부 쉰다 ──────────────────────────────
// 설정을 연 채 초점이 시트의 버튼이나 body 에 있으면 입력 갈래가 안 걸린다.
// 시트가 잡고 놓는 수로 가른다. 겹친 시트는 마지막 하나가 닫혀야 풀린다.
const outer = holdShortcuts();
assert.equal(surface(), true);
assert.equal(command(), true);
assert.equal(command(field, field), true);
ok("시트가 떠 있으면 포커스가 어디 있든 숫자 · ? · ⌘/Ctrl 키가 쉰다(시트 안 입력 칸에서도)");
const inner = holdShortcuts();
inner();
inner();
assert.equal(surface(), true);
ok("겹친 시트 하나를 (두 번) 닫아도 아래 시트가 남아 있으면 막는다");
outer();
allLive("마지막 시트가 닫힘");
ok("마지막 시트가 닫히면 단축키가 산다");

// ── 7. 닫기를 거치지 않고 지워진 시트는 잡은 것을 남기지 않는다 ─────────────────
// 시트가 넘기는 `alive`(딤이 문서에 붙어 있는가)가 거짓이 되면 놓은 것으로 센다.
let attached = true;
const orphan = holdShortcuts(() => attached);
assert.equal(surface(), true);
attached = false;
allLive("딤이 지워짐");
ok("딤이 문서에서 빠지면 놓기를 부르지 않아도 단축키가 산다");
orphan();
allLive("늦은 놓기");
ok("늦게 온 놓기는 아무것도 바꾸지 않는다");
attached = true;
allLive("지워졌던 딤의 함수가 다시 참");
ok("한 번 풀린 잡기는 그 함수가 다시 참이 되어도 되살아나지 않는다");

// ── 8. 메뉴 · 팝오버 · 팔레트: 잡지 않고 물어본다 ───────────────────────────
// 앱은 `components.ts` 가 기동 때 한 번 건다(화면에 메뉴가 떠 있는가). 여기서는 그 답을 손으로 바꾼다.
let menuOpen = false;
const unprobe = restWhile(() => menuOpen);
allLive("메뉴 없음");
menuOpen = true;
assert.equal(surface(), true);
assert.equal(command(), true);
assert.equal(surface(field, field), true);
ok("메뉴가 떠 있으면 숫자 · ? · ⌘/Ctrl 키가 쉰다(메뉴의 찾기 칸에서도)");
menuOpen = false;
allLive("메뉴가 닫힘");
ok("메뉴가 어느 길로든 사라지면 다음 키에서 바로 산다(놓을 것이 없다)");

// ── 9. 새는 잡기가 없다: 여는 · 닫는 순서를 섞어 돌린 끝에 아무것도 안 떠 있으면 다 돈다 ──────
// 닫는 길: 놓기 · 두 번 놓기 · 닫기 없이 지움 · 겹친 순서와 다른 순서로 닫기 · 빠른 여닫기.
const order = [3, 0, 4, 1, 2];
for (let round = 0; round < 50; round++) {
  const alive = [true, true, true, true, true];
  const rel = alive.map((_, i) => holdShortcuts(() => alive[i]));
  menuOpen = round % 2 === 0;
  assert.equal(surface(), true);
  for (const i of order) {
    if ((i + round) % 3 === 0) alive[i] = false;   // 다른 화면이 딤을 걷어 감
    else rel[i]();                                 // 닫기
    if (i % 2) rel[i]();                           // 두 번 닫기
  }
  menuOpen = false;
  allLive(`섞어 닫기 ${round}`);
}
for (let i = 0; i < 200; i++) { const r = holdShortcuts(); r(); }
allLive("빠른 여닫기 200번");
ok("섞인 순서 · 두 번 닫기 · 닫기 없이 지움 · 빠른 여닫기 뒤에 아무것도 안 떠 있으면 단축키가 다 돈다");

// ── 10. Esc 는 맨 위 층 하나가 받는다(연 순서) ────────────────────────────────────
const log: string[] = [];
const layer = (name: string, extra: { menu?: boolean; swallow?: boolean } = {}) => {
  let open = true;
  const rel = pushLayer({ alive: () => open, menu: extra.menu,
    escape: extra.swallow ? undefined : () => { log.push(name); open = false; rel(); } });
  return { close: () => { open = false; rel(); }, get open() { return open; } };
};
assert.equal(escapeTop(), false);
ok("층이 없으면 Esc 를 받지 않는다(화면의 처리기로 간다)");
const pal = layer("palette");
const note = layer("notice");                   // 팔레트 위에 늦게 뜬 알림
assert.equal(escapeTop(), true); assert.deepEqual(log, ["notice"]); assert.equal(pal.open, true);
assert.equal(escapeTop(), true); assert.deepEqual(log, ["notice", "palette"]);
assert.equal(escapeTop(), false);
ok("팔레트 위의 알림: 첫 Esc 는 알림만, 둘째는 팔레트, 그다음은 받지 않는다");
log.length = 0;
const settings = layer("settings");
const confirmL = layer("confirm");
const gone = layer("removed");
gone.close();                                   // 닫기 없이 빠진 층은 건너뛴다
escapeTop(); escapeTop();
assert.deepEqual(log, ["confirm", "settings"]);
ok("설정 위의 확인: 확인 · 설정 순서, 빠진 층은 Esc 를 가져가지 않는다");
log.length = 0;
const sheet = layer("sheet");
const signin = layer("signin", { swallow: true });
assert.equal(escapeTop(), true); assert.deepEqual(log, []); assert.equal(sheet.open, true);
signin.close();
escapeTop(); assert.deepEqual(log, ["sheet"]);
ok("Esc 를 삼키는 층(로그인 · 고르기를 요구하는 시트)이 맨 위면 아무것도 닫히지 않고 키는 받힌 것으로 센다");
log.length = 0;
const under = layer("composer");
layer("ctx", { menu: true }); layer("mention", { menu: true });
closeMenuLayers();
assert.deepEqual(log, ["mention", "ctx"]); assert.equal(under.open, true);
under.close();
ok("시트가 열릴 때 메뉴 층만 위에서부터 닫힌다");
void settings; void confirmL;
allLive("Esc 검사 뒤");

unprobe();
unprobe();
menuOpen = true;
allLive("물음을 뗀 뒤");
ok("뗀 물음은 다시 묻지 않는다(두 번 떼도 같다)");

console.log(`\nkeys: ${n}개 검사 통과`);
