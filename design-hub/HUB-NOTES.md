# Vinc Design Hub, implementation notes (phase 0 output)

These are the working notes for the Vinc design system overhaul. The approved plan is at
`~/.claude/plans/vinc-zesty-bird.md`. This hub is the **single token source** for three
consuming surfaces: `Vinc/app`, `Vinc-homepage/site`, and email.

## Structure

```
design-hub/
├── design.profile.yml               # instance profile (name: vinc-design, dark_selector: [data-theme="dark"])
├── DESIGN/
│   ├── design-tokens.json           # the DTCG source of truth
│   ├── tokens.css                   # generated (gen_tokens), never edited by hand
│   └── DESIGN.md                    # documentation scaffold
├── imports/
│   └── legacy-ink-paper.tokens.json # a reference copy of the old Ink and Paper tokens (repaired), for phase 3 mapping
├── tenants/desktop/design/          # the separate desktop theme override (primitive leaves only)
├── dist/{app,site}/                 # post-processed output for the consuming repositories (scope_tokens, gen_theme_ts, sync_out)
└── reports/                         # ds-gate output
```

The engine is the lime-design plugin at `${CLAUDE_PLUGIN_ROOT}/scripts/*.py`. On Windows
always use `python -X utf8`. Claude Code has to run **from the hub directory** or the skills'
relative paths do not resolve.

## ⚠ The engine contract (confirmed by experiment in phase 0, required reading for phase 3)

Constraints confirmed by testing `_meta/design_rules/token_gen.py::render_css` directly:

1. **The consumed layers are primitive and semantic, and that is all.** The `component` layer
   **is not read**. Of the old Ink and Paper's three layers, the component layer and the
   per-semantic `$description` rules are preserved **only in DESIGN.md**, not in the engine
   source of truth.
2. **Semantic values must be flat strings**: `"background": "{paper}"`. Using the DTCG object
   form as the old file did (`"background": { "$value": "{paper}", "$type": ... }`) crashes
   `gen_tokens` with `AttributeError: 'dict' object has no attribute 'lstrip'` (demonstrated:
   generating from `imports/legacy-ink-paper.tokens.json` exits 1). So the semantic layer of
   the new source of truth follows the **starter schema of flat strings**, and
   `$value`, `$type` and `$description` annotations go on the **primitive layer** only.
3. **A semantic reference reaches a primitive in one step.** `_resolve` looks up only
   `prim[ref_name(v)]`, so a chain like `"{semantic.primary}"` or `"{component.x}"` raises
   KeyError. Every semantic points directly at a primitive.
4. **The generated groups are fixed**: `radius` (DEFAULT becomes `--radius`, everything else
   `--radius-{k}`), `space` becomes `--space-{k}`, `fs` becomes `--fs-{k}`, `lh` becomes
   `--lh-{k}`, `font` (heading, ui, mono) becomes `--font-{k}`, `shadow` becomes
   `--shadow-{k}`. **Keys within a group are free**, so `"fs": {"0": "...", "1": "..."}`
   generates `--fs-0` and `--fs-1`.
5. **What the engine cannot emit**, and therefore needs a hub custom script:
   - `--icon-size` (there is no arbitrary group; work around it by putting an `"icon"` key in
     `space` to get `--space-icon`, or post-process)
   - the community `--dom-*` category palette
   - the View WebGL scene shades (heavy, mid, base, cross, fade and so on)
   - the homepage application mockup `--w-*`
   - web and desktop skin scoping (`:root[data-skin="web"]`), through `scope_tokens.py`
   - the hex integer module for three.js and Sigma, through `gen_theme_ts.py`
   - validate is lenient and passes even the object form (the reference count is fooled and
     only leaves an "unused primitive" INFO), so **whether gen passes is the real schema
     gate**.

## Token naming mismatch in app.css (to reconcile in phase 5)

- app.css references `--fs-0`, `--fs-1`, `--lh` and `--icon-size`, while the tokens currently
  generate `--fs-sm`, `--fs-base`, `--fs-lg` and `--lh-normal`. In phase 5, either set the
  source of truth's fs and lh keys to numbers (0, 1, 2 and so on) and add a `--lh` alias for
  normal, or rename in app.css. A decision is needed.

## Phase 0 completion state

- The old source of truth `Vinc-docs/DESIGN/design-tokens.json` had its syntax repaired at
  line 74 and is valid JSON (23 keys each for light and dark).
