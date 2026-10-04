# vinc-agent-framework

[Vinc](https://vincs.io) for [Microsoft Agent Framework](https://learn.microsoft.com/agent-framework/). Vinc is a knowledge graph you share with AI: the decisions, records and documents your team wrote, with the reasons attached. This package brings the relevant part of it into each agent run.

## Install

```bash
pip install vinc-agent-framework
```

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
- If Vinc is unreachable or the daily limit is spent, the run goes on without the block. A wrong key or space raises.
- For a team's graph pass `space="<team id>"`.

## Let the model look things up

```python
vinc = VincContextProvider(expose_tools=True)
```

adds two read-only tools, `vinc_brief` and `vinc_search`. You can also build them yourself with `create_vinc_tools(client)`.

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


## License

MIT
# Final output gate

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
