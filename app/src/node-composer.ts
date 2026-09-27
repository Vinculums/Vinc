/** 흐름 E — 노드 컴포저. 종류 · 제목 · 한 줄 · 주제 · (선택) 연결을 한 자리에서 적는다.
 *
 *  여태 손으로 노드를 만드는 곳은 빈 그래프의 초대 하나뿐이었고, 그 폼은 셋을 다쳤다:
 *  한글·영문 밖의 제목은 빈 id 가 됐고, 이미 있는 id 로 가는 제목은 **조용히 그 노드에
 *  합쳐** 속성을 갈아치웠고, 소유자를 `tenant:local` 로 박아 두었다. 여기서는
 *   · id 를 미리 보여 주고 쓰기 전에 확인한다 — 있으면 합치는 대신 "있는 노드 열기"를 준다
 *   · 어떤 문자 체계든 id 조각이 된다(`authoring.slugOf`)
 *   · 소유자를 싣지 않는다(서버가 이 앱의 소유자로 정하고, 계정 경로는 허브가 정한다)
 *  Enter 는 만들기, Ctrl/Cmd+Enter 는 만들고 열기, "계속 만들기"는 제목만 비우고 종류와
 *  주제를 남긴다. 쓰기 결과는 만든 것 · 맞춘 것 · 거절된 연결과 그 이유를 말한다. */
import { api, cfg } from "./api";
import { COPY } from "./copy";
import { esc, openSheet, sheetActions, showNotice } from "./components";
import { viewHooks } from "./topbar";
import {
  NODE_KINDS, RELS, edgeRefusal, fillTopicList, findNodes, forgetTopics, isWhen, nodeHitsHtml,
  relRefusal, slugOf, stampSlug, type NodeKind, type Rel,
} from "./authoring";

export type Made = { id: string; title: string; domain: string };
export type ComposerOpts = {
  kind?: NodeKind; title?: string; oneLiner?: string; topic?: string;
  /** 흐름 D — 이 노드가 나온 문서. props 에 남긴다(어디서 왔는지 되짚을 자리). */
  sourceDoc?: string;
};

type Target = { id: string; title: string };
type RelRow = { rel: Rel; target: Target | null };

const today = (): string => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

/** 컴포저를 연다. 처음 만든 노드로 풀린다(닫으면 null) — `[[` 의 "개념 만들기"가 그 제목을 쓴다. */
export function openComposer(o: ComposerOpts = {}): Promise<Made | null> {
  return new Promise((resolve) => {
    let first: Made | null = null;
    const done = (m: Made | null) => { if (!first && m) first = m; };
    const sheet = openSheet({ title: COPY.author.composerTitle, subtitle: COPY.author.composerSub,
                              onClose: () => resolve(first) });
    mount(sheet, o, done);
  });
}

