---
doc_id: vinc.spec.email-subsystem
layer: spec
domain: vinc/product
authority: spec
title: Account credentials and the email subsystem
supersedes: vinc.emails.architecture
updated: 2026-08-06
inherits_from: [vinc.canon.account-policy]
tags: [spec, product, account, email, auth, session, privacy]
---

# Account credentials and the email subsystem

> How a person proves who they are without a password, and everything the product sends to a mailbox.
> Tokens, cookies, sessions, mail delivery, mail identity, and the personal-data discipline that
> governs all of it.
>
> The surfaces these mechanisms serve are [[vinc.spec.web-system]], which also states the separation
> invariants referenced below. Tier and billing meaning is [[vinc.canon.account-policy]]. The words
> allowed in user-visible text are [[vinc.canon.terminology]].
>
> **The document this replaces was written before implementation** and described a single virtual
> machine behind a reverse proxy, a dedicated database schema, two least-privilege database users
> and a private network. None of that is what runs. Section 8 lists what was specified and not
> adopted, and why, rather than quietly deleting it. Everything else below was checked against
> `Vinc-homepage/account` on 2026-08-06.

## 1. The rule this subsystem must never break

**Nothing here may weaken the offline promise of the native app.** The app has no account, no login,
no telemetry, and its local data stays on the device. Any change that couples the native binary to
this subsystem is a security regression, not a feature.

That promise has a corollary about language, and it is the reason a claim matrix exists at all: the
optional web account makes certain absolute sentences literally false, and a privacy-branded product
that ships a false absolute has a trust problem rather than a wording problem.

| Where | What may be claimed |
|---|---|
| the native app | no account, no login, no telemetry, your notes and local data never leave your device |
| the web | an optional account for the newsletter and the paid plan. The only thing stored is the address you typed |
| never, anywhere | that the product never connects to the internet, or that it is fully air-gapped, or any phrasing that erases the optional website |

The forbidden-vocabulary gate in the app repository enforces the identifier half of this
(see [[vinc.canon.terminology]]). The marketing half is currently held by review.

> **Known gap.** The original specification also promised a banned-phrase lint over marketing and
> readme copy, and an isolation grep over the desktop sources. Measured 2026-08-06, neither exists
> in either pipeline.

## 2. Credentials

### 2.1 The token

- **Entropy.** Thirty-two random bytes from a cryptographic source, encoded for a URL. Never an
  identifier, a timestamp or a counter
- **At rest.** Only a keyed hash of the token is stored, with the key held in the service secrets and
  never in the database. The raw token exists in exactly two places: the email and the reader's
  browser. A database disclosure therefore yields no usable link
- **Lifetime.** Fifteen minutes for sign-in and for delete confirmation, twenty-four hours for
  newsletter confirmation, seven days for a team invitation. All single-use
- **One active sign-in token per address.** Requesting a new link invalidates every unused prior
  sign-in token for that address, so a link sitting in an inbox stops working once a newer one is
  asked for. Delete and invitation tokens are untouched by that sweep, since they answer a different
  question
- **Consumption is a single atomic conditional update** that sets the used timestamp only where it is
  still null and the expiry is still in the future, and the flow proceeds only when exactly one row
  changed. That one statement is the whole replay and double-submit guard, and it works because the
  database decides the race rather than the application
- **Comparisons are constant-time**, and lookup is by indexed equality on the hash rather than by
  string comparison of anything secret

### 2.2 The verify flow, built against a hostile mailbox

1. The link carries the token in the **URL fragment**, so it is never transmitted to the server,
   never written to a log and never leaks through a referrer
2. Loading the page changes nothing. It is a static interstitial, which is what lets it survive a
   scanner prefetch. Client script reads the fragment, strips it from the address bar, and waits
3. Only an explicit click posts the token, and only then does the atomic consume run
4. On first successful verification the member row is created. It is never created on request, which
   avoids both orphan rows and an existence leak

### 2.3 Flow binding, and why it is not a hard failure

