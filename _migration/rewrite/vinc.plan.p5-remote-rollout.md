---
doc_id: vinc.plan.p5-remote-rollout
layer: plan
domain: vinc/infra
authority: record
title: P5, the remote rollout that shipped onto a machine that no longer exists
supersedes: vinc.c.p5-remote-rollout
updated: 2026-08-07
inherits_from: [vinc.spec.remote-profile, vinc.research.desktop-shell, vinc.plan.p3-execution]
tags: [plan, infra, record, remote, mcp, connector, deployment, backup]
---

# P5, the remote rollout that shipped onto a machine that no longer exists

> **History. Complete.** This plan took Vinc from a loopback-only tool to a network endpoint a
> browser assistant could connect to. It ran to its last gate but one in five days in July 2026.
>
> **The endpoint survived and its substrate did not.** Within three weeks the machine, the reverse
> proxy, the deployment templates and the co-hosted site were all gone, replaced by managed
> containers. What that rollout produces today is [[vinc.spec.remote-profile]]. Read this for how it
> was built, what broke during bring-up, and the one gate that was never closed.

## 1. The structural idea: do not wait for the server

The owner was going to buy the domain and create the machine. The plan's first move was to sort the
work by whether it actually needed either.

| Track | Work | Needed a server |
|---|---|---|
| Preparatory | The MCP adapter core plus the local transport | no |
| Preparatory | The network transport plus the authorization gate | no, it was verified on loopback |
| Preparatory | The static homepage | no, it is a build artifact |
| Preparatory | Deployment templates, with the domain as the only substitution | no |
| Server | Six sequential steps, each behind a gate | yes |

All four preparatory items **completed and passed on the same day the plan was written**, inside the
existing test gates. The waiting cost nothing, which is the point: the amount of work genuinely
blocked on procurement is usually much smaller than it looks, and sorting for it is cheap.

Two constraints held across every phase. The local profile is **never modified by any of this**, and
secrets are injected from the server environment only, never into a bundle or a repository.

## 2. The server sequence, and its gates

Hardening came before the application, which is the ordering most rollouts get backwards.

| Step | Gate that had to pass |
|---|---|
| Base hardening: key-only login, a three-port firewall, intrusion blocking, unattended security updates, a non-root deployment account | An external port scan answers on nothing else, and password login is refused |
| Names and certificates | Both hostnames answer over TLS with a valid certificate, with placeholder content |
| The sidecar, reusing the release pipeline's frozen Linux binary so the server needed no language runtime | Health passes inside the machine and the port is unreachable from outside |
| The MCP adapter, ordered after the sidecar, with the proxy in front | A connector performs real tool calls against real data, and an unauthenticated request is refused |
| The homepage | The root domain links to the current release |
| Operations: nightly backup, off-host copy, health monitoring, disk alerts | **A restore rehearsal passes and is written into the ledger** |

That last gate was stated as a principle rather than a task: **a backup is not a backup until a
restore has been demonstrated.** It is the only gate in the plan that never closed. Section 5.

## 3. What bring-up actually cost

The ledger is the most valuable part of the original document, because it records the difference
between the plan and the day.

- The domain was acquired, and the machine had to be **created twice**, so its address changed
  between the plan and the deployment
- **The hardening script was silently overridden.** The cloud image ships its own configuration
  fragment whose name sorts after the one the script wrote, so the intended settings lost. The fix
  was to rename the fragment so it sorts first, which is a class of bug that produces no error at all
- **The backup command did not resolve the data home**, and the scheduled job then failed on a lock.
  Both were routed around by exposing backup as an HTTP call to the running service, which is also
  the only way to take a consistent snapshot without stopping it
- **Line endings were pinned in the repository** after the first transfer, because a script that
  arrives with the wrong ones does not run and does not say why
- The organization was renamed and split across four repositories, which made the release pipeline
  cross-repository: build in the source repository, publish into a separate public one
- **The connector registered, then taught a lesson about state.** Authorization sessions were held in
  memory, so restarting the adapter deregistered every client and the connector had to be reconnected
  by hand. That failure recurred later in a different form and is now solved properly, in section 6
  of [[vinc.spec.remote-profile]]
- **The deployed binary predated a fix it depended on.** Every query answered that the tables did not
  exist, because provisioning at startup had been added after that build was cut. Replacing the
  binary with a current one fixed it, since opening the store provisions it idempotently. The lesson
  is that a server binary deployed once is a **version that ages silently**, with no update
  mechanism and no version in any response to reveal it

## 4. What the plan explicitly excluded, and what happened to each

| Excluded then | Now |
|---|---|
| Synchronization between a local graph and a remote one. Moving data was to be manual pack transfer only | **Shipped.** Bidirectional field-level convergence, [[vinc.spec.dataplane]] section 6 |
| The automatic update feed | **Shipped**, with the update bundles signed by their own key |
| Operating system code signing | **Still open**, and reversed once in the meantime. [[vinc.plan.code-signing]] |
| Multiple users and tenancy, on the grounds that Vinc was single user | **Reversed by accounts.** The hosted graph is per member, ownership is forced from the authenticated identity, and isolation became a security requirement rather than an assumption. `adr-product-001` |

The last row is the one to notice. The plan was correct about the product it was describing and the
product changed underneath it. Nothing in an exclusion list should be read as permanent.

## 5. What became of it

Everything below the application layer was replaced between 2026-07-28 and 2026-07-29, when the
hosting moved to managed cloud services. Measured 2026-08-07:

- The machine and its managed database are **deleted**. There is no host to reach by shell
- The `deploy/` directory in the source repository is **empty and untracked**. The templates that
  were this plan's fourth preparatory output are gone, because there is nothing left to apply them to
- The site directory was **removed from the source repository**; the homepage repository holds the
  canonical copy. The cohabitation of product and site on one host was the arrangement that made
  co-serving sensible, and it ended with the host
- The domain changed. The endpoint is reachable under the current name and the old one is retired
- The reverse proxy is gone. Path selection that a proxy would have refused is now the application's
  own authorization code, which is a security-relevant consequence recorded in
  [[vinc.spec.remote-profile]] section 3

**The endpoint survived all of it**, which is the actual verdict on the plan. What was portable was
the layering: a core that knows nothing about transport, a transport that can be wrapped, and an
authorization gate implemented to the specification rather than to one vendor's behavior. What was
not portable was every assumption about the machine.

## 6. The gate that never closed

The plan's final step required an off-host backup and a restore rehearsal, and it stated plainly
that a backup nobody has restored is not a backup. On the machine, the mechanism was demonstrated
and the rotation was verified. The off-host copy and the disk alert were both still unimplemented
when the last ledger entry was written, and they were never implemented afterwards.

⇒ **The hosted graph has no backup today.** The obligation did not disappear with the machine, it
moved: it now applies to a multi-member store on a mounted file share with no snapshots enabled. It
is recorded as an open item in [[vinc.spec.remote-profile]] section 9,
[[vinc.spec.azure-infrastructure]] and [[vinc.ops.risk-audit-2026-08-05]].

## Provenance

| Source | Relation |
|---|---|
| [[vinc.spec.remote-profile]] | What this rollout produces today, and how the shape changed |
| [[vinc.spec.azure-infrastructure]] | The substrate that replaced the machine |
| [[vinc.ops.deployment]] | The deployment policy as built, across all five surfaces |
| [[vinc.spec.mcp-server]] | The adapter core and its transports, the first preparatory output |
| [[vinc.spec.web-system]] | The homepage, which became its own system |
| [[vinc.plan.p3-execution]] | The predecessor plan, whose release pipeline this one reused |
