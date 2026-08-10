---
doc_id: vinc.ops.risk-audit-2026-08-05
layer: ops
domain: vinc/security
authority: record
title: Full risk audit of 2026-08-05, with status refreshed
updated: 2026-08-07
inherits_from: [vinc.ops.deployment, vinc.spec.dataplane, vinc.spec.web-system, vinc.ops.cost-management]
tags: [ops, security, risk, audit, tenancy, availability, cost, record]
---

# Full risk audit of 2026-08-05, with status refreshed

> **A record.** The audit was carried out on 2026-08-05 across the three live repositories, the actual
> cloud configuration, and the as-built documents in the graph. Its categories are outage, security,
> operations, cost, infrastructure collapse, external connection, and cross-account error.
>
> **Because an audit that is not re-checked becomes fiction, every P0 and a sample of the rest were
> re-verified against the code on 2026-08-07.** Each item carries its status. Items that could not be
> re-verified from the source, because they depend on cloud configuration or on live traffic, say so
> rather than being assumed either way. Nothing below has been silently marked fixed.

## 1. The two headline findings, and both are still true

1. **There are hosted paths with no owner predicate.** The read-isolation and write-tenancy decisions
   `adr-security-001` and `adr-security-003` are enforced on node lookup and on fragment writes, but
   **the chunk arm of search** and **the whole of the synchronization apply path** sit outside them.
   The first leaks another member's document text as a search excerpt. The second lets one valid
   member key delete another member's concepts and edges. Both were confirmed in the code then, and
   **both are still live on 2026-08-07**.
2. **The account server's magic-link tokens do not check their purpose.** A team invitation link,
   valid for seven days and mailed to a third party, or an account deletion confirmation link, will
   mint a full login session for that address if it is submitted to the verification route. That is an
   account takeover path. Confirmed in the code then, and **still live on 2026-08-07**.

Everything else is of the family this project keeps meeting: **starts fine, health answers fine, only
the function is dead.** This audit found several new instances of it: a sidecar that can die while
health still answers, document synchronization that was never wired, a billing sweep with no caller,
and starter pack seeding that is a manual step.

**And the family struck again during the refresh window.** All three delivery workflows failed from
2026-08-04 because the code host moved its deployment identity subject to an immutable form, and
nobody noticed until 2026-08-06, because the desktop release does not authenticate to the cloud and
stayed green while the hub kept serving its previous revision. That is section 6.1 of this audit
proving itself two days after it was written. The account is in [[vinc.ops.deployment]] section 8.1.

## 2. What was verified, and what was re-verified

Before writing anything as fact, nine claims were checked by opening the file. Everything else quotes
a line of reasoning that was not independently re-confirmed, so **look again before acting on it**.

| Claim | Confirmed on 2026-08-05 | Status on 2026-08-07 |
|---|---|---|
| the chunk arm receives no owner; owner reaches only the node fallback, and the server's filter set is layer and document identifier only | yes | **unchanged, still live** |
| the chunk table has an owner column and the filter builder already supports it, so **the means exist and are unused** | yes | **unchanged** |
| the synchronization stamp write, the node tombstone delete and the edge tombstone delete all lack an owner predicate | yes | **unchanged, all three still live** |
| the token peek and consume helpers carry no purpose condition, while the deletion and invitation consumers do | yes | **unchanged, still live** |
| the verification route uses those two helpers and then mints a session for the token's address | yes | **unchanged** |
| the cross-site request token is set in exactly one place, at login, while the session helper only slides the session | yes | **unchanged, still live** |
| the subscription expiry sweep has no callers | yes | **unchanged, still zero callers** |
| invitation lookup by token ignores status, and revoking updates only the invitation row while the token row stays alive | yes | **unchanged, still live** |
| the bridge's liveness probe uses a signal call that terminates the process on Windows, while the same repository documents and avoids that trap elsewhere | yes | **unchanged** |

