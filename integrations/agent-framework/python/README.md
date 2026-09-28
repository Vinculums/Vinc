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

## License

MIT
