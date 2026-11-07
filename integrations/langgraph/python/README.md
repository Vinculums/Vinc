# vinc-langgraph

[Vinc](https://vincs.io) for [LangGraph](https://langchain-ai.github.io/langgraph/) and LangChain agents. Vinc is a knowledge graph you share with AI: the decisions, records and documents your team wrote, with the reasons attached. This package brings the relevant part of it into your graph nodes and model calls.

**If the agent has to follow its role's contract (what it must never do, its gates, the tools it may not use), use role mode (`prefetch_role=`).** Role mode stops the run when Vinc cannot be read; context mode goes on without context. See [Role mode or context mode](#role-mode-or-context-mode).

## Install

```bash
pip install "vinc-langgraph>=0.1.15"           # node helpers and tools
pip install "vinc-langgraph[agents]>=0.1.15"   # plus the middleware for create_agent
```

This README describes vinc-langgraph 0.1.15, which requires vinc-client 0.1.20 or later.

For a new install, prefer `pip install "vincs[langgraph]"`: it has this package as `vinc.integrations.langgraph`, with the same names, and includes LangChain for the middleware. `vinc-langgraph` keeps publishing under this name for existing installs.

Set `VINC_API_KEY` to a member key. A `vinc_ro_` key is enough for every read helper, the middleware and the tools. `record_episode` and `write_package` need a `vinc_sk_` key; with a read only key they raise `VincAuthError` (`read_only_key`) before anything is sent. Give the write key only to the node that writes.

## Role mode or context mode

`VincContextMiddleware` runs in role mode when you pass `prefetch_role`, and in context mode when you do not. The node helpers fail the same way: `prefetch` raises like role mode (it applies no tool rule) and `get_vinc_context` returns `None` like context mode. Both read on every call.

| Mode | When Vinc is unreachable or over the daily limit | Reads per run (hub calls) | Tools the role disallows | Pinning |
|---|---|---|---|---|
| Role mode (`prefetch_role=`) | Raises with no retry, so the model is not called (fails closed). Any failed read stops the run the same way: the role, a required document or the task lookup. | Once per run, on its first model call, reused by every later model call until a new human message: 1 for the role, 2 per required document (its text and its mirror node), 1 brief and 1 search. | Removed from the tools each model call sees; a call to one gets a tool message saying the role does not allow it. Names match exactly, and entries that match none of the agent's tools give one warning per run. | With `prefetch_pin` (a hash from `VincClient.role`), a role that changed raises `VincRoleChanged` and the model is not called (fails closed). Without it, each run reads the role as it is when the run starts. Either way a run in progress keeps its contract. |
| Context mode (no role) | Logs a warning and the model call goes on without context (fails open). A wrong key or space still raises. | One brief, plus a search when the brief ties or finds nothing, per question, kept for `cache_seconds` (60). An empty result from an outage is kept the same way. | Not applied: there is no role. | Not applicable: each lookup reads the graph as it is. |

The per-run read and the tool rule arrived on PyPI in 0.1.8; 0.1.6 read the role before every model call and did not apply `disallowed_tools`. The read tools from `create_vinc_tools` add up to `tool_calls_per_run` (4) calls per run in either mode.

## Helpers

Every helper takes the client first. The `a` prefixed twins take an `AsyncVincClient` and are awaited.

| Helper | Parameters | What it does |
|---|---|---|
| `get_vinc_context`, `aget_vinc_context` | `client`, `messages`, `search_fallback=True`, `max_chars`, `max_excerpts=3` | a fenced block for the newest human message, or `None` |
| `prefetch`, `aprefetch` | `client`, `role_id`, `task`, `space`, `max_chars`, `pin` | the role's required reading and task evidence |
| `get_vinc_package_context`, `aget_vinc_package_context` | `client`, `package_id`, `space` (required), `max_chars` | a handoff package, rechecked with this key |
| `build_system_message` | `block`, `base` | a `SystemMessage` with the block added |
| `last_user_text` | `messages` | the newest human message text |
| `create_vinc_tools` | `client`, `async_client`, `max_chars` | the read only tools `vinc_brief` and `vinc_search` |
| `record_episode`, `arecord_episode` | `client`, `title`, `summary`, `about`, `date`, `domain`, `tags`, `space`, `episode_id`, `create_only`, `operation_id` | one Episode, `vinc_sk_` key |
| `write_package`, `awrite_package` | `client`, `question`, `conclusion`, `claims`, `produced_by`, `run_id`, `space` (required), `package_id`, `create_only`, `operation_id` | one handoff Record, `vinc_sk_` key |
| `VincContextMiddleware` | `client`, `async_client`, `api_key`, `base_url`, `space`, `search_fallback=True`, `max_chars`, `max_excerpts=3`, `cache_seconds=60`, `prefetch_role`, `prefetch_max_chars`, `tool_calls_per_run=4`, `prefetch_pin` | context for each `create_agent` model call (`agents` extra) |
| `PackageProvider` | `package_id`, `space` (required), `client`, `async_client`, `api_key`, `base_url`, `max_chars` | a handoff package for each model call (`agents` extra) |
| `run_review`, `arun_review` | `model`, `messages`, `native_structured=True`, `config`, `options` | a `ReviewOutput` with one parse retry |
| `create_output_gate_middleware` | `gate`, `evidence` | checks the final response of a `create_agent` agent |
| `evaluate_output_node`, `require_output_pass` | `state`, `gate`, `evidence` | the same check as a graph node |
| `create_person_only_node` | `gate`, `role_id`, `run_id`, `evidence` | an approval node with a native `interrupt` |

## In a graph node

```python
from vinc_client import VincClient
from vinc_langgraph import build_system_message, get_vinc_context

vinc = VincClient()

def review(state):
    block = get_vinc_context(vinc, state["messages"])   # newest human message
    system = build_system_message(block, "You review design changes.")
    return {"messages": [llm.invoke([system, *state["messages"]])]}
```

`get_vinc_context` makes one call, two when nothing matched or several nodes tied (it then widens with a search). It returns `None` when nothing matched, and also when Vinc is unreachable or the daily limit is spent, so the node goes on without it. A wrong key or space raises.

Ordinary `VincContextMiddleware` lookups cache for 60 seconds by default, isolated
by client, reader, endpoint, space and rendering settings. Call `clear_cache()`
after a source update, or set `cache_seconds=0` for a fresh read at each model
call. This does not bypass the server's entitlement caches.

## In a `create_agent` agent

```python
from langchain.agents import create_agent
from vinc_langgraph import VincContextMiddleware

agent = create_agent("openai:gpt-5", middleware=[VincContextMiddleware()])
```

The middleware adds the block to the system message of each model call. In context mode (no `prefetch_role`), an agent that uses tools calls the model several times per question, so the answer is kept for 60 seconds (`cache_seconds`) instead of spending a Vinc call each time.

## Tools the model can call

```python
from vinc_langgraph import create_vinc_tools

tools = create_vinc_tools(VincClient())   # vinc_brief and vinc_search, read only
```

Inside a `create_agent` that has `VincContextMiddleware`, the model may call these two tools at most 4 times per run (`tool_calls_per_run`; a run is everything after the newest human message). The fifth call gets a sentence saying the cap is reached instead of a Vinc call. The count comes from the graph state, so concurrent runs on one agent do not share it. A graph that uses the tools without the middleware is not capped. `vinc-agent-framework` applies the same cap and sentence.

## Recording, after a person approved

Nothing in this package writes on its own. Put `record_episode` in the node after an `interrupt()`, and give only that node a `vinc_sk_` key. `examples/approve_then_record.py` is a complete graph:

```
context -> review -> approve (interrupt) -> record
```

The example accepts a host-provided checkpointer for durable recovery; its
default `InMemorySaver` lasts only for that process. Persist the episode id and
date before approval, and pass the same `episode_id` to `record_episode` on a
retry. This merges the same Episode; it does not make other side effects run
exactly once.

## Retrying a write and previewing a delete

Since 0.1.5 the write helpers take `create_only` and `operation_id` and pass them to vinc-client unchanged.

- Before the approval boundary, store a fresh lowercase UUID next to the episode id in host state. Pass it as `operation_id` on the first attempt and on every retry of that write. An identical retry returns the stored result without applying again; changed arguments under the same id conflict.
- After an uncertain response (`VincUnavailable`), read `client.operation_status(operation_id)` before deciding. `accepted` and `unknown` do not prove the write did not happen; `committed` means the graph transaction committed. Receipts are owner scoped and a read only key can read them.
- `create_only=True` refuses an id that already exists, before any change. Retry such a write with the same `operation_id`, or the retry is refused because the first attempt created the node.
- Nothing retries on its own.

Delete and merge are not wrapped by this package. Call the client's `delete`, `delete_edge`, `merge_nodes` or `doc_delete` with `dry_run=True` first; it changes nothing and returns the preview with a `preview_digest`. Show the preview to a person, then apply with `preview_digest=` (and an `operation_id`); if the affected state changed in between, the apply is refused. Both calls need a `vinc_sk_` key. The vinc-client README has the details.

## Structured review with one parse retry

```python
from vinc_langgraph import run_review, arun_review, UnparsedReview

try:
    review = run_review(model, "Review this draft against its cited rules")
except UnparsedReview:
    # Stop the revision path and escalate to a person.
    raise
```

Use these helpers inside a review node. They invoke a standalone model with
`with_structured_output(ReviewOutput, include_raw=True)`; no tool executor or
graph writer runs. `arun_review` provides the same async behavior. For a provider
without structured output support, select `native_structured=False` explicitly.
The helper retries one malformed output with sanitized feedback and then raises
`UNPARSED`, preserving the difference between an unparsed review and no findings.
Service and unsupported-schema errors propagate; `config` is passed unchanged.
Pass provider generation parameters through `options`, for example
`options={"max_completion_tokens": 2048, "reasoning_effort": "low"}`. They are
forwarded unchanged on both attempts, without adding `max_tokens` or
`temperature`. The helper owns `tools`, `tool_choice` and `response_format`;
these keys cannot override its structured review schema.
Parsed citations still need permission/existence checks, and person-only
requests still require approval before any action.

## Receive a handoff package

For an id held in graph state, use a node helper:

```python
from vinc_langgraph import get_vinc_package_context, build_system_message

def receive(state):
    block = get_vinc_package_context(reader, state["package_id"], space=state["space"])
    return {"messages": [llm.invoke([
        build_system_message(block, "Review the prior conclusion and cite available evidence."),
        *state["messages"],
    ])]}
```

`aget_vinc_package_context` is the async twin. For an agent bound to one handoff,
the `agents` extra also provides middleware:

```python
from vinc_langgraph import PackageProvider

receiver = create_agent(model, middleware=[PackageProvider(
    "record:dashboard-handoff", space="personal", client=reader,
)])
```

This is a same-space Record read under the recipient's current key. Unlike the
question lookup middleware, packages and permission checks are never cached.
Every model call rereads the package and distinct evidence ids. Missing carriers
fail explicitly; unavailable evidence is marked uncitable. Nothing writes on
read or completion. Write the producer's package with `client.write_package`
explicitly, and put `record_episode` in the approved write-back node as before.

## Prefetch the selected role's evidence

```python
middleware = VincContextMiddleware(
    client=reader, prefetch_role="concept:agent-reviewer-judgement",
    prefetch_max_chars=120000,
)
```

This reads the complete role contract, full required documents with verified
content hashes, and task brief/search once per run, on the run's first model
call, and adds that block to every model call of the run. A run is everything
after the newest human message (its id, or its text when it has none), so a new
human message reads again and a run in progress finishes on the contract it
started with, as in `vinc-agent-framework`. `cache_seconds` does not apply to this
mode; `clear_cache()` drops the kept blocks. A failed read is never kept: its error
propagates and the next model call reads again. Kept blocks are separated by client,
reader, endpoint, space, role, `prefetch_max_chars` and `prefetch_pin`, at most 64 at a time.
Read tools may still be passed to `create_agent` as an additional lookup path.

The role contract's `disallowed_tools` apply to the run, by exact tool name. The
middleware removes those tools from each model call's tool list, answers a call
to one of them with a tool message saying the role does not allow it (the tool
does not run), and logs one warning per run naming the entries that matched none
of the agent's tools. The contract comes from the prefetch read, so this adds no
hub call. A value that is not a list of names raises `invalid_role_contract`
before the model runs. Roster contracts name Claude Code tools (`Bash`, `Edit`,
`Write`, `NotebookEdit`, `Agent`); a LangGraph tool is affected only when its name
is the same.

The contract's run policy applies too (see the vinc-client README, A role's run
policy). Capability ids are not tool names. With no `capability_map`, the
vinc-client default maps bind the tools of common servers (GitHub, Linear, Slack,
the filesystem MCP server, and shell and file tools) to capability ids. For your
own tool names, pass `capability_map`, a mapping from tool names to capability ids
(`{"merge_pr": "github.pr.merge"}`), or `{}` to turn the defaults off. A tool
whose capability the role denies is removed like a disallowed one, a call to one
it holds for approval gets a result asking for a person (the tool does not run),
and when the role has an `allow` list a mapped tool that matches none of it is
removed. Tools the map does not know are untouched. When none of the agent's tools
is mapped, a role that sets capabilities logs one warning per run saying they are
not enforced. If your host registered a server under another name (`gh` for
GitHub), name it in the role's `tool_servers` or in `VINC_TOOL_SERVERS` (see the
vinc-client README, A role's run policy).
`budget.tool_calls` caps all tool calls of a run; later calls get a result saying
so. A malformed policy raises `invalid_role_contract` before the model runs.

