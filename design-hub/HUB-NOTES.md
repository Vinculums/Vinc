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

## Figma file inventory (WUXLeDDNBqih2KTZOz9wCX), the new canon after the 2026-08-06 rebuild

The old "owner-fixed four pages" rule was retired on 2026-08-06 when the owner deleted every
existing screen and directed a full rebuild. The detailed record is in
`concepts/figma-2026-08-rebuild.md`.

⚠ `get_metadata` without a nodeId has a bug where it reports **only one** page, so always
confirm the full list through `use_figma` and `figma.root.children`. This bug has produced two
misjudgements already.

The six canonical pages:

| # | Page | id | Content |
|---|---|---|---|
| 0 | **Design System (Homepage)** | `0:1` | the policy frame (306:30), the logo section (157:15, Logo/Mark 91:164), 48 icons (226:5), 14 Home components (231:5) |
| 1 | **Design System (App)** | `53:247` | 21 App components (243:27, pinned to Semantic Dark) |
| 2 | **Homepage** | `40:2` | 44 real-screen frames in five labelled rows (MARKETING, ACCOUNT, AUTH and STATES, DOCS, LIGHT and MOBILE) |
| 3 | **App (Desktop)** | `40:3` | 24 application screen and overlay frames (View, Search and Ask, Packs and Intake, Settings, Overlays, Web skin) |
| 4 | **Node** | `184:2076` | five graph examples: 3D dark (299:30), 3D light (300:30), 2D (301:30), Timeline (302:30), Sankey (303:30) |
| 5 | **Email** | `219:5` | the email design system (251:27) plus 14 templates (7 kinds in English and Korean) |

Seven variable collections, all new, with the old three deleted: Primitive (221:5), Semantic
(222:5, Light and Dark, matching the source-of-truth semantic names), Graph (223:5, linkRamp
plus viewScene), Domain (223:27), Window (223:35), Email (223:45) and Scale (224:5, with icon
18 and 24 newly added).

The font is **settled as Pretendard**; Inter stands in until it is installed, after which the
text styles' fontName is replaced in bulk. The full node id map is in the session scratch
`figma-node-map.md` and is to be ingested into the Vinc graph.

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

