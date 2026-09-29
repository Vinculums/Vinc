/** 끌어서 잇기의 상태 기계 — 브라우저 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/drag-connect-check.ts
 *
 *  렌더러(3D·2D)는 `hitAt`·`lock`·`onConnect` 셋만 넘기므로, 가짜 요소 하나에 포인터
 *  이벤트를 흘려 넣으면 두 렌더러가 공유하는 규칙을 그대로 돌려 볼 수 있다: Alt 가 아니면
 *  손대지 않는다 · 다른 노드 위에서 놓을 때만 부른다 · 빈 곳·같은 노드·Esc·취소는 아무것도
 *  안 부른다 · 끄는 동안만 잠근다. `drag-connect.ts` 가 import 0 인 것이 성립 조건이다. */
import assert from "node:assert/strict";
import { wireDragConnect, type DragEnd, type DragHit } from "../src/surfaces/drag-connect.ts";

let n = 0;
const ok = (label: string) => { n += 1; console.log("  ok", label); };

// ── 가짜 DOM — 이 모듈이 실제로 부르는 것만 ─────────────────────────────────
type Fake = { tag: string; attrs: Record<string, string>; gone: boolean;
  setAttribute(k: string, v: string): void; removeAttribute(k: string): void;
  append(...c: Fake[]): void; remove(): void };
const made: Fake[] = [];
(globalThis as any).document = Object.assign(new EventTarget(), {
  createElementNS: (_ns: string, tag: string): Fake => {
    const f: Fake = { tag, attrs: {}, gone: false,
      setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
      append() { /* 자식은 made 로 본다 */ }, remove() { this.gone = true; } };
    made.push(f);
    return f;
  },
});
(globalThis as any).window = Object.assign(new EventTarget(), { setTimeout });

class El extends EventTarget {
  captured = -1;
  kids: Fake[] = [];
  getBoundingClientRect() { return { left: 0, top: 0 }; }
  setPointerCapture(id: number) { this.captured = id; }
  releasePointerCapture() {
    this.captured = -1;
    this.dispatchEvent(new Event("lostpointercapture"));
  }
  appendChild(f: Fake) { this.kids.push(f); }
}

const A: DragHit = { id: "concept:a", title: "A", x: 10, y: 10, r: 8 };
const B: DragHit = { id: "concept:b", title: "B", x: 100, y: 100, r: 8 };
const R: DragHit = { id: "record:r", title: "R", x: 200, y: 10, r: 8 };
const hitAt = (x: number, y: number) =>
  [A, B, R].find((h) => Math.hypot(h.x - x, h.y - y) <= h.r) ?? null;

function rig(allowed?: (a: string, b: string) => boolean) {
  const el = new El();
  const calls: [DragEnd, DragEnd][] = [];
  const locks: boolean[] = [];
  const off = wireDragConnect(el as unknown as HTMLElement, {
    hitAt, lock: (on) => locks.push(on), onConnect: (a, b) => calls.push([a, b]), allowed });
  const ptr = (type: string, x: number, y: number, mods: { alt?: boolean; shift?: boolean } = {}) => {
    const e = Object.assign(new Event(type, { cancelable: true }), {
      clientX: x, clientY: y, altKey: !!mods.alt, shiftKey: !!mods.shift, button: 0, pointerId: 7 });
    el.dispatchEvent(e);
    return e;
  };
  const key = (k: string) => {
    const e = Object.assign(new Event("keydown", { cancelable: true }), { key: k });
    (globalThis as any).document.dispatchEvent(e);
    return e;
  };
  return { el, calls, locks, off, ptr, key };
}
const layerOf = (el: El) => el.kids[el.kids.length - 1];
const ringOf = () => made.filter((f) => f.tag === "circle").at(-1)!;

// ── 1. Alt 가 아니면 손대지 않는다 ──────────────────────────────────────────
{
  const t = rig();
  const d = t.ptr("pointerdown", 10, 10);
  t.ptr("pointerup", 100, 100);
  assert.equal(d.defaultPrevented, false);
  assert.deepEqual(t.calls, []); assert.deepEqual(t.locks, []);
  const s = t.ptr("pointerdown", 10, 10, { alt: true, shift: true });
  t.ptr("pointerup", 100, 100, { alt: true, shift: true });
  assert.equal(s.defaultPrevented, false);
  assert.deepEqual(t.calls, []);
  ok("맨 끌기(노드 옮기기·회전)와 Shift 끌기(영역 선택)는 그대로 지나간다");
}

// ── 2. 빈 곳에서 시작한 Alt 끌기도 지나간다 ──────────────────────────────────
{
  const t = rig();
  const d = t.ptr("pointerdown", 50, 50, { alt: true });
  assert.equal(d.defaultPrevented, false);
  assert.deepEqual(t.locks, []);
  ok("노드가 아닌 곳의 Alt 끌기는 평소 손에 맡긴다");
}

