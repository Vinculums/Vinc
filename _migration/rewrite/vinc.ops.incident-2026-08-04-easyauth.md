---
doc_id: vinc.ops.incident-2026-08-04-easyauth
layer: ops
domain: vinc/infra
authority: record
title: Platform authentication blocked member key verification, 2026-08-04
updated: 2026-08-07
inherits_from: [vinc.ops.deployment, vinc.canon.account-policy]
tags: [ops, infra, incident, easyauth, container-apps, api-keys, record]
---

# Platform authentication blocked member key verification, 2026-08-04

> **A record of an incident.** It is kept for the diagnostic method as much as for the cause, because
> the cause was invisible from outside and the previous incident with the identical symptom had a
> different cause. The runbook is [[vinc.ops.deployment]] and the topology is
> [[vinc.spec.azure-infrastructure]].

## 1. What happened

**Every member key on the hosted connector endpoint returned unauthorized.** The cause was the
platform's **built-in authentication**, switched on for the account container. Linking a container as a
static web app's backend enables that authentication automatically, and it cut the hub's direct call to
the account's internal key verification path **before the request ever reached the application**. The
hub's entitlement check demotes every failure to invalid, fail closed, so from outside the only visible
fact was an unauthorized response to every connector call.

**The web account features looked perfectly fine**, because a browser arrives through the site and
therefore passes. Key issuance succeeded on the day of the incident.

## 2. Why it took so long to find

The runbook already recorded an incident with **the same symptom and a different cause**: a missing
internal address variable falling back to a loopback port. That variable was already set correctly to
the live account hostname. **Checking the recorded cause, finding it healthy, and concluding the
problem was fixed is exactly how this one stays hidden.**

This was the third failure of the same family in this project.

| When | Cause | Symptom |
|---|---|---|
| 2026-07-30 | the authorization issuer variable was missing | authorization disabled entirely |
| 2026-08-04 | the account internal address variable was missing | every member key unauthorized |
| 2026-08-04 | platform authentication on the account blocked the internal path | every member key unauthorized |

What they share is **starts fine, health answers fine, only the function is dead**.

**The environment-existence gate could not have caught the third one, in principle.** Every hub variable
was correct and the defect was at the destination. Unless surface verification includes calling the
endpoint with a **real member key and expecting success**, something of this shape passes again.

## 3. A reusable diagnostic

1. **Read the body of the unauthorized response.** The application gate answers with a small JSON
   object naming the error. **An empty body carrying a bearer challenge header means the ingress
   refused it and the application never ran.** The status code alone does not distinguish the two.
2. **Read the silence in the account log.** The internal gate's rejection writes no audit entry, and a
   request that reaches key lookup logs an invalid-key line. If that line is absent, the failure is
   above the database lookup, which means it is a routing problem and not a key problem.
3. **Reproduce inside the container.** The verification helper swallows every exception, so there is no
   signal outside. Execute inside the hub container: sign an assertion and make the round trip
   directly, then look at the status code and the body.

### Hypotheses ruled out

| Hypothesis | Finding |
|---|---|
| the internal address variable is missing | already set to the live account hostname |
| the hub's seed and the account's pinned key disagree | the derived public key matched the pinned one |
| the hub container's egress is blocked | reached in a fifth of a second |
| clock skew expired the assertion | within tolerance, with two minutes to spare |
| the key was revoked | freshly issued keys failed identically |

**The lesson: rushing to reissue the key pair would have been wasted work. Comparing values comes
first.**

## 4. The remedy

Exclude the two internal paths from platform authentication, then restart the revision.

```
az containerapp auth update -n vinc-account -g vincsRG \
  --excluded-paths "/internal/mcp/verify-key,/internal/mcp/plan"
az containerapp revision restart -n vinc-account -g vincsRG --revision <revision>
```

**The restart is mandatory.** Even when the authentication settings show the exclusions, the
authentication sidecar keeps rejecting on the old configuration; five minutes of polling changed
nothing.

**Do not run this from a Unix-emulation shell on Windows.** Its path translation rewrote the leading
slash of the excluded path list into a Windows path and planted it that way, so the remedy applied only
half. Quoting does not help. Run it from the Windows shell, or disable the translation for the command.
It is the same trap that once planted a Windows path into the hub's data directory, and this was the
second occurrence.

## 5. What the remedy leaves behind

**Two internal paths are now publicly reachable.** The only defence is a single layer: a signed
assertion naming the issuer and audience within a two-minute window. Blocking internal paths from the
public internet was a property of the previous provider's reverse proxy, and on this platform the
built-in authentication had been performing that role **by accident**. A comment at the top of the
account's internal routes still describes the old topology and is stale.

**Verified on 2026-08-05**, one day later: the account's internal verification path answers with a
**body-carrying application-level rejection**, which confirms both that the exclusion is still in force
and that the path is publicly reachable. The standing risk is carried by
[[vinc.ops.risk-audit-2026-08-05]] section 7.4.

## 6. Handled the same day

- **The free-tier subject limit variable was added to the hub**, having been off until then, which is a
  clause of the account policy in [[vinc.canon.account-policy]]. Doing so revealed that **the hub cannot
  roll revisions**, because the outgoing revision holds the store's exclusive lock and the incoming one
  starts with a null driver. The hub pipeline deactivates the old revision and waits; updating the
  container by hand skips that step and leaves a failed revision holding all the traffic. The full
  account is in [[vinc.ops.deployment]] section 5.3.
- **A body whose bytes are not valid text used to pass through the request handler and kill it**,
  surfacing behind a proxy as a gateway error rather than a client error. A fix demoting it to a client
  error was written on a branch and was **not deployed at the time**. As of 2026-08-07 that handling is
  present in the working tree, degrading such a body to the same path as a parse failure; whether it has
  been deployed was not verified.
