# Vinc plugin for Cursor and Grok Bot

One plugin for both hosts. Grok Bot takes the same MCP servers, plugins and skills as Cursor.

| Part | File |
|---|---|
| The hosted Vinc MCP server, signed in with OAuth | `mcp.json` |
| Running your Vinc roles with their reading, gates and person-only stops | `skills/vinc-roles/SKILL.md` |
| Starting a Grok Bot routine from a Vinc event | `skills/vinc-routines/SKILL.md` |

## Add your own roles as skills

Each of your roles exports to a skill folder that drops into `skills/`:

```bash
pip install vincs
export VINC_API_KEY=vinc_ro_...
vinc --target account role export agent-reviewer --out skills
```

That writes `skills/agent-reviewer/SKILL.md`, the same file the Vinc MCP server serves through
the MCP Skills Extension. Export again after the role changes; the file names the contract hash
it was made from.

## Install

- **Cursor:** add this folder as a plugin, or install Vinc from the Cursor marketplace. The
  server asks you to sign in to vincs.io the first time a tool is called.
- **Grok Bot:** install the plugin under Settings, Plugins, then enable the skills for the Bot
  that should use them under Connect apps, Private skills.

Where OAuth is not offered, put a key in your own `~/.cursor/mcp.json` instead of this file:
`{"mcpServers": {"vinc": {"url": "https://mcp.vincs.io/mcp", "headers": {"Authorization": "Bearer <key>"}}}}`.
Never commit a key.
