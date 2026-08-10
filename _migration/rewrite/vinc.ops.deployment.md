---
doc_id: vinc.ops.deployment
layer: ops
domain: vinc/infra
authority: spec
title: The deployment runbook as built
updated: 2026-08-07
inherits_from: [vinc.spec.azure-infrastructure]
tags: [ops, infra, deployment, cd, oidc, containers, release, runbook]
---

# The deployment runbook as built

> **This document is the one deliberate exception in the corpus: the repository file is canonical and
> this graph copy is a search copy.** The living original is `Vinc-docs/vinc/DEPLOYMENT.md`, it is
> **still used in operations today**, and it is edited during incidents, sometimes hours after the
> incident. When the two disagree, **the repository file wins**. Do not correct an operational
> procedure here and assume it took effect. The exception exists because this file is read by a
> person in the middle of a failing deployment, at a moment when the graph may be exactly the thing
> that is down.
>
> Everything below is **as built**, not an ideal procedure. It records what actually happens, traps
> included, because most of it was written after something broke. The topology it deploys onto is
> [[vinc.spec.azure-infrastructure]] and the money is [[vinc.ops.cost-management]].
>
> The previous version of this runbook described a single virtual machine with a reverse proxy and
> service units. That provider's machine and managed database were deleted on 2026-07-28 and
> 2026-07-29 and none of it exists. Anything that reads like it is history.

## 1. Five delivery surfaces

| # | Surface | Source | Output | Trigger | Automation |
|---|---|---|---|---|---|
| 1 | **desktop application** | the whole application repository | releases on the public release repository | a version tag | pipeline exists, a person publishes the draft |
| 2 | **static site and web Vinc** | the homepage site tree plus the application tree, **pinned to the latest release tag** | the static web app | a site push, a version tag on the application repository, or manual | continuous delivery |
| 3 | **account backend** | the homepage account tree | the account container app | a push under that tree | continuous delivery |
| 4 | **hub, sidecar and MCP** | the application repository's server trees | the hub container app | a push under those paths | continuous delivery |
| 5 | **database** | account migration files | the managed relational database | applied by hand | none, forward only |

Surface 1 is unchanged from before the migration. Surfaces 2 through 4 got continuous delivery for
the first time in that migration; every one of them used to be a person over a shell session. Surface
5 is still manual, and the practice of applying migrations by hand survived the change of provider.

**Common facts.** One subscription, one resource group holding everything, with the static web app in
a neighbouring region because the service is not offered in the primary one. Three names, an apex and
two subdomains, all on managed certificates. Deployment authenticates with **federated credentials
and no long-lived secret**, through one application registration with a credential per repository,
and the role assignment is **scoped to the resource group, not the subscription**. The federated
subject is an opaque identifier, section 8.1, and getting that wrong is the single most expensive
mistake in this document.

**Why managed services rather than a virtual machine.** This subscription, newly created, refused
compute provisioning in every region tried, reporting the size as unavailable while the quota display
showed capacity. Every other resource provider worked normally, so the deployment moved to container
apps, a managed database and a static web app. The virtual machine option was originally first
choice. If the restriction is lifted the comparison is worth redoing, but this architecture has been
validated in real use and there is no urgency.

## 2. Desktop release

The pipeline definition is the release workflow: a three-platform matrix, a frozen Python sidecar, a
desktop bundle, a draft release, and a person pressing publish. It was unaffected by the migration.

### 2.1 The version bump script is canonical

The version is stamped in **ten places across six files**. Counting them by hand actually drifted: a
front-end lock file sat seven releases behind, because the Rust lock file was on the checklist and it
was not, and the clean-install command compares the dependency tree rather than the version, so it
passed quietly.

A synchronization script now sets and verifies all ten, and **a gate job in the release workflow
compares the tag against the files**, so a mismatch never reaches a build. That is the root fix for
the endlessly repeating update prompt: the update manifest takes its version from the tag while the
client takes its own version from the desktop configuration, so bumping the tag without the files
means installing changes nothing. The changelog is written by a person and is not the script's
business.

### 2.2 The driver pin has to be identical in four places

The embedded driver version is pinned in the integration workflow, the hub deployment workflow, the
release workflow and the hub image definition. **Miss one and that one quietly ships something else.**

The incident: only the release workflow lacked the pin. When a newer driver appeared upstream, from
that day only releases picked it up, and the Windows build broke on a missing shared library for the
driver's C interface, because that release had split the library out of its Windows wheel.

The quieter half is worse. While the pin was missing, a release was built against whatever was newest
that day while the integration workflow tested against the pinned version. **What was tested and what
shipped were different.** It did not break, but it had no basis, and it was resolved along with the
pin.

**Choosing a driver version is bound to two axes at once, and looking at one guarantees the other
breaks.** One axis is whether the Windows wheel still carries the C interface library. The other is
whether the extension server publishes builds for that version at all: one candidate version answers
not-found for its extensions. Only one version satisfies both, so this pin is not an arbitrary
choice, and raising it means re-measuring both axes.