To hold the agent to a contract you reviewed, pin it. Read the role once, keep
its `hash`, and pass it as `prefetch_pin`:

```python
pin = reader.role("concept:agent-reviewer-judgement")["hash"]
middleware = VincContextMiddleware(
    client=reader, prefetch_role="concept:agent-reviewer-judgement",
    prefetch_max_chars=120000, prefetch_pin=pin,
)
```

When the role has changed since (its props, title, topic or relations), the run's
first model call raises `VincRoleChanged` with the current `rev` and `hash` in
`details`, and the model is not called. The failure is not kept, so every new run
asks again. Moving the pin is your decision: review the new contract and set
`prefetch_pin` to its hash. The pin covers the role contract, not the text of the
documents it lists. `prefetch_pin` without `prefetch_role` raises `ValueError`.
The node helpers take the same value as `pin`.

Missing, partial or unverified required documents raise `prefetch_spec_unresolved`
and no optional task lookup result is used. Authentication/service failures raise; nothing
writes or delegates work.

Graph nodes can call `prefetch(reader, role_id, task)` or async `aprefetch` and
pass its returned block to `build_system_message`. The chosen role comes from
application/operator configuration. `prefetch_max_chars` controls the combined
middleware budget (default 24,000 characters). Large roles need an explicit
larger budget, as above. Exceeding it raises `prefetch_budget_exceeded` instead of
truncating the contract or required documents. The helpers use `max_chars` for
the same budget. With client 0.1.3 or later, the propagated exception's
message and `details["required_size"]` give the complete required context size.
Set `prefetch_max_chars=error.details["recommended_max_chars"]` after accepting
that input cost to leave up to 6,000 characters for task evidence too. The client
reads all required documents before reporting budget overflow and discards optional
task lookup results on that failure; it never raises the limit or retries automatically.
The size can change when graph content changes. All 23 hosted roles exceeded the
default in the 2026-10-06 measurement; successful blocks were 34,640 to 217,070
characters (characters are not tokens). With client 0.1.3, required reads
run in bounded concurrent batches. With client 0.1.4, task brief/search may start
before validation and are discarded if the integrity or budget checks fail. A failed batch stops further batches;
already launched reads are cancelled when possible and drained before return.
Apart from the middleware's reuse within one run, nothing is cached. To keep sequential reads, pass a
reader created with `prefetch_concurrency=1`; the client default is 8.
Model citation quality must be evaluated separately.

