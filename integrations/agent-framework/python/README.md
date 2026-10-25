# vinc-agent-framework

[Vinc](https://vincs.io) for [Microsoft Agent Framework](https://learn.microsoft.com/agent-framework/). Vinc is a knowledge graph you share with AI: the decisions, records and documents your team wrote, with the reasons attached. This package brings the relevant part of it into each agent run.

**If the agent has to follow its role's contract (what it must never do, its gates, the tools it may not use), use role mode (`prefetch_role=`).** Role mode stops the run when Vinc cannot be read; context mode goes on without context. See [Role mode or context mode](#role-mode-or-context-mode).

## Install

```bash
pip install "vinc-agent-framework>=0.1.9"
```

This README describes vinc-agent-framework 0.1.9, which requires vinc-client 0.1.13 or later.

For a new install, prefer `pip install "vincs[agent-framework]"`: it has this package as `vinc.integrations.agent_framework`, with the same names. `vinc-agent-framework` keeps publishing under this name for existing installs.

Set `VINC_API_KEY` to a member key. A `vinc_ro_` key is enough for the providers, the tools and `prefetch`. `record_episode` and `write_package` need a `vinc_sk_` key; with a read only key they raise `VincAuthError` (`read_only_key`) before anything is sent. Give the write key only to the step that writes.

## Role mode or context mode

`VincContextProvider` runs in role mode when you pass `prefetch_role`, and in context mode when you do not. Both read in `before_run`, once per run.

| Mode | When Vinc is unreachable or over the daily limit | Reads per run (hub calls) | Tools the role disallows | Pinning |
|---|---|---|---|---|
| Role mode (`prefetch_role=`) | `before_run` raises with no retry, so the model is not called (fails closed). Any failed read stops the run the same way: the role, a required document or the task lookup. | 1 for the role, 2 per required document (its text and its mirror node), 1 brief and 1 search. | Removed from the tools each model call sees; a call to one gets a result saying the role does not allow it. Names match exactly, and entries that match none of the agent's tools give one warning per run. Hiding needs a chat client with `ChatMiddlewareLayer` inside `FunctionInvocationLayer`, as the built-in clients have; with any other client the model still sees the tools, calls are still refused, and each run logs a warning. | With `prefetch_pin` (a hash from `AsyncVincClient.role`), a role that changed makes `before_run` raise `VincRoleChanged` and the model is not called (fails closed). Without it, each run reads the role as it is when the run starts. Either way a run keeps its contract to its end. |
| Context mode (no role) | Logs a warning and the run goes on without context (fails open). A wrong key or space still raises. | One brief, plus a search when the brief ties or finds nothing. None for a message shorter than `min_query_chars`. | Not applied: there is no role. | Not applicable: each run reads the graph as it is. |

The tool rule is new in 0.1.7; 0.1.6 did not apply `disallowed_tools`. With `expose_tools=True` the model's own lookups add up to `tool_calls_per_run` (4) calls per run in either mode.

## Helpers

The functions take an `AsyncVincClient` first and are awaited.

| Helper | Parameters | What it does |
|---|---|---|
| `VincContextProvider` | `client`, `api_key`, `base_url`, `space`, `source_id="vinc"`, `search_fallback=True`, `max_chars`, `max_excerpts=3`, `min_query_chars=3`, `expose_tools=False`, `prefetch_role`, `prefetch_max_chars`, `tool_calls_per_run=4`, `prefetch_pin` | context before each run, and optionally the read tools; `aclose()` closes the client it made itself |
| `PackageProvider` | `client`, `package_id`, `space` (required), `source_id="vinc-package"`, `max_chars` | a handoff package before each run |
| `create_vinc_tools` | `client`, `max_chars`, `cap=None` (a `ToolCallCap`) | the read only tools `vinc_brief` and `vinc_search` |
| `prefetch` | `client`, `role_id`, `task`, `space`, `max_chars`, `pin` | the role's required reading and task evidence |
| `record_episode` | `client`, `title`, `summary`, `about`, `date`, `domain`, `tags`, `space`, `episode_id`, `create_only`, `operation_id` | one Episode, `vinc_sk_` key |
| `write_package` | `client`, `question`, `conclusion`, `claims`, `produced_by`, `run_id`, `space` (required), `package_id`, `create_only`, `operation_id` | one handoff Record, `vinc_sk_` key |
| `run_review` | `agent`, `messages`, `native_structured=True`, `options` | a `ReviewOutput` with one parse retry |
| `VincOutputGateMiddleware` | `gate`, `evidence` | checks the final response of a non streaming run |
| `VincPersonOnlyExecutor` | `gate`, `role_id`, `run_id`, `evidence`, `id` | an approval executor with a native `request_info` event |

## Add context to every run

```python
from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient
from vinc_agent_framework import VincContextProvider

vinc = VincContextProvider()              # reads VINC_API_KEY; use a vinc_ro_ key
agent = Agent(
    client=OpenAIChatClient(),
    instructions="You review design changes.",
    context_providers=[vinc],
)
await agent.run("Why are our colour tokens stored as OKLCH?")
```

Before each run the provider looks up the newest user message in Vinc and, when something matches, adds a block to the instructions. The block is marked as data, not instructions, and it cites node ids the model can quote back.

- One call per run; two when the lookup found nothing or several nodes tied, because it then widens with a search (`search_fallback=False` turns that off).
- If Vinc is unreachable or the daily limit is spent, the run goes on without the block (role mode stops instead). A wrong key or space raises.
- For a team's graph pass `space="<team id>"`.

## Let the model look things up

```python
vinc = VincContextProvider(expose_tools=True)
```

adds two read-only tools, `vinc_brief` and `vinc_search`, and lets the model call them at most 4 times per run (`tool_calls_per_run`); the fifth call gets a sentence saying the cap is reached instead of a Vinc call. The tools are built per run, so concurrent runs do not share the count. You can also build them yourself with `create_vinc_tools(client)`; those count only against a `ToolCallCap` you pass, for the life of that cap. Import it with `from vinc_client.tools import ToolCallCap`; `ToolCallCap(limit=4)` is the default count, `limit=None` never caps, and a new run needs a new cap. `vinc-langgraph` applies the same cap and sentence.

## Writing, on purpose

The provider never stores the conversation: `after_run` does nothing. Record what happened when a person approved it, with a `vinc_sk_` key:

```python
from vinc_client import AsyncVincClient
from vinc_agent_framework import record_episode

writer = AsyncVincClient(api_key=WRITE_KEY)
await record_episode(
    writer,
    "Fixed contrast on the dark secondary button",
    summary="text-secondary now passes 4.5:1 on the dark surface.",
    about=["decision:tokens-are-oklch"],
)
```

## Retrying a write and previewing a delete

Since 0.1.5 the write helpers take `create_only` and `operation_id` and pass them to vinc-client unchanged.

- Before the approval boundary, store a fresh lowercase UUID next to the episode id in host state. Pass it as `operation_id` on the first attempt and on every retry of that write. An identical retry returns the stored result without applying again; changed arguments under the same id conflict.
- After an uncertain response (`VincUnavailable`), read `client.operation_status(operation_id)` before deciding. `accepted` and `unknown` do not prove the write did not happen; `committed` means the graph transaction committed. Receipts are owner scoped and a read only key can read them.
- `create_only=True` refuses an id that already exists, before any change. Retry such a write with the same `operation_id`, or the retry is refused because the first attempt created the node.
- Nothing retries on its own.

Delete and merge are not wrapped by this package. Call the client's `delete`, `delete_edge`, `merge_nodes` or `doc_delete` with `dry_run=True` first; it changes nothing and returns the preview with a `preview_digest`. Show the preview to a person, then apply with `preview_digest=` (and an `operation_id`); if the affected state changed in between, the apply is refused. Both calls need a `vinc_sk_` key. The vinc-client README has the details.

## Structured review with one parse retry

```python
from vinc_agent_framework import run_review, UnparsedReview

try:
    review = await run_review(reviewer_agent, "Review this draft against its cited rules")
except UnparsedReview:
    # Stop the revision path and escalate to a person.
    raise
```

The helper uses the framework's Pydantic `response_format`. A client without
structured output support must use `native_structured=False` explicitly.
It retains findings and citation IDs, rejects contradictory verdicts, retries
once on a parse failure, and then raises `UNPARSED`. Network, credential and
unsupported-provider errors propagate without parse retries. Each attempt is
stateless and disables executable tools. Use a reviewer agent with read-only
middleware and context providers. No graph write or approval happens here.
Caller options such as reasoning settings are retained; the helper owns the
review format and `tool_choice="none"`.

## Receive a handoff package

```python
from vinc_client import AsyncVincClient
from vinc_agent_framework import PackageProvider

reader = AsyncVincClient(api_key=READ_KEY)
receiver = Agent(
    chat_client,
    instructions="Review the prior conclusion and cite available evidence.",
    context_providers=[PackageProvider(reader, "record:dashboard-handoff", space="personal")],
)
```

`PackageProvider` reads that same-space Record before every run, using the
recipient's current key to recheck evidence. Bind a different provider for a
different handoff; shared provider objects do not hold per-session package ids.
Missing packages fail explicitly. `after_run` writes nothing. The producer uses
`AsyncVincClient.write_package` explicitly; the recipient calls `record_episode`
explicitly after review to write back what happened. Neither the provider nor
the helpers delegate to another agent or grant it permission.

## Prefetch the selected role's evidence

```python
provider = VincContextProvider(
    reader, prefetch_role="concept:agent-reviewer-judgement", expose_tools=True,
    prefetch_max_chars=120000,
)
```

Before every run this reads the chosen role's complete contract, its required
documents in full with verified content hashes, and the task's brief/search
evidence, with returned citation ids.
The optional read tools remain available for further questions; the model does
not have to decide to call them to receive the initial evidence. Required spec
misses, partial or unverified documents raise `prefetch_spec_unresolved` before
optional task lookup. Unavailable roles and authentication/service errors stop
this prefetch mode explicitly. Ordinary question-only lookup keeps its existing
behavior.

The role contract's `disallowed_tools` apply to the run, by exact tool name. A
context provider cannot take the agent's own tools out of the run directly, so
`before_run` adds two middleware for that run only: a chat middleware that
removes those tools from the tool list of each model call, and a function
middleware that answers a call to one of them with a result saying the role does
not allow it (the tool does not run). The first model call logs one warning
naming the entries that matched none of the agent's tools. The contract comes
from the prefetch read, so this adds no hub call; a value that is not a list of
names raises `invalid_role_contract` before the model runs.
Hiding needs a chat client that runs chat middleware inside its tool loop
(`FunctionInvocationLayer` outside `ChatMiddlewareLayer`, the documented order of
the built-in clients). A client without `ChatMiddlewareLayer` still refuses the
calls, but the model sees the tools, and each run logs a warning saying so.
Roster contracts name Claude Code tools (`Bash`, `Edit`, `Write`,
`NotebookEdit`, `Agent`); an Agent Framework tool is affected only when its name
is the same.

To hold the agent to a contract you reviewed, pin it. Read the role once, keep
its `hash`, and pass it as `prefetch_pin`:

```python
pin = (await reader.role("concept:agent-reviewer-judgement"))["hash"]
provider = VincContextProvider(
    reader, prefetch_role="concept:agent-reviewer-judgement",
    prefetch_max_chars=120000, prefetch_pin=pin,
)
```

When the role has changed since (its props, title, topic or relations),
`before_run` raises `VincRoleChanged` with the current `rev` and `hash` in
`details`, and the model is not called. Moving the pin is your decision: review
the new contract and set `prefetch_pin` to its hash. The pin covers the role
contract, not the text of the documents it lists. `prefetch_pin` without
`prefetch_role` raises `ValueError`.

For a manual pipeline, `await prefetch(reader, role_id, task)` returns the same
block, and takes the same value as `pin`. Both paths are read only and use no permission cache. `prefetch_max_chars`
sets the combined provider budget (default 24,000 characters). Large roles need
an explicit larger budget, as above; `prefetch_budget_exceeded` stops instead of
truncating the contract or a required document. The helper uses `max_chars` for
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
No prefetch cache or automatic reuse is added. To keep sequential reads, pass a
reader created with `prefetch_concurrency=1`; the client default is 8. A new role must be chosen by the application/operator, never by
text found in the graph.

## Final output gate

```python
from vinc_client import OutputGate
from vinc_agent_framework import VincOutputGateMiddleware

gate = OutputGate(fresh_role["props"])
middleware = VincOutputGateMiddleware(gate, evidence=host_evidence_for_this_run)
# Pass middleware=[middleware] to Agent and use non-streaming run().
```

The middleware checks the final response outside the model and raises
`OutputGateStopped` on block or pending person approval. Configure it outermost
if other middleware changes responses. Streaming is rejected before the agent
runs, so unchecked tokens cannot leave through this middleware. A host callback
supplies actual execution evidence; structured response person-only requests are
model data and match adopted action IDs. This is an output barrier, not tool
authorization. It neither calls another model nor writes a graph record.

## Approval after the model

Have your model executor emit a `vinc_client.RoleOutput`, using
`agent.run(..., tools=[], options={"response_format": RoleOutput,
"tool_choice": "none"})`. Connect a separate approval executor downstream:

```python
from agent_framework import WorkflowBuilder
from vinc_agent_framework import VincPersonOnlyExecutor

approval = VincPersonOnlyExecutor(gate, role_id=fresh_role["id"], run_id=run_id)
workflow = WorkflowBuilder(start_executor=model_executor).add_edge(model_executor, approval).build()
```

Structured person-only requests and gate-detected actions emit the native
`request_info` event. The authenticated host constructs
`ApprovalDecision(request_id=event.data.request_id, approved=True)` from the human
response, then calls `workflow.run(responses={event.request_id: decision})`.
The response handler uses the saved draft and does not call the model again.
Rejection yields no deliverable; blocking checks never become approvable. Approval
releases this output only and does not execute or authorize a side-effect tool.

Never expose unchecked model stream events. Configure workflow checkpoints when
pending approval must survive a process restart. This uses the framework's
[native request and response mechanism](https://learn.microsoft.com/en-us/agent-framework/workflows/human-in-the-loop).

## Version notes

| Version | Change |
|---|---|
| 0.1.9 | `record_run` writes one agent run as an Episode about its role, with its raw trace by hash (see the vinc-client README, Record a run). Requires vinc-client 0.1.13. |
| 0.1.8 | `prefetch_pin` on the provider and `pin` on `prefetch` hold role prefetch to a contract hash; a role that changed makes `before_run` raise `VincRoleChanged` before the model is called. Requires vinc-client 0.1.12. |
| 0.1.7 | With `prefetch_role`, the contract's `disallowed_tools` are removed from what the model sees (chat clients with `ChatMiddlewareLayer`) and a call to one gets a refusal result; entries that match no tool are warned once per run. Requires vinc-client 0.1.11. |
| 0.1.6 | `vinc_brief` and `vinc_search` answer with the unavailable sentence at once instead of waiting through the client's transient retries. Requires vinc-client 0.1.10. |
| 0.1.5 | The write helpers take `create_only` and `operation_id`. Requires vinc-client 0.1.9; its version notes list `operation_status`, `edges`, `role_check` and the delete and merge previews. |
| 0.1.4 | The read tools take `mode`, `as_of` and `known_at`. |
| 0.1.3 | Vinc tool calls are capped at 4 per run, and both packages send the same tool schema. |

0.1.5 was not published to PyPI; its changes first shipped in 0.1.6.

## License

MIT
