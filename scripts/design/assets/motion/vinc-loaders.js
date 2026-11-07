// Vinc 로딩 모션 — 정본은 scripts/design/assets/motion/ 이고 앱·사이트 사본은 sync_out.py 가
// 복사한다(손으로 고치지 않는다). 스타일은 같은 자리의 vinc-loaders.css.
//
// 다섯 가지와 자리(근거: 그래프 `vinc.design.loading-motions`):
//   mark   — 앱 시작 · 로그인 넘김 · 큰 그래프 첫 열기 · 사이트 확인 카드(가운데, 글 한 줄)
//   branch — 그래프 읽기·배치 · 패널 · 커뮤니티 그래프(가운데, 글 한 줄)
//   link   — 글자 옆 · 단추 안 · 검색·Ask 대기 · 저장 중(인라인)
//   rows   — 곧 올 목록 자리(문서 홈 · 주제 트리 · 검색 결과 · 커뮤니티 목록)
//   track  — 개수가 있는 일(가져오기 · 재임베딩 · 이전 · 업데이트), total 0 이면 왕복
//
// 모두 문자열을 돌려준다(앱과 사이트가 innerHTML 로 그린다). 읽는 글은 role=status 로 말하고
// 그림은 aria-hidden 이다. 모션을 줄인 사람에게는 CSS 가 정지 화면을 보인다.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function markSVG() {
  return '<span class="vl-mark" aria-hidden="true"><span class="vl-mark-ring"></span></span>';
}

// 주제(왼쪽 포인트 점)에서 두 갈래가 오른쪽으로 자란다. 위 갈래 넷, 아래 갈래 셋.
function branchSVG() {
  const items = [[44, 22], [80, 22], [116, 22], [152, 22], [62, 58], [98, 58], [134, 58]];
  const lines = ["M8 40 H22 V22 H44", "M44 22 H80", "M80 22 H116", "M116 22 H152",
    "M8 40 H22 V58 H62", "M62 58 H98", "M98 58 H134"];
  const step = [0, 1, 2, 3, 0, 1, 2];
  let s = '<svg class="vl-branch" viewBox="0 0 160 80" aria-hidden="true" focusable="false">';
  lines.forEach((d, i) => { s += `<path class="ln" pathLength="1" d="${d}" style="animation-delay:${step[i] * 0.18}s"/>`; });
  s += '<circle class="nd tp" cx="8" cy="40" r="5"/>';
  items.forEach(([x, y], i) => { s += `<circle class="nd" cx="${x}" cy="${y}" r="3.5" style="animation-delay:${step[i] * 0.18 + 0.14}s"/>`; });
  return s + "</svg>";
}

function linkSVG() {
  return '<svg class="vl-link" viewBox="0 0 44 12" aria-hidden="true" focusable="false">'
    + '<path pathLength="1" d="M6 6 H22"/><path pathLength="1" d="M22 6 H38"/>'
    + '<circle cx="6" cy="6" r="3"/><circle cx="22" cy="6" r="3"/><circle cx="38" cy="6" r="3"/></svg>';
}

/** 가운데 큰 대기(mark · branch). `fill` 이면 맡은 상자를 다 채운다. */
export function loaderBlock(kind, label, opts = {}) {
  const art = kind === "branch" ? branchSVG() : markSVG();
  const cls = `vl vl-block${opts.fill ? " vl-fill" : ""}${opts.className ? " " + opts.className : ""}`;
  return `<div class="${cls}" role="status" aria-live="polite">${art}`
    + (label ? `<span class="vl-label">${esc(label)}</span>` : "") + "</div>";
}

/** 글자 옆 · 단추 안. 단추 안에서는 단추 글자를 label 로 넘긴다. */
export function loaderInline(label, opts = {}) {
  const tag = opts.tag || "span";
  return `<${tag} class="vl vl-inline${opts.className ? " " + opts.className : ""}" role="status" aria-live="polite">`
    + linkSVG() + (label ? `<span class="vl-label">${esc(label)}</span>` : "") + `</${tag}>`;
}

/** 곧 올 목록 자리. 읽는 글은 화면에 안 보이고 화면 낭독기에만 간다. */
export function loaderRows(label, opts = {}) {
  const n = Math.max(1, Math.min(8, opts.count || 4));
  const widths = [62, 48, 74, 55, 66, 44, 70, 52];
  let s = `<div class="vl vl-rows${opts.compact ? " vl-compact" : ""}" role="status" aria-live="polite">`
    + `<span class="vl-sr">${esc(label)}</span>`;
  for (let i = 0; i < n; i++) {
    s += `<div class="r" aria-hidden="true" style="--i:${i}"><span class="dot"></span>`
      + `<span class="bar" style="width:${widths[i]}%"></span><span class="bar meta"></span></div>`;
  }
  return s + "</div>";
}

/** 개수가 있는 일. total 이 0 이면 개수를 모르는 동안이라 머리가 왕복한다.
 *  라벨은 한 번만 읽힌다: 보이는 글을 aria-labelledby 로 가리킨다(aria-label 을 또 달면 두 번 읽힌다).
 *  id 는 호출마다 새로 만든다 — 닫히는 중인 상자와 새 상자가 잠깐 함께 설 수 있다. */
let trackSeq = 0;
export function loaderTrack(label, done = 0, total = 0, opts = {}) {
  const steps = opts.steps || [0, 25, 50, 75, 100];
  const id = `vl-track-${++trackSeq}`;
  return `<div class="vl vl-track${total ? "" : " vl-indef"}" role="progressbar" aria-labelledby="${id}"`
    + ` aria-valuemin="0" aria-valuemax="${total || 100}"${total ? ` aria-valuenow="${done}"` : ""}`
    + ` data-steps="${steps.join(",")}">`
    + `<div class="rail" aria-hidden="true">${steps.map((p) => `<span class="ms" style="left:${p}%"></span>`).join("")}`
    + '<span class="fill"></span><span class="head"></span></div>'
    + `<div class="lab"><span class="vl-what" id="${id}">${esc(label)}</span><span class="vl-n">${total ? `${done} / ${total}` : ""}</span></div></div>`;
}

/** 그려 둔 track 을 진행에 맞춘다. total 0 이면 왕복으로 돌아간다. `text` 는 오른쪽 수치를 갈아 끼운다. */
export function setTrack(el, done, total, text) {
  if (!el) return;
  const track = el.classList.contains("vl-track") ? el : el.querySelector(".vl-track");
  if (!track) return;
  const p = total ? Math.max(0, Math.min(100, Math.round((done / total) * 100))) : 0;
  track.classList.toggle("vl-indef", !total);
  track.style.setProperty("--vl-p", `${p}%`);
  if (total) { track.setAttribute("aria-valuemax", String(total)); track.setAttribute("aria-valuenow", String(done)); }
  else track.removeAttribute("aria-valuenow");
  const steps = (track.dataset.steps || "").split(",").map(Number);
  track.querySelectorAll(".ms").forEach((m, i) => m.classList.toggle("on", total > 0 && p >= steps[i]));
  const n = track.querySelector(".vl-n");
  if (n) n.textContent = text ?? (total ? `${done} / ${total}` : "");
}
