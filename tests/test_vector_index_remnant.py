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
import _remnant_probe  # noqa: E402  (tests/ 가 sys.path 에 있다 — conftest 의 _tmpjail 과 같다)

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

    @_remnant_probe.needs("vector", _CHILD, "1")
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

    @_remnant_probe.needs("vector", _CHILD, "1")
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

    @_remnant_probe.needs("vector", _CHILD, "1")
    def test_every_write_to_the_table_is_refused_not_just_apply(self):
        """잔해 위에서는 삽입만이 아니라 삭제·아무 열의 SET 도 세그폴트다(실측). #407 은 적용만
        막았는데 문서 저장·삭제와 모델 전환도 `ChunkVec` 행을 지운다 — 백엔드가 문장을
        엔진에 보내기 전에 던진다. 읽기는 된다."""
        from api import docstore
        self._crash_after_drop(raw=True)
        s = self._open()
        with self.assertRaises(VectorIndexRemnant):
            s.ensure_search_indexes(dim=DIM)
        for q in ("MATCH (v:ChunkVec) WHERE v.doc_id = 'd' DETACH DELETE v",
                  "MATCH (v:ChunkVec) WHERE v.vec_id = 'c#001' SET v.doc_id = 'x'",
                  "CREATE (:ChunkVec {vec_id: 'n', chunk_id: 'c', doc_id: 'd', seq: 0})"):
            with self.assertRaises(VectorIndexRemnant, msg=q):
                s.run(q)
        res = docstore.switch_embed_model(s, "another-model")
        self.assertEqual(res.get("error"), "vector_index_remnant", res)
        self.assertEqual(s.run("MATCH (v:ChunkVec) RETURN count(v) AS n")[0]["n"], ROWS)

    @_remnant_probe.needs("vector", _CHILD, "1")
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

    @_remnant_probe.needs("vector", _CHILD, "1")
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

    @_remnant_probe.without("vector", _CHILD, "1")
    def test_this_engine_replays_a_raw_drop_without_a_remnant(self):
        """잔해가 안 생기는 엔진(0.20.4): 체크포인트 없는 드롭으로 죽어도 다음 기동이 색인을
        세우고 벡터가 그대로이며 `ChunkVec` 삽입이 된다."""
        self._crash_after_drop(raw=True)
        s = self._open()
        self.assertEqual(s.heal_vector_index(dim=DIM)["state"], "built")
        self.assertFalse(s.vector_index_remnant)
        self.assertEqual(s.run("MATCH (v:ChunkVec) RETURN count(v) AS n")[0]["n"], ROWS)
        self.assertGreater(self._vector_hits(), 0)
        s.drop_vector_index()
        s.run("CREATE (:ChunkVec {vec_id: 'n#000', chunk_id: 'n', doc_id: 'd', owner_id: 'o', "
              "seq: 0, embedding: $e})", {"e": [1.0, 1.0, 1.0, 1.0]})
        s.ensure_search_indexes(dim=DIM)
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

    def test_heal_on_an_empty_store_reports_empty(self):
        s = self._open()
        self.assertEqual(s.heal_vector_index(dim=DIM)["state"], "empty")
        self.assertEqual(s.heal_vector_index(dim=DIM)["state"], "empty")

    @_remnant_probe.needs("vector", _CHILD, "1")
    def test_heal_finds_a_remnant_on_an_empty_table(self):
        """행이 없어도 잔해는 남는다. #407 은 빈 표를 건너뛰어서 표시가 안 섰고, 그러면
        거절도 없이 첫 임베딩 적용이 세그폴트였다. 빈 표도 세워 보고, 잔해면 옮긴다."""
        child = (r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from store.backend import open_store
from store.config import load_config
s = open_store(load_config({"data_dir": sys.argv[2]}))
s.provision(dim=%d)
s.ensure_search_indexes(dim=%d)
s.run("CHECKPOINT")
s._load_ext("vector")
s._conn.execute("CALL DROP_VECTOR_INDEX('ChunkVec','chunk_vec')")
os._exit(0)
""" % (DIM, DIM))
        r = subprocess.run([sys.executable, "-c", child, str(ROOT), self.dir],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        s = self._open()
        res = s.heal_vector_index(dim=DIM)
        self.assertEqual(res["state"], "repaired", res)
        self.assertEqual(res["rows"], 0)
        self.assertFalse(s.vector_index_remnant)
        s.drop_vector_index()
        s.run("CREATE (:ChunkVec {vec_id: 'n#000', chunk_id: 'n', doc_id: 'd', owner_id: 'o', "
              "seq: 0, embedding: $e})", {"e": [1.0, 1.0, 1.0, 1.0]})
        s.ensure_search_indexes(dim=DIM)
        self.assertGreater(self._vector_hits(), 0)

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


# 노드 미러 자식: 미러를 채우고 색인을 세워 체크포인트한 뒤, 드롭하고 체크포인트 없이 죽는다.
_NODE_CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from store.backend import open_store
from store.config import load_config
s = open_store(load_config({"data_dir": sys.argv[2]}))
s.provision(dim=%(dim)d)
for i in range(%(rows)d):
    s.run("CREATE (:NodeVec {vec_id: $v, node_id: $n, kind: 'concept', owner_id: 'o', "
          "title: 't', text: 't', model: 'm', embedding: $e})",
          {"v": "o#n%%03d" %% i, "n": "n%%03d" %% i,
           "e": [float(i %% 7), float(i %% 5), 1.0, 2.0]})
assert s.create_node_vector_index() == ""
s.run("CHECKPOINT")
s._load_ext("vector")
s._conn.execute("CALL DROP_VECTOR_INDEX('NodeVec','node_vec')")
os._exit(0)
""" % {"dim": DIM, "rows": ROWS}


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class NodeMirrorRemnantTests(unittest.TestCase):
    """노드 미러(`NodeVec`)도 같은 잔해를 물려받는다. 배포 때의 재구축(`node_mirror.replace`)이
    고치지 못한다 — 옛 행을 지우고 새 행을 넣는데 잔해 위에서는 둘 다 세그폴트다(실측)."""

    def setUp(self):
        self.dir = os.path.join(tempfile.mkdtemp(), "store")
        self.store = None
        r = subprocess.run([sys.executable, "-c", _NODE_CHILD, str(ROOT), self.dir],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])

    def tearDown(self):
        if self.store is not None:
            try:
                self.store.close()
            except Exception:
                pass

    def _open(self):
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config({"data_dir": self.dir}))
        self.store.provision(dim=DIM)
        return self.store

    def _rows(self, s) -> int:
        return s.run("MATCH (n:NodeVec) RETURN count(n) AS n")[0]["n"]

    @_remnant_probe.needs("node", _NODE_CHILD)
    def test_the_mirror_rebuild_refuses_instead_of_crashing(self):
        from api import node_mirror
        s = self._open()
        reason = s.create_node_vector_index()
        self.assertIn("not loaded yet", reason)
        self.assertTrue(s.node_vector_index_remnant)
        self.assertFalse(s.vector_index_remnant)
        with self.assertRaises(VectorIndexRemnant):
            s.run("MATCH (n:NodeVec) WHERE n.owner_id IN ['o'] DELETE n")
        rows = [{"id": "x", "kind": "concept", "owner": "o", "title": "t", "text": "t",
                 "emb": [1.0, 2.0, 3.0, 4.0]}]
        res = node_mirror.replace(s, rows, "m")
        self.assertEqual(res.get("error"), "node_vector_index_remnant", res)
        self.assertEqual(self._rows(s), ROWS)

    @_remnant_probe.needs("node", _NODE_CHILD)
    def test_heal_moves_the_mirror_keeping_every_row(self):
        from api import node_mirror
        s = self._open()
        res = s.heal_node_vector_index(dim=DIM)
        self.assertEqual(res["state"], "repaired", res)
        self.assertEqual(res["rows"], ROWS)
        self.assertFalse(s.node_vector_index_remnant)
        self.assertFalse(s._has_table("NodeVecRebuild"))
        self.assertTrue(s.node_vector_search([1.0, 1.0, 1.0, 2.0], 3, owner="o"))
        rows = [{"id": "x", "kind": "concept", "owner": "o", "title": "t", "text": "t",
                 "emb": [1.0, 2.0, 3.0, 4.0]}]
        self.assertTrue(node_mirror.replace(s, rows, "m").get("index_ok"))
        self.assertEqual(self._rows(s), 1)
        self.assertEqual(s.heal_node_vector_index(dim=DIM)["state"], "present")

    @_remnant_probe.without("node", _NODE_CHILD)
    def test_this_engine_replays_the_mirror_drop_without_a_remnant(self):
        """잔해가 안 생기는 엔진(0.20.4): 미러 색인이 다시 서고 재구축이 된다."""
        from api import node_mirror
        s = self._open()
        self.assertEqual(s.create_node_vector_index(), "")
        self.assertFalse(s.node_vector_index_remnant)
        self.assertEqual(self._rows(s), ROWS)
        rows = [{"id": "x", "kind": "concept", "owner": "o", "title": "t", "text": "t",
                 "emb": [1.0, 2.0, 3.0, 4.0]}]
        self.assertTrue(node_mirror.replace(s, rows, "m").get("index_ok"))
        self.assertEqual(self._rows(s), 1)

    def test_server_boot_heals_the_mirror(self):
        from api.server import VincApp
        from store.config import load_config
        app = VincApp.open(load_config({"data_dir": self.dir, "embedding_model": "local-en"}))
        try:
            self.assertFalse(app.store.node_vector_index_remnant)
            self.assertTrue(app.store._has_index("NodeVec", app.store._NODE_VEC_INDEX))
            self.assertEqual(app.store.run("MATCH (n:NodeVec) RETURN count(n) AS n")[0]["n"],
                             ROWS)
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main()