- The hub was cloned from an empty remote, scaffolded, and the starter gen, scaffold and
  validate all exited 0 and passed.
- The engine schema contract was confirmed by experiment (above).
- The old source of truth is **legacy-frozen**: it exists to keep `app/src/tokens.css`
  reproducible until phase 5 replaces the application tokens. Never edit it by hand.

## Figma is kept level with the code by a gate now (2026-09-05)

The owner settled the direction: **code is canonical, Figma follows it.** Until this date
nothing checked that, and the cost showed up the first time anyone looked: four semantic
variables and two primitives were missing, the whole motion group and the dark shadows had
no counterpart, and dark `primary`/`primary-foreground` held **different values** from the
source of truth while their own Figma descriptions still recorded the code's. All of it is
applied now, and the drift is 0.

```bash
# 1. dump live Figma (the agent runs the JS in the script's docstring through use_figma)
#    into imports/figma-live.json, then:
python -X utf8 scripts/figma_diff.py                    # exit 1 = Figma is behind
python -X utf8 scripts/figma_diff.py --plan reports/figma-plan.json
```

The name map between the two sides lives in `scripts/figma_diff.py` and nowhere else, on
purpose: there is one consumer, so a second file would only give the two something to
disagree about. What is deliberately **not** compared is listed in the same docstring, and
that list is the contract, not an oversight: `Graph`/`Window`/`Email` reach code only
through `gen_theme_ts.py`, `Locale` is copy whose canon is the site, and `Domain` uses a
fixed palette in Figma against a generative model in code.

⚠ `imports/figma-live.json` is a machine dump, not a note. Patching it by hand makes the
gate agree with a file instead of with Figma, which is the one failure it exists to catch.

⚠ Four `Scale` keys are Figma-only and are **not** Figma being wrong: `icon/app` 18,
`icon/base` 24, `layout/lnb-w` 232, `layout/gnb-h` 52 all match values that live in
`app/src/app.css` rather than in the token SSoT. Promoting them into the SSoT is a separate
decision; until then they stay on the ignore list.

## The second gate: the lists, not the values (2026-09-05)

`figma_diff.py` answers "do the two sides hold the same values". It cannot answer "does Figma
have a frame for every screen we ship", and that question had been sitting in the plan marked
"not compared" for weeks. Counting it by hand once found four application modals and five
documentation pages missing. Counted by hand, it would be wrong again next week.

```bash
python -X utf8 scripts/figma_inventory.py     # exit 1 = 코드에 늘려 놓고 Figma 대응을 선언 안 했다
```

