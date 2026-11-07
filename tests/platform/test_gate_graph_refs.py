# -*- coding: utf-8 -*-
"""그래프 인용 게이트 — 모양 규칙과 리포 전체 대조.

왜 이 파일이 있나: 게이트가 **너무 많이 잡으면** 제품 자신의 위키링크 문법
(`[[doc_id]]` 자리표시자, 테스트 데이터)까지 빨갛게 만들어 곧 꺼지고, **너무 적게
잡으면** 애초에 없느니만 못하다. 그 경계가 `is_citation` 한 함수에 있다.

정본: [[vinc.canon.code-annotation]] §3.
"""
from __future__ import annotations

import unittest

from api.refs import is_not_graph_id
from scripts.gate_graph_refs import is_citation, main, normalize


class ShapeRules(unittest.TestCase):
    def test_document_and_node_ids_are_citations(self):
        for ref in ("vinc.spec.dataplane", "vinc.canon.code-annotation",
                    "record:some-fact", "decision:d45", "concept:chunk-ssot"):
            self.assertTrue(is_citation(ref), ref)

    def test_product_syntax_and_data_are_not(self):
        # 에디터 코드의 자리표시자·테스트 픽스처·메모리 슬러그. 잡으면 오탐이다.
        for ref in ("doc_id", "a", "1.0", "windows-embedded-test-fails",
                    "https://example.com"):
            self.assertFalse(is_citation(ref), ref)

    def test_id_shaped_tokens_that_are_not_graph_ids_are_reported(self):
        # 두 칸 구 문서 id 와 메모리 파일 이름. 예전에는 조용히 무시돼 아무것도 가리키지 않았다.
        for ref in ("vinc.arch", "vinc.mcp", "vinc-owner-isolation-sec1",
                    "windows-embedded-test-fails"):
            self.assertTrue(is_not_graph_id(ref), ref)

    def test_obvious_non_ids_stay_ignored(self):
        # 제품 위키링크 문법·배열·자리표시자·한국어는 인용이 아니다. 잡으면 게이트가 꺼진다.
        for ref in ("doc_id", "...", "1.0", "0.0, 1.0", "{ref}", "문서 id", "wikilinks",
                    "concept:a|Moved item", '"concept:a", "concept:b"', "doc:x", "a|display"):
            self.assertFalse(is_not_graph_id(ref), ref)
            self.assertFalse(is_citation(ref), ref)

    def test_anchor_is_stripped_like_a_section(self):
        self.assertEqual(normalize("vinc.mcp#§5"), "vinc.mcp")

    def test_a_retired_id_still_looks_like_a_citation(self):
        # 구 id 는 모양이 멀쩡하다 — 그래서 매니페스트 대조가 필요하다. 모양만으로는
        # `vinc.d.graph`(P5 이전 이름)를 걸러낼 수 없고, 걸러내서도 안 된다.
        self.assertTrue(is_citation("vinc.d.graph"))

    def test_section_marker_is_stripped_but_the_id_is_not(self):
        self.assertEqual(normalize("vinc.spec.dataplane §7"), "vinc.spec.dataplane")
        self.assertEqual(normalize("  vinc.spec.dataplane  "), "vinc.spec.dataplane")
        # 절 번호는 대조하지 않는다 — 문서 개정마다 코드가 빨개지면 안 된다.
        self.assertTrue(is_citation(normalize("vinc.spec.dataplane §3.2 표")))


class RepoIsClean(unittest.TestCase):
    def test_every_citation_in_the_repo_resolves(self):
        """CI 와 같은 검사를 로컬에서도 돌린다 — 게이트를 푸시 후에 처음 만나면
        고치는 자리가 이미 멀다."""
        self.assertEqual(main(), 0, "끊어진 그래프 인용이 있다(출력 참조)")


if __name__ == "__main__":
    unittest.main()


class ScanReach(unittest.TestCase):
    """스캔 대상 확장자가 좁아지면 **게이트는 계속 초록이면서** 검사만 줄어든다.
    그 조용한 후퇴가 실제로 있었다: 워크플로와 Terraform 의 인용 27건이
    2026-09-08 까지 한 번도 대조되지 않았다. 여기서 확장자별로 못 박는다."""

    def test_every_source_kind_that_carries_citations_is_scanned(self):
        import os
        import tempfile

        from scripts.gate_graph_refs import scan_repo

        names = ("a.py", "b.ts", "c.tsx", "d.rs", "e.md", "f.yml", "g.yaml", "h.tf")
        with tempfile.TemporaryDirectory() as root:
            for n in names:
                with open(os.path.join(root, n), "w", encoding="utf-8") as fh:
                    fh.write("# [[vinc.spec.dataplane]]" + os.linesep)
            # 스캔 밖이어야 하는 것 하나 — 넓히기가 전부를 삼키지 않았는지 본다.
            with open(os.path.join(root, "i.json"), "w", encoding="utf-8") as fh:
                fh.write('{"x": "[[vinc.spec.dataplane]]"}' + os.linesep)
            found = {rel for rel, _, _ in scan_repo(root)}
        self.assertEqual(found, set(names))


