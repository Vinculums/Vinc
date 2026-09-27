/** 흐름 F — 두 노드 잇기. 노드 카드·노드 상세의 "잇기"에서 시작한다.
 *
 *  ① 고르기: 지도 위에 띠가 선다 — 지도에서 노드를 누르거나 검색해서 고른다(View 가
 *     다음 초점을 이리로 넘긴다, `ViewHooks.pickNode`).
 *  ② 관계: A → B 화살표를 보며 넷 중 하나를 고른다(관련 · 선행 · 파생 · 상속). 방향은
 *     바꿀 수 있고, 규칙상 안 되는 관계는 미리 막고 이유를 붙인다(`relRefusal`).
 *     근거 문장은 선택이다. Enter 로 잇고, 이은 선은 곧바로 지도에 그려진다.
 *
 *  여태 관계를 만드는 길은 둘뿐이었고 둘 다 `relates_to` 로 박혀 있었다(연결이 없을
 *  때만 뜨는 제안 · Tools 의 문서 잇기). 종류를 고를 길도, 지울 길도 없었다. */
import { api } from "./api";
import { COPY } from "./copy";
import { esc, openSheet, sheetActions } from "./components";
import { viewHooks } from "./topbar";
import { RELS, edgeRefusal, findNodes, nodeHitsHtml, relRefusal, relSentence,
         type Rel } from "./authoring";

type End = { id: string; title: string };

let bar: HTMLElement | null = null;
let unbind: (() => void) | null = null;

function stopPicking(): void {
  viewHooks()?.pickNode?.(null);
  unbind?.(); unbind = null;
  bar?.remove(); bar = null;
}

/** 잇기를 시작한다 — `a` 에서 나가는 쪽이 기본 방향이다. */
export function startConnect(a: End): void {
  const A = COPY.author;
  stopPicking();
  const host = document.getElementById("surface") || document.body;
  bar = document.createElement("div");
  bar.className = "pick-bar";
  bar.setAttribute("role", "dialog");
  bar.setAttribute("aria-label", A.pickLead(a.title));
  bar.innerHTML = `
    <div class="pick-head">
      <span class="pick-lead">${esc(A.pickLead(a.title))}</span>
      <button class="rail-x" type="button" data-pick="cancel">${esc(A.cancel)}</button>
    </div>
    <p class="hint pick-hint">${esc(A.pickHint)}</p>
    <input type="text" autocomplete="off" spellcheck="false" placeholder="${esc(A.pickSearchPh)}"
           aria-label="${esc(A.pickSearchPh)}"/>
    <div class="cm-assist-menu pick-hits" role="listbox" hidden></div>
    <p class="hint pick-msg" role="status"></p>`;
  host.appendChild(bar);
  const input = bar.querySelector<HTMLInputElement>("input")!;
  const list = bar.querySelector<HTMLElement>(".pick-hits")!;
  const note = bar.querySelector<HTMLElement>(".pick-msg")!;

  const pick = (b: End) => {
    if (b.id === a.id) { note.textContent = A.relWhy.same; return; }
    stopPicking();
    openRelationPicker(a, b);
  };
  viewHooks()?.pickNode?.((id, title) => pick({ id, title }));

  let timer = 0, seq = 0;
  input.addEventListener("input", () => {
    window.clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { list.hidden = true; return; }
    timer = window.setTimeout(() => void (async () => {
      const mine = ++seq;
      const { hits, note } = await findNodes(q);
      if (mine !== seq || !bar) return;
      list.innerHTML = nodeHitsHtml(hits, note);
      list.hidden = false;
    })(), 200);
  });
  list.addEventListener("mousedown", (e) => {
    const row = (e.target as HTMLElement).closest<HTMLElement>("[data-id]");
    if (!row) return;
    e.preventDefault();
    pick({ id: row.dataset.id!, title: row.dataset.title || row.dataset.id! });
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) {
      e.preventDefault();
      const row = list.querySelector<HTMLElement>("[data-id]");
      if (row) pick({ id: row.dataset.id!, title: row.dataset.title || row.dataset.id! });
    }
  });
  bar.querySelector('[data-pick="cancel"]')!.addEventListener("click", stopPicking);
  // Esc 는 띠만 닫는다(지도의 단계 후퇴까지 가지 않게 캡처에서 먼저 받는다).
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "Escape" || !bar) return;
    e.stopPropagation();
    stopPicking();
  };
  document.addEventListener("keydown", onKey, true);
  // 표면을 떠나면 띠도 함께 간다(View 가 사라지면 고를 지도가 없다).
  const onHash = () => stopPicking();
  window.addEventListener("hashchange", onHash);
  unbind = () => {
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("hashchange", onHash);
  };
  input.focus();
}