**And the driver's own extension downloader is not used.** Installing an extension inside the pipeline
dies on a certificate verification failure because the client's certificate bundle is stale, while
the same URL fetched with an ordinary HTTP client succeeds and the bytes match the copies committed to
the repository. The release workflow therefore fills the extension cache directly. This trap stayed
hidden because local machines had a warm cache and the pipeline was on the unpinned newer version;
**pinning it was what triggered the first cold download.**

### 2.3 A script that emits workflow error commands must have its error stream captured in tests

The environment check script reports missing values using the build service's error annotation
syntax. A test that deliberately makes it fail, and lets that stream escape, paints red annotations on
every run of a passing job. Permanent red teaches a person to ignore real errors, which is the same
disease as everything else in this document. Capture the stream, and while capturing it, assert that
the message actually names the key that is missing.

### 2.4 The updater signing key is a single point of failure

Two kinds of signing exist and only one is applied. **Updater signing is in force.** Operating system
code signing is not, see section 2.5.

**Losing the key has no recovery path.** The public half is compiled into the desktop configuration
and clients reject any bundle signed by another key, so a new key ships only inside new installations
and **every already-installed user falls permanently out of automatic updates**, with manual
reinstallation the only way back.

The files live in the workspace root, outside every repository, and none of the three repositories has
ever committed them. The ignore rules block key and certificate extensions, so moving one inside would
not commit it either. **The passphrase is the empty string**, measured: signing succeeds with an empty
password. The file header suggests an encrypted secret key, but the practical protection is zero, so
the corresponding pipeline secret is empty as well, and there is **no forgotten passphrase to
recover**, because none was ever set.

Pipeline secrets cannot be read back once written, only overwritten, so their intended values are
recorded here: the signing secret is the private key file's contents verbatim, and its password is
the empty string.

**The risk model, corrected.** An earlier statement that the only backup was one local file was wrong.
There are two copies, the local file and the stored pipeline secret, and although the secret cannot be
read, the pipeline can still write with it.

| Lost | Consequence |
|---|---|
| the local file only | releases continue, since the pipeline signs. Moving to another pipeline or repository, and local signing, become permanently impossible |
| the stored secret only | restore it from the local file |
| **both** | permanent. Already-installed users fall out of automatic updates forever |

So the failure is not immediate death, it is arriving at a state where **the key can never be put
anywhere again**.

> **Outstanding:** attach both files to a password manager for off-site storage. Do not leave them in
> plain form on removable media or a synced folder. Because the key has no passphrase, **the file is
> the secret**, and whoever holds it can sign an update that installs automatically on every
> installation. A password manager encrypts at rest and compensates for the empty passphrase.
>
> Adding a passphrase is not possible with the desktop toolchain, which only signs and generates. A
> separate signing tool would be needed. The public key does not change so users are unaffected, but
> both pipeline secrets would have to be updated together. Once off-site storage exists the gain is
> small, so this is low priority.

### 2.5 Operating system code signing stays off until version 1.0.0

**Decision of 2026-07-30: stay unsigned, until 1.0.0.** This is not an open-ended deferral, it is a
review pinned to a version.

Background: `adr-infra-044` settled a zero-cost path, making the source repository public so that
Windows signing could come from a free foundation programme. Measurement showed the source repository
is private, so the premise failed, and that programme supports public repositories only. The cheapest
managed signing service admits only individuals in North America or organizations in Europe and the
United Kingdom, which excludes Korea. The three-way comparison is in
[[vinc.research.deploy-pipeline]] section 2.1.

**That decision replaces `adr-infra-044`.** The route through making the source public is not taken.
Full disclosure of the source is an irreversible business judgement and not something to do before
1.0.0 for the sake of one signature.

**The copy is not changed.** The download page is already honest: it says the build is not yet code
signed, that a warning may appear on first run, that it is safe, and it gives first-run instructions
for both operating systems in both languages. The one forward-looking line, promising a signed build
in a later milestone, **now has a real decision behind it**; the research document flagged it as a
contradiction only because nothing did. No version number goes into the copy, because a public promise
should not be pinned to a release.

**At the 1.0.0 review, check three things:** whether the repository is still to stay private, which is
the only condition that revives the public route; whether the stage justifies a paid certificate, at a
few hundred a year plus a separate annual fee for macOS; and whether the managed service's regional
eligibility has widened.

## 3. Static site and web Vinc

The static web app runs on the paid tier, and the reason is a requirement rather than a preference.
The free tier has **no linked backend**, so account routes could not be proxied under the site's own
origin, and the front end would have to make cross-origin credentialed requests, which fails
immediately because the account session cookie declares no domain and there is no cross-origin
middleware at all. The paid tier reproduces the original same-origin behaviour with zero code change,
for nine dollars a month.

