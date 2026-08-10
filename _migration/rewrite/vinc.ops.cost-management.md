---
doc_id: vinc.ops.cost-management
layer: ops
domain: vinc/infra
authority: spec
title: What the cloud bill can do, and what stops it
supersedes: vinc.ops.azure-cost
updated: 2026-08-07
inherits_from: [vinc.spec.azure-infrastructure]
tags: [ops, infra, cost, budget, quota, storage, retention]
---

# What the cloud bill can do, and what stops it

> The guards that are actually configured, and the ways this particular architecture can produce a
> bill nobody expected. The topology is [[vinc.spec.azure-infrastructure]] and the procedures are
> [[vinc.ops.deployment]].
>
> **This document is not the billed amount.** Read the actual charges in the portal's cost analysis.
> The command line consumption listing is **not supported on this subscription's offer type**, which
> was confirmed by trying it, so there is no scripted path to the numbers.
>
> Written on 2026-07-29 during the migration and revised on 2026-08-07. Several risks discovered
> later, in [[vinc.ops.risk-audit-2026-08-05]], are cost risks and are summarized in section 6.

## 1. What is already in place

| Guard | Setting | Effect |
|---|---|---|
| **monthly budget** | 150 a month, with alerts at half, three quarters and ninety percent of actual spend, plus a forecast alert at full, all to the operator's address | **it does not turn anything off**, it only notifies |
| **daily log ingestion cap** | one gigabyte a day, where the workspace previously had **no limit at all** | collection stops for the rest of that day, the service itself is unaffected |
| **container scale ceiling** | both container apps fixed at one replica with no autoscale rule | a traffic spike cannot add replicas, which is safe for the bill but shows up as latency or refusal instead |
| **role scope** | the deployment identity is a contributor **on this resource group only** | a delivery workflow cannot touch another resource group or subscription by accident |

Adjusting the budget's recipients or thresholds does not require recreating it. Patch the
notifications or the amount on the existing budget resource.

## 2. Container apps

Consumption billing is metered per virtual core second and gibibyte second, plus request count. Both
applications are fixed at exactly one replica, so **there is no autoscale at all**, and that is the
first line of defence.

| Scenario | Condition | Current exposure |
|---|---|---|
| autoscale runaway to dozens of replicas | someone raises the replica ceiling later without setting a concurrency threshold | **low** while the ceiling is one. **Re-read this table every time that ceiling is raised** |
| a flood of unwanted traffic inflating core seconds | externally reachable with no rate limiting | **medium**. Both applications have external ingress, and neither has application-level rate limiting. The account backend's origin check is an origin check, not a request budget |
| always-on idle charge | one replica held permanently, so compute bills around the clock even at zero traffic | **occurring by design.** The hub cannot scale down because of its single writer lock |
| image pull bandwidth on deployment | every delivery transfers an image from the registry | **low**, since both are in the same region and deployments are infrequent |

**Recommendation:** before raising the replica ceiling in response to real traffic, state a concurrency
threshold on the scale rule explicitly, and consider a usage-based alert on core consumption separate
from the spend-based budget.

## 3. The managed database

**The item that most deserves attention is automatic storage growth, which is enabled.**

| Scenario | Mechanism | Response |
|---|---|---|
| **storage grows and cannot shrink** | when free space falls below a threshold the service enlarges the storage automatically. **Enlarged storage can never be reduced** without recreating the server. So a temporary flood, a bad migration or a runaway log table, can take twenty gigabytes to hundreds, and **deleting the data does not lower the bill**, which stays at the enlarged size | check the configured storage size on a schedule. On a sharp rise, investigate the cause immediately, usually a bulk insert or a log table |
| compute tier raised by mistake | moving off the burstable tier multiplies the hourly rate | re-read the tier arguments before any change |
| high availability enabled by mistake | a zone-redundant standby is a **second full compute charge** | currently disabled, verified. Know what it costs before enabling it |
| backup storage overrun | raising the retention window increases backup storage in proportion to database size | currently at the one-week default. Recalculate before raising it |

## 4. The other four resources

