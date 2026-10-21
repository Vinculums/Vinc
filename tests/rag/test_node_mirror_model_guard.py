# -*- coding: utf-8 -*-
"""노드 미러 모델 가드 — 미러가 질의와 다른 모델의 벡터를 담는 동안 노드 의미 팔을 끈다.

잠그는 것:
  1. 미러 라벨(`NodeVec.model`)이 질의 모델과 다르면 팔은 빈 목록이고 `node_semantic` 이
     `model_mismatch` 와 사유를 싣는다. 제목 팔의 어휘 결과는 그대로 남는다.
  2. 라벨이 같거나, 한쪽을 모르면(질의 모델 미지정·빈 라벨) 팔은 그대로 돈다.
  3. 대조는 소유자 범위다: 남의 미러 라벨이 내 팔을 끄지 않는다.
  4. 라벨은 캐시된다: 두 번째 검색은 미러를 다시 훑지 않고, 청크 벡터 쓰기도 캐시를 깨지
     않는다. 미러 재구축(새 모델로 교체)은 캐시를 깨고 다음 검색부터 팔이 다시 켜진다.
  5. 라벨을 못 읽으면 팔을 `degraded` 로 내린다(조용히 켜 두지 않는다).
  6. brief 도 같은 사실을 나른다.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from api import fragment as frag
from api import node_mirror
from api import query as q
from store import embedded_backend as eb
from vinc_rag import retrieval

_DRIVER, _NAME = eb._import_driver()
_SCAN = "RETURN DISTINCT v.model"
DIM = 4


def _vec(*xs):
    v = list(xs) + [0.0] * (DIM - len(xs))
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


class MismatchUnit(unittest.TestCase):
    """스토어 없이 도는 판정."""

    class _Store:
        def __init__(self, labels):
            self.labels = labels
            self.mirror_models: dict = {}
            self.calls = 0

        def run(self, _query, params=None):
            self.calls += 1
            if isinstance(self.labels, Exception):
                raise self.labels
            return [{"model": m} for m in self.labels]

    def test_no_query_model_never_blocks(self):
        store = self._Store(["old-model"])
        self.assertIsNone(retrieval._node_model_mismatch(store, "user:a", None))
        self.assertIsNone(retrieval._node_model_mismatch(store, "user:a", ""))
        self.assertEqual(store.calls, 0, "질의 모델을 모르면 미러를 훑을 이유도 없다")

    def test_an_unknown_label_does_not_block(self):
        store = self._Store(["", "new-model"])
        self.assertIsNone(retrieval._node_model_mismatch(store, "user:a", "new-model"))

    def test_any_other_label_blocks_and_names_it(self):
        store = self._Store(["new-model", "old-model", ""])
        self.assertEqual(retrieval._node_model_mismatch(store, "user:a", "new-model"),
                         {"query_model": "new-model", "mirror_models": ["old-model"]})

    def test_a_failed_scan_raises_a_fixed_code(self):
        store = self._Store(RuntimeError("boom"))
        with self.assertRaisesRegex(RuntimeError, "^node_vector_model_unavailable$"):
            retrieval._node_model_mismatch(store, "user:a", "new-model")

    def test_the_cache_is_per_owner_and_bounded(self):
        store = self._Store(["m"])
        for i in range(retrieval._RUNTIME_STAMP_OWNERS + 5):
            retrieval._mirror_models(store, f"user:{i}")
        self.assertLessEqual(len(store.mirror_models), retrieval._RUNTIME_STAMP_OWNERS)
        calls = store.calls
        retrieval._mirror_models(store, "user:hit")
        retrieval._mirror_models(store, "user:hit")
        self.assertEqual(store.calls, calls + 1, "같은 세대의 두 번째 읽기는 캐시가 답한다")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class MirrorModelGuardE2E(unittest.TestCase):
    OWNER = "user:mirror-model"
    OTHER = "user:mirror-model-other"
    QUERY = "nothing shares a word with this"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
                     "VINC_NODE_SEMANTIC_ARM": "on"}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=DIM)
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:mine", "type": "concept", "title": "mine"},
        ]}, store=self.store)
        frag.merge_fragment({"owner": self.OTHER, "nodes": [
            {"id": "concept:theirs", "type": "concept", "title": "theirs"},
        ]}, store=self.store)
        self._fill(self.OWNER, "concept:mine", "mine", "old-model")
        self._fill(self.OTHER, "concept:theirs", "theirs", "other-owner-model")
        self.scans = 0
        real = eb._Conn.execute

        def counting(conn, query, parameters=None):
            if isinstance(query, str) and _SCAN in query:
                self.scans += 1
            return real(conn, query, parameters)
        patcher = mock.patch.object(eb._Conn, "execute", counting)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _fill(self, owner, node_id, title, model):
        """미러를 실제 교체 경로(`node_mirror.replace`)로 채운다. 재구축과 같은 문장이 지난다."""
        node_mirror.replace(self.store, [{"id": node_id, "kind": "concept", "owner": owner,
                                          "title": title, "text": title, "emb": _vec(1, 0)}],
                            model)

    def _search(self, query_model, *, query=None):
        """허브 모양 — 요청마다 새 리더 뷰로 묻는다."""
        view = self.store.reader_view()
        try:
            return q.search(query or self.QUERY, owner=self.OWNER, query_embedding=_vec(1, 0),
                            query_model=query_model, top_k=5, store=view)
        finally:
            view.close()

    @staticmethod
    def _semantic_ids(res):
        return [h["id"] for h in res.get("node_hits") or [] if "semantic" in h.get("arms", [])]

    def test_another_models_mirror_keeps_the_arm_off_and_says_why(self):
        res = self._search("new-model")
        state = res["node_semantic"]
        self.assertEqual(state["state"], "model_mismatch")
        self.assertEqual(state["query_model"], "new-model")
        self.assertEqual(state["mirror_models"], ["old-model"], "남의 미러 라벨이 섞였다")
        self.assertIn("rebuilt", state["reason"])
        self.assertEqual(self._semantic_ids(res), [])
        self.assertNotIn("node_error", res)

    def test_lexical_matches_survive_the_mismatch(self):
        res = self._search("new-model", query="mine")
        self.assertEqual(res["node_semantic"]["state"], "model_mismatch")
        hit = next(h for h in res["node_hits"] if h["id"] == "concept:mine")
        self.assertEqual(hit["arms"], ["title"])

    def test_the_same_model_runs_the_arm(self):
        res = self._search("old-model")
        self.assertEqual(res["node_semantic"]["state"], "complete")
        self.assertEqual(self._semantic_ids(res), ["concept:mine"])

    def test_an_unnamed_query_model_runs_the_arm_as_before(self):
        res = self._search(None)
        self.assertEqual(res["node_semantic"]["state"], "complete")
        self.assertEqual(self._semantic_ids(res), ["concept:mine"])
        self.assertEqual(self.scans, 0, "질의 모델이 없으면 라벨을 읽지 않는다")

    def test_the_label_is_read_once_until_the_mirror_changes(self):
        self.assertEqual(self._search("new-model")["node_semantic"]["state"], "model_mismatch")
        self.assertEqual(self.scans, 1)
        self.assertEqual(self._search("new-model")["node_semantic"]["state"], "model_mismatch")
        self.assertEqual(self.scans, 1, "같은 미러를 다시 훑었다")
        # 청크 재임베딩 모양의 쓰기는 미러 세대를 올리지 않는다.
        gen = eb.node_vector_generation()
        self.store.run("MATCH (n:ChunkVec) WHERE n.owner_id = $o SET n.owner_id = $o",
                       {"o": self.OWNER})
        self.assertEqual(eb.node_vector_generation(), gen)
        self._search("new-model")
        self.assertEqual(self.scans, 1, "청크 벡터 쓰기가 미러 라벨 캐시를 깼다")
        # 재구축이 미러를 새 모델로 갈면 다음 검색부터 팔이 다시 켜진다.
        self._fill(self.OWNER, "concept:mine", "mine", "new-model")
        self.assertGreater(eb.node_vector_generation(), gen)
        res = self._search("new-model")
        self.assertEqual(self.scans, 2)
        self.assertEqual(res["node_semantic"]["state"], "complete")
        self.assertEqual(self._semantic_ids(res), ["concept:mine"])

    def test_an_unreadable_label_degrades_the_arm(self):
        with mock.patch.object(retrieval, "_mirror_models", side_effect=RuntimeError("x")):
            res = self._search("new-model")
        self.assertEqual(res["node_semantic"]["state"], "degraded")
        self.assertEqual(res["node_semantic"]["error"], "node_vector_model_unavailable")
        self.assertEqual(self._semantic_ids(res), [])

    def test_brief_carries_the_mismatch(self):
        out = q.brief(self.QUERY, owner=self.OWNER, query_embedding=_vec(1, 0),
                      query_model="new-model", store=self.store)
        self.assertEqual(out["node_semantic"]["state"], "model_mismatch")
        same = q.brief(self.QUERY, owner=self.OWNER, query_embedding=_vec(1, 0),
                       query_model="old-model", store=self.store)
        self.assertNotIn("node_semantic", same, "정상 응답의 모양이 바뀌었다")


if __name__ == "__main__":
    unittest.main()
