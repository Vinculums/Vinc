// 상세 열의 속성(props) 절: 노드의 자유 속성을 그리고, 그 자리에서 편집 줄로 바꿔 저장한다.
// 그래프 표면(`view.ts`)이 `ViewHost` 를 넘겨 만든다. 형식과 값의 변환은 `props-edit.ts` 가 한다.
import { api, messageOf, isWriteConflict } from "../api";
import { COPY } from "../copy";
import { esc } from "../components";
import { renderMarkdown } from "../doc-editor";
import { refreshLnb, isAgentRoleId } from "../lnb";
import { PROP_KINDS, propsEditable, rowsFrom, propsFrom, convertText, sameProps, propsPatchOf,
         type PropRow, type PropKind } from "../props-edit";
import type { N3, ViewHost } from "./view-host";

/** Agent 역할 계약을 읽는 순서: 임무 → 직함·팀 → 등급 → 금지·사람 몫 → 읽을 것·관문
 *  → 나머지. 목록에 없는 키는 저장된 순서대로 뒤에 온다.
 *  근거: [[vinc.canon.agent-roles §3]] */
const ROLE_PROP_ORDER = ["mission", "job_title", "team", "team_role", "tier", "rung",
  "delegate_rung", "never", "person_only", "reads", "gates", "test_command", "repos",
  "paths", "tools", "disallowed_tools", "isolation", "writes_graph"];

/** 속성 절이 읽은 것 — 무슨 종인가(props 를 싣는가), 어느 판(rev)을 읽었는가. */
export type PropsMeta = { kind?: string; rev?: number; canEdit?: boolean };

/** 표면이 쥐는 속성 절의 손잡이. `drawNodeProps` 는 노드 맥락 응답이 올 때마다 표면이 부른다. */
export type PropsCard = {
  drawNodeProps(nd: N3, props?: Record<string, unknown>, meta?: PropsMeta): void;
};

