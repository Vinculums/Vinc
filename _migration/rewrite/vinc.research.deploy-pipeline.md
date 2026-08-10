---
doc_id: vinc.research.deploy-pipeline
layer: research
domain: vinc/infra
authority: research
title: Survey of what the delivery pipeline still leaves to a person
supersedes: vinc.ops.deploy-pipeline-research
updated: 2026-08-07
inherits_from: [vinc.ops.deployment]
tags: [research, infra, ci, cd, release, signing, secrets]
---

# Survey of what the delivery pipeline still leaves to a person

> **Research input, not a runbook.** [[vinc.ops.deployment]] records how delivery actually works.
> This document is the opposite kind of thing: a diagnosis of the points that were manual or fragile
> on 2026-07-29, with recommendations. Nothing here becomes canonical until it is decided.
>
> The survey was prompted by the cloud migration of 2026-07-28 and 2026-07-29, which gave three of
> the five delivery surfaces continuous delivery for the first time. That drew a visible line between
> what had become automatic and what was still done from memory, and this is the census of that line.
> Build automation had never appeared in any prior decision or backlog, so this was its first entry.
>
> Every item was measured, by quoting files, by calling the code host's API, or by reading workflow
> definitions. Section 7 records what has since been acted on, re-verified against the repositories
> on 2026-08-07. Read the findings as of the survey date and the status as of 2026-08-07.

## 1. What the survey found

The pipeline was already on the mature side. Worth stating first: unsigned releases were blocked
outright, a three-platform runtime smoke test asserted a healthy sidecar, release assets were
uploaded directly to sidestep a storage quota, fixed alias names were published so download links
never had to change, old releases were archived automatically, deployment authenticated with
federated credentials rather than a stored secret, and the static site token was fetched at run time
rather than kept as a secret. Every one of those exists because something failed once.

Even so, **fifteen points were manual or fragile**, falling into three kinds.

| Kind | Examples | Why it matters |
|---|---|---|
| **Quietly wrong** | 498 tests wired to no pipeline, a lock file left seven releases behind, an alias step that failed green, web and desktop asserting the same version for different code | the failure passes as success, which is the worst category |
| **Single points of failure** | no backup procedure for the updater signing key, two access tokens with unmanaged expiry | when it breaks there is no recovery path, or it surfaces on release day |
| **A decision whose premise collapsed** | the zero-cost code signing route assumed the source repository was public, and it was not | the decision has to be retaken |

**If only one gap could be named: the Python test suite of 498 tests was attached to no pipeline at
all.** It ran when a person remembered to run it.

## 2. Desktop release

### 2.1 The zero-cost signing route rested on a false premise

The decision recorded as `adr-infra-044`, taken 2026-07-13, settled a zero-cost path: make the source
repository public, then take the free foundation signing programme on Windows and stay unsigned on
macOS.

Calling the code host's API without authentication on 2026-07-29 gave a plain answer. The source
repository and the homepage repository both answered not-found, meaning private. Only the release
host repository answered publicly. **The foundation programme supports public open-source
repositories only**, so the zero-cost path did not exist in the state the project was actually in.
The documents disagreed with each other too: one workflow header stated correctly that the source
repository is private, while the macOS investigation note warned that public status was the
precondition for eligibility without recording that the precondition had failed.

The obvious alternative was checked and ruled out. The cheapest managed signing service, at ten
dollars a month, admits individuals in the United States and Canada or organizations in the European
Union and the United Kingdom. A Korean individual or organization is not eligible.

That left three options, and choosing among them is a business judgement rather than a technical one.

| Option | Cost | Precondition | Side effect |
|---|---|---|---|
| A. make the repository public and take the foundation programme | none | audit the history for committed secrets | the source becomes fully public, and it also removes the private-repository macOS runner surcharge, section 5.3 |
| B. stay unsigned | none | none | the operating system warnings persist, and the download page copy promised a signed build with nothing behind the promise |
| C. buy a commercial certificate | roughly 200 to 350 a year | organizational verification | renewal burden, and macOS is a separate 99 a year |

> **Recommendation at the time:** examine A first, since it was the route the decision originally
> intended and it clears the runner surcharge at the same time. But making the source public cannot
> be decided on technical grounds. If it is not possible, re-affirm B explicitly and repair the copy
> contradiction, rather than leaving a promise standing with no decision behind it.