### 3.1 The pipeline

On a site push, on a version tag in the application repository, or on manual dispatch: check out the
site tree, check out the application tree with a read-only cross-repository token, **detach onto the
latest version tag**, verify the tag against the application package version, build with the web base
path and with the reserved commit variable cleared, verify that the version and the source commit are
actually present in the built assets, merge site and application output, and upload.

Cross-repository checkout is needed because the web application's canonical source is the same tree
the desktop builds from, differing only in base path, while the static site lives in the other
repository. When this was done by hand, both repositories were checked out locally and merged before
upload; the pipeline automates exactly that.

### 3.2 Web Vinc is pinned to a release tag

**The invariant: what is at the web application path is always exactly some release's code.**

Before this, the application repository was checked out with no ref, which meant the default branch
head, and it drifted in both directions.

- **The web shipped untagged code.** Fourteen application files changed after a release tag while both
  the desktop installation and the web reported the same version, so the version a user saw became a
  lie.
- **And when the application changed, the web did not.** The only trigger was a site change, so an
  application change with no accompanying site change left the web on old code. Measured: an
  application change went unreflected on the web for **ten hours**, and then rode up **by accident**
  on a push that touched the site for an unrelated reason.

**Three fixes.**

1. **Pin the tag.** Select the highest version tag and check it out detached. If the tag and the
   application package version disagree, **block the deployment**, because that means the version
   synchronization step was skipped and the web would misreport its own version. To ship a non-tag ref
   urgently, override it through the manual dispatch input, which warns instead of blocking, since the
   bypass is deliberate.
2. **Fix the build commit.** The bundler prefers the build service's commit variable, and in this
   workflow that variable holds **the homepage repository's commit**, so the web application stamped
   an identifier unrelated to its own source. Measured: a stamped identifier that does not exist in
   the application repository at all, which defeated the entire point of the device. Clearing the
   variable in the build command makes the bundler fall back to reading the checked-out commit, which
   is the pinned tag's.

   > **A step-level environment declaration cannot clear it.** Names under the build service's
   > reserved prefixes are ignored when declared that way and the original value survives. It was
   > first done that way and **two deployments were blocked** as a result. Fix number three caught it,
   > and the log said exactly the right thing, that the application source commit was absent from the
   > artifacts. **The shell has to clear it, not the runner.**
3. **Verify it landed.** After the build, search the assets for the version and commit strings and
   fail if either is missing. If fix two is ever quietly undone, this catches it, and in the incident
   above it did exactly that.

**Refreshing the web when a new tag appears** is the job of a trigger workflow in the application
repository, which calls the site workflow through the API on a version tag. It requires a dispatch
token scoped to write actions on the homepage repository, and **fails red without it**, because
letting it pass silently means nobody learns the web was not refreshed. It is a separate workflow from
the desktop release, so a missing token leaves the release itself green.

> To reflect something immediately, run the site workflow manually at any time; it builds from the
> latest tag.

**Repository secrets.** The homepage repository needs the three deployment identity values and a
**read-only** cross-repository token for the application repository. That token is not the release
token, which has write access to the release repository: **different purpose, different scope, do not
reuse**.

The application repository needs one more, in the opposite direction, which the first token cannot
cover: the dispatch token described above.

> **The static web app's deployment token is deliberately not kept as a secret.** It is long enough
> that a person copying it truncates it, which happened twice and produced an invalid-token failure
> while the original token was fine and manual deployment with the same value had succeeded. Keeping
> it also creates double management when it rotates. The workflow signs in with the federated identity
> and reads the token at run time, masking it from the log.

> **A correction of 2026-08-05.** This section used to claim the command line client for the code host
> was not installed and that secrets therefore had to be registered through the web interface. That
> was **wrong**. It is installed and authenticated, with sufficient scopes to set secrets, rerun jobs,
> dispatch workflows and read failed logs. The error caused a stretch of time in which diagnosing a
> failed delivery was believed to require a person opening a browser.

### 3.3 The linked backend, and the door it opens by accident

Linking the static web app to the account container makes account routes under the site's own origin
proxy through to the container, so **to a browser it is one origin**. That is why session cookies,
cross-site request protection and cross-origin handling needed no code changes at all. The link is a
property of the static site resource and survives redeployment.

> **Side effect, measured 2026-07-29: the account container is no longer publicly reachable.** Linking
> it makes the platform switch on **its own built-in authentication** for that container. Calling the
> container's own hostname answers with an authorization challenge and only requests through the
> static web app pass. It is a security gain, restoring a boundary the old reverse proxy used to
> provide, but **anything health-checking or monitoring the container hostname directly breaks
> quietly**. A verification step in the account pipeline did exactly that and had to be pointed at the
> site hostname.
>
> By contrast the **hub is not linked**, so it does not get this protection: its internal paths remain
> publicly reachable and a signature check is the only gate, see [[vinc.ops.risk-audit-2026-08-05]].
> The same mechanism later caused a full outage of member API keys, [[vinc.ops.incident-2026-08-04-easyauth]].

