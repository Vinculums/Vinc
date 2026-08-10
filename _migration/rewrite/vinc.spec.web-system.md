---
doc_id: vinc.spec.web-system
layer: spec
domain: vinc/product
authority: spec
title: The web system as built, site to hosted graph
supersedes: vinc.c.web-vincs-app
updated: 2026-08-06
inherits_from: [vinc.canon.principles]
tags: [spec, product, web, account, community, proxy, hosting]
---

# The web system as built, site to hosted graph

> Everything the product exposes over the public web: the marketing site, the account backend, the
> browser build of Vinc, the community gallery, and the hosted graph behind them. What runs, and why
> it is shaped that way.
>
> Four things it deliberately does not own. Cloud topology and DNS are
> [[vinc.spec.azure-infrastructure]]. Deployment procedure, pipelines and runtime environment are
> [[vinc.ops.deployment]]. Credentials, sessions and mail are [[vinc.spec.email-subsystem]]. Tier and
> team policy is [[vinc.canon.account-policy]]. Repeating any of them here would produce a second
> copy that drifts, which has already happened once in this corpus.
>
> The canonical domain is `vincs.io`. The identifier this document replaces carried `vincs.app`,
> which is a historic name: that zone now holds outbound mail records only, and the site, the API
> and the hub are all on `vincs.io`. Measured against `Vinc-homepage` and `Vinc` on 2026-08-06.

## 1. Four surfaces on one origin, and one on its own

| Surface | What it is | Served from |
|---|---|---|
| static site | marketing, docs, pricing, and every account and connector screen. No build step: the folder is the artifact | the static web app |
| account backend | sessions, newsletter, keys, connectors, billing, teams, the graph proxy, the community API | a container, reached through the site's linked backend at `/api/*` |
| web Vinc, at `/app/` | the desktop frontend built for the browser, authenticated by the account session | the static web app |
| community gallery, at `/community/` | public pack listings and their visual summaries | the static web app plus the account API |
| MCP hub, on its own subdomain | the hosted graph data plane: a sidecar and an MCP server over one embedded store | a separate container |

**Same origin is a requirement, not a convenience.** There is no `api.` host in front of the
account backend for browsers to talk to. The linked backend makes `/api/*` arrive on the site's own
origin, which is what lets the session cookie carry the `__Host-` prefix and stay host-only, and
what keeps every request first-party so that `SameSite=Lax` actually protects it. Splitting the API
onto a subdomain would silently downgrade both.

Only the graph data plane is on a separate host, because it is not a browser origin concern: remote
MCP connectors reach it directly.

### 1.1 The separation invariants

These are the rules the whole system is arranged to keep. They are stated once here and referenced
elsewhere rather than restated.

| # | Invariant |
|---|---|
| B1 | The **native** desktop app never calls the account API, never creates a session, and renders no login, email or account interface |
| B2 | The account backend and the hub never read each other's database and share no secret. Trust between them is **public key only**, each pinning the other's verification key (`adr-security-102`) |
| B3 | No silent synchronization. Data leaves a device only through an explicit action, and the account sync path is opt-in per topic |
| B4 | Web-only personal data, disclosed. The server holds an email address and, for abuse control, request addresses and timestamps that live and die with their row |
| B5 | The identity endpoint is whitelisted and exposes nothing derived from a device or a local store |
| B6 | Passwordless only. There is no password field and no password column anywhere |

**The most common misreading of this system is applying B1 to the web build.** B1 is a property of
the native binary. The web build is the same frontend source compiled for a browser; it
authenticates against an account by design, and therefore has a session, a plan and quotas. That is
not a violation of B1, because the native binary is not the thing talking to the account, and B2
still holds across the boundary that matters. [[vinc.spec.architecture]] section 4 states the same
boundary from the architecture side.

### 1.2 Two repositories, on purpose

The site, the account backend and the community server live in a repository physically separate from
the desktop app (`adr-security-101`). Separation by convention would not survive a careless import;
separation by repository means the desktop build cannot reference an account endpoint even by
accident, because the code is not there to reference.

The two pieces that live with the app instead are the web build of the frontend and the hub, and
both belong there for the same reason: they are the **same frontend source** and the **same graph
engine** as the desktop, and duplicating either would fork the contract that
[[vinc.spec.architecture]] exists to keep single.

The site build therefore checks out both repositories: the static folder from one, the `/app/`
bundle built from the other with its base path set.

