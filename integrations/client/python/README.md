# vinc-client

Python client for the [Vinc](https://vincs.io) REST v1 API. Vinc is a knowledge graph you share with AI: decisions, records and documents your team wrote, with the reasons attached.

This package reads that graph for an agent and writes to it only when your code asks. It is the base of `vinc-langgraph` and `vinc-agent-framework`.

Looking for the `vinc` command? See [the terminal CLI installation guide](https://vincs.io/docs/cli/#install). `vinc-client` is a Python library and does not install a `vinc` executable.

**If the agent has to follow its role's contract (what it must never do, its gates, the tools it may not use), read it with `prefetch` (role mode).** `prefetch` raises when Vinc cannot be read; `get_context` (context mode) returns `None` and the model call goes on without context. See [Role mode or context mode](#role-mode-or-context-mode).

## Install

```bash
pip install "vinc-client>=0.1.20"
```

This README describes vinc-client 0.1.20. The unified `vincs` distribution carries the same source as `vinc.sdk`; a `vincs` release contains the client of the commit it was built from, so check its notes for the client version it includes.

For a new install, prefer `pip install vincs`: it has the same client as `vinc.sdk`, the framework adapters as extras and the `vinc` command. `vinc-client` keeps publishing under this name for existing installs.

## Keys and spaces

Create a member key in your Vinc account. `VincClient()` reads it from `VINC_API_KEY`; `VINC_API_URL` overrides the hub address (https, or http on loopback only).

| Key | What it can call |
|---|---|
| `vinc_ro_` (read only) | every read in the tables below, including `operation_status` and `role_check` |
| `vinc_sk_` (full) | the reads, plus `ingest`, `record_episode`, `record_run`, `write_package`, `delete`, `delete_edge`, `merge_nodes`, `doc_delete` |

With a read only key every write method raises `VincAuthError` with code `read_only_key` before anything is sent. A `dry_run` preview of a delete or merge counts as a write, because the server ranks it with the change it previews. `vinc.read_only` says which kind you hold. Use a read only key wherever the agent only needs context.

```python
from vinc_client import VincClient

vinc = VincClient()                      # reads VINC_API_KEY
team = VincClient(space="<team id>")     # a team's shared graph instead of your personal one
```

`VincClient(api_key=None, *, base_url=None, space=None, timeout=30.0, transport=None, prefetch_concurrency=8, retry_for=90.0)`. Every method also takes `space=` to override the client's default for one call.

| Argument | Default | Meaning |
|---|---|---|
| `api_key` | `VINC_API_KEY` | a `vinc_ro_` or `vinc_sk_` member key |
| `base_url` | `VINC_API_URL`, then `https://mcp.vincs.io` | https, or http on loopback only |
| `space` | personal | `"personal"` or a team id |
| `timeout` | `30.0` | seconds per request |
| `transport` | `None` | an `httpx` transport (`httpx.AsyncBaseTransport` for `AsyncVincClient`), for tests or a proxy |
| `prefetch_concurrency` | `8` | how many prefetch reads run at once, 1 to 8 (see [Prefetch](#prefetch-role-evidence-before-a-model-call)) |
| `retry_for` | `90.0` | seconds to keep retrying a transient error; `0` turns retries off (see [Transient errors](#transient-errors)) |

Use the client in a `with` block or call `close()` when you are done; `AsyncVincClient` takes `async with` or `aclose()`.

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
| `role` | `role_id`, `pin`, `compact` | a role's complete contract and links, with its `rev` and `hash`; with `pin`, only while the role is unchanged (see [Pin a role](#pin-a-role)); a role over the read limit comes back without relation evidence (see [Large roles](#large-roles)) |
| `document` | `doc_id` | exact document text with its content hash |
| `role_check` | `role_id`, `output`, `person_only_requests`, `self_authored` | the hub applies the role's checks; verdict `pass`, `block` or `interrupt`, and `pass` grants no permission |
| `operation_status` | `operation_id` | the receipt of an earlier write (see below) |
| `get_context` | `query`, `search_fallback`, `max_chars`, `max_excerpts`, `min_query_chars` | a fenced context block, or `None` |
| `prefetch` | `role_id`, `task`, `max_chars`, `pin` | the role's required reading and task evidence before a model call |
| `prefetch_with_contract` | `role_id`, `task`, `max_chars`, `pin` | the same block as `prefetch`, with the role contract it read (`RolePrefetch(block, contract)`) |
| `get_package_context` | `package_id`, `space` (required), `max_chars` | a handoff package, rechecked with this key |

Writes (`vinc_sk_` key):

| Method | Parameters | What it does |
|---|---|---|
| `ingest` | `nodes`, `edges`, `create_only`, `operation_id`, `review_claims` | merge nodes and edges by id |
| `record_episode` | `title`, `summary`, `about`, `date`, `domain`, `tags`, `episode_id`, `create_only`, `operation_id` | one Episode about existing nodes |
| `record_run` | `run_id`, `role`, `outcome`, run fields (`task_ref`, `host`, `model_family`, `steps`, `tool_calls`, `tokens_in`, `tokens_out`, `cost_usd`, `gate_results`, `cited_ids`, `shims_fired`, `trace`), `about`, `date`, `domain`, `tags`, `create_only`, `operation_id` | one agent run as an Episode about its role |
| `write_package` | `question`, `conclusion`, `claims`, `produced_by`, `run_id`, `space` (required), `title`, `package_id`, `create_only`, `operation_id` | one handoff Record |
| `delete` | `node_id`, `dry_run`, `preview_digest`, `operation_id` | delete a node and its edges |
| `delete_edge` | `from_id`, `to_id`, `type`, `dry_run`, `preview_digest`, `operation_id` | delete one relationship |
| `merge_nodes` | `keep_id`, `drop_id`, `dry_run`, `preview_digest`, `operation_id` | fold `drop_id` into `keep_id` |
| `doc_delete` | `doc_id`, `dry_run`, `preview_digest`, `operation_id` | delete one document |

Module exports, described in the sections below:

| Export | Parameters |
|---|---|
| `OutputGate` | `props`, `extra_gates`, `self_authored`; methods `evaluate(answer, proofs, person_only_requests, phase, manifest, resolved_sources, repositories)` and `require_pass(answer, **kwargs)`, which takes the same keywords and raises `OutputGateStopped` on block or interrupt |
| `GateResult` | what `evaluate` returns: `verdict`, `failed_checks`, `person_only_actions`, `evidence_sentences`; `as_dict()` |
| `OutputGateStopped` | raised by `require_pass`; `result` holds the `GateResult` |
| `ToolProof` | `action`, `target`, `call_id`, `success`, `claim=""`: a host observed tool result for `evaluate` |
| `sources_line` | `text`; the ids on the final `Sources:` line |
| `preserve_required_route` | `base`, `override` |
| `RoleOutput` | `text`, `person_only_requests` |
| `ApprovalRequest` | `request_id`, `role_id`, `run_id`, `output`, `actions` |
| `ApprovalDecision` | `request_id`, `approved` |
| `ApprovalResult` | `status`, `output`, `actions`; `as_dict()` |
| `prepare_person_only` | `output`, `gate`, `role_id`, `run_id`, `evidence` |
| `resolve_person_only` | `request`, `decision` |
| `ReviewOutput` | `verdict`, `findings`, `person_only_requests` |
| `Finding` | `what`, `cite`, `quote`, `rule` |
| `PersonOnlyRequest` | `action`, `reason` |
| `parse_review` | `value` |
| `UnparsedReview` | `reason`; `status` is `"UNPARSED"` |
| `check_package_handoff` | `node`, `space`, `gate`, `readable_ids`, `repositories`, `evidence_nodes`, `proofs`, `max_chars` |
| `RolePrefetch` | what `prefetch_with_contract` returns: `block`, `contract` |
| `RoleToolPolicy` | `role_id`, `disallowed`; `from_contract(contract)`, `allows(name)`, `allowed(tools)`, `unmatched(tools)`, `warn_unmatched(tools)`, `refusal(name)` |
| `format_context` | `brief`, `search=None`, `max_chars=4000`, `limit=5`: the block `get_context` returns, opening with the `Sources:` line instruction |
| `format_answer` | `brief`, `search=None`, `max_chars=4000`, `limit=5`: the same block without that instruction |
| `format_brief` | `brief`, `max_chars=4000` |
| `format_search` | `result`, `max_chars=4000`, `limit=5` |
| `fence` | `body`; wraps it in a backtick fence longer than any backtick run inside |
| `BOUNDARY`, `SOURCES_LINE` | the sentence that marks a block as data, not instructions, and the final `Sources:` line instruction |
| `build_episode_fragment` | `title`, `summary`, `about`, `date`, `domain`, `tags`, `episode_id`: the fragment `record_episode` sends |
| `build_run_fragment`, `run_episode_id`, `trace_ref` | the fragment `record_run` sends, the episode id of a run id, and a raw trace file's address |
| `build_shim_fragment` | `slug`, `target`, `patches`, `mechanism`, `applies_to`, `installed_because`, `measured_by`, `text`, `valid_from`, `title`, `domain`: one shim Record (see [Shims](#shims)) |
| `current_shims`, `attached_shims` | `client`, `role_id` (or `targets`), `model_family`, `rung`, `today`, `harness`, `space`: the current shims as `Shim` values |
| `shim_lines` | `shims`, `mechanisms`: the texts of prompt line and nudge shims |
| `read_runs`, `retirement_drafts` | the run episodes of a window, and draft Decisions for shims that stopped firing |
| `Shim`, `HARNESS_CONCEPT`, `MECHANISMS` | a stored shim, the concept all-role shims attach to, and the four mechanisms |
| `build_package_fragment` | `question`, `conclusion`, `claims`, `produced_by`, `run_id`, `title`, `package_id`: the fragment `write_package` sends |
| `format_package` | `node`, `space`, `readable_ids`, `evidence_nodes`, `max_chars=16000`: the package block |
| `VincError` and its subclasses | see [Errors](#errors) |

`vinc.read_only` is `True` for a `vinc_ro_` key. `without_retries()` is described under [Transient errors](#transient-errors).

## Context for a model turn

```python
block = vinc.get_context("Why are our colour tokens stored as OKLCH?")
if block:
    system_prompt += "\n\n" + block
```

`get_context` makes one call per turn. It returns `None` when nothing matched, and also when Vinc is unreachable or the daily limit is spent, so the model call goes on without it. A key or space problem raises, because it will not fix itself.

The block is fenced and introduced as data, not instructions: text in the graph can be written by anyone with access to it, so the model is told to treat it as reference material.

## Role mode or context mode

The framework adapters build their two modes on two calls here. The client keeps nothing between calls; how often a call runs is the adapter's choice (see the vinc-langgraph and vinc-agent-framework READMEs).

| Mode | When Vinc is unreachable or over the daily limit | Hub calls per call | Tools the role disallows | Pinning |
|---|---|---|---|---|
| Role mode (`prefetch`, `prefetch_with_contract`) | Raises with no retry (fails closed). Any failed read raises the same way: the role, a required document or the task lookup. | 1 for the role, 2 per required document (its text and its mirror node), 1 brief and 1 search. | `prefetch_with_contract` returns the contract; `RoleToolPolicy` applies its `disallowed_tools` by exact name (see below). | With `pin` (a hash from `role`), a role that changed raises `VincRoleChanged` before any document is read (fails closed). Without it, each call reads the role as it is now. |
| Context mode (`get_context`) | Logs a warning and returns `None`, so the model call goes on without context (fails open). A key or space problem still raises. | One brief, plus a search when `search_fallback=True` and the brief ties or finds nothing. None for a query shorter than `min_query_chars`. | Not applied: there is no role. | Not applicable: each call reads the graph as it is. |

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

## Record a run

When an agent run ends, write it once as an Episode about its role. Keep the raw trace (every prompt, tool call, tool result and output) as a file of your own, and pass its address, never a summary of it:

```python
from vinc_client import trace_ref

vinc.record_run(
    "run-2026-10-25-7",                      # the host's run id
    role="concept:agent-reviewer", outcome="success",   # success, failure, blocked, waiting, abandoned, error
    host="langgraph", model_family="claude", steps=4, tool_calls=6,
    tokens_in=18_200, tokens_out=950, cost_usd=0.021,
    cited_ids=["decision:tokens-are-oklch"],
    trace=trace_ref("traces/run-2026-10-25-7.jsonl"),   # sha256, bytes and the file name only
)
```

The episode id comes from the run id, so a retry of the same run merges into the same node; send all of the run's values again, because stored `props` are replaced whole. `trace_ref` reads the file to hash it and stores only its name, never the local path; pass `uri=` when the trace has a location you can share. Values you leave out are not stored. The role and every `about` id are read before the write.

## Shims

A shim is a deterministic patch a harness adds because a model needs it today: a prompt line, a nudge, a tool filter or a gate. Keep each one as a Record attached to the role it patches, or to the harness concept `concept:vinc-agent-harness` when it applies to every role, so it carries the day it was installed, the incident that justified it and, later, the day it was retired:

```python
from vinc_client import build_shim_fragment

fragment = build_shim_fragment(
    "say-when-tests-were-not-run",
    target="concept:agent-reviewer",           # or HARNESS_CONCEPT for every role
    patches="claims tests passed without running them",
    mechanism="prompt_line",                    # prompt_line, nudge, tool_filter or gate
    applies_to=["claude"],                      # model families or rungs; empty means all
    installed_because="record:review-claimed-green-tests-without-a-run",
    text="Say which tests you ran; if you ran none, say so.",
)
vinc.ingest(fragment["nodes"], fragment["edges"])
```

The id is `record:shim-<slug>`, `valid_from` defaults to today and the edge is `relates_to` with an evidence sentence. A `prompt_line` or `nudge` shim needs its `text`. The target must already exist.

Before a run, read the shims that apply and add their lines:

```python
from vinc_client import current_shims, shim_lines

shims = current_shims(vinc, "concept:agent-reviewer", model_family="claude")
system_prompt += "\n".join(shim_lines(shims, mechanisms=["prompt_line"]))
```

`current_shims` makes one edge read for the role and the harness concept and one node read per shim. It keeps a shim while `valid_from <= today < valid_until` and its `applies_to` is empty or names the family (or the `rung` you pass); with neither it keeps every current shim. A missing role raises `LookupError`, a harness concept that does not exist yet is fine, and an edge read that was cut short raises `RuntimeError` instead of returning a partial list. `shim_lines` returns the texts in id order without repeats; put them in the stable part of a prompt. Tool filters and gates are yours to apply; `Shim.mechanism` says which is which.

When the run ends, report what fired with `record_run(..., model_family=..., shims_fired={"record:shim-say-when-tests-were-not-run": 1})`. No node is written per firing.

To find shims that stopped firing, read the run episodes of a window and draft retirements:

```python
from vinc_client import HARNESS_CONCEPT, attached_shims, read_runs, retirement_drafts

runs = read_runs(vinc, since="2026-09-25")    # run episodes tagged agent-run, with props
shims = attached_shims(vinc, ["concept:agent-reviewer", HARNESS_CONCEPT])
drafts = retirement_drafts(shims, runs, window_start="2026-09-25", min_runs=30,
                           families_in_use=["claude", "gpt"])
```

`retirement_drafts` reads nothing. A shim is proposed when, on every family in use that it applies to, the runs that load it (its role's runs, or every run for a harness shim) since the window start or its install day number at least `min_runs` and fired it zero times. Each draft is a fragment with one Decision `decision:retire-shim-<slug>`, `props.state` `"draft"`, the per-family `counts` and the shim id in `props.shim`, `about` the role or the harness concept. Not firing does not prove a shim is unneeded, since a prompt line can prevent what it patches without visibly firing: a person ratifies only after the leave-one-out ablation row without that shim passes, then sets the shim's `valid_until`. `read_runs` pages the episode timeline and reads each `episode:run-...` by id, because the timeline returns no props. From the command line the same steps are `vinc shim ls` and `vinc shim watch`.

## Transient errors

The hosted hub can be briefly unavailable, for example while a new version is deployed. The client retries those cases for you, so they read as a slower call instead of an error:

- What is retried: HTTP 502, 503 and 504, a dropped connection and a timeout. Anything else (401, 404, 409, 429, 500 and every other answer) is raised at once, and so is an answer whose `details["retryable"]` is `false`.
- Which calls: reads (every `GET`, `brief`, `search`, `edges` with several nodes, `role_check`), previews (`dry_run=True`), and writes that carry an `operation_id`. A write without `operation_id` is never retried, because the server could apply it twice.
- How long: the client follows `Retry-After` when the hub sends it, and otherwise backs off exponentially up to 8 seconds between tries. It stops once the next try would start more than `retry_for` seconds (90 by default) after the first, and then raises the last error.
- `retry_for=0` turns retries off.
- `get_context` and `prefetch` never retry. They run inside a model turn, so a short outage makes them skip the turn or raise straight away rather than hold the conversation.
- `without_retries()` returns the same client (one connection) with retries off, for your own code that runs inside a model turn. The LangGraph and Agent Framework tools use it.

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
- `record_episode` and `write_package` pass `create_only` and `operation_id` to `ingest` unchanged. The client retries a write on its own only when it carries an `operation_id` and the error was transient (see above).

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
| `VincError` | the base class; every error below carries `code`, `message`, `status`, `request_id`, `details` and `retry_after` |
| `VincAuthError` | missing, malformed, refused or read only key; configuration is wrong |
| `VincNotFound` | the node, document or space does not exist for this key |
| `VincInvalidArgument` | the request was malformed or refused: a topic limit, a `create_only` identity that exists, an `expected_rev` or `preview_digest` that no longer matches, an `operation_id` reused with different arguments |
| `VincRoleChanged` | a pinned role changed; no contract was returned and `details` holds its current `id`, `rev` and `hash`. A subclass of `VincInvalidArgument` |
| `VincQuotaExceeded` | the daily limit is spent; `retry_after` is in seconds |
| `VincUnavailable` | network or server failure, raised after transient retries ran out; for a write, check `details["write_may_have_applied"]` and `operation_status` |
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

This reads the role contract with `role`, reads each distinct required spec by its
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
the same policy. Every invocation still makes fresh requests; the client keeps
nothing between calls (the LangGraph middleware reuses one run's block, see its
README). Concurrency changes elapsed time, not required input size.

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

### Pin a role

A role contract carries `rev`, the role node's stored revision, and `hash`: the
sha256 of the canonical JSON (sorted keys, no whitespace, UTF-8) of its `id`,
`title`, `domain`, `props` and `works_with`. Those are the fields the prefetch
block gives the model. Read the role once, review it, and keep the hash with your
agent's configuration:

```python
pin = reader.role("concept:agent-reviewer-judgement")["hash"]
block = reader.prefetch(
    "concept:agent-reviewer-judgement", "Review the proposed change.",
    max_chars=120000, pin=pin,
)
```

With `pin`, the hub returns the contract only while its hash is still the pinned
one. When someone has changed the role (its props, title, topic or relations),
`role` and `prefetch` raise `VincRoleChanged` before any document is read, with
the role's current `id`, `rev` and `hash` in `details`; the run does not start.
Moving the pin is your decision: read the role without a pin, review what changed,
and pin the new hash. The client also refuses a contract whose `hash` is not the
pin. A pin that is not 64 lowercase hex characters raises `VincInvalidArgument`
before any request.

The pin covers the role contract, not the documents listed in its `reads`: their
text is read fresh on every call and checked against its own `content_hash`. Over
REST the same check is `GET /v1/roles/{id}?expected_hash=<hash>` (optionally
`expected_rev`), which answers 409 `role_changed`. A hub older than this feature
answers 400 to the argument, so a pinned run stops there too.

### Large roles

The hub never cuts a role contract. One that serializes to 10,000 bytes or more is
refused with `role_contract_too_large` (HTTP 400, `details` holding `bytes`,
`limit` and `compact`), because a cut contract could lose a permission. Most of a
large contract is usually the evidence sentence on each `works_with` edge, so a
compact form leaves only those sentences out: props, endpoints, edge types and
confidence stay, and `hash` is still the complete contract's hash, so one pin
covers both forms.

`role` (and so `prefetch` and both adapters) reads the compact form on its own when
the hub refuses the complete one, and the result says `compact: True`. Pass
`compact=True` to ask for it at once, or `compact=False` to get the error instead.
A role that is over the limit even in compact form raises the same error with
`details["compact"]` set to `True`; shorten its props. Over REST the compact read
is `GET /v1/roles/{id}?compact=1`, and from the command line `vinc role get <id>
--compact`.

### Compile a role's context under a policy

`compile_context(client, ContextRequest(role=..., task=..., read_tool=...))` builds the
same kind of block as `prefetch`, with a policy choosing what goes in. With no policy the
choice follows `read_tool`, the flag that says the model can read documents itself
(`vinc_doc_get`):

| `read_tool` | Default policy | Block |
|---|---|---|
| `False` | `FULL_TEXT` (`p0-full`) | `prefetch`'s block, byte for byte: every required reading in full |
| `True` | `TASK_EVIDENCE` (`task-evidence-1`) | the role contract, each required reading as a pointer, and the task's evidence: the nodes its briefs and searches agree on, one document's matched sections, a resolved brief and the top search rows |

`role_prefetch` and `arole_prefetch` take the same default, and `read_tool` may be a
function of the role contract (`doc_tool_allowed`), so a role that disallows the
document tool keeps `FULL_TEXT`. Pass `policy=FULL_TEXT` to keep the full readings even
when the model has the tool. `manifest["policy"]["id"]` records which policy ran.

`TASK_EVIDENCE` was chosen by a policy search over agent tasks on a pinned store, where it
answered more tasks than `FULL_TEXT` at about a third of the input tokens. The runs and
the comparison are recorded in the Vinc graph (VIN-790).

### A role's disallowed tools

`prefetch_with_contract` returns the block and the role contract from the same
reads, so a caller can apply the contract without reading the role again.
`RoleToolPolicy.from_contract(contract)` reads `props.disallowed_tools` (absent
means none; a value that is not a list of names raises `invalid_role_contract`).
Names match exactly: `allowed(tools)` drops tools with a disallowed name,
`refusal(name)` is the tool result a refused call gets, and `warn_unmatched(tools)`
logs one warning naming the entries that matched none of `tools`. Both framework
adapters use this one implementation in their role prefetch mode.

### A role's run policy

A role contract can also say what the role outputs, how it may act, how much one run
may spend and which capabilities it holds. All four keys are optional:

| Key | Values |
|---|---|
| `output_kind` | `prose`, `code`, `verdict`, `write_proposal` or `none` |
| `act_policy` | action id (lowercase snake_case, as in `person_only`) to `act`, `act_and_report`, `ask_first` or `never` |
| `budget` | `tool_calls`, `model_calls`, `tokens` (whole numbers) and `cost_usd`, `wall_seconds` (numbers), all 0 or more |
| `capabilities` | `allow`, `deny` and `approve` lists of dotted capability ids such as `vinc.read` or `github.pr.merge`; a final `.*` matches a family |

`RolePolicy.from_contract(contract)` reads them and raises `invalid_role_contract`
when one is malformed, before the model is called (the hub only warns when the role is
written). `act(action)` answers the contract's mode for an action; a `person_only`
action without its own entry is `ask_first`, and a contract that lets the role act
alone on a `person_only` action is refused. `capability(id)` answers `deny`, `approve`
or `allow` in that order of precedence, and `limit(key)` one budget value. Hosts and
adapters enforce the policy; the client only reads it and grants nothing. Capability
ids are not tool names: an adapter maps its tools to them.

On 2026-10-06, all 23 hosted roster roles exceeded the default. Their required
text was 19,764 to 198,480 characters (median about 112,000); successful prefetch
blocks at a larger budget were 34,640 to 217,070 characters including the role
contract, formatting and task evidence. These are characters, not tokens or a
model price estimate, from one hub and one task. Both adapters read the block once
per run (vinc-langgraph from 0.1.8). LangGraph adds it to the system message of every
model call in the run, including later tool-loop calls; the Microsoft Agent Framework
provider adds it to the run's instructions. A smaller budget stops the model instead of
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

## Deterministic role output gate

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

## Time and validity

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

## One role into an agent plan

`plan_agent(client, "reviewer")` reads a role once and pins its contract by hash. The returned `AgentPlan` carries what any framework needs to run the role: the `RoleToolPolicy`, the `OutputGate`, the actions only a person may take (`person_only`), the roles that follow it by live edges (`hands_to`, `receives_from`) and the budget. `aplan_agent` does the same with an `AsyncVincClient`. `finish_run` writes the run record on purpose and keeps a failed write on the result as `record_error` instead of raising. The two adapters call these for you in `build_agent`.

## Version notes

| Version | Change |
|---|---|
| 0.1.20 | The context compiler: `compile_context` and `acompile_context` build a role's block under a replaceable `ContextPolicy` and refuse a plan that breaks one of nine invariants (`context_plan_rejected`); `FULL_TEXT` gives `prefetch`'s block byte for byte; `replay_context` and `check_replay` rebuild a block from its recorded reads. The document read tool `vinc_doc_get`: `read_document`, `aread_document` and `doc_tool_allowed` in `vinc_client.doc_tool`. With no policy and a document read tool, the default is `TASK_EVIDENCE` (see [Compile a role's context under a policy](#compile-a-roles-context-under-a-policy)); without one, or when its block does not fit `max_chars`, it is `FULL_TEXT`. `prefetch` is unchanged. |
| 0.1.19 | `build_eval_card_fragment` keeps `ungraded` (runs the runner could not grade) and `skipped` (tasks it left out) on a result cell, and rejects a negative count. |
| 0.1.18 | Evaluation cards: `build_eval_card_fragment` and `record_eval_card` write a role's measured result as a Record that supersedes the older card for the same suite, `current_cards` keeps the live ones, and `eval_cards` reads `GET /v1/roles/{id}/eval-cards`. `finish_run` and `afinish_run` take `extra` (model family, step counts, `trace`) for the run record. |
| 0.1.17 | `plan_agent`, `aplan_agent`, `AgentPlan`, `AgentRun` and `finish_run`: one role read into the policy, gate, person-only actions, next roles and budget a framework agent needs. The adapters' `build_agent` (vinc-langgraph 0.1.13, vinc-agent-framework 0.1.12) require it. |
| 0.1.16 | Shims as Records: `build_shim_fragment`, `current_shims` and `shim_lines` for a host, and `read_runs` with `retirement_drafts` for a watcher that drafts the retirement of shims that stopped firing (see [Shims](#shims)). |
| 0.1.15 | `RolePolicy` reads a role's `output_kind`, `act_policy`, `budget` and `capabilities` (see [A role's run policy](#a-roles-run-policy)); a malformed key raises `invalid_role_contract`. |
| 0.1.14 | `role` takes `compact`. A role over the hub's read limit is no longer an error by default: `role`, `prefetch` and both adapters read it again without relation evidence sentences, and the result says `compact: True` (see [Large roles](#large-roles)). The adapters (vinc-langgraph 0.1.11, vinc-agent-framework 0.1.10) require it. |
| 0.1.13 | `record_run` writes one agent run as an Episode about its role, with run props and a `trace_ref` built by `trace_ref()` (sha256, size and file name of the raw trace file); the episode id comes from the run id, so retries merge. The adapters (vinc-langgraph 0.1.10, vinc-agent-framework 0.1.9) add `record_run`. |
| 0.1.12 | `role`, `prefetch` and `prefetch_with_contract` take `pin`, a role contract `hash`; a role that changed raises the new `VincRoleChanged` before any document is read. Contracts carry `rev` and `hash`. The adapters (vinc-langgraph 0.1.9, vinc-agent-framework 0.1.8) pass it as `prefetch_pin`. |
| 0.1.11 | `prefetch_with_contract` returns the prefetch block with the role contract it read, and `RoleToolPolicy` applies the contract's `disallowed_tools` by exact name. `prefetch` is unchanged. The adapters (vinc-langgraph 0.1.8, vinc-agent-framework 0.1.7) use both. |
| 0.1.10 | Transient hub errors (502, 503, 504, dropped connections, timeouts) are retried for up to `retry_for` seconds (90 by default), following `Retry-After`. Writes are retried only with an `operation_id`. `get_context` and `prefetch` do not retry. |
| 0.1.9 | `record_episode` and `write_package` take `create_only` and `operation_id`. New `delete`, `delete_edge`, `merge_nodes` and `doc_delete` with `dry_run` and `preview_digest`. New `edges` and `role_check`. The adapters 0.1.5 pass the two write parameters through and require this client. |
| 0.1.8 | `ingest` takes `create_only` and `operation_id`; `operation_status` reads a write's receipt; `expected_rev` and `props_patch` guard a node edit. |
| 0.1.7 | `brief` and `search` take `mode`, `as_of` and `known_at`, and results carry a `temporal` object. Prefetch gives a `cite as` id only for a readable mirror. The adapters 0.1.4 expose the same read parameters. |
| 0.1.6 | `get_context` blocks end with the same `Sources:` line instruction as prefetch. |
| 0.1.5 | The prefetch block names the citable id of each required reading and asks for a final `Sources:` line; `sources_line` reads it. Both adapters share one tool schema. The adapters 0.1.3 cap Vinc tool calls at 4 per run. |
| 0.1.4 | Optional prefetch reads start before validation. |
| 0.1.3 | Required prefetch reads run in concurrent batches; a budget error reports the complete required size. |

0.1.9 was not published to PyPI; its changes first shipped in 0.1.10.

## License

MIT
