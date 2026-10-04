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

## Prefetch the selected role's evidence

```python
middleware = VincContextMiddleware(
    client=reader, prefetch_role="concept:agent-reviewer",
)
```

This reads the role contract, required spec briefs and task brief/search before
each model call, including the returned node ids. Unlike ordinary question
lookup, the role prefetch mode does not cache content or permission checks.
Read tools may still be passed to `create_agent` as an additional lookup path.
Required spec misses/ties and authentication/service failures raise; nothing
writes or delegates work.

Graph nodes can call `prefetch(reader, role_id, task)` or async `aprefetch` and
pass its returned block to `build_system_message`. The chosen role comes from
application/operator configuration. `prefetch_max_chars` controls the combined
middleware budget; exceeding it raises instead of discarding required readings.
Model citation quality must be evaluated separately.

## License

MIT