Static asset caching is handled by the platform's own content distribution with hash-based
invalidation, so the manual cache directives of the previous reverse proxy are gone.

## 4. Account backend

A small Node service with a lightweight HTTP framework and a relational driver, on a quarter of a
core, single replica.

**Containerization has two pressure points**, both found by reading the code. The email templates
directory has to be a **sibling of the build output**, because the mailer resolves it relative to its
own module location, a constraint carried over from the previous deployment and preserved in the copy
order. And the listen host had to become configurable rather than hardcoded to loopback, because the
platform's ingress attaches from **outside the container's network namespace**, so the process must
listen on all interfaces.

**The pipeline** runs the type check and tests again, separately from the pull request pipeline,
because a merge commit does not re-run the pull request gate and this is the last chance before
deployment; then builds the image in the registry's own build service, so the runner needs no
container daemon; then updates the container app's image tag while leaving every other setting alone;
then verifies the health route.

**Database and secrets.** The runtime connects with a data-manipulation-only account. Runtime secrets
live in the container app's own secret store rather than a file on a machine.

### 4.1 Changing a secret's value does not change the running container

Secrets are injected **when the container starts**, so replacing a value leaves the running revision
holding the old one. Two commands **succeed while doing nothing**: updating an environment variable to
the same secret reference leaves the template unchanged so no new revision appears, and copying a
revision without a suffix produces an identical template and is judged the same revision.

**The incident of 2026-08-02:** a setup script issued both in order and printed success, while the
active revision's creation time was **the previous day**. The server was still checking the old token
and the only symptom was a disguised client error, so the cause was invisible. The script's fixed
sleep had no basis: when no new revision appears, no amount of waiting changes anything.

**The answer** is to give a suffix and force a new revision. In single-revision mode traffic moves
once the new revision is healthy and the old one is cleaned up, so unlike a restart this is seamless
even at a single replica. **The indicator to check is the active revision's creation time.** A
command's exit code proves nothing here, which is the same principle as inspecting the artifact
itself in section 2.2. When only re-verifying, do not rotate the token.

> **A known limitation, recorded as risk and not changed:** the session cookie declares no domain.
> Harmless while the linked backend makes everything one origin, and broken the moment a separate
> client calls the account hostname directly, for instance a mobile application. There is no
> cross-origin middleware either, for the same reason.

## 5. Hub

Half a core, one gibibyte, single replica. **Scaling out is impossible** because of the embedded
store's exclusive writer lock, section 5.3, and a single state file. That also means idle compute is
billed continuously, see [[vinc.ops.cost-management]].

> **A correction of 2026-08-03.** This line long said the constraint was in-memory authorization
> state. That was false. Authorization state was designed to persist from the beginning. What actually
> happened is that **the file holding it fell outside the persistent volume** during the migration,
> section 5.6, and the document froze the symptom into a design claim. It now lives on the persistent
> volume and survives restarts.

### 5.1 The image

A completely different build path from the desktop. A container has no static-analysis blind spot for
dynamic imports, so freezing is unnecessary and the module runs directly. The driver pin matches
section 2.2.

**Extension seeding, settled by measurement, and to be followed exactly on any reproduction.** Do not
install extensions inside the build container: the download fails certificate verification, a problem
specific to that build environment, since the same URL answers normally from a local client.
Instead, the extension binaries are **downloaded in advance and committed to the repository**, and the
image points at them through an explicit directory variable. That variable override is supported and
is not limited to frozen builds, which was confirmed here. **The directory must be flat**; splitting
into subfolders breaks matching, and that mistake once left one extension silently unavailable.

**Persistence is a mounted file share** registered on the container environment and mounted into the
container, with the data directory pointing inside the mount. Verified by writing, searching,
restarting and searching again for the same data.

### 5.2 The pipeline, and two traps in the registry build service

Pushes under the server trees, the image definition, the project file, the environment check script,
and an **enumerated** list of packaging files trigger a registry build and an image update, then a
tool-listing check with a static token.

**The packaging path is a list, not a prefix.** The desktop version stamp file is deliberately absent,
so a desktop version bump does not redeploy the hub. Conversely, **adding a new build script under
packaging means adding it to that list**, or edits to it will never redeploy.

**Two traps that pass locally and fail only in the registry's build service**, both experienced.

1. Its pre-build dependency scan parses the image definition with its own parser and cannot tell a
   heredoc body from instructions. An import statement inside the body is read as a base image
   directive and the build dies in seconds with a parse complaint.
2. Even avoiding that, **heredocs do not work** in the classic builder: a chained command with a
   heredoc collapses into a single line, the shell never delivers the body, and the interpreter exits
   immediately on empty input. **The build is green and the artifact is absent.**