/** 속성 절을 그 표면의 상세 열에 묶는다. 편집기는 저장 · 다시 불러오기 뒤에 `host.reloadNode` 로 열을 새로 읽는다. */
export function createPropsCard(host: ViewHost): PropsCard {
  const { detail } = host;

  /** 속성(props) 절 — 노드가 가진 자유 속성을 **통째로** 보여 준다. 값은 생긴 대로 그린다(글·목록·객체).
   *  비어 있으면 절을 그리지 않는다 — 빈 제목만 남기지 않는다.
   *  근거: [[decision:lnb-picks-in-the-graph-open-the-floating-card-and-the-side-pane-2026-10-28]] */
  function drawNodeProps(nd: N3, props?: Record<string, unknown>, meta: PropsMeta = {}) {
    const box = detail.querySelector<HTMLElement>("#node-props");
    if (!box) return;
    const P = props || {};
    const keys = Object.keys(P);
    // 편집할 수 있는 종이면 비어 있어도 절을 그린다 — 첫 속성을 더할 자리가 있어야 한다.
    const editable = propsEditable(meta.kind) && meta.canEdit !== false;
    if (keys.length === 0 && !editable) { box.innerHTML = ""; return; }
    const role = isAgentRoleId(nd.id);
    const order = role
      ? [...ROLE_PROP_ORDER.filter((k) => k in P),
         ...keys.filter((k) => !ROLE_PROP_ORDER.includes(k))]
      : keys;
    const V = COPY.ui.viewx;
    box.innerHTML = `
      <section class="node-props">
        <div class="props-head">
          <h3 class="hint rail-sec">${esc(V.propsTitle)}</h3>
          ${editable ? `<button class="rail-x props-edit-btn" type="button"
              aria-label="${esc(V.propsEditTip)}" data-tip="${esc(V.propsEditTip)}">${
              esc(V.propsEdit)}</button>` : ""}
        </div>
        ${keys.length ? propsDl(P, order, 0) : `<p class="hint">${esc(V.propsEmpty)}</p>`}
      </section>`;
    // 역할의 이름은 직함이 있으면 그것이다 — Agent 레일(lnb.ts roleTitle)과 같은 규칙.
    const job = role && typeof P.job_title === "string" ? P.job_title.trim() : "";
    const h2 = detail.querySelector("h2");
    if (job && h2) h2.textContent = job;
    box.querySelector(".props-edit-btn")?.addEventListener("click",
      () => openPropsEditor(nd, P, order, meta));
  }

  /** 속성 편집기 — 상세 열의 속성 절을 제자리에서 편집 줄로 바꾼다.
   *
   *  줄마다 이름 · 형식 · 값 · 지우기다(형식과 값의 변환은 `props-edit.ts`). 저장은
   *  **전체 교체**다: 서버는 props 를 통째로 갈아 끼우므로 보내는 것이 늘 편집 결과
   *  전체이고, 지운 줄이 곧 지운 속성이다.
   *  저장 직전에 캐시를 거치지 않고 다시 읽어 **판(rev)이 편집을 시작할 때와 같을 때만**
   *  쓴다. 그 사이 다른 창·에이전트·동기화가 고쳤으면 덮어쓰지 않고 멈춘다 — 필드 이력이
   *  없어서 덮어쓰면 복구가 없다. owner 는 싣지 않는다(서버가 기존 소유자를 지키고,
   *  계정 경로는 허브가 세션으로 정한다).
   *  근거: [[decision:node-props-are-edited-in-the-document-pane-as-a-whole-and-checked-against-the-read-revision-2026-09-26]] */
  function openPropsEditor(nd: N3, props: Record<string, unknown>, order: string[],
                           meta: PropsMeta) {
    const found = detail.querySelector<HTMLElement>("#node-props");
    if (!found || !meta.kind) return;
    const box: HTMLElement = found;     // 안쪽 함수들까지 좁힌 타입이 가도록
    const V = COPY.ui.viewx;
    let rows: PropRow[] = rowsFrom(props, order);
    if (rows.length === 0) rows = [{ key: "", kind: "text", text: "" }];

    const lines = (t: string) => Math.min(12, Math.max(2, t.split("\n").length));
    const valueHTML = (r: PropRow) => {
      if (r.kind === "bool") {
        const on = r.text.trim() === "true";
        return `<select class="props-val" aria-label="${esc(V.propsKinds.bool)}">
          <option value="true"${on ? " selected" : ""}>${esc(V.propsYes)}</option>
          <option value="false"${on ? "" : " selected"}>${esc(V.propsNo)}</option></select>`;
      }
      if (r.kind === "number")
        return `<input class="props-val" type="text" inputmode="decimal" spellcheck="false"
          value="${esc(r.text)}" aria-label="${esc(V.propsKinds.number)}">`;
      const ph = r.kind === "list" ? ` placeholder="${esc(V.propsListHint)}"` : "";
      const mono = r.kind === "json" ? " props-mono" : "";
      return `<textarea class="props-val${mono}" rows="${lines(r.text)}" spellcheck="${
        r.kind === "text"}"${ph} aria-label="${esc(V.propsKinds[r.kind])}">${esc(r.text)}</textarea>`;
    };
    const rowHTML = (r: PropRow, i: number) => `
      <div class="props-erow" data-i="${i}">
        <div class="props-erow-head">
          <input class="props-key" type="text" value="${esc(r.key)}" spellcheck="false"
                 autocomplete="off" placeholder="${esc(V.propsKeyPh)}" aria-label="${esc(V.propsKeyPh)}">
          <select class="props-kind" aria-label="${esc(V.propsKindLabel)}">${PROP_KINDS.map((k) =>
            `<option value="${k}"${k === r.kind ? " selected" : ""}>${esc(V.propsKinds[k])}</option>`)
            .join("")}</select>
          <button class="rail-x props-del" type="button" aria-label="${esc(V.propsRemove(r.key))}"
                  data-tip="${esc(V.propsRemove(r.key))}">✕</button>
        </div>
        ${valueHTML(r)}
        <p class="props-err" role="alert" hidden></p>
      </div>`;

    /** 화면의 줄을 그대로 읽는다 — 상태의 원천은 DOM 하나다(다시 그릴 때만 배열로 옮긴다). */
    const collect = (): PropRow[] => [...box.querySelectorAll<HTMLElement>(".props-erow")]
      .map((row) => ({
        key: row.querySelector<HTMLInputElement>(".props-key")!.value,
        kind: row.querySelector<HTMLSelectElement>(".props-kind")!.value as PropKind,
        text: row.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
          ".props-val")!.value,
      }));
    const paint = (focusRow?: number) => {
      box.innerHTML = `
        <section class="node-props props-editing">
          <div class="props-head"><h3 class="hint rail-sec">${esc(V.propsTitle)}</h3></div>
          <div class="props-rows">${rows.map(rowHTML).join("")}</div>
          <button class="rail-x props-add" type="button">+ ${esc(V.propsAdd)}</button>
          <p class="hint props-status" role="status"></p>
          <div class="props-acts">
            <button class="btn" type="button" data-props="cancel">${esc(V.propsCancel)}</button>
            <button class="btn btn-primary" type="button" data-props="save">${esc(V.propsSave)}</button>
          </div>
        </section>`;
      if (focusRow !== undefined)
        box.querySelector<HTMLElement>(`.props-erow[data-i="${focusRow}"] .props-key`)?.focus();
    };
    const status = (html: string) => {
      const el = box.querySelector<HTMLElement>(".props-status");
      if (el) el.innerHTML = html;
    };
    // 편집기는 절 상자에 리스너를 단다. 접을 때 상자를 새로 갈아 끼워 그 리스너를 떼어
    // 낸다 — 같은 상자를 다시 쓰면 다음 편집에서 클릭 하나가 두 번 처리된다.
    const release = () => {
      const fresh = box.cloneNode(false) as HTMLElement;
      box.replaceWith(fresh);
    };
    const cancel = () => { release(); drawNodeProps(nd, props, meta); };
    const reload = () => { release(); void host.reloadNode(nd); };

    async function save() {
      rows = collect();
      const { props: next, errors } = propsFrom(rows);
      box.querySelectorAll<HTMLElement>(".props-err").forEach((p) => { p.hidden = true; });
      box.querySelectorAll(".props-erow").forEach((r) => r.removeAttribute("aria-invalid"));
      if (errors.length) {
        for (const er of errors) {
          const row = box.querySelector<HTMLElement>(`.props-erow[data-i="${er.index}"]`);
          const p = row?.querySelector<HTMLElement>(".props-err");
          if (p) { p.textContent = V.propsErr[er.code]; p.hidden = false; }
          row?.setAttribute("aria-invalid", "true");
        }
        box.querySelector<HTMLElement>(`.props-erow[data-i="${errors[0].index}"] .props-key`)?.focus();
        return;
      }
      if (sameProps(next, props)) { status(esc(V.propsNoChange)); return; }
      const acts = [...box.querySelectorAll<HTMLButtonElement>("button")];
      acts.forEach((b) => { b.disabled = true; });
      status(esc(V.propsSaving));
      const conflict = () => {
        status(`${esc(V.propsConflict)} <button class="rail-x props-reload" type="button">${
          esc(V.propsReload)}</button>`);
        box.querySelector(".props-reload")?.addEventListener("click", reload);
        box.querySelector<HTMLButtonElement>('[data-props="cancel"]')!.disabled = false;
      };
      try {
        // 판 확인은 서버가 쓰기와 같은 writer 안에서 한다(0.9.8 `expected_rev`): 편집을
        // 시작한 판이 아니면 쓰지 않고 충돌로 답한다. 바뀐 키만 `props_patch` 로 싣는다.
        // patch 로 못 싣는 편집(값으로서의 null)은 같은 판 조건으로 전체를 싣는다.
        const patch = propsPatchOf(props, next);
        const res: any = await api.editNode({ id: nd.id, type: meta.kind!,
          ...(patch ? { props_patch: patch } : { props: next }) }, meta.rev ?? 0);
        if (isWriteConflict(res)) { conflict(); return; }
        if (res?.executed === false) throw new Error(res.error || "");
        // ⚠ `merged` 는 실제로 들어간 것만 센다 — warnings 를 안 읽으면 건너뛴 쓰기
        // (남이 소유한 id 등)를 성공으로 읽는다.
        const merged = (res?.merged?.nodes?.created || 0) + (res?.merged?.nodes?.matched || 0);
        if (!merged) throw new Error((res?.warnings || []).join(" · "));
        release();
        await host.reloadNode(nd);      // 새 판으로 다시 그린다(편집 단추가 새 rev 를 든다)
        // 역할의 직함·팀은 Agent 목록이 props 에서 읽는다 — 목록도 새로 읽는다.
        if (isAgentRoleId(nd.id)) void refreshLnb({ quiet: true });
      } catch (err: any) {
        if (isWriteConflict(err)) { conflict(); return; }
        acts.forEach((b) => { b.disabled = false; });
        const why = messageOf(err, "").trim();
        status(`<span class="error-banner">${esc(V.propsFail)}${why ? ` ${esc(why)}` : ""}</span>`);
      }
    }

    paint();
    box.addEventListener("click", (e) => {
      const t = e.target as HTMLElement;
      if (t.closest(".props-add")) {
        rows = [...collect(), { key: "", kind: "text", text: "" }];
        paint(rows.length - 1);
      } else if (t.closest(".props-del")) {
        const i = Number(t.closest<HTMLElement>(".props-erow")!.dataset.i);
        rows = collect().filter((_, j) => j !== i);
        paint();
        box.querySelector<HTMLElement>(".props-add")?.focus();
      } else if (t.closest('[data-props="cancel"]')) cancel();
      else if (t.closest('[data-props="save"]')) void save();
    });
    box.addEventListener("change", (e) => {
      const sel = (e.target as HTMLElement).closest<HTMLSelectElement>(".props-kind");
      if (!sel) return;
      const cur = collect();
      const i = Number(sel.closest<HTMLElement>(".props-erow")!.dataset.i);
      const prev = rows[i]?.kind ?? "text";
      cur[i] = { ...cur[i], text: convertText(cur[i].text, prev, cur[i].kind) };
      rows = cur;
      paint();
      box.querySelector<HTMLElement>(`.props-erow[data-i="${i}"] .props-val`)?.focus();
    });
    // Esc 는 편집만 접는다(열 전체를 닫는 전역 Esc 까지 가지 않게). Ctrl/Cmd+Enter 는 저장.
    box.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); cancel(); }
      else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void save(); }
    });
    box.querySelector<HTMLElement>(".props-key")?.focus();
  }

  return { drawNodeProps };
}

