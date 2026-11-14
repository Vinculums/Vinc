import type { GraphNode, GraphEdge } from "./api";

type CachedGraph = { executed: boolean; nodes?: GraphNode[]; edges?: GraphEdge[];
  topicEdges?: GraphEdge[] };
type ViewBookmark = { domains: string[] };
export type Entry = { revision: string; raw: string; size: number; expires: number };
/** 한 세션이 들고 있는 완료 DTO 묶음. 스페이스를 떠날 때 잠깐 보관하려고 내보낸다. */
export type RetainedSnapshot = { session: string; revision: string; entries: [string, Entry][] };

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
  private purgeListeners: (() => void)[] = [];
  clear(): void {
    this.entries.clear(); this.session = ""; this.revision = ""; this.bookmark = undefined; ++this.epoch;
  }
  /** 메모리만이 아니라 보관해 둔 것까지 버린다: 로그인이 풀렸거나 계정이 바뀌었다. */
  purge(): void { this.clear(); for (const fn of this.purgeListeners) fn(); }
  onPurge(fn: () => void): void { this.purgeListeners.push(fn); }
  /** 지금 세션의 살아 있는 항목. 없으면 undefined. 책갈피는 포함하지 않는다. */
  snapshot(): RetainedSnapshot | undefined {
    this.prune();
    if (!this.session || !this.revision || !this.entries.size) return;
    return { session: this.session, revision: this.revision,
      entries: [...this.entries].filter(([, e]) => e.revision === this.revision) };
  }
  /** 보관해 둔 항목을 되돌린다. 세션·표식은 호출자가 방금 새로 확인한 값이어야 한다.
   *  이미 같은 세션에 항목이 있으면 건드리지 않는다. 되돌린 개수를 돌려준다. */
  restore(session: string, revision: string, entries: [string, Entry][], expires: number): number {
    if (!session || !revision) return 0;
    this.activate(session, revision);
    if (this.entries.size) return 0;
    let n = 0;
    for (const [key, e] of entries) {
      if (e.revision !== revision || key.length > 16_384 || this.entries.size >= this.maxEntries) continue;
      if (this.bytes + e.size > this.maxBytes) continue;
      // 되돌린 항목의 수명은 메모리 항목과 같이 짧게 둔다. 신선도는 표식이 정하지만 표식이
      // 세지 않는 변화가 있으므로 오래 들고 있을 이유가 없다.
      this.entries.set(key, { ...e, expires: Math.min(expires, this.clock() + this.ttl) }); n++;
    }
    return n;
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
  put(session: string, key: string, revision: string, graph: CachedGraph, generation: number): boolean {
    if (!this.current(session, revision) || generation !== this.epoch || !graph.executed
        || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) return false;
    const raw = JSON.stringify(graph);
    // UTF-16 strings are the retained graph representation; include the key and fixed entry overhead.
    const size = (raw.length + key.length + session.length) * 2 + 1024;
    if (size + (this.bookmark?.length || 0) * 2 > this.maxBytes || key.length > 16_384) return false;
    this.entries.delete(key);
    while (this.entries.size && (this.entries.size >= this.maxEntries || this.bytes + size > this.maxBytes))
      this.entries.delete(this.entries.keys().next().value!);
    this.entries.set(key, { revision, raw, size, expires: this.clock() + this.ttl });
    return true;
  }
  /** 떠나는 마운트가 보던 범위(주제)를 책갈피로 남긴다. 그 범위의 항목이 이 세션·표식에 보관돼 있을 때만 남긴다. */
  remember(session: string, key: string, revision: string, domains: string[]) {
    if (!this.current(session, revision) || !this.entries.has(key)) return;
    const bookmark = JSON.stringify({ domains });
    if (bookmark.length > 8192) return;
    if (this.bytes + (bookmark.length - (this.bookmark?.length || 0)) * 2 > this.maxBytes) return;
    this.bookmark = bookmark; this.bookmarkExpires = this.clock() + this.ttl;
  }
  last(session: string): ViewBookmark | undefined {
    this.prune();
    return session === this.session && this.bookmark ? JSON.parse(this.bookmark) : undefined;
  }
}

export const viewSessionCache = new ViewSessionCache();
if (typeof window !== "undefined") {
  // 쓰기와 페이지 이탈은 메모리만 비운다. 보관본은 표식으로 다시 확인하고 쓰므로 남긴다.
  for (const event of ["vinc:write", "pagehide"])
    window.addEventListener(event, () => viewSessionCache.clear());
  window.addEventListener("vinc:signin-required", () => viewSessionCache.purge());
  window.addEventListener("storage", event => {
    // 스페이스 전환은 다른 탭에서도 일어난다: 그 탭의 보관본을 지우지 않는다(메모리만 비운다).
    if (event.key === "vinc.team") viewSessionCache.clear();
    // 로그아웃은 `vinc.site.signedin` 으로 알려진다(계정 페이지가 쓴다).
    else if (!event.key || /^vinc\.(owner|mode|sidecar\.|account|site\.signedin)/.test(event.key)) viewSessionCache.purge();
  });
}
