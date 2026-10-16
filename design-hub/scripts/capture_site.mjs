// 공홈 캡처 파이프라인. 실행 중인 사이트(라이브 URL 또는 로컬 빌드)를 상태별로 PNG 로 남기고
// 매니페스트를 쓴다. Figma 의 `Site · *` 보드는 전부 이 PNG 에 대고 그린다(코드가 원천).
//
//   node scripts/capture_site.mjs                 # 전부
//   node scripts/capture_site.mjs --only account  # id 에 부분일치하는 것만
//   node scripts/capture_site.mjs --list          # 상태 목록만
//   --base https://vincs.io   --out reports/captures/site
//
// 규칙:
// - 로그인·오류 상태는 `/api/*` 를 픽스처로 돌려 만든다(실제 계정·실제 쓰기 없음). 픽스처의
//   필드 이름은 페이지 JS 가 읽는 그대로다. 근거: [[vinc.spec.figma-coverage-matrix]].
// - 모든 컨텍스트는 reducedMotion:'reduce' 다. 사이트가 이를 존중해 스크롤 리빌·캐러셀·터미널
//   타이핑을 건너뛰므로 섹션이 전부 보이는 정지 화면이 나온다.
// - 네이티브 alert/confirm 은 그릴 수 없으니 문구를 매니페스트 `dialogs` 에 남긴다.
// - 매니페스트는 인벤토리 게이트의 기대 집합이다(vinc.plan.figma-product-structure §8 D7).
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const HUB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const argv = process.argv.slice(2);
const flag = (name, dflt) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : dflt; };
const BASE = (flag("--base", "https://vincs.io")).replace(/\/$/, "");
const OUT = path.resolve(HUB, flag("--out", "reports/captures/site"));
const ONLY = flag("--only", null);
const LIST = argv.includes("--list");
const T = new Date().toISOString();

// ── 픽스처 ─────────────────────────────────────────────────────────────────
const ISO = "2026-08-01T09:03:00.000Z";
const user = (over = {}) => ({ user: { email: "ada@example.com", username: "ada", name: "Ada Lovelace", plan: "free", teams: [], newsletter: true, created_at: ISO, ...over } });
const ME = user();
const ME_NOHANDLE = user({ username: null, name: null });
const ME_PRO = user({ plan: "pro" });
const TEAM_ROW = { id: "018f0000-0000-7000-8000-000000000001", name: "Vinc Core", role: "admin", status: "active" };
const ME_TEAM = user({ teams: [TEAM_ROW, { ...TEAM_ROW, id: "018f0000-0000-7000-8000-000000000002", name: "Design Guild", role: "viewer" }] });
const BILLING = {
  trial: { plan: "free", trial: { open: true, until: "2026-12-31T23:59:59.000Z" }, checkout: true, subscriptions: [] },
  checkout: { plan: "free", trial: { open: false }, checkout: true, subscriptions: [] },
  none: { plan: "free", trial: { open: false }, checkout: false, subscriptions: [] },
  active: { plan: "pro", trial: { open: false }, checkout: true, subscriptions: [{ plan: "pro", status: "active", provider: "paddle", seats: 1, cancel_at_period_end: false, current_period_end: "2026-10-05T00:00:00.000Z" }] },
  trialing: { plan: "pro", trial: { open: false }, checkout: true, subscriptions: [{ plan: "pro", status: "trialing", provider: "paddle", seats: 1, cancel_at_period_end: false, current_period_end: "2026-09-20T00:00:00.000Z" }] },
  past_due: { plan: "pro", trial: { open: false }, checkout: true, subscriptions: [{ plan: "pro", status: "past_due", provider: "paddle", seats: 1, cancel_at_period_end: false, current_period_end: "2026-09-12T00:00:00.000Z" }] },
};
const KEYS = { keys: [
  { id: 12, prefix: "vinc_sk_a1b2", name: "Claude", scope: "full", created_at: ISO, last_used_at: "2026-09-05T14:02:00.000Z", revoked: false },
  { id: 13, prefix: "vinc_ro_9zz1", name: null, scope: "read", created_at: "2026-08-20T10:00:00.000Z", last_used_at: null, revoked: false },
] };
const FRESH_KEY = { id: 14, prefix: "vinc_sk_c3d4", name: null, scope: "full", key: "vinc_sk_c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8" };
// 커뮤니티(위키 셸) 픽스처. 모양은 Vinc-homepage test/site/community/fixture-server.mjs·agents-fixture.mjs 와 같다.
const C_ID = (n) => `019fa000-0000-7000-8000-${String(n).padStart(12, "0")}`;
const C_WHEN = "2026-10-13T09:00:00.000Z";
const C_SOURCES = [{ title: "W3C Design Tokens Format", url: "https://tr.designtokens.org/format/" }];
const C_REV = { revNo: 2, title: "Design tokens", body: "A design token is a named value that the build reads instead of a hard-coded colour or size.\n\n## Name the role, not the value\nCall it `surface-raised`, not `grey-900`.\n\n> A token that names its value has to be renamed every time the value moves.\n\n## Where the values live\nOne JSON file is the source; CSS and theme files are generated from it. See the [format draft](https://tr.designtokens.org/format/).", summary: "Explain role names", editor: "jade_kim", editorDeleted: false, channel: "web", clientName: "", originThread: null, revertedFrom: null, sources: C_SOURCES, links: [], createdAt: C_WHEN };
const C_PAGE = { publicId: C_ID(1), slug: "design-tokens", title: C_REV.title, isRules: false, locked: false, status: "active", updatedAt: C_WHEN, current: C_REV };
const C_RULES = { ...C_PAGE, publicId: C_ID(2), slug: "rules", title: "Frontend rules", isRules: true, current: { ...C_REV, title: "Frontend rules", body: "## Scope\nExplain how interfaces are built and reviewed, and cite a source for every claim." } };
const C_PAGES = [C_PAGE, C_RULES, { ...C_PAGE, publicId: C_ID(6), slug: "focus-states", title: "Focus states" }, { ...C_PAGE, publicId: C_ID(7), slug: "spacing-scale", title: "Spacing scale" }];
const C_THREAD = { publicId: C_ID(3), title: "Should spacing tokens use rem?", body: "The page says nothing about units. Could we add a short section on rem versus px?", author: "minsu", authorDeleted: false, status: "open", reactionCount: 2, myReaction: false, commentCount: 1, createdAt: C_WHEN, lastActivityAt: C_WHEN, canEdit: false };
const C_POST = { ...C_THREAD, publicId: C_ID(4), title: "What does your token review look like?", body: "We diff the token file in every pull request. Curious how other teams do it.", author: "jade_kim", status: "published", reactionCount: 4 };
const C_COMMENTS = [{ publicId: C_ID(5), body: "rem for spacing, px for borders. I can draft the section.", status: "published", author: "jade_kim", authorDeleted: false, createdAt: C_WHEN, reactionCount: 1, myReaction: false }];
const C_COMMUNITY = { community: { publicId: C_ID(90), slug: "frontend", name: "Frontend", topic: "Building and reviewing web interfaces: tokens, layout, accessibility.", createdAt: C_WHEN, operators: ["jade_kim"] }, myRole: null, canModerate: false, features: { agents: true } };
const C_DIRECTORY = { communities: [{ slug: "frontend", name: "Frontend", topic: C_COMMUNITY.community.topic, createdAt: C_WHEN }, { slug: "backend", name: "Backend", topic: "APIs, storage and the services behind them.", createdAt: C_WHEN }, { slug: "design-systems", name: "Design systems", topic: "Components, tokens and how teams keep them in step.", createdAt: C_WHEN }] };
const C_SHARE_ID = "019fb000-0000-7000-8000-000000000001";
const C_HASH = "a".repeat(64);
const C_SHARE = { publicId: C_SHARE_ID, community: { slug: "frontend", publicId: C_ID(90), name: "Frontend" }, status: "published", author: "jade_kim", authorDeleted: false, latestVersion: 2, origin: { kind: "page", page: "design-tokens", url: "/community/frontend/p/design-tokens" }, canVersion: false, canWithdraw: false };
const C_VERSION = { version: 2, hash: C_HASH, role: { id: "concept:agent-token-reviewer", label: "Token reviewer", domain: "vinc/agent", tags: ["agent", "review", "role"], props: { mission: "Review a pull request's colour and spacing values against the token file.", tier: "review", rung: "strong", never: ["Never approve a hard-coded colour."], person_only: [{ action: "merge", reason: "A person merges." }] } }, prompt: "Read the Frontend rules first.\nOpen the token file and the diff.\nList every value that bypasses a token.", tools: ["vinc_conventions", "vinc_search", "vinc_doc_get"], references: [{ kind: "page", title: "Design tokens · revision 2", url: "/community/frontend/p/design-tokens" }], license: "CC-BY-SA-4.0", parent: null, authors: ["jade_kim"], createdAt: C_WHEN };
const C_AGENT_CARD = { publicId: C_SHARE_ID, title: "Token reviewer", mission: C_VERSION.role.props.mission, author: "jade_kim", latestVersion: 2, license: C_VERSION.license, tools: C_VERSION.tools };
const C_PACK_CARD = { publicId: "019fb000-0000-7000-8000-000000000003", title: "Frontend review notes", description: "Token and focus-state notes from our reviews.", sizeBytes: 48200, author: "ada", status: "published", communitySlug: "frontend", canWithdraw: true, downloadUrl: "/api/community/packs/019fb000-0000-7000-8000-000000000003/download" };
// Take 행선지는 개인 그래프 하나만 둔다(팀 행선지는 사이트에서 걷히는 중이다).
const C_DESTINATIONS = { features: { agents: true }, draftActor: "capture-draft-actor-opaque-0123456789", destinations: [{ kind: "personal", name: "Personal", allowed: true }] };
const dom = (domain, e, c, r, d) => ({ domain, episodes: e, concepts: c, records: r, decisions: d, total: e + c + r + d });
const DOMAINS = { executed: true, rows: [
  dom("vinc/product", 40, 120, 300, 8), dom("vinc/design/tokens", 20, 60, 140, 3), dom("vinc/product/book/intro", 12, 40, 90, 1),
  dom("vinc/product/book/chapters", 10, 35, 80, 1), dom("vinc/ops/deploy", 9, 30, 70, 4), dom("vinc/design/motion", 7, 25, 55, 0),
  dom("vinc/research/graph", 6, 22, 48, 2), dom("vinc/ops/oncall", 5, 18, 40, 1), dom("vinc/research/nlp", 4, 14, 31, 0), dom("vinc", 3, 12, 26, 0),
  dom("vinc/legal", 3, 9, 20, 1), dom("vinc/finance", 2, 7, 15, 0), dom("vinc/marketing", 2, 5, 11, 0), dom("vinc/support", 1, 3, 7, 0),
] };
const TEAM = { id: TEAM_ROW.id, name: "Vinc Core", status: "active", seats: 5, myRole: "admin", mcpBase: "https://mcp.vincs.io",
  members: [{ user: "018f-u1", email: "ada@example.com", role: "admin" }, { user: "018f-u2", email: "bob@example.com", role: "editor" }, { user: "018f-u3", email: "cara@example.com", role: "viewer" }],
  invites: [{ id: 31, email: "dan@example.com", role: "viewer", status: "sent" }, { id: 32, email: "eve@example.com", role: "editor", status: "failed" }] };
