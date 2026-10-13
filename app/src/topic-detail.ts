import type { NodeContext, TopicDetail } from "./api";
import type { COPY } from "./copy";

export type DetailState = "ready" | "failed" | "unavailable" | "unsupported";
export function detailState(res: NodeContext): DetailState {
  if (res.status === "unavailable") return "unavailable";
  if (res.status === "unsupported_kind") return "unsupported";
  if (res.error || res.status === "query_failed") return "failed";
  return res.executed ? "ready" : "failed";
}
export function detailFailure(error: unknown): DetailState {
  const status = Number((error as { status?: number } | null)?.status);
  return [401, 403, 404].includes(status) ? "unavailable" : "failed";
}

type Copy = typeof COPY.ui.viewx;
export function detailMessage(state: DetailState, copy: Copy): string {
  return state === "unavailable" ? copy.detailUnavailable
    : state === "unsupported" ? copy.detailUnsupported : copy.detailFail;
}

export function topicDetailHTML(topic: TopicDetail, copy: Copy, esc: (s: string) => string): string {
  const go = (id: string, title: string, domain: string) =>
    `<button type="button" class="link-row" data-topic-go="${esc(id)}" data-dom="${esc(domain)}">
      <span class="link-title">${esc(title)}</span></button>`;
  const row = (label: string, count: number) =>
    `<div class="stat-row"><span class="hint">${esc(label)}</span><b>${count}</b></div>`;
  const path = topic.path.split("/").map((part, i, parts) => {
    const domain = parts.slice(0, i + 1).join("/");
    return `<button type="button" class="chip" data-topic-go="${esc("domain:" + domain)}"
      data-dom="${esc(domain)}">${esc(part)}</button>`;
  }).join(" / ");
  const group = (rel: "PARENT_OF" | "IN_DOMAIN", title: string, count: number) => {
    const items = topic.structure.items.filter(it => it.rel === rel);
    return `<section class="pane-links"><h3 class="hint rail-sec">${esc(title)} · ${count}</h3>`
      + items.map(it => go(it.id, it.title, it.rel === "PARENT_OF" ? it.id.slice(7) : it.domain)).join("")
      + (items.length ? "" : `<p class="card-note">${esc(copy.topicEmpty)}</p>`) + "</section>";
  };
  return `<p class="hint">${esc(copy.topicPath)}</p><p>${path}</p>
    <section data-topic-description></section>
    ${row(copy.topicDirect, topic.direct_nodes)}${row(copy.topicDescendants, topic.descendant_nodes)}
    ${row(copy.topicSubtree, topic.subtree_nodes)}
    <h3 class="hint rail-sec">${esc(copy.topicStructure)}</h3>
    <section class="pane-links"><h3 class="hint rail-sec">${esc(copy.topicParent)}</h3>
    ${topic.parent ? go(topic.parent.id, topic.parent.title, topic.parent.domain)
      : `<p class="card-note">${esc(copy.topicRoot)}</p>`}</section>
    ${group("PARENT_OF", copy.topicChildren, topic.child_topics)}
    ${group("IN_DOMAIN", copy.topicMembers, topic.direct_nodes)}
    ${topic.structure.offset > 0 ? `<button type="button" class="btn" data-topic-more="${Math.max(0, topic.structure.offset - 24)}">
      ${esc(copy.topicPrevious)}</button>` : ""}
    ${topic.structure.truncated ? `<button type="button" class="btn" data-topic-more="${topic.structure.next_offset}">
      ${esc(copy.relsMore(topic.structure.total - topic.structure.offset - topic.structure.items.length))}</button>` : ""}
    ${topic.consistency ? `<p class="hint" role="status">${esc(copy.topicDrift)}</p>` : ""}
    <p class="hint">${esc(copy.topicRelations)}</p>`;
}
