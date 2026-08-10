---
doc_id: vinc.release.0-7-2
layer: release
domain: vinc/product
authority: record
title: v0.7.2, three planned streams and a fourth that arrived
supersedes: vinc.ops.release-0-7-2
updated: 2026-08-07
inherits_from: [vinc.ops.deployment, vinc.canon.account-policy]
tags: [release, product, record, 0-7-2, account-policy, search, handoff]
---

# v0.7.2, three planned streams and a fourth that arrived

> **History.** This was a handoff document written the day before the release, describing what would
> be bundled and in what order it had to be deployed. It shipped on **2026-08-03**, and it carried
> one substantial stream the plan never mentioned.
>
> In the old corpus this was the only document whose declared authority claimed to be a source of
> truth **for a plan**, which is a category error the new scheme removes: a plan is a record the
> moment it is executed, and this one is.

## 1. Why three streams were bundled, and the risk of doing it

Three separate lines of work were ready at roughly the same time and were put into one release
deliberately. The consequence was stated plainly at the time: **if one is late, the whole release
waits.** That is the cost of bundling, and it is worth paying only when the parts share a deployment
window, which these did.

| Stream | Contents | State when the plan was written |
|---|---|---|
| A | Account policy: personal and team tiers, a topic ceiling, billing, team collaboration, a trial period, and the screens for all of it | code complete, committed, **not deployed** |
| B | Retrieval and conventions work: search pagination, a migration badge, mutability tags, an ingest warning | code complete, committed |
| C | Anything already waiting from before | **empty, by measurement** |

Stream C is a small model of how the plan worked. Rather than assuming a backlog existed, the commit
range since the previous tag was read: every commit in it belonged to streams A and B, so C was
declared the empty set instead of being left as an open question.

## 2. Stream A, and the ordering that mattered more than the code

The account policy work was the largest piece and the one with real deployment risk, because
**environment configuration had to precede the code**, not follow it. The container update command
does not carry environment or secrets, so deploying the code first produces a build in which the
policy's only differentiating feature is switched off and nobody can tell.

The order was database, then hub, then account service, then the switch. Getting it wrong was made
non-fatal by design rather than by care: team capability is gated by a **separate assertion type**,
so an out-of-order deployment yields "teams are not open yet" rather than misrouted writes. That
separation is the load-bearing safety property and it is described in [[vinc.canon.account-policy]].

The same document holds the other defensive default worth repeating here: the topic ceiling defaults
to **unlimited**, and the local path sends no entitlement at all. So the sidecar changes ship inside
every desktop build and lie **harmlessly dormant** there. The default inverts the risk from
"forgetting to exempt local users" into "forgetting to enforce the limit", and only one of those
failures is safe.

External lead times were the actual critical path, and none of them were engineering: payment
provider onboarding and product review, tax advice on cross-border sales, whether payouts settle in
local currency, and the fact that the support address had no mail routing at all.

## 3. Stream B, in one table

| Item | What it did |
|---|---|
| Cursor pagination for search (`adr-governance-014`) | A large result set had been **refused whole** rather than truncated. Now one page comes back with a continuation marker, an invalid or expired marker is an explicit error rather than a silent return to page one, and an oversized response envelope is trimmed instead of rejected |
| Summary and detail split | Responses carry excerpts by default and full text on request. This was the root cause of the oversized responses above, not a separate improvement |
| Migration badge (`adr-governance-013`) | While re-embedding is pending, the search screen says so and shows how many documents remain, then the badge disappears on its own |
| Mutability tags (`adr-governance-011`) | The conventions file's two tags described **origin**, and readers inferred mutability backwards from them. Three new tags answer the actual question, and files carrying the old tags still validate |
| Ingest warning | Declaring an edge to an already-stored node without including that node caused the edge to be dropped **silently**. The warning now names what to send and how. The structural fix was filed as a proposal instead, and became `adr-governance-015` |

