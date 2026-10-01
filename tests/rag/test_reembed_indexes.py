# -*- coding: utf-8 -*-
"""`/doc/reembed` 는 큐에 되돌리기만 한다 · `build_search_indexes` 는 드롭과 짝으로만 쓴다.

**RV-02(2026-09-29)**: `/doc/reembed` 가 임베딩 스택을 올려 `Chunk.embedding` 에 고정 모델
(local-en 768)로 쓰고 needs_embed 를 0 으로 내렸다. v8 이후 검색은 `ChunkVec` 만 읽으므로
그 문서는 의미 검색에서 영구히 빠졌다. 이제 이 경로는 needs_embed=1 만 세우고 벡터는
워커에 맡긴다 — 임베딩 스택도, 인덱스 단계도 없다.

그 인덱스 단계가 2026-08-09 에 `build_search_indexes` 의 'already exists' 로 터졌던 회귀가
이 파일의 출발이다. 경로는 사라졌지만 **구조 가드**(`build` 를 부르는 프로덕션 모듈은
`drop_search_indexes` 도 불러야 한다)는 다른 호출부를 지키므로 남긴다.
"""
from __future__ import annotations

import re
import types
import unittest
from pathlib import Path

from api import server as srv

REPO = Path(__file__).resolve().parent.parent.parent

#: 실엔진이 던지는 문구 그대로(2026-08-09 docker 실측). 손으로 다듬지 않는다.
ALREADY_EXISTS = "Binder exception: Index chunk_ft already exists in table Chunk."


class _Store:
    """엔진 대역. 인덱스 표면을 부르면 기록하고, `_DocMeta` 쓰기를 모은다."""

    def __init__(self):
        self.calls: list[str] = []
        self.writes: list[str] = []

    def build_search_indexes(self, *, dim: int) -> None:
        self.calls.append("build")
        raise RuntimeError(ALREADY_EXISTS)

    def ensure_search_indexes(self, *, dim: int | None = None) -> dict:
        self.calls.append("ensure")
        return {"fts": False, "vector": False}

    def drop_vector_index(self) -> None:
        self.calls.append("drop_vector")

    def run(self, query: str, params: dict | None = None):
        if "_DocMeta" in query and "RETURN" in query:
            return [{"doc_id": "d1", "owner_id": "u1", "content_hash": "h",
                     "needs_embed": 0}]
        if "SET" in query:
            self.writes.append(query)
        return []


class ReembedRequeueTest(unittest.TestCase):

    def _app(self, store):
        app = srv.VincApp.__new__(srv.VincApp)
        app.store = store
        app.jobs = types.SimpleNamespace(run_sync=lambda _label, fn: fn())
        return app

    def test_reembed_only_requeues(self):
        """벡터·인덱스를 건드리지 않고 needs_embed=1 만 세운다."""
        s = _Store()
        res = self._app(s).write_doc_reembed({"owner": "u1", "id": "d1"})[1]
        self.assertTrue(res.get("executed"), res)
        self.assertEqual(res.get("queued"), 1, res)
        self.assertEqual(s.calls, [], "reembed 가 인덱스를 건드렸다")
        self.assertEqual(len(s.writes), 1, s.writes)
        self.assertIn("needs_embed = 1", s.writes[0])
        self.assertNotIn("embedding", s.writes[0].replace("needs_embed", ""))

    def test_no_targets_means_no_work(self):
        s = _Store()
        s.run = lambda q, p=None: []          # stale_docs 가 빈 목록
        res = self._app(s).write_doc_reembed({"owner": "u1"})[1]
        self.assertTrue(res.get("executed"), res)
        self.assertEqual((res.get("queued"), res.get("total")), (0, 0))
        self.assertEqual(s.calls, [])


class BuildContractGuard(unittest.TestCase):
    """`build_search_indexes` 는 `drop_search_indexes` 와 **짝으로만** 쓴다.

    이 가드가 2026-08-09 에 `embed_pipeline` 의 같은 결함을 찾아냈다.
    """

    #: 프로덕션 코드만 본다. 테스트는 빈 스토어에 짓는 게 정상이라 제외하고,
    #: 점으로 시작하는 디렉터리(`.claude/worktrees/*` · `.venv`)는 **이 트리의 코드가
    #: 아니다** — 워크트리에는 다른 시점의 사본이 산다. 실제로 첫 실행에서 옛 워크트리
    #: 둘이 걸렸고, 그건 가드가 도는 증거이지 이 트리의 위반이 아니었다.
    SKIP_DIRS = ("tests", "build", "dist", "node_modules")

    def test_no_production_caller_uses_build_without_drop(self):
        offenders = []
        for py in REPO.rglob("*.py"):
            rel = py.relative_to(REPO)
            if any(part in self.SKIP_DIRS or part.startswith(".") for part in rel.parts):
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # 정의 자체는 제외하고 **호출**만 센다.
            calls = re.findall(r"(?<!def )\bbuild_search_indexes\s*\(", text)
            if not calls:
                continue
            if "drop_search_indexes" not in text:
                offenders.append(str(rel).replace("\\", "/"))
        self.assertEqual(
            offenders, [],
            "build_search_indexes 는 'already exists' 를 흡수하지 않는다("
            f"실측: {ALREADY_EXISTS}). 드롭이 선행되지 않는 자리에서는 "
            "ensure_search_indexes 를 쓸 것. 위반: " + ", ".join(offenders))


if __name__ == "__main__":
    unittest.main()
