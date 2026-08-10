---
adr: adr-product-001
doc_id: vinc.canon.account-policy
layer: canon
domain: vinc/product
authority: canon
title: Account policy, tiers, and the team model
supersedes: vinc.c.account-policy
updated: 2026-08-06
inherits_from: [vinc.canon.share-pack, vinc.canon.terminology]
tags: [canon, product, account, tier, team, billing]
---

# Account policy, tiers, and the team model

> `adr-product-001`, settled 2026-08-02. Two layers: **personal**, which is free or paid, and
> **team**, which is paid only. This document is the contract. The root document of the `product`
> domain.

## 1. The decisions

| # | Item | Decision |
|---|---|---|
| a | Top-level split | **personal** (free, pro) and **team** (paid only) |
| b | Free tier call limit | **raised to the paid level**, 2000 calls per day. The old 15 per day is abolished |
| c | What free is actually limited by | **five topics.** Exceeding it blocks creating a new topic and nothing else |
| d | Personal pro | unlimited topics, monthly or annual |
| e | Team | per seat, monthly or annual. **There is no free team** |
| f | Payments | one merchant of record to begin with, a domestic processor added later if growth warrants |
| g | Team surface in v1 | **web first.** Desktop sync of a team graph comes later |

### 1.1 Why the limit is topic count and not calls

The comparison that settled this: a well-known competitor does not limit the size of a workspace
one person uses alone. Its block limit applies only once a second member joins. The billing trigger
sits on collaboration, file weight, and history retention, and the solo user stays free
indefinitely. The free tier is an acquisition channel, not a trial.

Vinc takes the same shape, for a specific reason. **Squeezing the call limit stops the tool working
as a tool**, which removes the user's chance to judge the product at all. Topic count is the axis
instead:

- Someone using this for one thing, one job or one piece of research, stays free forever inside five
- The moment they start gathering several strands into one graph is the moment the product has shown
  its value, and the reason to upgrade arises on its own
- A topic is a **free string**, so the user decides what it means. This is not a forced taxonomy

### 1.2 Tiers

| | Personal free | Personal pro | Team |
|---|---|---|---|
| Tool calls | 2000 per day | 2000 per day | 2000 per day per member |
| Topics | **five** | unlimited | unlimited |
| Hosted graphs | one personal | one personal | one personal plus **one shared team graph** |
| Web, desktop, sync | yes | yes | yes, team graph on web first |
| Invites and roles | no | no | **yes**: viewer, editor, admin |

The call limit being identical across all three is deliberate. The limit **is an environment value,
not a code constant**, so it can be adjusted in operation.

### 1.3 Out of scope, stated so it is not assumed

- **No graph engine schema change.** A team exists because of one owner string
- **No per-topic or per-node permissions.** v1 permissions are **per operation**. A topic-level
  access list would require planting a predicate in every query plan, which is separate work
- **No authorship record.** A node in a team graph does not record who wrote it
- **No desktop sync of a team graph** in v1
- **No card data retained.** Payment methods live entirely with the processor

## 2. Entitlement

There is one source of truth for a person's entitlement, and every surface projects from it rather
than deciding for itself. Personal entitlement reaches the enforcement points by three routes, and
all three must agree, because a discrepancy between them is invisible to the user and shows up as
"it works on the web but not from my assistant."

Limits are consumed at named points. The web path does not decrement the call counter, which is why
launching teams on web first requires **no limit-related change at all**.

## 3. The topic limit

### 3.1 What a topic is

A topic is the free-string domain field. The user decides its meaning. That is what makes the limit
tolerable: it does not force a classification, it just counts distinct strings.

### 3.2 The enforcement rule

Exceeding the limit blocks **only the creation of a new topic**. Nothing already in the graph stops
working, nothing is hidden, nothing is deleted. A user at the limit keeps full use of the five they
have, forever.

This matters more than it sounds. A limit that degrades existing work turns a free tier into a
hostage situation, and the acquisition-channel logic of section 1.1 collapses with it.

### 3.3 It must not be bypassable

Enforcement lives at the point where a topic is actually created, not on the screen that offers the
button. A limit enforced only in the interface is not a limit.

## 4. Team architecture

### 4.1 A team is one owner string

The graph engine has no concept of a team. It has an owner column holding an opaque string. A team
graph is that string with a different prefix. **This is why the whole feature needs no schema
change**, and it is a dividend of the earlier decision to freeze field names and redefine only their
product meaning. See [[vinc.canon.terminology]].

### 4.2 The account service decides the graph owner

The client never names the graph it wants. The account service derives the owner from the
authenticated subject and the requested team, and signs that into the assertion. A client that could
name its own graph owner would be a cross-tenant hole.

### 4.3 The team assertion must be a new type, not a new field

**This is the critical point of the whole design.**

The hub rejects any assertion whose type is not the proxy type, and for that type it **unconditionally
forces the owner to the personal one derived from the subject**.

