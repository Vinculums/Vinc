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

/** 렌더 중에는 기억을 멈춘다. 로딩 표시로 내용을 비우는 순간 스크롤이 0 이 되고,
 *  그 `scroll` 이벤트가 방금 저장한 자리를 0 으로 덮기 때문이다. 복원이 만든 스크롤도
 *  같은 이유로 새 값이 아니다.
 *
 *  ⚠ **이 가드는 추론이지 실측이 아니다.** 2026-09-20 에 "왕복하면 0 으로 돌아온다"를
 *  보고 이것을 원인으로 짚었는데, 더 재 보니 그 세션의 원인은 다른 것이었다(아래
 *  `restoreScroll` 의 경고). 그래도 남기는 이유는 비우기 → 저장 경로가 실제로 열려
 *  있어서다. 이 가드가 없어도 되는지는 아직 증명되지 않았다. */
let settling = false;

const memKey = () => `${cfg.owner}|${location.hash}`;

/** 스크롤 리스너는 **한 번만** 건다. 라우터는 `#surface` 의 내용만 비우고 요소는
 *  그대로 두므로, 렌더마다 걸면 리스너가 쌓인다. 지금 주소가 이 표면이 그린 것과
 *  같을 때만 적는다 — 다른 표면에 있는 동안 온 스크롤을 이 주소의 자리로 적으면
 *  돌아왔을 때 엉뚱한 곳에 선다. */
function bindScroll(el: HTMLElement): void {
  if (scrollBound) return;
  scrollBound = true;
  el.addEventListener("scroll", () => {
    if (settling) return;                       // 렌더가 만든 스크롤은 사용자 것이 아니다
    if (location.hash === painted) scrolls.set(memKey(), el.scrollTop);
  }, { passive: true });
}

/** 내용이 들어간 **뒤** 한 프레임 기다려 복원한다. 레이아웃이 잡히기 전에 적으면
 *  스크롤 높이가 아직 0 이라 브라우저가 그 값을 버린다. */
function restoreScroll(el: HTMLElement, key: string): void {
  const y = scrolls.get(key) || 0;
  let tries = 0;
  const tick = () => {
    if (location.hash !== painted) { settling = false; return; }
    el.scrollTop = y;
    // 내용이 길면 그 프레임에 아직 레이아웃이 안 잡혀 `scrollHeight` 가 작고,
    // 브라우저는 넘치는 값을 조용히 깎는다. 안 들어갔으면 다음 프레임에 다시 적되
    // 세 번까지만 — 그 이상 붙들면 그 사이 사용자가 움직인 것을 덮는다.
    //
    // ⚠ **이 재시도도 추론이다.** 2026-09-20 에 "900 이 0 으로 읽힌다"를 이 원인으로
    // 짚었지만, 직접 재 보니 그 세션에서는 `requestAnimationFrame` 이 **아예 돌지
    // 않고** 있었다(브라우저 패널이 가려져 있으면 그렇게 된다 — `rafOk:false`,
    // 직접 건 scroll 리스너도 0회 발화). 즉 그 관측은 이 코드의 증거가 아니었다.
    // 검증된 것은 `#read/doc/<id>` 왕복에서 1200 이 1200 으로 돌아온 것 하나이고,
    // 소스 갈래(`…/src/<doc>`)의 복원은 rAF 가 도는 환경에서 다시 재야 한다.
    if (el.scrollTop < y && tries++ < 3) { requestAnimationFrame(tick); return; }
    // 복원이 만든 스크롤 이벤트까지 지난 **뒤에** 기억을 다시 연다. 같은 프레임에서
    // 풀면 그 이벤트가 사용자 스크롤로 읽힌다.
    requestAnimationFrame(() => { settling = false; });
  };
  requestAnimationFrame(tick);
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
  const { sel, doc } = parseViewUrl(location.hash);
  const key = location.hash;
  painted = key;
  bindScroll(el);
  const mem = memKey();
  settling = true;

  if (sel.kind !== "node" && sel.kind !== "doc") {
    // 선택이 없는 것은 오류가 아니다.
    el.innerHTML = notice("No selection.");
    restoreScroll(el, mem);
    return;
  }
  el.innerHTML = notice("Loading…");

  if (sel.kind === "doc") {
    const got = await api.docGet(cfg.owner, sel.id);
    if (painted !== key) return;
    if (!got.executed) { el.innerHTML = notice("Not available."); restoreScroll(el, mem); return; }
    el.innerHTML = header(text(got.title) || sel.id)
      + `<pre>${esc(text(got.text))}</pre>`;
    restoreScroll(el, mem);
    return;
  }

  const got = await api.nodeContext(cfg.owner, sel.id);
  // 조회가 끝나기 전에 선택이 바뀌었으면 이 응답은 남의 화면 것이다. 이 가드가 없으면
  // 빠르게 두 번 고를 때 늦게 온 응답이 나중 것을 덮는다.
  if (painted !== key) return;
  if (!got.executed) { el.innerHTML = notice("Not available."); restoreScroll(el, mem); return; }

  // 설명은 one_liner 가 비어 있으면 props 로 내려간다 — 쓰기 경로가 one_liner 를
  // 안 채워서 MCP 적재분이 전부 빈 값이기 때문이다(조회 계약의 같은 폴백).
  const note = text(got.one_liner)
    || text((got.props || {}).one_liner)
    || text((got.props || {}).note);
  const toView = formatViewUrl({ kind: "node", id: sel.id },
                               { surface: "view", doc });
  const rows = (got.excerpts || []).map((e) => {
    const head = [e.doc_title || e.doc_id, e.section].filter(Boolean).join(" · ");
    return `<li><small>${esc(head)}</small><p>${esc(e.excerpt)}</p></li>`;
  }).join("");

  const head = header(text(got.title) || sel.id,
                      `<a class="btn" href="${esc(toView)}">View</a>`)
    + (note ? `<p>${esc(note)}</p>` : "");

  if (!doc) {
    el.innerHTML = head + (rows ? `<ul>${rows}</ul>` : notice("No sources."));
    restoreScroll(el, mem);
    return;
  }
  // 소스가 지정됐다. **그 문서**를 읽는다 — 목록의 첫 줄로 바꿔치지 않는 것이
  // 계약의 요점이다(IA §4). 선택기는 아직 없고, 딥링크만 정확하다.
  el.innerHTML = head + notice("Loading…");
  const src = await api.docGet(cfg.owner, doc);
  if (painted !== key) return;
  el.innerHTML = head + (src.executed
    ? `<h2>${esc(text(src.title) || doc)}</h2><pre>${esc(text(src.text))}</pre>`
    : notice("That source is not available."));
  restoreScroll(el, mem);
}