const TEAM_VIEWER = { ...TEAM, myRole: "viewer" };
const TEAM_NOMCP = { ...TEAM, mcpBase: "" };
const NODES = { nodes: Array.from({ length: 9 }, (_, i) => ({ id: `n${i + 1}`, label: ["Retrieval", "Reranking", "Chunking", "Embeddings", "Evaluation set", "Query rewriting", "Hybrid search", "Recall at k", "Cross-encoder"][i], kind: i % 3 ? "Concept" : "Record" })) };
const NODE_CTX = { node_kind: "Concept", one_liner: "Reordering a candidate list with a model that reads query and passage together.", excerpts: [
  { doc_title: "Reranking notes", excerpt: "A cross-encoder scores each (query, passage) pair jointly, which is slower but sharper than a bi-encoder." },
  { doc_title: "Evaluation 2026-09-04", excerpt: "Cross-language queries gained the most: ko to en +0.251 nDCG." },
  { doc_title: "Plan 0.9.0", excerpt: "Rerank only the top 50 hybrid hits; latency budget 1.5 s." }],
  links: { items: [{ title: "Hybrid search", id: "n7", domain: "vinc/research/graph" }, { title: "Evaluation set", id: "n5", domain: "vinc/research/graph" }, { title: "Embeddings", id: "n4", domain: "vinc/research/nlp" }] } };
const ERR = (code, message, status) => ({ status, json: { error: { code, message, request_id: "req_capture" } } });
const E401 = ERR("not_authenticated", "Sign in to continue.", 401);
const E500 = ERR("internal_error", "Something went wrong.", 500);

// ── 라우팅 ─────────────────────────────────────────────────────────────────
// table: [method, pathPattern(RegExp|string), responder]. responder: {status,json,delay} | (url, request) => same | "abort"
const ok = (json, delay = 0, status = 200) => ({ status, json, delay });
const baseApi = (me = ME) => [["GET", "/api/me", me ? ok(me) : E401]];
async function install(page, table) {
  await page.route(/\/api\/|127\.0\.0\.1:\d+\/connect\/callback/, async (route) => {
    const req = route.request(); const url = new URL(req.url()); const method = req.method();
    for (const [m, p, responder] of table) {
      const hit = (m === "*" || m === method) && (p instanceof RegExp ? p.test(url.pathname + url.search) : url.pathname === p);
      if (!hit) continue;
      let r = typeof responder === "function" ? responder(url, req) : responder;
      if (r === "abort") return route.abort("connectionrefused");
      if (r === "hang") return; // never answers: keeps a "busy" state on screen
      if (r.delay) await new Promise((res) => setTimeout(res, r.delay));
      return route.fulfill({ status: r.status ?? 200, contentType: "application/json", body: JSON.stringify(r.json ?? {}) });
    }
    if (url.pathname.startsWith("/api/")) return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "not_found", message: url.pathname } }) });
    return route.continue();
  });
}

