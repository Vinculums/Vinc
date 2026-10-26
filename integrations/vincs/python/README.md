# Vinc for Python and the terminal

Install the SDK and the `vinc` command together:

```sh
pip install "vincs>=0.1.6"
vinc --help
vinc --version
```

This README describes vincs 0.1.6. It carries vinc-client 0.1.17, vinc-langgraph 0.1.13
and vinc-agent-framework 0.1.12, built from the same commit. The [versions](#versions)
table lists what each release carries.

Use the account graph from the terminal with a member key created in your Vinc
account. Set `VINC_API_KEY` in your environment, then run:

```sh
vinc --target account whoami
vinc --target account search --query "release checklist"
```

Read a node with `vinc node get <id>`, then send its `rev` as `expected_rev` when
editing a fragment. `props_patch` is JSON Merge Patch and requires that revision;
`props` retains its existing whole-value replacement behavior. `--create-only`
refuses existing identities. Documents accept `--expected-content-hash` from the
previous read.

`vinc delete <id> --dry-run`, `vinc doc rm --id <id> --dry-run`,
`vinc edge delete --from <id> --to <id> --type <type> --dry-run`, and
`vinc merge nodes <keep> <drop> --dry-run` return a bounded complete preview or
refuse it. Review deleted and retained content, then pass its `--preview-digest`
with `--yes`; changed affected state conflicts before mutation. The merge preview
lists unsupported edges and any relationship properties the existing merge loses.

For a write whose response might be lost, generate a fresh lowercase UUID and pass
`--operation-id <uuid>`. `vinc operation status <uuid>` reads that space's receipt.
An identical retry uses the stored result without applying again; changed arguments
conflict. `accepted` and `unknown` do not prove nonapplication. `committed` proves a
graph transaction; published persistence is confirmed only by
`durability: published_generation` in the write response. Receipts are retained
until owner purge, bounded to 5000 per owner, and contain result summaries rather
than authored text. Replayed results are historical; read the current targets.
CLI, MCP and REST never retry uncertain writes automatically.

The account CLI uses the same commands and error handling as the desktop's CLI.
This Python install does not bundle the native local database, extensions or
embedding model. For a self-contained local CLI, use the signed terminal download
from [the CLI installation guide](https://vincs.io/docs/cli/#install).

```python
from vinc import Vinc, AsyncVinc

client = Vinc()  # reads VINC_API_KEY
answer = client.brief("release checklist")
```

Choose integrations using one package name:

```sh
pip install "vincs[langgraph]"
pip install "vincs[agent-framework]"
pip install "vincs[crewai]"
pip install "vincs[hermes]"
pip install "vincs[all]"
```

```python
from vinc.integrations.langgraph import get_vinc_context
from vinc.integrations.agent_framework import VincContextProvider
from vinc.integrations.crewai import create_agent
```

`vinc` exports every name `vinc_client` exports (they live in `vinc.sdk`), plus
`Vinc` and `AsyncVinc` as short names for `VincClient` and `AsyncVincClient`.
`vinc.integrations.langgraph` exports what `vinc_langgraph`
exports and `vinc.integrations.agent_framework` what `vinc_agent_framework`
exports, with the same parameters. The vinc-client, vinc-langgraph and
vinc-agent-framework READMEs list every method and helper with its parameters.
`vinc.integrations.crewai` has no separate distribution; [CrewAI](#crewai) below
lists its names.

These modules are built from the same source as `vinc-client`, `vinc-langgraph`
and `vinc-agent-framework`. Existing installations and imports keep working;
the unified package does not overwrite their files. Framework imports require
the corresponding extra. Importing the base SDK does not load a framework or
start a local database. No conversation is automatically written to Vinc.

The Python distribution version is independent of the product version reported
by `vinc --version`. See the [SDK guide](https://vincs.io/docs/frameworks/) for
key types, read-only use and explicit writes.

## CrewAI

`pip install "vincs[crewai]"` installs CrewAI 1.15.3 or later with the adapter, on
Python 3.11 to 3.13 (CrewAI does not support 3.14, where the extra installs
nothing). It needs vincs 0.1.6 or later.

```python
from crewai import Crew, Task
from vinc import OutputGate, Vinc
from vinc.integrations.crewai import create_agent, create_output_guardrail

reader = Vinc()  # a vinc_ro_ key is enough to read
role = reader.role("concept:agent-reviewer-judgement")
reviewer = create_agent(reader, role["id"], llm="gpt-4o", prefetch_pin=role["hash"])
review = Task(description="Review the diff in PR 12.", expected_output="Findings with sources",
              agent=reviewer, guardrail=create_output_guardrail(OutputGate(role["props"])))
result = Crew(agents=[reviewer], tasks=[review]).kickoff()
```

To get a running agent from a role address in one call, use `build_agent`. It reads the role once,
pins it by hash, adds the guardrail and the two read tools, and runs one task per call. `run`
returns an `AgentRun` and writes the run record at the end (a `vinc_sk_` key; a failed write is
kept as `record_error`, never raised; `record=False` writes nothing):

```python
from vinc.integrations.crewai import build_agent

agent = build_agent("reviewer-judgement", llm="gpt-4o")  # VINC_API_KEY from the environment
run = agent.run("Review the diff in PR 12.")
run.status   # done, blocked (a gate failed) or waiting (a person-only action)
run.output   # the answer, or None when it was blocked or is waiting
```

A person-only action stops the run in `waiting`; approving it is the separate step below, and
approval never executes a tool. If the role changes after it was read, the next run raises
`VincRoleChanged` instead of running on a contract nobody reviewed. For async code use
`abuild_agent` and `arun`.

| Role contract | In CrewAI |
|---|---|
| `job_title`, `mission` | the agent's `role` and `goal` |
| `never`, `person_only` | the agent's `backstory`, read when `create_agent` builds it |
| the complete contract, every required document in full, task evidence | read again before every task the agent runs (`prefetch_with_contract`, the call the other adapters make) and given to the task as its context; over `prefetch_max_chars` (24,000) it raises `prefetch_budget_exceeded` and the model is not called |
| `disallowed_tools` | removed, by exact tool name, from the tools the task runs with, so the model does not see them and CrewAI cannot run them; entries that match no tool give one warning per run |
| `gates`, `never_checks` | `create_output_guardrail(gate)` on the task: a blocked output, or one that needs a person, raises `OutputGateStopped` instead of being sent back to the model to retry |
| person-only actions | `VincPersonOnlyInput` answers CrewAI's human input step (below) |

With `expose_tools=True` each run adds `vinc_brief` and `vinc_search` and allows 4
calls between them; `create_vinc_tools(client)` builds the same two tools for
any agent. CrewAI runs the tool calls of one model turn at the same time, so the
cap and `budget.tool_calls` count calls as they start: when one turn goes over,
which of its calls gets the sentence is not fixed, but how many do is. `prefetch_pin` holds both reads to one contract; a changed role raises
`VincRoleChanged` before the model is called. `package_id` with `package_space`
adds one handoff package to every run. Run the agent on a `Task` in a `Crew`:
`Agent.kickoff` outside a crew is refused, because it would skip the role. Pass
`async_client` for `crew.akickoff()`.

Person-only actions go to CrewAI's human input step. Give the task
`human_input=True` and the role output schema, bind it, and run the crew inside
`installed()`:

```python
from vinc import ApprovalDecision, RoleOutput
from vinc.integrations.crewai import VincApprovalRejected, VincPersonOnlyInput

release = Task(description="Prepare the merge of PR 12.", expected_output="The review",
               agent=reviewer, human_input=True, output_pydantic=RoleOutput)

def decide(request):  # your own human channel, never a model
    return ApprovalDecision(request_id=request.request_id, approved=ask_a_person(request))

step = VincPersonOnlyInput(OutputGate(role["props"]), role_id=role["id"], run_id=run_id,
                           decide=decide, tasks=[release])
with step.installed():
    Crew(agents=[reviewer], tasks=[release]).kickoff()
```

A passing draft is released without asking anyone. A draft that needs a person
calls `decide` once; approval releases the saved draft unchanged and runs no tool,
rejection raises `VincApprovalRejected`, and a blocked draft raises
`OutputGateStopped`. None of these calls the model again. `step.result(task)`
returns what released the draft. Tasks you do not bind keep CrewAI's own prompt.

Nothing is written unless you call a write helper. Record the crew run when it
ends, with a `vinc_sk_` key:

```python
from vinc import trace_ref
from vinc.integrations.crewai import record_run

record_run(Vinc(api_key=WRITE_KEY), run_id, role=role["id"], outcome="success",
           host="crewai", steps=len(result.tasks_output),
           tokens_in=result.token_usage.prompt_tokens,
           tokens_out=result.token_usage.completion_tokens, trace=trace_ref("run.jsonl"))
```

`record_episode`, `write_package` and the `a` prefixed async forms take the
LangGraph adapter's parameters and send the same bodies; so do `prefetch` and
`get_vinc_package_context`.

Where CrewAI behaves differently from the other two adapters:

- The prefetch block reaches the model in the task message, CrewAI's place for
  task context, not in the system message.
- A model that calls a removed tool anyway gets CrewAI's `Tool not found`
  result instead of the role's refusal sentence.
- CrewAI sends tool schemas in strict mode, so every argument is listed as
  required. Names, descriptions, defaults and bounds are the shared ones.
- Inside a crew a tool error, such as a wrong key or space, reaches the model as
  CrewAI's tool error and the run goes on; with `tool_failure_policy="raise"` on
  the agent the crew stops with CrewAI's `ToolExecutionFailedError` instead.
  Called directly, the tool raises the Vinc error.

## Hermes Agent

`vincs[hermes]` makes Vinc the memory provider of
[Hermes Agent](https://hermes-agent.nousresearch.com). Hermes finds it through the
`hermes_agent.memory_providers` entry point, which Hermes reads from v2026.8.13 on
(v0.22.5 was tested; the `hermes-agent` 0.19.0 on PyPI does not read it). Install it
into the Python environment Hermes runs in, then choose `vinc`:

```sh
pip install "vincs[hermes]"
hermes memory setup
hermes memory status
```

Setup asks for a member key, which Hermes keeps in `$HERMES_HOME/.env` as
`VINC_API_KEY`; the provider never writes it anywhere. It also asks for the space to
read (`personal` or a team id) and for `auto_record_space`. Those two go to
`$HERMES_HOME/vinc.json`.

What it reads. Before every turn it adds the block `get_context` builds: a brief,
widened by a search when the brief ties or finds nothing. When Vinc is unreachable or
the key is refused, that turn goes on without it. The model can also call
`vinc_search` and `vinc_brief`, which takes a node id too.

What it writes. The model gets `vinc_record`, which writes one Episode, only in the
person's own conversation and only with a `vinc_sk_` key; its description tells the
model to call it only when the person asks. If you name one space as
`auto_record_space`, each completed turn is recorded there as an Episode tagged
`hermes-turn`. Nothing else is written: nothing without that setting, nothing from
cron jobs, subagents or any other agent Hermes runs on its own, nothing before
compaction, and nothing when Hermes's built-in memory changes.

The `hermes` extra installs nothing beyond `vincs`: the provider runs inside Hermes,
which brings its own interface.

## Adding an adapter

A new framework or host adapter joins `vincs` as one module and one extra. It
needs no distribution, trusted publisher or release tag of its own.

1. Put its source in `src/vinc/integrations/<name>/`. Import the SDK from
   `vinc.sdk` and the framework only inside that package, so `import vinc`
   never loads it.
2. Add an extra named after the module, with `-` for `_` (module
   `agent_framework`, extra `agent-framework`), that lists the framework's
   requirements, and add the same name to `all`. Add the extra together with
   its adapter, never ahead of it. An adapter that a host loads into its own
   environment through an entry point (step 3) may leave its extra empty, because
   the host is its framework; `hermes` is one.
3. If the host finds plugins through an entry point, declare it under
   `[project.entry-points."<group>"]` and point it at a name in that package.
   Entry points are installed by every `vincs` install, extra or not, so the
   target has to import with only the host and the SDK present.

`tests/test_install.py` fails when a module under `vinc.integrations` has no
extra of its name, when an extra installs nothing and no entry point names its
module, or when `all` leaves one out.
CI builds the wheel, installs it once per extra in a clean environment, imports
the adapters that extra enables and runs `vinc --version`; it reads the extras
from the wheel, so a new one is covered without editing the workflow.

The LangGraph and Agent Framework adapters are the exception to step 1. Their
source stays in `integrations/langgraph/python` and
`integrations/agent-framework/python`, because `vinc-langgraph` and
`vinc-agent-framework` still publish from it, and `hatch_build.py` copies it in
at build time with its `vinc_client` imports pointed at `vinc.sdk`.

## Versions

| vincs | vinc-client | vinc-langgraph | vinc-agent-framework | What it adds |
|---|---|---|---|---|
| 0.1.6 | 0.1.17 | 0.1.13 | 0.1.12 | `build_agent` in the LangGraph and Agent Framework adapters (a running agent from a role address), `plan_agent` in the SDK; the run record helpers, a role's run policy and budget, shims as Records; the `crewai` and `hermes` extras |
| 0.1.5 | 0.1.12 | 0.1.9 | 0.1.8 | `pin` on `role` and `prefetch`, `prefetch_pin` on the adapters, and `VincRoleChanged` |
| 0.1.4 | 0.1.11 | 0.1.8 | 0.1.7 | `prefetch_with_contract` and `RoleToolPolicy`, so the adapters keep a role's disallowed tools out; LangGraph reads the role once per run; transient retries; `edges`, `role_check`, delete and merge previews; `create_only` and `operation_id` on the write helpers |
| 0.1.3 | 0.1.8 | 0.1.4 | 0.1.4 | `create_only` and `operation_id` on `ingest`, `operation_status`, `expected_rev` and `props_patch` |
| 0.1.2 | 0.1.7 | 0.1.4 | 0.1.4 | `mode`, `as_of` and `known_at` on reads |
| 0.1.1 | 0.1.6 | 0.1.3 | 0.1.3 | fixes to the citations prefetch gives after the required reading |
| 0.1.0 | 0.1.6 | 0.1.3 | 0.1.3 | first release |

Each package's README has its own version notes.
