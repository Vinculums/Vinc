---
doc_id: vinc.research.desktop-shell
layer: research
domain: vinc/design
authority: research
title: The installed shell, examined
supersedes: vinc.d.desktop-shell
updated: 2026-08-07
inherits_from: [vinc.spec.surface-tree, vinc.research.ux-restructure, vinc.spec.atlas-visual-language]
tags: [research, design, shell, navigation, tauri, first-run, desktop]
---

# The installed shell, examined

> Research input, dated 2026-07-16, from a survey run on 2026-07-10. It answers four owner
> questions: whether to use a left navigation bar at all, what structure suits an installed
> application rather than a web application, what an installed application is obliged to provide,
> and what of the first design draft should be reconsidered.
>
> **Its headline verdict has been reversed twice since.** Section 2 records that rather than
> deleting it, because the four pieces of evidence behind the verdict are still sound and it is the
> premise that moved. What is built today is in [[vinc.spec.surface-tree]].

## 1. How the original graded its own evidence

The study used two grades and both are worth preserving, because the difference between them
predicted which conclusions later held.

| Grade | Meaning |
|---|---|
| Verified | Survived an adversarial check: the claim was argued against from three directions and stood |
| Single source | Taken from an official document, with no rebuttal found, but the adversarial check was not completed. Re-confirm against primary documentation before implementing |

Every navigation finding in section 2 is verified. Almost every platform capability claim in
sections 3 and 4 is single source, and that is exactly where the divergences in section 6 are
concentrated.

## 2. Navigation: the full-width sidebar was rejected

Four lines of evidence pointed the same way.

| Source | Finding |
|---|---|
| Apple interface guidance | A sidebar is top level navigation, but it demands substantial horizontal and vertical space. It must be hideable, and hidden by default is explicitly against the guidance, on discoverability grounds |
| Material guidance | A navigation rail is the component for three to seven destinations, and at desktop resolution it is sufficient as the sole navigation. Five surfaces sits in the middle of that range |
| A public interface redesign that was walked back | Maximizing the canvas was the stated motive, and the chrome was reduced to a slim bottom bar. But the floating navigation and property panels were **withdrawn after the beta**: they narrowed the canvas rather than widening it, content hid behind them, and they defeated the alignment tools |
| A mature graph-first desktop application | A slim icon ribbon summons the graph, and the graph's own settings live in an in-canvas overlay rather than in application settings. Its split between a global and a local scope is the same shape as the Atlas scope levels, which cross-confirmed that structure |

The verdict was a collapsible icon rail, visible by default, expanding to labels on hover or a
toggle key, with the map surface canvas-first: no left chrome besides the rail, graph controls
in-canvas, view actions on a slim bottom bar. A floating navigation panel was rejected on the
inherited evidence, and a top tab bar was rejected for consuming vertical space and intruding on
the canvas. The command palette was designated a **secondary** route rather than a replacement for
navigation, because discoverability was the rail's job.

### 2.1 The verdict was reversed twice, and the second reversal is not a return

The actual trajectory is: `adr-design-026` rejected the full-width sidebar and adopted the icon
rail. `adr-design-056` then removed the rail entirely in favor of a single top bar.
`adr-design-069` then put a left column back.

**The third step is not a reversal of the first.** `adr-design-026` looked at the left column as
navigation to five surfaces, and against that use the evidence above still holds. What
`adr-design-069` puts there is not a surface list, it is a topic and document tree: hundreds of
items, browsed rather than switched between. The three to seven destination range governs surface
navigation and says nothing about a hierarchical list of documents.

What survives from the original verdict, unchanged:

- Not hidden by default.
- No floating navigation panel. The node-anchored context card introduced by `adr-design-070` is
  not navigation and is not covered by that rejection.
- Canvas first. The returning left column is a grid column, not an overlay, so it does not cover
  the map.

### 2.2 Window chrome

Fully custom title bars are supported through three routes. The study's recommendation was a
**hybrid**: on macOS keep the system title bar in an overlay style, so window movement and window
management behavior are retained while content extends underneath, and use a fully custom title bar
only on Windows. The macOS half of that claim was graded single source and flagged for
re-confirmation. See section 6 for what shipped.

## 3. Conventions of an installed application

Where these differ from a web application.

| Item | Verdict | Note |
|---|---|---|
| Native menu | Adopt. File, View, Help | The platform decides whether the menu is global or in-window |
| System tray | **Defer.** Vinc is not a resident application: the job queue lives and dies with the window. A long ingestion finishing is adequately reported by an operating system notification | |
| Window state restore | Adopt, as a requirement | An official plugin does save and restore automatically |
| Multiple windows | **Reject.** One window | The store has a single writer, and the comparison view answered the split-screen demand internally |
| File association | Adopt. Register the pack extension so a received pack opens the application straight into an import sheet | Completes the sharing path described in [[vinc.canon.share-pack]] at the operating system level |
| Drag and drop | Adopt, application-wide. Documents and folders to intake, packs to the import sheet | |
| Operating system notification | Adopt, minimally. One per completed background job. No marketing notifications | |

## 4. What an installed application must provide

