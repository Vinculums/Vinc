# Licensing

This repository holds code and content under more than one license. This page says which
license covers which part. When a directory carries its own `LICENSE` file, that file wins.

| Part | Where | License |
|---|---|---|
| Integration packages: the Python client, the LangGraph and Agent Framework adapters, the `vincs` CLI, the OpenClaw plugin, the ChatGPT and Cursor plugins | `integrations/` | [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0), see the `LICENSE` in each package |
| The public API and tool contracts | `vinc_mcp/openapi_v1.json`, `vinc_mcp/tool_reference.json` | Apache-2.0 |
| Official packs and agent packs | `starter-packs/*.vincpack`, `starter-packs/agents/`, `starter-packs/work/` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Everything else: the desktop and web app, the sidecar, the store, the engines, retrieval, the MCP and hub server, packaging, scripts, emails, and the installers built from them | the rest of this repository | [FSL-1.1-ALv2](LICENSE) |

## What FSL-1.1-ALv2 allows

The Functional Source License lets you use, copy, modify and redistribute the software for any
purpose except a **Competing Use**: making it available to others in a commercial product or
service that substitutes for Vinc or offers substantially similar functionality. Internal use,
non-commercial education and research, and professional services you provide to a licensee are
allowed. Two years after a version is made available, that version is also licensed under
Apache-2.0. The full text is in [`LICENSE`](LICENSE).

FSL is a source-available license, not an open source license approved by the OSI. Call the
product core "source available" or "Fair Source", not "open source". The integration packages
are open source under Apache-2.0.

## Earlier releases

Desktop installers, the CLI and the integration packages released before this licensing took
effect were distributed under the MIT License, and those releases stay available under it.
The licenses above apply from the next release of each.

## Not licensed

- **Trademarks.** The names Vinc and Vincs, the ring mark, the app icon and the logo files
  (`scripts/design/assets/logo/`, `app/src-tauri/icons/`, `app/public/*.svg`) are not covered by
  any license in this repository. A fork must use a different name and mark.
- **Third-party components** keep their own licenses: the fonts in `app/src/fonts/` (SIL OFL 1.1),
  the LadybugDB extensions in `packaging/lbug_extensions/` (MIT), and every dependency listed in
  `app/public/third-party/THIRD_PARTY_NOTICES.md`.

## Contributions

Contributions to the integration packages are accepted under Apache-2.0 with a DCO sign-off
(`git commit -s`). Contributions to the rest of the repository need a contributor license
agreement, which will be in place before outside contributions are accepted.

## Questions

For a use the licenses above do not cover, such as a commercial license for a Competing Use,
write to support@vincs.io.
