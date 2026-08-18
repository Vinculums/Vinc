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
