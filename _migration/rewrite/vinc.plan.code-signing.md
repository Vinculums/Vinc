---
doc_id: vinc.plan.code-signing
layer: plan
domain: vinc/infra
authority: record
title: Code signing, a plan that was reversed and still has not landed
updated: 2026-08-07
inherits_from: [vinc.ops.deployment]
tags: [plan, infra, record, signing, notarization, release, superseded]
---

# Code signing, a plan that was reversed and still has not landed

> **History, not intent.** This was written as an execution plan for signing and notarizing the
> desktop builds along the paid route. `adr-infra-044` then reversed the direction to a zero-cost
> route, which made the plan obsolete before any of it was wired.
>
> **Measured 2026-08-07: neither route is in force.** Neither platform is signed, and the
> zero-cost route's own precondition does not hold either. What is worth keeping from the
> document is the procurement comparison and the wiring locations, and both are recorded below
> in the past tense they now deserve.

## 1. The problem it was written for

An unsigned build warns on first launch: the operating system says the developer cannot be
verified on one platform and that it protected the machine on the other. The friction sits between
download and first run, which is the worst place to put it, because the user has already decided
to try the product and the machine is telling them not to.

The pipeline had been built to leave a hole for this. The release workflow said in a comment that
signing was a later milestone and that the secrets would be injected into the build step when they
existed. **The wiring was absent and the procurement was the whole blocker**, which is why the plan
was mostly a schedule for a person rather than for code.

## 2. What the paid route proposed

Two independent procurements, one per platform, each with its own lead time.

| Platform | Mechanism | What had to be obtained |
|---|---|---|
| macOS | Developer ID application certificate plus notarization | Program membership at ninety-nine dollars a year, a certificate exported with its private key, an app-specific password for the notary service, and a team identifier |
| Windows | Authenticode over a cloud signing service | An organization-validated certificate with cloud key custody, and the credentials the signing tool needs |

The certificate comparison is the part that still has value, because the constraints it records are
structural rather than momentary.

| Option | Verdict at the time |
|---|---|
| The cloud provider's own signing service | **Excluded: not issuable in this country.** Availability was restricted to a small set of jurisdictions |
| An organization-validated certificate from a cloud signing vendor | **Recommended.** Headless signing from a build runner, invoked through the bundler's custom sign command |
| A cheaper cloud certificate from another vendor | Lowest price, hardest automation |
| An extended-validation certificate | Most expensive, and **its historical advantage had already lapsed**: the reputation service no longer promised immediate warning suppression for it |

Estimated cost was roughly two hundred and fifty to three hundred and fifty dollars in the first
year. Notarization itself is free.

## 3. What reversed it

`adr-infra-044`, decided 2026-07-13, chose a **zero-cost path** instead: make the source repository
public, take free signing for Windows from a foundation program that serves open source projects,
and leave macOS unsigned with first-run bypass instructions on the download page. Program
membership would then be bought only if macOS signing were actually wanted.

Going public had a second benefit that had nothing to do with certificates: build minutes on the
hosted runners are free for public repositories, and the macOS runner is billed at ten times the
base rate for private ones.

## 4. What is actually true today

Measured against the repositories and the live site on 2026-08-07.

| Claim from either plan | State |
|---|---|
| macOS builds are signed and notarized | **No.** Nothing is wired |
| Windows builds are signed | **No.** Nothing is wired |
| The source repository is public, which the zero-cost route requires | **No.** `Vinculums/Vinc` is private. Only `Vinculums/Vincs-app`, the release host, is public |
| The download page tells the user how to get past the warning | **Yes**, for both platforms. The first release's note also promised that signed builds would follow, and that sentence has not been revisited since |
| The updater is signed | **Yes**, and it always was a different thing. See below |

The repository state deserves a note rather than a silent correction. The rollout ledger records
`Vinculums/Vinc` as public at the organization move on 2026-07-13, which is what made the zero-cost
route available. It is private now, and **nothing in the corpus records when or why that changed.**
The gap is left visible rather than reasoned away.

