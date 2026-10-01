# -*- coding: utf-8 -*-
"""FTS 색인 불일치 자기복구(2026-08-09) — 쓰기 경로가 실제로 복구하는지.

배경(실측). ladybug 0.18.0 의 FTS 증분 유지가 시간이 지나며 존재하는 행의 문서를
잃는다. 그 행을 지우면 엔진이 이렇게 던진다:

    FTS index 'chunk_ft' is inconsistent: document for node offset 992 is
    missing during delete. Drop and recreate the FTS index.

라이브 허브에서 실제로 났고(3번째), 스냅샷 사본으로 재현했으며, `DROP_FTS_INDEX` +
`CREATE_FTS_INDEX`(5.8초)로 사라졌다.

⚠ 이 테스트는 `_with_fts_repair` 를 **직접 부르지 않는다**. `delete_doc`·`put_doc`
을 통해 몬다 — 그래야 누가 감싸기를 풀었을 때 여기서 깨진다. 헬퍼만 검사하면
"헬퍼는 멀쩡한데 아무도 안 쓰는" 상태를 통과시킨다(도구 설명이 rpc 층에서 통째로
덮여 있던 것과 같은 종류의 사각이다).

FTS 확장은 Windows 휠에 없어 실엔진으로는 못 돈다. 그래서 **엔진 자리에 스텁**을
두되, 예외 문구는 라이브에서 받은 원문 그대로 쓴다.
"""
from __future__ import annotations

import unittest

from api import docstore
from store.embedded_backend import EmbeddedBackend

#: 라이브에서 받은 원문(2026-08-09). 손으로 다듬지 않는다 — 판별이 실제 문구에
#: 걸리는지가 요점이다.
LIVE_MSG = ("Runtime exception: FTS index 'chunk_ft' is inconsistent: document for "
            "node offset 992 is missing during delete. Drop and recreate the FTS index.")

_CHUNK_DELETE = "MATCH (c:Chunk) WHERE c.doc_id = $d DETACH DELETE c"


class _Store:
    """엔진 스텁. 청크 DETACH DELETE 에서 지정 횟수만큼 FTS 불일치를 던진다."""

    def __init__(self, *, fail_times: int = 1, msg: str = LIVE_MSG,
                 has_rebuild: bool = True):
        self.queries: list[str] = []
        self.rebuilds = 0
        self._left = fail_times
        self._msg = msg
        if not has_rebuild:                       # 구 백엔드 흉내
            del self.rebuild_fts_index

    # --- 백엔드가 노출하는 표면 -------------------------------------------------
    def rebuild_fts_index(self) -> None:
        self.rebuilds += 1

    def is_fts_inconsistent(self, exc: BaseException) -> bool:
        return EmbeddedBackend.is_fts_inconsistent(exc)

    def run(self, query: str, params: dict | None = None):
        self.queries.append(query)
        if query == _CHUNK_DELETE and self._left > 0:
            self._left -= 1
            raise RuntimeError(self._msg)
        if "count(c) AS c" in query:
            return [{"c": 3}]
        if "_DocMeta" in query and "RETURN" in query:
            return [{"doc_id": "d1", "owner_id": "u1", "content_hash": "h"}]
        if "count" in query.lower():
            return [{"c": 0}]
        return []


class _StoreNoRebuild(_Store):
    """`rebuild_fts_index` 가 없는 구 백엔드 — 재raise 되어야 한다."""

    rebuild_fts_index = None            # getattr 이 None 을 돌려주게 한다


class FtsRepairTest(unittest.TestCase):

    def test_delete_doc_rebuilds_and_retries_once(self):
        """불일치를 만나면 재빌드하고 재시도해서 **삭제가 성공한다**."""
        s = _Store(fail_times=1)
        res = docstore.delete_doc(s, "d1", tombstone=False)
        self.assertTrue(res.get("executed"), res)
        self.assertEqual(s.rebuilds, 1, "재빌드가 정확히 한 번 돌아야 한다")
        self.assertEqual(s.queries.count(_CHUNK_DELETE), 2, "실패 1 + 재시도 1")

    def test_retry_is_exactly_once_not_a_loop(self):
        """재빌드 후에도 같은 오류면 **그대로 올린다**. 무한 재시도 금지 —
        5.8초짜리 재빌드를 반복하면 잡 워커가 통째로 멎는다."""
        s = _Store(fail_times=99)
        with self.assertRaises(RuntimeError):
            docstore.delete_doc(s, "d1", tombstone=False)
        self.assertEqual(s.rebuilds, 1)
        self.assertEqual(s.queries.count(_CHUNK_DELETE), 2)

    def test_unrelated_error_does_not_trigger_rebuild(self):
        """조건을 좁게 잡는다. 아무 예외에나 재빌드를 걸면 진짜 결함이
        5.8초짜리 재시도에 덮인다."""
        s = _Store(fail_times=1, msg="Runtime exception: Buffer manager exception: OOM")
        with self.assertRaises(RuntimeError):
            docstore.delete_doc(s, "d1", tombstone=False)
        self.assertEqual(s.rebuilds, 0)
        self.assertEqual(s.queries.count(_CHUNK_DELETE), 1, "재시도하지 않는다")

    def test_backend_without_rebuild_reraises(self):
        """`rebuild_fts_index` 가 없는 백엔드에서는 조용히 삼키지 않고 올린다."""
        s = _StoreNoRebuild(fail_times=1)
        with self.assertRaises(RuntimeError):
            docstore.delete_doc(s, "d1", tombstone=False)

    def test_put_doc_delete_path_is_also_wrapped(self):
        """재청킹의 구 청크 삭제도 같은 경로다 — 여기만 빠지면 문서 **갱신**이
        같은 오류로 죽는다(삭제보다 훨씬 자주 도는 경로다)."""
        s = _Store(fail_times=1)
        res = docstore.put_doc(s, owner="u1", text="hello world\n\nsecond block",
                               doc_id="d1", title="t")
        self.assertTrue(res.get("executed") or res.get("chunks"), res)
        self.assertEqual(s.rebuilds, 1)


class MarkerTest(unittest.TestCase):
    """판별 표지 자체 — 실제 문구는 잡고 이웃 오류는 안 잡아야 한다."""

    def test_live_message_is_detected(self):
        self.assertTrue(EmbeddedBackend.is_fts_inconsistent(RuntimeError(LIVE_MSG)))

    def test_index_missing_is_not_this_condition(self):
        """`ensure_search_indexes` 가 맡는 **색인 부재**는 다른 조건이다.
        여기서 참이 되면 부재를 만날 때마다 전량 재빌드를 돌게 된다."""
        for m in ("Table Chunk doesn't have an index with name chunk_ft",
                  "Table Chunk does not exist",
                  "Runtime exception: Node(nodeOffset: 992) has connected edges in "
                  "table MENTIONS in the fwd direction, which cannot be deleted."):
            with self.subTest(m=m[:40]):
                self.assertFalse(EmbeddedBackend.is_fts_inconsistent(RuntimeError(m)))

    def test_both_marks_required(self):
        """한쪽 표지만으로는 안 잡는다 — 색인명·오프셋은 매번 다르므로 문구 전체를
        쓸 수 없고, 한 표지만 보면 무관한 오류를 물어 엉뚱하게 재빌드한다."""
        self.assertFalse(EmbeddedBackend.is_fts_inconsistent(
            RuntimeError("FTS index 'chunk_ft' query failed")))
        self.assertFalse(EmbeddedBackend.is_fts_inconsistent(
            RuntimeError("catalog is inconsistent")))


if __name__ == "__main__":
    unittest.main()
