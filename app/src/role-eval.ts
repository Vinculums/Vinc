/** 역할 편집 면 옆 칸의 평가 카드. 저장된 카드(`GET /eval-cards`)를 읽어 그대로 보여 준다.
 *
 *  수는 다시 계산하지 않는다 - 카드를 만든 사람(`vinc eval run`)이 잰 값이다. 서로 다른 묶음 해시나
 *  저장소의 카드는 한 줄로 섞지 않고 카드마다 따로 둔다. 새 카드에 대체된 옛 카드는 숫자만 알린다.
 *  근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]] */
import { COPY } from "./copy";
import { esc } from "./components";

type Rate = { k?: number; n?: number; rate?: number } | null | undefined;
type Cell = {
  adapter?: string; model_family?: string; runs_per_task?: number;
  pass_1?: Rate; pass_k?: Rate; violation?: Rate; fabrication?: Rate;
  cost_per_success_usd?: number | null; ungraded?: number; skipped?: number;
};
export type EvalCard = {
  id: string; date?: string; current?: boolean;
  suite?: { id?: string; tasks?: number }; results?: Cell[];
};

/** 공유 버전 스냅샷에 실린 카드의 칸. 해시가 두 언어에서 같도록 정수만 담는다:
 *  비율은 `{k, n}`, 비용은 마이크로달러다(api/community_agents.py `_eval_cards`). */
type SharedRate = { k?: number; n?: number } | null | undefined;
type SharedCell = Omit<Cell, "pass_1" | "pass_k" | "violation" | "fabrication" | "cost_per_success_usd"> & {
  pass_1?: SharedRate; pass_k?: SharedRate; violation?: SharedRate; fabrication?: SharedRate;
  cost_per_success_microusd?: number | null;
};
type SharedCard = { id: string; date?: string; suite?: { id?: string; tasks?: number }; results?: SharedCell[] };

/** 보여 줄 현재 카드 수. 더 있으면 최신순 앞쪽만 둔다. */
const SHOWN = 3;

const pct = (r: Rate): string | null =>
  r && typeof r.rate === "number" ? `${Math.round(r.rate * 100)}%` : null;
const detail = (r: Rate): string =>
  r && typeof r.k === "number" && typeof r.n === "number" ? `${r.k}/${r.n}` : "";

function cellHtml(cell: Cell): string {
  const R = COPY.author.role;
  const rows: string[] = [];
  const row = (label: string, value: string | null, title = "") => {
    if (value === null) return;
    rows.push(`<dt${title ? ` title="${esc(title)}"` : ""}>${esc(label)}</dt><dd${title ? ` title="${esc(title)}"` : ""}>${esc(value)}</dd>`);
  };
  row(R.evalMetrics.pass_1, pct(cell.pass_1), detail(cell.pass_1));
  row(R.evalMetrics.pass_k, pct(cell.pass_k), [R.evalAlways(cell.runs_per_task ?? 0), detail(cell.pass_k)].join(" · "));
  row(R.evalMetrics.violation, pct(cell.violation), detail(cell.violation));
  row(R.evalMetrics.fabrication, pct(cell.fabrication), detail(cell.fabrication));
  const cost = cell.cost_per_success_usd;
  row(R.evalMetrics.cost, typeof cost === "number" ? `$${cost.toFixed(cost < 1 ? 3 : 2)}` : null);
  const notes = [
    cell.skipped ? R.evalSkipped(cell.skipped) : "",
    cell.ungraded ? R.evalUngraded(cell.ungraded) : "",
  ].filter(Boolean);
  return `<div class="eval-cell">
      <p class="eval-cell-name">${esc(R.evalCell(cell.adapter || "", cell.model_family || ""))}</p>
      <dl class="eval-metrics">${rows.join("")}</dl>
      ${notes.map((n) => `<p class="eval-note">${esc(n)}</p>`).join("")}
    </div>`;
}

/** 역할 id(`concept:agent-dev-core`)의 짧은 이름. 명령에 그대로 칠 수 있다. */
const shortName = (id: string): string => id.replace(/^concept:/, "").replace(/^agent-/, "");

const sharedRate = (r: SharedRate): Rate =>
  r && typeof r.k === "number" && typeof r.n === "number" ? { k: r.k, n: r.n, rate: r.n ? r.k / r.n : undefined } : null;

function sharedCell(cell: SharedCell): Cell {
  const micro = cell.cost_per_success_microusd;
  return { ...cell, pass_1: sharedRate(cell.pass_1), pass_k: sharedRate(cell.pass_k),
           violation: sharedRate(cell.violation), fabrication: sharedRate(cell.fabrication),
           cost_per_success_usd: typeof micro === "number" ? micro / 1_000_000 : null };
}

/** 커뮤니티에서 가져간 역할이 공유 버전과 함께 받은 카드. 작성자가 잰 값이고 여기서 다시 돌린 것이 아니다.
 *  가져간 역할은 켜기 전(`status: proposed`)에도 이 칸이 보인다. 카드가 없으면 빈 문자열이다. */
export function sharedEvalCardsHtml(props: Record<string, unknown>): string {
  const R = COPY.author.role;
  const snapshot = props.shared_snapshot as { evalCards?: SharedCard[] } | undefined;
  const source = props.community_source as { version?: number; author?: { handle?: string | null } } | undefined;
  const cards = Array.isArray(snapshot?.evalCards) ? snapshot!.evalCards : [];
  if (!cards.length) return "";
  const body = cards.slice(0, SHOWN).map((c) => `<article class="eval-card">
      <p class="eval-card-suite">${esc(R.evalSuite(c.suite?.id || c.id, c.suite?.tasks ?? 0))}${
        c.date ? ` · ${esc(c.date.slice(0, 10))}` : ""}</p>
      ${(c.results || []).map((cell) => cellHtml(sharedCell(cell))).join("")}
    </article>`).join("");
  return `<p class="eval-note">${esc(R.evalShared(source?.version ?? 0, source?.author?.handle || ""))}</p>${body}`;
}

export function evalCardsHtml(cards: EvalCard[], roleId: string): string {
  const R = COPY.author.role;
  const current = cards.filter((c) => c.current !== false);
  const replaced = cards.length - current.length;
  const run = `<p class="eval-run eval-note">${esc(R.evalRun)}<code>vinc eval run ${esc(shortName(roleId))} --suite FILE --model PROVIDER:MODEL</code></p>`;
  if (!current.length) return `<p class="eval-note">${esc(R.evalEmpty)}</p>${run}`;
  const body = current.slice(0, SHOWN).map((c) => `<article class="eval-card">
      <p class="eval-card-suite">${esc(R.evalSuite(c.suite?.id || c.id, c.suite?.tasks ?? 0))}${
        c.date ? ` · ${esc(c.date.slice(0, 10))}` : ""}</p>
      ${(c.results || []).map(cellHtml).join("")}
    </article>`).join("");
  return `${body}${replaced > 0 ? `<p class="eval-note">${esc(R.evalReplaced(replaced))}</p>` : ""}${run}`;
}