On request, a second random value is set as a short-lived host-prefixed cookie and its hash is stored
beside the token. On verification, a match means the same browser and the flow proceeds automatically.

A missing or mismatched value is **not** treated as an attack. It is equally the signature of someone
who asked on a phone and opened on a laptop. So instead of refusing, the response carries a
confirm-context payload showing when the request was made and the approximate source address, and the
page asks the reader to continue only if they started it. A repeat call carrying an explicit
confirmation then proceeds.

This preserves legitimate cross-device use while removing the silent case: a forwarded link can no
longer log someone in without a human reading a sentence that says the request came from somewhere
else. **The token is not consumed until the flow is committed**, so reaching the confirm screen and
walking away leaves the link usable.

### 2.4 Purpose is stored but not checked on sign-in

The token table carries a purpose, one of sign-in, delete confirmation or team invitation. Delete
confirmation and invitation acceptance each consume with the purpose in the condition, so neither can
redeem a token minted for something else.

**The sign-in path does not.** Its read and its consume both match on the hash alone. Measured
2026-08-06 in `account/src/repos/authTokens.ts`. The consequence is that a token minted for another
purpose, including a seven-day invitation sent to a third-party address, can be presented to the
sign-in endpoint and will produce a session for the address the token was keyed to.

Flow binding does not stop it. Those tokens store a meaningless challenge value, so the request falls
through to the confirm-context screen of section 2.3, and that screen is a warning to a human rather
than a check: whoever holds the token supplies the confirmation and proceeds.

This is an open finding, not a design. [[vinc.ops.risk-audit-2026-08-05]] is the record.

### 2.5 Sessions and cross-site protection

- **Opaque and server-side, never a stateless token.** The cookie carries a random identifier; the
  database stores only its keyed hash with the member and an expiry. This is what makes sign-out and
  account deletion take effect immediately, which a self-contained token cannot do
- **Three cookies, all host-prefixed**, meaning secure, root path, and no domain attribute, so none
  of them can be set or read by a sibling host: the session, the short-lived login-flow value, and
  the readable cross-site token
- **Lifetime is two limits, not one.** Thirty days of inactivity and ninety days absolute, both
  enforced in the query that loads the session rather than only in the cookie, because a cookie
  lifetime is a client-side suggestion
- **The session identifier is new on every sign-in**, which closes session fixation
- **Every authenticated state-changing request** echoes the readable token in a header, on top of the
  lax same-site cookie and the origin allowlist. Three independent layers, because each has a known
  bypass and they do not share one

### 2.6 Enumeration parity

The sign-in request, the newsletter subscription and the unsubscribe endpoints all return the same
generic success regardless of whether the address exists, is rate limited or is suppressed. Sends are
asynchronous, so timing does not branch on existence either, and per-address rate limits **drop
silently** rather than returning a distinct error, since a limit that only fires for real addresses
is itself an oracle. Public forms carry a hidden honeypot field.

Creating the member only on first verification makes this natural rather than something the code has
to remember: at request time there is nothing to look up.

## 3. Mail delivery

One provider, transactional stream. The delivery function is small and its guarantees are all
negative, which is the point.

- **It never throws.** A failed send returns a reason and the caller still answers success, because a
  visible send error would be an enumeration oracle. Total failure is logged
- **Suppression is checked before every send.** The suppression table is fed by provider webhooks:
  a hard bounce suppresses, a complaint suppresses and marks the subscriber
- **Every send carries an idempotency key** derived from the token or the address, so a retry never
  produces a second email. One flow means at most one token, which means at most one message
- **Three attempts with exponential backoff**, and client errors other than rate limiting are not
  retried, since retrying them cannot help

### 3.1 The content rule, which is a live contract

**No customer personal data appears in the body of a transactional email.** Not the address, not a
name, not anything derived from a device or a local store. The only per-send secret in a token email
is the opaque token inside the action link.

This is enforced in two places rather than one, and the redundancy is deliberate.

