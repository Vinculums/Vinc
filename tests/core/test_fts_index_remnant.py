# -*- coding: utf-8 -*-
"""FTS 색인 잔해 — `Chunk` 의 FTS 드롭이 WAL 에만 있는 채 죽으면 다음 기동이 무엇을 물려받는가.

벡터 잔해(`test_vector_index_remnant.py`)와 같은 결함이 FTS 에도 있다. 드롭이 WAL 에서
재생될 때 확장이 아직 안 올라와 있어 `Chunk` 저장층에 같은 이름의 '로드 안 된' 색인이 남고,
그 뒤로는 FTS 생성이 `... is not loaded yet` 로 실패하며 `Chunk` 의 행 쓰기가 **전부**
세그폴트다 — 삽입·삭제·아무 열의 SET 까지. 즉 문서 저장·삭제가 전부 컨테이너를 죽인다.
`Chunk` 에는 `MENTIONS` 가 걸려 있어서 `ChunkVec` 처럼 표만 떠서 옮길 수 없다 — 수리는
관계 표까지 함께 옮기고 교체를 한 트랜잭션으로 한다.
근거: [[record:an-fts-remnant-on-chunk-stops-every-document-write-and-the-heal-moves-chunk-with-its-edges-2026-09-25]]

⚠ 잔해가 있는 표에는 **절대 쓰지 않는다** — 테스트 프로세스가 통째로 죽는다. 잔해는
자식 프로세스가 만들고(드롭 뒤 체크포인트 없이 `os._exit`), 부모가 연다. 쓰기 거절을
확인하는 테스트는 백엔드가 문장을 엔진에 보내기 **전에** 던지는지를 본다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from store.embedded_backend import FtsIndexRemnant, import_driver  # noqa: E402
import _remnant_probe  # noqa: E402  (tests/ 가 sys.path 에 있다 — conftest 의 _tmpjail 과 같다)

_DRIVER, _NAME = import_driver()
DIM = 4
DOCS = 6
MAX_CHARS = 60

# 자식: 문서를 넣고(청크 여럿), 청크마다 개념으로 MENTIONS, 반은 결정으로도 잇는다. FTS 를
# 세워 체크포인트한 뒤 드롭하고 **체크포인트 없이** 죽는다.
# MODE=raw 는 드롭을 엔진에 직접 내린다(이 수정 전의 동작), MODE=backend 는 백엔드의 드롭.
_CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from store.backend import open_store
from store.config import load_config
from api import docstore
s = open_store(load_config({"data_dir": sys.argv[2]}))
s.provision(dim=%(dim)d)
for i in range(%(docs)d):
    r = docstore.put_doc(s, owner="o", doc_id="d%%02d" %% i, max_chars=%(max_chars)d,
                         text=("alpha beta gamma delta number %%d. " %% i) * 4)
    assert r.get("executed"), r
s.run("CREATE (:Concept {concept_id: 'k1', owner_id: 'o', title: 'alpha'})")
s.run("CREATE (:Decision {decision_id: 'x1', owner_id: 'o', title: 'beta'})")
s.run("MATCH (c:Chunk), (k:Concept {concept_id: 'k1'}) CREATE (c)-[:MENTIONS {method: 'title'}]->(k)")
s.run("MATCH (c:Chunk), (x:Decision {decision_id: 'x1'}) WHERE c.chunk_index = 0 "
      "CREATE (c)-[:MENTIONS {method: 'stem'}]->(x)")
s.ensure_search_indexes()
s.run("CHECKPOINT")
if sys.argv[3] == "raw":
    s._load_ext("fts")
    s._conn.execute("CALL DROP_FTS_INDEX('Chunk','chunk_ft')")
else:
    s.drop_search_indexes()
os._exit(0)
""" % {"dim": DIM, "docs": DOCS, "max_chars": MAX_CHARS}

# 자식: 잔해가 있는 스토어에서 수리를 돌리다 교체 트랜잭션의 한 지점에서 죽는다.
# DIE=in_tx 는 트랜잭션 안(DROP 은 했고 RENAME 전), DIE=after_commit 은 커밋 뒤·체크포인트 전.
_HEAL_AND_DIE = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from store.backend import open_store
from store.config import load_config
s = open_store(load_config({"data_dir": sys.argv[2]}))
s.provision(dim=%(dim)d)
die = sys.argv[3]