The second is the dangerous one. Model baking was skipped entirely while the workflow reported
success, and the only visible sign was the **image size**, roughly a third of what it should be. At
run time it fell back to a full-precision model, ran out of memory, and took the MCP server down with
it.

**The rule: no inline scripts in the image definition.** Copy a separate file and run it, and have
that script **verify its own output and fail**, as the model baking script does by refusing to finish
below a size floor. **And put at least one artifact-level indicator, a size or a count, into
deployment verification.** A green workflow does not look at what it produced.

This pipeline is independent of the desktop release, so server deployment is not chained to desktop
releases. **A change to the stored format is different:** this pipeline only swaps images, so a change
to the graph schema itself must not go out on the automatic trigger. Dispatch it manually and carry
out the data migration separately. This principle is inherited from an earlier incident where an
automatic deployment of a storage engine change met data in the old format.

### 5.3 A single writer makes rolling updates fail structurally

**The platform's default, start the new revision and then retire the old one, does not work here.**
Both revisions hold the same file share and the store takes an **exclusive file lock**, so the
incoming sidecar cannot open the store, reports a null driver, and the entrypoint health check times
out, leaving the revision failed.

**Measured 2026-07-29:** exactly that on the second deployment. Deactivating the old revision to
release the lock let the new one recover on its own. The first deployment passed only because there
was no predecessor, meaning the trap was **only the first deployment works and every one after fails**.

The pipeline enforces the order: update the image, deactivate every old revision so the lock is
released, then confirm the new revision is running. A single writer makes **a brief interruption
during deployment unavoidable**; the account backend is stateless and unaffected.

> Follow the same order by hand. Updating the image and stopping there leaves the new revision quietly
> failed while the old one keeps serving, so **the deployment did not happen and the service looks
> fine**, which is hard to notice.

#### 5.3.1 Restarting a revision cannot succeed in this application

Same lock. At a single replica the platform **starts a new pod while keeping the old one alive**, and
the new pod's sidecar cannot open the store, so it crash-loops indefinitely while the old pod keeps
serving. The revision reads as failed and unhealthy while **the service is fine**, which is the same
trap again. Measured: two replicas coexisting for twenty-five minutes with five restarts on the new
one.

**Do this instead.** Usually, do nothing: if the reason to restart is a deployment, the pipeline
already swaps revisions and releases the lock. If a cycle is genuinely needed now, **create a new
revision and then deactivate the old one**, and the deactivation is the line that releases the lock,
without which the new revision falls into the same loop. Expect one to two minutes of downtime between
the two. To reverse, activate the old revision and deactivate the new one.

**Repository secrets** for the application repository: the three deployment identity values and a
static bearer token for post-deployment health verification, whose value must match the production
container's secret.

### 5.4 The runtime environment list

**This list is not codified anywhere.** The values were entered by hand and neither infrastructure code
nor the pipeline validates them, so moving platform or recreating the application **drops them
silently**. Section 5.5 is that failure happening twice.

| Variable | Required | If absent |
|---|---|---|
| sidecar token | yes | the entrypoint exits immediately |
| data directory | yes | defaults inside the mount. Must match the file share mount point, and **the gate checks the value**, blocking anything outside the mount |
| MCP static token | yes | no static bearer, so deployment health verification is impossible |
| **issuer** | **yes** | **the entire authorization layer switches off, section 5.5** |
| authorization admin key | yes | bootstrap auto-approval is unavailable |
| hub signing seed | yes | request assertions cannot be signed, so member approval is disabled |
| account public key | yes | account assertions cannot be verified, so member approval is disabled |
| connect URL | yes | there is a code default. **When overriding, check the domain** |
| **account internal URL** | **yes** | **member key verification loops back to a dead port and every member key returns unauthorized, section 5.5** |
| quota switch | policy | anything but on means **unlimited tool calls** |
| embedding switch | optional | defaults on. Off means no worker, so new documents get full-text indexing only and their vectors stay empty |
| embedding interval | optional | how long to wait when nothing is pending |
| embedding cache | optional | **must be on the persistent volume**, or a large model download repeats every restart |
| state directory | optional | the **working copy**. **Must be local**: pointing it at the share makes the state database refuse its first write, section 5.6. The gate checks the value |
| state snapshot | optional | the **persistent copy**. Must be inside the mount, or authorization registrations vanish on every deployment, section 5.6 |
| journal mode | optional | leave it at the rollback journal. **Do not raise it to write-ahead logging**, which creates companion files so that copying one file is no longer a consistent snapshot, section 5.6 |

Member approval turns on only when **four** things are present together: the authorization gate, the
hub signing seed, the account public key and the connect URL. Missing any one silently degrades to the
administrative key path.

> **A documented drift:** the quota guidance in this runbook and the guidance in the environment check
> script disagree about the free allowance. The policy of record is [[vinc.canon.account-policy]] and
> it is the higher figure. Both copies here should be reconciled to it.

### 5.5 Three failures of the same family

