# -*- coding: utf-8 -*-
"""Concept.one_liner 쓰기 승격 — no-llm-chat 연구 §3.2/§9, Decision Q5(2026-08-11).

배경: `node_context`/Ask 가 인용하는 `one_liner` 열은 document-derived 경로
(`api.docstore.extract_doc`)만 채웠다. `vinc_ingest`(fragment 경로)는 그 열을 SET
한 적이 없어, MCP 로 적재한 개념은 문을 통해 들어왔는지에 따라 값이 있거나 없는
불일치 상태였다(오너 결정: fragment 경로에 승격 추가). Episode 의 date/summary
승격(F9, `test_episode_date.py`)과 정확히 같은 규칙을 Concept.one_liner 에 적용:
top-level 이 오면 그것이 이기고, 없으면 props.one_liner 또는 props.note 를 승격한다
(키가 아예 없을 때만 — 빈 문자열로 지우는 것까지 top-level 계약 그대로)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment as frag  # noqa: E402
from store import pkns  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import import_driver
    _DRIVER, _ = import_driver()
except Exception:
    pass


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ConceptOneLinerE2E(unittest.TestCase):
    OWNER = "user:a"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
        }.items():
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

    def _pk(self, nid: str) -> str:
        """PK 네임스페이싱이 켜졌을 때의 물리 키. 이 테스트는 API 를 거치지
        않고 스토어를 **직접** 조회하므로 물리 층의 말로 물어야 한다."""
        return pkns.key(self.OWNER, nid)

    def _row(self, cid: str) -> dict:
        rows = self.store.run(
            "MATCH (c:Concept) WHERE c.concept_id = $id "
            "RETURN c.one_liner AS ol, c.props AS props", {"id": self._pk(cid)})
        self.assertTrue(rows, f"{cid} 가 없다")
        return rows[0]

    def _ingest(self, node: dict) -> dict:
        return frag.merge_fragment({"owner": self.OWNER, "nodes": [node]},
                                   store=self.store)

    # ── R2: 쓴 것은 어떤 읽기 도구로든 읽혀야 한다 ───────────────────────────
    # one_liner 는 쓸 수는 있는데 어떤 MCP 읽기 도구로도 안 나왔다. 그래서 쓴 값이
    # 맞게 들어갔는지 어시스턴트가 **확인할 수 없었고**, 확인 못 하는 쓰기가 무엇을
    # 치르는지는 이 세션이 세 번 보여 줬다. 열은 Concept 에만 있으므로 나머지
    # 종류에서 빈 문자열인 것은 결손이 아니라 사실이다.
    def test_neighbors_가_one_liner_를_돌려준다(self):
        from api import query as q
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:seed", "type": "concept", "title": "S", "domain": "d",
             "one_liner": "씨앗의 한 줄."},
            {"id": "concept:other", "type": "concept", "title": "O", "domain": "d",
             "one_liner": "이웃의 한 줄."}],
            "edges": [{"from": "concept:seed", "to": "concept:other",
                       "type": "relates_to", "evidence": "둘은 이어져 있다"}]},
            store=self.store)
        out = q.neighbors(owner=self.OWNER, node="concept:seed", store=self.store)
        self.assertTrue(out["executed"] and out["neighbors"], out)
        got = {n["id"]: n for n in out["neighbors"]}
        self.assertIn("concept:other", got)
        self.assertEqual(got["concept:other"]["one_liner"], "이웃의 한 줄.")

    def test_graph_가_one_liner_를_돌려준다(self):
        from api import query as q
        self._ingest({"id": "concept:g1", "type": "concept", "title": "G",
                      "domain": "d", "one_liner": "그래프에서 읽히는 한 줄."})
        out = q.graph(owner=self.OWNER, seed="concept:g1", depth=1, store=self.store)
        self.assertTrue(out["executed"], out)
        node = next((n for n in out["nodes"] if n["id"] == "concept:g1"), None)
        self.assertIsNotNone(node, out.get("nodes"))
        self.assertEqual(node["one_liner"], "그래프에서 읽히는 한 줄.")

    def test_열이_없는_종류는_빈_문자열이지_오류가_아니다(self):
        """Decision·Record 에는 이 열이 없다. UNION 의 모양을 리터럴로 맞추지 않으면
        Binder 가 죽고 이웃 조회 전체가 함께 죽는다 — 이 파일이 pinned·content_time
        에서 이미 두 번 겪은 자리다."""
        from api import query as q
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:anchor", "type": "concept", "title": "A", "domain": "d"},
            {"id": "decision:d1", "type": "decision", "title": "D", "domain": "d"},
            {"id": "record:r1", "type": "record", "title": "R", "domain": "d"}],
            "edges": [
                {"from": "decision:d1", "to": "concept:anchor", "type": "about",
                 "evidence": "그 결정은 이것에 관한 것이다"},
                {"from": "record:r1", "to": "concept:anchor", "type": "relates_to",
                 "evidence": "그 기록은 이것에 붙는다"}]},
            store=self.store)
        out = q.neighbors(owner=self.OWNER, node="concept:anchor", store=self.store)
        self.assertTrue(out["executed"], out)          # 죽지 않는다
        for n in out["neighbors"]:
            self.assertIn("one_liner", n)              # 모양은 균일하다
            if n["id"] in ("decision:d1", "record:r1"):
                self.assertEqual(n["one_liner"], "")

    def test_props_의_one_liner_가_열로_승격된다(self):
        self._ingest({"id": "concept:p1", "type": "concept", "title": "t",
                      "props": {"one_liner": "핵심 정리 한 줄."}})
        row = self._row("concept:p1")
        self.assertEqual(row["ol"], "핵심 정리 한 줄.")
        # props 원본은 깎지 않는다.
        self.assertEqual(json.loads(row["props"]), {"one_liner": "핵심 정리 한 줄."})

    def test_props_의_note_도_one_liner_로_승격된다(self):
        """실 데이터에서 두 이름이 다 쓰인다(note·one_liner) — 둘 다 받는다."""
        self._ingest({"id": "concept:p2", "type": "concept", "title": "t",
                      "props": {"note": "note 로만 실린 경우."}})
        self.assertEqual(self._row("concept:p2")["ol"], "note 로만 실린 경우.")

    def test_one_liner_가_note_보다_이긴다(self):
        self._ingest({"id": "concept:p3", "type": "concept", "title": "t",
                      "props": {"one_liner": "먼저", "note": "나중"}})
        self.assertEqual(self._row("concept:p3")["ol"], "먼저")

    def test_top_level_이_props_보다_이긴다(self):
        self._ingest({"id": "concept:p4", "type": "concept", "title": "t",
                      "one_liner": "top",
                      "props": {"one_liner": "props"}})
        self.assertEqual(self._row("concept:p4")["ol"], "top")

    def test_props_에_one_liner_도_note_도_없으면_아무것도_지어내지_않는다(self):
        self._ingest({"id": "concept:p5", "type": "concept", "title": "t",
                      "props": {"domain": "x"}})
        self.assertFalse(self._row("concept:p5")["ol"])

    def test_공백뿐인_props_one_liner_는_승격하지_않는다(self):
        self._ingest({"id": "concept:p6", "type": "concept", "title": "t",
                      "props": {"one_liner": "   "}})
        self.assertFalse(self._row("concept:p6")["ol"])

    def test_문자열이_아닌_props_one_liner_는_승격하지_않는다(self):
        self._ingest({"id": "concept:p7", "type": "concept", "title": "t",
                      "props": {"one_liner": ["a", "b"]}})
        row = self._row("concept:p7")
        self.assertFalse(row["ol"])
        self.assertEqual(json.loads(row["props"]), {"one_liner": ["a", "b"]})  # 유실 아님


class FragmentSchemaContractTests(unittest.TestCase):
    """도구 계약에도 one_liner 가 있어야 한다 — 없으면 상류는 props 로만 되돌아간다."""

    def test_vinc_ingest_노드_스키마가_one_liner_를_선언한다(self):
        from vinc_mcp.tools import _FRAGMENT_SCHEMA
        props = _FRAGMENT_SCHEMA["properties"]["nodes"]["items"]["properties"]
        self.assertIn("one_liner", props)
        self.assertTrue(props["one_liner"].get("description"))


if __name__ == "__main__":
    unittest.main()