## Final output gate

```python
from vinc_client import OutputGate
from vinc_langgraph import create_output_gate_middleware, evaluate_output_node

gate = OutputGate(fresh_role["props"])
middleware = create_output_gate_middleware(gate, evidence=host_evidence_for_this_run)
# Pass middleware=[middleware] to create_agent; use invoke()/ainvoke().
```

Intermediate AI tool-call messages continue to the tool loop. The final response
is checked outside the model. A block raises `OutputGateStopped`. An interrupt
replaces the final message with the same answer plus the pending person-only
decisions under `PENDING_HEADER`, because the person reading it decides; pass
`on_interrupt="stop"` to raise instead.
This does not authorize or undo tool execution. Do not expose unchecked model
stream events to an end user.

For an explicit StateGraph, place `evaluate_output_node(state, gate,
evidence=host_evidence)` after the model node, declare `vinc_output_gate` in the
state schema and route its verdict to delivery, stopping, or a separate approval
node. The helper returns decisions and bounded evidence, not the original answer.
`require_output_pass` is available when the graph should raise instead of routing.
State-provided proof dictionaries are ignored. Neither path calls another model
or automatically stores an episode.

## Approval after the model

Have the model node store a `vinc_client.RoleOutput` dictionary in `role_output`.
Declare `vinc_approval` in your StateGraph state schema and add a separate node:

