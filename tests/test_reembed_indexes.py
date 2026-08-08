# -*- coding: utf-8 -*-
"""`/doc/reembed` 가 마지막 인덱스 단계에서 터지던 회귀(2026-08-09).

**실측(linux/amd64 · ladybug 0.18.0 · 리포의 실제 스키마와 EmbeddedBackend)**:

    1) 빈 스토어에 build_search_indexes          → 성공
    2) 벡터만 드롭하고 build_search_indexes      → Binder exception:
                                                   Index chunk_ft already exists in table Chunk.
    3) 같은 상태에서 ensure_search_indexes       → 성공 {'fts': False, 'vector': False}
    4) 아무것도 안 드롭하고 build                → Index chunk_vec already exists (벡터가 먼저)

2)가 `/doc/reembed` 가 실제로 밟던 상태다 — `reembed_doc` 이 `_drop_vector_index_if_any`
로 **벡터만** 드롭하기 때문(FTS 를 쓰기마다 드롭했더니 임베딩 도는 동안 키워드 검색
성공률이 0이었던 2026-07-31 실측 때문에 그렇게 돼 있다). 그리고 예외가 FTS 에서
나므로 CREATE_VECTOR_INDEX 는 이미 성공한 뒤다 — **임베딩도 벡터 색인도 다 만들어
놓고 결과만 오류로 돌려주고 있었다.**

4)는 "FTS 만 흡수하면 된다"를 기각한다. 그래서 고침은 흡수가 아니라 **호출부를
`ensure_search_indexes` 로** 바꾸는 쪽이다.

이 파일은 두 가지를 못박는다:

- 행동: `write_doc_reembed` 를 실제로 몰아서, 인덱스가 이미 있는 스토어에서도
  **executed=True** 로 끝나는지. 헬퍼를 직접 부르지 않는다 — 그러면 "헬퍼는 멀쩡한데
  아무도 안 쓰는" 상태를 통과시킨다.
- 구조: `build_search_indexes` 를 부르는 프로덕션 모듈은 `drop_search_indexes` 도
  불러야 한다(그 메서드의 계약). 이 가드가 없었으면 `embed_pipeline` 의 같은 결함을
  못 찾았을 것이다 — 실제로 이번에 그렇게 찾았다.
"""
from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

from api import server as srv

REPO = Path(__file__).resolve().parent.parent

#: 실엔진이 던지는 문구 그대로(2026-08-09 docker 실측). 손으로 다듬지 않는다.
ALREADY_EXISTS = "Binder exception: Index chunk_ft already exists in table Chunk."


class _Store:
    """엔진 대역. **인덱스가 이미 있는** 스토어를 흉내 낸다.

    `build_search_indexes` 는 실엔진처럼 던지고, `ensure_search_indexes` 는 실엔진처럼
    흡수한다(위 실측 2·3). 여기서 검사하는 것은 핸들러가 **어느 쪽을 부르는가**다.
    """

    def __init__(self):
        self.calls: list[str] = []
        self.ensure_dims: list[int] = []
        self.dropped_vector = 0

    # --- 인덱스 표면 ---------------------------------------------------------
    def build_search_indexes(self, *, dim: int) -> None:
        self.calls.append("build")
        raise RuntimeError(ALREADY_EXISTS)

    def ensure_search_indexes(self, *, dim: int | None = None) -> dict:
        self.calls.append("ensure")
        self.ensure_dims.append(dim)
        return {"fts": False, "vector": False}

    def drop_vector_index(self) -> None:
        self.dropped_vector += 1

    # --- docstore 가 쓰는 최소 표면 -------------------------------------------
    def run(self, query: str, params: dict | None = None):
        if "_DocMeta" in query and "RETURN" in query:
            return [{"doc_id": "d1", "owner_id": "u1", "content_hash": "h",
                     "needs_embed": 1}]
        if "c.chunk_id AS id" in query:
            return [{"id": "d1#00000", "text": "본문"}]
        return []


def _fake_embedding_stack():
    """핸들러가 임베딩 스택 부재로 **조기 강등**하지 않게 최소 대역을 심는다.

    강등 자체는 별도 회귀 테스트(test_server.test_reembed_without_stack_...)가 이미
    지킨다. 여기서 보려는 것은 그 **뒤** 단계다."""
    chonkie = types.ModuleType("chonkie")
    ctypes = types.ModuleType("chonkie.types")

    class _Chunk:
        def __init__(self, text="", start_index=0, end_index=0, token_count=0):
            self.text = text
            self.embedding = [0.1, 0.2, 0.3, 0.4]

    class _Refinery:
        def __init__(self, embedding_model=None):
            pass

        def __call__(self, chunks):
            return chunks

    chonkie.EmbeddingsRefinery = _Refinery
    ctypes.Chunk = _Chunk
    chonkie.types = ctypes
    return {"chonkie": chonkie, "chonkie.types": ctypes}


class ReembedIndexTest(unittest.TestCase):

    def setUp(self):
        from engines import embed_pipeline as ep
        self._saved_mods = {k: sys.modules.get(k) for k in ("chonkie", "chonkie.types")}
        sys.modules.update(_fake_embedding_stack())
        self._ep = ep
        self._saved_build = ep.build_embeddings_model
        ep.build_embeddings_model = lambda *_a, **_k: object()

    def tearDown(self):
        self._ep.build_embeddings_model = self._saved_build
        for k, v in self._saved_mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def _app(self, store):
        app = srv.VincApp.__new__(srv.VincApp)
        app.store = store
        app.jobs = types.SimpleNamespace(run_sync=lambda _label, fn: fn())
        return app

    def test_reembed_succeeds_when_indexes_already_exist(self):
        """회귀 본체 — 인덱스가 살아 있는 스토어에서도 잡이 성공으로 끝나야 한다.

        고침 전에는 여기서 `executed=False` + `Binder exception` 이 나왔다."""
        s = _Store()
        res = self._app(s).write_doc_reembed({"owner": "u1", "id": "d1"})[1]
        self.assertTrue(res.get("executed"), f"인덱스 단계에서 죽었다: {res}")
        self.assertNotIn("build", s.calls,
                         "build_search_indexes 는 'already exists' 를 흡수하지 않는다 "
                         "— 벡터만 드롭하는 이 경로에서는 항상 터진다")
        self.assertIn("ensure", s.calls, "인덱스 보장 단계 자체가 사라지면 안 된다")

    def test_index_step_gets_the_model_dimension(self):
        """차원을 안 넘기면 벡터 색인이 안 만들어진다 — 방금 채운 임베딩이
        검색에서 안 잡힌다(에러 없이 조용히)."""
        from engines.embed_pipeline import model_dim
        s = _Store()
        self._app(s).write_doc_reembed({"owner": "u1", "id": "d1"})
        self.assertEqual(s.ensure_dims, [model_dim("local-en")])

    def test_no_targets_means_no_index_work(self):
        """대상이 없으면 인덱스를 건드리지 않는다(불필요한 DDL 금지)."""
        s = _Store()
        s.run = lambda q, p=None: []          # stale_docs 가 빈 목록
        res = self._app(s).write_doc_reembed({"owner": "u1"})[1]
        self.assertTrue(res.get("executed"), res)
        self.assertEqual(s.calls, [])


class BuildContractGuard(unittest.TestCase):
    """`build_search_indexes` 는 `drop_search_indexes` 와 **짝으로만** 쓴다.

    이 가드가 이번에 `embed_pipeline` 의 같은 결함을 찾아냈다. 행동 테스트는
    `/doc/reembed` 하나만 덮으므로, 같은 함정을 밟는 **다른** 호출부는 못 잡는다.
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
