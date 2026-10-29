// figma_wire_prototypes.js — 캡처 보드 위에 프로토타입 연결(ON_CLICK → NAVIGATE)과 인터랙션 지도를 놓는다.
//
// use_figma(Plugin API) 스크립트다. 페이지 이름 하나를 PAGE 에 넣고 그 본문을 그대로 실행한다
// (한 호출에 setCurrentPageAsync 는 한 번). 보드를 다시 캡처하면 옛 보드가 사라지므로 연결도 함께
// 사라진다 — 재캡처 뒤에는 이 스크립트를 페이지마다 다시 돌릴 것.
//
// 표의 한 줄은 [보드, 핫스팟 조건, 목적지 보드, 라벨] 이다. 핫스팟 조건은 캡처가 붙인 레이어 이름
// (Button · Link · Tab · Menu Item · Canvas …)과 첫 텍스트를 정규식으로 고른다. 목적지가 **다른
// 페이지**면 연결은 걸 수 없다(Figma 프로토타입은 한 페이지 안에서만 잇는다) — 그 줄은 지도와 표에
// 회색으로만 남는다. 앱은 표면(04)→오버레이(05) 전이가 대부분이라 그 제약이 크게 보인다.
//
// 근거: [[decision:site-boards-are-captures-of-the-running-code]].

const PAGE = "11 · Site Screens"; // "04 · Surfaces" | "05 · Overlays"

const V = (s) => `App · View${s ? " · " + s : ""} / dark`, T = (s) => `App · Topbar · ${s} / dark`, V3 = V("3d");
const B = (re) => ({ name: /^Button$/, text: re }), TAB = (re) => ({ name: /^Tab$/, text: re }), CLOSE = { name: /^Button - Close$/, text: /^✕$/ };
const VP = (b, cur) => ["3D", "2D", "Timeline", "Weave"].filter(v => v !== cur).map(v => [b, B(new RegExp(`^${v}$`)), V(v === "3D" ? "3d" : v.toLowerCase()), `Viewpoint ${v}`]);
const SHELL = (b) => [
  [b, { name: /^Button - Search mode: Find$/ }, T("mode-menu"), "Search mode menu"],
  [b, { name: /^Button - View and legend$/ }, V("lens-options-3d"), "View and legend"],
  [b, { name: /^Button - GRAPH.md$/ }, T("graphmd"), "GRAPH.md"],
  [b, { name: /^Button - Tools$/ }, T("tools-create"), "Tools"],
  [b, { name: /^Button - Settings$/ }, T("settings-account"), "Settings"],
];
const TOOLS = (b, cur) => ["Create", "Import", "Share"].filter(v => v !== cur).map(v => [b, B(new RegExp(`^${v}$`)), T("tools-" + v.toLowerCase()), `${v} tab`]);
const SETTINGS = (b, cur) => ["Account", "Connections", "Data", "Advanced"].filter(v => v !== cur).map(v => [b, TAB(new RegExp(`^${v}$`)), T("settings-" + v.toLowerCase()), `${v} tab`]);

