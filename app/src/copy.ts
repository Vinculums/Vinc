/** English copy deck — SSoT: Vinc-docs vinc/D surface specs (DEC D18).
 *  Voice: precise, warm, craftsmanlike. Verb-first CTAs. No learning/memory
 *  narrative (F2) — approved verbs: mine, map, trace, surface, bind, connect. */
export const COPY = {
  tagline: "Mine what matters.",
  sovereignty: "Vinc runs entirely on this machine — nothing leaves.",
  intake: {
    idleTitle: "Bring your corpus in.",
    idleBody: "Drop files or folders here, or choose sources.",
    idleCta: "Choose sources",
    loading: (n: number, m: number, d: number) => `Ingesting ${n} of ${m} — ${d} domains so far.`,
    success: (n: number, m: number) => `${n} records across ${m} domains.`,
    successCta: "Open in Atlas",
    error: (src: string) => `Couldn't read ${src}. Check the file and try again — everything already ingested is safe.`,
    retryCta: "Try again",
  },
  records: {
    idle: "No records yet.",
    idleCta: "Ingest sources",
    header: (n: number, m: number) => `${n} records · ${m} domains`,
    error: "Records couldn't load. Retry — nothing was changed.",
    actions: { atlas: "Show in Atlas", pin: "Pin", tag: "Tag" },
  },
  atlas: {
    idle: "Nothing to map yet. Ingest a source or run a search — the atlas draws itself from your records.",
    idleCta: "Ingest sources",
    loading: (d: number) => `Laying out ${d} domains…`,
    domainTip: (dom: string, n: number, k: number) =>
      `${dom}: ${n} records · ${k} ties to other domains. Click to open the lens.`,
    gatewayTip: (dom: string) => `Crosses into ${dom} — click to follow.`,
    error: "The atlas couldn't load. Retry — your records are untouched.",
    retryCta: "Retry",
  },
  search: {
    idle: "Search across everything you've ingested — by meaning or by keyword.",
    empty: (q: string) => `No matches for '${q}'. Try broader terms, or browse the Atlas instead.`,
    emptyCta: "Open Atlas",
    matches: (n: number) => `${n} matches`,
    error: "Search couldn't run. Retry — the index is intact.",
    actions: { atlas: "Show in Atlas", open: "Open" },
  },
  packs: {
    idle: "Bundle records into a pack you can carry anywhere — or hand a slice of your data to someone else.",
    idleCta: "Create a pack",
    shareCta: "Share a slice",
    importCta: "Import a pack",
    loading: (n: number) => `Packing ${n} records…`,
    importing: "Importing…",
    success: (n: number, size: string) => `Pack ready — ${n} records, ${size}.`,
    shareReady: (c: number, e: number) => `Share pack ready — ${c} concepts, ${e} connections.`,
    shareNote: "Preference signals stay with you — the pack carries content only.",
    imported: (created: number, matched: number) =>
      `Imported — ${created} new, ${matched} already here.`,
    successCta: "Reveal in folder",
    error: "The pack didn't finish. Try again — your records are untouched.",
  },
  settings: {
    lock: "Lock Vinc with a passphrase. Optional — your data stays local either way.",
    mcp: "Tool calls from connected clients appear here, as they happen.",
    data: "Everything Vinc knows lives in this folder.",
    dataCta: "Back up now",
    token: "Session token for the local sidecar (dev scaffold).",
  },
} as const;