> **Known gap.** The original specification promised a continuous integration guard that greps the
> desktop sources for any reference to the account host or an account endpoint. Measured on
> 2026-08-06 the app pipeline runs the test suite, the forbidden-vocabulary gate, the contrast gate
> and a typecheck, and **no such isolation grep exists**. B1 is currently held by repository
> separation and review, not by a check.

## 2. The static site

Every page is self-contained HTML with inline styles and scripts, which is what makes the strict
content policy in section 6 expressible at all. Pages are grouped by what they do rather than by
where they sit in the tree: marketing and documentation, the account screens, and the four
interstitials that email links land on.

The documentation section was consolidated into one tree with permanent redirects from the two
older paths, so external links and search results keep resolving.

### 2.1 The interstitial rules

Four pages receive a link that arrived by email: sign-in verification, newsletter confirmation,
unsubscribe, and account deletion. They follow rules that exist because a mailbox is a hostile
environment, and each rule answers a specific failure.

- **Zero third-party assets.** No external image, script, font or stylesheet. An email client's
  proxy, a scanner or a corporate gateway cannot be given anything to fetch
- **The token rides in the URL fragment**, so it is never sent to the server and never appears in a
  log or a referrer. The page reads it, strips it from the address bar immediately, and holds it in
  memory. Only the unsubscribe token is in the query string, because the one-click standard requires
  a form target
- **A page load changes nothing.** State changes happen on an explicit click. Mail scanners prefetch
  links, and a scanner must not be able to consume a single-use token or unsubscribe a reader
- **No referrer**, and same-origin fetches with credentials

Every page carries the English and Korean copy decks and a light and dark theme, defaulting to dark
(`adr-design-038`).

## 3. The account backend

Node with a small HTTP framework, one process, no framework magic. What is worth recording is not
the endpoint list, which changes and is readable from `account/src/app.ts`, but the shape that holds
across it.

**Two middlewares wrap everything under `/api`.** The first assigns a correlation id used in the
error envelope and in audit lines. The second is a first-party guard that, on any state-changing
method, requires an allowlisted `Origin` or `Referer` and a JSON content type. Webhook, one-click
and machine-triggered routes are named exceptions, because their callers have neither an origin nor
a session and prove themselves by signature or shared secret instead.

Route groups, in the order they mount: authentication, identity, newsletter subscription, account
management, public handle, connector keys, connector approval, the graph proxy, community, the
release-mail trigger, the internal service-to-service routes, unsubscribe, mail webhooks, billing,
and teams.

**The internal routes sit outside the `/api` guard on purpose**, and they are not reachable through
the site's linked backend. They exist for the hub to ask the account two questions: whether a
connector key is valid, and what tier a subject currently has. Both are gated by a signed assertion
from the hub with a short maximum age, which is the depth-in-defense layer beneath the network
position.

### 3.1 The content-type trap the platform introduced

Measured 2026-07-30. **A POST with no body loses its `Content-Type` header** crossing the linked
backend, so the first-party guard rejects it with a message about the content type. This was a
regression introduced by the hosting move: the previous reverse proxy passed the header through.

```
POST /api/auth/logout  (no body)  -> 400  Content-Type must be application/json
POST /api/auth/logout  -d '{}'    -> 401  not_authenticated   (guard passed)
```

The fix is on the caller: **every state-changing POST sends at least `{}`**. Relaxing the guard
would have been the wrong direction, because that guard is the depth-in-defense half of the
cross-site protection.

The failure was invisible in the way that matters. Logout and account deletion were both broken, and
because the sign-out button navigated away in a `finally` block, the session survived and the user
was bounced straight back to the account page. It read as "sign out does not work" rather than as a
rejected request. When adding a route, check whether it sends a body.

### 3.2 Data

One managed relational database, one schema, one account. Forward-only migrations, eighteen of them
as of 2026-08-06, applied by a migration tool rather than by any schema-diffing layer. Timestamps
are millisecond precision in coordinated universal time; email uniqueness is case-insensitive
through the collation rather than through application logic; every secret is stored as a keyed hash
and never in usable form, with the single exception explained in [[vinc.spec.email-subsystem]].

The tables, by what they hold: members, authentication tokens, sessions, newsletter subscribers,
send suppressions, connector keys, community packs, subscriptions, processor webhook events, teams,
memberships and invitations.