- **The templates no longer render the address at all.** The footer line that used to read "sent to
  your-address" was removed outright. The recipient address appears only in the real envelope
- **The delivery function masks it anyway.** Any address passed as a template value is reduced to one
  or two leading characters plus a fixed-length filler before rendering, so if a future template
  reintroduces the tag, the raw address still cannot reach the body. A guardrail that survives the
  removal of the thing it was guarding is the only kind worth keeping
- **The welcome mail no longer greets by name**, because sign-in is address-only and no display name
  is ever collected. The greeting had been the local part of the address, which is to say the address
- **The team invitation carries the team name and the role, and not the inviter.** Its trust anchor is
  the team, not a person's address. This is the one message that goes to a third party, so it is the
  one where leaking the sender's address would leak something the recipient was never given

### 3.2 Templates

Static HTML files, one per type per language, in the account repository. Fully self-contained: inline
styles only, no external image, script or font, and no tracking pixel. The subject line lives in the
first HTML comment and is extracted **after** merge-tag substitution, which is what allows a version
number in a subject. Extracting before substitution once shipped a raw merge tag in a subject line,
and it went unnoticed only because no subject needed a value until then.

Punctuation follows the surface rule: **no em dash anywhere in user-visible text**. The template
readme attributes that rule to a design decision; the register describes that decision as something
else, and the rule's owner is [[vinc.canon.terminology]], which records it as an owner instruction
dated 2026-07-22.

A test asserts that every action link in the mailer points at a page that exists. It was added after
an invitation mail went out pointing at a path that had never been built, and every recipient who
clicked got a not-found page. Nothing else would have caught it, because the mail sent successfully
and the failure was entirely on the far side of the click.

### 3.3 The one bulk send

Every message except the release announcement is transactional: one person acted, one message went
out. The release announcement goes to every confirmed subscriber, and that changes three things.

- **One-click unsubscribe headers are mandatory**, both the list header and the one-click post
  declaration. Without them recipients press the spam button instead of unsubscribing, and that
  damages the sending domain's reputation for **all** mail, including sign-in links. The header
  target is the post-only API route; the in-body link is the friendlier interstitial, because the two
  have different audiences and therefore are not the same URL
- **The idempotency key includes the version**, so triggering the same release automatically and
  manually sends once. The provider's deduplication window is about a day, so re-triggering the same
  version later **will** send again
- **Sending is paced** at roughly six hundred milliseconds apart and runs after the response is
  returned, so a restart mid-send leaves it partially delivered. Rerunning the same version inside
  the deduplication window recovers it safely

The trigger route is authenticated by a shared header secret rather than a session, because the
caller is a continuous-delivery job with neither an origin nor a cookie. **If the secret is not
configured the route answers exactly like a path that does not exist**, since a challenge would
confirm the endpoint is there, and the workflow that calls it is public. A setup script writes the
value to both places that must agree and finishes with a dry run, precisely because a half-applied
configuration is disguised as a missing route and would otherwise stay unnoticed until a release went
out silently.

## 4. Mail identity, and the two domains

Measured 2026-07-30 and unchanged since.

**The sending domain and the link domain are different, on purpose.** Mail goes out from the historic
domain because the provider's signing and policy records live in that zone, and moving them means
re-verifying the domain, during which sending stops. Links in every body point at the canonical
domain, which is the only one that resolves to a site.

Four records remain in the historic zone, and there is no address record. The list matters because it
is short and load-bearing:

| Record | What it does |
|---|---|
| a signing key, as a text record on a provider-named subdomain | proves the message was not forged |
| a policy record and a feedback exchanger on a sending subdomain | authorizes the provider and carries bounce and complaint feedback back |
| a policy record on the parent, set to reject | stops anyone spoofing sign-in mail from this domain |

The feedback path is what feeds the webhook that feeds the suppression table, so it is not
decoration.

> **Deleting these four kills sign-in mail.** They are easy to sweep up while cleaning out a zone
> that otherwise looks abandoned, which is exactly what the zone looks like once its address record
> is removed. That removal already happened once, deliberately and carefully, on 2026-07-29.