// ── 3. 노드 → 다른 노드 = onConnect 한 번 ────────────────────────────────────
{
  const t = rig();
  const d = t.ptr("pointerdown", 12, 9, { alt: true });
  assert.equal(d.defaultPrevented, true, "호환 마우스 이벤트(sigma·영역 선택)를 막는다");
  assert.deepEqual(t.locks, [true]);
  assert.equal(t.el.captured, 7);
  const layer = layerOf(t.el);
  assert.equal(layer.attrs.class, "dc-layer");
  t.ptr("pointermove", 60, 60, { alt: true });
  const line = made.filter((f) => f.tag === "line").at(-1)!;
  assert.equal(line.attrs.x2, "60");
  assert.equal(ringOf().attrs.visibility, "hidden");
  t.ptr("pointermove", 101, 99, { alt: true });
  assert.equal(line.attrs.x2, "100", "대상 위에서는 선 끝이 그 중심에 붙는다");
  assert.equal(ringOf().attrs.class, "dc-ring");
  assert.equal(ringOf().attrs.visibility, undefined);
  t.ptr("pointerup", 101, 99);
  assert.deepEqual(t.calls, [[{ id: A.id, title: "A" }, { id: B.id, title: "B" }]]);
  assert.deepEqual(t.locks, [true, false]);
  assert.equal(layer.gone, true);
  const click = Object.assign(new Event("click", { cancelable: true }), {});
  t.el.dispatchEvent(click);
  assert.equal(click.defaultPrevented, true, "놓은 뒤의 click 은 노드 누름으로 읽히지 않는다");
  const later = new Event("click", { cancelable: true });
  t.el.dispatchEvent(later);
  assert.equal(later.defaultPrevented, false, "삼키는 것은 한 번뿐이다");
  ok("노드에서 다른 노드로 끌어 놓으면 onConnect(a, b) 를 한 번 부르고 잠금을 푼다");
}

// ── 4. 취소 경로 — 아무것도 부르지 않는다 ────────────────────────────────────
{
  const t = rig();
  t.ptr("pointerdown", 10, 10, { alt: true });
  t.ptr("pointerup", 300, 300);
  t.ptr("pointerdown", 10, 10, { alt: true });
  t.ptr("pointermove", 100, 100);
  t.ptr("pointerup", 11, 11);                        // 제자리(같은 노드)로 돌아와 놓음
  assert.deepEqual(t.calls, []);
  assert.deepEqual(t.locks, [true, false, true, false]);
  ok("빈 곳이나 같은 노드에서 놓으면 쓰지 않는다");
}
{
  const t = rig();
  t.ptr("pointerdown", 10, 10, { alt: true });
  t.ptr("pointermove", 100, 100);
  const other = t.key("Enter");
  assert.equal(other.defaultPrevented, false, "Esc 가 아닌 키는 건드리지 않는다");
  const esc = t.key("Escape");
  assert.equal(esc.defaultPrevented, true);
  t.ptr("pointerup", 100, 100);                      // Esc 뒤의 놓기는 아무 일도 아니다
  assert.deepEqual(t.calls, []);
  assert.deepEqual(t.locks, [true, false]);
  const idle = t.key("Escape");
  assert.equal(idle.defaultPrevented, false, "끄는 중이 아니면 Esc 를 가로채지 않는다");
  ok("Esc 는 끄는 중에만 취소하고, 그 밖의 Esc 는 띠·열·시트에 남긴다");
}
{
  const t = rig();
  t.ptr("pointerdown", 10, 10, { alt: true });
  t.ptr("pointermove", 100, 100);
  t.el.dispatchEvent(new Event("pointercancel"));
  t.ptr("pointerdown", 10, 10, { alt: true });
  t.ptr("pointermove", 100, 100);
  (globalThis as any).window.dispatchEvent(new Event("blur"));
  t.ptr("pointerdown", 10, 10, { alt: true });
  t.ptr("pointermove", 100, 100);
  t.off();                                            // 렌더러가 해체되면(관점 전환)
  t.ptr("pointerup", 100, 100);
  assert.deepEqual(t.calls, []);
  assert.deepEqual(t.locks, [true, false, true, false, true, false]);
  ok("포인터 취소 · 창 떠남 · 렌더러 해체는 쓰지 않고 잠금을 푼다");
}

// ── 5. 고리 모양 — 규칙에 안 맞는 쌍은 흐리게(막지는 않는다) ──────────────────
{
  const t = rig((a, b) => !b.startsWith("record:"));
  t.ptr("pointerdown", 10, 10, { alt: true });
  t.ptr("pointermove", 200, 10);
  assert.equal(ringOf().attrs.class, "dc-ring dc-no");
  t.ptr("pointerup", 200, 10);
  assert.equal(t.calls.length, 1, "놓으면 고르기 창이 이유를 말한다 — 버튼 길과 같다");
  ok("이을 수 없는 쌍은 흐린 고리로 미리 보이고, 놓으면 같은 고르기 창으로 간다");
}

console.log(`drag-connect-check: ${n} ok`);