function mount(sheet: ReturnType<typeof openSheet>, o: ComposerOpts, done: (m: Made) => void) {
  const A = COPY.author;
  let kind: NodeKind = o.kind || "concept";
  const stamp = stampSlug();
  const rels: RelRow[] = [];
  let idState: "" | "checking" | "new" | "exists" = "";
  let checkSeq = 0;
  let busy = false;

  sheet.body.innerHTML = `
    <div class="nc">
      <div class="ei-field"><span id="nc-kind-l">${esc(A.fKind)}</span>
        <div class="seg nc-kinds" role="radiogroup" aria-labelledby="nc-kind-l">${
          NODE_KINDS.map((k) => `<button type="button" class="chip" role="radio" data-kind="${k}"
            data-tip="${esc(A.kindHint[k])}">${esc(A.kinds[k])}</button>`).join("")}</div>
        <p class="hint nc-kind-hint" id="nc-kind-hint"></p>
      </div>
      <label class="ei-field"><span>${esc(A.fTitle)}</span>
        <input id="nc-title" type="text" autocomplete="off" value="${esc(o.title || "")}"/></label>
      <p class="hint nc-id"><span id="nc-id"></span> <span id="nc-id-state"></span>
        <button class="rail-x" type="button" id="nc-open-existing" hidden>${esc(A.openExisting)}</button></p>
      <label class="ei-field"><span>${esc(A.fOneLiner)}</span>
        <input id="nc-ol" type="text" autocomplete="off" value="${esc(o.oneLiner || "")}"/></label>
      <label class="ei-field"><span>${esc(A.fTopic)}</span>
        <input id="nc-topic" type="text" list="nc-topics" autocomplete="off" spellcheck="false"
               placeholder="${esc(A.topicHint)}" value="${esc(o.topic || "")}"/></label>
      <datalist id="nc-topics"></datalist>
      <details class="nc-more" id="nc-more"><summary id="nc-more-sum"></summary>
        <div id="nc-more-body"></div></details>
      <details class="nc-links" id="nc-links"><summary>${esc(A.linksSum)}</summary>
        <div id="nc-rels"></div>
        <button class="rail-x" type="button" id="nc-rel-add">+ ${esc(A.linkAdd)}</button>
      </details>
      <label class="nc-keep"><input type="checkbox" id="nc-keep"/> ${esc(A.keepCreating)}</label>
      <p class="hint nc-keys">${esc(A.keysHint)}</p>
    </div>`;
  // 결과·거절 한 줄은 발에 선다 — 몸은 스크롤 상자라 거기 두면 긴 폼에서 안 보인다.
  sheet.foot.innerHTML = `<div id="nc-msg" class="nc-msg" role="status"></div>`
    + sheetActions({ id: "nc-create", label: A.create },
                   { id: "nc-create-open", label: A.createOpen });
  const $ = <T extends HTMLElement>(s: string) => sheet.el.querySelector<T>(s)!;
  const titleEl = $<HTMLInputElement>("#nc-title");
  const olEl = $<HTMLInputElement>("#nc-ol");
  const topicEl = $<HTMLInputElement>("#nc-topic");
  const msgEl = $<HTMLElement>("#nc-msg");
  void fillTopicList($<HTMLDataListElement>("#nc-topics"));

  const idOf = () => `${kind}:${slugOf(titleEl.value) || stamp}`;
  const msg = (html: string) => { msgEl.innerHTML = html; };

  // ── 종류 ────────────────────────────────────────────────────────────────────
  function paintKind() {
    sheet.el.querySelectorAll<HTMLElement>(".nc-kinds .chip").forEach((b) => {
      const on = b.dataset.kind === kind;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", String(on));
    });
    $("#nc-kind-hint").textContent = A.kindHint[kind];
    const more = $<HTMLDetailsElement>("#nc-more");
    const body = $("#nc-more-body");
    const field = (id: string, label: string, ph = "", area = false, val = "") => area
      ? `<label class="ei-field"><span>${esc(label)}</span><textarea id="${id}" rows="2">${esc(val)}</textarea></label>`
      : `<label class="ei-field"><span>${esc(label)}</span><input id="${id}" type="text"
           autocomplete="off" placeholder="${esc(ph)}" value="${esc(val)}"/></label>`;
    // 종류마다 따로 적는 자리. 개념은 없다(한 줄 설명이 그 자리다).
    more.hidden = kind === "concept";
    if (kind === "decision") {
      $("#nc-more-sum").textContent = A.moreDecision;
      body.innerHTML = field("nc-chosen", A.fChosen) + field("nc-why", A.fWhy, "", true);
    } else if (kind === "record") {
      $("#nc-more-sum").textContent = A.moreRecord;
      body.innerHTML = field("nc-valid", A.fValidFrom, A.datePh);
    } else if (kind === "episode") {
      // 사건은 날짜가 뜻의 절반이라 펼쳐 두고 오늘로 채운다.
      $("#nc-more-sum").textContent = A.moreEpisode;
      body.innerHTML = field("nc-date", A.fDate, A.datePh, false, today());
      more.open = true;
    } else body.innerHTML = "";
    paintRels();
    void checkId();
  }
  sheet.el.querySelector(".nc-kinds")!.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>("[data-kind]");
    if (!b) return;
    kind = b.dataset.kind as NodeKind;
    paintKind();
  });
  // 라디오 묶음의 화살표 — 고르면서 옮긴다.
  sheet.el.querySelector(".nc-kinds")!.addEventListener("keydown", (e) => {
    const k = (e as KeyboardEvent).key;
    if (k !== "ArrowRight" && k !== "ArrowLeft") return;
    e.preventDefault();
    const i = NODE_KINDS.indexOf(kind) + (k === "ArrowRight" ? 1 : -1);
    kind = NODE_KINDS[(i + NODE_KINDS.length) % NODE_KINDS.length];
    paintKind();
    sheet.el.querySelector<HTMLElement>(`.nc-kinds [data-kind="${kind}"]`)?.focus();
  });

  // ── id 미리보기와 사전 확인 ──────────────────────────────────────────────────
  let checkTimer = 0;
  async function checkId() {
    const id = idOf();
    // 제목이 비었으면 id 도 말하지 않는다 — 시각 조각을 먼저 보이면 그것이 id 인 줄 안다.
    $("#nc-id").textContent = titleEl.value.trim() ? A.idPreview(id) : "";
    $("#nc-open-existing").hidden = true;
    if (!titleEl.value.trim()) { idState = ""; $("#nc-id-state").textContent = ""; return; }
    idState = "checking";
    $("#nc-id-state").textContent = `· ${A.idChecking}`;
    const mine = ++checkSeq;
    const r = await api.nodeContext(cfg.owner, id, 1).catch(() => null);
    if (mine !== checkSeq) return;
    idState = r?.executed ? "exists" : "new";
    $("#nc-id-state").textContent = `· ${idState === "exists" ? A.idExists : A.idNew}`;
    $("#nc-open-existing").hidden = idState !== "exists";
  }
  titleEl.addEventListener("input", () => {
    $("#nc-id").textContent = titleEl.value.trim() ? A.idPreview(idOf()) : "";
    window.clearTimeout(checkTimer);
    checkTimer = window.setTimeout(() => void checkId(), 250);
  });
  $("#nc-open-existing").addEventListener("click", () => {
    const id = idOf();
    sheet.close();
    const h = viewHooks();
    if (h?.openNodePane) void h.openNodePane(id);
    else location.hash = `#view/read/${encodeURIComponent(id)}`;
  });

  // ── 연결(선택) ──────────────────────────────────────────────────────────────
  function paintRels() {
    const box = $("#nc-rels");
    box.innerHTML = rels.map((r, i) => {
      const why = r.target ? relRefusal(r.rel, idOf(), r.target.id) : null;
      return `<div class="nc-rel" data-i="${i}">
        <select class="nc-rel-type" aria-label="${esc(A.linksSum)}">${RELS.map((x) =>
          `<option value="${x}"${x === r.rel ? " selected" : ""}>${esc(A.rel[x])}</option>`).join("")}</select>
        <div class="nc-rel-target">
          <input type="text" autocomplete="off" spellcheck="false" placeholder="${esc(A.linkTargetPh)}"
                 value="${esc(r.target?.title || "")}" aria-label="${esc(A.linkTargetPh)}"/>
          <div class="cm-assist-menu nc-rel-hits" role="listbox" hidden></div>
        </div>
        <button class="rail-x nc-rel-x" type="button" aria-label="${esc(A.linkRemove)}"
                data-tip="${esc(A.linkRemove)}">✕</button>
        ${why ? `<p class="hint nc-rel-why">${esc(why)}</p>` : ""}
      </div>`;
    }).join("");
  }
  $("#nc-rel-add").addEventListener("click", () => {
    rels.push({ rel: "relates_to", target: null });
    paintRels();
    sheet.el.querySelector<HTMLInputElement>(`.nc-rel[data-i="${rels.length - 1}"] input`)?.focus();
  });
  const relsBox = $("#nc-rels");
  relsBox.addEventListener("change", (e) => {
    const sel = (e.target as HTMLElement).closest<HTMLSelectElement>(".nc-rel-type");
    if (!sel) return;
    rels[Number(sel.closest<HTMLElement>(".nc-rel")!.dataset.i)].rel = sel.value as Rel;
    paintRels();
  });
  relsBox.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    const row = t.closest<HTMLElement>(".nc-rel");
    if (!row) return;
    const i = Number(row.dataset.i);
    if (t.closest(".nc-rel-x")) { rels.splice(i, 1); paintRels(); return; }
    const hit = t.closest<HTMLElement>("[data-id]");
    if (hit) {
      rels[i].target = { id: hit.dataset.id!, title: hit.dataset.title || hit.dataset.id! };
      paintRels();
    }
  });
  let relTimer = 0;
  relsBox.addEventListener("input", (e) => {
    const input = (e.target as HTMLElement).closest<HTMLInputElement>(".nc-rel-target input");
    if (!input) return;
    const row = input.closest<HTMLElement>(".nc-rel")!;
    const i = Number(row.dataset.i);
    rels[i].target = null;                        // 글자를 고치면 고른 것이 풀린다
    window.clearTimeout(relTimer);
    const q = input.value.trim();
    const list = row.querySelector<HTMLElement>(".nc-rel-hits")!;
    if (q.length < 2) { list.hidden = true; return; }
    relTimer = window.setTimeout(() => void (async () => {
      const { hits, note } = await findNodes(q);
      if (!row.isConnected) return;
      list.innerHTML = nodeHitsHtml(hits, note);
      list.hidden = false;
    })(), 200);
  });

  // ── 만들기 ──────────────────────────────────────────────────────────────────
  async function create(open: boolean) {
    if (busy) return;
    const title = titleEl.value.trim();
    const topic = topicEl.value.trim();
    const ol = olEl.value.trim();
    if (!title) { msg(esc(A.needTitle)); titleEl.focus(); return; }
    if (!topic) { msg(esc(A.needTopic)); topicEl.focus(); return; }
    const val = (s: string) => sheet.el.querySelector<HTMLInputElement | HTMLTextAreaElement>(s)?.value.trim() || "";
    const when = kind === "record" ? val("#nc-valid") : kind === "episode" ? val("#nc-date") : "";
    if (when && !isWhen(when)) { msg(esc(A.dateBad)); return; }
    const id = idOf();
    // 확인이 아직 안 끝났으면 기다린다 — 있는 id 에 조용히 합치는 길을 열어 두지 않는다.
    if (idState !== "new" && idState !== "exists") await checkId();
    if (idState === "exists") { msg(esc(A.idTaken(id))); $("#nc-open-existing").focus(); return; }
    // 대상 칸에 글자만 치고 목록에서 고르지 않은 연결 — 조용히 버리지 않고 묻는다.
    const typedNoPick = [...sheet.el.querySelectorAll<HTMLElement>(".nc-rel")].some((row) =>
      !rels[Number(row.dataset.i)]?.target && !!row.querySelector("input")?.value.trim());
    if (typedNoPick) { msg(esc(A.linkNoTarget)); return; }

    const node: Record<string, unknown> = { id, type: kind, title, domain: topic };
    const props: Record<string, unknown> = {};
    // 한 줄 설명의 자리는 종류마다 다르다: 개념은 `one_liner` 열, 사건은 `summary` 열,
    // 기록·결정은 열이 없어 props.note(상세 카드가 읽는 자리).
    if (ol) {
      if (kind === "concept") node.one_liner = ol;
      else if (kind === "episode") node.summary = ol;
      else props.note = ol;
    }
    if (kind === "decision") {
      if (val("#nc-chosen")) props.chosen = val("#nc-chosen");
      if (val("#nc-why")) props.why = val("#nc-why");
    }
    if (kind === "record" && when) node.valid_from = when;
    if (kind === "episode" && when) node.date = when;
    if (o.sourceDoc) props.source_doc = o.sourceDoc;
    if (Object.keys(props).length) node.props = props;
    const edges = rels.filter((r) => r.target && !relRefusal(r.rel, id, r.target.id))
      .map((r) => ({ type: r.rel, from: id, to: r.target!.id }));
    const refused = rels.filter((r) => r.target && relRefusal(r.rel, id, r.target.id));

    busy = true;
    const btns = [...sheet.foot.querySelectorAll<HTMLButtonElement>("button")];
    btns.forEach((b) => { b.disabled = true; });
    msg(esc(A.creating));
    let res: any;
    try {
      res = await api.ingestFragment({ nodes: [node], edges });
    } catch (e: any) {
      res = { executed: false, error: String(e?.message || "") };
    }
    busy = false;
    btns.forEach((b) => { b.disabled = false; });
    if (res?.executed === false) {
      msg(`<span class="error-banner">${esc(A.createFail)}</span>`
        + (res.error ? ` <span class="hint">${esc(String(res.error))}</span>` : ""));
      return;
    }
    if ((res?.id_conflicts || []).includes(id)) { msg(esc(A.conflict(id))); return; }
    const nodes = res?.merged?.nodes || {};
    if (!(nodes.created || nodes.matched)) {
      msg(`<span class="error-banner">${esc(A.createFail)}</span>`); return;
    }
    // 무엇을 했는지 한 줄씩: 만들었다(또는 맞췄다) · 연결 몇 개 · 안 된 연결과 이유.
    const lines = [nodes.created ? A.created(A.kinds[kind], title) : A.matched(title)];
    const madeEdges = Number(res?.merged?.edges?.created || 0);
    if (madeEdges) lines.push(A.linksMade(madeEdges));
    for (const r of refused) lines.push(A.linkRefused(r.target!.title, relRefusal(r.rel, id, r.target!.id)!));
    // 서버가 일부를 건너뛰었다. 응답은 어느 것인지 이름으로 주지 않으므로(경고는 접힌
    // 산문이다) 보낸 대상을 함께 말한다 — 규칙 위반은 위에서 이미 걸렀으므로 드문 경우다.
    if (edges.length > madeEdges) {
      const sent = edges.map((e) => rels.find((r) => r.target?.id === e.to)?.target?.title || e.to);
      lines.push(A.linkRefused(sent.join(", "), edgeRefusal(res?.warnings)));
    }
    done({ id, title, domain: topic });
    forgetTopics();
    void import("./lnb").then((m) => m.refreshLnb({ quiet: true }));
    const h = viewHooks();
    if (open) {
      sheet.close();
      if (h?.openNodePane) void h.openNodePane(id, topic, title);
      else location.hash = `#view/read/${encodeURIComponent(id)}`;
      return;
    }
    if ($<HTMLInputElement>("#nc-keep").checked) {
      // 계속 만들기 — 종류와 주제는 남기고 제목·설명·연결만 비운다.
      titleEl.value = ""; olEl.value = "";
      rels.length = 0;
      paintKind();
      msg(lines.map(esc).join("<br>"));
      titleEl.focus();
      if (h?.reveal) void h.reveal(id, topic, title);
      return;
    }
    sheet.close();
    if (h?.reveal) void h.reveal(id, topic, title);
    else location.hash = `#view/trace/${encodeURIComponent(id)}`;
    // 빠진 연결이 있으면 시트가 닫힌 뒤에도 그 사실을 한 번 말한다(만든 것만 말하고 끝내지 않는다).
    if (lines.length > 1 && (refused.length || edges.length > madeEdges))
      void showNotice(lines.join(" "), A.done);
  }

  sheet.foot.querySelector("#nc-create")!.addEventListener("click", () => void create(false));
  sheet.foot.querySelector("#nc-create-open")!.addEventListener("click", () => void create(true));
  sheet.el.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing || e.shiftKey) return;
    const t = e.target as HTMLElement;
    // 연결 대상 검색칸에서 Enter = 목록의 첫 노드를 고른다(만들기가 아니다).
    const relInput = t.closest<HTMLInputElement>(".nc-rel-target input");
    if (relInput && !(e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      relInput.closest(".nc-rel")?.querySelector<HTMLElement>("[data-id]")?.click();
      return;
    }
    // 여러 줄 칸의 Enter 는 줄바꿈이고, 단추·접기·고르기의 Enter 는 그 자신의 일이다.
    if (t instanceof HTMLTextAreaElement && !(e.ctrlKey || e.metaKey)) return;
    if (t.closest("button, summary, select")) return;
    e.preventDefault();
    void create(e.ctrlKey || e.metaKey);
  });

  paintKind();
  titleEl.focus();
  if (titleEl.value) titleEl.select();
}

