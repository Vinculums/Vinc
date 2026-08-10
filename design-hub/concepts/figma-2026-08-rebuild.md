# The 2026-08 Figma rebuild, the node map

File: WUXLeDDNBqih2KTZOz9wCX

The record of what was built during and after the full rebuild of 2026-08-06. It is a node
map: the identifiers below are what a plugin call has to address, so they are reproduced
exactly and nothing here is rounded or paraphrased.

## Pages (2026-08-06)
| Page | Node id | Purpose |
|---|---|---|
| Design System(Homepage) | 0:1 | the public site design system (the logo section 157:15 preserved) |
| Design System(App) | 53:247 | the application design system |
| Homepage | 40:2 | the real homepage screens |
| App(Desktop) | 40:3 | the real application screens |
| Node | 184:2076 | four graph examples (3D/2D/Timeline/Sankey) |
| Email | 219:5 | the email design system plus 14 templates (new) |

## Nodes preserved from before
- Logo / Mark component: 91:164 (canonical, the two-stroke V)
- Logo section: 157:15 (01 · Logo)
- Logo dark sample: 91:169

## The old variable collections (reorganized in Phase 1)
- `color` VariableCollectionId:1:2 (18 variables, Light/Dark), previous generation, to be cleared
- `App · Deep Signal (draft)` VariableCollectionId:123:168 (10 variables, Dark)
- `Home · Signal (draft)` VariableCollectionId:136:167 (11 variables, Light/Dark)

## Fonts
- ~~Pretendard is absent from the Figma list (confirmed 2026-08-06), the owner has to install it~~
- ~~Text styles were created on Inter as a stand-in. Once Pretendard is installed, run a script to
  swap fontName across the styles in one pass~~
- **Resolved (measured 2026-08-08).** Pretendard is installed and the text styles actually use it.
  The live style set also differs from the record below: Display/Bold 44/52 · H1/Bold 32/38 ·
  H2/Bold 24/30 · Lead 17/26 · Body 15/23 · MD 14/21 · SM 13/20 · Caption 11/16.
  Evidence: `imports/figma-variables-2026-08-08.json`

