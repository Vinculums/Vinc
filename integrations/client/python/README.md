# vinc-client

Python client for the [Vinc](https://vincs.io) REST v1 API. Vinc is a knowledge graph you share with AI: decisions, records and documents your team wrote, with the reasons attached.

This package reads that graph for an agent and writes to it only when your code asks. It is the base of `vinc-langgraph` and `vinc-agent-framework`.

Looking for the `vinc` command? See [the terminal CLI installation guide](https://vincs.io/docs/cli/#install). `vinc-client` is a Python library and does not install a `vinc` executable.

## Install

```bash
pip install "vinc-client>=0.1.9"
```

This README describes vinc-client 0.1.9. The unified `vincs` distribution carries the same source as `vinc.sdk`; a `vincs` release contains the client of the commit it was built from, so check its notes for the client version it includes.

## Keys and spaces

Create a member key in your Vinc account. `VincClient()` reads it from `VINC_API_KEY`; `VINC_API_URL` overrides the hub address (https, or http on loopback only).

| Key | What it can call |
|---|---|
| `vinc_ro_` (read only) | every read in the tables below, including `operation_status` and `role_check` |
| `vinc_sk_` (full) | the reads, plus `ingest`, `record_episode`, `write_package`, `delete`, `delete_edge`, `merge_nodes`, `doc_delete` |

With a read only key every write method raises `VincAuthError` with code `read_only_key` before anything is sent. A `dry_run` preview of a delete or merge counts as a write, because the server ranks it with the change it previews. `vinc.read_only` says which kind you hold. Use a read only key wherever the agent only needs context.

```python
from vinc_client import VincClient

vinc = VincClient()                      # reads VINC_API_KEY
team = VincClient(space="<team id>")     # a team's shared graph instead of your personal one
```

`VincClient(api_key=None, *, base_url=None, space=None, timeout=30.0, transport=None, prefetch_concurrency=8)`. Every method also takes `space=` to override the client's default for one call.

## Methods

`AsyncVincClient` has the same methods, awaited. Optional parameters are keyword only.

Reads (either key):

| Method | Parameters | What it does |
|---|---|---|
| `whoami` | | the key's owner, plan and reachable teams |
| `brief` | `query`, `max_excerpts`, `historical`, `mode`, `as_of`, `known_at` | one node with its excerpts and relations |
| `search` | `query`, `limit`, `cursor`, `detail`, `historical`, `mode`, `as_of`, `known_at` | text and meaning search, paged by `cursor` |
| `recall` | `domain`, `tags`, `limit`, `cursor` | episode timeline, newest first |
| `node` | `node_id` | one node by id; `VincNotFound` when absent |
| `edges` | `node` or `nodes`, `type`, `dir`, `limit` | stored edges with direction and evidence; pass exactly one of `node` and `nodes` |
| `role` | `role_id` | a role's complete contract and links |
| `document` | `doc_id` | exact document text with its content hash |
| `role_check` | `role_id`, `output`, `person_only_requests`, `self_authored` | the hub applies the role's checks; verdict `pass`, `block` or `interrupt`, and `pass` grants no permission |
| `operation_status` | `operation_id` | the receipt of an earlier write (see below) |
| `get_context` | `query`, `search_fallback`, `max_chars`, `max_excerpts`, `min_query_chars` | a fenced context block, or `None` |
| `prefetch` | `role_id`, `task`, `max_chars` | the role's required reading and task evidence before a model call |
| `get_package_context` | `package_id`, `space` (required), `max_chars` | a handoff package, rechecked with this key |

Writes (`vinc_sk_` key):

| Method | Parameters | What it does |
|---|---|---|
| `ingest` | `nodes`, `edges`, `create_only`, `operation_id`, `review_claims` | merge nodes and edges by id |
| `record_episode` | `title`, `summary`, `about`, `date`, `domain`, `tags`, `episode_id`, `create_only`, `operation_id` | one Episode about existing nodes |
| `write_package` | `question`, `conclusion`, `claims`, `produced_by`, `run_id`, `space` (required), `title`, `package_id`, `create_only`, `operation_id` | one handoff Record |
| `delete` | `node_id`, `dry_run`, `preview_digest`, `operation_id` | delete a node and its edges |
| `delete_edge` | `from_id`, `to_id`, `type`, `dry_run`, `preview_digest`, `operation_id` | delete one relationship |
| `merge_nodes` | `keep_id`, `drop_id`, `dry_run`, `preview_digest`, `operation_id` | fold `drop_id` into `keep_id` |
| `doc_delete` | `doc_id`, `dry_run`, `preview_digest`, `operation_id` | delete one document |

Module exports, described in the sections below:

| Export | Parameters |
|---|---|
| `OutputGate` | `props`, `extra_gates`, `self_authored`; methods `evaluate(answer, proofs, person_only_requests, phase, manifest, resolved_sources, repositories)` and `require_pass` |
| `ToolProof` | a host observed tool result for `evaluate` |
| `sources_line` | `text`; the ids on the final `Sources:` line |
| `preserve_required_route` | `base`, `override` |
| `prepare_person_only` | `output`, `gate`, `role_id`, `run_id`, `evidence` |
| `resolve_person_only` | `request`, `decision` |
| `parse_review` | `value` |
| `check_package_handoff` | `node`, `space`, `gate`, `readable_ids`, `repositories`, `evidence_nodes`, `proofs`, `max_chars` |
| `format_context`, `format_brief`, `format_search`, `format_answer`, `fence` | render read results as a fenced block |
| `build_episode_fragment`, `build_package_fragment`, `format_package` | the fragments the write helpers send, and the package block |

## Context for a model turn

```python
block = vinc.get_context("Why are our colour tokens stored as OKLCH?")
if block:
    system_prompt += "\n\n" + block
```

`get_context` makes one call per turn. It returns `None` when nothing matched, and also when Vinc is unreachable or the daily limit is spent, so the model call goes on without it. A key or space problem raises, because it will not fix itself.

The block is fenced and introduced as data, not instructions: text in the graph can be written by anyone with access to it, so the model is told to treat it as reference material.

## Reading

```python
vinc.brief("release checklist")          # one node, its excerpts and relations
vinc.search("spacing scale", limit=10)   # text and meaning search
vinc.recall(domain="vinc/design")        # episode timeline, newest first
vinc.node("decision:tokens-are-oklch")   # one node by id
vinc.edges("decision:tokens-are-oklch")  # its stored edges
```

## Writing, on purpose

Nothing in this package writes because a conversation happened. Record an episode when a person approved what the agent did:

```python
vinc = VincClient(api_key=WRITE_KEY)     # vinc_sk_
vinc.record_episode(
    "Fixed contrast on the dark secondary button",
    summary="Token text-secondary moved to pass 4.5:1 on the dark surface.",
    about=["decision:tokens-are-oklch"],
)
```

Every `about` id is read before the write, so a typo raises `VincNotFound` instead of creating an empty node.

`props` sent with `ingest` replace a node's stored props whole. Read the node first and send the merged dict, or send the node's `expected_rev` (its `rev` from `node`) with `props_patch`, a JSON Merge Patch on the stored props. If someone wrote in between, the whole fragment is refused before any change and `details["current_rev"]` holds the newer revision.

## Retrying a write

A write whose response was lost may or may not have landed. Generate a fresh lowercase UUID for each logical write and pass it as `operation_id`:

```python
import uuid
from vinc_client import VincUnavailable

op = str(uuid.uuid4())
try:
    vinc.record_episode("Release checklist approved", about=["decision:release-plan"],
                        episode_id="episode:release-checklist-approved", operation_id=op)
except VincUnavailable:
    receipt = vinc.operation_status(op)   # decide from the receipt, then retry with the same op
```

- An identical retry with the same `operation_id` returns the stored result without applying again. Changed arguments under the same id conflict.
- `operation_status(op)` reads the receipt. `accepted` and `unknown` do not prove the write did not happen; `committed` means the graph transaction committed. A replayed result is a historical summary, so read the targets to see their current state.
- Receipts are owner scoped, kept until the owner is purged, and bounded to 5000 per owner.
- `create_only=True` refuses identities that already exist, before any change. Without it a write keeps the usual upsert behaviour.
- `record_episode` and `write_package` pass `create_only` and `operation_id` to `ingest` unchanged. The client never retries a write on its own.

## Delete and merge with a preview

`delete`, `delete_edge`, `merge_nodes` and `doc_delete` change the graph irreversibly. Call them twice: first with `dry_run=True`, which changes nothing and returns the complete preview with a `preview_digest`; then with that digest.

```python
preview = vinc.merge_nodes("concept:colour-tokens", "concept:color-tokens", dry_run=True)
# show the preview to a person, then apply exactly what they saw:
vinc.merge_nodes("concept:colour-tokens", "concept:color-tokens",
                 preview_digest=preview["preview_digest"], operation_id=op)
```

If the affected state changed after the preview, the apply call is refused instead of acting on something nobody saw. A preview that would be too large, or that touches a target the key cannot reach, is refused too. Both calls need a `vinc_sk_` key.

## Errors

| Class | Meaning |
|---|---|
| `VincAuthError` | missing, malformed, refused or read only key; configuration is wrong |
| `VincNotFound` | the node, document or space does not exist for this key |
| `VincInvalidArgument` | the request was malformed or refused: a topic limit, a `create_only` identity that exists, an `expected_rev` or `preview_digest` that no longer matches, an `operation_id` reused with different arguments |
| `VincQuotaExceeded` | the daily limit is spent; `retry_after` is in seconds |
| `VincUnavailable` | network or server failure; for a write, check `details["write_may_have_applied"]` and `operation_status` |
| `VincPartialWrite` | part of a write was skipped; `result` holds what the server said |



## Review output validation

`ReviewOutput` validates `verdict` (`ACCEPT` or `RETURN`),
`findings[{what, cite[]}]` and `person_only_requests[{action, reason}]`.
Legacy reviews may omit `person_only_requests`; they default to an empty list.
Optional `quote` and `rule` fields preserve the reviewer's explanation.
`RETURN` requires findings and `ACCEPT` requires none. `parse_review` rejects
malformed JSON and extra fields. Shape validation does not establish citation
existence, completion, or a person's approval.

The framework packages expose review helpers with one parse retry. If both
outputs fail, they raise `UnparsedReview` with `status="UNPARSED"`, rather than
returning an empty successful review. Route that exception to a person.

## Hand a package to another agent

Write a package explicitly after reviewing the producer's result. Select `space`
explicitly (`"personal"` or a team UUID); all evidence must already exist there.
This initial contract supports same-space handoffs only.

```python
handoff = writer.write_package(
    "When does the dashboard become stale?", "Keep the 36 hour threshold.",
    claims=[{"claim": "Stale after 36 hours.", "evidence_ids": ["decision:threshold"]}],
    produced_by="agent:analyst", run_id="analysis-1", space="personal",
    package_id="record:dashboard-handoff",
)
# The receiving agent uses its own key, which may be read only.
block = reader.get_package_context(handoff["package_id"], space=handoff["space"])
```

The Record carries the complete question, conclusion, claims, source ids and
producer/run provenance. Source contents are never copied. Each read checks
distinct evidence ids with the receiving key again; deleted or inaccessible
evidence makes the affected claim explicitly uncitable. Missing carriers and
service/authentication errors propagate. No permission or content cache is used.
Returned evidence state includes the current content hash and any validity
interval or successor the server provides. Readable evidence is not necessarily
current: historical or superseded evidence has `use_as_current=false`, and
unknown currentness remains unknown. These are read-time observations, not a
pinned copy of the producer's source revision.
The complete data block is fenced; exceeding `max_chars` raises
`package_budget_exceeded` instead of truncating the conclusion. Use a fresh
package id for each handoff; choosing an existing id deliberately replaces that
Record's props. The server validates sources when writing, so source Records
must be persisted before the package rather than created alongside it.


## Prefetch role evidence before a model call

```python
block = reader.prefetch(
    "concept:agent-reviewer-judgement", "Review the proposed change.",
    max_chars=120000,
)
```

This uses the role API from VIN-303, reads each distinct required spec by its
declared document identifier through `document(doc_id)`, then briefs the task
and searches with the role topic as
context. Returned node ids remain in the fenced reference blocks. The topic is
search context, not a hard domain filter. Role contracts remain graph data;
choosing this API does not delegate work or widen the caller's permissions.

The block asks the model to end every answer, refusals and fixed-format outputs
included, with one final line `Sources: [id] [id]` (or `Sources: none`).
`sources_line(answer)` returns the id-shaped tokens on that line, `[]` when it
names none and `None` when the line is absent; it does not change the output
gate's verdict.

The caller must choose the role explicitly. Its complete contract is retained;
each required document is retained in full, with its verified content hash.
For each verified document, prefetch also reads its mirror node in the same
space, within the same bounded worker. Only an existing readable mirror gets a
`cite as` id. A missing mirror keeps the full document and is labelled
`no citable id`; the model must not invent a citation from the document key.
Mirror authentication, quota, service and malformed-response failures propagate.
Missing, partial or unverified documents raise `prefetch_spec_unresolved`, and
no optional task lookup result is used. Invalid role contracts, authentication and
service failures propagate. There is no prefetch permission/content cache or automatic write.
Client 0.1.3 reads required documents in concurrent batches of at most eight,
using a per-call thread pool for `VincClient` and tasks for `AsyncVincClient`.
Results are validated and rendered in the declared reading order. Starting with
client 0.1.4, optional task lookups may start before validation: `brief` is
submitted first and `search` right after the role read. Task lookups and documents
share one pool of `prefetch_concurrency` slots (documents queue behind the lookups);
the role read runs on the calling thread. If
validation fails they are cancelled and any result that already arrived is
discarded; the required error is raised unchanged. A failed batch stops
subsequent batches; requests already in flight may finish. Pending reads are
cancelled and all in-flight work is drained before return. Synchronous network reads cannot be interrupted once started,
so failure cleanup can wait for their configured request timeout.

Set `VincClient(..., prefetch_concurrency=1)` or
`AsyncVincClient(..., prefetch_concurrency=1)` to retain sequential reads
(role, documents, budget, brief, search).
The setting accepts integers from 1 to 8. In synchronous mode, 1 also keeps
custom transport calls on the calling thread; concurrent custom transports must
be thread safe. Pass that configured reader to either framework adapter to use
the same policy. Every invocation still makes fresh requests; there is no implicit
reuse across model calls. Concurrency changes elapsed time, not required input size.

The combined budget defaults to 24,000 characters. Starting with client 0.1.3,
`prefetch_budget_exceeded` reports the **complete** `required_size` in both its
message and `error.details`, after reading and verifying every required document.
Optional brief/search results are discarded on that failure (with concurrency
above 1 the calls may already have been sent). `error.details["max_chars"]`
is the attempted budget; `error.details["recommended_max_chars"]` adds 6,002
characters for the separator and up to 6,000 characters of optional task evidence.
Choose that value once as `max_chars` (or the adapter's `prefetch_max_chars`) after
accepting the model input cost. `required_size` alone fits the full required context
exactly but leaves no task evidence space. No automatic retry, budget increase,
truncation or prefetch cache occurs. If the role or documents change between calls,
the required size can change too.

On 2026-10-06, all 23 hosted roster roles exceeded the default. Their required
text was 19,764 to 198,480 characters (median about 112,000); successful prefetch
blocks at a larger budget were 34,640 to 217,070 characters including the role
contract, formatting and task evidence. These are characters, not tokens or a
model price estimate, from one hub and one task. LangGraph injects the block on
every model call, including later tool-loop calls; the Microsoft Agent Framework
provider injects it once per run. A smaller budget stops the model instead of
silently reducing its required reading.

CI replays all 23 role read lists with verified hosted document sizes and synthetic
bodies in `tests/test_prefetch_budget.py`. A fresh read-only key can also check live
roles and one explicit correction before release, without calling a model:

```bash
# Set VINC_API_KEY in the command environment only, using a vinc_ro_ key.
python integrations/client/python/examples/check_prefetch_budget.py --space personal
```

For a paired latency check, with the same read-only key in the command environment:

```bash
python integrations/client/python/examples/benchmark_prefetch.py --iterations 4
```

This alternates sequential/concurrent order on the same hub and task, measuring
fresh calls with a 600,000-character budget in both sync and async modes. The
sequential setting reproduces the successful 0.1.2 request order; both paths run
the installed client version. It prints each timing and the two medians, and fails
if either concurrent median is not below half its sequential median. The measured
time combines hub work and network time. `--synthetic` instead replays captured
agent-qa text sizes and scaled 0.28/0.24/0.95-second request delays; those results
are labelled synthetic and cannot establish a live-hub latency acceptance.

The check prints only role ids and sizes, never keys or retrieved document bodies.
This provides evidence before the model runs; model citation quality needs a
separate evaluation.


## Version notes

| Version | Change |
|---|---|
| 0.1.9 | `record_episode` and `write_package` take `create_only` and `operation_id`. New `delete`, `delete_edge`, `merge_nodes` and `doc_delete` with `dry_run` and `preview_digest`. New `edges` and `role_check`. The adapters 0.1.5 pass the two write parameters through and require this client. |
| 0.1.8 | `ingest` takes `create_only` and `operation_id`; `operation_status` reads a write's receipt; `expected_rev` and `props_patch` guard a node edit. |
| 0.1.7 | `brief` and `search` take `mode`, `as_of` and `known_at`, and results carry a `temporal` object. Prefetch gives a `cite as` id only for a readable mirror. The adapters 0.1.4 expose the same read parameters. |
| 0.1.6 | `get_context` blocks end with the same `Sources:` line instruction as prefetch. |
| 0.1.5 | The prefetch block names the citable id of each required reading and asks for a final `Sources:` line; `sources_line` reads it. Both adapters share one tool schema. The adapters 0.1.3 cap Vinc tool calls at 4 per run. |
| 0.1.4 | Optional prefetch reads start before validation. |
| 0.1.3 | Required prefetch reads run in concurrent batches; a budget error reports the complete required size. |

## License

MIT
# Deterministic role output gate

Fetch fresh structured role props, then freeze them for this invocation:

```python
from vinc_client import OutputGate, ToolProof

gate = OutputGate(role["props"])
result = gate.evaluate(final_answer)
# result.verdict is pass, block or interrupt; this does not approve an action.
```

The gate reads typed `gates`, `never_checks` and action/reason `person_only`.
Unknown conditions and legacy prose-only contracts block. Reasons never determine
authority. Required gates are immutable; `extra_gates` can only add distinct IDs.
`preserve_required_route(base, override)` rejects removal or shortcut bypass of
an existing gate-runner path.

Host-observed terminal results can support an exact completion sentence:

```python
proof = ToolProof("tests", "tests/core", actual_call_id, True,
                  claim="Focused core tests passed.")
result = gate.evaluate("Focused core tests passed.", proofs=[proof])
```

Construct proofs from actual calls in this invocation. Never deserialize model
proofs or accept a model's completion claim as evidence. A proof cannot support
a different sentence or a broader all-tests claim. The library cannot authenticate
fabricated host input. It does not execute, authorize or undo tools.

At `phase="handoff"`, declare the package repositories, its producer/run/claims
manifest and the source IDs the recipient actually resolved under current
permissions. Applicable original command gates need terminal `ToolProof` entries
with `action="command"` and `target` equal to the original command string. Missing
sources/results block; ordinary text answers do not require a handoff manifest.

Results include check IDs, pending action IDs and bounded matching evidence
sentences. Treat those excerpts as private model output; do not log or copy them
across spaces automatically. `require_pass` raises `OutputGateStopped` on block
or interrupt, with no model output in its exception message.

These checks implement an enumerated subset of `never`; its preserved prose
continues to bind the operator. There are no model calls or graph writes.

## Structured person requests and approval

`RoleOutput` contains `text` and `person_only_requests: [{action, reason}]`.
Use it as the model response schema; each action must match the fresh role's
adopted person-only action ID. `prepare_person_only(output, gate, role_id=...,
run_id=...)` returns a checked output or a draft-bound `ApprovalRequest`. Blocked
outputs raise before becoming approvable. The run ID must identify this invocation.

The host authenticates the human and constructs an `ApprovalDecision` with the
exact request ID and a boolean `approved`. `resolve_person_only` resumes that
saved request without a model call. A different draft/run, modified request or
truthy string cannot approve it. Rejection returns no deliverable.

Approval releases this reviewed output only. It does not authorize or execute
tools, merge, deploy or publish anything. Keep side-effect tools behind a separate
host authorization step. The request hash binds data; it does not authenticate
the responding person. Treat drafts and pending requests as private output.
## Quote a checked chain handoff

Use `check_package_handoff(node, space=..., gate=producer_gate,
readable_ids=current_reader_ids, proofs=producer_tool_records,
repositories=package_repositories)` before forwarding an existing package.
The host resolves those IDs with the current reader in the selected space;
model-created proof dictionaries and cached permissions are not evidence.
The helper checks the complete conclusion and claims, preserves their exact
wording and attribution, and refuses empty or unresolved manifests. It neither
routes nor executes a task. Orchestrators quote this incoming block and add their
own selected role, bounded package, acceptance criteria and delivery order.


### Time and validity

`search` and `brief` accept optional `mode="general"`, `"current"`, or `"historical"`.
Omitting the mode preserves existing behavior. Historical mode keeps expired evidence
available; current mode checks present validity and reports unknown dates and conflicting
claims. It cannot be combined with `historical=True` or `as_of`.

```python
client.search("service release", mode="current")
client.search("service release", mode="historical", as_of="2025")
```

`as_of` selects overlapping half-open validity periods. `known_at` filters initial creation
time; it does not restore an earlier body revision. Search nodes and passages carry a
`temporal` object with nullable creation, update, occurrence and validity dates, successor
metadata, explicit supersession evidence and conflicts. Update time is not occurrence time.
The read tools in both framework adapters expose these optional parameters too.