// ── 상태 목록 ───────────────────────────────────────────────────────────────
// { id, url, route?, trigger, api, ls, vp, ua, scheme, full, before(page) }   route: 매니페스트 행의 라우트(생략 시 url 의 경로)
const S = [];
const add = (o) => S.push({ full: true, ls: {}, api: [], ...o });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const MOBILE = { width: 390, height: 844 };
const UA = {
  mac: "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
  win: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
  linux: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
  other: "Mozilla/5.0 (Unknown; rv:1.0) Gecko/20100101 Firefox/1.0",
};

// landing
add({ id: "landing__default", url: "/", trigger: "load, signed out", api: baseApi(null) });
add({ id: "landing__signed-in", url: "/", trigger: "load with a session: GNB shows Account and My graph, hero CTA relabels", api: baseApi(ME), ls: { "vinc.site.signedin": "1" } });
add({ id: "landing__light", url: "/", trigger: "theme segment: light", api: baseApi(null), ls: { "vinc.site.theme": "light" }, scheme: "light" });
add({ id: "landing__ko", url: "/", trigger: "globe button: Korean (reference only, boards stay English)", api: baseApi(null), ls: { "vinc.site.lang": "ko" } });
add({ id: "landing__mobile-390", url: "/", trigger: "viewport 390: GNB wraps to two rows", api: baseApi(null), vp: MOBILE });
for (const n of [1, 2, 3]) add({ id: `landing__steps-${n}`, url: "/#how", trigger: `three-steps dot ${n}`, api: baseApi(null), full: false, before: async (p) => { await p.getByRole("button", { name: `Step ${n}` }).click(); await sleep(500); await p.locator("#how").scrollIntoViewIfNeeded(); } });
const newsletter = async (p, email) => { await p.locator("#fin").locator("xpath=following-sibling::section[1]").scrollIntoViewIfNeeded().catch(() => {}); const f = p.locator("input[type=email]").last(); await f.scrollIntoViewIfNeeded(); await f.fill(email); await f.press("Enter"); await sleep(600); };
add({ id: "landing__newsletter-busy", url: "/", trigger: "newsletter submit, response pending", api: [...baseApi(null), ["POST", /\/api\/subscribe/, "hang"]], full: false, before: (p) => newsletter(p, "ada@example.com") });
add({ id: "landing__newsletter-success", url: "/", trigger: "newsletter submit 200: block swaps to Almost there", api: [...baseApi(null), ["POST", /\/api\/subscribe/, ok({ status: "ok" })]], full: false, before: (p) => newsletter(p, "ada@example.com") });
add({ id: "landing__newsletter-invalid", url: "/", trigger: "newsletter submit with a bad address", api: [...baseApi(null), ["POST", /\/api\/subscribe/, ERR("invalid_email", "Invalid email", 400)]], full: false, before: (p) => newsletter(p, "not-an-email") });
add({ id: "landing__newsletter-error", url: "/", trigger: "newsletter submit 500", api: [...baseApi(null), ["POST", /\/api\/subscribe/, E500]], full: false, before: (p) => newsletter(p, "ada@example.com") });

// community
const C_API = "/api/community";
const C_C = `${C_API}/c/frontend`;
const commApi = (me = null, extra = []) => [...baseApi(me), ...extra,
  ["GET", `${C_API}/communities`, ok(C_DIRECTORY)],
  ["GET", `${C_API}/communities/mine`, me ? ok({ communities: [C_DIRECTORY.communities[0]] }) : E401],
  ["GET", C_C, ok(C_COMMUNITY)],
  ["GET", `${C_C}/pages`, ok({ pages: C_PAGES.map(({ current, ...p }) => p) })],
  ["GET", `${C_C}/recent`, ok({ edits: [{ ...C_REV, page: "design-tokens" }] })],
  ["GET", /^\/api\/community\/c\/frontend\/(p\/[^/]+|rules)$/, (url) => ok({ page: C_PAGES.find((p) => url.pathname.endsWith("/" + p.slug)) ?? C_PAGE })],
  ["GET", /\/p\/[^/]+\/projection\?/, ok({ revision: 2, state: "current", projectedRevision: 2 })],
  ["GET", /\/p\/[^/]+\/talk(\?|$)/, ok({ threads: [C_THREAD], nextCursor: null })],
  ["GET", /\/p\/[^/]+\/talk\/[0-9a-f-]+/, ok({ thread: C_THREAD, comments: C_COMMENTS, nextCommentCursor: null })],
  ["GET", /\/c\/frontend\/lounge(\?|$)/, ok({ posts: [C_POST], nextCursor: null })],
  ["GET", /\/c\/frontend\/lounge\/[0-9a-f-]+/, ok({ post: C_POST, comments: C_COMMENTS, nextCommentCursor: null })],
  ["GET", /\/p\/[^/]+\/agents(\?|$)/, ok({ agents: [C_AGENT_CARD], nextCursor: null })],
  ["GET", /\/c\/frontend\/attachments\?/, ok({ agents: [C_AGENT_CARD], packs: [] })],
  ["GET", `${C_API}/agents/destinations`, me ? ok(C_DESTINATIONS) : ok({ features: { agents: true }, destinations: [] })],
  ["GET", /\/a\/[0-9a-f-]+\/v\/\d+\/take-preview\?/, ok({ draftActor: C_DESTINATIONS.draftActor, shareId: C_SHARE_ID, version: 2, hash: C_HASH, destination: { kind: "personal" }, candidate: { id: "concept:agent-token-reviewer-aaaaaa", exists: false }, defaultMode: "separate" })],
  ["GET", /^\/api\/community\/a\/[0-9a-f-]+(\/v\/\d+)?$/, ok({ share: C_SHARE, version: C_VERSION, versions: [{ version: 1, hash: "b".repeat(64), title: "Token reviewer", license: C_VERSION.license }, { version: 2, hash: C_HASH, title: "Token reviewer", license: C_VERSION.license }], retainedVersions: [], access: "public" })],
  ["GET", /^\/api\/community\/u\/[^/]+$/, (url) => ok({ profile: { handle: url.pathname.split("/").pop() }, edits: [{ community: "frontend", page: "design-tokens", revision: 2, title: C_PAGE.title, summary: C_REV.summary, channel: "web", createdAt: C_WHEN }], threads: [], posts: [], agents: [], packs: [] })],
];
// 셸이 다 그린 뒤 찍는다: #community-main 의 aria-busy 가 빠지는 것이 렌더 끝 신호다.
const shellReady = async (p) => { await p.locator("#community-main:not([aria-busy])").waitFor(); await sleep(400); };
// SPA 라 하위 경로도 한 파일(community/index.html)이 그린다. 매니페스트의 route 는 그 파일 기준으로 둔다.
const comm = (id, url, trigger, opts = {}) => add({ id: `community__${id}`, url, route: "/community/", trigger, api: commApi(opts.me ?? null), ls: opts.me ? { "vinc.site.signedin": "1" } : {}, vp: opts.vp, full: opts.full ?? true, before: shellReady });
const C_PAGE_URL = "/community/frontend/p/design-tokens";
const C_AGENT_URL = `/community/a/${C_SHARE_ID}/v/2`;
comm("default", "/community/", "directory: communities, signed out");
comm("signed-in", "/community/", "directory with a session: my communities", { me: ME });
comm("home", "/community/frontend", "community home: topic, Rules link, page index");
comm("page-read", C_PAGE_URL, "wiki page, Read tab: body, sources, revision line");
comm("talk", `${C_PAGE_URL}/talk/${C_THREAD.publicId}`, "Talk tab: one thread with a reply");
comm("lounge", "/community/frontend/lounge", "Lounge: post index");
comm("agent-share", C_AGENT_URL, "agent share, pinned version 2: role, tools, license, hash");
comm("agent-take", `${C_AGENT_URL}/take`, "Take panel signed in: exports and Add to my Graph (personal)", { me: ME });
comm("mobile-390", C_PAGE_URL, "viewport 390: wiki page, Read tab", { vp: MOBILE });