function propsDl(obj: Record<string, unknown>, keys: string[], depth: number): string {
  return `<dl class="props-dl">${keys.map((k) => `
    <div class="props-row"><dt>${esc(k)}</dt><dd>${propValueHTML(k, obj[k], depth)}</dd></div>`)
    .join("")}</dl>`;
}

function propValueHTML(key: string, v: unknown, depth: number): string {
  const none = `<span class="hint">${esc(COPY.ui.common.none)}</span>`;
  if (v === null || v === undefined || v === "") return none;
  if (typeof v === "string") {
    // 여러 줄이거나 긴 글은 문서와 같은 렌더러로. 짧은 값은 글자 그대로 둔다.
    return v.includes("\n") || v.length > 160
      ? `<div class="doc-render">${renderMarkdown(v)}</div>` : inlineCode(v);
  }
  if (typeof v === "number" || typeof v === "boolean")
    return `<span class="props-scalar">${esc(String(v))}</span>`;
  if (Array.isArray(v)) {
    if (v.length === 0) return none;
    // `reads` 는 "이 순서로 읽어라"라서 번호를 단다. 나머지는 순서가 뜻이 아니다.
    const tag = key === "reads" ? "ol" : "ul";
    return `<${tag} class="props-list">${v.map((x) => `<li>${
      typeof x === "object" && x !== null ? propValueHTML("", x, depth + 1)
        : inlineCode(String(x))}</li>`).join("")}</${tag}>`;
  }
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    const ks = Object.keys(o);
    if (ks.length === 0) return none;
    // 두 단을 넘으면 원문 JSON 으로 — 들여쓰기가 열 폭을 다 먹는다.
    return depth >= 2
      ? `<pre class="props-json">${esc(JSON.stringify(o, null, 2))}</pre>`
      : propsDl(o, ks, depth + 1);
  }
  return esc(String(v));
}

/** 짧은 값 — 이스케이프하고 `코드` 표기만 살린다(역할 계약은 도구 이름을 그렇게 적는다). */
function inlineCode(s: string): string {
  return esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
}