### 4.1 Two signatures that are easy to confuse

The updater signature is live and has been since automatic updates shipped. It is generated with a
key pair belonging to the application framework and it proves that **an update bundle is the one we
published**. It says nothing to the operating system about who we are, so it does not remove a
single first-run warning. Operating system code signing is the other thing, and it is the one that
is absent.

The plan anticipated exactly this and put the key-pair generation in its final phase as
pre-wiring. That phase is the only one that landed, though it landed on its own schedule rather
than as part of this plan.

## 5. Where the wiring goes, which has not changed

This is the reusable part. Anyone picking the work up starts here.

- **macOS is environment only.** The build action creates a temporary keychain, imports the
  certificate, signs, submits to the notary service and staples the ticket when six environment
  variables are present. One step in `.github/workflows/release.yml` is the entire integration point
- **Windows cannot be done through the environment.** The bundler takes either a certificate
  thumbprint from the machine store or a custom sign command, and both live in
  `app/src-tauri/tauri.conf.json`. The sign command is invoked once per artifact with the file path
  substituted, so the tool has to be installed on the runner first
- **Secrets belong to the source repository, not the publishing one.** Signing happens where the
  build happens. The publishing repository holds only the upload token

> **A trap that has already cost a release.** Wiring the macOS variables from secrets that do not
> exist injects **empty strings**, and the bundler treats an existing-but-empty signing identity as
> a request to sign. The macOS build then fails while Linux and Windows pass, so the failure looks
> like a platform problem rather than a configuration one. This was measured on the first v0.4.0
> tag build. The variables are therefore commented out rather than wired, and whoever restores them
> must add an emptiness guard that exports the variables only when they are non-empty.

## 6. Risks the plan identified that outlived it

- **The sidecar is the hard part of notarization, not the shell.** The application bundles a
  single-file frozen Python binary, and single-file bundles under a hardened runtime tend to be
  rejected: they execute memory they wrote, they carry unsigned embedded binaries, and they extract
  themselves at runtime. The remedies, in order of preference, are to sign the sidecar as well, then
  to grant the narrow entitlements, then to fall back to a directory bundle. **A successful build is
  not a successful notarization**, so verification has to be done on a real artifact
- **Signing does not remove the Windows warning immediately.** An organization-validated signature
  accrues reputation over downloads. The expectation to set is that signing starts a clock, not that
  it flips a switch
- **Certificate lifetimes were cut.** The plan recorded a cap of four hundred and fifty-nine days,
  which turns renewal into a calendar item rather than a background fact
- **Intel Macs are not covered.** The build matrix has one Apple target, and the sidecar is frozen
  per architecture, so widening it costs a matrix entry and a cross-build. The download page
  compensates in copy: it states the requirement as Apple Silicon and tells Intel users to build
  from source for now

## 7. One invariant the plan checked, and it still holds

Notarization is a **build-time** act: the artifact is uploaded to the vendor during continuous
integration, and stapling the resulting ticket to the bundle means the user's machine validates it
**offline** on first launch. Nothing about signing makes the application contact anyone at runtime,
so the promise that the desktop application needs no account and no network is untouched by any of
this. The same check applies to the updater key, which signs a manifest and does not authorize a
connection.

## Provenance

| Source | Relation |
|---|---|
| `Vinc/.github/workflows/release.yml` | The single integration point, and the comment block that holds its place |
| `Vinc/app/src-tauri/tauri.conf.json` | Where a Windows signature would be configured |
| `Vinc-homepage/site/download/`, `site/release/` | The first-run instructions that stand in for a signature today |
| [[vinc.canon.decisions]] | `adr-infra-044`, the reversal |
| [[vinc.research.desktop-shell]] | Where the reversal was recorded with its reasoning |
| [[vinc.ops.deployment]] | The release surface this would attach to |
| [[vinc.plan.p5-remote-rollout]] | The ledger entry recording the repository as public at the organization move |