> The original plan called for a dedicated schema name and two least-privilege database users, one
> for runtime data manipulation and one for migrations. **Neither was adopted**, because the managed
> database tier does not permit creating additional users. The consequence is stated rather than
> hidden: the application runs with the account that also owns the schema. It is recorded again as a
> gap in [[vinc.spec.azure-infrastructure]] section 9.

### 3.3 Limits, purging and audit

Rate limits are layered and per subject. Sign-in requests are capped per address and per source
address, with the per-address cap dropping silently rather than erroring, so a limit cannot be used
as an existence oracle. Graph calls are capped per member per minute, community publishing and
importing per member per hour.

The counters are **in-process token buckets**, with one exception: the per-address sign-in cap is
counted from the token table, so it survives a restart. That split is deliberate. A limit protecting
against a mail bomb must be durable; a limit protecting a busy interface from itself does not need
to be.

An always-on purge job runs hourly and deletes expired tokens past a grace day, expired sessions,
and unconfirmed subscribers past a week. Retention is therefore a property of a running process, and
if that process stops, personal data accumulates. That is why the job logs its counts.

Audit lines carry the event, the outcome, the correlation id and a **keyed hash of the address**.
No token, no secret and no address in the clear is ever written, so a takeover attempt is detectable
without the log itself becoming a second store of personal data.

## 4. Web Vinc

The same frontend bundle as the desktop app, with its data base path pointed at the account instead
of at a local sidecar. On boot a gate checks for the session, synchronously through the readable
cross-site token cookie or by asking the identity endpoint, and sends an unauthenticated visitor to
sign in before any graph call is attempted.

### 4.1 The proxy chain, and why the owner is forced

```
frontend, web build, base path /api/graph
  -> account graph routes: session required, cross-site token required on writes, owner not accepted
  -> account proxy service: signs a short-lived assertion naming the member, the operation and the audience
  -> hub internal route: verifies with the account's public key only
  -> hub forces the owner from the assertion subject and discards any owner in the arguments
  -> graph engine
```

**No layer above the hub accepts an owner from the caller.** Not the frontend, not the account
route. The reason to force rather than to validate is that validation has a default branch and
forcing does not: one member's session cannot reach another member's graph even if every layer above
were compromised into asking for it. This is the transport half of the ownership rules
(`adr-security-001`, `adr-security-003`); the store half is [[vinc.spec.dataplane]] section 5.

If the account's signing key is not configured, the graph surface answers as unavailable and the
rest of the site is untouched. That is what allowed the feature to be deployed before it was
switched on.

### 4.2 A new operation has to be registered in three places

Proxying is an allowlist, not a passthrough. An operation reaches the browser only if it appears in
the sidecar routes, in the hub's proxy operation map, and in the account's own read or write map.

Miss one and **that one feature dies quietly on the web** while the desktop build keeps working.
This is not hypothetical: the totals view returned not-found on the web for a period because the
operation was in the hub map from the beginning and missing from the account map, and its drill-down
was present, so the inner door was open while the entrance did not exist. The same class of gap was
later found and closed for the document tree, the conventions rebuild and the evidence backfill.
The comments in `account/src/routes/graph.ts` name each one, which is the cheapest available guard.

Three families stay outside the allowlist on purpose, so it is not merely a performance filter:
global vocabulary, which has no owner dimension; the desktop-to-account synchronization machinery;
and local file backup.

### 4.3 Full write parity

The browser build is not a reader. Reads cover the map, the ledger, retrieval, coupling, decisions,
topics, the document canon and its view-specific extracts, and job polling. Writes cover preference
signals, activation, node and edge deletion, node merging, fragment and pack ingestion, document
writes and derivation, re-embedding, topic renaming, conventions rebuild and evidence backfill.

Parity is the point. A feature written once appears on both builds, so a divergence between them is
a bug rather than a design.

## 5. Community gallery

A member publishes a knowledge pack to a public gallery; anyone can read its summary and download
it, and a signed-in member can bring it straight into their own hosted graph.

- **Publishing requires a public handle**, because publishing is the only act that attaches a name
  to something. A member without one is refused with a distinct code rather than a generic error, so
  the interface can offer to set it and return
- **The pack is validated and summarized before storage**, then stored with the summary. Limits, all
  measured in `account/src/routes/community.ts` and `lib/packSummary.ts` on 2026-08-06: two
  mebibytes serialized, five thousand nodes, twenty thousand edges, ten publishes and ten imports
  per member per hour
