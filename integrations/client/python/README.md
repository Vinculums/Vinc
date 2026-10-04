# vinc-client

Python client for the [Vinc](https://vincs.io) REST v1 API. Vinc is a knowledge graph you share with AI: decisions, records and documents your team wrote, with the reasons attached.

This package reads that graph for an agent and writes to it only when your code asks. It is the base of `vinc-langgraph` and `vinc-agent-framework`.

Looking for the `vinc` command? See [the terminal CLI installation guide](https://vincs.io/docs/cli/#install). `vinc-client` is a Python library and does not install a `vinc` executable.

## Install

```bash
pip install vinc-client
```

## Keys and spaces

Create a member key in your Vinc account. A `vinc_ro_` key reads; a `vinc_sk_` key can also write. Use a read-only key wherever the agent only needs context.

```python
from vinc_client import VincClient

vinc = VincClient()                      # reads VINC_API_KEY
team = VincClient(space="<team id>")     # a team's shared graph instead of your personal one
```

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

## Errors

| Class | Meaning |
|---|---|
| `VincAuthError` | missing, malformed, refused or read-only key; configuration is wrong |
| `VincNotFound` | the node, document or space does not exist for this key |
| `VincInvalidArgument` | the request was malformed or refused (for example a topic limit) |
| `VincQuotaExceeded` | the daily limit is spent; `retry_after` is in seconds |
| `VincUnavailable` | network or server failure; for a write, check `details["write_may_have_applied"]` |
| `VincPartialWrite` | part of a write was skipped; `result` holds what the server said |

## Async

`AsyncVincClient` has the same methods, awaited.

## License

MIT
