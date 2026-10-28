"""ChatGPT 플러그인 번들(integrations/plugins/chatgpt): 매니페스트·MCP 항목·스킬이 호스트가 읽는 꼴인가.

실제 ChatGPT·닷에 설치해 보는 것은 사람 단계다. 여기서는 파일의 꼴과, 스킬이 부르는 도구와
이벤트가 실제 Vinc 의 것인지만 고정한다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from vinc_mcp import events, roles, tools

PLUGIN = Path(__file__).resolve().parents[2] / "integrations" / "plugins" / "chatgpt"
_KEBAB = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_SEMVER = re.compile(r"\d+\.\d+\.\d+")
#: .codex-plugin/plugin.json 이 아는 필드(openai/plugins 의 plugin-json-spec).
_FIELDS = {"name", "version", "description", "author", "homepage", "repository", "license",
           "keywords", "skills", "hooks", "mcpServers", "apps", "interface"}
_INTERFACE = {"displayName", "shortDescription", "longDescription", "developerName", "category",
              "capabilities", "websiteURL", "privacyPolicyURL", "termsOfServiceURL",
              "defaultPrompt", "brandColor", "composerIcon", "logo", "screenshots"}


def _manifest() -> dict:
    return json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))


def _frontmatter(text: str) -> dict:
    assert text.startswith("---\n"), "SKILL.md must open with front matter"
    out = {}
    for line in text[4:].split("\n---\n", 1)[0].splitlines():
        key, _, value = line.partition(": ")
        out[key] = value
    return out


def test_manifest_fields_and_paths():
    manifest = _manifest()
    assert set(manifest) <= _FIELDS and set(manifest["interface"]) <= _INTERFACE
    assert manifest["name"] == "vinc" and _KEBAB.fullmatch(manifest["name"])
    assert _SEMVER.fullmatch(manifest["version"])
    for key in ("skills", "mcpServers"):
        path = manifest[key]
        assert path.startswith("./") and ".." not in path
        assert (PLUGIN / path).exists()
    prompts = manifest["interface"]["defaultPrompt"]
    assert 1 <= len(prompts) <= 3 and all(len(p) <= 128 for p in prompts)


def test_mcp_entry_is_the_hosted_server_with_no_key():
    entry = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
    server = entry["mcpServers"]["vinc"]
    assert server["type"] == "http" and server["url"] == roles.MCP_URL
    assert not {"headers", "bearer_token_env_var", "env"} & set(server)


def test_skills_call_real_tools_and_name_real_events():
    names = {tool["name"] for tool in tools.TOOLS}
    found = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    assert [p.parent.name for p in found] == ["vinc-events", "vinc-roles"]
    for path in found:
        text = path.read_text(encoding="utf-8")
        meta = _frontmatter(text)
        assert meta["name"] == path.parent.name and not meta["name"].startswith("agent-")
        assert len(meta["description"]) > 40
        for called in set(re.findall(r"`(vinc_[a-z_]+)`", text)):
            assert called in names, f"{path.parent.name} names an unknown tool {called}"
    watched = (PLUGIN / "skills" / "vinc-events" / "SKILL.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"`([a-z]+\.[a-z_]+)`", watched)) - {"data.run"}
    assert listed == set(events.KINDS)