**Container registry, on the basic tier.** Included storage is ten gigabytes. Every deployment pushes
both a moving tag and a commit tag, so tags accumulate, and **the retention policy is disabled**,
verified. The hub image is large, so a modest number of deployments exhausts the included storage. A
retention policy that prunes old commit tags is the fix. Cross-region pull egress does not apply,
since the registry and the applications share a region.

**Log analytics workspace.** The daily cap was **unlimited** before this work, verified. An application
bug that repeats an error hundreds of times a second, a reconnection loop or a crash loop, can push
ingestion into tens or hundreds of gigabytes within a day, and the billing model is per gigabyte, so
it is charged. **A one gigabyte daily cap now exists**, with the default retention window unchanged.
At current traffic a day's logs should use a small fraction of it, and hitting it loses only the rest
of that day's logs while **the service keeps running**.

**Static web app, on the paid tier.** Included bandwidth is generous and the content is mostly static
assets, so at this scale overrun is unlikely; watch it if traffic ever surges. The linked backend
carries no additional charge, it is part of the tier.

**Storage account and the file share.** The hub's graph is a file-backed database, so every query is
file input and output, and **transaction charges can exceed capacity charges** if traffic grows
substantially, though not at this scale. Capacity grows naturally with the graph, and the account is
on the cheapest redundancy already.

## 5. Orphaned resources, from an actual incident

During the migration **a test container application survived a failed delete command without anyone
noticing** and kept running. The window was short so the cost impact was negligible, but left alone
this accumulates.

Two habits prevent it.

- **A monthly reconciliation.** List what is actually running in the resource group and compare it
  against the inventory in [[vinc.spec.azure-infrastructure]]. Anything not on that list gets
  investigated immediately, as either a test leftover or an undocumented addition.
- **Always re-confirm the result of a delete.** A delete can fail on a transient error, for example an
  unregistered subscription provider, **while appearing to have succeeded**. This was experienced
  directly. It is the same principle as the deployment runbook's rule that an exit code proves
  nothing.

## 6. Cost risks found later

The 2026-08-05 audit found several that this document did not originally cover. They are recorded in
[[vinc.ops.risk-audit-2026-08-05]] with their remediation, and summarized here because they are
charges rather than defects.

- **The embedding worker accumulates its whole result set in memory before applying it.** Batching
  bounds the encode step but not the accumulation, so a large document can exceed the container
  ceiling. An out-of-memory kill there takes the MCP server with it, which converts a cost concern
  into an outage.
- **Community pack downloads are unauthenticated and unmetered**, and each call reads a full pack
  body out of the database, with no cache in front. Bandwidth and database reads are both billed.
- **Pack bodies are stored whole in database rows** and unlisting one leaves the row, with no hard
  delete path, which feeds directly into the storage growth ratchet in section 3.
- **Quotas apply to the connector path only**, not to the web application's graph path, so a free
  member can drive unlimited ingestion and search through the web and the cost lands on compute.
- **Several things grow without bound**: job results are retained in memory for the process lifetime,
  tombstone collection and expired-record purging are defined but never called, and idle session rows
  survive for weeks.
- **The vector index is dropped globally on every embedding round**, which means the hub may be
  running with its vector arm effectively off most of the time: **paying for it and getting full-text
  quality**. Drop and rebuild belong once per round, at the end.

## 7. Responding to a runaway bill

1. On a budget alert, open cost analysis and **identify which resource rose** before doing anything.
   The scope is narrow because there is only one resource group.
2. If it is the container applications, list the revisions and check the replica count, and cap it
   immediately by lowering the maximum if needed.
3. If it is database storage, remember that **deleting the data does not reduce the charge**. The only
   way to recover the cost long term is a new instance and a data migration, so prevention matters far
   more than the immediate response.
4. If it is log ingestion, the daily cap already stops it. The urgent work is finding which container
   and which log is producing the volume.
5. If the cause is unclear or still spreading, scale the offending application to zero replicas, which
   **stops the service and stops the charge**. This is plainly an emergency measure: confirm first
   that the spend really is runaway and that an outage is acceptable.

## 8. Deliberately not done

- **Reservation and savings plan recommendations.** At this scale there is no benefit in prepaying,
  because usage has not reached a predictable steady state.
- **A per-core usage alert separate from the spend budget.** Useful as an early warning independent of
  cost, but low priority while both applications are fixed at one replica.