// account
// 계정의 Community 절은 위키 셸 API 를 읽는다: 가입한 커뮤니티 · 내 기여(/u/:handle) · 내가 공유한 에이전트와 팩(/agents/mine).
const C_NOTHING = [["GET", `${C_API}/communities/mine`, ok({ communities: [] })], ["GET", /^\/api\/community\/u\/[^/]+$/, (url) => ok({ profile: { handle: url.pathname.split("/").pop() }, edits: [], threads: [], posts: [], agents: [], packs: [] })]];
const acctApi = ({ me = ME, billing = BILLING.trial, keys = KEYS, shares = { agents: [C_AGENT_CARD], packs: [C_PACK_CARD] }, joined = true, domains = DOMAINS, extra = [] } = {}) => [
  ...baseApi(me), ["GET", "/api/billing/status", ok(billing)], ["GET", "/api/account/keys", ok(keys)], ["GET", /\/api\/graph\/domains/, ok(domains)],
  ["GET", /\/api\/username\/check/, (url) => { const u = url.searchParams.get("u") || ""; if (u === "ada") return ok({ valid: true, available: false, reason: "taken" }); if (u.length < 3) return ok({ valid: false, available: false, reason: "too_short" }); if (u === "admin") return ok({ valid: false, available: false, reason: "reserved" }); if (/[^a-z0-9_]/.test(u)) return ok({ valid: false, available: false, reason: "bad_format" }); return ok({ valid: true, available: true, reason: null }); }],
  ["GET", `${C_API}/agents/mine`, ok(shares)], ...commApi(me, joined ? [] : C_NOTHING).slice(baseApi(me).length),
  ...extra];
const acct = (id, trigger, opts = {}, before, full = true) => add({ id: `account__${id}`, url: "/account/", trigger, api: acctApi(opts), ls: { "vinc.site.signedin": "1" }, before, full });
acct("default", "load: free plan, trial open, two keys, one joined community with a shared agent and pack, graph at the vinc level (top 6 + 3 more)");
acct("pro", "plan pro: header badge, topics tile without the limit", { me: ME_PRO, billing: BILLING.active });
acct("team-member", "user with teams: Team plan badge, LNB lists the teams", { me: ME_TEAM, billing: BILLING.none });
acct("plan-get-pro", "billing: trial closed, checkout open: Get Pro", { billing: BILLING.checkout }, async (p) => p.locator("#planCard").scrollIntoViewIfNeeded(), false);
acct("plan-active", "billing: active Paddle subscription: Cancel subscription + Active until", { billing: BILLING.active }, async (p) => p.locator("#planCard").scrollIntoViewIfNeeded(), false);
acct("plan-trialing", "billing: trialing subscription", { billing: BILLING.trialing }, async (p) => p.locator("#planCard").scrollIntoViewIfNeeded(), false);
acct("plan-past-due", "billing: past_due: Payment failed · in grace period", { billing: BILLING.past_due }, async (p) => p.locator("#planCard").scrollIntoViewIfNeeded(), false);
acct("plan-hidden", "billing: nothing applies: CTA hidden", { billing: BILLING.none }, async (p) => p.locator("#planCard").scrollIntoViewIfNeeded(), false);
const uname = (v) => async (p) => { await p.locator("#unameCard").scrollIntoViewIfNeeded(); await p.fill("#unameInput", v); await sleep(500); };
acct("username-unset", "username null: 'You haven't set a username yet.'", { me: ME_NOHANDLE }, async (p) => p.locator("#unameCard").scrollIntoViewIfNeeded(), false);
acct("username-checking", "typing: Checking…", { extra: [["GET", /\/api\/username\/check/, "hang"]] }, uname("newname"), false);
acct("username-available", "check: available, Save enabled", {}, uname("jade_kim"), false);
acct("username-taken", "check: taken", {}, uname("ada"), false);
acct("username-too-short", "check: too short", {}, uname("ab"), false);
acct("username-bad-format", "check: bad format", {}, uname("Bad-Name"), false);
acct("username-reserved", "check: reserved", {}, uname("admin"), false);
acct("username-saved", "Save 200: Saved · @handle", { extra: [["POST", "/api/account/username", ok({ status: "ok", username: "jade_kim" })]] }, async (p) => { await uname("jade_kim")(p); await p.locator("#unameSave").click(); await sleep(500); }, false);
acct("username-cooldown", "Save 429: changed recently", { extra: [["POST", "/api/account/username", ERR("too_many_requests", "cooldown", 429)]] }, async (p) => { await uname("jade_kim")(p); await p.locator("#unameSave").click(); await sleep(500); }, false);
acct("username-conflict", "Save 409: taken at save time", { extra: [["POST", "/api/account/username", ERR("username_taken", "taken", 409)]] }, async (p) => { await uname("jade_kim")(p); await p.locator("#unameSave").click(); await sleep(500); }, false);
const keyCard = async (p) => p.locator("#keyCard").scrollIntoViewIfNeeded();
acct("keys-empty", "no keys: 'No keys yet…'", { keys: { keys: [] } }, keyCard, false);
acct("keys-fresh", "Generate a key 201: fresh key box, copy once", { extra: [["POST", "/api/account/keys", ok(FRESH_KEY, 0, 201)], ["GET", "/api/account/keys", ok({ keys: [...KEYS.keys, { ...FRESH_KEY, created_at: T, last_used_at: null, revoked: false }] })]] }, async (p) => { await keyCard(p); await p.locator("#mkNew").click(); await sleep(600); }, false);
acct("keys-fresh-readonly", "Generate a read-only key 201", { extra: [["POST", "/api/account/keys", ok({ ...FRESH_KEY, prefix: "vinc_ro_77aa", scope: "read", key: "vinc_ro_77aa0011223344556677889900aabbcc" }, 0, 201)]] }, async (p) => { await keyCard(p); await p.locator("#mkNewRo").click(); await sleep(600); }, false);
acct("keys-limit", "Generate 400 bad_request: native alert 'Key limit reached…' (text in manifest)", { extra: [["POST", "/api/account/keys", ERR("bad_request", "limit", 400)]] }, async (p) => { await keyCard(p); await p.locator("#mkNew").click(); await sleep(400); }, false);
acct("keys-revoke-confirm", "Revoke: native confirm (text in manifest)", {}, async (p) => { await keyCard(p); await p.locator("#keyList button").first().click(); await sleep(300); }, false);
const graph = async (p) => p.locator("#acc-graph").scrollIntoViewIfNeeded();
acct("graph-default", "graph: donut at the vinc level, six named slices + '3 more'", {}, graph, false);
acct("graph-more-open", "graph: '3 more' expanded", {}, async (p) => { await graph(p); await p.locator("#gLegend button", { hasText: /more/i }).first().click(); await sleep(400); }, false);
acct("graph-drill-product", "graph: legend row 'product' › descends, breadcrumb All › product", {}, async (p) => { await graph(p); await p.locator("#gLegend button", { hasText: /product/ }).first().click(); await sleep(500); }, false);
acct("graph-drill-book", "graph: product › book, two leaves", {}, async (p) => { await graph(p); await p.locator("#gLegend button", { hasText: /product/ }).first().click(); await sleep(400); await p.locator("#gLegend button", { hasText: /book/ }).first().click(); await sleep(500); }, false);
acct("graph-empty", "graph: rows [] : Nothing synced yet", { domains: { executed: true, rows: [] } }, graph, false);
const commCard = async (p) => { await p.locator("#acc-community").scrollIntoViewIfNeeded(); await p.locator("#communityShares h3").first().waitFor(); await sleep(300); };
acct("community-list", "Community: one joined community, one contribution, one shared agent and one shared pack", {}, commCard, false);
acct("community-empty", "Community: nothing joined, contributed or shared", { joined: false, shares: { agents: [], packs: [] } }, commCard, false);
const delOpen = async (p) => { await p.locator("#delCard").scrollIntoViewIfNeeded(); await p.locator("#delOpenBtn").click(); await p.waitForSelector("#delModal", { state: "visible" }); await sleep(300); };
acct("delete-ask", "Delete: modal, Delete disabled until the word is typed", {}, delOpen, false);
acct("delete-typed", "Delete: DELETE typed, button enabled", {}, async (p) => { await delOpen(p); await p.fill("#delTyped", "DELETE"); await sleep(200); }, false);
acct("delete-busy", "Delete: request pending", { extra: [["POST", "/api/account/delete", "hang"]] }, async (p) => { await delOpen(p); await p.fill("#delTyped", "DELETE"); await p.locator("#delGo").click(); await sleep(500); }, false);
acct("delete-done", "Delete 202: We sent a confirmation link", { extra: [["POST", "/api/account/delete", ok({ status: "sent" }, 0, 202)]] }, async (p) => { await delOpen(p); await p.fill("#delTyped", "DELETE"); await p.locator("#delGo").click(); await sleep(600); }, false);
acct("delete-error", "Delete 500: back to ask with an error line", { extra: [["POST", "/api/account/delete", E500]] }, async (p) => { await delOpen(p); await p.fill("#delTyped", "DELETE"); await p.locator("#delGo").click(); await sleep(600); }, false);
add({ id: "account__mobile-390", url: "/account/", trigger: "viewport 390", api: acctApi(), ls: { "vinc.site.signedin": "1" }, vp: MOBILE });