### 4.1 The policy-record merge trap

The canonical domain has its own sender policy record, added automatically when inbound routing was
configured, and **it does not include the mail provider**. Today that is harmless, because sending
happens from the other domain which has its own record.

The moment the sending address moves to the canonical domain, that record kills delivery. The fix is
to merge both authorizations into **one** record. Adding a second policy record is exactly the wrong
move and is the intuitive one: the standard permits only one per name, and two produce a permanent
error that fails everything rather than half of it.

### 4.2 Receiving

The contact address is on the canonical domain. It previously pointed at the historic one, where
**neither zone had any mail exchanger at all**, so the address published in the privacy policy as the
place to send questions and data requests did not receive anything.

Inbound is now handled by a routing service on the canonical domain, which adds its own exchangers,
signing key and policy record automatically. Those must not be entered by hand: priorities and key
values get mistyped. They coexist with the existing records because text records may repeat under one
name and there was no exchanger on the parent to conflict with.

Two operational facts about it:

- **Records alone do not deliver.** A destination mailbox must be added and then confirmed from that
  mailbox before a rule can even select it. The destination is stored only inside the routing service
  and never published, so a personal mailbox can be used without exposure, and changing who handles
  contact means changing the destination and nothing on the site
- **Verification is done without sending mail**, by asking the exchanger about a recipient and
  disconnecting before any message body. **Both a real and a nonexistent address must be tried.**
  Checking only the real one reads a catch-all configuration, which accepts everything and becomes a
  spam magnet, as a healthy result

## 5. Data lifecycle and privacy

- **Minimization.** An address, and optionally a name that is never collected today. No password,
  ever. A request address is optional, is stored in binary form, and lives and dies with the token or
  session row it belongs to. There is no separate long-lived audit copy and no user agent is recorded
- **Retention** is enforced by the hourly purge described in [[vinc.spec.web-system]] section 3.3
- **Deletion** is two-step and confirmed by email, and its ordering, including the fail-closed graph
  erasure that must succeed before any account row is removed, is [[vinc.spec.web-system]] section 7.
  There is no soft delete and no personal-data tombstone
- **Newsletter consent and account consent are separate.** Deleting the account removes the
  subscriber row only when newsletter removal was also asked for, and unsubscribing never requires
  signing in and never reveals whether the address was subscribed

### 5.1 The subscriber row is the only source of newsletter truth

Send eligibility is one confirmed status on one row keyed by address. The account screen's toggle
reads and writes **that** row. There is deliberately no boolean on the member record, because two
places holding the same consent is how a person unsubscribes and keeps receiving mail.

One asymmetry follows from magic-link sign-in: an authenticated member has already proven ownership
of the address, so turning the toggle on confirms immediately rather than sending a second opt-in.
The double opt-in exists to prove ownership, and ownership is already proven.

The unsubscribe token is the single secret in this system **stored in usable form**. It has to be:
it is perpetual, it must work with no session, and the one-click standard requires it in a query
string. It is low sensitivity by construction, since the only thing it can do is stop mail.

## 6. Copy

The English and Korean decks for these flows are the templates themselves and the site pages, and
this document does not mirror them. That decision follows the same reasoning as
[[vinc.spec.ui-copy]] section 1: a mirrored inventory decays silently, and a reader trusts it while
it decays. What is stable and worth stating is the rule set.

- **Check-inbox copy never confirms that an address exists.** It says a link was sent if the address
  is valid, matching the API's uniform response. Copy that says "we sent you a link" undoes section
  2.6 in one sentence
- **The delete dialog reassures about local data explicitly**, because the single most likely
  misreading of "delete your account" is that it reaches the device
- **Every screen that could be read as "the app has an account" carries the boundary note**
- Expiry is stated in the message that carries the link, along with what triggered it, so a recipient
  who did not request it can tell that immediately

## 7. Security controls, in one list

