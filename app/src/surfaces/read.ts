/** 리더 표면 — 0.9.4 셸의 **구조 조각**이다. 시각 디자인이 아니다.
 *
 *  이 파일이 증명하는 것 하나: `#read/...` 가 열리고, 고른 노드의 정본이 뜨고,
 *  View 로 갔다 와도 그 선택이 살아 있다는 것. 그것이 D4a 의 전부다.
 *
 *  ⚠ **여기서 화면을 디자인하지 않는다.** 발주자가 VIN-88 의 21프레임을 와이어프레임
 *  수준으로 판정하고 "현재 와이어프레임을 구현해서 시각 디자인 단계를 우회하지 말
 *  것"이라고 못박았다. 그래서 이 표면은 **기존 시각 언어를 그대로** 쓰고, 새 카피를
 *  짓지 않으며(버튼은 코드명 `View`), 상단바 차례(`ORDER`)에도 안 선다 — 주소로만
 *  닿는다. 승인된 고해상도가 나오면 그 위에 표면·카피·차례가 함께 온다.
 *  근거: [[vinc.spec.desktop-content-first-ia-0-9-4]].
 *
 *  ⚠ **쓰는 클래스는 `app.css` 에 실재하는 것뿐이다.** 첫 초안이 `.pad`·`.muted`·
 *  `.read-sources` 를 썼는데 셋 다 없는 이름이었다(브라우저 실측: `.pad` 의 padding
 *  이 0). 없는 클래스는 조용히 아무것도 안 하므로 타입체크도 게이트도 안 잡는다 —
 *  화면을 실제로 열어 봐야 갈린다. 여백은 `#surface` 가 이미 갖고 있다.
 */
import { api, cfg } from "../api";
import { parseViewUrl, formatViewUrl } from "../view-url";

/** 이 표면이 그린 마지막 주소. 조회가 끝나기 전에 선택이 바뀌면 늦게 온 응답이
 *  나중 화면을 덮는데, 그것을 막는 표식이다. */
let painted = "";

/** 읽던 자리 — 주소마다 하나. 계약이 요구하는 것은 "선택이 살아남는다"가 아니라
 *  **읽던 자리가 살아남는다**이다: View 를 갔다 오거나 뒤로 갔을 때 문단 첫 줄로
 *  튕기면 긴 문서에서 자리를 잃는다.
 *  근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §5]] — 리더의 스크롤은 그 컨텍스트
 *  안에서 노드·문서마다 보관되고 뒤로가기와 View 왕복에 복원된다.
 *
 *  키에 owner 가 드는 이유: 같은 주소라도 스토어가 다르면 **다른 문서**다(같은 계약의
 *  "컨텍스트가 모든 캐시·요청·기록 키의 일부다"). 창을 닫으면 사라지는 세션 기억이고
 *  디스크에 남기지 않는다 — 재시작 복원은 측정된 설계가 따로 필요하다고 계약이 못박았다. */
const scrolls = new Map<string, number>();
let scrollBound = false;

const memKey = () => `${cfg.owner}|${location.hash}`;

/** 스크롤 리스너는 **한 번만** 건다. 라우터는 `#surface` 의 내용만 비우고 요소는
 *  그대로 두므로, 렌더마다 걸면 리스너가 쌓인다. 지금 주소가 이 표면이 그린 것과
 *  같을 때만 적는다 — 다른 표면에 있는 동안 온 스크롤을 이 주소의 자리로 적으면
 *  돌아왔을 때 엉뚱한 곳에 선다. */
function bindScroll(el: HTMLElement): void {
  if (scrollBound) return;
  scrollBound = true;
  el.addEventListener("scroll", () => {
    if (location.hash === painted) scrolls.set(memKey(), el.scrollTop);
  }, { passive: true });
}

/** 내용이 들어간 **뒤** 한 프레임 기다려 복원한다. 레이아웃이 잡히기 전에 적으면
 *  스크롤 높이가 아직 0 이라 브라우저가 그 값을 버린다. */
function restoreScroll(el: HTMLElement, key: string): void {
  const y = scrolls.get(key) || 0;
  requestAnimationFrame(() => {
    if (location.hash === painted) el.scrollTop = y;
  });
}

const text = (s: unknown) => String(s ?? "");

/** 앱에 공용 이스케이프가 없어서 여기 둔다. 정본 본문이 그대로 들어오는 자리다. */
function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c] || c));
}

/** 표면 머리 — `app.css` 의 `.surface-header`/`.surface-title` 을 그대로 쓴다. */
function header(title: string, cta = ""): string {
  return `<div class="surface-header">`
    + `<div class="surface-title"><h1>${esc(title)}</h1></div>${cta}</div>`;
}

const notice = (s: string) => `<p><small>${esc(s)}</small></p>`;

export async function renderRead(el: HTMLElement): Promise<void> {
  const { sel } = parseViewUrl(location.hash);
  const key = location.hash;
  painted = key;
  bindScroll(el);
  const mem = memKey();

  if (sel.kind !== "node" && sel.kind !== "doc") {
    // 선택이 없는 것은 오류가 아니다.
    el.innerHTML = notice("No selection.");
    return;
  }
  el.innerHTML = notice("Loading…");

  if (sel.kind === "doc") {
    const got = await api.docGet(cfg.owner, sel.id);
    if (painted !== key) return;
    if (!got.executed) { el.innerHTML = notice("Not available."); return; }
    el.innerHTML = header(text(got.title) || sel.id)
      + `<pre>${esc(text(got.text))}</pre>`;
    restoreScroll(el, mem);
    return;
  }

  const got = await api.nodeContext(cfg.owner, sel.id);
  // 조회가 끝나기 전에 선택이 바뀌었으면 이 응답은 남의 화면 것이다. 이 가드가 없으면
  // 빠르게 두 번 고를 때 늦게 온 응답이 나중 것을 덮는다.
  if (painted !== key) return;
  if (!got.executed) { el.innerHTML = notice("Not available."); return; }

  // 설명은 one_liner 가 비어 있으면 props 로 내려간다 — 쓰기 경로가 one_liner 를
  // 안 채워서 MCP 적재분이 전부 빈 값이기 때문이다(조회 계약의 같은 폴백).
  const note = text(got.one_liner)
    || text((got.props || {}).one_liner)
    || text((got.props || {}).note);
  const toView = formatViewUrl({ kind: "node", id: sel.id }, { surface: "view" });
  const rows = (got.excerpts || []).map((e) => {
    const head = [e.doc_title || e.doc_id, e.section].filter(Boolean).join(" · ");
    return `<li><small>${esc(head)}</small><p>${esc(e.excerpt)}</p></li>`;
  }).join("");

  el.innerHTML = header(text(got.title) || sel.id,
                        `<a class="btn" href="${esc(toView)}">View</a>`)
    + (note ? `<p>${esc(note)}</p>` : "")
    + (rows ? `<ul>${rows}</ul>` : notice("No sources."));
  restoreScroll(el, mem);
}