**All three are "starts fine, health answers fine, only the function is dead".**

#### The issuer, 2026-07-30

Connectors could not reach the endpoint while health answered normally, so **the server looked
perfectly fine**. The chain: the variable was absent, so the entrypoint did not pass the authorization
flag, which is conditional on it; so the gate was null; so the protected-resource discovery route
returned not-found saying authorization was disabled; so the issuer fell back to a plain HTTP form of
the request host; so the challenge header on an unauthorized response pointed at plain HTTP rather
than TLS; so client discovery failed.

**The lesson: the hub's authorization layer turns on and off with one variable, and the off state does
not appear in the health response at all. A healthy health response is not evidence that authorization
is alive.** The same check also found the connect URL pointing at a **domain that had been deleted**,
a leftover from the domain move.

#### A recovery deployment planting a Windows path, and a missing internal URL, 2026-08-04

Both are of the same family, and **the environment check script built to prevent that family passed
both**, because one key was not on its list and the other was checked for existence but not for value.

**First, the data directory pointed outside the mount and an empty graph was served.** During a
recovery deployment the update command was run from a Unix-emulation shell on Windows, and its path
translation rewrote the argument into a Windows path, which was planted verbatim. On Linux that string
is a **relative path, not an error**, so the sidecar created a fresh empty graph inside the image
layer and started normally: the banner named the driver, health answered, the graph API answered.
**All of it looked correct** while the entire hosted graph was invisible and writes were going to
ephemeral storage. The decisive evidence was that the file share's database files had not been
modified at all since that revision started. The state snapshot had drifted out of the volume by the
same mechanism.

> **A derived correction:** the conclusion that a tolerant replay option had worked in that deployment
> **had no basis**. The healthy responses came from opening an empty database, and the option had
> never actually run. The deployment that put the path back was its first real test.

**Second, the account internal URL had never been set, and every member key failed.** Member keys are
verified by the hub asking the account. The code default for that address dates from the era when
account and hub shared one host, and in this architecture they are separate applications, so the
loopback port has nothing behind it. The verification call was refused, the exception path
fail-closed to no result, resolution missed, and every well-formed key returned unauthorized, **with
nothing in the health response, the banner or the logs to say why**. It had been in that state since
the migration.

**The gate was strengthened in the same change:** the internal URL became required, the data directory
is checked for being inside the mount, and the four address-bearing variables are checked for being
plain text and for **not pointing at the dead domain**, including its subdomains. A regression test
pins the exact bad value from the incident.

#### What to actually verify after a deployment

Health alone is not enough. Check the protected-resource discovery route answers rather than
not-found; check the challenge header on an unauthorized call names TLS rather than plain HTTP; check
the authorization redirect lands on the live connect domain. **Then check two more things the first
four do not cover**, because the data path and the member key path can both answer normally while
dead: read the data directory value back and confirm the file share's files are being modified, and
call the endpoint with a real member key.

### 5.6 Two lessons about state storage

**The symptom:** connector attachment fails with an unknown client identifier, and once in that state
a client cannot recover on its own. The path is unique to one branch, so the message identifies it
exactly.

**The cause.** Authorization client registrations and tokens live in a small embedded state database
whose directory variable **was set nowhere**, not in the image, not in the entrypoint, not in the
gate. Unset, it falls back to a path under the container user's home, which is **inside the image
layer**, while the only persistent mount is the file share. So every new revision started with an
empty registration table. Clients cache the identifier they received at first registration and present
it again on reconnection, so the failure is permanent.

**This was a regression lost in the migration, not a design.** The startup code says explicitly that
connectors survive restarts and names a production path, but that path belonged to the previous
machine's service directory. The replacement setting never arrived and only the intent survived. Token
and code storage share that database, so **every deployment also invalidated every user's tokens**,
meaning re-consent was required on each deployment even after successful re-registration.

**The first fix failed and took the hub down.** Moving the state database onto the persistent volume
seemed obvious. On deployment the entrypoint reported the intended persistent path, the sidecar came
up, and the MCP process died on a locked database at its first schema write. The revision fell to
failed, and in single-revision mode the old one was already gone, so the endpoint returned not-found
until a rollback to the previous image restored it.

**Two lessons.**

- **That embedded database cannot run on a network file share.** It is not only about journal modes.
  The engine relies on byte-range locking, the share does not provide it properly, and it fails **from
  the first write**. Lowering the journal mode was not enough. Do not confuse this with the graph store
  and the model cache living happily on the same volume: neither of them uses that locking model.
- **A failure of the state store must not kill the service.** The constructor's exception propagated
  and ended the MCP process. Persistence is a convenience and authentication is a precondition, and
  the priorities were inverted.

**The final fix separates where it is read and written from where it is kept.**

