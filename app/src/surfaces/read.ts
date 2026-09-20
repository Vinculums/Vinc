/** 주소로만 여는 실험 리더. 시각 승인이나 기본 셸 전환을 포함하지 않는다.
 *  근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §5]]. */
import { api, cfg } from "../api";
import { parseViewUrl, formatViewUrl } from "../view-url";

const scrolls = new Map<string, number>();
const context = () => ({ owner: cfg.owner, base: cfg.base, team: cfg.team, token: cfg.token });
type Context = ReturnType<typeof context>;
type Reading = {
  el: HTMLElement; hash: string; context: Context; key: string;
  ready: boolean; frame: number | null; onScroll: () => void;
};
let active: Reading | null = null;

function sameContext(a: Context, b: Context): boolean {
  return a.owner === b.owner && a.base === b.base && a.team === b.team && a.token === b.token;
}

// 같은 주소로 돌아와도 이전 요청과 이번 요청은 다른 렌더다.
function current(reading: Reading): boolean {
  return active === reading && location.hash === reading.hash
    && sameContext(reading.context, context()) && reading.el.isConnected;
}

/** 공유 surface를 비우기 전에 호출한다. 다음 표면의 스크롤은 저장하지 않는다. */
export function disposeRead(): void {
  if (!active) return;
  const reading = active;
  if (reading.ready && sameContext(reading.context, context()))
    scrolls.set(reading.key, reading.el.scrollTop);
  active = null;
  reading.el.removeEventListener("scroll", reading.onScroll);
  if (reading.frame !== null) cancelAnimationFrame(reading.frame);
}

function restoreScroll(reading: Reading): void {
  const y = scrolls.get(reading.key) || 0;
  let tries = 0;
  const tick = () => {
    if (!current(reading)) return;
    reading.el.scrollTop = y;
    if (reading.el.scrollTop < y && tries++ < 3) {
      reading.frame = requestAnimationFrame(tick);
      return;
    }
    // 로딩과 복원이 만든 scroll 이벤트가 저장된 위치를 덮지 않게 한다.
    reading.frame = requestAnimationFrame(() => {
      if (current(reading)) reading.ready = true;
      reading.frame = null;
    });
  };
  reading.frame = requestAnimationFrame(tick);
}

const text = (s: unknown) => String(s ?? "");
function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c] || c));
}
function header(title: string, cta = ""): string {
  return '<div class="surface-header">'
    + '<div class="surface-title"><h1>' + esc(title) + '</h1></div>' + cta + '</div>';
}
const notice = (s: string) => '<p><small>' + esc(s) + '</small></p>';

export async function renderRead(el: HTMLElement): Promise<void> {
  disposeRead();
  const hash = location.hash;
  const captured = context();
  const reading: Reading = {
    el, hash, context: captured,
    // 비밀 값은 위치 키에 넣거나 영속화하지 않는다.
    key: JSON.stringify([captured.base, captured.owner, captured.team, hash]),
    ready: false, frame: null,
    onScroll: () => {
      if (reading.ready && current(reading)) scrolls.set(reading.key, el.scrollTop);
    },
  };
  active = reading;
  el.addEventListener("scroll", reading.onScroll, { passive: true });
  const paint = (html: string) => {
    if (!current(reading)) return;
    el.innerHTML = html;
    restoreScroll(reading);
  };
  el.innerHTML = notice("Loading…");
  let unavailable = notice("Not available.");

  try {
    const { sel, doc } = parseViewUrl(hash);
    if (sel.kind !== "node" && sel.kind !== "doc") {
      paint(notice("No selection."));
      return;
    }
    if (sel.kind === "doc") {
      const got = await api.docGet(captured.owner, sel.id);
      if (!current(reading)) return;
      if (!got.executed || got.verified !== true || typeof got.text !== "string") {
        paint(notice("Not available."));
        return;
      }
      paint(header(text(got.title) || sel.id) + '<pre>' + esc(got.text) + '</pre>');
      return;
    }

    const got = await api.nodeContext(captured.owner, sel.id);
    if (!current(reading)) return;
    if (!got.executed) { paint(notice("Not available.")); return; }
    const note = text(got.one_liner)
      || text((got.props || {}).one_liner) || text((got.props || {}).note);
    const toView = formatViewUrl({ kind: "node", id: sel.id }, { surface: "view", doc });
    const rows = (got.excerpts || []).map((e) => {
      const head = [e.doc_title || e.doc_id, e.section].filter(Boolean).join(" · ");
      return '<li><small>' + esc(head) + '</small><p>' + esc(e.excerpt) + '</p></li>';
    }).join("");
    const head = header(text(got.title) || sel.id,
                        '<a class="btn" href="' + esc(toView) + '">View</a>')
      + (note ? '<p>' + esc(note) + '</p>' : "");
    if (!doc) {
      paint(head + (rows ? '<ul>' + rows + '</ul>' : notice("No sources.")));
      return;
    }
    // 지정된 소스가 실패해도 다른 문서로 대체하지 않는다.
    unavailable = head + notice("That source is not available.");
    el.innerHTML = head + notice("Loading…");
    const src = await api.docGet(captured.owner, doc);
    if (!current(reading)) return;
    paint(src.executed && src.verified === true && typeof src.text === "string"
      ? head + '<h2>' + esc(text(src.title) || doc) + '</h2><pre>' + esc(src.text) + '</pre>'
      : unavailable);
  } catch {
    paint(unavailable);
  }
}
