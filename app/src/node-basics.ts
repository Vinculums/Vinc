/** 흐름 H — 노드의 기본 정보(제목 · 한 줄 설명 · 주제 · 태그 · 시점)를 문서 열에서 고친다.
 *
 *  여태 고칠 수 있는 것은 속성(props)뿐이었다. 쓰기는 속성 편집과 같은 두 규칙을 따른다:
 *   · **판 확인** — 저장 직전에 캐시 없이 다시 읽어 판(rev)이 편집을 시작할 때와 같을 때만
 *     쓴다. 그 사이 누가 고쳤으면 덮지 않고 멈춘다(필드 이력이 없어 덮으면 복구가 없다).
 *   · **바뀐 필드만** 싣는다. 싣지 않은 필드는 서버가 보존한다. 단 props 는 실으면 통째로
 *     교체되므로, 기록·결정의 한 줄 설명(props.note)을 고칠 때는 지금 props 전체에 합쳐서
 *     보낸다 — 안 실은 키가 경고 없이 사라지는 함정이 거기 있다.
 *  근거: [[decision:node-props-are-edited-in-the-document-pane-as-a-whole-and-checked-against-the-read-revision-2026-09-26]] */
import { api, cfg } from "./api";
import { COPY } from "./copy";
import { esc } from "./components";
import { fillTopicList, forgetTopics, isWhen } from "./authoring";

type Kind = "concept" | "record" | "decision" | "episode";
type Basics = { title: string; oneLiner: string; topic: string; tags: string[]; when: string };
/** node-context 가 준 것 — 제목·주제·한 줄·props·판. */
type BasicsSource = {
  node_kind?: string; rev?: number; title?: string; domain?: string;
  one_liner?: string; props?: Record<string, unknown>;
};

/** 한 줄 설명이 사는 자리 — 개념은 `one_liner`, 사건은 `summary`, 기록·결정은 props.note. */
const lineField = (k: Kind): "one_liner" | "summary" | "note" =>
  k === "concept" ? "one_liner" : k === "episode" ? "summary" : "note";
/** 시점이 사는 자리 — 개념·기록은 `content_time`, 사건은 `date`. 결정은 없다(열이 없다). */
const whenField = (k: Kind): "content_time" | "date" | null =>
  k === "episode" ? "date" : k === "decision" ? null : "content_time";

/** 태그와 시점은 node-context 가 싣지 않는다 — 그래프의 씨앗 조회(깊이 1)로 읽는다.
 *  `ok:false` = 못 읽었다. 그때 두 칸은 **고칠 수 없게** 둔다: 빈 태그 칸에 하나를 더해
 *  저장하면 태그 목록이 통째로 그 하나로 바뀐다(있던 태그가 사라진다). */
async function readExtras(id: string, k: Kind): Promise<{ ok: boolean; tags: string[]; when: string }> {
  try {
    const g: any = await api.graph(cfg.owner, { seed: id, depth: 1 });
    const n = (g?.nodes || []).find((x: any) => x.id === id);
    if (!n) return { ok: false, tags: [], when: "" };
    const tags = Array.isArray(n.tags) ? n.tags.map(String) : [];
    // 사건의 날짜는 `ts` 로 온다(없으면 수정 시각으로 채워지므로 날짜 꼴일 때만 받는다).
    const when = k === "episode" ? (isWhen(String(n.ts || "")) ? String(n.ts) : "")
      : String(n.content_ts || "");
    return { ok: true, tags, when };
  } catch { return { ok: false, tags: [], when: "" }; }
}

