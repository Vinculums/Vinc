/** 끌어서 잇기(흐름 F 의 지름길) — 3D·2D 가 함께 쓴다.
 *
 *  Alt(맥은 Option)를 누른 채 노드에서 끌면 그 노드에서 포인터까지 선이 따라오고, 포인터
 *  아래의 노드에 고리가 선다. 다른 노드 위에서 놓으면 `onConnect(a, b)` 하나만 부른다 —
 *  쓰기는 하지 않는다. 호출측이 잇기 버튼과 **같은** 관계 고르기(`openRelationPicker`)를
 *  연다. 빈 곳·같은 노드에서 놓거나 Esc 를 누르면 아무것도 쓰지 않고 끝난다.
 *
 *  손의 배분: 맨 끌기는 2D 에서 노드 옮기기 · 3D 에서 회전, Shift 끌기는 3D 영역 선택이다.
 *  그래서 Alt 이고, Shift 가 함께 눌렸으면 영역 선택에 양보한다.
 *
 *  렌더러는 셋만 준다: 화면 좌표의 노드 찾기(`hitAt`), 끄는 동안의 잠금(`lock` — 카메라와
 *  노드 끌기), 놓았을 때 부를 것(`onConnect`). 선과 고리는 WebGL 이 아니라 DOM(SVG)이다 —
 *  잠든 렌더러(3D 는 할 일이 없으면 틱을 멈춘다)를 깨우지 않아도 그려지고, 두 렌더러가
 *  같은 모양을 낸다. 움직임은 없다(전환·애니메이션 0) — 움직임 줄이기 설정에서도 같다.
 *
 *  이 파일은 import 0 이다 — `app/dev/drag-connect-check.ts` 가 브라우저 없이 상태 기계를
 *  돌리는 조건이다. 관계 규칙(`relRefusal`)은 호출측이 `allowed` 로 넘긴다.
 *  근거: [[vinc.plan.view-card-timeline-period-authoring-2026-09-27]] §3.3 */

export type DragEnd = { id: string; title: string };
/** 화면(컨테이너 기준 px)에서 찾은 노드 — 중심과 화면 반지름. */
export type DragHit = DragEnd & { x: number; y: number; r: number };

/** 작은 마크도 잡히도록 두는 최소 반지름(px). */
export const HIT_MIN_PX = 10;

const SVG = "http://www.w3.org/2000/svg";

