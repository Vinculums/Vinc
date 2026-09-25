# -*- coding: utf-8 -*-
"""벡터 색인 잔해 — 드롭이 WAL 에만 있는 채 죽으면 다음 기동이 무엇을 물려받는가.

허브는 2026-09-24 16:38Z 부터 이것으로 크래시 루프에 빠졌다. 드롭이 WAL 에서 재생될 때
확장이 아직 안 올라와 있어 저장층에 같은 이름의 '로드 안 된' 색인이 남고, 그 뒤로는
색인 생성이 `... is not loaded yet` 로 실패하며 `ChunkVec` 삽입은 **세그폴트**다.
근거: [[record:a-crash-between-a-vector-index-drop-and-the-next-checkpoint-leaves-a-remnant-2026-09-25]]

⚠ 잔해가 있는 표에는 **절대 삽입하지 않는다** — 테스트 프로세스가 통째로 죽는다.
잔해는 자식 프로세스가 만들고(드롭 뒤 체크포인트 없이 `os._exit`), 부모가 연다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from store.embedded_backend import VectorIndexRemnant, _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()
DIM = 4
ROWS = 60

# 자식: 벡터를 넣고 색인을 세워 체크포인트한 뒤, 드롭하고 **체크포인트 없이** 죽는다.
# RAW=1 은 드롭을 엔진에 직접 내린다(이 수정 전의 동작). RAW=0 은 백엔드의 드롭을 쓴다.
_CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from store.backend import open_store
from store.config import load_config
s = open_store(load_config({"data_dir": sys.argv[2]}))
s.provision(dim=%(dim)d)
for i in range(%(rows)d):
    s.run("CREATE (:ChunkVec {vec_id: $v, chunk_id: 'c', doc_id: 'd', owner_id: 'o', "
          "seq: $s, embedding: $e})",
          {"v": "c#%%03d" %% i, "s": i, "e": [float(i %% 7), float(i %% 5), 1.0, 2.0]})
s.ensure_search_indexes(dim=%(dim)d)
s.run("CHECKPOINT")
if sys.argv[3] == "1":
    s._load_ext("vector")
    s._conn.execute("CALL DROP_VECTOR_INDEX('ChunkVec','chunk_vec')")
else:
    s.drop_vector_index()
os._exit(0)
""" % {"dim": DIM, "rows": ROWS}


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class VectorIndexRemnantTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.dir = os.path.join(self._tmp, "store")
        self.store = None

    def tearDown(self):
        if self.store is not None:
            try:
                self.store.close()
            except Exception:
                pass

    def _crash_after_drop(self, *, raw: bool) -> None:
        r = subprocess.run([sys.executable, "-c", _CHILD, str(ROOT), self.dir, "1" if raw else "0"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])

    def _open(self):
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config({"data_dir": self.dir}))
        self.assertIsNotNone(self.store, "임베디드 스토어 open 실패")
        self.store.provision(dim=DIM)
        return self.store

    def _vector_hits(self) -> int:
        # 색인을 직접 친다. `vector_search` 는 자식을 부모 `Chunk` 로 되돌리는데 이 고정물에는
        # 부모가 없어서, 색인이 멀쩡해도 0 을 준다.
        self.store._load_ext("vector")
        r = self.store._conn.execute(
            "CALL QUERY_VECTOR_INDEX('ChunkVec','chunk_vec', "
            "CAST([1.0, 1.0, 1.0, 2.0] AS FLOAT[4]), 5) RETURN node.vec_id")
        n = 0
        while r.has_next():
            r.get_next()
            n += 1
        return n

    def test_crash_after_drop_leaves_a_remnant_that_blocks_the_index(self):
        """이 수정 전의 드롭(체크포인트 없음)으로 죽으면 잔해가 남는다. 확인되면 플래그가
        서고, 두 번째 시도는 빌드를 다시 돌리지 않고 곧바로 거절한다."""
        self._crash_after_drop(raw=True)
        s = self._open()
        with self.assertRaises(VectorIndexRemnant):
            s.ensure_search_indexes(dim=DIM)
        self.assertTrue(s.vector_index_remnant)
        t = time.monotonic()
        with self.assertRaises(VectorIndexRemnant):
            s.ensure_search_indexes(dim=DIM)
        self.assertLess(time.monotonic() - t, 1.0, "확인된 잔해 앞에서 빌드를 또 돌렸다")

    def test_apply_refuses_while_the_remnant_is_known(self):
        """잔해 앞에서 삽입은 세그폴트다 — 적용은 표를 건드리기 전에 거절한다."""
        from api import docstore
        self._crash_after_drop(raw=True)
        s = self._open()
        with self.assertRaises(VectorIndexRemnant):
            s.ensure_search_indexes(dim=DIM)
        res = docstore.apply_embeddings(s, "d", {"c": [[1.0, 1.0, 1.0, 1.0]]})
        self.assertEqual(res.get("error"), "vector_index_remnant")
        self.assertFalse(res.get("executed"))
        self.assertEqual(s.run("MATCH (v:ChunkVec) RETURN count(v) AS n")[0]["n"], ROWS)

    def test_reader_view_does_not_schedule_a_build_on_a_remnant(self):
        submitted = []
        self._crash_after_drop(raw=True)
        s = self._open()
        with self.assertRaises(VectorIndexRemnant):
            s.ensure_search_indexes(dim=DIM)
        rv = s.reader_view(writer=lambda label, fn, timeout: submitted.append(label))
        try:
            self.assertTrue(rv.vector_index_remnant)
            rv.schedule_search_indexes(dim=DIM)
        finally:
            rv.close()
        self.assertEqual(submitted, [], "실패할 빌드를 잡으로 걸었다")

    def test_heal_rebuilds_the_table_keeping_every_vector(self):
        """기동 수리: 표를 새로 떠서 옮기면 잔해가 사라지고 색인이 선다. 행은 그대로이고,
        그 뒤 쓰기 경로(드롭 → 삽입 → 재생성)가 정상이다."""
        self._crash_after_drop(raw=True)
        s = self._open()
        res = s.heal_vector_index(dim=DIM)
        self.assertEqual(res["state"], "repaired", res)
        self.assertEqual(res["rows"], ROWS)
        self.assertFalse(s.vector_index_remnant)
        self.assertGreater(self._vector_hits(), 0)
        s.drop_vector_index()
        s.run("CREATE (:ChunkVec {vec_id: 'n#000', chunk_id: 'n', doc_id: 'd', owner_id: 'o', "
              "seq: 0, embedding: $e})", {"e": [1.0, 1.0, 1.0, 1.0]})
        s.ensure_search_indexes(dim=DIM)
        self.assertEqual(s.run("MATCH (v:ChunkVec) RETURN count(v) AS n")[0]["n"], ROWS + 1)
        self.assertEqual(s.heal_vector_index(dim=DIM)["state"], "present")

    def test_heal_survives_reopen(self):
        self._crash_after_drop(raw=True)
        self._open().heal_vector_index(dim=DIM)
        self.store.close()
        s = self._open()
        self.assertEqual(s.heal_vector_index(dim=DIM)["state"], "present")
        self.assertGreater(self._vector_hits(), 0)

    def test_the_drop_checkpoints_so_a_crash_leaves_no_remnant(self):
        """예방: 백엔드의 드롭은 곧바로 체크포인트하므로 같은 자리에서 죽어도 잔해가 없다."""
        self._crash_after_drop(raw=False)
        s = self._open()
        built = s.ensure_search_indexes(dim=DIM)
        self.assertTrue(built["vector"])
        self.assertFalse(s.vector_index_remnant)

    def test_heal_builds_a_missing_index_on_a_healthy_store(self):
        self._crash_after_drop(raw=False)
        s = self._open()
        self.assertEqual(s.heal_vector_index(dim=DIM)["state"], "built")
        self.assertGreater(self._vector_hits(), 0)

    def test_heal_on_an_empty_store_does_nothing(self):
        s = self._open()
        self.assertEqual(s.heal_vector_index(dim=DIM)["state"], "empty")

    def test_heal_finishes_a_swap_that_died_after_the_drop(self):
        """수리가 옛 표를 버린 직후 죽었다: 기동의 프로비저닝이 빈 `ChunkVec` 을 다시 만들고,
        수리가 옮겨 둔 표로 교체를 마저 한다. 벡터는 하나도 잃지 않는다."""
        from store.embedded_schema import render_node_table
        self._crash_after_drop(raw=False)
        s = self._open()
        tmp = s._CHUNK_VEC_REBUILD
        s._conn.execute(render_node_table("ChunkVec", dim=DIM, as_label=tmp))
        s._conn.execute(f"COPY {tmp} FROM (MATCH (v:ChunkVec) RETURN v.vec_id, v.chunk_id, "
                        "v.doc_id, v.owner_id, v.seq, v.embedding)")
        s._conn.execute("DROP TABLE ChunkVec")
        s.close()
        s = self._open()
        self.assertEqual(s.run("MATCH (v:ChunkVec) RETURN count(v) AS n")[0]["n"], 0)
        res = s.heal_vector_index(dim=DIM)
        self.assertEqual(res["state"], "built", res)
        self.assertEqual(res["rows"], ROWS)
        self.assertFalse(s._has_table(tmp))

    def test_heal_drops_a_partial_copy_when_the_old_table_is_intact(self):
        from store.embedded_schema import render_node_table
        self._crash_after_drop(raw=False)
        s = self._open()
        tmp = s._CHUNK_VEC_REBUILD
        s._conn.execute(render_node_table("ChunkVec", dim=DIM, as_label=tmp))
        res = s.heal_vector_index(dim=DIM)
        self.assertEqual(res["rows"], ROWS)
        self.assertFalse(s._has_table(tmp))

    def test_server_boot_heals_before_serving(self):
        """`VincApp.open` 이 HTTP 를 띄우기 전에 수리한다 — 읽기와 겹치지 않는 유일한 때다."""
        from api.server import VincApp
        from store.config import load_config
        self._crash_after_drop(raw=True)
        app = VincApp.open(load_config({"data_dir": self.dir, "embedding_model": "local-en"}))
        try:
            self.store = None
            self.assertFalse(app.store.vector_index_remnant)
            self.assertTrue(app.store._has_index("ChunkVec", app.store._VEC_INDEX))
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main()
