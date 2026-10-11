import type { GraphNode, GraphEdge } from "./api";
import type { LayoutGraph } from "./view-layout";

export type CachedGraph = { executed: boolean; nodes?: GraphNode[]; edges?: GraphEdge[];
  topicEdges?: GraphEdge[]; partial?: boolean; snapshot?: unknown; graphSnapshotPending?: boolean };
export type ViewBookmark = { domains: string[] };
export type ViewCameraState = { prefs: string; camera2?: unknown; camera3?: unknown };
type Entry = { revision: string; raw: string; layoutKey?: string; layout?: string;
  state?: string; size: number; expires: number };

/** 완료된 DTO만 보관한다. 렌더러가 좌표·endpoint를 바꿔도 다음 마운트에 전파되지 않는다.
 * 권한 있는 현재 scope/identity와 변경 표식 확인은 호출자가 매 재진입에서 수행한다.
 */
export class ViewSessionCache {
  private entries = new Map<string, Entry>();
  private session = "";
  private revision = "";
  private bookmark: string | undefined;
  private bookmarkExpires = 0;
  private epoch = 0;
  constructor(private maxBytes = 32 * 1024 * 1024, private maxEntries = 4,
    private ttl = 300_000, private clock = () => Date.now()) {}
  get generation() { return this.epoch; }
  get count() { this.prune(); return this.entries.size; }
  get hasBookmark() { this.prune(); return !!this.bookmark; }
  get bytes() { return [...this.entries.values()].reduce((n, e) => n + e.size, (this.bookmark?.length || 0) * 2); }
  clear(): void {
    this.entries.clear(); this.session = ""; this.revision = ""; this.bookmark = undefined; ++this.epoch;
  }
  activate(session: string, revision: string): number {
    if (session !== this.session) { this.clear(); this.session = session; }
    if (revision !== this.revision) { this.entries.clear(); this.revision = revision; ++this.epoch; }
    return this.epoch;
  }
  private prune() {
    for (const [key, e] of this.entries) if (e.expires <= this.clock()) this.entries.delete(key);
    if (this.bookmarkExpires <= this.clock()) this.bookmark = undefined;
  }
  private current(session: string, revision: string) {
    this.prune(); return !!session && !!revision && session === this.session && revision === this.revision;
  }
  get(session: string, key: string, revision: string): CachedGraph | undefined {
    if (!this.current(session, revision)) return;
    const e = this.entries.get(key); if (!e) return;
    this.entries.delete(key); this.entries.set(key, e);
    return JSON.parse(e.raw);
  }
  getLayout(session: string, key: string, revision: string, layoutKey: string): LayoutGraph | undefined {
    if (!this.current(session, revision)) return;
    const e = this.entries.get(key);
    return e?.layoutKey === layoutKey && e.layout ? JSON.parse(e.layout) : undefined;
  }
  getState(session: string, key: string, revision: string, prefs: string): ViewCameraState | undefined {
    if (!this.current(session, revision)) return;
    const e = this.entries.get(key), state: ViewCameraState | undefined = e?.state && JSON.parse(e.state);
    return state?.prefs === prefs ? state : undefined;
  }
  put(session: string, key: string, revision: string, graph: CachedGraph, generation: number,
      layout?: { key: string; graph: LayoutGraph }): boolean {
    if (!this.current(session, revision) || generation !== this.epoch || !graph.executed
        || graph.partial || graph.snapshot || graph.graphSnapshotPending
        || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) return false;
    const raw = JSON.stringify(graph), shaped = layout && JSON.stringify(layout.graph);
    // UTF-16 strings are the retained graph representation; include key/state and fixed entry overhead.
    const previous = this.entries.get(key);
    const size = (raw.length + (shaped?.length || 0) + key.length + session.length
      + (previous?.state?.length || 0)) * 2 + 1024;
    if (size + (this.bookmark?.length || 0) * 2 > this.maxBytes || key.length > 16_384) return false;
    this.entries.delete(key);
    while (this.entries.size && (this.entries.size >= this.maxEntries || this.bytes + size > this.maxBytes))
      this.entries.delete(this.entries.keys().next().value!);
    this.entries.set(key, { revision, raw, layoutKey: layout?.key, layout: shaped,
      state: previous?.state, size,
      expires: this.clock() + this.ttl });
    return true;
  }
  remember(session: string, key: string, revision: string, state: ViewCameraState, domains: string[]) {
    if (!this.current(session, revision)) return;
    const e = this.entries.get(key); if (!e) return;
    const next = JSON.stringify(state), bookmark = JSON.stringify({ domains });
    if (next.length + bookmark.length > 8192) return;
    const delta = (next.length - (e.state?.length || 0)) * 2;
    if (this.bytes + delta + (bookmark.length - (this.bookmark?.length || 0)) * 2 > this.maxBytes) return;
    e.size += delta; e.state = next; this.bookmark = bookmark; this.bookmarkExpires = this.clock() + this.ttl;
  }
  last(session: string): ViewBookmark | undefined {
    this.prune();
    return session === this.session && this.bookmark ? JSON.parse(this.bookmark) : undefined;
  }
}

export const viewSessionCache = new ViewSessionCache();
if (typeof window !== "undefined") {
  for (const event of ["vinc:signin-required", "vinc:write", "pagehide"])
    window.addEventListener(event, () => viewSessionCache.clear());
  window.addEventListener("storage", event => {
    if (!event.key || /^vinc\.(owner|team|mode|sidecar\.|account)/.test(event.key)) viewSessionCache.clear();
  });
}