| Capability | State at the time | Ruling |
|---|---|---|
| Automatic update | New | An official updater plugin, driven by a **static manifest**, so no server is required. This is the only optional network contact in the product, so it is named explicitly in Settings: on by default, disclosed at first run, switchable off, failing silently. A manual check belongs in the Help menu |
| Window size and position memory | New, required | |
| Local settings storage | Reinforce | A key-value file store replaces the scaffold's browser storage |
| Command palette | New | Surface navigation, recent records, and actions. Secondary to the rail |
| Keyboard scheme | Reinforce | The existing map keys plus surface keys, a rail toggle, and a shortcut sheet |
| Backup and restore | Existing | Snapshot with a consistency checkpoint, surfaced in Settings and the File menu |
| Data directory management | Existing | Settings, plus reveal in file manager |
| Crash and problem reporting | New | **No external transmission.** A local log file and a reveal action. A remote crash reporter is rejected |
| Dark mode | Existing design | Follow the operating system theme, with a manual override |
| Accessibility | Existing rule | Contrast tokens, a visible focus ring, and keyboard reach to every function |
| First run | Existing | Section 5 |
| Language | Existing | English is canon, and there is no internationalization framework |

## 5. First run: the shortest path from installation to value

Login-first was already retired by `adr-design-007`. The reconsidered path is:

1. **The first screen is the intake empty state**, carrying the sovereignty line. No separate
   onboarding screen. Embedding model preparation shows as progress under the drop zone.
2. **A sample pack ships with the installer.** The empty state offers a secondary action that
   imports a small bundled pack, so a new user reaches the map inside a minute with no data of their
   own. It reuses the share pack path (`adr-engineering-019`), so it is cheap, and it is removable
   in one click by deleting a reserved owner scope.
3. **One first-run notice, inside the empty state rather than as a modal**: where data is stored,
   and that update checks happen. **No consent-gate screen.**

## 6. What shipped, and what did not

Measured against `Vinc/app` and `Vinc/app/src-tauri` on 2026-08-07.

| Proposal | State |
|---|---|
| Automatic update through a static manifest | **Built.** The updater plugin is a dependency, the manifest endpoint is a release asset, and only the public key is pinned in configuration, per `adr-security-102` |
| Pack file association | **Built.** The bundle registers the extension |
| Sample pack | **Built.** `app/src/sample-pack.json` ships in the bundle |
| Command palette | **Built**, with a discoverability gap recorded in [[vinc.spec.surface-tree]]: the search field took the unmodified chord, so the palette moved to a modified one that is announced only on the shortcut sheet |
| Dark by default | **Built** as `adr-design-038`. The release notes for v0.7.4 make the consequence concrete: a segmented control defect that existed only in the dark override was the one most users saw |
| Window chrome hybrid | **Not built as recommended.** The single window declares undecorated chrome on every platform, so macOS does not keep the system title bar in an overlay style. The study flagged its own macOS claim as single source and asked for re-confirmation before implementing; the divergence is consistent with that flag but is not recorded anywhere as a decision |
| Window state restore | **Not built.** The plugin is not a dependency |
| Native menu | **Not built.** No menu is registered |
| System tray | Still deferred, as recommended |
| Local settings store plugin | **Not built as recommended.** Preferences persist through browser storage, which is what the study proposed to replace |

One thing was added that this study did not consider: a single-instance guard, introduced in v0.5.2
so a second launch focuses the existing window and exits, because duplicate launches were spawning
duplicate sidecars.

## 7. The decisions this study raised

| Now | Was | Decision |
|---|---|---|
| `adr-design-026` | D26 | Icon rail navigation. Superseded by `adr-design-056`, then re-answered for a different purpose by `adr-design-069` |
| `adr-design-038` | D38 | Dark by default, and developer detail hidden from surface copy |
| `adr-design-042` | D42 | Both language decks in one build. Bilingual releases were rejected: they double the release matrix and make switching language a reinstallation |
| `adr-engineering-043` | D43 | The data home defaults to a dot directory in the user's home, plus the release pipeline that freezes the sidecar into a single binary and bundles it |
| `adr-infra-044` | D44 | The zero-cost code signing path. **Superseded**, see [[vinc.plan.code-signing]] |

`adr-design-038` also carries the amendment that banned the em dash from screen copy. The
document attributes that instruction to 2026-07-10; [[vinc.canon.terminology]] carries the standing
rule and dates it 2026-07-22. Either the instruction was repeated or one of the two dates is wrong,
and that is recorded here rather than reconciled by guessing.

> **Measured 2026-08-07, and worth acting on:** the desktop window title and the bundle description
> both contain an em dash. The rule that bans it originated in this document.

## 8. Decisions raised here that have no identifier

Five proposals were approved as decisions in the original and never became nodes, so nothing can
cite them and nothing can find what depends on them.

| Label | Decision | Its state today |
|---|---|---|
| D27 | Window chrome hybrid | Not built, see section 6 |
| D28 | Automatic update through a static manifest, on by default | Built |
| D29 | Pack file association into an import sheet | Built |
| D30 | Bundled sample pack at first run | Built |
| D41 | Platform control grammar: segmented controls for panel choices, where height rather than color carries state, and buttons with a fine gradient, a top highlight, and a press displacement | Partly visible in the current segmented control, which gained a sliding thumb in v0.7.4 |

These labels are described here by effect. **They are deliberately not given `adr-` identifiers**,
because inventing one would make an unregistered decision look registered. The general shape of this
gap, thirty-one decisions that exist only as prose, is recorded in [[vinc.canon.decisions]].

## Provenance

| Source | Relation |
|---|---|
| Owner questions, 2026-07-10 | The four questions this study answers |
| Platform and framework documentation, surveyed 2026-07-10 | Apple and Material navigation guidance, a public interface redesign and its walk-back, a graph-first desktop application, and desktop framework documentation for window customization, menus, window state, storage and updates |
| `Vinc/app/src-tauri/tauri.conf.json`, `Cargo.toml` | The as-built check in section 6 |
| [[vinc.spec.surface-tree]] | What the shell became |
| [[vinc.spec.settings]] | Where the update switch and the data directory ended up |
| [[vinc.canon.decisions]] | Registry for the `adr-` identifiers used above |