// team
const teamApi = ({ me = ME_TEAM, team = TEAM, extra = [] } = {}) => [
  ...baseApi(me), ["GET", /^\/api\/teams\/[^/]+$/, ok(team)], ["GET", /\/api\/graph\/domains\?team=/, ok(DOMAINS)], ["GET", /\/api\/graph\/graph\?/, ok(NODES)], ["GET", /\/api\/graph\/node-context\?/, ok(NODE_CTX)],
  ["GET", /\/api\/teams\/[^/]+\/invite-candidates/, ok({ candidates: [{ publicId: "018f-u9", username: "cara", name: "Cara K" }, { publicId: "018f-u8", username: "carlos", name: "Carlos M" }] })], ...extra];
const team = (id, trigger, opts = {}, before, full = true, url = "/account/team/") => add({ id: `team__${id}`, url, trigger, api: teamApi(opts), ls: { "vinc.site.signedin": "1" }, before, full });
const settled = async (p) => { await p.waitForFunction(() => ["tName", "ntCta", "acceptBtn"].some((id) => { const e = document.getElementById(id); return e && e.offsetParent !== null; })); await sleep(600); };
team("default-admin", "admin: members with role selects, invites, connector, seats, danger zone", {}, settled);
team("viewer", "viewer: read-only pills, no invite or danger card", { team: TEAM_VIEWER }, settled);
team("no-mcp-base", "server sends no mcpBase: connector card hidden", { team: TEAM_NOMCP }, settled);
team("no-teams", "user without teams: No teams yet + Create a team", { me: ME }, settled, false);
team("invite-card", "arrive with #token: invitation card", {}, settled, false, "/account/team/#inv_abc123");
team("invite-accepted", "Accept 200: You joined the team", { extra: [["POST", "/api/teams/invites/accept", ok({ ok: true, role: "editor" })]] }, async (p) => { await settled(p); await p.locator("#acceptBtn").click(); await sleep(400); }, false, "/account/team/#inv_abc123");
team("invite-invalid", "Accept 400: not valid any more", { extra: [["POST", "/api/teams/invites/accept", ERR("invalid_or_expired_token", "expired", 400)]] }, async (p) => { await settled(p); await p.locator("#acceptBtn").click(); await sleep(500); }, false, "/account/team/#inv_abc123");
const inviteCard = async (p) => { await settled(p); await p.locator("#inviteCard").scrollIntoViewIfNeeded(); };
team("invite-typeahead", "type 'ca' in the invite field: candidates dropdown", {}, async (p) => { await inviteCard(p); await p.fill("#ivQ", "ca"); await sleep(500); }, false);
team("invite-sent", "Invite 200 sent", { extra: [["POST", /\/api\/teams\/[^/]+\/invites$/, ok({ id: 33, role: "editor", status: "sent" })]] }, async (p) => { await inviteCard(p); await p.fill("#ivQ", "frank@example.com"); await p.locator("#ivBtn").click(); await sleep(500); }, false);
team("invite-seat-full", "Invite 409 seat_limit", { extra: [["POST", /\/api\/teams\/[^/]+\/invites$/, ERR("seat_limit", "full", 409)]] }, async (p) => { await inviteCard(p); await p.fill("#ivQ", "frank@example.com"); await p.locator("#ivBtn").click(); await sleep(500); }, false);
team("invite-delivery-failed", "Invite 200 status failed", { extra: [["POST", /\/api\/teams\/[^/]+\/invites$/, ok({ id: 33, role: "editor", status: "failed" })]] }, async (p) => { await inviteCard(p); await p.fill("#ivQ", "frank@example.com"); await p.locator("#ivBtn").click(); await sleep(500); }, false);
team("connector-copied", "Copy: label flips to Copied", {}, async (p) => { await settled(p); await p.locator("#connectorCard").scrollIntoViewIfNeeded(); await p.locator("#cnCopy").click(); await sleep(200); }, false);
team("danger-error", "Delete team confirm accepted, 500: inline error", { extra: [["POST", /\/api\/teams\/[^/]+\/delete$/, E500]] }, async (p) => { await settled(p); await p.locator("#dangerCard").scrollIntoViewIfNeeded(); p.acceptDialogs = true; await p.locator("#delBtn").click(); await sleep(600); }, false);
const graphTab = async (p) => { await settled(p); await p.locator('[data-sub="graph"]').click(); await p.waitForFunction(() => document.querySelector("#topicList")?.children.length > 0 || (document.querySelector("#graphEmpty")?.textContent || "").trim().length > 0); await sleep(600); };
team("graph", "Graph tab: donut of the top 4 domains + topic list", {}, graphTab, false);
team("graph-empty", "Graph tab, rows []", { extra: [["GET", /\/api\/graph\/domains\?team=/, ok({ executed: true, rows: [] })]] }, graphTab, false);
const rail = async (p) => { await graphTab(p); await p.locator("#topicList button, #topicList [role=button], #topicList li").first().click(); await p.waitForSelector("#drillRail", { state: "visible" }); await sleep(500); };
team("drill-list", "click a topic: drill rail with concepts", {}, rail, false);
team("drill-loading", "drill rail, request pending", { extra: [["GET", /\/api\/graph\/graph\?/, "hang"]] }, rail, false);
team("drill-empty", "drill rail, nodes []", { extra: [["GET", /\/api\/graph\/graph\?/, ok({ nodes: [] })]] }, rail, false);
team("drill-error", "drill rail 500", { extra: [["GET", /\/api\/graph\/graph\?/, E500]] }, rail, false);
team("drill-detail", "click a concept: detail with excerpts and related, Back chevron", {}, async (p) => { await rail(p); await p.locator("#drillBody button").first().click(); await sleep(600); }, false);
team("create-modal", "New team: create modal", {}, async (p) => { await settled(p); await p.locator("#newTeamBtn").click(); await p.waitForSelector("#createModal", { state: "visible" }); await sleep(300); }, false);
team("create-error", "Create 500: inline error", { extra: [["POST", "/api/teams", E500]] }, async (p) => { await settled(p); await p.locator("#newTeamBtn").click(); await p.fill("#crName", "Research Guild"); await p.locator("#crGo").click(); await sleep(500); }, false);