1. **Working copy local, snapshot on the volume.** The entrypoint defaults the working directory to
   local storage, where the database behaves normally, and the snapshot to a file inside the mount,
   which is **copied only**. On startup, if the working copy is missing it is restored from the
   snapshot, and every write that must persist updates the snapshot through a temporary file and a
   rename. Quota counters are not snapshotted, since they are soft state that resets with the window
   and there is no reason to copy on every call.
2. **Fail soft.** A failure constructing the store is caught and **degraded to memory**, and snapshot
   failures swallow their exceptions and leave only a status string. Making the worst case "authorize
   again after a restart" rather than "the service is down" is the core lesson of this incident.
3. **The rollback journal stays.** The reason changed: no longer the share, but that write-ahead
   logging creates companion files, so **copying one database file is no longer a consistent
   snapshot**. A rollback journal leaves a single file after commit, so a copy is a snapshot.
4. **The gate checks the direction of both paths.** Note that the check is **the exact opposite** of
   the first incident's: a working directory pointing into the mount is the value that reproduces the
   second incident, so it blocks; the snapshot pointing outside the mount blocks. Both unset is
   normal, since the entrypoint supplies the defaults.
5. **Health carries state store diagnostics.** The sharp edge of this incident was believing something
   worked while it did not, with health answering only that it was fine. The path, journal mode and
   snapshot outcome are now reported.

**One action is needed after deploying the fix.** Already-issued client identifiers do not come back,
since the database starts empty once. Reconnect the connector so registration runs again, and from
then on it survives deployments. **Verify** that the snapshot state reads as successful and, from the
second deployment onward, that the restore state reads as restored, then restart the revision and
confirm a tool call works without re-consenting.

### 5.7 The embedding worker

Before the migration a scheduled timer ran a separate environment to fill vectors for pending
documents. The migration dropped it, and the hub container mentioned embedding **nowhere**. Every
document arriving through web editing, pack import or file drop got full-text indexing with empty
vectors, so **semantic search degraded with no error at all** and there was no signal.

Now the entrypoint starts the worker **inside the container**, after the sidecar reports healthy.
Reversing that order makes the first round fail for nothing.

**Why a lean runtime rather than a tensor framework, on measured grounds.** The container ceiling is
one gibibyte and the tensor framework's resident set would sit near it, and **an out-of-memory kill
takes the whole container, meaning the MCP server, down with it**. Raising the container size doubles
the always-on charge, so the lean runtime, which finishes well under the ceiling, avoids the increase.
The image cost is a few hundred megabytes rather than gigabytes.

> **Identity of the vector space is the whole of this switch.** Existing vectors came from the previous
> stack and new vectors mix into the same index, and a mismatch degrades search **with no error**.
> This was verified by cosine comparison in a disposable container, with a worst case of five nines of
> agreement including over-length and empty inputs, using an official model export and the pooling
> configuration published with the model, not a guess. **Re-run that verification whenever the
> embedding pipeline is touched**; the procedure is in the worker's own comments.

**The worker dying does not take the container down.** It is an auxiliary function, and wiring a
restart to it entangles reclaiming the single writer lock, making MCP availability hostage to a
background task. It can be switched off through its variable without rebuilding.

> As of 2026-08-07 **the worker has never actually loaded its model**, so the hub is running the
> retrieval half of the product on full-text search alone. See [[vinc.plan.embedding-pipeline]].

## 6. Database

A small burstable managed relational server with automatic growth enabled, managed daily backups kept
for a week without geographic redundancy.

**Two accounts, on the same least-privilege principle as before.** One data-manipulation account for
the runtime, and one privileged account intended for migrations. In practice the first bootstrap ran
as the administrator, and least privilege should be brought into line from the next migration onward.
The previous provider's managed database blocked user creation and forced a single account; this one
permits it, so **the original design could finally be applied**.

**Migrations are still applied by hand.** Run the bootstrap schema and grants, adjusting host patterns
because the previous private-network assumption no longer holds, then the forward sections of each
migration file in order, and record the applied filenames in the migrations table by hand. The
migration tool named in the documentation is **still not installed**, and the gap between the
documentation and the practice is unchanged from before. Do not infer whether something was applied,
query the migrations table.

**Automatic storage growth cannot be reversed.** Once it grows it never shrinks. The risk and the
defences are in [[vinc.ops.cost-management]].

## 7. Cross-surface ordering

**Account change with a schema change:** apply the migration to the database, then let the account
pipeline deploy the code, then the site if needed. Schema before code, unchanged in principle from
before the migration.

**Hub storage format change:** carry out the data migration by hand, then trigger the hub pipeline
manually. Do not leave it to the automatic push trigger, section 5.2.

**Anything touching the deletion cascade:** hub first, then the account backend, then the static site.
Reversing it opens a window in which the account's signed deletion call meets a not-found and
fail-closes.

## 8. Deployment identity

Established during the migration. To add delivery to another repository or service, create the
application registration and service principal once, assign the contributor role **scoped to the
resource group rather than the subscription**, then add one federated credential per repository.