## Phase 1 output (2026-08-06)
### Variable collections (the old three deleted: color 1:2, App Deep Signal 123:168, Home Signal 136:167)
- `Primitive` VariableCollectionId:221:5 (30 colours, single mode, hidden from the picker)
- `Semantic` VariableCollectionId:222:5 (24 roles, Light 222:0 / Dark 222:1, aliases of primitives)
- `Graph` VariableCollectionId:223:5 (21: link/5, label/3, node/3, scene fill and stroke 6, plus glowOpacity 4)
- `Domain` VariableCollectionId:223:27 (dom-1 to dom-6, dom-other, Light/Dark)
- `Window` VariableCollectionId:223:35 (w-* 9 colours, constant in dark)
- `Email` VariableCollectionId:223:45 (email/* 9 colours, constant in dark)
- `Scale` VariableCollectionId:224:5 (radius 4, space 7, fs 5, icon 2 new, layout 2)

### Semantic variable ids (for binding)
background 222:6 · foreground 222:7 · card 222:8 · card-foreground 222:9 · muted 222:10 · muted-foreground 222:11 · caption-foreground 222:12 · border 222:13 · input 222:14 · ring 222:15 · primary 222:16 · primary-foreground 222:17 · accent 222:18 · accent-foreground 222:19 · link 222:20 · info 222:21 · info-strong 222:22 · info-foreground 222:23 · success 222:24 · success-foreground 222:25 · warning 222:26 · warning-foreground 222:27 · danger 222:28 · danger-foreground 222:29 (all carry the VariableID: prefix)

### Effect styles
shadow/soft · float · elev-1 to elev-3, glass/panel (bg blur14), glow/focus · near · mid · far (graph nodes only)

### Text styles (Inter as a stand-in, awaiting Pretendard)
Display 56/64 Bold · XL 36/41 Bold · LG/Semi Bold 17/26 · Base/Regular and Medium 15/23 · Base/Prose 15/26 · SM/Regular and Medium 13/20 · Mono/SM 13/20 (JetBrains Mono) · Email/H1 23/32 Semi Bold

### New policies (to be documented in Phase 8)
1. The font-weight scale: body 400 / label and emphasis 500 / headings, buttons and active 600 / display and brand 700 (the GNB's 680 maps to 700)
2. icon tokens: icon/app 18px, icon/base 24px
3. The canonical radius is the SSoT (the landing page's inline drift is retired)
4. fs-xl and display are written as the values resolved at 1440px (36/56)

## Phase 2 output (2026-08-06)
- Icon section `02 · Icons`: 226:5 (page 0:1, x80 y320)
- All 45 icons are Icon / <name> components bound to the foreground variable:
  alert 226:10 · arc 226:14 · bar-group 226:20 · caret 226:23 · check 226:26 · chevron-left 226:29 · chevron-right 226:32 · comet 226:37 · dash-ramp 226:42 · decide 226:46 · disc 226:49 · domains 226:54 · dot-grid 227:15 · dot 227:18 · download 227:23 · dumbbell 227:28 · edit 227:31 · ellipse 227:34 · graph 227:40 · half-disc 227:44 · hourglass 227:48 · ingest 227:54 · lock 227:58 · login 227:63 · onboarding 228:9 · pack-export 228:15 · pause 228:19 · pendulum 228:24 · pill-v 228:27 · pill 228:30 · pin 228:34 · play 228:37 · recall 228:48 · ring 228:51 · search 228:57 · settings 228:65 · sparkle 229:7 · stack 229:12 · sync 229:18 · tag 229:22 · undo 229:26 · upload 229:31 · x 229:34 · pack-in 229:39 · share-nodes 229:45
- The logo rules card: 230:5 (inside section 01 · Logo, the section widened to 1100)
- Additional UI icons: globe 235:8 · sun 235:12 · moon 235:15 (for the GNB)
- SCALE constraints applied to the vectors inside logo 91:164, so it holds up when scaled down

## Phase 3 output: the Home set (section 03 · Components (Home) = 231:5, page 0:1)
- Home / Button 231:10 (Kind=Primary 231:6 / Quiet 231:8, label#231:0)
- Home / Badge 232:14 (Tone=Info/Success/Ring, label)
- Home / Chip 232:21 (Status=Done/Now/Neutral, label)
- Home / Toggle 234:9 (State=On/Off)
- Home / Input 234:14 (State=Default/Focus, text)
- Home / Seg 234:25 (Active=A/B, labelA/labelB)
- Home / GNB 237:5 (firstLink prop, bg .82 with blur, 6 links, globe plus the theme segmented control)
- Home / Footer 237:32 (© 2026 Vinc · MIT License plus 5 links)
- Home / LangModal 238:16 (English active and checked, 한국어)
- Home / Banner 238:30 (Kind=Notice/Trial, text)
- Home / Card 240:17 (feature card, title/desc, icon swapped by hand)
- Home / CommunityCard 240:25 (title/author/stats plus the domain bar)
- Home / AuthCard 241:37 (Tone=Default/Danger, title/sub, slot)
- Home / ModalCard 241:38 (title, slot, actions)

## Phase 3 output: the App set (section 03 · Components (App) = 243:27, page 53:247, pinned to Semantic Dark)
- App / Button 243:36 (Kind=Primary/Ghost/Quiet/Danger, label)
- App / Chip 243:41 (State=Default/Active, label)
- App / NavItem 244:41 (State=Default/Active, label, icon swap)
- App / DomainRow 244:52 (State, name/count)
- App / DocRow 244:62 (Ink=1/2/3, title)
- App / ListRow 244:71 (State=Default/Hover, mark/title/meta)
- App / StateMark 245:47 (State=Full/Half/Empty ● ◐ ○)
- App / ProgressBar 245:48 (fill width overridden)
- App / ErrorBanner 245:50 (text, prefixed △)
- App / EmptyState 245:52 (title/desc plus cta-slot)
- App / Tooltip 245:66 (text, glass)
- App / Sheet 246:83 (Size=Action 480x380 / Write 832x560 / Browse 980x620, title/subtitle, head/body/foot)
- App / ViewSeg 246:120 (Active=3D/2D/Timeline/Sankey)
- App / Slider 246:121
- App / MenuItem 246:131 (Tone=Default/Hover/Danger, label)
- App / GraphNode 247:61 (Tier=Focus/Near/Mid/Far, glow effect, the only glow)
- App / PackCard 247:70 (State=Default/Highlight, name/counts/meta)
- App / FloatCard 247:71 (title, slot, a 2px accent bar on the left, glass)
- App / UpdateToast 247:89 (State=Available/Downloading/Installing/Failed)
- App / Titlebar 250:53 (1280x34)
- App / Topbar 250:66 (1280x48, actions left · search centre · slot right)

## Phase 3 output: the Email set (section 00 · Email Design System = 251:27, page 219:5)
- Email / BrandRow 251:28 (V #C7CAF0 plus the wordmark)
- Email / CTA 251:34 (label)
- Email / Chip 251:36 (text)
- Email / Frame 251:38 (h1/body/fine plus slot, 640 body · 480 card · pinned dark)

## Phase 4 output: the Homepage screens (page 40:2, all pinned to dark, x pitch 1560)
- Home · Landing / dark 252:27 (x0: GNB, hero, the vshell mock-up with a graph placeholder, how, features, packs, vows, fin (always indigo), the newsletter in two states, footer)
- Home · Download / dark 255:131 (x1560)
- Home · Pricing / dark 256:174 (x3120: the trial banner, the annual/monthly segmented control, three tiers, FAQ)
- Home · Community / dark 258:231 (x4680) plus Pack modal states 258:353 (y2900, four steps)
- Home · Release / dark 260:337 (x6240: v0.7.4 plus roadmap chips)
- Home · Privacy / dark 260:418 (x7800: six EN sections plus a header carrying KO alongside)
- Home · Auth · Login & Welcome 261:416 (y4700: LOGIN form/sent plus WELCOME loading/form/available)
- Home · Auth · Verify states 262:438 (y5600: six states)
- Home · Auth · Subscribe & Unsubscribe 263:462 (y6500: 5 plus 5 states)
- Home · Account / dark 264:502 (x9360: seven acard variants, the key shown once)
- Home · Account · Team / dark 266:550 (x10920: accept/create/teamView/invite plus candlist/danger)
- Home · Account · Delete states 266:651 (y7500: five states)
- Home · Connect & Connect-app states 267:615 (y8400: 5 plus 6 states, kv plus the spoofing warning)
- Home · 404 / dark 268:657 (x12480, new)
- Home · Service states 268:709 (429/unavailable/signed-out, new)
- Home · Mobile GNB drawer 268:746 (390px, new)
- 15 Docs frames (y9500, x 0 to 21840 at a pitch of 1560): Overview 269:714 · Quickstart 269:807 · Use-cases 269:903 · Import 270:825 · Atlas 270:921 · Search 270:1017 · Packs 271:936 · Shortcuts 271:1032 (the shortcut table) · MCP Setup 271:1149 (host cards) · MCP Tools 273:1047 · CLI 273:1139 · GRAPH.md What 273:1233 · Generate 274:1158 · Spec 274:1250 · Validate 274:1338
- ~~four representative light frames~~ (the old light landing and download were deleted along with the rebuild)

## Rebuilt against the real screens (2026-08-06, after analysing vincs.io with Playwright)
- Deleted: the old Landing dark and light, Download dark and light, Pricing, Community
- **Home · Landing / dark 278:1051** (new): a two-tone headline (the second line in the link colour) with the logo above, the orbit visual (orbit 278:1099: a centre card circle, the logo, radial lines, satellite dots and four icon tiles), the application mock-up vshell 279:1103 (a decorative mini graph, the icon rail, the Hybrid search card, the 2D/3D pill, domain chips), Three clicks (numbered cards 01 to 03), No setup maze (six icon tile cards), packs (an OFFICIAL PACK kicker), vows as a single large card (two columns of round icons), fin centred (the indigo band retired: logo, heading, a Connect it link and a Download button), the newsletter centred
- **Home · Download / dark 282:1189** (new): a centred hero (64 logo, the version line, the detection sentence), a notice banner (a round info icon), platform cards with full-width format buttons (.dmg and .exe indigo, .deb quiet), the Windows card highlighted with the ring
- **Home · Pricing / dark 283:1241** (new): a centred hero with the segmented control, prices ₩0 / $79 / $120 (as the live site writes them), an inline Popular pill, a full-width CTA, FAQ
- **Home / CommunityCard component v2 284:27** (the old 240:25 deleted): a graph thumbnail, an OFFICIAL pill, a domain kicker (◆), four tag chips, imports and an Import button. props: title#284:0 author#284:1 stats#284:2 domain#284:3 imports#284:4
- **Home · Community / dark 285:1296** (new): a two-tone heading, a Share your own pack button, full-width search with a Recent sort, cards 2x2
- 16 Docs frames (15 plus a light one): the kicker (section name in caps, in the link colour) and the separator are patched
- New light frames: Landing light 286:1581 · Download light 286:1800

## The canonical layout of the Homepage page (40:2), five row labels
- Row1 MARKETING y0: Landing(0) Download(1560) Pricing(3120) Community(4680) Release(6240) Privacy(7800) 404(9360)
- Row2 ACCOUNT y5400: Account(0) Team(1560) Account-light(3120)
- Row3 AUTH·STATES y9000 onward: Login&Welcome 9000 · Verify 9950 · Sub/Unsub 10900 · Delete 12400 · Connect 13350 · Service 15000 · PackModal 15700
- Row4 DOCS y17000: 15 plus a light one, x pitch 1560
- Row5 LIGHT·MOBILE y19600: Landing-light(0) Download-light(1560) MobileGNB(3120)

## Phase 5 output: the App screens (page 40:3 App(Desktop), pinned dark, 1280x800)
### Row1 Atlas y0
- Atlas default 287:30 (the canonical shell: titlebar, topbar with the lens and view segmented control, 232 left navigation, canvas placeholder, metric bar, lasso hint)
- Atlas empty 289:98 (x1560) · lens panel (Options) 289:221 (x3120) · coachmark and lasso 289:373 (x4680)
- node detail and related 290:311 (x6240) · edge detail 290:412 (x7800) · reader 290:468 (x9360) · Web skin 297:2681 (x10920)
### Row2 Search·Ask y1000
- results 291:494 · no results and reindex 291:617 (x1560) · error and browse 291:728 (x3120) · Ask answer and disambiguation 291:849 (x4680)
### Row3 Packs·Intake y2000
- Packs (including dropzone 42:3) 292:715 · Intake states (Ingesting 42:5 / Success 42:7 / Error) 292:859 (x1560)
### Row4 Settings y3200
- Account tab (dim plus modal) 294:803 · Connections/Data/Advanced 294:898 (x1560) · device flow, five states 294:986 (x4200)
### Row5 to Row6 Overlays y4400/5600/6800
- Overlays1 (Share/Destination/Import pack/Confirm/Prompt) 295:881
- Overlays2 (Tools Create/GRAPH.md/Doc editor conflict) 296:891
- Overlays3 (palette / context menu / tooltip / four toasts / Ask history / Cheatsheet) 297:904

## Phase 6 output: the Graph examples (page 184:2076 Node, 1200x800)
- Graph · 3D · dark 299:30 (x0: an oblique camera projection, domain spheres and torus rings, focus and near glow, hexagonal records, diamond bridges, depth-faded edges, four hub labels)
- Graph · 3D · light 300:30 (x1300: matte ink D77, focus in indigo)
- Graph · 2D · dark 301:30 (x2600: the sigma fan layout, four edge tiers with arrowheads, hub badges, minimap and viewport)
- Graph · Timeline · dark 302:30 (x3900: root, neck and three branches, circle = concept and hexagon = document, leader labels, causal béziers (dotted where they run backwards), month ticks, caption pills)
- Graph · Sankey · dark 303:30 (x5200: a three-column alluvial, a path-preserving focus ribbon (peri), ink .82 strata with a bg hairline, label plates, 2 more topics)

## Second revision (2026-08-06: reproducing the graph visuals precisely, the background, and switching inside one frame)
- **Home / PageBackground 313:30** (page 0:1, section 231:5): four aurora blobs (the radial-gradient values taken from the code), halftone dots (pitch 19, the single-vector ROUND-cap dot trick, ring α.10), a cursor boost spot (rest 50%/42%, larger dots with a radial mask) and a spotlight. The NOISE effect is omitted because the plugin API refused validation; it can be added by hand. PATTERN paint was also confirmed unsupported by this file's runtime
  - an absolute instance was inserted into the seven marketing frames (landing, download, pricing, community, release, privacy, 404)
- **Orbit rebuilt 315:1753**: index.html coordinates 1:1 (400vb ×1.12). Guide circles r118/168 dash 2 8, ten spokes (even ring/.38 · odd mutedFg/.32), four chips plus recall as the fifth, five dots, three signals, hub glow r44 plus hub r30 plus logo 32 (all tinted with ring)
- **The vshell mock-up rebuilt (inside 279:1103)**: code coordinates 1:1 (720x400 sliced at ×1.164). 12 edges plus 3 highlighted, nodes plain 9 / pt 3 / sel r13 with halo r20 dash 4 5, two signals, a rail of 6 icons (graph active = point 18%), the crumb and the 2D/3D chip inside the canvas, three legend dot colours, the detail card (dot plus lines at 88/64/76%)
- **Community thumbnail refreshed** (284:28): a wide constellation of 22 nodes and 22 edges
- **Locale collection VariableCollectionId:318:30** (EN 318:0 / KO 318:1): 70 string variables (landing/* mock/* gnb/*), binding the 68 landing texts plus the GNB's five main links. **Switching language inside one frame was verified** (a KO screenshot). The limit: labels driven by a component TEXT property (the button CTAs) cannot be bound to a variable, so they stay EN
- **Dark and light work the same way**: select the frame, switch the Semantic mode. The four duplicated light frames were deleted (Landing light, Download light, Docs Overview light, Account light)
- The Home/Card instances on the landing page (steps and features) were detached so they could be bound
- ~~Pretendard is still not exposed (Figma needs a restart), swap procedure in scratchpad/pretendard-swap-plan.md~~
  Superseded on 2026-08-08: see Fonts above, Pretendard is installed and in use