// auth and transactional
const login = (id, trigger, api, before) => add({ id: `login__${id}`, url: "/login/", trigger, api, before, full: false });
const submitLogin = async (p, email = "ada@example.com") => { await p.fill("input[type=email]", email); await p.locator("button[type=submit]").first().click(); await sleep(600); };
login("default", "load, signed out", baseApi(null));
login("sending", "submit, response pending", [...baseApi(null), ["POST", "/api/auth/request", "hang"]], submitLogin);
login("sent", "submit 200: Check your inbox", [...baseApi(null), ["POST", "/api/auth/request", ok({ status: "ok", message: "sent" })]], submitLogin);
login("invalid", "submit 400 invalid_email", [...baseApi(null), ["POST", "/api/auth/request", ERR("invalid_email", "invalid", 400)]], submitLogin);
login("rate-limited", "submit 429", [...baseApi(null), ["POST", "/api/auth/request", ERR("too_many_requests", "wait", 429)]], submitLogin);
login("error", "submit 500", [...baseApi(null), ["POST", "/api/auth/request", E500]], submitLogin);
const verify = (id, trigger, api, before, url = "/auth/verify/#tok_abc123") => add({ id: `verify__${id}`, url, trigger, api, before, full: false });
const CTX409 = { status: 409, json: { error: { code: "confirm_required", message: "confirm", confirm_context: { requested_at: "2026-09-05T12:00:00Z", approx_ip: "203.0.113.x", approx_location: "Seoul, KR" } } } };
verify("confirm", "arrive with a token: click gate", baseApi(null));
verify("verifying", "Confirm clicked, response pending", [...baseApi(null), ["POST", "/api/auth/verify", "hang"]], async (p) => { await p.locator("#doVerify").click(); await sleep(400); });
verify("ctx", "verify 409 confirm_required: Confirm it's you with request context", [...baseApi(null), ["POST", "/api/auth/verify", CTX409]], async (p) => { await p.locator("#doVerify").click(); await sleep(500); });
verify("expired", "verify 400: This link is spent", [...baseApi(null), ["POST", "/api/auth/verify", ERR("invalid_or_expired_token", "spent", 400)]], async (p) => { await p.locator("#doVerify").click(); await sleep(500); });
verify("error", "verify 500", [...baseApi(null), ["POST", "/api/auth/verify", E500]], async (p) => { await p.locator("#doVerify").click(); await sleep(500); });
verify("invalid", "no token in the URL", baseApi(null), undefined, "/auth/verify/");
add({ id: "welcome__loading", url: "/welcome/", trigger: "session probe pending", api: [["GET", "/api/me", "hang"]], full: false });
add({ id: "welcome__form", url: "/welcome/", trigger: "signed in without a handle: pick a username", api: [["GET", "/api/me", ok(ME_NOHANDLE)], ["GET", /\/api\/username\/check/, ok({ valid: true, available: true })]], full: false, before: async (p) => p.waitForSelector("#card", { state: "visible" }) });
add({ id: "welcome__available", url: "/welcome/", trigger: "typed a free handle", api: [["GET", "/api/me", ok(ME_NOHANDLE)], ["GET", /\/api\/username\/check/, ok({ valid: true, available: true })]], full: false, before: async (p) => { await p.waitForSelector("#card", { state: "visible" }); await p.fill("#unameInput", "jade_kim"); await sleep(500); } });
add({ id: "welcome__taken", url: "/welcome/", trigger: "typed a taken handle", api: [["GET", "/api/me", ok(ME_NOHANDLE)], ["GET", /\/api\/username\/check/, ok({ valid: true, available: false, reason: "taken" })]], full: false, before: async (p) => { await p.waitForSelector("#card", { state: "visible" }); await p.fill("#unameInput", "ada"); await sleep(500); } });
const connect = (id, trigger, api, before, url = "/connect/#req=r_1&cra=cra_abc") => add({ id: `connect__${id}`, url, trigger, api, before, full: false });
const DESCRIBE = ok({ req: "r_1", client_name: "Claude", redirect_host: "claude.ai" });
connect("loading", "describe pending", [...baseApi(ME), ["POST", "/api/connect/describe", "hang"]]);
connect("ask", "describe 200: Let this app reach your account graph?", [...baseApi(ME), ["POST", "/api/connect/describe", DESCRIBE]]);
connect("login", "describe 401: Sign in to approve", [...baseApi(null), ["POST", "/api/connect/describe", E401]]);
connect("done", "approve 200: Connected, then redirect", [...baseApi(ME), ["POST", "/api/connect/describe", DESCRIBE], ["POST", "/api/connect/approve", ok({ redirect: `${BASE}/connect/#done` })]], async (p) => { await p.locator("#ok").click(); await sleep(700); });
connect("expired", "describe 400: This request expired", [...baseApi(ME), ["POST", "/api/connect/describe", ERR("invalid_request", "gone", 400)]]);
connect("error", "describe network failure: Approval didn't go through", [...baseApi(ME), ["POST", "/api/connect/describe", "abort"]]);
connect("no-cra", "missing cra: expired immediately", baseApi(ME), undefined, "/connect/#req=r_1");
const capp = (id, trigger, api, before, url = "/connect-app/#port=5555&state=st_abc") => add({ id: `connect-app__${id}`, url, trigger, api, before, full: false });
const CB = ["POST", /127\.0\.0\.1:\d+\/connect\/callback/, ok({ ok: true })];
capp("ask", "signed in: Link the desktop app to this account?", baseApi(ME));
capp("login", "401: Sign in", baseApi(null));
capp("done", "Approve: key 201 + loopback 200: App linked", [...baseApi(ME), ["POST", "/api/account/keys", ok(FRESH_KEY, 0, 201)], CB], async (p) => { await p.locator("#ok").click(); await sleep(700); });
capp("manual", "Approve: key 201, loopback refused: One manual step with the key", [...baseApi(ME), ["POST", "/api/account/keys", ok(FRESH_KEY, 0, 201)], ["POST", /127\.0\.0\.1:\d+\/connect\/callback/, "abort"]], async (p) => { await p.locator("#ok").click(); await sleep(900); });
capp("error", "Approve: key 400: Couldn't issue a key", [...baseApi(ME), ["POST", "/api/account/keys", ERR("bad_request", "limit", 400)]], async (p) => { await p.locator("#ok").click(); await sleep(600); });
capp("expired", "malformed hash: The app stopped waiting", baseApi(ME), undefined, "/connect-app/#port=99");
const tok = (prefix, url) => (id, trigger, api, before, u = url) => add({ id: `${prefix}__${id}`, url: u, trigger, api, before, full: false });
const sub = tok("subscribe", "/subscribe/confirm/#tok_abc");
sub("confirm", "arrive with a token", baseApi(null));
sub("confirming", "Confirm clicked, pending", [...baseApi(null), ["POST", "/api/subscribe/confirm", "hang"]], async (p) => { await p.locator("#doConfirm").click(); await sleep(400); });
sub("confirmed", "200: You're on the list", [...baseApi(null), ["POST", "/api/subscribe/confirm", ok({ status: "confirmed" })]], async (p) => { await p.locator("#doConfirm").click(); await sleep(500); });
sub("expired", "400: Link expired", [...baseApi(null), ["POST", "/api/subscribe/confirm", ERR("invalid_or_expired_token", "x", 400)]], async (p) => { await p.locator("#doConfirm").click(); await sleep(500); });
sub("error", "500: Couldn't confirm", [...baseApi(null), ["POST", "/api/subscribe/confirm", E500]], async (p) => { await p.locator("#doConfirm").click(); await sleep(500); });
sub("no-token", "no token: expired", baseApi(null), undefined, "/subscribe/confirm/");
const unsub = tok("unsubscribe", "/unsubscribe/?token=tok_abc");
unsub("confirm", "arrive with a token: Stop release news?", baseApi(null));
unsub("unsubscribing", "pending", [...baseApi(null), ["POST", /\/api\/unsubscribe/, "hang"]], async (p) => { await p.locator("#doUnsub").click(); await sleep(400); });
unsub("unsubscribed", "200", [...baseApi(null), ["POST", /\/api\/unsubscribe/, ok({ status: "unsubscribed" })]], async (p) => { await p.locator("#doUnsub").click(); await sleep(500); });
unsub("invalid", "400 invalid_token", [...baseApi(null), ["POST", /\/api\/unsubscribe/, ERR("invalid_token", "x", 400)]], async (p) => { await p.locator("#doUnsub").click(); await sleep(500); });
unsub("error", "500", [...baseApi(null), ["POST", /\/api\/unsubscribe/, E500]], async (p) => { await p.locator("#doUnsub").click(); await sleep(500); });
unsub("no-token", "no token: invalid", baseApi(null), undefined, "/unsubscribe/");
const del = tok("account-delete", "/account/delete/#tok_abc");
del("confirm", "arrive with a token, signed in: account row shown", baseApi(ME));
del("confirm-signed-out", "arrive with a token, 401: account row hidden", baseApi(null));
del("deleting", "Delete clicked, pending", [...baseApi(ME), ["POST", "/api/account/delete/confirm", "hang"]], async (p) => { await p.locator("#doDelete").click(); await sleep(400); });
del("deleted", "200: Your web account is gone", [...baseApi(ME), ["POST", "/api/account/delete/confirm", ok({ status: "deleted" })]], async (p) => { await p.locator("#doDelete").click(); await sleep(500); });
del("expired", "400: This link is spent", [...baseApi(ME), ["POST", "/api/account/delete/confirm", ERR("invalid_or_expired_token", "x", 400)]], async (p) => { await p.locator("#doDelete").click(); await sleep(500); });
del("error-no-retry", "409 confirm_required with a server message, Retry hidden", [...baseApi(ME), ["POST", "/api/account/delete/confirm", ERR("confirm_required", "The confirmation must come from the same browser that requested it.", 409)]], async (p) => { await p.locator("#doDelete").click(); await sleep(500); });
del("error-retry", "network failure: error with Retry", [...baseApi(ME), ["POST", "/api/account/delete/confirm", "abort"]], async (p) => { await p.locator("#doDelete").click(); await sleep(500); });
del("no-token", "no token: expired", baseApi(ME), undefined, "/account/delete/");