> **Decided 2026-07-30: option B, until version 1.0.0.** The second half of the recommendation was
> taken. Making the source public is irreversible and was judged not worth doing before 1.0.0 for the
> sake of a signature. `adr-infra-044` is replaced by that decision. The point is that this is not an
> open-ended deferral but a review pinned to a version. The copy contradiction dissolves with it: the
> download page line promising a signed build in a later milestone was contradictory only because
> nothing stood behind it, and now something does. The copy is not changed, because a public promise
> should not be pinned to a version number. The canonical record is [[vinc.ops.deployment]] section
> 1.7, including the three things to check at the 1.0.0 review.

### 2.2 The signing key is the pipeline's first single point of failure

**Losing the key has no recovery path.** The public key is compiled into the desktop configuration
and clients reject bundles signed by anything else. A new key ships only inside new installations, so
**every already-installed user falls permanently out of automatic updates** and manual reinstallation
is the only way back.

State at the time of the survey: no document anywhere in the three repositories described backup,
recovery or rotation. The key files sat in the workspace root as plain files, and the public half
matched the value compiled into the desktop configuration byte for byte. Two things were already
right, in that the files live outside every repository and none of the three has ever committed them.
Two things were wrong: the ignore rules did not list key or certificate extensions, so moving a file
inside a repository would have been stopped by nothing, and the only backup appeared to be that one
local file.

Recommended immediately: back the key up off-site in a password manager or sealed medium, write the
location and the recovery procedure into the deployment runbook, add the extensions to the ignore
rules, and destroy the leftover access key of the deleted virtual machine.

### 2.3 Version bumping touched more places than the checklist said

One measured mismatch: the front-end lock file still declared a version seven releases old while its
package file was current.

The cause is an **asymmetry**. The Rust lock file was on the checklist and the front-end lock file
was not. That file is only regenerated when dependencies change, and nobody touches it during a
version bump, and the clean-install command compares the dependency tree rather than the version
field, so it **passes quietly**.

Worse, the table enumerating the places to edit **had disappeared from the deployment runbook**
during the rewrite for the cloud migration, surviving only in an earlier revision. Reading the
current document told nobody where to edit.

One file was the most fragile of all, carrying three different spellings of the version inside itself:
a four-element tuple, a four-part dotted string, and a comment naming the current version in prose.

Recommended: a bump script that updates every site at once and then verifies that all of them agree,
exiting non-zero on any mismatch, with the same verification wired as a gate at the start of the
release workflow. That closes the trap where a tag and the files disagree, which is the root of the
endlessly repeating update prompt.

### 2.4 Release cards are authored three times, and the key scheme runs out

A single release costs at least thirty-seven hand-written lines across two languages. On top of that,
the changelog written for developers and the release card written for users are three entirely
separate bodies of text. **This is deliberate duplication, not an automation target.** The changelog
states in its own header that the canonical user-facing copy lives on the site.

The one thing that does need attention is the key prefix scheme used to index release cards. It began
at one two-character prefix and has walked forward one step per release. **Four remain**, and no rule
exists for what follows, and the lower bound blocks reuse. Switching to a version-derived prefix now
lets existing cards stay as they are while new ones adopt the new scheme. After the scheme is
exhausted the same choice has to be made in a hurry.

### 2.5 The alias step failed green

The helper that copies each built bundle to a stable download name matched by a filename pattern and
skipped silently when the pattern found nothing. **If the bundler ever changed its naming convention,
all four download buttons on the homepage would return not-found while the build reported success.**
The same file blocked a missing updater signature with a hard failure, so this was an asymmetry in
strictness rather than an oversight of principle. Recommended: assert that all four aliases exist and
fail otherwise.

## 3. Web Vinc

### 3.1 Web and desktop claimed the same version for different code

At the time of the survey, fourteen application source files had changed since the current release
tag, including the alluvial rework recorded as `adr-design-068`, while the package version was
unchanged.

So the next site deployment would put the reworked code on the web while the installed desktop
release carried the code from before it, and **both would report the same version string**.