**Live probes on 2026-08-05.** The hub health endpoint answered with a reachable sidecar and restored
authorization state, protected-resource discovery answered, the challenge header named TLS, the site
health route answered, and the account's internal verification path answered with a **body-carrying
application-level rejection**. So the exclusion applied during the 2026-08-04 incident was still in
force, and at the same time **that path is confirmed publicly reachable**.

**One positive finding worth stating.** The node link projection does carry owner predicates on both
ends, including record attachments, so read isolation is genuinely enforced on that path. The gap is
specific, not general.

## 3. Cross-account error, the highest priority

The hosted hub runs one store for many owners, so a missing owner predicate is not a weakness, it is
an incident.

### 3.1 Search chunk results have no owner filter, P0, still live

Search rows can carry **excerpts of another member's document text**. Node hits are filtered, which is
what makes this hard to notice, and the regression test only exercises the node fallback. Both the
vector arm and the full-text arm are affected.

The remedy is close to one line: set the owner into the filter map in the search handler, and on paths
where owner is mandatory, hosted and web, **refuse** when the filter map is empty, fail closed. Then
add a contract test for the chunk arm. The absence of that test is what kept this alive.

### 3.2 Synchronization apply deletes and updates other people's nodes, P0, still live

The apply path ignores owner in three places.

- **Node tombstones.** Any holder of a valid member key can push a tombstone naming another member's
  concept and it is deleted. The only guard compares the node's update time against the deletion time,
  and **the deletion time is attacker-controlled**, so the guard is inert.
- **Edge tombstones.** The same route removes another member's relations, and more quietly.
- **The stamp loop.** When the fragment merge correctly skips a node as owned by another tenant, the
  loop that follows still raises the revision and sequence number **of the skipped node**. That is not
  destruction, but it desynchronizes the victim's three-way merge base into quiet corruption.

Remedy: an owner predicate on all three statements, clamp the deletion time to a server-generated
clock, and remove skipped nodes from the write list.

### 3.3 The merge base has no owner dimension, P1

The synchronization base table is keyed on node identifier alone. Two members using the same concept
identifier overwrite each other's baseline and then merge against an arbitrary counterpart value.
**No error is raised and the data is simply wrong.** The key needs to become the pair of owner and
node identifier. The same root affects the push and pull cursors, the selected domain list and the
last clock value, none of which are namespaced by owner. It is the same lineage as the empty-pull
incident, where the workaround was applied and the root was left.

**Status 2026-08-07:** the base table's primary key is still the node identifier and the table has no
owner column at all, so it also cannot be purged by owner, see section 3.6.

### 3.4 The conventions document is one shared identifier across all tenants, P1

The conventions document identifier is a fixed literal and the document metadata table is keyed on
identifier. On the hosted store, **whoever regenerates it first owns it**, and for everyone else the
build fails with an owner mismatch while reads degrade quietly to the default template. The identifier
has to become owner-derived, with a migration.

**Status 2026-08-07:** the identifier is still a fixed literal.

### 3.5 Subject and type vocabulary is a process-global file, P1

The vocabulary store is a single file beside the database, one per application. On the hosted store, an
open type name created by one member enters the shared vocabulary as proposed, and another member's
conventions tool reads it as **this graph's vocabulary**. User-authored type names leak and the
vocabulary is polluted.

**Status 2026-08-07:** still a single file beside the database.

### 3.6 Job results are not bound to an owner, P1

Job identifiers are a monotonic counter, so they enumerate trivially, and retrieval performs no owner
comparison. Another tenant's ingestion, synchronization or backup results, including file paths, counts
and warnings, are readable, and the web proxy passes the job operation straight through. Identifiers
should become random tokens, with owner recorded and compared.

**Status 2026-08-07:** job retrieval still takes an identifier and no owner.

### 3.7 Team subscriptions leak into personal entitlement, P1, still live

