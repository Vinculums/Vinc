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
Parsed citations still need permission/existence checks, and person-only
requests still require approval before any action.

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
