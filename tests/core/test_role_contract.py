# -*- coding: utf-8 -*-
"""사람이 직접 쓰는 Agent 역할 — 쓰기가 계약과 어긋난 곳을 **말하는가**.

정본: [[vinc.canon.agent-roles]]. 여기서 고정하는 것 셋:
  1. 어긋난 역할은 경고와 함께 **그대로 쓰인다**(거부하지 않는다).
  2. **보낸 것만 본다** — 도메인만 다시 보낸 호출에 "props 가 없다"고 말하지 않는다.
  3. 상수는 두 벌(api · vinc_mcp)이므로 둘이 같아야 한다.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment as frag  # noqa: E402
from api import graphmd  # noqa: E402
from api import role_contract as rc  # noqa: E402
from engines import domain_hierarchy  # noqa: E402
from vinc_mcp import roles  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass

PACKS = sorted((Path(__file__).resolve().parents[2] / "starter-packs" / "agents").glob("*.json"))


def good(**over):
    node = {
        "id": "concept:agent-release-manager", "type": "concept",
        "title": "Release manager", "domain": "vinc/agent",
        "one_liner": "Bump, changelog and tag readiness; a person publishes.",
        "tags": ["agent", "deliver", "role"],
        "props": {"mission": "Bump, changelog and tag readiness; a person publishes.",
                  "tier": "deliver", "rung": "fast",
                  "never": ["push a tag"], "person_only": ["publishing a release"]},
    }
    node.update(over)
    return node


class ConstantsMatchTheServer(unittest.TestCase):
    """두 벌이 갈리면 쓰기가 허락한 역할을 prompts/list 가 못 찾는다."""

    def test_domain_prefix_and_roster_concept(self):
        self.assertEqual(rc.DOMAIN, roles.DOMAIN)
        self.assertEqual(rc.ID_PREFIX, roles.ID_PREFIX)
        self.assertEqual(rc.ROSTER_CONCEPT, roles.ROSTER_CONCEPT)
        self.assertIn(rc.DOMAIN, domain_hierarchy.RESERVED_TOPICS)

    def test_rungs_are_the_ladder(self):
        self.assertEqual(set(rc.RUNGS), set(roles.RUNG_TO_MODEL))


class CheckSaysWhatIsWrong(unittest.TestCase):

    def test_a_good_role_is_silent(self):
        self.assertEqual(rc.check(good(), created=True), [])

    def test_one_level_below_the_topic_is_fine(self):
        self.assertEqual(rc.check(good(domain="vinc/agent/roles"), created=True), [])

    def test_role_topic_uses_the_write_paths_domain_normalization(self):
        for topic in (" / agent // release / ", "vinc // agent / roles"):
            with self.subTest(topic=topic):
                self.assertEqual(rc.check(good(domain=topic), created=True), [])
                self.assertTrue(rc.check(good(domain=topic, props={}), created=True))

    def test_the_top_level_agent_topic_is_the_new_home(self):
        # 새 역할의 자리는 `agent` 다. 옛 `vinc/agent` 도 그대로 받는다.
        # 근거: [[decision:agent-roles-live-under-a-top-level-agent-topic-2026-10-02]]
        self.assertEqual(rc.check(good(domain="agent"), created=True), [])
        self.assertEqual(rc.check(good(domain="agent/release"), created=True), [])
        self.assertEqual(rc.check(good(domain="agent/release/ops"), created=True), [])
        self.assertEqual(rc.check(good(domain="agents"), created=True), [])

    def test_the_roster_concept_and_other_ids_are_not_roles(self):
        self.assertEqual(rc.check({"id": rc.ROSTER_CONCEPT, "props": {}}, created=True), [])
        self.assertEqual(rc.check({"id": "concept:release", "props": {}}, created=True), [])

    def _one(self, node, created=True, needle=""):
        out = rc.check(node, created=created)
        self.assertEqual(len(out), 1, out)
        self.assertIn(needle, out[0])
        self.assertNotIn("—", out[0])     # 호스트 화면에 뜨는 문장이다(em-dash 금지)
        return out[0]

    def test_deep_topics_are_visible(self):
        self.assertEqual(rc.check(good(domain="vinc/agent/team/ops"), created=True), [])

    def test_outside_the_topic(self):
        for topic in ("agent-lab", "agents", "vinc/agent-lab", "vinc/engineering"):
            with self.subTest(topic=topic):
                self.assertEqual(rc.check(good(domain=topic, props={}), created=True), [])

    def test_created_without_a_domain(self):
        node = good()
        del node["domain"]
        self.assertEqual(rc.check(node, created=True), [])

    def test_the_title_prefix(self):
        self._one(good(title="Agent role: Release manager"), needle="prefix")

    def test_missing_required_props_are_named(self):
        node = good()
        node["props"] = {k: v for k, v in node["props"].items() if k not in ("never", "rung")}
        msg = self._one(node, needle="missing")
        self.assertIn("rung", msg)
        self.assertIn("never", msg)

    def test_created_without_props(self):
        node = good()
        del node["props"]
        self._one(node, needle="no props")

    def test_a_model_name_is_not_a_rung(self):
        node = good()
        node["props"] = {**node["props"], "rung": "opus"}
        self._one(node, needle="rung 'opus'")

    def test_unknown_tier(self):
        node = good()
        node["props"] = {**node["props"], "tier": "manager"}
        self._one(node, needle="tier 'manager'")

    def test_a_string_where_a_list_goes(self):
        node = good()
        node["props"] = {**node["props"], "never": "push a tag"}
        self._one(node, needle="must be a list")

    def test_one_liner_and_mission_drift(self):
        self._one(good(one_liner="Something else."), needle="differ")

    def test_bad_slug(self):
        self._one(good(id="concept:agent-Release_Manager"), needle="lowercase")


class OnlyWhatWasSentIsChecked(unittest.TestCase):
    """보내지 않은 필드는 보존된다 — 그러니 그것에 대해 말하면 거짓 경고다."""

    def test_a_domain_only_resend_of_an_existing_role(self):
        node = {"id": "concept:agent-release-manager", "type": "concept",
                "domain": "vinc/agent/roles"}
        self.assertEqual(rc.check(node, created=False), [])

    def test_the_same_stub_on_create_is_told(self):
        node = {"id": "concept:agent-release-manager", "type": "concept", "domain": "agent"}
        out = rc.check(node, created=True)
        self.assertTrue(any("no props" in w for w in out), out)

    def test_omitted_domain_uses_the_stored_topic_for_props_checks(self):
        node = {"id": "concept:agent-release-manager", "props": {}}
        for stored in ("agent", "vinc/agent/release"):
            with self.subTest(stored=stored):
                self.assertTrue(any("props is missing" in w for w in
                                    rc.check(node, created=False, stored_domain=stored)))
        self.assertEqual(rc.check(node, created=False, stored_domain="agent-lab"), [])

    def test_explicit_domain_takes_precedence_over_the_stored_topic(self):
        node = {"id": "concept:agent-release-manager", "props": {}, "domain": "agent-lab"}
        self.assertEqual(rc.check(node, created=False, stored_domain="agent"), [])
        node["domain"] = "agent"
        self.assertTrue(rc.check(node, created=False, stored_domain="agent-lab"))

    def test_null_domain_preserves_the_stored_topic_like_the_write_path(self):
        node = {"id": "concept:agent-release-manager", "props": {}, "domain": None}
        self.assertTrue(rc.check(node, created=False, stored_domain="agent"))
        self.assertEqual(rc.check(node, created=False, stored_domain="agent-lab"), [])


class OfficialPacksMeetTheContract(unittest.TestCase):
    """계약은 한 벌이다 — 공식 팩도 사람이 쓰는 역할과 같은 검사를 통과해야 한다."""

    def test_every_pack_role_is_silent(self):
        self.assertEqual(len(PACKS), 5)
        for p in PACKS:
            pack = json.loads(p.read_text(encoding="utf-8"))
            for n in pack["fragment"]["nodes"]:
                with self.subTest(pack=p.name, role=n["id"]):
                    self.assertEqual(rc.check(n, created=True), [])


class ConventionsAlwaysCarryRoles(unittest.TestCase):
    """Roles 절이 없는 GRAPH.md 도 읽을 때는 역할 계약을 싣는다(저장본은 그대로)."""

    def _file(self, lang="en"):
        tpl = graphmd._TPL_KO if lang == "ko" else graphmd._TPL_EN
        text = tpl.format(header=graphmd._HEADER[lang].format(v=graphmd.SPEC_VERSION, day="d"),
                          generated=graphmd.generated_block({"domains": ["vinc/x"]}, lang=lang))
        # Roles 절을 걷어 낸 옛 파일
        lines = text.splitlines()
        i = next(k for k, ln in enumerate(lines) if graphmd._is_heading(ln, "roles"))
        j = next(k for k in range(i + 1, len(lines)) if lines[k].startswith("## "))
        return "\n".join(lines[:i] + lines[j:]) + "\n"

    def test_a_file_that_has_it_is_untouched(self):
        full = graphmd._TPL_EN.format(header="h", generated="g")
        same, added = graphmd.with_roles_section(full)
        self.assertFalse(added)
        self.assertEqual(same, full)

    def test_default_roles_reserve_the_prefix_in_both_languages(self):
        self.assertIn("agent- prefix is reserved for roles", graphmd._TPL_EN)
        self.assertIn("agent- 접두사는 역할 전용", graphmd._TPL_KO)

    def test_missing_roles_is_added_in_order_and_still_checks(self):
        for lang in ("en", "ko"):
            with self.subTest(lang=lang):
                old = self._file(lang)
                self.assertFalse(any(graphmd._is_heading(ln, "roles") for ln in old.splitlines()))
                new, added = graphmd.with_roles_section(old)
                self.assertTrue(added)
                self.assertIn("concept:agent-", new)
                self.assertIn("person_only", new)
                heads = [k for ln in new.splitlines()
                         for k, _ in graphmd.SECTIONS if graphmd._is_heading(ln, k)]
                self.assertLess(heads.index("roles"), heads.index("topics"))
                res = graphmd.check(new)
                blocks = [f for f in res.get("findings", []) if f.get("level") == "BLOCK"]
                self.assertEqual(blocks, [], blocks)

    def test_a_hand_written_file_without_topics_gets_it_before_the_block(self):
        text = ("# GRAPH.md\n\n## What this is [this graph decides]\nx\n\n"
                + graphmd.generated_block({"domains": []}) + "\n\n## Do not\n- y\n")
        new, added = graphmd.with_roles_section(text)
        self.assertTrue(added)
        lines = new.splitlines()
        r = next(i for i, ln in enumerate(lines) if graphmd._is_heading(ln, "roles"))
        g = next(i for i, ln in enumerate(lines) if graphmd.GEN_START_RE.match(ln))
        self.assertLess(r, g)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ThroughTheRealWritePath(unittest.TestCase):
    """경고는 응답에 실리고, 노드는 그래도 쓰인다."""
    OWNER = "user:a"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())
        self.store.provision(dim=4)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _ingest(self, node):
        return frag.merge_fragment({"owner": self.OWNER, "nodes": [node]},
                                   store=self.store, read_back=True)

    def test_a_broken_role_is_written_and_warned(self):
        node = good(title="Agent role: Release manager", domain="vinc/agent/a/b")
        res = self._ingest(node)
        self.assertEqual(res["merged"]["nodes"]["created"], 1)
        text = " ".join(res.get("warnings") or [])
        self.assertIn("prefix", text)
        self.assertNotIn("one level", text)
        stored = {n["id"]: n for n in res.get("stored") or []}
        self.assertEqual(stored[node["id"]]["props"]["tier"], "deliver")

    def test_a_good_role_writes_clean_and_a_domain_resend_stays_clean(self):
        res = self._ingest(good())
        self.assertEqual(res.get("warnings") or [], [])
        res = self._ingest({"id": "concept:agent-release-manager", "type": "concept",
                            "domain": "vinc/agent/roles"})
        self.assertEqual(res.get("warnings") or [], [])

    def test_same_prefix_ordinary_concept_and_partial_updates_stay_clean(self):
        node = {"id": "concept:agent-lab-research-handoff-package", "type": "concept",
                "title": "Handoff package", "domain": "agent-lab", "props": {"question": "Q"}}
        for update in (node, {"id": node["id"], "type": "concept", "props": {"question": "Q2"}},
                       {"id": node["id"], "type": "concept", "domain": None, "title": "New title"}):
            with self.subTest(update=update):
                res = self._ingest(update)
                self.assertFalse(res.get("partial"), res)
                self.assertEqual(res.get("warnings") or [], [])
                self.assertEqual(res["stored"][0]["domain"], "agent-lab")

    def test_existing_role_props_update_without_domain_still_warns(self):
        self.assertFalse(self._ingest(good()).get("partial"))
        for domain in ({}, {"domain": None}):
            res = self._ingest({"id": "concept:agent-release-manager", "type": "concept",
                                "props": {}, **domain})
            self.assertTrue(res.get("partial"), res)
            self.assertTrue(any("props is missing" in w for w in res["warnings"]))
            self.assertEqual(res["stored"][0]["domain"], "vinc/agent")

    def test_explicit_topic_change_controls_role_validation(self):
        self._ingest(good())
        res = self._ingest({"id": "concept:agent-release-manager", "type": "concept",
                            "domain": "agent-lab", "props": {}})
        self.assertFalse(res.get("partial"), res)
        self.assertEqual(res["stored"][0]["domain"], "agent-lab")
        res = self._ingest({"id": "concept:agent-release-manager", "type": "concept",
                            "domain": "agent", "props": {}})
        self.assertTrue(any("props is missing" in w for w in res["warnings"]))
        self.assertEqual(res["stored"][0]["domain"], "agent")


if __name__ == "__main__":
    unittest.main()