The plan projection asks only whether **any** subscription is entitled, without looking at what kind
it is. Paying for a single team seat therefore makes that person's **personal** graph a paid one, on
both the account route and key verification. The reverse defect exists too: deleting a team cancels
**all** of that person's subscriptions, including a personal one, because the cancellation filter has
no team dimension.

### 3.8 Residue after account deletion, P2

The owner purge label set omits the edge tombstone table and does not touch the synchronization base,
so edge deletion markers and merge baselines survive an account deletion. Community packs go the other
way and are removed by a cascade, which turns **another member's gallery link into a not-found**. The
policy differs per surface.

**Status 2026-08-07:** the purge label set has since gained the node tombstone, document metadata and
document tombstone tables. **The edge tombstone table is still missing**, and the synchronization base
still has no owner column to purge by. The community pack cascade was not re-checked.

## 4. Authentication and session

### 4.1 Token purpose is unchecked, an account takeover, P0, still live

Neither the peek nor the consume helper constrains the purpose. So submitting a team invitation token
to the verification route with the confirmation flag set proceeds through all four steps: the peek
succeeds because an invitation token is equally unused and unexpired; the confirmation flag skips the
flow-binding check; the consume succeeds; and the route then finds or creates a user by the token's
address and issues a session for it.

Invitation tokens are valid for **seven days**, are mailed to a third party, and are the kind of link
people forward, because they are presented as a way to join a team. If no account exists, one is
created. Deletion confirmation tokens are promoted to login tokens the same way.

The token repository's own comments understand the danger precisely, warning that anyone handed the
link hijacks somebody else's invitation. **That defence went into the invitation consumer only, and
not into the login path that reads the same table.** The fix is a purpose condition on both helpers,
plus a regression test.

### 4.2 The request token does not slide with the session, P0, still live

The session cookie is reissued on every request with a thirty-day idle window, while the request token
cookie is issued once at login with the same lifetime. So **exactly thirty days after login** the
session is alive and the request token has expired, and from that moment every state-changing call
fails. **Even signing out requires the token**, so the user cannot get out of it. It is a timer.

### 4.3 Revoking an invitation has no effect, P1, still live

Lookup by token ignores the status column and the acceptance path does not check it either. Revoking
updates the invitation row and leaves the token row alive. **A revoked invitation link still joins the
team**, and because revocation freed the seat, the seat check passes too. That is full read and write
exposure of a team graph.

### 4.4 The forwarded address header is trusted, P1

The client address helper takes the first hop verbatim. Forging the header makes the rate limits on
magic link issuance, verification and handle lookup effectively unlimited. Unmetered magic link sending
also damages the sending domain's reputation.

**Status 2026-08-07:** the helper still takes the first hop with no trusted-proxy check.

### 4.5 Rate limiting, scheduled work and send loops are all in process, P1

The rate limiter states in its own comments that it assumes a single instance. At N replicas the
effective limit is N times higher, the purge schedule runs N times, and the **global** hourly ceiling
on release mail becomes per-replica, so bulk duplicate sending becomes possible. It happens to hold
today because both applications are pinned to one replica, and **nowhere is it written down that a
scaling setting is holding up a security assumption**.

### 4.6 Unused secrets and a single pepper, P2

Two secrets are required at startup and **are not used anywhere in the code**. The real security rests
on one pepper value, and rotating it invalidates sessions, API keys and subscription confirmation
tokens simultaneously, with no procedure written. A belief that the session secret was rotated does not
correspond to anything.

### 4.7 A dead domain remains in the trusted origin list, P2, still live

The default allowed origin list still contains the retired domain. Its address record was removed on
2026-07-29, so whoever obtains that address could obtain a certificate and control a trusted origin.
The same-site cookie policy blocks immediate theft, but there is no reason to leave it.

## 5. Outage and recovery

### 5.1 The hub graph has no backup, P0