// content pages
add({ id: "pricing__monthly", url: "/pricing/", trigger: "load: monthly", api: [...baseApi(null), ["GET", "/api/billing/status", ok(BILLING.checkout)]] });
add({ id: "pricing__yearly", url: "/pricing/", trigger: "Yearly segment", api: [...baseApi(null), ["GET", "/api/billing/status", ok(BILLING.checkout)]], before: async (p) => { await p.getByRole("button", { name: /yearly|연간/i }).click(); await sleep(400); } });
add({ id: "pricing__trial-banner", url: "/pricing/", trigger: "billing: trial open: banner with the end date", api: [...baseApi(null), ["GET", "/api/billing/status", ok(BILLING.trial)]] });
add({ id: "pricing__ko", url: "/pricing/", trigger: "Korean: KRW prices (reference)", api: [...baseApi(null), ["GET", "/api/billing/status", ok(BILLING.checkout)]], ls: { "vinc.site.lang": "ko" } });
add({ id: "pricing__mobile-390", url: "/pricing/", trigger: "viewport 390", api: [...baseApi(null), ["GET", "/api/billing/status", ok(BILLING.checkout)]], vp: MOBILE });
for (const [k, ua] of Object.entries(UA)) add({ id: `download__${k}`, url: "/download/", trigger: `platform detected from the user agent: ${k}`, api: baseApi(null), ua });
add({ id: "release__default", url: "/release/", trigger: "load", api: baseApi(null) });
add({ id: "privacy__default", url: "/privacy/", trigger: "load", api: baseApi(null) });
add({ id: "404__default", url: "/this-page-is-not-here/", trigger: "unknown path", api: baseApi(null), full: false });
for (const d of ["", "mcp/", "agents/", "cli/", "graph-md/", "mcp/sign-in/"]) add({ id: `docs__${d ? d.replace(/\/$/, "").replace(/\//g, "-") : "index"}`, url: `/docs/${d}`, trigger: "load", api: baseApi(null) });
add({ id: "docs__mobile-800", url: "/docs/mcp/", trigger: "viewport 800: sidebar becomes a horizontal strip", api: baseApi(null), vp: { width: 800, height: 900 } });
add({ id: "docs__mobile-390", url: "/docs/", trigger: "viewport 390", api: baseApi(null), vp: MOBILE });