export function wireDragConnect(el: HTMLElement, o: {
  hitAt: (x: number, y: number) => DragHit | null;
  lock: (on: boolean) => void;
  onConnect: (a: DragEnd, b: DragEnd) => void;
  /** 고리 모양만 가른다(이을 수 있는 쌍 = 포인트색, 아니면 흐린 점선). */
  allowed?: (a: string, b: string) => boolean;
}): () => void {
  let src: DragHit | null = null;
  let over: DragHit | null = null;
  let pid = -1;
  let layer: SVGSVGElement | null = null;
  let line: SVGLineElement | null = null;
  let ring: SVGCircleElement | null = null;
  let swallowClick = false;

  const local = (e: PointerEvent) => {
    const r = el.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  const mk = <K extends keyof SVGElementTagNameMap>(tag: K, cls: string) => {
    const n = document.createElementNS(SVG, tag);
    n.setAttribute("class", cls);
    return n;
  };
  /** 그 쌍에 규칙상 가능한 관계가 하나라도 있는가 — 없으면 고리를 흐리게 둔다.
   *  놓으면 고르기 창이 이유를 말하므로 막지는 않는다(버튼 길과 같은 결과). */
  const allowed = (a: string, b: string) => o.allowed ? o.allowed(a, b) : true;

  function draw(x: number, y: number) {
    if (!src || !line || !ring) return;
    // 대상 위에 있으면 선 끝을 그 중심에 붙인다 — 어디에 놓일지를 선이 먼저 말한다.
    const ex = over ? over.x : x, ey = over ? over.y : y;
    line.setAttribute("x1", String(src.x)); line.setAttribute("y1", String(src.y));
    line.setAttribute("x2", String(ex)); line.setAttribute("y2", String(ey));
    if (over) {
      ring.setAttribute("cx", String(over.x)); ring.setAttribute("cy", String(over.y));
      ring.setAttribute("r", String(over.r + 4));
      ring.setAttribute("class", allowed(src.id, over.id) ? "dc-ring" : "dc-ring dc-no");
      ring.removeAttribute("visibility");
    } else {
      ring.setAttribute("visibility", "hidden");
    }
  }

  const overAt = (x: number, y: number): DragHit | null => {
    const h = o.hitAt(x, y);
    return h && src && h.id !== src.id ? h : null;
  };

  /** 끝낸다. `commit` 이 참이고 다른 노드 위였을 때만 `onConnect` 를 부른다 — 그 밖의
   *  모든 끝(빈 곳 · 같은 노드 · Esc · 포인터 취소 · 해체)은 쓰기 없이 조용히 닫힌다. */
  function end(commit: boolean) {
    const a = src, b = over;
    src = null; over = null;
    if (pid >= 0) { try { el.releasePointerCapture(pid); } catch { /* 이미 풀렸다 */ } }
    pid = -1;
    layer?.remove(); layer = null; line = null; ring = null;
    document.removeEventListener("keydown", onKey, true);
    o.lock(false);
    if (commit && a && b && b.id !== a.id) {
      o.onConnect({ id: a.id, title: a.title }, { id: b.id, title: b.title });
    }
  }

  // 전부 **캡처 단계**에서 받는다 — 렌더러(트랙볼·sigma·노드 끌기)는 캔버스 안쪽 요소에서
  // 듣으므로, 여기서 멈추면 그쪽은 이 끌기를 아예 못 본다. pointerdown 의 preventDefault 는
  // 뒤따르는 호환 마우스 이벤트(sigma·영역 선택이 듣는 mousedown/move/up)도 막는다.
  const onDown = (e: PointerEvent) => {
    if (src || !e.altKey || e.shiftKey || e.button !== 0) return;
    const p = local(e);
    const hit = o.hitAt(p.x, p.y);
    if (!hit) return;                     // 빈 곳의 Alt 끌기는 평소 손(회전·이동)에 맡긴다
    e.preventDefault(); e.stopPropagation();
    src = hit; over = null; pid = e.pointerId;
    try { el.setPointerCapture(pid); } catch { /* 합성 이벤트 등 — 캡처 없이도 돈다 */ }
    o.lock(true);
    layer = mk("svg", "dc-layer");
    layer.setAttribute("aria-hidden", "true");
    line = mk("line", "dc-line");
    const from = mk("circle", "dc-src");
    from.setAttribute("cx", String(hit.x)); from.setAttribute("cy", String(hit.y));
    from.setAttribute("r", String(hit.r + 4));
    ring = mk("circle", "dc-ring");
    layer.append(line, from, ring);
    el.appendChild(layer);
    draw(p.x, p.y);
    document.addEventListener("keydown", onKey, true);
  };
  const onMove = (e: PointerEvent) => {
    if (!src) return;
    e.stopPropagation(); e.preventDefault();
    const p = local(e);
    over = overAt(p.x, p.y);
    draw(p.x, p.y);
  };
  const onUp = (e: PointerEvent) => {
    if (!src) return;
    e.stopPropagation(); e.preventDefault();
    // 놓은 자리로 한 번 더 판정한다(마지막 move 와 up 사이에 움직였을 수 있다).
    const p = local(e);
    over = overAt(p.x, p.y);
    // 뒤따르는 click 은 렌더러의 "노드 누름"(카드 열기)으로 읽히면 안 된다.
    swallowClick = true;
    window.setTimeout(() => { swallowClick = false; }, 0);
    end(true);
  };
  const onCancel = () => { if (src) end(false); };
  const onClick = (e: MouseEvent) => {
    if (!swallowClick) return;
    swallowClick = false;
    e.stopPropagation(); e.preventDefault();
  };
  // Esc 는 끄는 동안에만 가로챈다 — 잇기 띠·열·시트가 같은 Esc 로 닫히지 않게.
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "Escape" || !src) return;
    e.stopPropagation(); e.preventDefault();
    end(false);
  };

  el.addEventListener("pointerdown", onDown, true);
  el.addEventListener("pointermove", onMove, true);
  el.addEventListener("pointerup", onUp, true);
  el.addEventListener("pointercancel", onCancel, true);
  el.addEventListener("lostpointercapture", onCancel, true);
  el.addEventListener("click", onClick, true);
  window.addEventListener("blur", onCancel);
  return () => {
    if (src) end(false);
    el.removeEventListener("pointerdown", onDown, true);
    el.removeEventListener("pointermove", onMove, true);
    el.removeEventListener("pointerup", onUp, true);
    el.removeEventListener("pointercancel", onCancel, true);
    el.removeEventListener("lostpointercapture", onCancel, true);
    el.removeEventListener("click", onClick, true);
    window.removeEventListener("blur", onCancel);
  };
}
