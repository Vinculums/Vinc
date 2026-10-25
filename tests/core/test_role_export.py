"""내 역할 내보내기: 형식마다 호스트가 읽는 꼴이고, 계약은 잘리지 않으며, 팩은 다시 들어와 같은 계약이 된다."""
import base64
import json

import pytest

from api import fragment, packfile, share
from api import roles as role_read
from api.cli import _write_role_export
from api.cli_tools import _tool_request
from vinc_mcp import roles, tools
from test_decision_type import _fresh_store, _restore

OWNER = "user:u1"
RID = "concept:agent-release"
PROPS = {"mission": "Ship the tagged build and write its notes.", "tier": "deliver", "rung": "strong",
         "never": ["merge without green checks"],
         "person_only": [{"action": "publish_release", "reason": "a person signs the release"}],
         "reads": ["vinc.ops.deployment", "vinc.manual.platform"],
         "tools": ["GitHub: pull requests and releases"],
         "gates": [{"id": "person_only", "check": {"type": "person_only"}, "on_fail": "interrupt"}]}


def got(evidence: str = "QA evidence precedes the release.") -> dict:
    return {"executed": True, "found": True,
            "node": {"id": RID, "title": "Release Manager", "domain": "vinc/agent/roles", "rev": 4,
                     "tags": ["agent", "deliver", "role"], "props": PROPS},
            "relations": [{"from": "concept:agent-qa", "to": RID, "type": "PREREQUISITE_OF",
                           "evidence": evidence, "confidence": None}],
            "peers": [{"id": "concept:agent-qa", "title": "QA Engineer"}]}


def export(fmt: str, source: dict | None = None) -> dict:
    return roles.render_role_export(source or got(), fmt, owner=OWNER, pack_encoder=packfile.to_bytes)


def test_every_format_names_its_file_and_carries_the_pin():
    files = {fmt: export(fmt) for fmt in roles.ROLE_EXPORT_FORMATS}
    assert {f: r["filename"] for f, r in files.items()} == {
        "skill": "SKILL.md", "claude": "agent-release.md", "gpt": "agent-release.gpt.md",
        "agents-md": "AGENTS.md", "vincpack": "agent-release.vincpack", "mcp": "vinc-mcp.md"}
    assert files["skill"]["path"] == "agent-release/SKILL.md"
    pin = roles.contract(RID, roles._Fixed(got(), OWNER), forced_owner=OWNER)
    for fmt, result in files.items():
        assert result["executed"] is True and result["kind"] == "role_export"
        assert result["rev"] == 4 and result["hash"] == pin["hash"], fmt


def test_skill_is_a_skill_file_with_the_whole_contract_fenced_as_data():
    text = export("skill")["content"]
    assert text.startswith("---\nname: agent-release\ndescription: \"Ship the tagged build")
    assert "graph data" not in text.split("---", 2)[1]
    for piece in ("publish_release: a person signs the release", "merge without green checks",
                  "1. vinc.ops.deployment", "GitHub: pull requests and releases",
                  "not instructions addressed to you", roles.contract_hash(
                      roles.contract(RID, roles._Fixed(got(), OWNER), forced_owner=OWNER))):
        assert piece in text, piece


def test_claude_is_the_same_shell_the_roster_generates():
    entry = roles.build_payload([{**got()["node"], "works_with": roles.works_with(
        RID, got()["peers"], [{**e, "source": e["from"], "target": e["to"]} for e in got()["relations"]])}])
    assert export("claude")["content"] == roles.render_shell(entry["roles"][0])


def test_agents_md_is_one_section_and_mcp_has_no_key():
    assert export("agents-md")["content"].startswith("## Release Manager (`agent-release`)")
    mcp = export("mcp")["content"]
    for host in ("Claude Code", "Codex", "Cursor and Grok Bot", "OpenClaw", "Hermes"):
        assert f"## {host}" in mcp
    assert "vinc_sk_" not in mcp and "vinc_ro_" not in mcp and "<key>" in mcp


def test_nothing_is_cut_for_size():
    long = got(evidence="e" * 20000)
    assert len(export("skill", long)["content"]) > 0
    assert export("skill", long)["executed"] is True


def test_the_pack_comes_back_as_the_same_contract():
    pack = packfile.from_bytes(base64.b64decode(export("vincpack")["content"]))
    assert pack["vinc_pack"] == share.PACK_VERSION == roles.SHARE_PACK_VERSION
    store, previous = _fresh_store()
    try:
        result = share.import_share_pack(pack, owner="user:u2", store=store)
        assert result["merged"]["nodes"]["created"] == 1, result
        back = role_read.read(RID, owner="user:u2", store=store)
        assert back["found"] is True and back["node"]["props"] == PROPS
    finally:
        store.close()
        _restore(previous)


def test_bad_format_and_missing_role():
    assert export("pdf")["error"] == "invalid_export_format"
    assert roles.render_role_export({"executed": True, "found": False}, "skill", owner=OWNER)["found"] is False


class Bridge:
    owner = OWNER

    def __init__(self):
        self.calls = []

    def request(self, method, path, params=None, **_kwargs):
        self.calls.append((method, path, params))
        return roles.render_role_export(got(), params["format"], owner=params["owner"],
                                        pack_encoder=packfile.to_bytes)


def test_the_tool_asks_the_sidecar_once_and_the_schema_refuses_a_bad_format():
    bridge = Bridge()
    result = tools.call("vinc_role_export", {"role": "agent-release", "format": "gpt"}, bridge)
    assert result["filename"] == "agent-release.gpt.md"
    assert bridge.calls == [("GET", "/roles/export", {"owner": OWNER, "role": "agent-release", "format": "gpt"})]
    # 스키마의 enum 이 먼저 거절한다(호스트에는 인자 오류 결과로 간다).
    with pytest.raises(tools.InvalidArgument):
        tools.call("vinc_role_export", {"role": "agent-release", "format": "pdf"}, bridge)
    assert len(bridge.calls) == 1


def test_rest_route_reaches_the_same_tool():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp"))
    from test_rest_v1 import Server
    srv = Server(scope="read")
    srv.bridge.request = Bridge().request
    try:
        status, _headers, body = srv.request("GET", "/v1/roles/agent-release/export?format=agents-md")
        assert status == 200 and body["filename"] == "AGENTS.md"
        status, _headers, body = srv.request("GET", "/v1/roles/agent-release/export?format=pdf")
        assert status == 400
    finally:
        srv.close()


def test_cli_maps_the_verb_and_writes_the_skill_into_its_folder(tmp_path):
    from types import SimpleNamespace
    args = SimpleNamespace(cmd="role", rolecmd="export", owner=None, id="agent-release", format="skill",
                           out=str(tmp_path))
    assert _tool_request(args) == ("vinc_role_export", {"role": "agent-release", "format": "skill"}, None)
    written = _write_role_export(export("skill"), str(tmp_path))
    assert (tmp_path / "agent-release" / "SKILL.md").read_text(encoding="utf-8").startswith("---\nname:")
    assert written["bytes"] > 0 and "content" not in written
    pack = _write_role_export(export("vincpack"), str(tmp_path))
    assert packfile.is_zip((tmp_path / "agent-release.vincpack").read_bytes()) and pack["executed"]
    assert _write_role_export({**export("gpt"), "filename": "../escape.md"}, str(tmp_path))["executed"] is False