- **The summary is what the gallery renders**: topic distribution, node and edge counts, the share
  of cross-topic edges, top tags, edge type distribution. Computing it once at publish time keeps
  listing cheap and keeps the gallery from parsing member content on every request
- **Taking something down is unlisting, not deleting.** Only the publisher can do it. Deleting the
  account removes the rows through the cascade, which is what keeps the erasure story honest
- **Moderation is post-publication**, with lightweight safety at the door: size, format and field
  length

The pack format itself is [[vinc.canon.share-pack]]; the surface that composes and consumes packs is
[[vinc.spec.surface-packs]].

### 5.1 Official packs are seeded by hand, and that has bitten once

The migration that introduced official packs **changes the schema only**. It relaxes the member
reference so an official row belongs to no account, and adds the official flag. The rows themselves
are inserted by an operator running a seeding script.

So rebuilding the database runs every migration successfully and **loses the official packs
silently**. That happened during the cloud migration: measured 2026-07-30, the table had zero rows
and the gallery was serving empty.

Two facts make reseeding survivable now, and both are recent.

- **The pack sources are in version control.** The manuscripts, the build pipeline and the four
  built packs were moved into the app repository on 2026-07-31. Before that they existed on one
  development machine only, and losing it would have left the gallery rows alive with no way to
  correct or rebuild what they contained
- **The container conversion is a script.** A built pack is a zip container (`adr-engineering-055`)
  while the gallery storage path is JSON only, since the column is text and the download declares a
  JSON type. This is not a defect but the pack format's stated contract, that **reading accepts both
  shapes and only writing produces a zip**. A tool converts and writes the manifest, warns about
  anything over the size limit, and the seeding script upserts by title so rerunning is safe

## 6. Hosting configuration for the static surface

The static configuration file carries the security headers, the redirects and the cache rules. Its
history is worth one paragraph because it explains why it exists at all.

When the system moved to managed hosting the file did not come with it, so everything fell back to
platform defaults and two things the previous reverse proxy had provided disappeared: the content
security policy and frame denial. Clickjacking defense was zero and script origin was unconstrained.
Hashed asset bundles were also being revalidated every thirty seconds instead of being cached for a
year.

Both were restored on 2026-07-30. The policy was **not newly invented**: the values that had
actually been running under the previous proxy were carried across, which is why the site's
self-contained inline scripts and styles still work under it. The only external origin the site
references is a link target, which no policy directive covers.

Two rules in that file are easy to get wrong.

- **Route matching is first come, first served.** The shared single-source assets, the navigation
  script, the documentation script and the three stylesheets, carry no content hash, so they must be
  declared as no-cache **above** the immutable rule for their directory. Reverse the order and the
  navigation bar freezes for a year
- **The API path carries no route entry at all.** Adding one risks interfering with linked-backend
  routing, and if that breaks, sign-in and the whole browser app break with it. A single no-store
  header is not worth that

Robots and sitemap files are served from the static folder directly. The name servers are configured
to resolve only, so there is no edge in front able to serve them instead, which also means any
edge-level content policy setting has nothing to apply to.

## 7. End-to-end flows

**Sign in.** The site sends a request, the member receives a link, the interstitial reads the
fragment token and waits for a click, the account consumes it atomically, creates the member on
first success, and issues the session. Details are [[vinc.spec.email-subsystem]] section 2.

**Newsletter.** Double opt-in with a confirmation mail, then a welcome mail. Unsubscribing needs no
sign-in and never reveals whether an address was subscribed.

**Account deletion.** Two steps, both irreversible at the end, and the ordering is the whole point:

1. an authenticated request mints a short-lived confirmation token and sends it
2. the confirmation consumes it atomically
3. **the hosted graph is purged first**, through a separately signed request to the hub, and the
   result decides what happens next. Not configured means skip; success means the deleted counts are
   confirmed; **failure means the account is not deleted and the caller gets an error**
4. the provider identity is purged, then a single transaction removes the account rows, cascading
   sessions, keys and community packs
5. the session cookie is cleared. Nothing local to any device is touched

Step three is fail-closed for one reason: **"deleted" must not become a lie.** If the graph cannot be
erased, the account has to remain so the deletion can be retried. The purge request names the owner
derived from the member's public identifier and is signed with a distinct assertion type, and the
identifier is captured **before** the rows disappear. The cascade on the store side is
[[vinc.spec.dataplane]] section 5.

