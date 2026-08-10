---
doc_id: vinc.release.changelog
layer: release
domain: vinc/product
authority: record
title: The release record, and how a release is cut
supersedes: vinc.changelog
updated: 2026-08-07
inherits_from: [vinc.ops.deployment]
tags: [release, product, record, changelog, versioning, mirror, procedure]
---

# The release record, and how a release is cut

> **The repository file is the record. This document is not a copy of it.**
> `Vinc/CHANGELOG.md` is where release history is written and where it is read from. The copy that
> lives in the graph exists so the text is searchable, and it is a **mirror**: it lags, and nothing
> is ever edited there.
>
> Reproducing every entry here would create a second version of the same history that drifts from
> the first, which is precisely the failure this corpus was rewritten to end. So this document holds
> what the file itself cannot: **where each record lives, what shape an entry takes, where a version
> number is stamped, and what a release actually consists of.**

## 1. Three records, three audiences

| Record | Location | Audience | Language |
|---|---|---|---|
| Developer summary | `Vinc/CHANGELOG.md` | whoever works on it next | Korean |
| Release notes | `Vinc-homepage/site/release/` | users | English, with a Korean deck |
| Published artifacts | releases on the public host repository | anyone downloading | not prose |

The developer summary and the release notes are **different documents with different jobs**, and the
file says so in its own header: user-facing copy is canonical on the site, and the file is the
developer's version. Editing them as one produces either a changelog that markets or release notes
that leak internals. Both have happened to other projects and neither is recoverable by proofreading.

## 2. The shape of an entry

Newest first, one section per version, headed by the version, a short title naming the two or three
things that changed, and the date. Then, in order:

- **A one-line lede** in a quote block, saying what this release is actually about. Not a summary of
  the list below it. The good ones name a **cause** rather than a feature: what was wrong, and what
  turned out to be wrong about it
- **Themed subsections** for the substantial work, one per area
- **A fixes section**, which is usually the longest and the most useful. Each fix says what the
  symptom was and what the mechanism turned out to be. The convention that makes these worth reading
  is that a fix entry explains **why it was invisible**, since a defect that was easy to see would
  have been fixed sooner
- **Notes worth knowing**, for behavior a reader would otherwise discover by surprise: a schema
  version rising, old rows deliberately left empty, a feature that stays dormant until the server is
  configured

Entries are written in the past tense about measured things. Numbers in them are measurements, not
estimates, and where something was not measured the entry says so.

## 3. Where a version is stamped

**Ten places across six files**, and `scripts/version_sync.py` is the authority. It is not a
checklist, it is the executable form of one:

```
python scripts/version_sync.py --check          # every site agrees
python scripts/version_sync.py --set 0.7.5      # update all ten
```

It exists because the hand-kept checklist failed in a way nobody noticed: one lock file went seven
releases without being bumped, because the checklist listed its sibling and not it, and the install
step compares dependency trees rather than versions so it **passed silently every time**.

**The changelog is deliberately not one of the ten.** It is prose a person writes, and a script that
edited it would either mangle it or, worse, produce an empty entry that satisfies the check.

The release workflow runs the check as its **first job**, comparing every site against the tag, and
refuses to build on a mismatch. The failure that gate prevents is specific and otherwise permanent:
the update manifest takes its version from the git tag while an installed client reports the version
compiled into its own configuration. Tag without bumping, and every user is told forever that an
update is available, installs it, and is told again.

> One line in the repository deployment runbook still says six places. It is stale. The script says
> ten and the script is what runs.

## 4. Cutting a release

1. **Bump** the ten sites with the script
2. **Write the changelog entry**, and the release notes if the release is user visible
3. **Tag and push.** The tag is the only trigger for the desktop build
4. The **version gate** compares tag to files, then a three-platform matrix builds the frozen sidecar
   and the application bundles
5. Artifacts, update bundles and the update manifest are uploaded to a **draft** release on the
   public host repository
6. **A person presses publish.** This step is deliberately manual and is the last point at which a
   release can be abandoned without a user seeing it

**The tag does one more thing that is easy to forget: it redeploys the site.** The static site pins
the web application build to the most recent tag, so cutting a desktop release also ships the web
one. Release and homepage are coupled by construction, not by convention, and the full picture of
all five deployment surfaces is [[vinc.ops.deployment]].

## 5. Where the history stands

**v0.7.4, released 2026-08-06**, is the current release, and the version sites all agree on it.

The arc, for orientation and not as a substitute for reading the file: a bootstrap and a packaged
desktop application through the 0.1 series; accounts, synchronization and a driver replacement
through 0.2 to 0.5; a run of graph structure repairs; document canon, where stored source text
became the authority for its own chunks; a shell consolidation and then its partial reversal; and
the 0.7 series, which brought the map's legibility, Korean semantic retrieval, the assistant writing
conventions, account policy, and motion that says where a floating surface came from.

## 6. What keeps this record honest

- **An entry describes what was measured.** "Search failed 49 percent of the time during a save" is
  an entry. "Improved reliability" is not
- **A reversal is written as a reversal**, naming what it reverses. Half the design rulings in this
  project are revisions of earlier ones, and a revision recorded as a fresh feature makes the
  original look current forever
- **Dormant code is announced.** A change that ships in the binary but stays inactive until a server
  is configured is more confusing than a change that is absent, so it gets a note
- **The mirror is never the edit target.** Corrections go to the repository file, and the graph copy
  is replaced from it

## Provenance

| Source | Relation |
|---|---|
| `Vinc/CHANGELOG.md` | The record. This document describes it and does not duplicate it |
| `Vinc/scripts/version_sync.py` | The authority for where a version is stamped |
| `Vinc/.github/workflows/release.yml` | The build, the version gate, and the draft publish |
| `Vinc-homepage/site/release/` | User-facing release notes, canonical for user copy |
| [[vinc.ops.deployment]] | The five deployment surfaces and what triggers each |
| [[vinc.spec.product-overview]] | The product summary, which points here rather than mirroring it |
| [[vinc.release.0-7-2]] | One release bundle recorded in full detail, as a worked example |
