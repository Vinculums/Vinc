---
doc_id: vinc.plan.graph-conventions
layer: plan
domain: vinc/governance
authority: record
title: How the conventions contract was decided
supersedes: vinc.plan.graph-md
updated: 2026-08-07
inherits_from: [vinc.canon.graph-conventions]
tags: [plan, governance, record, conventions, graph-md, method, measurement]
---

# How the conventions contract was decided

> **History.** This is the record of the work that produced the assistant writing conventions, not
> the conventions themselves. The contract is [[vinc.canon.graph-conventions]] and it shipped.
>
> The plan is worth keeping for three things it holds that the contract does not: the **method** by
> which an external research report was adjudicated, the **code findings that reversed one of its
> recommendations**, and a **measurement design that was defined and has still not been run.**

## 1. The method: adjudicate the report against the code

The input was a commissioned research report. It was not adopted and it was not discarded. Every
claim was checked against the running system, and each verdict was labelled by the kind of evidence
behind it: a location in code, a confirmed external source, an inference, or a judgment. Labelling
the weak ones is what made it possible to act on the strong ones.

| Claim | Verdict |
|---|---|
| A hybrid of short prose plus a generated block | adopt |
| The store is authoritative and the file is a derived view | adopt |
| A sixty-line ceiling | adopt, with a hard failure added above one hundred |
| Granularity taught by examples rather than by a rule | adopt, with the examples made domain neutral |
| Defer splitting a general core out, mark sections instead | adopt |
| Place it as a repository file, then a stored document, then a protocol resource | **reverse the third step.** Section 3 |
| Have the assistant fill in the content time of a document | **reject.** The write path does not carry the field |
| Generate the skeleton only | adopt, with the boundary of "skeleton" written down |
| Use these three domains as examples | hold. The report said itself that this was an assumption, and the generated block fills it |
| Measure the rate at which new type proposals appear | **modify.** A rate cannot be measured here. Section 5 |

**The report's discipline was better than its conclusions in one respect worth naming.** It marked
its own uncertainties, and the largest one it flagged is exactly where the measurement below started.
A report that says what it does not know is more useful than one that is uniformly confident.

## 2. What reading the code found that the report could not

Seven findings, of which the first is the reason the whole thing was built.

1. **The assistant has no way to see the ratified vocabulary.** Not one of the tools reads it;
   vocabulary reads are HTTP only. So an assistant learns the vocabulary **after writing**, from the
   proposals echoed back in the response. This is the strongest argument for the file: it is not a
   nice-to-have convention sheet, it fills **the one read channel that was empty**
2. **Content time can be read and cannot be written** over the tool path. The asymmetry is not
   ignorance, it is a one-way valve, and it changes what the template may instruct
3. **A document can be stored under a fixed identifier**, so a well-known id for the conventions file
   is possible
4. **That document would synchronize.** Document export sends every document the owner has, with no
   exclusion filter, so a local graph's conventions would overwrite an account graph's
5. **That document would be chunked and indexed**, so fragments of the conventions would surface in
   ordinary search results
6. **The server declares tools and nothing else.** No resources, no prompts
7. **Widening the handshake has caused an outage before.** An unnegotiated field in the initialize
   response broke a connector on 2026-07-31, which puts a risk premium on opening any new protocol
   surface

## 3. The reversal: a read tool instead of a protocol resource

The report recommended ending at a protocol resource. Three independently sufficient objections
sent it elsewhere.

- The server does not declare that capability at all
- Resources are **application controlled**, so a model cannot fetch one on its own initiative. A
  channel the reader cannot open is not a channel
- Extending the handshake carries the risk premium from finding seven

The replacement is a **read-only tool** plus one sentence appended to the ingest tool's description
telling the assistant to call it once before its first write. Tool listings are model controlled and
are always in context, so this works on every host with no human step and no protocol change. The
cost is about twenty lines and one permanently visible tool description.

That single appended sentence is the only structural defense against the failure mode that kills
documents like this one, which is that nobody reads them.

## 4. Two mines that had to be cleared first

Findings four and five above are not caveats, they are prerequisites. Storing the document without
handling them makes the conventions file **actively harmful**: it pollutes search results and lets
one graph assert another graph's domain list. The acceptance criteria for that phase were written to
include both, and the invariant that came out of it is that **each graph owns its own conventions.**