- Opaque server-side sessions, keyed-hash storage, rotation on sign-in, immediate revocation
- Lax same-site plus origin allowlist plus double-submit token on every authenticated state change
- Layered per-address and per-source rate limits, silent for the per-address case, honeypot fields
- Uniform responses, asynchronous sends, no existence branch
- Phishing resistance: short single-use links, same-browser binding, a confirm screen for everything
  else, and a reject policy on the sending domain
- **Open redirect is not possible**, because the post-sign-in destination is a fixed path rather than
  a parameter. If a return path is ever needed it must be a relative path matching a strict pattern
  against a small allowlist
- Secrets are injected as environment values, validated at boot, and the process refuses to start if
  one is missing or malformed. Nothing secret is passed as a process argument or committed
- Audit lines carry hashed identifiers and outcomes only

## 8. What was specified and not adopted

The superseded document is the origin of most of the rules above, and it is honest to record where
reality diverged rather than to rewrite history.

| Specified | What happened |
|---|---|
| a dedicated database schema and two least-privilege users, one for data and one for migrations | not adopted. The managed database tier does not permit creating users. The application runs as the schema owner, recorded as a gap in [[vinc.spec.azure-infrastructure]] |
| the database on a private network with no public endpoint | not adopted. The firewall admits traffic originating inside the cloud, because the container platform's outbound addresses are dynamic and cannot be listed |
| a reverse proxy terminating TLS, secrets in a file owned by a dedicated system user | replaced. The platform terminates TLS and secrets are container secrets. The same-origin property the proxy provided is now provided by the linked backend |
| a scheduled-event purge in the database as an optional accelerator | not adopted. The managed tier withholds the privilege, so the always-on job is the only mechanism |
| a metadata-only shared-packs table with content in object storage | superseded. Public sharing shipped as the community gallery, which stores the pack itself. See [[vinc.spec.web-system]] section 5 |
| a global daily outbound ceiling that fails closed | **never implemented.** Measured 2026-08-06: no such limit exists anywhere in the account sources. The per-address and per-source limits are the only send-side protection |
| the paid plan as a future schema | shipped. Subscriptions, processor webhook events and teams are all live tables. The policy is [[vinc.canon.account-policy]] |

## 9. Known risks

1. **Same-origin coupling.** Serving the API on the site's own origin is what makes the host-prefixed
   session cookie and lax same-site protection work, and it also means any script injection on a
   marketing page shares an origin with that cookie. The strict content policy, zero third-party
   assets and the HTTP-only flag are load-bearing mitigations rather than hardening extras
2. **Cross-device redemption rests on a human reading a sentence.** When approximate location is
   unavailable, or the reader clicks through without reading, a socially engineered forward can still
   complete. The short lifetime and the explicit wording are the compensating controls
3. **Purpose is not checked on sign-in**, section 2.4
4. **No send ceiling**, section 8. A mail flood is bounded only per address and per source
5. **Purge is a process, not a schedule.** If the always-on job stops, single-use token rows and the
   personal data on them accumulate with nothing reporting it
6. **Isolation between this subsystem and the graph plane is operational**, not enforced by code:
   separate repositories, separate credentials, no shared secret. A single deployment
   misconfiguration could recouple them, which is why deployment-time verification of credentials and
   scope belongs in [[vinc.ops.deployment]]

## Provenance

| Source | Relation |
|---|---|
| `Vinc-homepage/account/src` and `account/emails` | as-built authority for credentials, delivery and templates |
| [[vinc.spec.web-system]] | the surfaces, the separation invariants, purge, deletion ordering |
| [[vinc.canon.account-policy]] | tiers, teams and billing, which this subsystem authenticates for |
| [[vinc.canon.terminology]] | the vocabulary gate and the forbidden identifier list |
| [[vinc.spec.azure-infrastructure]] | where this runs, and the platform behavior it inherits |
| [[vinc.ops.risk-audit-2026-08-05]] | the record for the open findings in sections 2.4 and 9 |
