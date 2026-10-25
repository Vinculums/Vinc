# -*- coding: utf-8 -*-
"""MCP Skills 확장: 모던 세대에서 역할 하나가 스킬 하나로 나가고, 핸드셰이크 세대는 그대로 닫혀 있다."""
from __future__ import annotations

import hashlib
import json

from vinc_mcp import rpc
from vinc_mcp.bridge import Bridge

META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}


def skill_text(slug: str) -> str:
    return (f"---\nname: {slug}\ndescription: {json.dumps('Do the ' + slug + ' work.')}\n---\n\n"
            f"# {slug}\n\nRole `concept:{slug}` from Vinc.\n")


class Stub(Bridge):
    def __init__(self, n_roles: int = 3):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:test")
        self.slugs = [f"agent-r{i:02d}" for i in range(n_roles)]
        self.calls: list[tuple[str, dict]] = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((path, dict(params or {})))
        if path == "/roles":
            off = int((params or {}).get("offset") or 0)
            rows = [{"id": "concept:" + s, "title": s.upper(), "mission": "m"} for s in self.slugs]
            page = rows[off:off + 100]
            return {"executed": True, "roles": page,
                    "next_offset": off + 100 if off + 100 < len(rows) else None}
        if path == "/roles/export":
            slug = params["role"].split(":", 1)[1]
            if slug not in self.slugs:
                return {"kind": "role_export", "executed": True, "found": False}
            return {"kind": "role_export", "executed": True, "content": skill_text(slug),
                    "filename": "SKILL.md", "path": slug + "/SKILL.md"}
        return {"executed": False}


def call(d, method, params=None, meta=META, **kw):
    p = dict(params or {})
    if meta is not None:
        p["_meta"] = meta
    return d.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": p}, **kw)


def test_skills_list_carries_a_complete_manifest_with_digests():
    d = rpc.Dispatcher(Stub())
    r = call(d, "skills/list")["result"]
    assert r["resultType"] == "complete" and r["ttlMs"] == 60_000 and r["cacheScope"] == "private"
    first = r["skills"][0]
    text = skill_text("agent-r00").encode("utf-8")
    assert first == {"uri": "skill://agent-r00/SKILL.md",
                     "frontmatter": {"name": "agent-r00", "description": "Do the agent-r00 work."},
                     "resources": [{"uri": "skill://agent-r00/SKILL.md",
                                    "digest": "sha256:" + hashlib.sha256(text).hexdigest(),
                                    "size": len(text)}]}
    assert "nextCursor" not in r


def test_skills_list_pages_by_cursor():
    d = rpc.Dispatcher(Stub(n_roles=30))
    first = call(d, "skills/list")["result"]
    assert len(first["skills"]) == 25 and first["nextCursor"] == "25"
    rest = call(d, "skills/list", {"cursor": first["nextCursor"]})["result"]
    assert [s["uri"] for s in rest["skills"]] == [f"skill://agent-r{i:02d}/SKILL.md" for i in range(25, 30)]
    assert "nextCursor" not in rest
    assert call(d, "skills/list", {"cursor": "x"})["error"]["code"] == -32602


def test_skills_get_answers_by_uri_and_refuses_unknown_ones():
    d = rpc.Dispatcher(Stub())
    r = call(d, "skills/get", {"uri": "skill://agent-r01/SKILL.md"})["result"]
    assert r["skill"]["frontmatter"]["name"] == "agent-r01" and r["resultType"] == "complete"
    for uri in ("skill://agent-nobody/SKILL.md", "skill://agent-r01/other.md", "file:///etc/passwd", None):
        assert call(d, "skills/get", {"uri": uri})["error"]["code"] == -32602, uri


def test_resources_read_serves_the_same_bytes_the_manifest_hashes():
    d = rpc.Dispatcher(Stub())
    entry = call(d, "skills/get", {"uri": "skill://agent-r02/SKILL.md"})["result"]["skill"]
    r = call(d, "resources/read", {"uri": "skill://agent-r02/SKILL.md"})["result"]
    content = r["contents"][0]
    assert content["mimeType"] == "text/markdown" and content["uri"] == "skill://agent-r02/SKILL.md"
    digest = "sha256:" + hashlib.sha256(content["text"].encode("utf-8")).hexdigest()
    assert digest == entry["resources"][0]["digest"]
    listed = call(d, "resources/list")["result"]["resources"]
    assert [x["uri"] for x in listed] == [f"skill://agent-r{i:02d}/SKILL.md" for i in range(3)]


def test_the_handshake_era_stays_closed():
    d = rpc.Dispatcher(Stub())
    for method in ("skills/list", "skills/get", "resources/list", "resources/read"):
        assert call(d, method, {"uri": "skill://agent-r00/SKILL.md"}, meta=None)["error"]["code"] == -32601
    init = d.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "t", "version": "0"}}})
    assert init["result"]["capabilities"] == {"tools": {}, "prompts": {}}


def test_templates_and_subscribe_stay_closed_in_the_modern_era_too():
    d = rpc.Dispatcher(Stub())
    for method in ("resources/templates/list", "resources/subscribe"):
        assert call(d, method)["error"]["code"] == -32601, method


def test_community_spaces_and_quota_follow_the_prompt_rules():
    d = rpc.Dispatcher(Stub())
    r = call(d, "skills/list", forced_owner="community:0198c0de-1111-7222-8333-444455556666")
    assert "error" in r
    seen = []

    def quota(owner, plan):
        seen.append(owner)
        return {"isError": True, "content": [{"type": "text", "text": "limit reached"}]}
    r = call(d, "skills/list", forced_owner="user:test", quota=quota)
    assert r["error"]["code"] == -32603 and seen == ["user:test"]
