# Vinc for Python and the terminal

Install the SDK and the `vinc` command together:

```sh
pip install vincs
vinc --help
vinc --version
```

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

These modules are built from the same source as `vinc-client`, `vinc-langgraph`
and `vinc-agent-framework`. Existing installations and imports keep working;
the unified package does not overwrite their files. Framework imports require
the corresponding extra. Importing the base SDK does not load a framework or
start a local database. No conversation is automatically written to Vinc.

The Python distribution version is independent of the product version reported
by `vinc --version`. See the [SDK guide](https://vincs.io/docs/frameworks/) for
key types, read-only use and explicit writes.
