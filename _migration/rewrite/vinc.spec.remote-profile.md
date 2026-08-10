---
doc_id: vinc.spec.remote-profile
layer: spec
domain: vinc/infra
authority: spec
title: The remote profile as built
supersedes: vinc.d.remote-profile
updated: 2026-08-06
inherits_from: [vinc.canon.principles]
derives_from: [vinc.research.desktop-shell]
tags: [spec, infra, remote, mcp, connector, oauth, tenancy]
---

# The remote profile as built

> `adr-infra-036`. Vinc is reachable over the network as an MCP endpoint, in addition to the local
> default, so that a browser-based assistant can use it as a connector and so that the same graph is
> reachable from every device. The local profile is unchanged by it, which is the whole point of
> calling this a **separate, opt-in profile**.
>
> The decision was taken on 2026-07-10 and described one shape. What runs is a different shape, and
> this document is the second one. Section 1 says what moved and why, because a reader who has only
> seen the decision will otherwise look for a machine that does not exist.

## 1. What the decision assumed, and what happened

The decision described the operator running Vinc on **their own cloud virtual machine**, with a
reverse proxy in front, the product's homepage served statically from the same host, and the data
therefore moving exactly one step away from the user's own machine while staying out of anyone
else's hands. The framing was explicit: sovereignty moves one notch, this is not a hosted service.

Two things overtook that.

**The virtual machine could not be provisioned.** The subscription refused compute in every region
attempted, so the deployment became managed containers instead. That story, and the resource shapes
it produced, are [[vinc.spec.azure-infrastructure]] section 1.

**Accounts arrived.** Once members could sign in, get a key and reach a graph the project operates,
the remote endpoint stopped being "your own machine, further away" and became a hosted graph keyed
to an account (`adr-product-001`). The name "remote profile" survived; the sovereignty claim behind
it did not.

**What did survive is the part that mattered operationally.** The local profile is still the
default and is unchanged: no account, no key, no network. The remote path is entered deliberately,
per call, and never by upgrade. A user who never opts in cannot tell that the remote path exists.

## 2. The two profiles

| | Local, the default | Remote, opt-in |
|---|---|---|
| Binding | loopback, session token | published ingress on the hub container, section 3 |
| Transport | stdio, to a locally registered host | Streamable HTTP, to a remote connector or to a desktop pointed at it |
| Where the data sits | the user's machine | the hosted graph, on a mounted file share |
| Who the owner is | unscoped, a single local profile | forced from the authenticated subject, section 5 |
| Store relationship | not applicable | **independent.** Moving data between them is what packs and synchronization are for |
| Authentication | none required | required, and the server refuses to start without it |

Moving a graph between the two is not a migration feature: it is [[vinc.canon.share-pack]] for a
copy and [[vinc.spec.dataplane]] section 6 for continuous convergence.

## 3. The loopback invariant does not hold on the hub

The decision's architectural principle was that **the sidecar never changes**: it stays on loopback
inside the machine and a reverse proxy owns the public surface. Half of that is still true and the
other half is not, and the difference is worth stating precisely because it is a security-relevant
fact that reads like a detail.

Inside the hub container the **sidecar is still loopback only**, on the internal port, and the MCP
server reaches it exactly as a local bridge would. But **the MCP transport itself is now the public
surface**: the container entrypoint binds it to all interfaces and the platform ingress publishes
it directly. There is no reverse proxy in front of it, because the reverse proxy went away with the
virtual machine and nothing replaced it.

So the public boundary moved from a proxy that could select paths into the application's own
authorization code. Everything the old proxy would have refused by path now has to be refused by the
server. The hub's internal paths are publicly reachable and a signature check is the only gate on
them, which is recorded as an open exposure in [[vinc.ops.risk-audit-2026-08-05]] and contrasted
with the account container's accidental front door in [[vinc.spec.azure-infrastructure]] section 8.

On desktop the original binding still holds: the HTTP transport defaults to loopback there, and the
difference is entirely in how the hub starts it.

## 4. Three ways in, and none is optional

The transport accepts a caller through any one of three mechanisms, and **refuses to start if none
is configured**, so an unauthenticated public endpoint cannot happen by omission.

- **A static bearer token.** For internal use and for the deployment's own verification step. It
  carries no member identity
- **The authorization gate.** Protected-resource and authorization-server metadata, dynamic client
  registration, authorization codes and proof-key exchange with the stronger method only. It
  implements the specification's requirements and **nothing vendor specific**, which is why two
  different browser assistants register against the same gate. Approval has two modes: an
  administrative key that auto-approves and produces an **unowned** token, for pilot and bootstrap,
  and member approval, which sends the user through the site to sign in and consent and stamps the
  resulting token with their identity
- **A member API key.** The key a member generates on the site, presented as the bearer. The hub
  verifies it against the account and caches the answer briefly, positively for a minute and
  negatively for a few seconds so that a key created a moment ago is not locked out

The verification path deserves its own sentence, because it is the mechanism that keeps the hub
from becoming a second copy of the account. **The hub holds no account database and no account
secret.** It signs a short-lived assertion with its own private key, and the account verifies it
against the hub's **public key only** (`adr-security-102`). Sharing a public key is not sharing a
secret, and that asymmetry is what makes it safe for the two services to be operated as one system
without merging their trust.

