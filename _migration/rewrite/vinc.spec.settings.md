---
doc_id: vinc.spec.settings
layer: spec
domain: vinc/design
authority: spec
title: Settings, a modal with four tabs
supersedes: vinc.d.settings
updated: 2026-08-06
inherits_from: [vinc.spec.surface-tree]
tags: [spec, design, settings, modal, account, mcp]
---

# Settings, a modal with four tabs

> Global chrome, not a surface. It is deliberately absent from the surface tree.
>
> The design principle is **no settings sprawl**, and it is held by **demoting depth, not by
> capping count**. Low-level items move behind a tab rather than being deleted or exposed.

## 1. Why this document keeps getting the shape wrong

The previous two versions each described a layout the code had already left, and each was corrected
by reading the source. That pattern is worth naming rather than repeating.

- v0.1 said the screen had **exactly three items**. It did not
- v0.2 corrected that to a **flat list of seven**, and noted that a comment at the top of the
  implementation still said "exactly three items", so the document and the code were **citing each
  other in a circle** with neither holding a fact
- This version, measured 2026-08-06, finds a **modal with four tabs**, not a flat list at all

The stale comment is gone from the code. The lesson stands: a screen document must be re-measured
against the implementation at every rewrite, because it describes something that moves.

## 2. As-built, 2026-08-06

A centered modal with a tab bar and a close control. Closing dispatches the escape key rather than
calling a close function, so **ownership of the close logic stays in the shell** and this module
does not couple to it.

| Tab | Holds | Availability |
|---|---|---|
| **Account** | account connection by device flow, graph sync, theme, language, lock | everywhere |
| **Connections** | local MCP host registration and removal | **desktop only** |
| **Data** | local data folder and backup | everywhere |
| **Advanced** | workspace owner, engine address, secrets, manual sync key | **desktop only** |

**Web Vinc hides the two desktop-only tabs**, along with the account connection and sync sections
inside Account, because in the hosted build there is no local sidecar for them to configure.

Theme, language, and lock are inside **Account** rather than each occupying a top-level row. That is
the demotion principle in action: they are settings a person touches once, so they do not deserve
first-level real estate.

## 3. Account connection replaces pasting a key

The connect action opens the system browser to the account connect page. The issued key returns to
the sidecar over loopback, and the app polls once to collect it and stores it locally.

**The app never calls the account API itself.** That is what preserves the repository separation
decision: the desktop client talks to its own sidecar, and only the sidecar holds a credential
that reaches an account service. Routing the key through loopback rather than through the app is
the mechanism, not an implementation detail.

This replaced a flow where the user copied a member key out of a web page and pasted it into a
text field, which failed in the ordinary ways that pasting credentials fails.

## 4. Rules

**Developer detail is demoted, not hidden.** The engine address and connection secret are reachable,
in Advanced. What sits **below** that layer, the embedding model and the backend driver, is still
not in the interface at all: those have a single source in configuration. Sprawl is prevented by
this layering, not by refusing to add rows.

**Lock is opt-in and off by default.** With no passphrase set there is no unlock step at all.
When it is set, startup passes through an unlock dialog, which is a dialog and not a screen.

**Theme and language apply immediately.** A language change re-renders every surface, because copy
is read through a proxy rather than captured at construction.

**Registerable MCP hosts mirror the sidecar's own list.** A host that is absent from that list still
works: the server behaves identically, and the interface offers manual registration instructions
instead of a button. The list is a convenience, never a gate.

## 5. Where the copy lives

Copy is owned by code, not by this document. The full inventory is [[vinc.spec.ui-copy]].

## 6. Related

| Subject | Document |
|---|---|
| Why Settings is not in the surface tree | [[vinc.spec.surface-tree]] |
| MCP tool contract behind the Connections tab | [[vinc.canon.mcp-contract]] |
| Account tiers and team membership shown in Account | [[vinc.canon.account-policy]] |
| Data directory and backup semantics | [[vinc.spec.dataplane]] |