const TABLES = {
  "11 · Site Screens": [
    ["Site · Landing / dark", { text: /^Get started$/ }, V3, "Get started → web app"],
    ["Site · Landing / dark", { text: /^Download$/ }, "Site · Download · mac / dark", "Download"],
    ["Site · Landing / dark", { text: /^Read the docs$/ }, "Site · Docs · index / dark", "Read the docs"],
    ["Site · Landing / dark", { text: /^Browse the community$/ }, "Site · Community / dark", "Browse the community"],
    ["Site · Landing / dark", { name: /^Button - Step 1/ }, "Site · Landing · steps-1 / dark", "Step 1 dot"],
    ["Site · Landing / dark", { name: /^Button - Step 2/ }, "Site · Landing · steps-2 / dark", "Step 2 dot"],
    ["Site · Landing / dark", { name: /^Button - Step 3/ }, "Site · Landing · steps-3 / dark", "Step 3 dot"],
    ["Site · Landing / dark", { text: /^Subscribe$/ }, "Site · Landing · newsletter-success / dark", "Subscribe"],
    ["Site · Landing / dark", { text: /^Sign in$/ }, "Site · Auth · Login / dark", "GNB Sign in"],
    ["Site · Landing / dark", { text: /^Pricing$/ }, "Site · Pricing · monthly / dark", "GNB Pricing"],
    ["Site · Landing / dark", { text: /^Community$/ }, "Site · Community / dark", "GNB Community"],
    ["Site · Landing / dark", { text: /^Docs$/ }, "Site · Docs · index / dark", "GNB Docs"],
    ["Site · Community / dark", { text: /^Share your own pack$/ }, "App · Packs / dark", "Share your own pack → web app Packs"],
    ["Site · Community / dark", { text: /^Import$/ }, "Site · Community · modal-confirm-button / dark", "Import on a card"],
    ["Site · Community / dark", { name: /^Link - Vinc home/ }, "Site · Landing / dark", "GNB mark"],
    ["Site · Community / dark", { text: /^Sign in$/ }, "Site · Auth · Login / dark", "GNB Sign in"],
    ["Site · Account / dark", { text: /^Sign out$/ }, "Site · Landing / dark", "Sign out"],
    ["Site · Account / dark", { text: /^Save$/ }, "Site · Account · username-saved / dark", "Username Save"],
    ["Site · Account / dark", { text: /^Generate a key$/ }, "Site · Account · keys-fresh / dark", "Generate a key"],
    ["Site · Account / dark", { text: /^Generate a read-only key$/ }, "Site · Account · keys-fresh-readonly / dark", "Generate a read-only key"],
    ["Site · Account / dark", { text: /^Revoke$/ }, "Site · Account · keys-revoke-confirm / dark", "Revoke (native confirm)"],
    ["Site · Account / dark", { text: /^How to connect$/ }, "Site · Docs · mcp / dark", "How to connect"],
    ["Site · Account / dark", { text: /^Delete$/ }, "Site · Account · delete-ask / dark", "Delete account"],
    ["Site · Account / dark", { text: /^Open my graph$/ }, V3, "Open my graph → web app"],
    ["Site · Account / dark", { text: /^product\s*$/ }, "Site · Account · graph-drill-product / dark", "Legend row product ›"],
    ["Site · Account / dark", { text: /^3 more\s*$/ }, "Site · Account · graph-more-open / dark", "3 more"],
    ["Site · Account / dark", { text: /^Open the gallery$/ }, "Site · Community / dark", "Open the gallery"],
    ["Site · Account / dark", { text: /^Take down$/ }, "Site · Account · community-takedown-confirm / dark", "Take down (native confirm)"],
    ["Site · Account / dark", { text: /^\+$/ }, "Site · Account · Team · create-modal / dark", "+ New team"],
    ["Site · Account / dark", { text: /^My graph$/ }, V3, "GNB My graph"],
    ["Site · Account / dark", { text: /^Community$/ }, "Site · Community / dark", "GNB Community"],
    ["Site · Account · Team · default-admin / dark", { text: /^Invite$/ }, "Site · Account · Team · invite-sent / dark", "Invite"],
    ["Site · Account · Team · default-admin / dark", { text: /^Copy$/ }, "Site · Account · Team · connector-copied / dark", "Copy connector address"],
    ["Site · Account · Team · default-admin / dark", { text: /^Delete team$/ }, "Site · Account · Team · danger-error / dark", "Delete team (native confirm)"],
    ["Site · Account · Team · default-admin / dark", { text: /^Graph$/ }, "Site · Account · Team · graph / dark", "Graph tab"],
    ["Site · Account · Team · default-admin / dark", { text: /^Community$/ }, "Site · Account · Team · community-tab / dark", "Community tab"],
    ["Site · Account · Team · default-admin / dark", { text: /^Design Guild$/ }, "Site · Account · Team · viewer / dark", "Team row Design Guild (viewer)"],
    ["Site · Account · Team · default-admin / dark", { text: /^\+$|New team/ }, "Site · Account · Team · create-modal / dark", "+ New team"],
    ["Site · Account · Team · graph / dark", { text: /^vinc\/product/ }, "Site · Account · Team · drill-list / dark", "Topic row → drill rail"],
    ["Site · Account · Team · drill-list / dark", { text: /^Retrieval$/ }, "Site · Account · Team · drill-detail / dark", "Concept → detail"],
    ["Site · Auth · Login / dark", { name: /^Button/, text: /link|Send|Continue|Sign in/i }, "Site · Auth · Login · sent / dark", "Send the link"],
    ["Site · Auth · Verify · confirm / dark", { name: /^Button$/, text: /^Sign in to vincs\.io$/ }, "Site · Auth · Verify · verifying / dark", "Sign in to vincs.io"],
    ["Site · Auth · Welcome · form / dark", { text: /^Set username$/ }, "Site · Account / dark", "Set username"],
    ["Site · Auth · Welcome · form / dark", { text: /^Skip for now$/ }, "Site · Account / dark", "Skip for now"],
    ["Site · Connect · ask / dark", { name: /^Button$/, text: /^Approve connection$/ }, "Site · Connect · done / dark", "Approve connection"],
    ["Site · Connect · ask / dark", { name: /^Button$/, text: /^Don.t connect$/ }, "Site · Account / dark", "Don't connect"],
    ["Site · Connect-app · ask / dark", { name: /^Button/, text: /Link|Approve|Allow/ }, "Site · Connect-app · done / dark", "Link the app"],
    ["Site · Subscribe · confirm / dark", { text: /^Confirm/ }, "Site · Subscribe · confirmed / dark", "Confirm"],
    ["Site · Unsubscribe · confirm / dark", { text: /^Unsubscribe$/ }, "Site · Unsubscribe · unsubscribed / dark", "Unsubscribe"],
    ["Site · Account · Delete · confirm / dark", { text: /^Delete/ }, "Site · Account · Delete · deleted / dark", "Delete"],
    ["Site · Account · Delete · confirm / dark", { text: /^Keep/ }, "Site · Account / dark", "Keep"],
    ["Site · Pricing · monthly / dark", { text: /^Yearly$/ }, "Site · Pricing · yearly / dark", "Yearly"],
    ["Site · Pricing · yearly / dark", { text: /^Monthly$/ }, "Site · Pricing · monthly / dark", "Monthly"],
    // Download 의 mac/win/linux 는 UA 가 고르는 상태라 클릭이 없다 — 연결하지 않는다.
  ],
  "04 · Surfaces": [
    ...VP(V3, "3D"),
    [V3, { name: /^Button - Close the sidebar$/ }, V("lnb-collapsed"), "Close the sidebar"],
    [V3, { name: /^Button$/, text: /^›$/ }, V("topic-scope"), "Expand a topic in the sidebar"],
    [V3, { name: /^Canvas$/ }, V("node-detail"), "Click a node on the canvas"],
    ...SHELL(V3),
    ...VP(V("2d"), "2D"), ...VP(V("timeline"), "Timeline"), ...VP(V("weave"), "Weave"),
    [V("topic-scope"), { name: /^Button$/, text: /^Show all$/ }, V3, "Show all"],
    [V("topic-scope"), { name: /^Canvas$/ }, V("node-detail"), "Click a node on the canvas"],
    [V("lnb-collapsed"), { name: /^Button - (Open|Show) the sidebar$/ }, V3, "Open the sidebar"],
    [V("node-detail"), CLOSE, V3, "Close the detail"],
    [V("node-detail"), { name: /^Button - Fold the document panel$/ }, V("docpane-folded"), "Fold the document panel"],
    [V("node-detail"), B(/^Links$/), V("node-float-links"), "Links"],
    [V("node-detail"), B(/^Delete$/), V("node-delete-confirm"), "Delete (confirm)"],
    [V("node-detail"), B(/^Share$/), T("share-sheet"), "Share"],
    [V("node-detail"), B(/^Copy prompt$/), V("node-ask-menu"), "Copy prompt"],
    [V("reader"), B(/^Back$/), V("node-detail"), "Back"],
    [V("reader"), { name: /^Button - Fold the document panel$/ }, V("docpane-folded"), "Fold the document panel"],
    [V("reader"), CLOSE, V3, "Close the detail"],
    [V("docpane-folded"), { name: /^Button - (Unfold|Expand|Open) the document panel$/ }, V("node-detail"), "Unfold the document panel"],
    [V("edge-detail"), CLOSE, V3, "Close the edge detail"],
    ["App · Search / dark", { name: /^Button - Search mode: Find$/ }, T("mode-menu"), "Search mode menu"],
    ["App · Packs / dark", B(/^Add knowledge to Vinc\.$/), "App · Packs · intake-ingesting / dark", "Drop zone"],
    ["App · Packs / dark", B(/^Choose a file$/), "App · Packs · intake-ingesting / dark", "Choose a file"],
    ["App · Packs / dark", B(/^Try the sample pack$/), "App · Packs · intake-success / dark", "Try the sample pack"],
    ["App · Packs / dark", B(/^Preview & save$/), T("share-sheet"), "Preview & save"],
    ["App · Packs / dark", B(/^Create a full backup pack$/), T("share-result"), "Create a full backup pack"],
  ],
  "05 · Overlays": [
    [T("mode-menu"), B(/^Ask$/), T("ask-intro"), "Ask mode"],
    [T("mode-menu"), B(/^Find$/), T("find-results"), "Find mode"],
    [T("ask-intro"), { name: /^Button - Search mode: Ask$/ }, T("mode-menu"), "Search mode menu"],
    [T("ask-answer"), B(/^History$/), T("ask-history"), "History"],
    [T("ask-answer"), B(/^Clear$/), T("ask-intro"), "Clear"],
    [T("ask-answer"), B(/^Close$/), V3, "Close (closes)"],
    [T("ask-history"), B(/^Close$/), V3, "Close (closes)"],
    [T("ask-history"), B(/^Clear$/), T("ask-intro"), "Clear"],
    ...TOOLS(T("tools-create"), "Create"), ...TOOLS(T("tools-import"), "Import"), ...TOOLS(T("tools-share"), "Share"),
    [T("tools-share"), B(/^Continue$/), T("share-sheet"), "Continue"],
    [T("tools-share"), B(/^Cancel$/), V3, "Cancel (closes)"],
    [T("share-sheet"), B(/^Share$/), T("share-result"), "Share"],
    [T("share-sheet"), B(/^Cancel$/), V3, "Cancel (closes)"],
    [T("share-sheet"), CLOSE, V3, "Close (closes)"],
    ...SETTINGS(T("settings-account"), "Account"), [T("settings-account"), B(/^Connect account$/), T("settings-connections"), "Connect account"], [T("settings-account"), CLOSE, V3, "Close (closes)"],
    ...SETTINGS(T("settings-connections"), "Connections"), [T("settings-connections"), CLOSE, V3, "Close (closes)"],
    ...SETTINGS(T("settings-data"), "Data"), [T("settings-data"), CLOSE, V3, "Close (closes)"],
    ...SETTINGS(T("settings-advanced"), "Advanced"), [T("settings-advanced"), CLOSE, V3, "Close (closes)"],
    [T("browse-sheet"), B(/^art$/), T("browse-sheet-docs"), "Open a topic"],
    [T("browse-sheet"), CLOSE, V3, "Close (closes)"],
    [T("browse-sheet-docs"), B(/^‹ All topics$/), T("browse-sheet"), "‹ All topics"],
    [T("browse-sheet-docs"), CLOSE, V3, "Close (closes)"],
    [T("import-pack-sheet"), B(/^Import as mine$/), "App · Packs · intake-success / dark", "Import as mine"],
    [T("import-pack-sheet"), B(/^Cancel$/), V3, "Cancel (closes)"],
    [T("import-doc-sheet"), B(/^(Import|Add|Save)/), "App · Packs · intake-success / dark", "Import the document"],
    [T("import-doc-sheet"), B(/^Cancel$/), V3, "Cancel (closes)"],
    [T("cheatsheet"), { name: /^Button - Close$/ }, V3, "Close (closes)"],
    [T("graphmd"), { name: /^Button - Close$/ }, V3, "Close (closes)"],
    [V("node-float"), B(/^Links$/), V("node-float-links"), "Links"],
    [V("node-float"), B(/^Copy prompt$/), V("node-ask-menu"), "Copy prompt"],
    [V("node-float"), B(/^Share$/), T("share-sheet"), "Share"],
    [V("node-float"), B(/^Details and source$/), V("node-detail"), "Details and source"],
    [V("node-float-links"), B(/^Details and source$/), V("node-float"), "Details and source"],
    [V("node-float-links"), B(/^→$/), V("node-float"), "Follow a link"],
    [V("node-ask-menu"), B(/^Links$/), V("node-float-links"), "Links"],
    [V("node-delete-confirm"), B(/^(Delete|Remove)/), V3, "Delete (closes)"],
    [V("node-delete-confirm"), B(/^(Cancel|Keep)/), V("node-detail"), "Cancel"],
    [V("topic-context-menu"), { name: /^Menu Item$/, text: /^Rename$/ }, V("topic-rename-prompt"), "Rename"],
    [V("topic-rename-prompt"), B(/^Rename$/), V3, "Rename (closes)"],
    [V("topic-rename-prompt"), B(/^Keep$/), V3, "Keep (closes)"],
    [V("topic-rename-prompt"), B(/^Remove from this topic$/), V3, "Remove from this topic (closes)"],
    [V("lens-options-3d"), { name: /^Button - Show explanations$/ }, V("lens-help-open"), "Show explanations"],
    [V("lens-options-3d"), TAB(/^Legend$/), V("lens-legend"), "Legend tab"],
    [V("lens-options-3d"), B(/^2D$/), V("lens-options-2d"), "Viewpoint 2D"],
    [V("lens-options-3d"), B(/^Timeline$/), V("lens-options-timeline"), "Viewpoint Timeline"],
    [V("lens-options-3d"), B(/^Weave$/), V("lens-options-weave"), "Viewpoint Weave"],
    [V("lens-options-2d"), B(/^3D$/), V("lens-options-3d"), "Viewpoint 3D"],
    [V("lens-options-timeline"), B(/^3D$/), V("lens-options-3d"), "Viewpoint 3D"],
    [V("lens-options-weave"), B(/^3D$/), V("lens-options-3d"), "Viewpoint 3D"],
    [V("lens-legend"), TAB(/^Options$/), V("lens-options-3d"), "Options tab"],
    [V("lens-help-open"), { name: /^Button - (Hide|Show) explanations$/ }, V("lens-options-3d"), "Hide explanations"],
    [V("first-run"), B(/^Continue offline$/), V3, "Continue offline (closes)"],
    [V("first-run"), B(/^Sign in$/), T("settings-account"), "Sign in"],
    [V("coachmark"), B(/^Got it$/), V3, "Got it (closes)"],
    [V("new-node-sheet"), B(/^Create$/), V3, "Create (closes)"],
    [V("new-node-sheet"), B(/^Choose a file$/), "App · Packs · intake-ingesting / dark", "Choose a file"],
    // palette → palette-filtered, find-* 결과, search 결과는 타이핑이 만든 상태라 클릭 연결이 없다.
  ],
};
const FLOWS = {
  "11 · Site Screens": [["Site · Landing / dark", "Site · from Landing"], ["Site · Account / dark", "Site · from Account"], ["Site · Auth · Login / dark", "Site · from Login"], ["Site · Auth · Verify · confirm / dark", "Site · from magic link"]],
  "04 · Surfaces": [[V3, "App · from the graph"], ["App · Packs / dark", "App · from Packs"]],
  "05 · Overlays": [[T("mode-menu"), "App · search modes"], [T("tools-create"), "App · Tools"], [T("settings-account"), "App · Settings"], [V("node-float"), "App · node float"], [V("lens-options-3d"), "App · View and legend"]],
};

