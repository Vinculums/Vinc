# Homepage draft: Home · Signal (v1.1, 2026-07-19)

The new design for the homepage, and for the web build of Vinc that will follow it.
**Dark first, settled by the owner**: the default is Dark mode in the Deep Signal lineage,
a `#0C0D13` family background with a periwinkle accent. Light mode is derived from the logo
reference (ink on grain grey with an indigo tip) as "grain grey, ink, indigo" and is the
**secondary mode for the theme toggle**. Logo, homepage and desktop application are one
bloodline.

All seven Figma screens rebind colour to variables down to the screen level (166 nodes), and
the frames carry `setExplicitVariableModeForCollection(Dark)`, so switching the mode alone
shows the light draft immediately. The constants that ignore the mode are the application
mockup (always dark), the final CTA band (always indigo) and the Guide mini visuals (always
dark).

The old Ink and Paper assets were **deleted entirely** from the `Homepage (Design system)`
page: the Foundations swatches, the Notation icon showcase and the old logo exploration.
Preserved: the owner's canonical logo (78:164), the `Logo / Mark` component (91:164) and the
dark sample. The Notation icon **main components** (9:88 and others) live off canvas, so the
application sidebar references are safe.

## Figma variables, the `Home · Signal (draft)` collection (Light and Dark modes)

| Token | Light | Dark | Use |
|---|---|---|---|
| bg/page | `#F2F2F0` | `#0C0D13` | page background |
| bg/section | `#E9E9E6` | `#12141D` | alternating section band |
| bg/card | `#FBFBFA` | `#191C2A` | card |
| text/primary | `#17171A` | `#E8E7EF` | body |
| text/soft | `#55565E` | `#A7A9BF` | secondary |
| text/mute | `#82838D` | `#6E7189` | caption |
| border | `#DEDEDA` | `#262A3D` | hairline |
| accent/cta | `#32355C` | `#3A4070` | CTA surface, the logo's tip indigo |
| accent/cta-text | `#F2F2F0` | `#B9BEF0` | CTA text |
| accent/link | `#4A4F8C` | `#8F96D9` | links and points |
| accent/ring | `#8F96D9` | `#8F96D9` | focus ring |

The code syntax is `var(--home-*)` and scoping is done. Web dark mode reads the Dark values
under `data-theme="dark"`.

## Components (the `Homepage (Design system)` page, all bound to variables)

| Component | Variants and properties |
|---|---|
| `Home / Button` | Kind = Primary or Quiet, label TEXT |
| `Home / Badge` | label TEXT (dot plus pill) |
| `Home / Card` | title and desc TEXT plus the logo mark icon |
| `Home / Header` | logo and wordmark, five links, a sign-in button; an instance on every screen |
| `Home / Footer` | brand, copyright, four links; an instance on every screen |

## Screens (the `Homepage (real screens)` page, **auto-layout section structure**)

| Screen | Composition |
|---|---|
| Home · Desktop (41:4) | Header, hero (badge, 56px headline, two CTAs, a **dark Deep Signal mockup** with a constellation of GraphNode instances), a three-step how-it-works band, six feature cards in a 2 by 3 grid, a three-promises band, the indigo final CTA, Footer |
| Download (41:8) | three OS cards with the current OS highlighted, plus a checksum note |
| Release (41:12) | three changelog cards with v0.3.1 emphasized as current, plus a roadmap in the filled, half and empty circle notation |
| Guide (41:16) | four step blocks, each with a dark mini visual (constellation, chat bubble, sync diagram) |
| Login (152:174) | a centred authentication card (logo, magic link), part of the new Account and Community section |
| Account (153:199) | profile, MCP keys, a newsletter toggle and an account deletion zone |
| Community (154:221) | a 2 by 3 pack gallery, with the topic distribution bar in three indigo steps |

## Rules

- **No glow on the homepage.** Glow exists only on graph nodes inside the application mockup
  (the GraphNode instances).
- The application mockup is **always dark**, even over a light page, because it advertises
  the desktop application. This inherits the old `--w-*` practice, but as a component
  instance.
- Keep the status notation language (filled, half and empty circles).
- ⚠ A Figma trap: **when a frame is auto-layout, the children's x and y are ignored**. 41:4
  was originally a vertical auto-layout, so the first build stacked everything, and it was
  reorganized into a section structure. `resize()` resets the sizing mode to FIXED, so set
  AUTO **after** resizing.