```python
from vinc_langgraph import create_person_only_node

approval = create_person_only_node(gate, role_id=fresh_role["id"], run_id=run_id)
graph.add_node("approval", approval)
graph.add_edge("model", "approval")
```

Compile with a checkpointer and use the same thread ID when resuming. The node
emits a draft-bound native `interrupt` when a structured request or gate identifies
a person-only action. An authenticated human response resumes with
`Command(resume={"request_id": pending["request_id"], "approved": True})`.
The completed model node is not repeated. Deliver only the checked
`vinc_approval` result; rejected output has no deliverable. Blocking checks cannot
be overridden by approval. Keep side-effect tools separately authorized.

Use a durable checkpointer in production and never expose unchecked model stream
events. This follows the framework's [interrupt contract](https://docs.langchain.com/oss/python/langgraph/interrupts).

## Build an agent from a role

Needs the `agents` extra. One call reads the role, pins its contract by hash and returns a running agent:

```python
from vinc_langgraph import build_agent

agent = build_agent("reviewer", model, tools=[my_tool])      # a sync VincClient is made from the key
run = agent.invoke("Review the policy change.")
run.status      # "done", "blocked" or "waiting" (a person decides)
run.output      # the checked answer, or None when the gate stopped it
run.record      # the run record written at the end
```

