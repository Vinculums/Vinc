"""R0 — 실행 매니페스트가 **비교 가능성**을 실제로 판정하는가.

왜 생겼나: 평가 산출물 둘을 비교해도 되는지 판단할 재료가 어느 출력에도 없었다.
`eval_rerank` 는 스토어 카운트 셋(docs·chunks·vecs)과 스레드 수를, `eval_search` 는
아무것도 안 실었다. 그래서 코퍼스가 다른 두 실행, split 스토어와 비-split 스토어,
서로 다른 CPU 의 수치가 같은 표에 올라도 아무도 몰랐다 — 실제로 같은 아티팩트가
러너 CPU 에 따라 147ms 와 271ms 로 갈렸다.

여기서 무는 것은 세 가지다: 코퍼스 지문이 **내용 변경에 반응하는가**, 같은 입력에
**결정론적인가**, 그리고 split 여부를 **벡터/청크 비로 판정하는가**.
근거: [[vinc.plan.retrieval-research-2026-09-08 §3]]
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_eval_search():
    """`scripts/` 는 패키지가 아니다 — 파일 경로로 든다(다른 테스트와 같은 관례)."""
    spec = importlib.util.spec_from_file_location(
        "eval_search_for_test", _ROOT / "scripts" / "eval_search.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeStore:
    """`store.run` 만 흉내 낸다 — 매니페스트가 읽는 것은 그 하나다."""

    def __init__(self, docs: list[tuple[str, str]], chunks: int, vecs: int):
        self._docs = docs
        self._chunks = chunks
        self._vecs = vecs
        self.closed = False

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        if "_DocMeta" in query and "content_hash" in query:
            owner = (params or {}).get("o")
            rows = [{"doc_id": d, "h": h} for d, h in self._docs]
            # owner 필터는 쿼리가 하지만 여기서는 전량을 준다(픽스처가 단일 owner).
            assert owner is None or isinstance(owner, str)
            return sorted(rows, key=lambda r: r["doc_id"])
        if "Chunk)" in query and "count" in query:
            return [{"c": self._chunks}]
        if "ChunkVec" in query and "count" in query:
            return [{"c": self._vecs}]
        raise AssertionError(f"예상 밖 쿼리: {query}")

    def close(self):
        self.closed = True


class RunManifestTests(unittest.TestCase):
    def setUp(self):
        self.es = _load_eval_search()

    def test_corpus_hash_changes_when_a_document_changes(self):
        """내용이 바뀌면 지문이 바뀐다. 이게 아니면 매니페스트는 장식이다."""
        a = _FakeStore([("d1", "h-one"), ("d2", "h-two")], chunks=10, vecs=21)
        b = _FakeStore([("d1", "h-one"), ("d2", "CHANGED")], chunks=10, vecs=21)
        ma = self.es.run_manifest(a, "user:local", "/tmp/x")
        mb = self.es.run_manifest(b, "user:local", "/tmp/x")
        self.assertIsNotNone(ma["corpus_sha256"])
        self.assertNotEqual(ma["corpus_sha256"], mb["corpus_sha256"])

    def test_corpus_hash_is_stable_across_calls(self):
        """같은 스토어를 두 번 재면 같은 값이다 — 아니면 비교 규칙이 성립 못 한다."""
        s = _FakeStore([("d2", "h2"), ("d1", "h1")], chunks=4, vecs=4)
        first = self.es.run_manifest(s, None, None)["corpus_sha256"]
        second = self.es.run_manifest(s, None, None)["corpus_sha256"]
        self.assertEqual(first, second)

    def test_a_document_set_of_different_size_hashes_differently(self):
        """문서가 빠진 스토어는 다른 코퍼스다(gold 누락 픽스처를 같은 것으로 읽지 않게)."""
        full = _FakeStore([("d1", "h1"), ("d2", "h2")], chunks=8, vecs=8)
        part = _FakeStore([("d1", "h1")], chunks=4, vecs=4)
        self.assertNotEqual(self.es.run_manifest(full, None, None)["corpus_sha256"],
                            self.es.run_manifest(part, None, None)["corpus_sha256"])
        self.assertEqual(self.es.run_manifest(full, None, None)["docs"], 2)

    def test_split_is_inferred_from_vectors_per_chunk(self):
        """비-split 은 정확히 1.0, split 은 그보다 크다(실측 ~2.08).

        `reindex --split` 없이 만든 스토어로 낸 품질 수치는 프로덕션 모양이 아니다.
        그 사실이 결과 JSON 에 안 실리면 다음 사람이 알 방법이 없다."""
        plain = self.es.run_manifest(_FakeStore([("d", "h")], chunks=100, vecs=100), None, None)
        split = self.es.run_manifest(_FakeStore([("d", "h")], chunks=100, vecs=208), None, None)
        self.assertIs(plain["split_inferred"], False)
        self.assertEqual(plain["vecs_per_chunk"], 1.0)
        self.assertIs(split["split_inferred"], True)
        self.assertEqual(split["vecs_per_chunk"], 2.08)

    def test_it_carries_the_keys_the_comparison_rule_names(self):
        """비교 규칙이 이름을 부르는 키가 실제로 다 실린다."""
        m = self.es.run_manifest(_FakeStore([("d", "h")], chunks=1, vecs=1), None, None)
        for k in ("corpus_sha256", "split_inferred", "docstore_sha256", "ladybug",
                  "onnxruntime", "python", "threads", "cpu", "code_rev", "docs",
                  "chunks", "vecs"):
            self.assertIn(k, m, f"매니페스트에 {k} 가 없다")
        self.assertIn("model", m["cpu"])
        self.assertIn("flags", m["cpu"])

    def test_a_broken_store_says_so_instead_of_hashing_nothing(self):
        """스토어가 못 답하면 `corpus_sha256` 은 None 이고 사유가 남는다.

        빈 코퍼스의 해시(=상수)를 정상 지문으로 읽으면 서로 다른 고장난 실행 둘이
        '같은 코퍼스'로 보인다 — 조용한 거짓 비교다."""
        class Dead(_FakeStore):
            def run(self, query, params=None):
                if "_DocMeta" in query:
                    raise RuntimeError("store is closed")
                return super().run(query, params)

        m = self.es.run_manifest(Dead([("d", "h")], chunks=1, vecs=1), None, None)
        self.assertIsNone(m["corpus_sha256"])
        self.assertIn("corpus_error", m)
        self.assertEqual(m["docs"], -1)


if __name__ == "__main__":
    unittest.main()
