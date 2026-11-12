/** 전역 단축키가 비켜설 자리 — 판정은 여기 **한 벌**만 산다.
 *
 *  `INPUT|TEXTAREA` 만 보면 안 된다: CodeMirror 6 의 편집면은
 *  `div.cm-content[contenteditable=true]` 라 그 검사에 **절대 안 걸린다.** 여태 이
 *  계약을 main.ts(전역)와 view.ts(표면)가 각자 구현했고 한쪽만 고쳐져 있어서,
 *  편집기에 포커스가 있어도 `1`·`2`·`?` 가 새어 나갔다 — `1`·`2` 는 본문에
 *  글자를 넣으면서 **동시에** 표면을 갈아엎었다(실측 2026-09-07).
 *
 *  DOM 을 안 쓴다(오리 타이핑). 그래서 `app/dev/keys-check.ts` 가 브라우저 없이
 *  이 판정을 그대로 돌린다 — 앱에는 JS 테스트 러너가 없다. */
export function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as { tagName?: string; isContentEditable?: boolean } | null;
  if (!el) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA"
    || el.isContentEditable === true;
}

/** 단축키 표(`shortcuts.ts`)의 두 갈래.
 *  `surface`: 1 · 2 · 3 과 `?`. 글자이기도 하므로 입력 칸과 편집기에서는 글자로 들어간다.
 *  `command`: ⌘/Ctrl K · ⌘/Ctrl ⇧ K · ⌘/Ctrl N · ⌘/Ctrl ⇧ N. 입력 중에도, 편집기 안에서도 동작한다. */
export type ShortcutKind = "surface" | "command";

/** 이 단축키가 지금 쉬어야 하는가 — 표의 단축키를 받는 처리기는 전부 이것 하나에 묻는다.
 *
 *  맨 위에 층(시트 · 다이얼로그 · 모달 화면 · 메뉴 · 팝오버 · 팔레트)이 떠 있으면 두 갈래 다 쉰다.
 *  Esc 는 표에 있지만 여기 묻지 않는다: Esc 는 맨 위 층 하나가 받는다(아래 `escapeTop`).
 *  층이 없으면 `surface` 만 입력 칸 · 편집기에서 쉰다.
 *
 *  문서 열은 View 옆에 `autoFocus:false` 로 열린다(열자마자 키보드를 뺏으면 Esc 같은 조작이
 *  글자로 들어간다). 그래서 포커스가 아직 View·트리에 남아 있는데도 숫자가 `#search` 로
 *  새어 나갔다 — 문서를 읽는 중에 화면이 통째로 갈렸다(F-DOC-KB-01, 2026-09-15 실측).
 *  편집기가 떠 있는지는 부르는 쪽이 재서 `editorOpen` 으로 넘긴다(`doc-editor.docEditorOpen`).
 *
 *  ⚠ **DOM 은 여기서 읽지 않는다.** 이 파일이 DOM 을 안 쓰는 것이 `app/dev/keys-check.ts`
 *  를 성립시킨다. 화면에 무엇이 떠 있는지는 아래 쌓임과 `restWhile` 로 받은 함수만 안다. */
export function shortcutsRest(
  kind: ShortcutKind,
  target: EventTarget | null,
  active: EventTarget | null,
  editorOpen = false,
): boolean {
  if (layerOnTop()) return true;
  if (kind === "command") return false;
  return editorOpen || isTypingTarget(target) || isTypingTarget(active);
}

/** 열린 층의 쌓임: 시트 · 다이얼로그 · 모달 화면 · 팔레트 · 메뉴가 열 때 올리고 닫을 때 내린다. 나중에 올린 것이
 *  맨 위다. 층이 떠 있는 동안 표의 단축키는 쉬고(`shortcutsRest`), Esc 는 맨 위 층 하나만 받는다(`escapeTop`).
 *
 *  초점이 시트 안 버튼이나 body 에 있으면 입력 칸 검사가 안 걸린다. 그러면 설정을 연 채로 숫자가
 *  뒤 화면을 바꾸고 `?` 가 시트를 하나 더 쌓는다. 층을 여는 쪽이 올리고 내린다 — 부르는 쪽마다
 *  따로 재지 않으므로 모든 처리기가 함께 쉰다.
 *  `alive` 가 거짓이 된 층은 내린 것으로 센다: 닫기를 거치지 않고 지워지거나 숨어도(다른 화면이 걷어
 *  가도) 단축키와 Esc 가 영영 막히지 않는다.
 *  `escape` 는 그 층의 Esc 다(닫고 초점을 연 자리로 돌린다). 없으면 Esc 를 삼킨다: 고르기를 요구하는
 *  시트와 로그인 화면은 Esc 로 닫히지 않고, 그 뒤의 화면도 그 Esc 를 듣지 않는다.
 *  `menu` 인 층은 시트가 열릴 때 함께 닫힌다(`closeMenuLayers`).
 *  돌려준 함수는 내리기다. 두 번 불러도 한 번만 내린다. */
export type LayerOpts = { alive: () => boolean; escape?: () => void; menu?: boolean };
const stack: LayerOpts[] = [];
export function pushLayer(opts: LayerOpts): () => void {
  const entry: LayerOpts = { ...opts };
  stack.push(entry);
  return () => { const i = stack.indexOf(entry); if (i >= 0) stack.splice(i, 1); };
}

/** 살아 있는 층만 남긴다(문서 순서가 아니라 연 순서). */
function live(): LayerOpts[] {
  for (let i = stack.length - 1; i >= 0; i--) if (!stack[i].alive()) stack.splice(i, 1);
  return stack;
}

/** Esc 를 맨 위 층에 준다. 받을 층이 있었으면 참: 부르는 쪽은 그 키를 멈춰 뒤의 누구도 듣지 않게 한다.
 *  층이 없으면 거짓이고, 그때 Esc 는 main 과 같이 화면의 처리기(레일 닫기 · 그래프 뿌리 · 검색 칸)로 간다. */
export function escapeTop(): boolean {
  const s = live();
  const top = s[s.length - 1];
  if (!top) return false;
  top.escape?.();
  return true;
}

/** 떠 있는 메뉴 층을 위에서부터 각자의 Esc 로 닫는다. 시트 · 다이얼로그가 열릴 때 부른다. */
export function closeMenuLayers(): void {
  for (const l of [...live()].reverse()) if (l.menu) l.escape?.();
}

/** 잡기 없이 **물어서** 쉬게 하는 층: 쌓임에 올리지 못하는 메뉴(공간 전환 메뉴 · 시간과 범례 창)와 그 밖의
 *  열린 메뉴. `open` 은 키가 올 때마다 불린다. 남겨 둘 상태가 없으므로 어느 길로 닫히든 다음 키에서 바로
 *  풀린다. 앱은 기동 때 한 번 건다(`components.ts`). 돌려준 함수는 떼기다. */
const probes = new Set<() => boolean>();
export function restWhile(open: () => boolean): () => void {
  const entry = () => open();
  probes.add(entry);
  return () => { probes.delete(entry); };
}

function layerOnTop(): boolean {
  if (live().length > 0) return true;
  for (const open of probes) if (open()) return true;
  return false;
}