Four items were deliberately left out, and the reason for one of them is the interesting one: the
second half of the tag work would have made a graph carry a single topic, which **structurally
changes the denominator** of the domain fragmentation metric that a scheduled review was about to
use. Shipping it would have made the review unreadable.

## 4. What actually shipped

Released **2026-08-03**. Everything in streams A and B shipped as planned. And the release carried a
**fourth stream the plan does not mention**: the Atlas visibility work, `adr-design-075` and
`adr-design-076`.

That work is substantial and it is what the release notes lead with. Zooming out had been turning
the map black, because the fog range was a constant tied to scene size while the default camera
already sat at its edge. Hovering a node made everything else vanish, because the three-dimensional
path had a recede value hardcoded at nearly four times the specified one. Most marks in the dark
theme were **indistinguishable from the background**, with the darkest tier measured at a contrast
ratio of 1.15 to 1, and edges borrowed a border color that put lines underneath the surface they
were drawn on. All three were measurable defects rather than matters of taste, and the release added
a contrast gate to continuous integration that recomputes against **two different contrast models**,
because each is generous where the other is strict.

⇒ **The honest reading of this document is that a release bundle is an estimate.** Written one day
ahead by someone with full visibility, it was missing its headline feature. The lesson is not to
plan harder; it is that the changelog, written after the fact, is the record, and a pre-release
bundle description is a **planning artifact** that should never be cited for what shipped.

## 5. The handoff items, and what became of them

| Item | Outcome |
|---|---|
| Confirm where a version number is stamped, since two sources disagreed on six versus seven places | **Already answered in the repository and nobody looked.** An executable version script had existed since 2026-07-29 and covers **ten sites across six files**. The disagreement was between two prose checklists while a runnable authority sat beside them. See [[vinc.release.changelog]] |
| Collect the proposal-backlog metric from the hub, which cannot be read through the account proxy | Not collected. The review it fed **remains pending**, per [[vinc.canon.graph-conventions]] |
| Move the scope record into a dated episode once the release actually went out | Not done in the form specified. The graph carries an episode for the **planning** of this release, and its date column is empty, because the ingest tool schema does not declare the field even though the store persists it. That defect is recorded in [[vinc.canon.mcp-contract]] |
| Reconcile the account policy ledger with reality | The policy document's own ledger and the landed-work episodes disagreed at the time |

### 5.1 The baseline that cannot be compared

The metric snapshot taken for the scheduled review recorded **zero domain fragmentation**, measured
across the eight domains the graph then had.

Those eight domains no longer exist. The corpus redesign replaced them with seven differently drawn
ones, so the comparison the snapshot was taken for **cannot be performed as designed**. The measure
has to be re-baselined against the new vocabulary before it means anything, which is the same
conclusion reached independently in [[vinc.plan.graph-conventions]] section 5.

## 6. What was kept out of this release on purpose

- **The plan badge on the desktop.** The account endpoint already returned the plan and the settings
  screen simply did not display it
- **The domestic payment route**, behind business registration and payment-provider review
- **The team clauses in the privacy policy and terms.** Recorded not as a release item but as a
  **precondition for opening teams at all**, because members' content becomes visible to the whole
  team, contributions remain after a member is removed, and public handles are searchable by team
  administrators. Disclosing any of that afterwards is a dispute
- **A trial-expiry notice.** Without it, a trial user is simply downgraded on the day with no
  message, which is acceptable only while no real user is on a trial

## Provenance

| Source | Relation |
|---|---|
| `Vinc/CHANGELOG.md` | What actually shipped, and the authority over this document wherever they differ |
| [[vinc.release.changelog]] | Where the release record lives and how a release is cut |
| [[vinc.canon.account-policy]] | Stream A: the policy, the tiers, and the dormant-by-default design |
| [[vinc.canon.graph-conventions]] | Stream B: the conventions contract and its pending review |
| [[vinc.spec.surface-search]] | The search surface that pagination and the badge changed |
| [[vinc.research.atlas-visibility-verification]] | The measurement behind the unplanned fourth stream |
| [[vinc.ops.deployment]] | Why the desktop ships on a tag while the services ship on a push |
