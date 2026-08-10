---
doc_id: vinc.ops.incident-2026-07-16-macos-sidecar
layer: ops
domain: vinc/infra
authority: record
title: The macOS sidecar that would not start, 2026-07-16
supersedes: vinc.ops.diagnostics-macos
updated: 2026-08-07
inherits_from: [vinc.spec.architecture]
tags: [ops, infra, incident, macos, sidecar, gatekeeper, diagnostics, record]
---

# The macOS sidecar that would not start, 2026-07-16

> **A record of an investigation.** One field report was chased down, **all three hypotheses were
> refuted**, and the real defect turned out to be somewhere else entirely. It is kept for two reasons:
> so nobody investigates the same three things again, and so the measurement technique can be reused.
>
> The identifier changed in the 2026-08 corpus rewrite, from a diagnostics name to an incident name
> carrying the date, because incident records are now named that way uniformly. Nothing about the
> content changed with it.

## 1. The symptom

A field report against an early version on Apple silicon: loading the sample pack failed, and the
built-in diagnostic line named a base address on a dynamic port, a session token of the expected
length, and a health check that timed out.

That one line already ruled a great deal out.

| Clue | Meaning |
|---|---|
| a base address on a high dynamic port | dynamic port assignment worked, so the version is confirmed and the shell allocated a port |
| a session token of the expected length | token delivery worked, so the handshake between the shell and the front end was fine |
| the health check timed out | **nothing was answering on that port** |
| the load failure was the web engine's generic fetch failure | the platform equivalent of a network-level fetch error |

So this was **a completely different layer** from the defects previously seen on Windows, which were
cross-origin policy, token delivery, and a fixed port colliding with a reserved range.

## 2. Measuring macOS from Windows

Neither the sponsor nor the development environment had a Mac. The investigation used **hosted macOS
runners on the build service**, which are Apple silicon and were free for the public repository
involved, and **installed the actual distributed disk image** on them. Three techniques carried it.

1. **Reproduce download quarantine.** A command-line download does not attach the quarantine attribute,
   so a browser download has to be imitated by setting it explicitly, and the right-click approval that
   a user performs is imitated by clearing it **on the bundle only**.

   ```bash
   xattr -wr com.apple.quarantine "0081;$(printf '%x' $(date +%s));Safari;$(uuidgen)" Vinc.app
   xattr -d com.apple.quarantine Vinc.app
   ```
2. **Force the gatekeeper on.** If the runner image has it disabled, the measurement no longer
   resembles real use, so enable it explicitly first.
3. **Encode the answer in the step outcome.** The build service's log API refuses anonymous access, so
   the logs could not be read, **while job and step outcomes are readable through the public API**. So
   each probe was made a separate step allowed to fail, which makes **success or failure itself the
   result**. The investigation workflow lived on a throwaway branch and was deleted once it had served
   its purpose, after three rounds.

## 3. The three refuted hypotheses, do not investigate again

### Quarantine kills the sidecar: refuted

The reasoning was sound. With an unsigned distribution, downloading the disk image marks every
executable inside the bundle. Right-click and open approves **the bundle**, so the sidecar the
application spawns as a child stays marked, and the gatekeeper kills it silently, with no dialogue
because it was launched by a program rather than by a person. It even explained the platform
difference, since the Windows reputation check warns once on first launch of the executable and does
not kill children.

**Measured: the sidecar started successfully even with quarantine applied.** A full imitation of real
use, quarantine set, bundle-only approval, launch, check after fifteen seconds, also left the sidecar
alive. Quarantine is not the cause.

### A slow cold start outruns the health poll: refuted

The reasoning: an unsigned binary's first launch attracts a malware scan, and a single-file frozen
Python bundle unpacks itself on every launch, and both are expensive only on that platform.

**Measured: from launch to a healthy response took under five seconds.** The polling window was ample.

### The web engine blocks a loopback request as mixed content: refuted