> **Do not compose the subject by hand.** Copy the string the code host actually sends, which appears
> in the workflow log's federated token details.

### 8.1 The subject is now an immutable identifier, incident of 2026-08-05

**Symptom:** all three delivery workflows died at sign-in with no matching federated identity record.

**Two causes were stacked.** What was registered was the old form, naming the organization and the
repository by name. What the code host actually sends now is a form carrying **numeric identifiers
alongside both names**, in the shape of owner and identifier, then repository and identifier, then the
branch reference.

The organization had been renamed **and** the code host had moved to immutable identifiers.
**Correcting only the name still does not match**: however exactly the name is written, without the
numeric identifiers it is a mismatch. Anyone who skips the log and reasons "the organization was
renamed, so fix the name" fails a second time before finding out.

The two credentials now registered carry the immutable form for the application repository and the
homepage repository, both on the main branch reference. Because they are immutable, **renaming the
organization again will not break them**, which is why the code host moved to this form. The two
old-form credentials were deleted after confirming green.

**How quiet was it:** the hub delivery had been failing for this reason **since 2026-08-04** and
nobody knew. The desktop release does not authenticate to the cloud and stayed green, and the hub kept
serving its previous revision so it looked healthy from outside. **"The deployment did not happen" and
"the service is down" are different things, and the first has no signal.**

**Diagnosis, next time the same symptom appears:** read the failed run's log for the subject claim and
the error code, list the registered federated credentials, and **put the two strings side by side and
compare them character by character. Do not guess. Only the log knows what the code host sends.**

> **A shell trap:** a filter expression on the credential listing is eaten by the Windows shell's
> parser and dies on a syntax error. Take the raw output and filter it in the shell instead, or name
> the credential directly, since the identifier argument also accepts a name.

The workflow side needs only the sign-in action and the token-issuing permission; every later command
reuses that session. **There is no long-lived client secret anywhere**, so that leak risk is
structurally absent.

### 8.2 Three more measured traps

**A default working directory applies to the entire workflow.** Declaring one at the top of the
account pipeline put not only the test job but also the build job inside that directory, so the image
build looked for the directory nested inside itself and failed, **leaving no trace in the registry's
build history**, which delayed diagnosis. Scope it per job.

**The static site deployment action has no input for skipping the API build.** Passing one produces an
unexpected-input warning. To disable it, set the API location to an empty string.

**Unix-emulation path translation on Windows rewrites more than resource identifiers.** A resource
identifier passed to a cloud command is mistaken for a path and rewritten, which once made a
backend-linking call fail with not-found. **And environment variable values are rewritten too**, which
is what planted the Windows path in section 5.5. **Any value beginning with a slash is at risk**, and
almost every absolute path inside a container does. Three ways out: disable the translation for that
command, run it from the Windows shell instead, or double the leading slash and confirm the value
survived. **Whichever is used, read the value back afterwards.** That incident's command exited
successfully and the revision started normally; without reading it back, nothing showed.

## 9. Rollback, honestly

| Surface | Reversal |
|---|---|
| desktop | re-point the latest release at the previous one |
| static site and web application | the platform keeps deployment history, so a previous deployment can be swapped back through the portal |
| account backend | revisions are retained, so reassigning all traffic to a previous revision is an immediate rollback |
| hub | the same, **except when the deployment changed the data format**, in which case reverting the revision leaves the volume in the new format. Section 5.2's advance check exists to prevent that |
| database | forward only, no rollback. Restore from the managed daily backup through the console |

## 10. Checklists

**Desktop release.** Bump the version through the script, write the changelog, push the tag, confirm
the three-platform pipeline is green, publish the draft, verify the update manifest.

**Account or hub deployment.** If the schema changed, migrate first and confirm through the migrations
table. Pushing to the main branch runs test, build, deploy and health verification automatically.
Confirm the workflow succeeded; a failure leaves the previous revision serving, so traffic is
unaffected, and the platform does not shift traffic to an unhealthy revision. For the hub, decide
first whether the storage format changed, and if so dispatch manually instead.

**Static site and web application.** A site push deploys automatically. An application change alone
does not trigger it; the web tracks release tags, so use a manual dispatch to push an application
change out urgently.

**Cost.** Review the checks in [[vinc.ops.cost-management]] at least monthly, and immediately on any
budget alert.

## 11. Related

| Document | Contents |
|---|---|
| [[vinc.spec.azure-infrastructure]] | resource inventory, topology, names and routes |
| [[vinc.ops.cost-management]] | cost structure, budget, and the ways a bill can explode |
| [[vinc.ops.risk-audit-2026-08-05]] | the standing risks this runbook's gaps create |
| [[vinc.ops.incident-2026-08-04-easyauth]] | the outage caused by the linked backend's built-in authentication |
| [[vinc.research.deploy-pipeline]] | the survey that produced most of the automation described here |
| [[vinc.release.changelog]] | what changed in each release |
