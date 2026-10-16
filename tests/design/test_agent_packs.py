# -*- coding: utf-8 -*-
"""starter-packs/agents/*.json — 기본·분야 Agent 팩의 계약.

지키려는 것:

1. 다섯 팩이 모두 공유 팩 계약을 통과하고(validate_pack), 영어만 담는다.
2. 모든 노드는 역할이다: `concept:agent-*`, 예약 주제 `vinc/agent`, props 계약 완비,
   rung 은 사다리에 있는 값. 오케스트레이터는 어디에도 없다(그래프는 스케줄러가 아니다).
3. 프롬프트 표면과 **같은 렌더러**로 렌더된다 — 새 스페이스의 `prompts/list` 가 기본 팩의
   네 역할을 그대로 내고, `prompts/get` 이 그 계약을 낸다.
4. 기본 팩은 무료 주제 상한이 꽉 찬 그래프에도 들어간다(예약 주제 면제).
5. 매니페스트(`official-packs.json`)가 다섯을 공식팩으로 들고 있다 — 계정 백엔드의 자동
   적재는 그 공식 팩 행을 읽는다.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "starter-packs" / "tools"))

import validate_pack                     # noqa: E402
from api import fragment as frag         # noqa: E402
from api import query                    # noqa: E402
from engines import domain_hierarchy     # noqa: E402
from vinc_mcp import roles               # noqa: E402
from vinc_mcp.bridge import Bridge       # noqa: E402

AGENTS = ROOT / "starter-packs" / "agents"
PACKS = sorted(AGENTS.glob("*.json"))
DEFAULT = AGENTS / "default-agents.json"
MANIFEST = json.loads((ROOT / "starter-packs" / "official-packs.json").read_text(encoding="utf-8"))
HANGUL = re.compile(r"[가-힣]")
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}
REQUIRED_PROPS = ("mission", "tier", "rung", "repos", "paths", "reads", "gates",
                  "never", "person_only", "writes_graph")


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _nodes(p: Path) -> list[dict]:
    return _load(p)["fragment"]["nodes"]


class PackContract(unittest.TestCase):
    def test_five_packs_and_the_default_is_one_of_them(self):
        self.assertEqual(len(PACKS), 5, [p.name for p in PACKS])
        self.assertIn(DEFAULT, PACKS)

    def test_every_pack_passes_the_share_pack_validator(self):
        errors: list[str] = []
        warns: list[str] = []
        for p in PACKS:
            validate_pack.check_pack(p, errors, warns)
        self.assertEqual(errors, [])
        self.assertEqual(warns, [], "P8: Hangul in a release pack")

    def test_english_only(self):
        for p in PACKS:
            with self.subTest(pack=p.name):
                self.assertIsNone(HANGUL.search(p.read_text(encoding="utf-8")))

    def test_every_node_is_a_role_in_the_reserved_topic(self):
        for p in PACKS:
            for n in _nodes(p):
                with self.subTest(pack=p.name, node=n["id"]):
                    self.assertTrue(n["id"].startswith(roles.ID_PREFIX))
                    self.assertEqual(n["type"], "concept")
                    self.assertEqual(n["domain"], roles.DOMAIN)
                    self.assertTrue(domain_hierarchy.is_reserved(n["domain"]))
                    # 제목은 직무명이다. 접두는 GRAPH.md 감사 규칙(2026-09-24)이 금한다 —
                    # 여기서 요구하던 것을 뒤집었다([[vinc.canon.agent-roles §7]]).
                    self.assertTrue(n["title"].strip())
                    self.assertFalse(n["title"].lower().startswith("agent role:"))
                    props = n["props"]
                    for k in REQUIRED_PROPS:
                        self.assertIn(k, props)
                    self.assertIn(props["rung"], roles.RUNG_TO_MODEL)
                    self.assertIn(props["tier"],
                                  ("orchestrate", "execute", "verify", "deliver", "knowledge"))
                    self.assertEqual(sorted(n["tags"]), sorted(["agent", props["tier"], "role"]))
                    # one_liner 는 prompts/list 의 description 이 되고 mission 은 prompts/get 의
                    # description 이 된다 — 둘이 갈리면 고르는 화면과 계약이 다른 말을 한다.
                    self.assertEqual(n["one_liner"], props["mission"])
                    # reads·gates·paths 는 사용자의 것이다. 비워 두고 tool_note 가 그 말을 한다.
                    self.assertEqual((props["reads"], props["gates"], props["paths"], props["repos"]),
                                     ([], [], [], []))
                    self.assertTrue(props.get("tool_note"))

    def test_no_pack_ships_an_orchestrator(self):
        """결정: 오케스트레이터는 기본에도 분야 팩에도 없다. 그래프는 스케줄러가 아니고,
        어떤 역할이 어떤 순서로 도는지는 런타임의 일이다."""
        for p in PACKS:
            for n in _nodes(p):
                self.assertNotEqual(n["id"], "concept:agent-orchestrator", p.name)
                self.assertNotEqual(n["props"]["tier"], "orchestrate", (p.name, n["id"]))

    def test_role_ids_are_unique_across_all_packs(self):
        """같은 id 가 두 팩에 있으면 나중 설치가 앞 설치의 props 를 통째로 덮는다."""
        seen: dict[str, str] = {}
        for p in PACKS:
            for n in _nodes(p):
                self.assertNotIn(n["id"], seen, f"{n['id']} in {p.name} and {seen.get(n['id'])}")
                seen[n["id"]] = p.name

    def test_the_verify_tier_denies_the_edit_tools(self):
        for p in PACKS:
            for n in _nodes(p):
                if n["props"]["tier"] == "verify":
                    with self.subTest(pack=p.name, node=n["id"]):
                        self.assertTrue(EDIT_TOOLS <= set(n["props"].get("disallowed_tools") or []))

    def test_edges_carry_evidence_and_stay_inside_the_pack(self):
        for p in PACKS:
            pack = _load(p)
            ids = {n["id"] for n in pack["fragment"]["nodes"]}
            for e in pack["fragment"]["edges"]:
                with self.subTest(pack=p.name, edge=(e["from"], e["to"])):
                    self.assertIn(e["from"], ids)
                    self.assertIn(e["to"], ids)
                    self.assertTrue(e.get("evidence"))

    def test_every_pack_is_listed_as_an_official_pack(self):
        by_file = {e["file"]: e for e in MANIFEST}
        for p in PACKS:
            with self.subTest(pack=p.name):
                e = by_file.get(f"agents/{p.name}")
                self.assertIsNotNone(e, f"agents/{p.name} missing from official-packs.json")
                self.assertEqual(e["dominantDomain"], roles.DOMAIN)
                self.assertLessEqual(len(e["title"]), 120)
                self.assertLessEqual(len(e["description"]), 500)
        default = by_file["agents/default-agents.json"]
        # 계정 백엔드가 이 제목으로 공식 팩 행을 찾는다(provisionDefaults.ts).
        self.assertEqual(default["title"], "Default Agents")


class RenderedLikeThePromptSurface(unittest.TestCase):
    """스냅샷 렌더러(`render_shell`)가 곧 `prompts/get` 이다 — 팩 노드가 그 길을 지난다."""

    def test_every_role_renders_through_the_shared_renderer(self):
        for p in PACKS:
            nodes = _nodes(p)
            payload = roles.build_payload(nodes)
            self.assertEqual(len(payload["roles"]), len(nodes), p.name)
            for r in payload["roles"]:
                with self.subTest(pack=p.name, role=r["name"]):
                    text = roles.render_shell(r)
                    self.assertIn(f"name: {r['name']}", text)
                    self.assertIn(f"model: {roles.RUNG_TO_MODEL[r['props']['rung']]}", text)
                    self.assertIn("## Mission", text)
                    self.assertIn("## Output contract", text)
                    self.assertNotIn("—", text)    # 화면 노출 텍스트, em-dash 금지(D38)


class _FreshSpace(Bridge):
    """기본 팩만 들어간 새 스페이스를 답하는 가짜 사이드카."""

    def __init__(self, nodes):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:new")
        self.nodes = nodes
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, dict(params or {})))
        params = params or {}
        if path == "/roles":
            if params.get("role"):
                node = next((n for n in self.nodes if n["id"] == params["role"]), None)
                return {"executed": True, "found": node is not None,
                        "node": node, "peers": [], "relations": []}
            return {"executed": True, "roles": [
                {"id": n["id"], "title": n["title"], "domain": n["domain"],
                 "mission": n["one_liner"]} for n in self.nodes], "next_offset": None}
        if path == "/neighbors":
            if params.get("node") == f"domain:{roles.DOMAIN}":
                return {"executed": True, "found": True, "neighbors": [
                    {"rel": "IN_DOMAIN", "id": n["id"], "title": n["title"],
                     "one_liner": n["one_liner"], "domain": n["domain"]} for n in self.nodes]}
            return {"executed": True, "found": False, "neighbors": []}
        if path == "/graph":
            node = next((n for n in self.nodes if n["id"] == params.get("seed")), None)
            return {"executed": True, "found": bool(node), "nodes": [] if node is None else [
                {"id": node["id"], "label": node["title"], "kind": "concept",
                 "domain": node["domain"], "tags": node["tags"], "props": node["props"]}]}
        raise AssertionError(f"unexpected bridge call: {path}")


class DefaultPackOnTheWire(unittest.TestCase):
    def setUp(self):
        self.nodes = _nodes(DEFAULT)

    def test_a_fresh_space_lists_the_four_default_roles_as_prompts(self):
        got = roles.list_prompts(_FreshSpace(self.nodes))
        self.assertEqual([p["name"] for p in got["prompts"]],
                         ["agent-curator", "agent-researcher", "agent-reviewer", "agent-steward"])
        # 고르는 화면의 부제는 one_liner(=mission)다.
        by_name = {p["name"]: p["description"] for p in got["prompts"]}
        self.assertTrue(by_name["agent-steward"].startswith("Keep the graph honest"))

    def test_get_prompt_hydrates_a_default_role(self):
        got = roles.get_prompt("agent-reviewer", {}, _FreshSpace(self.nodes))
        text = got["messages"][0]["content"]["text"]
        self.assertTrue(got["description"].startswith("Check every claim"))
        self.assertIn("disallowedTools: Edit, Write, NotebookEdit", text)
        self.assertIn("## Never", text)
        self.assertIn("edit any node or document", text)


class DefaultPackAndTheFreeCap(unittest.TestCase):
    def test_the_default_pack_lands_in_a_graph_whose_topic_cap_is_full(self):
        """무료 그래프가 이미 다섯 주제라도 기본 팩은 들어간다 — 그 주제는 제품의 것이다."""
        full = {"work", "research", "home", "study", "family"}
        with mock.patch.object(query, "domain_names", lambda **kw: set(full)):
            denial = frag._domain_gate(_nodes(DEFAULT), store=object(), owner="user:new",
                                       max_domains=5)
        self.assertIsNone(denial)
        # 그리고 그 뒤에도 사용자의 다섯은 그대로 다섯이다: 여섯째 일반 주제만 막힌다.
        with mock.patch.object(query, "domain_names", lambda **kw: set(full) | {roles.DOMAIN}):
            denial = frag._domain_gate([{"id": "concept:x", "type": "concept", "domain": "sixth"}],
                                       store=object(), owner="user:new", max_domains=5)
        self.assertTrue(denial and denial["current"] == 5)


if __name__ == "__main__":
    unittest.main()