The infrastructure document says so itself: file share snapshots are **not enabled**. Only the managed
database has backups, daily and kept for a week, and the entire member graph is unprotected. A storage
incident, an accidental deletion or format corruption all have no recovery path. It is a few clicks, so
**of everything in this document it has the best ratio of effort to protection**.

**Status 2026-08-07: not verifiable from the source.** It depends on cloud configuration and was not
re-checked. Treat it as open until someone confirms otherwise.

### 5.2 The health endpoint lies, on both layers, P0, partly addressed

- The hub carried the sidecar's reachability as a diagnostic field and returned two hundred
  regardless, so an external observer sees health while the sidecar is dead.
- The entrypoint watchdog exits after five failures, but that ends **the subshell only**. The first
  process keeps running and keeps answering, so the claim that the container comes down when the
  sidecar dies is not implemented.
- No platform liveness probe is configured, so nothing restarts automatically.

**Status 2026-08-07:** the diagnostic is present, and the code now states explicitly that the status
code is deliberately left at two hundred, on the grounds that no automatic probe is attached and that
tying automatic restarts to health is itself risky under a single writer lock, so it is meant as a
signal for a person. **That converts an oversight into a recorded decision, and it does not close the
gap**: an external monitor still cannot distinguish a healthy hub from a dead sidecar. The watchdog
and probe items were not re-verified.

### 5.3 A failed journal replay is fail-closed by default, P1

If the container is killed hard, by a deployment or an out-of-memory kill, and the write-ahead journal
is left incomplete, every later start fails to open the store and reports a null driver, with recovery
requiring a manual variable and a redeployment. A single automatic tolerant retry at startup and a
health marker are needed.

### 5.4 A failed reopen after snapshotting bricks the store, P1

The backup job closes the connection to avoid the lock, copies, and reopens in a cleanup block. If the
reopen fails, the handle is left empty and only the exception propagates. The sidecar is alive and
every read and write is dead, with no recovery short of a restart.

### 5.5 The bridge kills the desktop application on Windows, P1, still live

The liveness check calls the signal API with a zero signal, and the process identifier it checks is the
**desktop shell's**. On Windows that call maps to process termination, so it is not a query, it is a
kill. The same repository documents this exact trap elsewhere and implements a safe path, so promoting
that helper into a shared module finishes it.

### 5.6 Account deletion splits on partial failure, P1

The account route **consumes the deletion token first** and then calls the graph purge, returning a
server error if the purge fails. When the hub is down the user has to request the mail again, consuming
another token each time, until the hourly limit is hit. In the other direction, the team ownership
constraint restricts deletion while the guard counts only teams not already marked deleted, so a
marked-only team produces a **partial deletion in which the graph is gone and the account remains**.

### 5.7 The subscription sweep is never called, P1, still live

The sweep function is defined and has **zero callers**. The only scheduled work at startup is an expiry
purge. So a trial that ends, a past-due grace period that lapses and a scheduled cancellation that
arrives all leave the state unchanged and **entitlement permanently alive**. That is lost revenue, and
combined with the fail-open in section 7.2 it is effectively irreversible.

### 5.8 Mail failures are entirely silent, P2

The mailer does not throw, it logs, and every caller discards the promise. If the mail provider key
expires, no magic link goes out while the API keeps answering successfully. Since login is magic-link
only, that is a total login outage. There is precedent: the provider automatically disabled the webhook
after a run of consecutive failures and it was not noticed for days.

### 5.9 Payment webhook exceptions are swallowed and answered successfully, P2

If applying an event throws, for example on a brief database interruption, the handler marks it failed
and still answers successfully. The provider does not resend. **A user who paid and is not upgraded**
results, with no replay path.

## 6. Observability and operations

### 6.1 There is no availability monitoring at all, P0

The only alerting is the spend budget. Every incident in this project has therefore been discovered by
a user report or by chance: the missing issuer that disabled every connector, fourteen hours of serving
an empty graph, and the platform authentication that failed every member key. **Registering an external
uptime check at five-minute intervals against the hub health route and the site health route is the
second cheapest action in this document.**