// ── 실행부 ─────────────────────────────────────────────────────────────────────
const ALL = ["11 · Site Screens", "04 · Surfaces", "05 · Overlays", "06 · States", "07 · Prototypes"];
const pg = Object.fromEntries(ALL.map(n => [n, figma.root.children.find(p => p.name === n)]));
for (const p of Object.values(pg)) await p.loadAsync();
await figma.setCurrentPageAsync(pg[PAGE]);
await figma.loadFontAsync({ family: "Inter", style: "Regular" }); await figma.loadFontAsync({ family: "Inter", style: "Semi Bold" });
const find = (n) => { for (const p of Object.values(pg)) { const x = p.children.find(c => c.name === n); if (x) return x; } };
const textOf = (n) => ("findOne" in n ? (n.findOne(t => t.type === "TEXT")?.characters || "") : (n.characters || "")).trim();
const hot = (board, m) => board.findAll(x => x.type === "FRAME" && !/:(align|margin)$/.test(x.name) && (m.name ? m.name.test(x.name) : /^(Button|Link|Input|Canvas)/i.test(x.name)) && (!m.text || m.text.test(textOf(x))))[0];
const byBoard = new Map(); const missing = [], errors = []; let reactions = 0, crossPage = 0; const seen = new Set();
for (const [b, m, t, label] of TABLES[PAGE]) {
  const board = find(b), dest = find(t);
  if (!board || !dest) { missing.push(`${b} → ${t}: ${!board ? "board" : "target"} missing`); continue; }
  const h = hot(board, m); if (!h) { missing.push(`${b}: ${label}`); continue; }
  if (seen.has(h.id)) continue; seen.add(h.id);
  const samePage = dest.parent === board.parent;
  if (samePage) { try { h.reactions = [{ trigger: { type: "ON_CLICK" }, actions: [{ type: "NODE", destinationId: dest.id, navigation: "NAVIGATE", transition: null, preserveScrollPosition: false }] }]; reactions++; } catch (e) { errors.push(`${label}: ${String(e.message).slice(0, 80)}`); } } else crossPage++;
  if (!byBoard.has(b)) byBoard.set(b, []); byBoard.get(b).push({ h, label, target: t, samePage });
}
let badges = 0;
for (const [b, rows] of byBoard) {
  const board = find(b); board.findAll(x => x.name === "Interaction map").forEach(x => x.remove());
  const map = figma.createFrame(); map.name = "Interaction map"; map.fills = []; map.clipsContent = false;
  board.appendChild(map); if (board.layoutMode && board.layoutMode !== "NONE") map.layoutPositioning = "ABSOLUTE";
  map.x = 0; map.y = 0; map.resizeWithoutConstraints(board.width, board.height); map.locked = true;
  rows.forEach((r, i) => {
    const bx = r.h.absoluteTransform[0][2] - board.absoluteTransform[0][2] + Math.min(r.h.width, 48) - 12, by = r.h.absoluteTransform[1][2] - board.absoluteTransform[1][2] - 12;
    const dot = figma.createEllipse(); dot.resize(24, 24); dot.fills = [{ type: "SOLID", color: r.samePage ? { r: 0.36, g: 0.40, b: 0.85 } : { r: 0.45, g: 0.45, b: 0.5 } }]; dot.strokes = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }]; dot.strokeWeight = 2;
    const num = figma.createText(); num.fontName = { family: "Inter", style: "Semi Bold" }; num.fontSize = 12; num.characters = String(i + 1); num.fills = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
    map.appendChild(dot); map.appendChild(num); dot.x = bx; dot.y = by; num.x = bx + (24 - num.width) / 2; num.y = by + (24 - num.height) / 2;
    const g = figma.group([dot, num], map); g.name = `${i + 1} · ${r.label}`; badges++;
  });
}
// 07 · Prototypes 의 표 — 이 페이지의 보드 것만 지우고 다시 만든다(이름 패턴으로 지우면 다른 페이지 표를 함께 지운다).
const p7 = pg["07 · Prototypes"];
for (const b of byBoard.keys()) p7.children.filter(n => n.name === `Map · ${b.replace(" / dark", "")}`).forEach(n => n.remove());
const existing = p7.children.filter(n => n.name.startsWith("Map · ")).sort((a, b) => (a.y - b.y) || (a.x - b.x));
const last = existing[existing.length - 1]; let tx = last ? last.x + last.width + 60 : 0, ty = last ? last.y : 0;
for (const [b, rows] of byBoard) {
  const f = figma.createAutoLayout("VERTICAL", { name: `Map · ${b.replace(" / dark", "")}`, itemSpacing: 6, paddingTop: 20, paddingBottom: 20, paddingLeft: 24, paddingRight: 24 });
  f.fills = [{ type: "SOLID", color: { r: 0.09, g: 0.09, b: 0.11 } }]; f.cornerRadius = 12; p7.appendChild(f);
  const h = figma.createText(); h.fontName = { family: "Inter", style: "Semi Bold" }; h.fontSize = 16; h.characters = b.replace(" / dark", ""); h.fills = [{ type: "SOLID", color: { r: 0.9, g: 0.9, b: 0.95 } }]; f.appendChild(h);
  rows.forEach((r, i) => { const t = figma.createText(); t.fontName = { family: "Inter", style: "Regular" }; t.fontSize = 13; t.characters = `${i + 1} · ${r.label}  →  ${r.target.replace(" / dark", "")}${r.samePage ? "" : "  (other page, no wire)"}`; t.fills = [{ type: "SOLID", color: { r: 0.75, g: 0.76, b: 0.82 } }]; f.appendChild(t); });
  if (tx + f.width > 4200) { tx = 0; ty += 500; } f.x = tx; f.y = ty; tx += f.width + 60;
}
pg[PAGE].flowStartingPoints = FLOWS[PAGE].map(([b, name]) => ({ nodeId: find(b).id, name }));
return { page: PAGE, entries: TABLES[PAGE].length, boards: byBoard.size, reactions, crossPage, badges, missing, errors };