class _Dying:
    def __init__(self, conn):
        self._c, self._committed = conn, False
    def execute(self, q, *a):
        if die == "in_tx" and q.startswith("ALTER TABLE") and "RENAME" in q:
            os._exit(0)
        if die == "after_commit" and self._committed and q == "CHECKPOINT":
            os._exit(0)
        r = self._c.execute(q, *a)
        if q == "COMMIT":
            self._committed = True
        return r
    def __getattr__(self, name):
        return getattr(self._c, name)

s._conn = _Dying(s._conn)
s.heal_fts_index(dim=%(dim)d)
os._exit(3)        # 여기까지 오면 죽을 자리를 못 지난 것이다
""" % {"dim": DIM}


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class FtsIndexRemnantTests(unittest.TestCase):
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

    def _child(self, code: str, *args: str, rc: int = 0) -> None:
        r = subprocess.run([sys.executable, "-c", code, str(ROOT), self.dir, *args],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        self.assertEqual(r.returncode, rc, r.stderr[-2000:])

    def _crash_after_drop(self, *, raw: bool = True) -> None:
        self._child(_CHILD, "raw" if raw else "backend")

    def _open(self):
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config({"data_dir": self.dir}))
        self.assertIsNotNone(self.store, "임베디드 스토어 open 실패")
        self.store.provision(dim=DIM)
        return self.store

    def _detect(self):
        s = self._open()
        with self.assertRaises(FtsIndexRemnant):
            s.ensure_search_indexes()
        return s

    def _shape(self, s) -> dict:
        """행과 엣지 — 수리 전후가 같아야 하는 것."""
        def n(q):
            return s.run(q)[0]["n"]
        return {
            "chunks": n("MATCH (c:Chunk) RETURN count(c) AS n"),
            "to_concept": n("MATCH (:Chunk)-[m:MENTIONS]->(:Concept) RETURN count(m) AS n"),
            "to_decision": n("MATCH (:Chunk)-[m:MENTIONS]->(:Decision) RETURN count(m) AS n"),
            "texts": sorted(r["t"] for r in s.run("MATCH (c:Chunk) RETURN c.text AS t")),
            "methods": sorted(r["m"] for r in s.run(
                "MATCH (:Chunk)-[m:MENTIONS]->() RETURN m.method AS m")),
        }

    def _fts_hits(self, s, term: str = "number") -> int:
        return len(s.fulltext_search(term, 50))

    @_remnant_probe.needs("fts", _CHILD, "raw")
    def test_crash_after_drop_leaves_a_remnant_that_blocks_the_index(self):
        """이 수정 전의 드롭(체크포인트 없음)으로 죽으면 잔해가 남는다. 확인되면 플래그가
        서고, 두 번째 시도는 빌드를 다시 돌리지 않고 곧바로 거절한다."""
        self._crash_after_drop()
        s = self._detect()
        self.assertTrue(s.fts_index_remnant)
        self.assertFalse(s.vector_index_remnant)
        t = time.monotonic()
        with self.assertRaises(FtsIndexRemnant):
            s.ensure_search_indexes()
        self.assertLess(time.monotonic() - t, 1.0, "확인된 잔해 앞에서 빌드를 또 돌렸다")

    @_remnant_probe.needs("fts", _CHILD, "raw")
    def test_a_failed_create_does_not_make_the_next_attempt_look_present(self):
        """FTS 생성은 잔해 앞에서 실패하면서도 내부 표를 남기고, 그 뒤의 시도는 문구가
        바뀐다("Table: 8_chunk_ft_docs already exists ..."). 'already exists' 로만 가르면
        그걸 '이미 있음'으로 읽어 기동 수리가 잔해를 두고 `present` 라고 답했다(실측).
        앞선 프로세스가 한 번 실패한 뒤에 여는 것 — 허브에서는 검색이 그 한 번을 한다."""
        self._crash_after_drop()
        self._detect()
        self.store.close()
        s = self._detect()                     # 새 프로세스의 둘째 시도도 잔해다
        self.assertTrue(s.fts_index_remnant)
        self.assertEqual(s.heal_fts_index(dim=DIM)["state"], "repaired")
        self.assertGreater(self._fts_hits(s), 0)

    @_remnant_probe.needs("fts", _CHILD, "raw")
    def test_writes_to_chunk_are_refused_before_they_reach_the_engine(self):
        """잔해 앞에서 `Chunk` 쓰기는 세그폴트다 — 백엔드가 문장을 보내기 전에 던진다.
        읽기와 다른 표의 쓰기는 그대로 된다. 문서 경로는 쓰기 **전에** 구조화된 사유로 선다."""
        from api import docstore
        self._crash_after_drop()
        s = self._detect()
        before = self._shape(s)
        for q in ("CREATE (:Chunk {chunk_id: 'new', doc_id: 'd', text: 'x'})",
                  "MATCH (c:Chunk) WHERE c.doc_id = 'd00' DETACH DELETE c",
                  "MATCH (c:Chunk) WHERE c.doc_id = 'd00' SET c.section_title = 'x'"):
            with self.assertRaises(FtsIndexRemnant, msg=q):
                s.run(q)
        with s.session() as sess, self.assertRaises(FtsIndexRemnant):
            sess.run("MERGE (c:Chunk {chunk_id: 'new'}) SET c.text = 'x'")
        s.run("CREATE (:Concept {concept_id: 'k2', owner_id: 'o', title: 'other'})")
        self.assertEqual(s.run("MATCH (v:ChunkVec) RETURN count(v) AS n")[0]["n"], 0)

        put = docstore.put_doc(s, owner="o", doc_id="d00", text="changed body")
        self.assertEqual(put.get("error"), "fts_index_remnant", put)
        self.assertFalse(put.get("executed"))
        new = docstore.put_doc(s, owner="o", doc_id="fresh", text="a brand new document")
        self.assertEqual(new.get("error"), "fts_index_remnant", new)
        gone = docstore.delete_doc(s, "d01", owner="o")
        self.assertEqual(gone.get("error"), "fts_index_remnant", gone)
        self.assertEqual(self._shape(s), before, "거절됐는데 무언가 바뀌었다")
        # 거절된 문서는 그대로 읽힌다
        self.assertIn("number 1", docstore.get_doc(s, "d01", owner="o").get("text") or "")

    @_remnant_probe.needs("fts", _CHILD, "raw")
    def test_reader_view_neither_builds_nor_schedules_on_a_remnant(self):
        submitted = []
        self._crash_after_drop()
        s = self._detect()
        rv = s.reader_view(writer=lambda label, fn, timeout: submitted.append(label))
        try:
            self.assertTrue(rv.fts_index_remnant)
            with self.assertRaises(FtsIndexRemnant):
                rv.ensure_search_indexes()
            rv.schedule_search_indexes(dim=DIM)
        finally:
            rv.close()
        self.assertEqual(submitted, [], "실패할 빌드를 잡으로 걸었다")

    @_remnant_probe.needs("fts", _CHILD, "raw")
    def test_heal_moves_chunk_and_its_edges_keeping_every_row(self):
        """기동 수리: `Chunk` 와 `MENTIONS` 를 새 표로 옮기면 잔해가 사라지고 FTS 가 선다.
        청크·엣지(개념·결정 두 쌍 모두)·열 값이 그대로이고, 그 뒤 문서 저장·삭제가 정상이다."""
        from api import docstore
        self._crash_after_drop()
        s = self._open()
        before = self._shape(s)
        self.assertGreater(before["to_decision"], 0)
        res = s.heal_fts_index(dim=DIM)
        self.assertEqual(res["state"], "repaired", res)
        self.assertEqual(res["rows"], before["chunks"])
        self.assertFalse(s.fts_index_remnant)
        self.assertEqual(self._shape(s), before)
        self.assertFalse(s._has_table("ChunkRebuild"))
        self.assertFalse(s._has_table("MENTIONSRebuild"))
        self.assertGreater(self._fts_hits(s), 0)
        put = docstore.put_doc(s, owner="o", doc_id="d00", text="rewritten zeta body")
        self.assertTrue(put.get("executed"), put)
        self.assertTrue(docstore.delete_doc(s, "d01", owner="o").get("executed"))
        self.assertEqual(self._fts_hits(s, "zeta"), 1)
        self.assertEqual(s.heal_fts_index(dim=DIM)["state"], "present")

    def test_heal_survives_reopen(self):
        self._crash_after_drop()
        s = self._open()
        before = self._shape(s)
        s.heal_fts_index(dim=DIM)
        self.store.close()
        s = self._open()
        self.assertEqual(s.heal_fts_index(dim=DIM)["state"], "present")
        self.assertEqual(self._shape(s), before)
        self.assertGreater(self._fts_hits(s), 0)

    @_remnant_probe.without("fts", _CHILD, "raw")
    def test_this_engine_replays_a_raw_drop_without_a_remnant(self):
        """잔해가 안 생기는 엔진(0.20.4): 체크포인트 없는 드롭으로 죽어도 다음 기동이 색인을
        세우고 행·엣지가 그대로이며 `Chunk` 쓰기가 된다. 이 사실이 깨지면(잔해가 다시 생기면)
        이 테스트는 건너뛰고 위의 잔해 테스트들이 돈다."""
        from api import docstore
        self._crash_after_drop()
        s = self._open()
        before = self._shape(s)
        self.assertEqual(s.heal_fts_index(dim=DIM)["state"], "built")
        self.assertFalse(s.fts_index_remnant)
        self.assertEqual(self._shape(s), before)
        self.assertGreater(self._fts_hits(s), 0)
        put = docstore.put_doc(s, owner="o", doc_id="d00", text="rewritten zeta body")
        self.assertTrue(put.get("executed"), put)
        self.assertTrue(docstore.delete_doc(s, "d01", owner="o").get("executed"))
        self.assertEqual(self._fts_hits(s, "zeta"), 1)

    def test_the_drop_checkpoints_so_a_crash_leaves_no_remnant(self):
        """예방: 백엔드의 드롭은 곧바로 체크포인트하므로 같은 자리에서 죽어도 잔해가 없다.
        색인만 없으므로 기동이 세운다."""
        self._crash_after_drop(raw=False)
        s = self._open()
        self.assertEqual(s.heal_fts_index(dim=DIM)["state"], "built")
        self.assertFalse(s.fts_index_remnant)
        self.assertGreater(self._fts_hits(s), 0)

    @_remnant_probe.needs("fts", _CHILD, "raw")
    def test_heal_drops_a_swap_that_died_before_commit(self):
        """수리가 교체 트랜잭션 안에서 죽었다: 커밋 전이라 옛 표가 온전하고 옮기던 표만
        남는다. 다음 기동의 수리가 그걸 버리고 처음부터 다시 한다."""
        self._crash_after_drop()
        s = self._open()
        before = self._shape(s)
        s.close()
        self.store = None
        self._child(_HEAL_AND_DIE, "in_tx")
        s = self._open()
        self.assertTrue(s._has_table("ChunkRebuild"))
        self.assertEqual(self._shape(s), before)
        res = s.heal_fts_index(dim=DIM)
        self.assertEqual(res["state"], "repaired", res)
        self.assertEqual(self._shape(s), before)
        self.assertFalse(s._has_table("ChunkRebuild"))
        self.assertFalse(s._has_table("MENTIONSRebuild"))

    @_remnant_probe.needs("fts", _CHILD, "raw")
    def test_a_swap_that_died_after_commit_is_replayed_whole(self):
        """커밋 뒤·체크포인트 전에 죽었다: WAL 재생이 교체를 통째로 마친다 — 반쪽이 없다."""
        self._crash_after_drop()
        s = self._open()
        before = self._shape(s)
        s.close()
        self.store = None
        self._child(_HEAL_AND_DIE, "after_commit")
        s = self._open()
        self.assertFalse(s._has_table("ChunkRebuild"))
        self.assertFalse(s._has_table("MENTIONSRebuild"))
        self.assertEqual(self._shape(s), before)
        self.assertEqual(s.heal_fts_index(dim=DIM)["state"], "built")
        self.assertGreater(self._fts_hits(s), 0)

    @_remnant_probe.needs("fts", _CHILD, "raw")
    def test_heal_keeps_the_old_tables_when_a_count_differs(self):
        """옮긴 엣지 수가 다르면 교체하지 않는다 — 옛 표를 그대로 두고, 옮기던 표를 버리고,
        잔해 표시와 쓰기 거절이 그대로 남는다."""
        self._crash_after_drop()
        s = self._detect()
        before = self._shape(s)
        real = s._count_rel
        s._count_rel = lambda label: real(label) - (1 if label.endswith("Rebuild") else 0)
        with self.assertRaises(RuntimeError):
            s.heal_fts_index(dim=DIM)
        self.assertTrue(s.fts_index_remnant)
        self.assertFalse(s._has_table("ChunkRebuild"))
        self.assertFalse(s._has_table("MENTIONSRebuild"))
        self.assertEqual(self._shape(s), before)
        with self.assertRaises(FtsIndexRemnant):
            s.run("CREATE (:Chunk {chunk_id: 'new'})")

    def test_server_boot_heals_before_serving(self):
        """`VincApp.open` 이 HTTP 를 띄우기 전에 수리한다 — 읽기와 겹치지 않는 유일한 때다."""
        from api.server import VincApp
        from store.config import load_config
        self._crash_after_drop()
        app = VincApp.open(load_config({"data_dir": self.dir, "embedding_model": "local-en"}))
        try:
            self.assertFalse(app.store.fts_index_remnant)
            self.assertTrue(app.store._has_index("Chunk", app.store._FTS_INDEX))
        finally:
            app.close()


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class RemnantWriteGuardTests(unittest.TestCase):
    """거절 판정 자체 — 잔해 없이 표시만 세워 본다(엔진에 닿는 쓰기는 없다)."""

    def setUp(self):
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config({"data_dir": os.path.join(tempfile.mkdtemp(), "s")}))
        self.store.provision(dim=DIM)

    def tearDown(self):
        self.store.close()

    def test_nothing_is_refused_without_a_remnant(self):
        self.store.run("CREATE (:Chunk {chunk_id: 'a', text: 'x'})")
        self.store._refuse_remnant_write("MATCH (c:Chunk) DETACH DELETE c")

    def test_only_writes_that_name_the_table_are_refused(self):
        s = self.store
        s._remnants.add("Chunk")
        for q in ("CREATE (:Chunk {chunk_id: 'a'})",
                  "MERGE (c:Chunk {chunk_id: $id}) SET c.text = $t",
                  "MATCH (c: Chunk) WHERE c.doc_id = $d DETACH DELETE c",
                  "COPY Chunk FROM 'x.csv'"):
            with self.assertRaises(FtsIndexRemnant, msg=q):
                s._refuse_remnant_write(q)
        for q in ("MATCH (c:Chunk) RETURN count(c) AS n",
                  "MATCH (c:Chunk) WHERE c.chunk_id = $id RETURN c.text AS text",
                  "MATCH (v:ChunkVec) WHERE v.doc_id = $d DETACH DELETE v",
                  "MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.needs_embed = 1",
                  "MATCH (c:Chunk) WITH count(c) AS n RETURN n AS offset"):
            s._refuse_remnant_write(q)       # 던지지 않는다

    def test_a_lowercase_label_names_the_same_table_and_is_refused(self):
        """엔진은 라벨의 대소문자를 가리지 않는다. 소문자로 부른 쓰기도 잔해 표를 친다(RV-35)."""
        s = self.store
        s.run("CREATE (:Chunk {chunk_id: 'a', text: 'x'})")
        self.assertEqual(s.run("MATCH (c:chunk) RETURN count(c) AS n")[0]["n"], 1)
        s._remnants.add("Chunk")
        with self.assertRaises(FtsIndexRemnant):
            s.run("match (c:chunk) detach delete c")
        s._remnants.discard("Chunk")
        self.assertEqual(s.run("MATCH (c:Chunk) RETURN count(c) AS n")[0]["n"], 1,
                         "거절이 엔진에 닿기 전에 나지 않았다")
        s._remnants.add("Chunk")
        for q in ("MERGE (c:CHUNK {chunk_id: $id})", "copy chunk from 'x.csv'"):
            with self.assertRaises(FtsIndexRemnant, msg=q):
                s._refuse_remnant_write(q)
        s._refuse_remnant_write("match (v:chunkvec) where v.doc_id = $d detach delete v")


if __name__ == "__main__":
    unittest.main()