**Status 2026-08-07: not verifiable from the source, and reinforced by events.** The delivery outage of
2026-08-04 to 2026-08-06 ran for two days undetected, which is the same failure mode one layer up.

### 6.2 Starter pack seeding is a manual step, not a migration, P1

The starter pack documentation says so directly: the schema is migrated and the rows are inserted by a
person. During the cloud migration the community pack table really did end up empty and the gallery was
served blank. It recurs every time the database is recreated. It belongs in the delivery pipeline as an
idempotent step, and an empty table should fail a gate.

### 6.3 Canonical document text does not synchronize, P1, still live

The server routes for exporting and applying documents exist, and the engine behind them exists, but
the orchestrator handles node deltas only and the hub proxy exposes only the node delta operations. So
pressing synchronize moves **no document text at all**. Concepts and episodes synchronize while the
canonical text stays trapped on one machine, and there is no error.

**Status 2026-08-07:** the synchronization orchestrator still contains no reference to document
synchronization.

### 6.4 The sidecar suppresses its request log wholesale, P1, still live

The log hook is empty, so client errors and server errors raised in the sidecar never reach the
container log, and the cause of a gateway error cannot be traced from the sidecar side. The hub has a
rule of logging errors only, and the sidecar should match it.

### 6.5 Hub runtime configuration lives outside the code, P1

Twelve variables were entered by hand and neither infrastructure code nor the pipeline validates them.
**Three incidents of that family have already happened.** The gate runs at deployment time only and
checks existence rather than meaning. Committing the container definition as infrastructure code would
make the source canonical, and the required-variable check should move inside the process and surface
on the health endpoint.

### 6.6 The proxy allowlist is duplicated in three places with no comparison, P2

The sidecar handler, the hub's proxy map, and the account's operation lists and role table. Three drift
incidents have already occurred, each removing exactly one feature: a missing ledger route made the
totals view return not-found, a missing navigation route left the sidebar blank, and a missing backfill
route disappeared silently. **Whatever is missing fails only in that one feature, quietly.** An endpoint
that publishes the operation list, compared in the pipeline against the other two, closes it.

### 6.7 Document drift, P2

- **The free quota figure disagrees between two places.** The deployment runbook states a small daily
  allowance while the environment check script states a much larger one, and the policy of record in
  [[vinc.canon.account-policy]] is the larger. **Still true on 2026-08-07.**
- Two homepage readme files still describe the previous provider's machine and reverse proxy.
- An operation list comment claims a document deletion operation is unexposed while it is registered.
- The account's internal route comments still claim the previous reverse proxy topology and a loopback
  binding, both stale.

## 7. Connectors and external access

### 7.1 Bridge retry behaviour differs from its own documentation, P1

The docstring says retries happen only when the connection itself fails, while the implementation
catches everything, so **read timeouts are retried too**. The bridge timeout is one minute and the job
timeout is ten, so a large document write, a pack ingest or a synchronization apply sends an
already-queued write two more times. The merge is idempotent, but the endpoint stamping and sequence
counter increment revisions and sequence numbers repeatedly and produce a synchronization echo.

### 7.2 Entitlement fail-open does not reflect downgrades, P1

The plan refresh keeps the last known value when the account lookup fails, and when the proxied
assertion carries no plan claim **no ceiling is applied at all**. A user downgraded by cancellation or
by a trial ending keeps a paid connector for the whole duration of an account outage. Combined with
section 5.7 that is permanent. The fallback needs a maximum age, and a missing plan claim should be
counted.

There is a contract gap in the other direction too. The account answers with distinct codes for an
unknown key, a failed assertion, an unpinned key and a database exception, but **nothing tells the
caller whether a retry is appropriate**. Treating everything as a refusal kills every connector during
a brief account database interruption; treating everything as a pass revives revoked keys.