That exactly defeats the stated purpose of stamping the version into the build, which was to make it
impossible to confuse which version a diagnostic report came from, a device introduced in response to
the field report in [[vinc.ops.incident-2026-07-16-macos-sidecar]]. A report naming that version gave
no way to tell which build produced it.

Recommended: inject the commit identifier, or a build timestamp, into the web build as a separate
constant and carry it in the diagnostic payload. The version string stays as it is and only an
identifier is appended, so the blast radius is small.

### 3.2 A cross-repository trigger cannot be built from path filters

The application source lives in one repository and the site workflow lives in another. **Push events
do not cross repositories**, so widening the path filter would not have triggered anything. A
dispatch event, or an explicit workflow call from the source side, was required.

### 3.3 An unpinned checkout shipped unverified application code

The site workflow checked the source repository out with no ref, which always resolves to the default
branch head. **Fixing a typo in the site and pushing it would carry whatever unverified commit was on
the source branch at that moment out to the web.** Site changes and application changes could not be
separated at commit granularity.

Recommended: pin the ref to the latest release tag, so the desktop and the web are the same code, and
override the ref through a manual dispatch input only when something has to go out urgently. Together
with the identifier from section 3.1 this makes "which code is the web running" answerable.

## 4. Quality gates, the largest gap

### 4.1 The Python test suite was attached to nothing

The test tree held thirty-seven files and 498 test functions, close to the count the changelog
claimed. The only way to run them was a single stdlib runner script.

**Measured wiring: zero.** The release workflow ran no Python tests, its only check being a sidecar
smoke test asserting a healthy response, which answers whether the build starts and not whether it
works. The hub deployment workflow ran none either, and its tool-listing check ran **after** the
deployment, so it could not block one. The forbidden-terms gate protecting the copy voice was
unwired as well, and so was the test that locks that gate down.

That means the owner-isolation and write-tenancy tests behind `adr-security-001` and
`adr-security-003`, and the synchronization merge tests, all depended on somebody remembering.
During the session that rewrote the hub as a container and deployed it, the 498 tests did not run
once.

Recommended as the highest priority: create a continuous integration workflow in the source
repository that runs the suite and the forbidden-terms gate on push and on pull request, and make
the hub deployment depend on it, so a broken test blocks a deployment instead of being noticed after.

### 4.2 The front end had no tests, no linting, and no separable type check

The application package declared four scripts and four development dependencies. There was no test
runner, no browser test harness, and no linter or formatter. The type check was folded inside the
build script, so **it could not be run on its own**, which meant front-end type errors surfaced only
in the site deployment workflow, and in the desktop release they were buried inside the bundler
action.

Recommended, as the cheapest possible improvement: split the type check into its own script and wire
it. Adopting a test framework is a separate question, but the case had just strengthened, because the
alluvial work had produced the first **pure function modules** in the front end.

### 4.3 Coverage unmeasured, dependencies unscanned

The account test suite was well wired but had no coverage provider installed, so **nobody knew what
was not tested**. Automated dependency updates, code scanning and audit were absent from all three
repositories, and the install command explicitly disabled auditing. The dependency surface spans
front-end packages, several thousand Rust crates, and the account tree.

Recommended: add dependency update configuration to all three repositories. Code scanning is a paid
feature on private repositories, so it belongs with the public-source decision in section 2.1.

## 5. Secrets, tokens, cost

### 5.1 Two access tokens whose expiry surfaces too late

| Token | Where it fails | Character |
|---|---|---|
| release token | three jobs of the release workflow | fails **after the tag has already been pushed**, so retrying requires deleting and re-pushing the tag |
| source read token | the second step of the site workflow | that workflow runs on every trivial site edit, so the moment it expires every site deployment stops |

The release token carries an extra trap. The step that creates the draft release swallows its own
failure, because three parallel jobs race to create it and only one can win. **An expired token fails
identically and is swallowed the same way.** The next upload step then fails with a message about a
missing release, which hides the fact that the cause was the token.

**Neither token had an expiry date, an owner, or a renewal procedure recorded anywhere.**

Recommended: record the expiry in the runbook, add a token validity pre-check at the start of the
release workflow so it fails **before** the tag is pushed, and consider moving to an installation
identity with no expiry.

### 5.2 Stale documents and leftovers

