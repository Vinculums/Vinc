/** 승인함의 판정. 화면(`surfaces/approvals.ts`)이 쓰는 순수 함수만 둔다.
 *
 *  승인 하나는 사람의 답을 기다리는 실행 하나다. 허브가 주는 목록(`GET /runs/waiting`)을 그대로
 *  믿되 모양만 다듬고, 답에는 목록이 준 `ask`(사람이 본 질문 번호)를 **고치지 않고** 돌려보낸다.
 *  허브는 그 번호가 지금 열린 질문일 때만 답을 받으므로, 이 번호를 새로 셈하면 다른 질문에 답하게 된다.
 *  계획: `vinc.plan.agent-approvals-in-the-vinc-app-2026-11-07`, 실행 프로토콜: [[vinc.canon.mcp-contract]]
 *
 *  import 가 없다: 노드 테스트가 이 파일을 그대로 불러 쓴다(`tests/approvals.test.mjs`). */

export type RunRequest = { action: string; reason: string; kind: string };
export type RunDecision = {
  kind: string; question: string; actions: string[]; requests: RunRequest[];
  asked_at: string; expires_at: string;
};
export type WaitingRun = {
  id: string; run_id: string; role: string; space: string; title: string; task: string;
  host: string; step: number; ask: number; asked_at: string; expires_at: string;
  expired: boolean; decision: RunDecision;
};
export type Answer = "approve" | "decline" | "reply";

/** 허브의 답 글자 상한(`run_protocol.ANSWER_TEXT_MAX`). 넘기면 허브가 `invalid_text` 로 거절한다. */
export const ANSWER_TEXT_MAX = 2000;

const str = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));

/** 목록 응답 하나를 화면이 쓰는 모양으로. id 나 질문 번호가 없는 항목은 답할 수 없으므로 뺀다. */
export function normalizeRuns(res: unknown): WaitingRun[] {
  const rows = (res as { runs?: unknown })?.runs;
  if (!Array.isArray(rows)) return [];
  const out: WaitingRun[] = [];
  for (const raw of rows) {
    if (!raw || typeof raw !== "object") continue;
    const r = raw as Record<string, unknown>;
    const d = (r.decision && typeof r.decision === "object" ? r.decision : {}) as Record<string, unknown>;
    const ask = Number(r.ask);
    const id = str(r.id);
    if (!id || !Number.isInteger(ask) || ask < 1) continue;
    const requests = Array.isArray(d.requests) ? d.requests : [];
    out.push({
      id, run_id: str(r.run_id), role: str(r.role), space: str(r.space), title: str(r.title),
      task: str(r.task), host: str(r.host), step: Number(r.step) || 0, ask,
      asked_at: str(r.asked_at || d.asked_at), expires_at: str(r.expires_at || d.expires_at),
      expired: r.expired === true,
      decision: {
        kind: str(d.kind) || "person_only", question: str(d.question),
        actions: Array.isArray(d.actions) ? d.actions.map(str) : [],
        requests: requests.filter((q) => q && typeof q === "object").map((q) => {
          const x = q as Record<string, unknown>;
          return { action: str(x.action), reason: str(x.reason), kind: str(x.kind) };
        }),
        asked_at: str(d.asked_at), expires_at: str(d.expires_at),
      },
    });
  }
  return out;
}

/** 지금 만료됐는가. 허브의 표식이 먼저고, 화면을 연 채 시간이 지난 것은 만료 시각으로 본다. */
export function isExpired(run: WaitingRun, now: number): boolean {
  if (run.expired) return true;
  const at = Date.parse(run.expires_at);
  return Number.isFinite(at) && at <= now;
}

/** 아직 답할 수 있는 대기의 수. 메뉴의 숫자다. */
export function openCount(runs: WaitingRun[], now: number): number {
  return runs.filter((r) => !isExpired(r, now)).length;
}

/** 만료까지 남은 분(올림). 만료 시각이 없거나 지났으면 null. */
export function minutesLeft(run: WaitingRun, now: number): number | null {
  const at = Date.parse(run.expires_at);
  if (!Number.isFinite(at) || at <= now) return null;
  return Math.max(1, Math.ceil((at - now) / 60_000));
}

/** 허브에 보낼 답. `run` 은 목록의 id, `ask` 는 목록이 준 번호 그대로다. 글은 앞뒤 공백을 걷는다. */
export function answerBody(run: WaitingRun, answer: Answer, text: string):
    { run: string; ask: number; answer: Answer; text?: string } {
  const t = text.trim();
  return { run: run.id, ask: run.ask, answer, ...(t ? { text: t } : {}) };
}

/** 이 글로 이 답을 보낼 수 있는가. 글로만 답하는 것(reply)은 글이 있어야 한다. */
export function canSend(answer: Answer, text: string): boolean {
  const t = text.trim();
  if (t.length > ANSWER_TEXT_MAX) return false;
  return answer !== "reply" || t.length > 0;
}

/** 역할 id 의 표시 이름. `concept:` 접두는 사람에게 뜻이 없다. */
export function roleLabel(role: string): string {
  return role.replace(/^concept:/, "");
}

/** 승인함 주소의 해시(`#approvals` · `#approvals/<run>`). */
export function approvalHash(run = ""): string {
  return run ? `#approvals/${encodeURIComponent(run)}` : "#approvals";
}

/** 해시에서 고른 실행 id. 목록이면 "". */
export function runOfHash(hash: string): string {
  const m = /^#approvals\/(.+)$/.exec(hash);
  if (!m) return "";
  try { return decodeURIComponent(m[1]!); } catch { return ""; }
}

/** 밖에서 온 경로형 딥링크(`/app/approvals/<run>`, 알림이 여는 주소)를 해시로 옮길 자리.
 *  앱은 해시로 라우팅하므로, 그 주소로 열리면 `<base>#approvals/<run>` 으로 바꿔 쓴다.
 *  해당 없으면 null. `base` 는 빌드의 기준 경로(웹 `/app/`, 셸 `/`). */
export function deepLinkTarget(pathname: string, base: string): { path: string; hash: string } | null {
  const root = base.endsWith("/") ? base : base + "/";
  if (!pathname.startsWith(root + "approvals")) return null;
  const rest = pathname.slice((root + "approvals").length);
  if (rest !== "" && rest !== "/" && !rest.startsWith("/")) return null;
  let run = "";
  try { run = decodeURIComponent(rest.replace(/^\/+|\/+$/g, "")); } catch { run = ""; }
  return { path: root, hash: approvalHash(run) };
}

/** 사람에게 보여 줄 경로형 주소(복사·공유용). 웹 기준 경로 아래다. */
export function deepLinkPath(base: string, run: string): string {
  const root = base.endsWith("/") ? base : base + "/";
  return `${root}approvals/${encodeURIComponent(run)}`;
}