### 7.3 Member keys sit in plain text in two places, P1

In the desktop web view's local storage, where `adr-infra-036` said keys would not be put on disk, and
local storage is on disk. One cross-site scripting flaw in the web view leaks a read and write key for
the entire account graph. And in the connector host's configuration, where the token sits as a plain
environment value and is printed by a single command. Because the endpoint address is an environment
value as well, changing it sends **the same key to an arbitrary endpoint**.

### 7.4 The hub's internal paths are publicly reachable behind one signature check, P1

The previous reverse proxy's blocking layer disappeared with that provider and no ingress rule replaced
it. Behind those paths sit an owner-forced read and write route and a **whole-account deletion** route.
The account's two internal paths are in the same state, as the intended side effect of the 2026-08-04
remedy. A single regression in signature verification is immediately the worst case. Depth needs
restoring, either by blocking the path at ingress or by separating the port.

### 7.5 Tool contract and behaviour disagree, P2

- The delete, edge delete and merge tools have no owner in their schema while the handlers read one.
  Over the local transport the owner is always absent, which makes those **unscoped deletions**, and no
  document says so.
- A target field is injected into every tool, and when a remote target arrives at the hub, the hub tries
  to make an outbound call. It should be injected on the local profile only.
- A body above the size ceiling reads as empty and produces a confusing protocol error. It should be a
  size rejection with an explicit reason.

**Status 2026-08-07:** the non-text-encoding body defect, which used to kill the request handler and
surface as a gateway error, **is fixed in the working tree**: such a body is now degraded to the same
path as a parse failure. Whether that fix is deployed was not verified.

### 7.6 A fixed port and a rebinding race, P2

The container binds a fixed port, so if it is occupied the entrypoint gives up after a health wait. The
desktop and the bridge use a pattern where the parent claims an ephemeral port, releases it, and the
child rebinds, so on Windows a port inside the hypervisor's reserved range makes the child's bind fail
and the screen shows only a health timeout. A path already exists where the child binds directly and
reports the real port on its output, and everything should use it.

## 8. Cost and resources

Summarized here and carried in full by [[vinc.ops.cost-management]] section 6.

- **The embedding worker's accumulation bypasses its own batch ceiling, P1.** Encoding runs in small
  batches while the entire result set is held in memory and then serialized. On a large document that
  exceeds the container ceiling, and **an out-of-memory kill takes the container and therefore the MCP
  server down**. Avoiding a tensor framework was motivated by exactly that risk, and it reappears on
  the accumulation axis rather than the batch axis. The sidecar having no request body ceiling, where
  the hub has one, makes it worse.
- **Automatic database storage growth is one-way, P1**, and community packs store whole pack bodies in
  rows with no hard delete.
- **Community pack download is unauthenticated and unmetered, P1**, reading a full body from the
  database on every call, with no cache.
- **Quotas do not apply on the web path, P2.** They gate the connector route only, so a free member can
  drive unlimited ingestion and search through the web.
- **Several things grow without bound, P2**: job results retained forever in memory, a tombstone
  collector and an expired-record purge that are defined and never called, disabled registry retention
  against a large image, and idle session rows surviving for weeks.
- **The vector index drop is global, P2.** The apply step drops the index every round and the index is
  table-wide, so with a worker on a five-minute cycle the vector arm may be off almost always: **paying
  for it and getting full-text quality**. Drop and rebuild belong once per round.

**Status 2026-08-07:** the tombstone collector and the expired-record purge still have no callers.

## 9. What can simply break

