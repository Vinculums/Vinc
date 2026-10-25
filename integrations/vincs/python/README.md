# Vinc for Python and the terminal

Install the SDK and the `vinc` command together:

```sh
pip install "vincs>=0.1.5"
vinc --help
vinc --version
```

This README describes vincs 0.1.5. It carries vinc-client 0.1.12, vinc-langgraph 0.1.9
and vinc-agent-framework 0.1.8, built from the same commit. The [versions](#versions)
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
pip install "vincs[all]"
```

```python
from vinc.integrations.langgraph import get_vinc_context
from vinc.integrations.agent_framework import VincContextProvider
```

`vinc` exports every name `vinc_client` exports (they live in `vinc.sdk`), plus
`Vinc` and `AsyncVinc` as short names for `VincClient` and `AsyncVincClient`.
`vinc.integrations.langgraph` exports what `vinc_langgraph`
exports and `vinc.integrations.agent_framework` what `vinc_agent_framework`
exports, with the same parameters. The vinc-client, vinc-langgraph and
vinc-agent-framework READMEs list every method and helper with its parameters.

These modules are built from the same source as `vinc-client`, `vinc-langgraph`
and `vinc-agent-framework`. Existing installations and imports keep working;
the unified package does not overwrite their files. Framework imports require
the corresponding extra. Importing the base SDK does not load a framework or
start a local database. No conversation is automatically written to Vinc.

The Python distribution version is independent of the product version reported
by `vinc --version`. See the [SDK guide](https://vincs.io/docs/frameworks/) for
key types, read-only use and explicit writes.

## Adding an adapter

A new framework or host adapter joins `vincs` as one module and one extra. It
needs no distribution, trusted publisher or release tag of its own.

1. Put its source in `src/vinc/integrations/<name>/`. Import the SDK from
   `vinc.sdk` and the framework only inside that package, so `import vinc`
   never loads it.
2. Add an extra named after the module, with `-` for `_` (module
   `agent_framework`, extra `agent-framework`), that lists the framework's
   requirements, and add the same name to `all`. Add the extra together with
   its adapter, never ahead of it.
3. If the host finds plugins through an entry point, declare it under
   `[project.entry-points."<group>"]` and point it at a name in that package.
   Entry points are installed by every `vincs` install, extra or not, so the
   target has to import with only the host and the SDK present.

`tests/test_install.py` fails when a module under `vinc.integrations` has no
extra of its name, when an extra installs nothing, or when `all` leaves one out.
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
| 0.1.5 | 0.1.12 | 0.1.9 | 0.1.8 | `pin` on `role` and `prefetch`, `prefetch_pin` on the adapters, and `VincRoleChanged` |
| 0.1.4 | 0.1.11 | 0.1.8 | 0.1.7 | `prefetch_with_contract` and `RoleToolPolicy`, so the adapters keep a role's disallowed tools out; LangGraph reads the role once per run; transient retries; `edges`, `role_check`, delete and merge previews; `create_only` and `operation_id` on the write helpers |
| 0.1.3 | 0.1.8 | 0.1.4 | 0.1.4 | `create_only` and `operation_id` on `ingest`, `operation_status`, `expected_rev` and `props_patch` |
| 0.1.2 | 0.1.7 | 0.1.4 | 0.1.4 | `mode`, `as_of` and `known_at` on reads |
| 0.1.1 | 0.1.6 | 0.1.3 | 0.1.3 | fixes to the citations prefetch gives after the required reading |
| 0.1.0 | 0.1.6 | 0.1.3 | 0.1.3 | first release |

Each package's README has its own version notes.