/** 기본 정보 절을 그린다(읽기 모습 + 편집 단추). `onSaved` 는 View 가 지도와 열을 새로 그린다. */
export async function drawBasics(box: HTMLElement, id: string, src: BasicsSource,
                                 onSaved: (domain: string) => void): Promise<void> {
  const k = src.node_kind as Kind;
  if (!["concept", "record", "decision", "episode"].includes(k)) { box.innerHTML = ""; return; }
  const A = COPY.author;
  const props = src.props || {};
  const extras = await readExtras(id, k);
  if (!box.isConnected) return;
  const cur: Basics = {
    title: src.title || "",
    oneLiner: lineField(k) === "note" ? String(props.note ?? "") : src.one_liner || "",
    topic: src.domain || "",
    tags: extras.tags,
    when: extras.when,
  };
  const none = `<span class="hint">${esc(COPY.ui.common.none)}</span>`;
  const unknown = `<span class="hint">${esc(A.extrasFail)}</span>`;
  const wf = whenField(k);
  const view = () => {
    box.innerHTML = `
      <section class="node-basics">
        <div class="props-head">
          <h3 class="hint rail-sec">${esc(A.basicsTitle)}</h3>
          <button class="rail-x basics-edit-btn" type="button" aria-label="${esc(A.basicsEditTip)}"
                  data-tip="${esc(A.basicsEditTip)}">${esc(A.basicsEdit)}</button>
        </div>
        <dl class="props-dl">
          <div class="props-row"><dt>${esc(A.fOneLiner)}</dt><dd>${cur.oneLiner ? esc(cur.oneLiner) : none}</dd></div>
          <div class="props-row"><dt>${esc(A.fTopic)}</dt><dd>${cur.topic ? esc(cur.topic) : none}</dd></div>
          <div class="props-row"><dt>${esc(A.fTags)}</dt><dd>${!extras.ok ? unknown
            : cur.tags.length ? esc(cur.tags.join(", ")) : none}</dd></div>
          ${wf ? `<div class="props-row"><dt>${esc(A.fWhen)}</dt><dd>${!extras.ok ? unknown
            : cur.when ? esc(cur.when) : none}</dd></div>` : ""}
        </dl>
      </section>`;
    box.querySelector(".basics-edit-btn")!.addEventListener("click", edit);
  };

  function edit() {
    const f = (idn: string, label: string, val: string, ph = "", hint = "", off = false) =>
      `<label class="ei-field"><span>${esc(label)}</span>
         <input id="${idn}" type="text" autocomplete="off" value="${esc(val)}" placeholder="${esc(ph)}"
                ${idn === "nb-topic" ? 'list="nb-topics" spellcheck="false"' : ""}${off ? " disabled" : ""}/>
         ${hint ? `<small class="hint">${esc(off ? A.extrasFail : hint)}</small>` : ""}</label>`;
    box.innerHTML = `
      <section class="node-basics props-editing">
        <div class="props-head"><h3 class="hint rail-sec">${esc(A.basicsTitle)}</h3></div>
        ${f("nb-title", A.fTitle, cur.title)}
        <label class="ei-field"><span>${esc(A.fOneLiner)}</span>
          <textarea id="nb-ol" rows="2">${esc(cur.oneLiner)}</textarea></label>
        ${f("nb-topic", A.fTopic, cur.topic, A.topicHint)}
        <datalist id="nb-topics"></datalist>
        ${f("nb-tags", A.fTags, cur.tags.join(", "), "", A.tagsHint, !extras.ok)}
        ${wf ? f("nb-when", A.fWhen, cur.when, A.datePh, A.whenHint, !extras.ok) : ""}
        <p class="hint props-status" role="status"></p>
        <div class="props-acts">
          <button class="btn" type="button" data-nb="cancel">${esc(COPY.ui.viewx.propsCancel)}</button>
          <button class="btn btn-primary" type="button" data-nb="save">${esc(COPY.ui.viewx.propsSave)}</button>
        </div>
      </section>`;
    void fillTopicList(box.querySelector<HTMLDataListElement>("#nb-topics")!);
    const q = <T extends HTMLElement>(s: string) => box.querySelector<T>(s);
    const status = (html: string) => { const s = q(".props-status"); if (s) s.innerHTML = html; };
    const read = (): Basics => ({
      title: q<HTMLInputElement>("#nb-title")!.value.trim(),
      oneLiner: q<HTMLTextAreaElement>("#nb-ol")!.value.trim(),
      topic: q<HTMLInputElement>("#nb-topic")!.value.trim(),
      tags: q<HTMLInputElement>("#nb-tags")!.value.split(",").map((t) => t.trim()).filter(Boolean),
      when: q<HTMLInputElement>("#nb-when")?.value.trim() || "",
    });

    async function save() {
      const next = read();
      if (!next.title) { status(esc(A.needTitle)); q<HTMLInputElement>("#nb-title")!.focus(); return; }
      if (next.when && !isWhen(next.when)) { status(esc(A.dateBad)); q<HTMLInputElement>("#nb-when")?.focus(); return; }
      const node: Record<string, unknown> = { id, type: k };
      if (next.title !== cur.title) node.title = next.title;
      if (next.topic !== cur.topic) node.domain = next.topic;
      // 못 읽은 두 칸은 싣지 않는다(서버가 보존한다).
      if (extras.ok && next.tags.join("\u0000") !== cur.tags.join("\u0000")) node.tags = next.tags;
      if (extras.ok && wf && next.when !== cur.when) node[wf] = next.when;
      const lf = lineField(k);
      const lineChanged = next.oneLiner !== cur.oneLiner;
      if (lineChanged && lf !== "note") node[lf] = next.oneLiner;
      if (Object.keys(node).length === 2 && !lineChanged) { status(esc(A.basicsNoChange)); return; }
      const btns = [...box.querySelectorAll<HTMLButtonElement>(".props-acts button")];
      btns.forEach((b) => { b.disabled = true; });
      status(esc(COPY.ui.viewx.propsSaving));
      try {
        // 판 확인 — 캐시 없이 지금 값을 읽는다. 다르면 덮지 않는다.
        const fresh = await api.nodeContext(cfg.owner, id, 1);
        if (!fresh.executed || (fresh.rev ?? 0) !== (src.rev ?? 0)) {
          status(`${esc(COPY.ui.viewx.propsConflict)} <button class="rail-x nb-reload" type="button">${
            esc(COPY.ui.viewx.propsReload)}</button>`);
          q(".nb-reload")?.addEventListener("click", () => onSaved(cur.topic));
          q<HTMLButtonElement>('[data-nb="cancel"]')!.disabled = false;
          return;
        }
        // props 는 통째로 교체된다 — 지금 값 **전체**에 합쳐 보낸다.
        if (lineChanged && lf === "note") {
          const whole: Record<string, unknown> = { ...(fresh.props || {}) };
          if (next.oneLiner) whole.note = next.oneLiner; else delete whole.note;
          node.props = whole;
        }
        const res: any = await api.ingestFragment({ nodes: [node] });
        if (res?.executed === false) throw new Error(res.error || "");
        const merged = (res?.merged?.nodes?.created || 0) + (res?.merged?.nodes?.matched || 0);
        if (!merged) throw new Error((res?.warnings || []).join(" · "));
        if (node.domain !== undefined) forgetTopics();
        void import("./lnb").then((m) => m.refreshLnb({ quiet: true }));
        onSaved(next.topic);
      } catch (err: any) {
        btns.forEach((b) => { b.disabled = false; });
        const why = String(err?.message || "").trim();
        status(`<span class="error-banner">${esc(A.basicsFail)}</span>${why ? ` <span class="hint">${esc(why)}</span>` : ""}`);
      }
    }

    q('[data-nb="cancel"]')!.addEventListener("click", view);
    q('[data-nb="save"]')!.addEventListener("click", () => void save());
    // Enter 로 저장(한 줄 설명 칸에서는 Ctrl/Cmd+Enter), Esc 는 편집만 접는다.
    box.querySelector(".node-basics")!.addEventListener("keydown", (e) => {
      const ke = e as KeyboardEvent;
      if (ke.key === "Escape") { ke.stopPropagation(); view(); return; }
      if (ke.key !== "Enter" || ke.isComposing) return;
      const inArea = ke.target instanceof HTMLTextAreaElement;
      if (inArea && !(ke.ctrlKey || ke.metaKey)) return;
      if ((ke.target as HTMLElement).closest("button")) return;
      ke.preventDefault();
      void save();
    });
    q<HTMLInputElement>("#nb-title")!.focus();
  }

  view();
}