The account integration workflow header still said deployment was manual over a shell session; a
deployment script still targeted the deleted virtual machine and could not run; the download page
promised a signed build that no decision supported; and the Python project version was pinned at a
placeholder unrelated to the release version, which may or may not be intended.

### 5.3 Private-repository build minutes are the hidden half of the public-source question

The build service applies a **ten times multiplier to macOS runners on private repositories**. The
release workflow runs a three-platform matrix on every release, so this accumulates. Making the
source public would resolve the signing eligibility and this surcharge at the same time, and the two
should be weighed together rather than separately.

## 6. Recommended order

**Block the risk first.** Back the signing key up off-site and write the procedure down, add key and
certificate extensions to the ignore rules, destroy the leftover access key of the deleted machine.

**Then stop the things that fail green.** Create a continuous integration workflow running the 498
tests and the forbidden-terms gate, make hub deployment depend on it, add a version bump script with
a consistency check wired as a release gate, and bring the stale lock file up to date.

**Then stabilize release operations.** Fail the build when an alias is missing, move the release card
key scheme to a version-derived form while four remain, and split out the front-end type check.

**Then align web and desktop.** Inject a build identifier into the web build, pin the source checkout
to the release tag, and add the cross-repository trigger.

**Then security and tokens.** Dependency update configuration in three repositories, recorded token
expiry with a pre-flight check, and a cleanup pass over the stale documents.

**Left to the sponsor**, because technique cannot settle them: the code signing decision, and whether
to adopt a front-end test framework now that pure function modules exist.

## 7. Status on 2026-08-07

Re-verified against the repositories. Most of the survey has been acted on, which is the reason this
document is an input and not a description of the present.

| Finding | Status |
|---|---|
| 2.1 code signing | **decided**, option B until 1.0.0, recorded in [[vinc.ops.deployment]] section 1.7 |
| 2.2 ignore rules | **done**, key and certificate extensions are listed |
| 2.2 off-site key backup | **not done.** The runbook now records that the key has no passphrase at all, which makes the file itself the secret, and that losing both the local file and the stored secret is permanent |
| 2.3 version bump | **done**, a synchronization script owns ten sites across six files, with a check mode, and the release workflow gates the tag against the files. All version sites now read the same current release |
| 2.4 release card key scheme | **not done** |
| 2.5 alias step | **done**, a missing pattern now emits an error and returns non-zero |
| 3.1 build identifier | **done**, and the runbook records that clearing the environment variable at step level does not work because the name is reserved, so the shell has to clear it |
| 3.2 cross-repository trigger | **done**, a trigger workflow in the source repository calls the site workflow on a release tag |
| 3.3 unpinned checkout | **done**, the site workflow now pins to the latest release tag and refuses to deploy when the tag and the package version disagree |
| 4.1 tests in continuous integration | **done**, a workflow runs the suite, the forbidden-terms gate and a contrast gate, and hub deployment depends on that gate job |
| 4.2 separable type check | **done**, it is its own script and is wired as a job |
| 4.3 dependency updates | **partly done**, configured in the source and homepage repositories, absent from the documentation repository |
| 4.3 coverage provider | **not done** |
| 5.1 token expiry and pre-check | **not done** |
| 5.2 stale documents | **partly done**, the runbook has since corrected its own claim that the command line client was unavailable |

**One thing the survey could not have seen.** The organization was renamed to `Vinculums` on
2026-08-05, and the code host has moved its deployment identity subject to an immutable identifier
form. Together those broke all three continuous delivery workflows until 2026-08-06. The old
organization name also now answers with a redirect, which breaks callers that do not follow one. The
incident and the correct procedure are in [[vinc.ops.deployment]] section 6.1, and it is a direct
example of this document's first category: the desktop release stayed green throughout because it
does not authenticate to the cloud, and the hub kept serving its old revision, so **nothing appeared
to be wrong for two days**.

## 8. Method and limits

Everything was measured: files quoted directly, the code host's API called without authentication to
establish repository visibility, revision history compared for version drift, and workflow
definitions searched for wiring. Exactly one external lookup was made, for the eligibility and price
of the managed signing service.

**What could not be settled locally:** the actual expiry dates of the two access tokens, which are
visible only in the code host's settings.