It compares three lists, each extracted from the tree rather than typed out: **site routes**
(every `index.html` under `Vinc-homepage/site`), **application modals** (every `openSheet`,
`confirmDestructive`, `promptText` and `openContextMenu` call site, keyed by module plus title
expression so moving code around does not break the key), and **icons** (`app/src/icons/*.svg`
plus the site's icon module).

The distinction that makes it survivable: a **declared gap** is written as `None` in the map,
prints every run and does not fail; an **undeclared** item fails. Failing on the backlog would
have got the gate switched off, and passing silently would restore exactly the state it exists
to end. Draw the missing frame, replace the `None` with its name, and from then on the gate
verifies the frame really exists.

⚠ `components.ts` is where those four helpers are **defined**. Counting it as a call site
invents four modals that do not exist, so it is excluded by name and a test holds that.

### What the gate reports today (2026-09-05, end of the sync campaign)

```
사이트 라우트     결손 0
앱 모달          결손 0
아이콘           결손 0
앱 표면 상태      선언된 상태 27 · 결손 0
화면 상태        선언된 상태 81 · 결손 0 · 미선언 0
앱 컴포넌트       대조된 변형 46 · 실패 0
```

The last line is a different kind of check from the four above it, added 2026-09-06. Those
count: is the thing there. It corresponds: every desktop component variant is bound to the
mark in `app/src` that defines it, and the gate fails when that mark is gone. The axis exists
because a comparison of all 21 desktop components found four disagreements that every counting
gate passed, since the counts were right and only the names were wrong: `DocRow` numbered its
ink scale 1/2/3 where the code inlines `--ink: 0|1|2`, `StateMark` said `Full` where the
function signature says `filled`, `PackCard` carried a `Highlight` state with nothing behind it,
and `Chip` was missing the `.chip-static` the stylesheet has. Values, colour and structure
agreed everywhere; naming was the whole of the drift.

Getting there meant building what code had and Figma did not: seven documentation
screens, four application overlays, nineteen icons, the Weave graph example, and nine
account/team/community states. It also meant rebuilding the landing screen to the live
layout and moving copy to the live values across eleven screens. Every screen frame carries
its Vinc address as a Dev Mode annotation and all 39 components carry theirs in the
`description`, both written in this repo's double-bracket citation syntax (`[[` id `]]`).

Two axes the file had nothing for now exist: a `Flows` page holding five user journeys with
their branches, and real prototype connections on the application page, which had **zero**
across 2,160 nodes while the homepage had 102.

⚠ **CI does not have `Vinc-homepage`** — a job checks out its own repository only. The route
comparison then cannot be made at all, and getting that wrong fails in both directions: feed
the empty set in as the code list and all 38 mapped routes read as "deleted from code", so the
gate is permanently red; pass silently and "we checked" is a lie. `sync_out.py --check` met
this first and reached the same answer: **skip it, and print that you skipped**. The first CI
run of PR #217 was red on exactly this line while local was green, so a test now reproduces
the condition rather than trusting that it will not recur.

## Figma file inventory (WUXLeDDNBqih2KTZOz9wCX), the new canon after the 2026-08-06 rebuild

The old "owner-fixed four pages" rule was retired on 2026-08-06 when the owner deleted every
existing screen and directed a full rebuild. The detailed record is in
`concepts/figma-2026-08-rebuild.md`.

⚠ `get_metadata` without a nodeId has a bug where it reports **only one** page, so always
confirm the full list through `use_figma` and `figma.root.children`. This bug has produced two
misjudgements already.

⚠ The table below says six pages and it is **wrong**: there are seven. `Homepage(Docs)`
(`337:2590`) was added later and never written down here, which is what a hand-maintained
inventory does. Measured 2026-09-05 through `figma.root.children`, which is also how the
`get_metadata` bug below is worked around.

The six canonical pages:

| # | Page | id | Content |
|---|---|---|---|
| 0 | **Design System (Homepage)** | `0:1` | the policy frame (306:30), the logo section (157:15, Logo/Mark 91:164), 48 icons (226:5), 14 Home components (231:5) |
| 1 | **Design System (App)** | `53:247` | 21 App components (243:27, pinned to Semantic Dark) |
| 2 | **Homepage** | `40:2` | 44 real-screen frames in five labelled rows (MARKETING, ACCOUNT, AUTH and STATES, DOCS, LIGHT and MOBILE) |
| 3 | **App (Desktop)** | `40:3` | 24 application screen and overlay frames (View, Search and Ask, Packs and Intake, Settings, Overlays, Web skin) |
| 4 | **Node** | `184:2076` | five graph examples: 3D dark (299:30), 3D light (300:30), 2D (301:30), Timeline (302:30), Sankey (303:30) |
| 5 | **Email** | `219:5` | the email design system (251:27) plus 14 templates (7 kinds in English and Korean) |

Variable collections: Primitive (221:5), Semantic (222:5, Light and Dark, matching the
source-of-truth semantic names), Graph (223:5, linkRamp plus viewScene), Domain (223:27),
Window (223:35), Email (223:45) and Scale (224:5, with icon 18 and 24 newly added). Two more
have appeared since: **Locale** (318:30, EN and KO, 376 copy strings) and **Motion**
(477:2571, created 2026-09-05 to carry the SSoT's curves and durations).

The font is **settled as Pretendard and the swap is still not done.** Measured 2026-09-05, and
the two halves disagree, which is why a glance at the text styles misleads:

| Layer | State |
|---|---|
| The 22 text styles | all declare Pretendard |
| App(Desktop) screen text | 784 nodes: **Inter 778 · JetBrains Mono 6 · Pretendard 0** |
| Font availability to the MCP session | Pretendard is **not loadable** (0 of 1,938 families) |

So the styles were repointed at Pretendard but the screens were never re-rendered onto it.

**Why the family is missing here and not in your Figma.** The MCP session executes against
Figma's hosted font catalogue only: 1,938 families containing the Google set (Noto Sans KR,
Nanum, Gothic A1) and Figma's own bundles (Inter, SF Pro, JetBrains Mono), and **no
OS-installed font at all** (no Malgun Gothic, no Segoe UI, no Apple SD Gothic). Pretendard is
not a Google font, so the desktop app and the browser resolve it from the machine it is
installed on while this environment cannot see it. The file is fine; what breaks is only an
agent-side `characters` write, which fails with `The font family "Pretendard" does not exist`.

**The way around it, and it is the better way anyway.** Text bound to a variable is fixed at
the variable, and a variable write needs no font. Both nodes that blocked the F8 purge turned
out to be bound to `Locale` entries (`mock/view`, which was renamed in the same pass because
its key carried the retired word too, and `landing/s2t`), so setting those values cleared them.
That is also the route for the whole 376-string copy sync: it does not need the font, and it
fixes the copy at its source instead of in each frame. Only genuinely detached text on a
Pretendard style is unreachable from here.

The full node id map is in the session scratch `figma-node-map.md` and is to be ingested into
the Vinc graph.

## Next

Phase 1, the mood board and drafts, awaits the user's reference material. After that, phase 2
builds the Figma library and phase 3 settles this hub's source of truth through `/ds-import`.
Make the Figma variable names match the semantic token names exactly, because the import is a
manual mapping.

## Exporting to Claude Design (settled 2026-08-08)

```bash
python -X utf8 scripts/push_ssot.py     # gate plus build, producing dist/claude-design/tokens/ssot.css
# then plan a DesignSync upload with writes: ["tokens/ssot.css"] and nothing else
```

**`/ds-push` is not used as it is.** That skill's direct path is, as it says itself, for a
tokens-only hub, and it replaces `styles.css` with a single line
(`@import "./tokens/tokens.css"`). The Vinc project is not tokens-only: it has some forty
components, three templates, two ui_kits and 23 guideline cards, and its `styles.css` pulls in
six files under `tokens/`. Overwriting it pushes those six outside the import closure and
**every component and card loses its variables.**

Fixing lime-design would not solve it either, because two of the `tokens/` files are ones the
hub **cannot produce in principle**:

| File | Content | Hub |
|---|---|---|
| `tokens/fonts.css` | six `@font-face` rules (self-hosted Pretendard and JetBrains Mono) | no, it emits only `--font-*` name stacks |
| `tokens/base.css` | the reset (box-sizing, body, ::selection, :focus-visible, accent-color) | no, not a token generator's business |

So **the paths were split**. Hub output goes to `tokens/ssot.css` alone, and the project's
`styles.css` imports it **last**, so when both define the same value the source of truth wins.
A push touches exactly one file, which puts the cards, components and templates out of range.
`styles.css` was edited once on 2026-08-08 and is not touched again.

⚠ If `guidelines/`, `components/`, `templates/` or `assets/` appear in finalize_plan's
`writes`, that is an accident. The list must always be exactly `["tokens/ssot.css"]`.

## Motion tokens (2026-09-05)

The engine cannot emit a motion group (§5 above), so the hub carries one custom step:

- SSoT: `DESIGN/design-tokens.json` gains a `motion` group (flat strings, one `note`).
  `--ease-out` / `--ease-in-out` / `--ease-drawer` / `--ease-settle`, `--dur-fast` /
  `--dur-hover` / `--dur-base` / `--dur-in` / `--dur-out` / `--dur-panel`. Same values for
  every skin.
- `scripts/gen_motion.py` injects the block before `/* Shape */` in the **dist** outputs only.
  `DESIGN/tokens.css` stays pure engine output so `gen_tokens --check` keeps passing.
- `scripts/build_dist.py` fixes the order (engine base, engine tenant, copies, scope, motion,
  theme.ts). Run it, then `scripts/sync_out.py --check|--apply`. `build_dist.py --check` runs
  the two engine `--check`s.
- Found while wiring this: `tenants/desktop/design/tokens.css` and the app copies had never
  received the dark `Depth` overrides that the SSoT already had; the first `build_dist` run
  brought them across (4 `--shadow-*` lines under `[data-theme="dark"]`).
- Consumers no longer define motion tokens locally (`app/src/app.css`, homepage `site.css`).
  The app's large surfaces use `--ease-drawer` (the old local `--ease-out` value); small UI
  uses `--ease-out`; `app/src/motion.ts` reads `--dur-in`, `--dur-out`, `--ease-settle`.