## 5. The measurement design, which is the unfinished part

The report proposed measuring how often new type proposals appear. That cannot be measured here: the
product is local first and has **no ingest telemetry**, by design. Both replacements are stock
measures taken from a snapshot, computable from routes that already exist.

| Metric | Definition | Wanted direction |
|---|---|---|
| Proposal backlog | how many types sit in the proposed state | down |
| Domain fragmentation | after normalizing case and separators, how many groups hold more than one spelling | down |

Three honest limits were written into the design rather than discovered later:

- **One snapshot before the change, then weekly for four weeks.** Against one person's graph, so it
  is an **observation, not a statistic**, and the report of it has to say so
- **The backlog metric has a trap.** It rises whenever ratification simply is not performed, so it
  has to be read next to ratification activity, with new proposals per week counted by hand
- **A stopping rule, with a named alternative.** If neither metric moves in four weeks, the file is
  abandoned and the effort goes into the correction loop instead: make the ingest response explain
  **why** a type is only proposed and name the nearest ratified term. Prevention gives way to
  correction rather than to nothing

**The four-week review has not happened.** The corpus that would be measured has since been
rewritten wholesale, which resets the baseline, and the measurement should be re-baselined rather
than compared across that break.

## 6. The question the numbering still has a hole for

One question was defined precisely and never answered: whether the document write tool should accept
a document's **content time**, given that the read path already returns it.

The plan refused to settle it by argument and specified a blind protocol instead. Twenty documents,
half with the year stated in the body and half genuinely ambiguous; the ambiguous half is the
experiment and the explicit half is the control. Three conditions make the result valid:

- a **fresh session** does the measuring, because a conversation that already knows the risk cannot
  measure it
- the prompt must **not** say "do not invent" or "leave it blank if unsure", or the experiment
  measures that sentence rather than the model
- the document listing tool is **off limits**, because it returns the answer key

Two numbers decide it. Any wrong answer in the explicit half is enough to argue against exposing the
field. The real question is what fraction of the ambiguous half is left **blank**, and below roughly
seven in ten the conclusion is that exposing the field is a feature for **manufacturing wrong
values**. An invented year is not a value known to be wrong; it is a value nobody knows is wrong, so
no downstream correction loop catches it.

It could not be run at the time because the local store was empty and the remote one had no key.

⇒ **This is why the governance decision series has a hole in it.** The number was allocated to this
question in the plan and never became a decision, because the decision was deferred to a measurement
that was never taken. [[vinc.canon.decisions]] leaves the number as a gap rather than closing it by
pulling later decisions down.

## 7. What the plan left open, and what happened to each

| Left open | Outcome |
|---|---|
| How other assistant hosts actually treat protocol resources | Never measured, and it stopped mattering: the chosen path needs no protocol extension. It only becomes a question again if the deferred option is reopened |
| Whether the granularity examples change how material is ingested | Still an inference. It is what the four-week review was for |
| Whether a user writing in Korean should get a Korean conventions file | Both were shipped and the choice left to the user. The validator detects language from section titles and degrades gently when neither matches |
| Whether a shared pack should carry a conventions file | **Closed by the contract: it does not.** Someone else's conventions asserting your domain list is false on arrival |

## 8. What shipped, and when

Phases one through three all landed on 2026-08-01, with the test suite green at each step and the
new route registered across every surface that proxies it. The fourth phase is the measurement in
section 5 and remains open.

The specification moved out of this plan and into a contract document at that point, which is why
this file stops here: **a plan that keeps being edited after it ships turns into a second, worse copy
of the thing it produced.**

## Provenance

| Source | Relation |
|---|---|
| [[vinc.canon.graph-conventions]] | The contract this plan produced. Authoritative for everything current |
| [[vinc.canon.decisions]] | The governance decisions this plan registered, and the gap at six |
| [[vinc.canon.mcp-contract]] | The tool surface, including the read tool this plan added |
| [[vinc.spec.mcp-server]] | The handshake whose extension carries the risk premium |
| [[vinc.spec.dataplane]] | Document synchronization and chunking, the two mines |
| [[vinc.research.methodology]] | The later governance proposals that continued this series |
