# Vinc plugin for ChatGPT and dots

| Part | File |
|---|---|
| Plugin identity and directory card | `.codex-plugin/plugin.json` |
| The hosted Vinc MCP server, signed in with OAuth (refresh tokens, so it stays connected) | `.mcp.json` |
| Running your Vinc roles with their reading, gates and person-only stops | `skills/vinc-roles/SKILL.md` |
| Subscribing to Vinc events in a Work chat or a dot | `skills/vinc-events/SKILL.md` |

## Add your own roles as skills

```bash
pip install vincs
export VINC_API_KEY=vinc_ro_...
vinc --target account role export agent-reviewer --out skills
```

That writes `skills/agent-reviewer/SKILL.md`, the same file the Vinc MCP server serves through
the MCP Skills Extension. Export again after the role changes.

## Install

- **ChatGPT:** Plugins, the plus button, Add custom MCP server, with the address
  `https://mcp.vincs.io/mcp` and OAuth. Choose Create as a plugin, then add the skills of this
  folder to it. For a local copy, place this folder in `~/.codex/plugins/vinc` and list it in
  `~/.agents/plugins/marketplace.json`.
- **Dots** use the plugins enabled for the account. Whether a plugin added this way reaches a dot
  is not confirmed yet.

Events need a Work chat or a dot, and the Vinc server must have event webhooks turned on.