| Risk | State |
|---|---|
| single region, no redundancy | database backups are not geographically redundant, storage is locally redundant, high availability is off. A regional failure takes everything at once and there is no copy to restore from |
| the hub cannot scale out | an exclusive single-writer lock, so a traffic increase appears as an **outage**, not as a bill |
| hub deployment requires downtime | two revisions cannot share the volume. Updating the image alone leaves the new revision quietly failed while the old one keeps serving, so it looks deployed and healthy and is neither |
| a format-changing deployment cannot be rolled back | reverting the revision leaves the volume in the new format, and there is no volume backup, so there is no path at all |
| the apex address record is a fixed address | if it is reissued the whole site goes down with no deployment involved |
| the edge network is bypassed entirely | every record is direct, because enabling the proxy breaks managed certificate renewal, so there is structurally no filtering, caching or absorption layer and the route to adding one is blocked |
| certificate verification records | if the two verification records are lost the symptom is invisible until expiry, and then both subdomains fail at once |
| the retired domain's mail records | deleting them kills login mail. There is precedent for deleting only the address record from that zone |
| the database firewall admits all cloud traffic | reachable from any tenant in the cloud, with credentials as the only defence |
| the updater signing key | the passphrase is empty so the file itself is the secret, and losing both the local file and the stored secret puts **every installed user permanently out of automatic updates** |
| containers run as root, with no dependency scanning | automatic dependency updates, code scanning and auditing were absent, and auditing is explicitly disabled at install |

**Status 2026-08-07:** dependency update configuration has since been added to the application and
homepage repositories, and is still absent from the documentation repository. The rest of this table
was not re-verified.

## 10. Priority

### P0, now

| # | Item | Because | Status 2026-08-07 |
|---|---|---|---|
| 1 | a purpose condition on token peek and consume | invitation and deletion links take over accounts, 4.1 | **open** |
| 2 | an owner filter on the search chunk arm | cross-tenant text disclosure, 3.1 | **open** |
| 3 | an owner predicate on the three synchronization apply statements, and clamp the deletion time | cross-tenant destruction, 3.2 | **open** |
| 4 | slide the request token with the session | everything stops thirty days after login with no self-recovery, 4.2 | **open** |
| 5 | enable file share snapshots | the member graph has no backup, 5.1 | **not verified** |
| 6 | register two external uptime checks | no monitoring at all, 6.1 | **not verified** |

### P1, this cycle

Wire the subscription sweep to a schedule. Check invitation status on acceptance. Give the merge base a
composite owner key. Make the conventions identifier owner-derived. Scope the vocabulary store to an
owner. Compare owners on job retrieval and use random identifiers. Separate team and personal
entitlement. Make the watchdog signal the main process and let health report unavailability. Replace the
bridge liveness probe. Narrow the bridge retry exception. Wire document synchronization. Stream the
embedding apply and give the sidecar a body ceiling. Correct the trusted hop for the forwarded address.
Rate limit community downloads. Move starter pack seeding into the pipeline.

### P2, recorded and sequenced

Block the hub's internal paths at ingress. Add replay protection to assertions. Finish the purge label
set. Compare the proxy allowlist in the pipeline. Call the collectors that exist. Enable registry
retention. Extend dependency updates to the remaining repository. Enable geographically redundant
database backups. Clear the document drift.

## 11. Limits of this audit

- **Hub-side enforcement was not followed to the end.** The account server's owner scoping is solid: it
  never carries an owner from the request body, it uses the signed subject only, and it separates the
  assertion type for teams. But the enforcement all lives on the hub side, and the real strength of
  assertion verification can only be settled by a round trip with a live key.
- **Nothing was exploited.** The two P0 tenancy findings are certain as code paths, but no exploit was
  executed. Writing a reproduction test before fixing them is the safer order.
- **The account policy in [[vinc.canon.account-policy]] is entirely unimplemented**, so the team-related
  defects may not be exposed to users yet. Check the team feature flag.
- **The desktop application's native layer and front-end bundle were examined only at the surface.**
- **The 2026-08-07 refresh re-verified the source code only.** Anything resting on cloud configuration,
  on live traffic, or on whether a merged fix has been deployed, is marked as not verified rather than
  guessed at.