The reasoning: on that platform the web view's origin is a secure custom scheme, so a plain-HTTP
loopback request could be blocked as mixed content, because that engine, unlike the other one, does not
treat loopback as a secure origin. On Windows the origin is plain HTTP so the schemes match, and since
such a block happens earlier than cross-origin checks, it did not contradict the earlier cross-origin
fix either.

**The discriminating design mattered more than the hypothesis.** A mixed-content block happens **before
the request leaves for the network**, so if that were the cause there would be **zero client
connections** on the sidecar's port, even though a command-line client can reach it. So, **before**
reaching for a command-line client, two things were measured: whether the application's own script had
run at all, by looking for its keys in the web view's storage, and whether the port carried any
non-listening connection traces.

**Measured: both passed.** The web view had loaded the page and had made a **real connection** to the
sidecar's port. There is no block.

### Conclusion

**The version worked completely normally on a clean Apple silicon machine.** The cause was specific to
that one device, most plausibly a leftover store from an older version or security software. The
build service always starts from a machine with no existing data directory, so **device-specific state
differences cannot be reproduced there**, which is the limit of remote guessing.

## 4. The real defect was the silence

What three rounds of investigation actually bought was this: **the application could say nothing except
that health timed out**. A contemporaneous analysis of connector tool defects had identified the same
disease under the heading of missing failure-signal design, and here it was again one layer up in the
application. With instrumentation, the first report would have ended the matter.

### The strongest candidate, which the next release disarmed

The application's open path let a provisioning exception propagate, which **killed the server
outright**, leaving nobody on the port and producing an unexplained health timeout. **That is exactly
what a stale, locked or corrupted store from an older version reproduces**, and the build service never
hit it because its store is always new. It is the same lineage as provisioning behaving oddly against a
pre-existing database in another session.

### What the next release changed

| Change | Effect |
|---|---|
| the open path swallows the provisioning exception and continues starting | a store problem no longer kills the whole engine |
| health answers **degraded** with the provisioning error or the store error attached | an unhealthy start is no longer hidden, while a healthy one still answers plainly |
| the shell collects the sidecar's error output, exit code and signal, exposed through a status command | the reason for an instant death reaches the diagnostic |
| the diagnostic stamps the application version and the port the shell actually assigned | version and port confusion is removed from the report itself |
| the health wait was extended | headroom for a cold start, harmless even though that hypothesis was refuted |

### How to read the next report

| Diagnostic text | Cause |
|---|---|
| health degraded, with an error attached | **a store or provisioning problem, confirmed.** Removing the data directory and restarting resolves it immediately |
| health timed out, with the sidecar reported as terminated with a code or signal | the sidecar died instantly, and the reason is printed |
| health timed out, with no sidecar events at all | the sidecar is alive and the web view cannot reach it, which is an entirely different branch |

**Status on 2026-08-07:** all of these are still in place. The status command is still registered on
the shell, and health still answers degraded with the provisioning error or store error attached.

## 5. Remaining facts

- The macOS distribution is **Apple silicon only**, so an Intel Mac cannot run it at all. The reporter's
  application did run, which confirms Apple silicon. **Still true on 2026-08-07.**
- Because the application is unsigned, placing it in the applications folder and approving it once is
  **normal procedure**, and it was **not** the cause of this loading failure, since that hypothesis was
  refuted. The only real fix is signing: on Windows through a free programme for public open-source
  repositories, and on macOS through a paid developer membership.
- **The repository's visibility is the precondition** for the free Windows programme and for free build
  minutes. A brief switch to private happened on the day of this investigation and was reverted.

> **What has changed since.** The repository was later confirmed to be **private**, which invalidated
> the premise of the zero-cost signing route, and on 2026-07-30 the decision was taken to **stay
> unsigned until version 1.0.0**. So the platform warnings described here persist by decision rather
> than by neglect, and it is reviewed at 1.0.0. See [[vinc.ops.deployment]] section 2.5 and
> [[vinc.research.deploy-pipeline]] section 2.1.