// ── 다른 스크립트가 같은 상태 목록을 쓴다 ───────────────────────────────────
// capture_site_figma.mjs 는 같은 상태·픽스처로 페이지를 띄운 뒤 PNG 대신 Figma html-to-design 캡처를
// 제출한다. 상태 정의를 한 곳에 두려고 여기서 내보내고, 실행부는 직접 실행일 때만 돈다.
export { S, install, BASE, MOBILE, sleep };
const isMain = process.argv[1] && import.meta.url === new URL(`file:///${process.argv[1].replace(/\\/g, "/")}`).href;

// ── 실행 ───────────────────────────────────────────────────────────────────
const onlyList = ONLY ? ONLY.split(",") : null;
const states = onlyList ? S.filter((s) => onlyList.some((o) => s.id.includes(o))) : S;
if (!isMain) { /* imported: 실행부 건너뜀 */ } else {
if (LIST) { for (const s of states) console.log(s.id.padEnd(40), s.trigger); console.log(states.length, "states"); process.exit(0); }
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
// 어느 사이트 리비전을 그렸는지 남긴다(형제 체크아웃 Vinc-homepage 의 HEAD; 라이브가 그것과 다르면 사람이 맞춘다).
let siteRev = null; try { siteRev = (await import("node:child_process")).execSync("git rev-parse HEAD", { cwd: path.resolve(HUB, "..", "..", "Vinc-homepage"), encoding: "utf8" }).trim(); } catch {}
const manifest = { base: BASE, capturedAt: T, tool: "design-hub/scripts/capture_site.mjs", siteRev, states: [] };
let fails = 0;
for (const s of states) {
  const vp = s.vp ?? { width: 1440, height: 900 };
  const ctx = await browser.newContext({ viewport: vp, colorScheme: s.scheme ?? "dark", reducedMotion: "reduce", locale: "en-US", userAgent: s.ua, deviceScaleFactor: 1 });
  await ctx.addCookies([{ name: "__Host-csrf", value: "capture", url: BASE }]).catch(() => {});
  const ls = { "vinc.site.theme": s.scheme ?? "dark", "vinc.site.lang": "en", ...s.ls };
  await ctx.addInitScript((kv) => { try { for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, v); } catch (e) {} }, ls);
  const page = await ctx.newPage();
  const dialogs = [];
  if (process.env.CAPTURE_DEBUG) {
    page.on("request", (r) => { if (r.url().includes("/api/")) console.log("   →", r.method(), r.url().replace(BASE, "")); });
    page.on("response", (r) => { if (r.url().includes("/api/")) console.log("   ←", r.status(), r.url().replace(BASE, "")); });
  }
  // 네이티브 대화상자는 하나의 핸들러만 둔다(둘이면 두 번째 accept/dismiss 가 던져 실행이 통째로 죽는다).
  // 상태가 확인을 눌러야 하면 before() 안에서 page.acceptDialogs = true 로 표시한다.
  page.on("dialog", async (d) => { dialogs.push({ type: d.type(), message: d.message() }); await (page.acceptDialogs ? d.accept() : d.dismiss()).catch(() => {}); });
  const row = { id: s.id, route: s.route ?? s.url.replace(/[#?].*$/, ""), state: s.id.split("__")[1], trigger: s.trigger, viewport: vp, scheme: s.scheme ?? "dark", file: `${s.id}.png` };
  try {
    await install(page, s.api);
    await page.goto(BASE + s.url, { waitUntil: "networkidle", timeout: 45000 }).catch(async () => page.goto(BASE + s.url, { waitUntil: "load", timeout: 45000 }));
    await sleep(700);
    if (s.before) await s.before(page);
    await page.screenshot({ path: path.join(OUT, row.file), fullPage: s.full });
    const buf = fs.readFileSync(path.join(OUT, row.file));
    row.sha1 = crypto.createHash("sha1").update(buf).digest("hex");
    row.height = s.full ? await page.evaluate(() => document.documentElement.scrollHeight) : vp.height;
    if (dialogs.length) row.dialogs = dialogs;
    row.finalUrl = page.url().replace(BASE, "");
    console.log("ok  ", s.id, row.height + "px", dialogs.length ? JSON.stringify(dialogs) : "");
  } catch (e) {
    fails++; row.error = String(e.message || e).split("\n")[0].slice(0, 200); console.log("FAIL", s.id, row.error);
    if (process.env.CAPTURE_DEBUG) console.log("   body:", (await page.evaluate(() => document.body.innerText.slice(0, 400)).catch(() => "?")).replace(/[ \t\r\n]+/g, " "), "| url:", page.url());
  }
  manifest.states.push(row);
  await ctx.close();
}
await browser.close();
// --only 재실행은 기존 매니페스트에 행을 갈아 끼운다(부분 실행이 전체 매니페스트를 지우지 않게).
const mpath = path.join(OUT, "manifest.json");
if (onlyList && fs.existsSync(mpath)) {
  const known = new Set(S.map((s) => s.id));   // 스크립트에서 빠진 상태의 옛 행은 버린다
  const old = JSON.parse(fs.readFileSync(mpath, "utf8")); const byId = new Map(old.states.filter((r) => known.has(r.id)).map((r) => [r.id, r]));
  // 다른 base(예: 로컬 사이트)로 다시 찍은 행은 자기 base·siteRev 를 단다. 매니페스트 머리는 나머지 행의 출처라 그대로 둔다.
  for (const r of manifest.states) byId.set(r.id, old.base && old.base !== BASE ? { ...r, base: BASE, siteRev } : r);
  manifest.states = [...byId.values()]; manifest.capturedAt = old.capturedAt; manifest.updatedAt = T;
  if (old.base && old.base !== BASE) { delete manifest.siteRev; for (const [k, v] of Object.entries(old)) if (k !== "states" && k !== "updatedAt") manifest[k] = v; }
}
fs.writeFileSync(mpath, JSON.stringify(manifest, null, 1));
console.log(`\n${manifest.states.length - fails} captured, ${fails} failed → ${path.relative(HUB, OUT)}`);
process.exit(fails ? 1 : 0);
}