/** 관계 고르기 — A → B 화살표 · 관계 넷 · 방향 바꾸기 · 근거(선택). */
export function openRelationPicker(a: End, b: End): void {
  const A = COPY.author;
  let from = a, to = b;
  const firstOk = () => RELS.find((r) => !relRefusal(r, from.id, to.id)) || RELS[0];
  let rel: Rel = firstOk();
  let connected = false;
  const sheet = openSheet({ title: A.connectTitle });
  sheet.foot.innerHTML = sheetActions({ id: "cx-go", label: A.connectCta });
  const go = sheet.foot.querySelector<HTMLButtonElement>("#cx-go")!;

  function paint() {
    const why = relRefusal(rel, from.id, to.id);
    if (why) rel = firstOk();
    sheet.body.innerHTML = `
      <div class="cx-arrow">
        <span class="cx-end">${esc(from.title)}</span>
        <span class="cx-dir" aria-hidden="true">→</span>
        <span class="cx-end">${esc(to.title)}</span>
        <button class="rail-x cx-swap" type="button" id="cx-swap">⇄ ${esc(A.swap)}</button>
      </div>
      <div class="cx-rels" role="radiogroup" aria-label="${esc(A.connectTitle)}">
        ${RELS.map((r) => {
          const no = relRefusal(r, from.id, to.id);
          const on = r === rel && !no;
          return `<button type="button" class="cx-rel${on ? " on" : ""}" role="radio"
              data-rel="${r}" aria-checked="${on}"${no ? ' aria-disabled="true"' : ""}>
            <span class="cx-rel-name">${esc(A.rel[r])}</span>
            <span class="hint cx-rel-sub">${esc(no || relSentence(r, from.title, to.title))}</span>
          </button>`;
        }).join("")}
      </div>
      <label class="ei-field"><span>${esc(A.evidence)}</span>
        <input id="cx-ev" type="text" autocomplete="off" placeholder="${esc(A.evidencePh)}"/></label>
      <div id="cx-msg" role="status"></div>`;
    const allNo = RELS.every((r) => relRefusal(r, from.id, to.id));
    go.disabled = allNo;
    if (allNo) {
      sheet.body.querySelector("#cx-msg")!.innerHTML =
        `<p class="hint">${esc(A.connectRefused(relRefusal(rel, from.id, to.id) || ""))}</p>`;
    }
  }
  sheet.body.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    if (connected) return;
    if (t.closest("#cx-swap")) {
      const ev = sheet.body.querySelector<HTMLInputElement>("#cx-ev")?.value || "";
      [from, to] = [to, from];
      paint();
      sheet.body.querySelector<HTMLInputElement>("#cx-ev")!.value = ev;
      return;
    }
    const r = t.closest<HTMLElement>(".cx-rel");
    if (r && r.getAttribute("aria-disabled") !== "true") {
      const ev = sheet.body.querySelector<HTMLInputElement>("#cx-ev")?.value || "";
      rel = r.dataset.rel as Rel;
      paint();
      sheet.body.querySelector<HTMLInputElement>("#cx-ev")!.value = ev;
      sheet.body.querySelector<HTMLElement>(`.cx-rel[data-rel="${rel}"]`)?.focus();
    }
  });
  // 관계 묶음의 위아래 화살표 — 막힌 것은 건너뛴다.
  sheet.body.addEventListener("keydown", (e) => {
    const k = e.key;
    if ((k === "ArrowDown" || k === "ArrowUp") && (e.target as HTMLElement).closest(".cx-rels")) {
      e.preventDefault();
      const ok = RELS.filter((r) => !relRefusal(r, from.id, to.id));
      const i = ok.indexOf(rel) + (k === "ArrowDown" ? 1 : -1);
      rel = ok[(i + ok.length) % ok.length];
      paint();
      sheet.body.querySelector<HTMLElement>(`.cx-rel[data-rel="${rel}"]`)?.focus();
    }
  });
  sheet.el.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing) return;
    const t = e.target as HTMLElement;
    if (t.closest("#cx-swap")) return;                 // 방향 바꾸기 단추 자신의 Enter
    e.preventDefault();
    // 관계 줄에서 Enter = 그 관계로 곧바로 잇는다(고르고 한 번 더 누를 일을 만들지 않는다).
    const r = t.closest<HTMLElement>(".cx-rel");
    if (r && !connected && r.getAttribute("aria-disabled") !== "true") rel = r.dataset.rel as Rel;
    go.click();
  });

  go.addEventListener("click", () => void (async () => {
    if (connected) { sheet.close(); return; }
    const msg = sheet.body.querySelector<HTMLElement>("#cx-msg")!;
    const why = relRefusal(rel, from.id, to.id);
    if (why) { msg.innerHTML = `<p class="hint">${esc(A.connectRefused(why))}</p>`; return; }
    const evidence = sheet.body.querySelector<HTMLInputElement>("#cx-ev")?.value.trim() || "";
    go.disabled = true;
    msg.innerHTML = `<p class="hint">${esc(A.connecting)}</p>`;
    let res: any;
    try {
      res = await api.ingestFragment({ edges: [{ type: rel, from: from.id, to: to.id,
                                                 ...(evidence ? { evidence } : {}) }] });
    } catch (e: any) { res = { executed: false, error: String(e?.message || "") }; }
    go.disabled = false;
    if (res?.executed === false) {
      msg.innerHTML = `<span class="error-banner">${esc(A.connectFail)}</span>`
        + (res.error ? ` <span class="hint">${esc(String(res.error))}</span>` : "");
      return;
    }
    if (!Number(res?.merged?.edges?.created || 0)) {
      msg.innerHTML = `<p class="hint">${esc(A.connectRefused(edgeRefusal(res?.warnings)))}</p>`;
      return;
    }
    connected = true;
    msg.innerHTML = `<p class="hint" role="status">${esc(A.connected(
      relSentence(rel, from.title, to.title)))}</p>`;
    go.textContent = A.done;
    go.focus();
    // 이은 선을 곧바로 그린다(문서 열이 열려 있으면 그대로 두고, 그 열이 시작한 노드를
    // 보이고 있으면 새 연결이 든 모습으로 다시 그린다). 방향을 바꿨어도 시작한 노드로 간다.
    void viewHooks()?.reveal?.(a.id);
  })());

  paint();
  sheet.body.querySelector<HTMLElement>(`.cx-rel[data-rel="${rel}"]`)?.focus();
}