The agent reads the role's required documents before each run, hides the tools the role disallows, refuses calls it holds for a person, caps tool calls at its budget and checks the final answer with the role's gates. `agent.plan` lists the person-only actions and the roles that follow this one (`hands_to`); chaining roles is yours. A person-only action ends the run as `waiting`; approving it is a separate step (`create_person_only_node`) and never executes a tool. `abuild_agent` is the async form. A failed run record write is kept as `run.record_error` and never raised. If the role changed since it was read, the next run raises `VincRoleChanged`.

## Version notes

| Version | Change |
|---|---|
| 0.1.15 | `context_policy` on the middleware builds the role's block with the vinc-client context compiler instead of `prefetch`; `create_vinc_tools(documents=True)` adds `vinc_doc_get`, and with it (when the role allows it) a policy may show required readings as pointers. With no `context_policy`, an agent whose tools include `vinc_doc_get` gets its block from the compiler's default (the task evidence policy when the role allows the tool). With no `capability_map`, the role's capabilities apply through the vinc-client default maps, and `tool_servers` names the host's servers. Requires vinc-client 0.1.20. |
| 0.1.14 | `prefetch_max_chars` on `build_agent` and `abuild_agent`, for a role whose required reading is longer than the 24,000 characters each call has room for. Requires vinc-client 0.1.17. |
| 0.1.13 | `build_agent`, `abuild_agent` and `VincAgent`: a running agent from a role address, with the role's prefetch, tool policy, budget, gates and run record. Requires vinc-client 0.1.17. |
| 0.1.12 | With `prefetch_role`, the contract's run policy applies: `capability_map` binds tools to capability ids so denied ones are removed and approval ones refused with a request for a person, and `budget.tool_calls` caps a run's tool calls. Requires vinc-client 0.1.15. |
| 0.1.11 | A role over the hub's read limit loads: the middleware and `prefetch` read it without relation evidence sentences instead of failing (see the vinc-client README, Large roles). Requires vinc-client 0.1.14. |
| 0.1.10 | `record_run` and `arecord_run` write one agent run as an Episode about its role, with its raw trace by hash (see the vinc-client README, Record a run). Requires vinc-client 0.1.13. |
| 0.1.9 | `prefetch_pin` on the middleware and `pin` on the node helpers hold role prefetch to a contract hash; a role that changed raises `VincRoleChanged` before the model is called. Requires vinc-client 0.1.12. |
| 0.1.8 | With `prefetch_role`, the contract's `disallowed_tools` are removed from what the model sees and a call to one gets a refusal message; entries that match no tool are warned once per run. Requires vinc-client 0.1.11. |
| 0.1.7 | With `prefetch_role`, the middleware reads the role once per run instead of before every model call; a new human message reads again. `clear_cache()` also drops these blocks. |
| 0.1.6 | `vinc_brief` and `vinc_search` answer with the unavailable sentence at once instead of waiting through the client's transient retries. Requires vinc-client 0.1.10. |
| 0.1.5 | The write helpers take `create_only` and `operation_id`. Requires vinc-client 0.1.9; its version notes list `operation_status`, `edges`, `role_check` and the delete and merge previews. |
| 0.1.4 | The read tools take `mode`, `as_of` and `known_at`. |
| 0.1.3 | Vinc tool calls are capped at 4 per run, and both packages send the same tool schema. |

0.1.5 and 0.1.7 were not published to PyPI; their changes first shipped in 0.1.6 and 0.1.8.

## License

Apache-2.0 (see `LICENSE`). Releases published earlier under the MIT License stay under it.