> **This path is fragile in a way that is invisible from the application.** On 2026-08-04 the
> platform's own authentication in front of the account rejected the hub's verification calls, and
> every member key failed. The application code was correct throughout. The diagnosis is in
> [[vinc.ops.incident-2026-08-04-easyauth]] and the general lesson is that an empty unauthorized
> response with a bearer challenge means the front door answered, not the application.

## 5. Ownership on the remote path

Whichever mechanism let the caller in, **the owner comes from the authenticated identity and never
from the request**. A member key or a member-approved token resolves to that member; the graph is
scoped to them at the store level by the rules in [[vinc.spec.dataplane]] section 5. The
administrative-key path is the exception and produces an unowned token, which is acceptable only
because it exists for a single-tenant bootstrap.

The plan the account reports travels with the identity and is re-checked periodically rather than
trusted for the life of a token, and an unrecognized plan value is **downgraded loudly rather than
silently**: a quiet downgrade appears to the user as "I paid and it treats me as free", when the
only actual cause is that the two services were deployed out of order.

## 6. Connector state has to outlive a deployment

A connector registers once and expects to stay registered. That is a persistence requirement, and
it was learned the hard way twice.

The authorization state was always designed to be persistent, in a small embedded database.
**The failure was where the file sat.** During the cloud migration it landed on container storage,
so every revision replaced it, every registered client vanished, and a connector presenting its
cached client identifier was refused permanently with an unknown-client error. Moving it to the
mounted share then failed differently, because that share does not provide the byte-range locks the
database needs, and the revision went unhealthy.

The resolution splits the two roles: the **working copy is local** where locking behaves, and the
**snapshot is a file copy** on the share, restored at startup. Copying is the one operation the
share supports without reservation. Section 6 of [[vinc.spec.azure-infrastructure]] records the
same constraint from the storage side.

Note the deliberate asymmetry in failure handling: if the state store cannot be opened at all, the
server **degrades to memory and keeps serving** rather than refusing to start. Connector persistence
is a convenience; the MCP endpoint is the product.

## 7. Reaching it from a desktop

The desktop is a client of this profile as well as an alternative to it. Every tool accepts a target,
and the remote target sends the call to the hosted endpoint with the member key, defaulting to the
production host and overridable by environment. Without a key the tool answers with an instruction
to get one rather than a transport error, because the failure is a setup step and should read like
one.

Synchronization uses the same host by default through its own client. That is a separate channel
from tool calls and is described in [[vinc.spec.dataplane]] section 6.

Registration of a remote connector is a manual, one-time URL entry. There is nothing to automate:
a loopback address is structurally unreachable from a remote host, so the local automation described
in [[vinc.spec.mcp-server]] section 5 has no remote counterpart by construction.

## 8. What the original plan specified that no longer applies

| Original | Status |
|---|---|
| A two-core, four-gigabyte virtual machine in Seoul, at ten to thirty thousand won a month | Never provisioned. The hub is a half-core, one-gibibyte container |
| A reverse proxy terminating TLS and selecting paths | Gone with the machine. TLS is a managed certificate; path selection is now the application's own authorization |
| Service units for the sidecar and the adapter, with ordering between them | Reproduced inside one container entrypoint, which starts the sidecar, waits for its health, then hands off to the transport |
| A nightly backup job copying a snapshot off the host | **Not rebuilt.** Nothing schedules a backup on the hub today, and the share has no snapshots enabled |
| Unattended security updates and an intrusion blocker | Not applicable to a managed container. Patching is an image rebuild |
| The homepage served from the same host, statically only | The static-only rule survived in a different form: the site is a static web app, which cannot host dynamic code at all. The cohabitation is gone; the two are separate resources |
| A free pilot domain as an interim anchor | Skipped. A real domain was registered directly |
| An embedding model resident in memory on the server | Replaced by a lean runtime with the model baked into the image, section 6 of [[vinc.spec.azure-infrastructure]] |

The entrypoint added something the original never had: a **watchdog**. On 2026-08-03 the sidecar
stopped responding for several minutes while the container's main process stayed alive and healthy,
so the platform saw nothing wrong and every graph read and write was blocked silently. The watchdog
now checks liveness every few seconds and restarts the sidecar, giving up after five consecutive
failures rather than restarting forever, because an endless restart loop makes reclaiming the store
lock harder. The health endpoint still reports the condition, so a person retains a signal.

## 9. Open

- **The hub's internal paths are publicly reachable**, gated only by signature verification. See
  [[vinc.ops.risk-audit-2026-08-05]]
- **No backup exists for the hosted graph**, section 8
- **Vector search does not work on the remote path either**, because the embedding worker has never
  loaded its model. A hosted deployment was supposed to be the place where embedding finally
  happened, since the desktop binary excludes the stack entirely. See
  [[vinc.plan.embedding-pipeline]]
- **The sovereignty question left over from section 1.** The decision's own promise, that a user
  could run this on hardware they control, is not currently deliverable in a documented way. Nothing
  in the code prevents it, since the container is ordinary and the store is a file, but no
  self-hosting path is written down or tested
