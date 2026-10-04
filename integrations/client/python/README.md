# vinc-client

Python client for the [Vinc](https://vincs.io) REST v1 API. Vinc is a knowledge graph you share with AI: decisions, records and documents your team wrote, with the reasons attached.

This package reads that graph for an agent and writes to it only when your code asks. It is the base of `vinc-langgraph` and `vinc-agent-framework`.

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