class ValidityBlocks(unittest.TestCase):
    """존재만 보던 게이트는 대체된 노드도 통과시켰다(2026-10-16 감사에서 9건·18자리).
    매니페스트의 `validity` 맵으로 오프라인에서 막는다."""

    def _run(self, manifest: dict, text: str):
        import io as _io
        import json
        import os
        import tempfile
        from contextlib import redirect_stdout

        from api import refs

        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "a.py"), "w", encoding="utf-8") as fh:
                fh.write(text + os.linesep)
            path = os.path.join(root, "m.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            out = _io.StringIO()
            with redirect_stdout(out):
                rc = refs.offline_check(manifest=path, roots=[root, root])
            product = refs.offline_result(manifest=path, roots=[root])
        return rc, out.getvalue(), product

    def test_superseded_node_is_blocked_with_its_successor(self):
        rc, out, product = self._run(
            {"docs": [], "nodes": ["decision:old"],
             "validity": {"decision:old": {"validity": "historical", "superseded_by": "decision:new"}}},
            "# [[" + "decision:old" + "]]")
        self.assertEqual(rc, 1)
        self.assertIn("decision:new", out)
        self.assertEqual(product["status"], 1)
        self.assertEqual(product["invalid"][0]["successor"], "decision:new")

    def test_historical_node_without_successor_is_blocked(self):
        rc, out, _ = self._run(
            {"docs": [], "nodes": ["record:old"], "validity": {"record:old": {"validity": "historical"}}},
            "# [[" + "record:old" + "]]")
        self.assertEqual(rc, 1)
        self.assertIn("historical", out)

    def test_current_node_passes(self):
        rc, _, product = self._run(
            {"docs": [], "nodes": ["record:x"], "validity": {"record:x": {"validity": "current"}}},
            "# [[" + "record:x" + "]]")
        self.assertEqual((rc, product["status"]), (0, 0))

    def test_a_manifest_without_validity_still_parses(self):
        rc, _, _ = self._run({"docs": ["vinc.spec.dataplane"], "nodes": []}, "# [[" + "vinc.spec.dataplane" + "]]")
        self.assertEqual(rc, 0)

    def test_a_legacy_id_is_blocked_even_when_nothing_else_is_wrong(self):
        rc, out, _ = self._run({"docs": [], "nodes": []}, "# [[" + "vinc.arch" + "]]")
        self.assertEqual(rc, 1)
        self.assertIn("not a graph id", out)

    def test_worktrees_are_not_scanned(self):
        from api.refs import SKIP_DIRS
        self.assertIn("_wt", SKIP_DIRS)


class LongCitations(unittest.TestCase):
    """괄호 안 길이에 상한이 있으면 그보다 긴 id 는 스캔에서 빠지고, 게이트는 그 인용을
    대조하지 않은 채 초록으로 지나간다. 120자를 넘는 id 도 모으고 대조하는지 못 박는다."""

    LONG = "record:" + "-".join(["a-citation-longer-than-the-old-cap"] * 5)

    def _run(self, manifest: dict):
        import io as _io
        import json
        import os
        import tempfile
        from contextlib import redirect_stdout

        from api import refs

        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "a.py")
            with open(source, "w", encoding="utf-8") as fh:
                fh.write("# 근거: [[" + self.LONG + " §2]]" + os.linesep)
            path = os.path.join(root, "m.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh)
            hits = refs.scan_file(source)
            out = _io.StringIO()
            with redirect_stdout(out):
                rc = refs.offline_check(manifest=path, roots=[root, root])
        return hits, rc, out.getvalue()

    def test_the_fixture_is_longer_than_the_old_cap(self):
        self.assertGreater(len(self.LONG), 120)
        self.assertTrue(is_citation(self.LONG))

    def test_a_long_id_is_collected(self):
        hits, _, _ = self._run({"docs": [], "nodes": [self.LONG]})
        self.assertEqual(hits, [(1, self.LONG)])

    def test_a_long_id_missing_from_the_manifest_blocks(self):
        _, rc, out = self._run({"docs": [], "nodes": []})
        self.assertEqual(rc, 1)
        self.assertIn(self.LONG, out)

    def test_a_long_id_that_was_superseded_blocks(self):
        _, rc, out = self._run({"docs": [], "nodes": [self.LONG], "validity": {
            self.LONG: {"validity": "historical", "superseded_by": "record:new"}}})
        self.assertEqual(rc, 1)
        self.assertIn("record:new", out)

    def test_a_long_id_in_the_manifest_passes(self):
        _, rc, _ = self._run({"docs": [], "nodes": [self.LONG]})
        self.assertEqual(rc, 0)
