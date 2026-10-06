# vinc-langgraph

[Vinc](https://vincs.io) for [LangGraph](https://langchain-ai.github.io/langgraph/) and LangChain agents. Vinc is a knowledge graph you share with AI: the decisions, records and documents your team wrote, with the reasons attached. This package brings the relevant part of it into your graph nodes and model calls.

## Install

```bash
pip install vinc-langgraph            # node helpers and tools
pip install "vinc-langgraph[agents]"  # plus the middleware for create_agent
```

Set `VINC_API_KEY` to a member key. A `vinc_ro_` key is enough for everything except `record_episode`.

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

The middleware adds the block to the system message of each model call. An agent that uses tools calls the model several times per question, so the answer is kept for 60 seconds (`cache_seconds`) instead of spending a Vinc call each time.

## Tools the model can call

```python
from vinc_langgraph import create_vinc_tools

tools = create_vinc_tools(VincClient())   # vinc_brief and vinc_search, read only
```

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
content hashes, and task brief/search before
each model call, including the returned node ids. Unlike ordinary question
lookup, the role prefetch mode does not cache content or permission checks.
Read tools may still be passed to `create_agent` as an additional lookup path.
Missing, partial or unverified required documents raise `prefetch_spec_unresolved`
before optional task lookup. Authentication/service failures raise; nothing
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
reads all required documents before reporting budget overflow and skips optional
task lookup on that failure; it never raises the limit or retries automatically.
The size can change when graph content changes. All 23 hosted roles exceeded the
default in the 2026-10-06 measurement; successful blocks were 34,640 to 217,070
characters (characters are not tokens).
Model citation quality must be evaluated separately.


## License

MIT
# Final output gate

```python
from vinc_client import OutputGate
from vinc_langgraph import create_output_gate_middleware, evaluate_output_node

gate = OutputGate(fresh_role["props"])
middleware = create_output_gate_middleware(gate, evidence=host_evidence_for_this_run)
# Pass middleware=[middleware] to create_agent; use invoke()/ainvoke().
```

Intermediate AI tool-call messages continue to the tool loop. The final response
is checked outside the model; block and interrupt raise `OutputGateStopped`.
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