**Web Vinc and community.** A session reaches `/app/`, the boot gate passes, graph reads and writes
proxy to the hub. Publishing goes through the consent sheet to the gallery; a gallery entry can be
downloaded as a file or imported directly into the member's own graph.

## 8. Connectors, tiers and teams

The layer that ended the single shared administrative key.

- **Connector keys.** A member mints a key whose plaintext is shown once and stored only as a keyed
  hash, lists them, and revokes them. The hub verifies a presented key by asking the account over
  the internal route
- **Connector approval.** The hub sends a signed request assertion; the account verifies it and
  shows the consent screen using **verified** values rather than anything the client claimed, which
  is what defeats a forged client name. On approval the account issues its own short, single-use
  assertion and returns the connector to the hub. Each side pins only the other's public key (B2)
- **Tier** is a column on the member row, read by the identity endpoint and by the hub's quota
  logic. The hub re-asks the account for the current tier on a timer, because a connector token
  freezes the tier at approval time and would otherwise treat a member as free forever after they
  upgraded
- **Teams** scope a graph call with a query parameter. The account resolves the team, refuses to
  reveal a team the caller is not in, refuses a team that is not active, checks the role against a
  single shared role table, and signs a **distinct assertion type** so an older hub rejects it at the
  type check instead of silently writing team data into a personal graph. The reasoning is
  [[vinc.canon.account-policy]] section 4.3, and the role check here is the second of two, the
  authoritative one being at the hub
- **Billing and teams are each gated by an environment value**, so the code can ship before the
  feature opens

## 9. Security summary

Four properties are system-level and belong here. The credential-level controls, which are cookies,
token storage, cross-site defense, enumeration parity and audit logging, are
[[vinc.spec.email-subsystem]] section 7 and are not repeated.

- **Isolation.** Physically separate repositories, no shared secret between the account and the hub,
  public-key-only trust in both directions (B1, B2, `adr-security-101`, `adr-security-102`)
- **Tenancy.** Owner scoping on every read and write path, the owner forced from a signed subject
  and never accepted from a request body, verified by a two-owner test against the real engine
- **Deletion.** Graph erasure attempted before account erasure, fail-closed, section 7
- **Depth in defense at the door.** The first-party guard is a second layer beneath the double-submit
  token, which is why section 3.1 was fixed on the caller rather than by relaxing the guard

The one asymmetry to keep in view: **the hub has no platform front door.** It is not linked to the
static web app, so its internal paths are publicly reachable and the signature check is the only
gate in front of them. The previous reverse proxy used to refuse those paths at the edge. That layer
is gone, which makes the forced-owner rule in section 4.1 load-bearing rather than defensive.
See [[vinc.spec.azure-infrastructure]] section 8 and [[vinc.ops.risk-audit-2026-08-05]].

## 10. Known gaps

| Gap | Detail |
|---|---|
| No app-isolation check in continuous integration | Section 1.2. B1 rests on repository separation and review |
| The hub's internal paths are publicly reachable | Section 9. Signature only, no network or platform layer in front |
| Official packs vanish on a database rebuild | Section 5.1. Migration and seeding are separate steps by design, and only the second is manual |
| Database credentials are not separated | Section 3.2. The managed tier does not allow additional users |
| Purge retention depends on a running process | Section 3.3. If the job stops, personal data accumulates and nothing else notices |

## Provenance

| Source | Relation |
|---|---|
| `Vinc-homepage/account`, `Vinc-homepage/site` | as-built authority for the account backend, the static site and the community server |
| `Vinc/app`, `Vinc/vinc_mcp` | the web build of the frontend and the hub |
| [[vinc.spec.email-subsystem]] | credentials, sessions, mail, and the personal-data discipline |
| [[vinc.canon.account-policy]] | tiers, the topic limit, team architecture and billing |
| [[vinc.spec.azure-infrastructure]] | where these surfaces run, and the platform behavior they inherit |
| [[vinc.ops.deployment]] | how they are deployed, and in what order |
| [[vinc.spec.architecture]] | the one-contract-two-builds arrangement this system is the hosted half of |
| [[vinc.spec.dataplane]] | the store this proxies into, ownership enforcement and the purge cascade |
| [[vinc.canon.share-pack]] | the pack format the gallery stores and serves |
| [[vinc.spec.remote-profile]] | the opt-in remote connector profile that reaches the hub directly |
