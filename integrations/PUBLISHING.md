# Python integration releases

`vincs` is the unified SDK, optional integrations and account CLI distribution.
Its source is `integrations/vincs/python`. The same trusted-publisher workflow
accepts the TestPyPI package choice `vincs` and production tags
`integrations-vincs-v<version>`. Register the pending publisher for `vincs` on
each index with GitHub owner `Vinculums`, repository `Vinc`, workflow
`integrations-publish.yml`, and environment `testpypi` or `pypi` respectively.
The first upload creates the project; pending registration does not reserve
its name. Build the wheel from the standalone sdist and verify clean installs
before publication. Existing package publishers and release tags stay valid.

The unified build takes the SDK, adapters and CLI from their canonical source
directories. Its SDK lives under `vinc.sdk`; adapters under
`vinc.integrations.langgraph` and `vinc.integrations.agent_framework`. CLI
implementation modules are private runtime files, loaded only in the CLI child
process. No old distribution's files are overwritten or required to be installed.
The wheel has no native local engine or embedding model; the signed standalone
CLI remains the local/offline installation path.

Each package has its own version in its `pyproject.toml`. Desktop `v*` tags do not
publish Python packages. The workflow in `.github/workflows/integrations-publish.yml`
is the only publish path and uses PyPI Trusted Publishing; it stores no PyPI token.

Before the first release, create the three projects on both TestPyPI and PyPI.
For each project, register a GitHub trusted publisher with owner `Vinculums`,
repository `Vinc`, workflow `integrations-publish.yml`, and the corresponding
environment `testpypi` or `pypi`. Configure those GitHub environments with the
desired reviewers and tag restrictions. This account setup is VIN-190.

1. Merge and verify the package CI on `main`. From `main`, dispatch
   `integrations-publish` with the package name. This publishes that version to
   TestPyPI only. Install the uploaded wheel from TestPyPI and import its module.
2. After that check passes, create and push an annotated tag at the verified
   `main` commit: `integrations-vinc-client-v0.1.0`,
   `integrations-vinc-agent-framework-v0.1.0`, or
   `integrations-vinc-langgraph-v0.1.0` (replace the version with the exact
   `pyproject.toml` version). The workflow refuses a tag whose commit is not on
   `main` or whose version differs from the package metadata.
3. Wait for the PyPI workflow to finish, then install the package from PyPI in a
   clean environment and verify its import. Publish `vinc-client` before either
   adapter, because both depend on it.

Registry uploads are permanent for a given name and version. A retry of the same
upload can fail because the files already exist; inspect the registry and the
workflow result before deciding whether to bump a version.
