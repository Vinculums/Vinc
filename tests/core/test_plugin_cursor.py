"""호스트 플러그인 번들(integrations/plugins): 매니페스트·MCP 항목·스킬이 호스트가 읽는 꼴인가.

실제 호스트에 설치해 보는 것은 사람 단계다. 여기서는 파일의 꼴과, 스킬이 부르는 도구가
실제 Vinc 도구인지, 역할 내보내기가 번들의 `skills/` 에 그대로 놓이는지만 고정한다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from api import cli
from vinc_mcp import roles, tools

ROOT = Path(__file__).resolve().parents[2] / "integrations" / "plugins"
CURSOR = ROOT / "cursor"
_KEBAB = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
_SEMVER = re.compile(r"\d+\.\d+\.\d+")
#: Cursor 플러그인 매니페스트가 아는 필드(cursor.com/docs/reference/plugins).
_CURSOR_FIELDS = {"name", "description", "version", "author", "homepage", "repository", "license",
                  "keywords", "logo", "rules", "agents", "skills", "commands", "hooks", "mcpServers",
                  "variables"}


def _frontmatter(text: str) -> dict:
    assert text.startswith("---\n"), "SKILL.md must open with front matter"
    head = text[4:].split("\n---\n", 1)[0]
    out = {}
    for line in head.splitlines():
        key, _, value = line.partition(": ")
        out[key] = value
    return out


def _skills(plugin: Path) -> list[Path]:
    return sorted((plugin / "skills").glob("*/SKILL.md"))


def test_cursor_manifest_is_one_kebab_named_plugin():
    manifest = json.loads((CURSOR / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert set(manifest) <= _CURSOR_FIELDS
    assert _KEBAB.fullmatch(manifest["name"]) and manifest["name"] == "vinc"
    assert _SEMVER.fullmatch(manifest["version"])
    assert manifest["author"]["name"] and manifest["description"]


def test_cursor_mcp_entry_is_the_hosted_server_with_no_key():
    entry = json.loads((CURSOR / "mcp.json").read_text(encoding="utf-8"))
    assert entry == {"mcpServers": {"vinc": {"url": roles.MCP_URL}}}


def test_skills_are_named_after_their_folders_and_call_real_tools():
    names = {tool["name"] for tool in tools.TOOLS}
    found = _skills(CURSOR)
    assert [p.parent.name for p in found] == ["vinc-roles", "vinc-routines"]
    for path in found:
        text = path.read_text(encoding="utf-8")
        meta = _frontmatter(text)
        assert meta["name"] == path.parent.name and _KEBAB.fullmatch(meta["name"])
        assert len(meta["description"]) > 40
        # 역할 내보내기(agent-*)와 이름이 겹치지 않는다: 같은 skills/ 폴더에 함께 놓인다.
        assert not meta["name"].startswith("agent-")
        for called in set(re.findall(r"`(vinc_[a-z_]+)`", text)):
            assert called in names, f"{path.parent.name} names an unknown tool {called}"


def test_routine_recipe_uses_flags_the_cli_has(capsys):
    text = (CURSOR / "skills" / "vinc-routines" / "SKILL.md").read_text(encoding="utf-8")
    assert "VINC_FORWARD_TOKEN" in text
    try:
        cli._main(["events", "tail", "--help"])
    except SystemExit:
        pass
    usage = capsys.readouterr().out
    for flag in ("--role", "--kind", "--cursor-file", "--forward", "--once"):
        assert flag in text and flag in usage


def test_role_export_lands_in_the_plugin_skills_folder(tmp_path):
    got = {"executed": True, "found": True, "node": {
        "id": "concept:agent-reviewer", "title": "Reviewer", "domain": "vinc/agent", "rev": 3,
        "props": {"mission": "Review a change.", "tier": "verify", "rung": "strong",
                  "never": ["merge"], "person_only": []}}, "relations": [], "peers": []}
    out = roles.render_role_export(got, "skill", owner="user:x")
    assert out["executed"] and out["path"] == "agent-reviewer/SKILL.md"
    written = cli._write_role_export(out, str(tmp_path / "skills"))
    target = Path(written["written"])
    assert target == tmp_path / "skills" / "agent-reviewer" / "SKILL.md"
    meta = _frontmatter(target.read_text(encoding="utf-8"))
    assert meta["name"] == "agent-reviewer"