Add a team field to that existing type, and consider an old hub running against a new account
service. The hub does not know the field. It sees a valid proxy assertion. It forces the personal
owner. **Team writes land silently in the member's personal graph.** Team documents accumulate where
nobody looks for them, and the team screen shows an empty result.

There is no error anywhere in that sequence. It is **silent misbehavior, which is the worst
possible failure mode**, and it would be discovered days later as data loss.

So teams use **new assertion types**:

| Type | Use | How an old hub responds |
|---|---|---|
| the personal proxy type | personal graph, unchanged byte for byte | normal |
| a team proxy type | team graph reads and writes | **rejected at the type check** |
| a team purge type | graph erasure when a team is deleted | **rejected at the type check** |

With this, no backward-compatibility shim is needed, and getting the deployment order wrong is
**demoted from a correctness problem to "the team feature is not open yet."**

The team assertion carries the member as subject, the graph owner, the role, the operation name,
the tier, and issue and expiry times. The hub validates the owner's **shape with a regular
expression** and decides the role against the table below.

### 4.4 Permissions are per operation, with three roles

Fine-grained permissions would require rewriting every query plan, so v1 divides the operations that
**already have names**.

| Role | May do |
|---|---|
| **viewer** | every read operation, plus job status |
| **editor** | viewer, plus writes: pin, tag, touch, activate, ingest, pack import, document write, extract, re-embed, evidence backfill, conventions build |
| **admin** | editor, plus destructive operations: delete node, delete edge, merge nodes, delete document, rename topic |

- Enforcement happens **at the hub**, which is the point that cannot be bypassed, and **again at the
  account proxy** as defense in depth
- Team graph erasure is not a graph operation. It belongs only to the team deletion procedure
- **A drift test lives on both sides**: the union of operation names in the role table must equal the
  full proxy allowlist. Adding an operation without assigning it a role breaks the build.
  This guard exists because a three-way list drift once killed one view on the web only, and
  nothing reported it

### 4.5 Limits and teams

Because team v1 is web first and the web path does not decrement, **launching teams changes nothing
about limits.** Only the key design is fixed in advance:

- Personal keeps its existing key, so **no counter row ever needs migrating**
- Team, later, uses a composite of team and member, which prevents one member burning the whole
  team's allowance and prevents team work eating a personal allowance

### 4.6 Blast radius of deletion

- **A member deleting their own account cannot harm the team graph.** The erasure path can only
  derive a personal owner from a subject, so a team owner is **structurally unreachable** from it.
  This property is pinned by a test rather than left to inspection
- **A team owner's account deletion is refused** with a conflict. They must delete the team or
  transfer ownership first
- **Team deletion is ordered and halts on failure**: cancel the subscription, confirm the graph is
  erased, then delete the team, membership, and invitation records
- **There is no authorship record.** Because the owner is the team, a node does not say who wrote it

### 4.7 Why desktop is not in v1

Desktop sync assumes one local graph maps to one account graph. A team introduces a second remote
graph for the same person, and the sync cursor, the tombstone namespace, and the conflict rules all
assume a single pairing. Opening that on desktop without redoing those three is how you get a
personal graph and a team graph overwriting each other. Web first is not a scheduling convenience:
it is the boundary of what the current sync design can express.

## 5. Account model and billing

The account service holds subscriptions, webhook events, and charge attempts. **Those three names
are chosen**, because the obvious alternatives collide with the forbidden metering identifiers
recorded in [[vinc.canon.terminology]].

- **The processor holds all payment data.** Vinc never sees or stores a card
- **Subscription state is a state machine**, not a boolean, because a lapsed payment, a grace
  period, and a cancellation that has not yet reached its period end are three different states with
  three different entitlements
- **Webhooks are the truth of billing state**, not the response to the checkout call. A checkout that
  the user abandons at the processor's page never produces a webhook, and treating the redirect as
  success grants entitlement to someone who did not pay
- **Account deletion is ordered**: cancel with the processor first, then erase the graph, then delete
  the account records. Reversing that leaves a paying subscription attached to nothing

### 5.1 One merchant of record first

Starting with a single merchant-of-record processor means tax and invoicing obligations across
jurisdictions sit with the processor rather than with us. A domestic processor is added later if
growth justifies the second integration, and the abstraction exists from the start so that adding
one does not touch entitlement logic.

## 6. State

The code for the earlier rollout phases and the team work has landed in the account service, and
migrations are in place. What is worth recording here is not the phase number but the switch
design: **each phase is gated by the presence of an environment value**, so the user-visible change
is zero until that value is set. That is what makes the rollout reversible without a deployment.

> The original of this document was written before implementation and described everything in the
> future tense. The contract above is unchanged by implementation; only the tense was wrong.

## 7. Deliberately unresolved

- Per-topic access control, which needs the query plan work described in section 1.3
- Authorship in team graphs, which needs a column and therefore a schema change, and which section
  1.3 excluded on purpose
- Desktop team sync, blocked on the sync design described in section 4.7
