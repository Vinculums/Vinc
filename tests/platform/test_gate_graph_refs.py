# -*- coding: utf-8 -*-
"""그래프 인용 게이트 — 모양 규칙과 리포 전체 대조.

왜 이 파일이 있나: 게이트가 **너무 많이 잡으면** 제품 자신의 위키링크 문법
(`[[doc_id]]` 자리표시자, 테스트 데이터)까지 빨갛게 만들어 곧 꺼지고, **너무 적게
잡으면** 애초에 없느니만 못하다. 그 경계가 `is_citation` 한 함수에 있다.

정본: [[vinc.canon.code-annotation]] §3.
"""
from __future__ import annotations

import unittest

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
