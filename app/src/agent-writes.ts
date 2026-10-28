/** 에이전트 쓰기 검토(A5) — Agent 보드 맨 위. 최근 쓰기를 출처별로 묶고, 검사를 다 통과한 묶음은
 *  "모두 유지"로 한 번에 넘기며 사람이 볼 항목만 남긴다. 서버 계약: `api/agent_writes.py`.
 *
 *  "유지"는 그래프에 쓰지 않는다 — 이미 들어간 쓰기를 받아들인다는 이 사람의 표시라 이 기기에만
 *  남긴다(묶음의 마지막 쓰기 시각까지). 그 뒤로 같은 출처가 또 쓰면 묶음이 다시 열린다.
 *  근거: [[vinc.research.graph-pages-and-marks-2026-10-25]] §5 A5 */
import { api, cfg } from "./api";
import { COPY } from "./copy";
import { esc } from "./components";

type Item = { kind: "node" | "relation"; id: string; title: string; at: string; checks: string[];
  node_kind?: string; type?: string; from?: string; to?: string };
type Group = { source: { key: string; kind: "run" | "none" }; items: Item[]; open: number };
export type AgentWrites = { executed: boolean; groups?: Group[]; total?: number; truncated?: boolean;
  days?: number; error?: string };

const KEPT_KEY = "vinc.agent-writes.kept";
/** 출처마다 "여기까지 유지했다"의 마지막 쓰기 시각. 읽기·쓰기 실패는 화면을 깨지 않는다. */
function kept(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(KEPT_KEY) || "{}") || {}; } catch { return {}; }
}
function keep(scope: string, key: string, at: string): void {
  try {
    const all = kept();
    all[`${scope}|${key}`] = at;
    localStorage.setItem(KEPT_KEY, JSON.stringify(all));
  } catch { /* 저장소가 막혀 있으면 이번 화면에서만 숨는다 */ }
}

/** 묶음의 가장 늦은 쓰기 시각 — 유지 표시는 이것까지를 덮는다. */
const latest = (g: Group) => g.items.reduce((m, i) => (i.at > m ? i.at : m), "");

export async function mountAgentWrites(box: HTMLElement, live: () => boolean): Promise<void> {
  const A = COPY.ui.agentWrites;
  box.innerHTML = `<p class="hint">${esc(A.loading)}</p>`;
  let res: AgentWrites;
  try { res = await api.agentWrites(cfg.owner); } catch { res = { executed: false }; }
  if (!live()) return;
  if (!res.executed) { box.innerHTML = `<p class="hint">${esc(A.failed)}</p>`; return; }
  const scope = cfg.team || cfg.owner;
  const paint = () => {
    const marks = kept();
    const groups = (res.groups || []).filter((g) => {
      const k = marks[`${scope}|${g.source.key}`];
      return !(g.open === 0 && k && k >= latest(g));
    });
    const label = (g: Group) => g.source.kind === "run" ? A.sourceRun(g.source.key) : A.sourceNone;
    // 키를 글자로 부른다 — 덱 키 대조(tests/design/test_copy_parity.py)가 호출부를 찾을 수 있게.
    const why = (c: string) => c === "evidence_is_an_id" ? A.checks.evidence_is_an_id
      : c === "evidence_missing" ? A.checks.evidence_missing
      : c === "decision_without_start" ? A.checks.decision_without_start
      : c === "props_shredded" ? A.checks.props_shredded : c;
    const go = (i: Item) => i.kind === "relation" ? i.from! : i.id;
    const row = (i: Item) => `<li class="aw-item">
        <button type="button" class="aw-go" data-go="${esc(go(i))}">${esc(i.kind === "relation"
          ? `${A.relation(i.type || "")} → ${i.title}` : i.title)}</button>
        <span class="hint">${esc(i.checks.map(why).join(" · "))}</span></li>`;
    box.innerHTML = `<header class="aw-head"><h2>${esc(A.title(res.days || 14))}</h2>
        <span class="hint">${esc(A.count(res.total || 0))}${res.truncated ? ` · ${esc(A.truncated)}` : ""}</span></header>
      ${groups.length ? groups.map((g, gi) => {
        const need = g.items.filter((i) => i.checks.length);
        const pass = g.items.length - need.length;
        return `<section class="aw-group" data-g="${gi}">
          <div class="aw-group-head"><b>${esc(label(g))}</b>
            <span class="hint">${esc(A.groupCount(g.items.length, need.length))}</span>
            ${need.length ? "" : `<button class="btn" type="button" data-keep="${gi}">${esc(A.keepAll)}</button>`}</div>
          ${need.length ? `<ul class="aw-list">${need.map(row).join("")}</ul>` : ""}
          ${pass && need.length ? `<p class="hint">${esc(A.passing(pass))}</p>` : ""}
        </section>`; }).join("")
      : `<p class="hint">${esc(A.none)}</p>`}`;
    box.querySelectorAll<HTMLElement>("[data-go]").forEach((b) => b.addEventListener("click", () => {
      location.hash = `#view/trace/${encodeURIComponent(b.dataset.go!)}`;
    }));
    box.querySelectorAll<HTMLElement>("[data-keep]").forEach((b) => b.addEventListener("click", () => {
      const g = groups[Number(b.dataset.keep)];
      if (g) { keep(scope, g.source.key, latest(g)); paint(); }
    }));
  };
  paint();
}
