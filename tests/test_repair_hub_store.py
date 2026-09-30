# -*- coding: utf-8 -*-
"""`scripts/repair_hub_store.py` — 허브 데이터 수리 셋을 실엔진의 작은 스토어에서 본다.

일부러 망가뜨린 스토어(고아 벡터 · 벡터 구멍 · 멈춘 쓰기 창 셋)를 짓고 사본을 고친다.
본다: 원본은 그대로다 · 각 수리의 전후 수 · 키가 물리(네임스페이싱)여도 같은 것을 지운다 ·
재큐 뒤에도 벡터 팔이 켜져 있고 옛 벡터가 답한다 · 출력에 id·소유자가 없다.
근거: [[vinc.plan.rag-code-review-fixes-2026-09-29]]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from api import docstore as ds  # noqa: E402
from store import pkns  # noqa: E402
from store import embedded_backend as eb  # noqa: E402

_DRIVER, _ = eb._import_driver()
_SEP = pkns.SEP
_TEXT = "".join(f"Paragraph {i} about the hub store and its repair.\n\n" for i in range(120))
_N = len(ds.chunk_doc(_TEXT))


def _open(path):
    from store.backend import open_store
    from store.config import load_config
    s = open_store(load_config({"data_dir": path}))
    assert s is not None, eb.EmbeddedBackend.last_open_error
    return s


def _cids(s, doc):
    # 저장값 그대로 — `core-pkns` 잡은 플래그를 켜고 돌아서 기본 읽기가 물리 키를 벗긴다.
    return [r["id"] for r in s.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS id "
                                   "ORDER BY c.chunk_index", {"d": doc}, raw=True)]


def _physical(s, doc, owner, *, chunks=True, vectors=True):
    """문서의 청크·벡터 키를 물리 키(owner U+001F id)로 다시 쓴다 — PK 네임스페이싱 이관본의 모양.

    한쪽만 옮기면 키 모양이 어긋난 쌍이 된다. 청크만 옮긴 것이 허브의 모양이다: 이관이 합성한
    청크 PK 는 재구축을 거쳐 그대로 남고, 워커는 벗긴 논리 id 로 벡터를 쓴다."""
    phys = lambda k: f"{owner}{_SEP}{pkns.strip(k)}"                       # noqa: E731
    if chunks:
        rows = s.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS id, "
                     "c.chunk_index AS ix, c.char_start AS cs, c.char_end AS ce, c.text AS t",
                     {"d": doc}, raw=True)
        s.run("MATCH (c:Chunk) WHERE c.doc_id = $d DETACH DELETE c", {"d": doc})
        for r in rows:
            s.run("CREATE (c:Chunk {chunk_id: $id, doc_id: $d, owner_id: $o, chunk_index: $ix, "
                  "char_start: $cs, char_end: $ce, text: $t})",
                  {"id": phys(r["id"]), "d": doc, "o": owner, "ix": r["ix"], "cs": r["cs"],
                   "ce": r["ce"], "t": r["t"]})
    if vectors:
        rows = s.run("MATCH (v:ChunkVec) WHERE v.doc_id = $d RETURN v.vec_id AS vid, "
                     "v.chunk_id AS cid", {"d": doc}, raw=True)
        s.run("MATCH (v:ChunkVec) WHERE v.doc_id = $d DETACH DELETE v", {"d": doc})
        for r in rows:
            s.run("CREATE (v:ChunkVec {vec_id: $vid, chunk_id: $cid, doc_id: $d, owner_id: $o, "
                  "seq: 0, embedding: [1.0, 0.0, 0.0, 0.0]})",
                  {"vid": phys(r["vid"]), "cid": phys(r["cid"]), "d": doc, "o": owner})


def _seed(path, *, physical=False, mixed=False):
    """문서 여섯(소유자 둘)을 임베딩까지 끝낸 뒤 망가뜨린다. 망가뜨린 것:

    - gone : 청크만 지워 벡터가 고아로 남는다(청크 _N 개 → 고아 _N)
    - cross: 벡터 하나의 소유자를 남으로 바꾼다 → 소유자별로는 고아 1, 그 청크는 구멍
    - hole : 벡터 하나를 지운다(needs_embed=0 인데 구멍)
    - ok   : 쓰기 창 표식만 남았다(①에서 끊김) — 청크가 메타의 본문 그대로
    - tail : 표식 + 꼬리 청크가 없다(② 도중 끊김) — 이어지지만 본문이 메타와 다르다
    - gap  : 표식 + 0번 청크가 없다

    ``mixed`` 면 키 모양이 어긋난 두 문서를 더한다(고아가 아니다 — 청크가 같은 소유자 밑에 있다):
    - fwd  : 청크만 물리 키(허브의 모양)
    - rev  : 벡터만 물리 키"""
    s = _open(path)
    try:
        s.provision(dim=4)
        ds.switch_embed_model(s, "model-a")
        docs = {}
        names = [("gone", "user:a"), ("cross", "user:a"), ("hole", "user:a"),
                 ("ok", "user:a"), ("tail", "user:b"), ("gap", "user:b")]
        if mixed:
            names += [("fwd", "user:a"), ("rev", "user:b")]
        for name, owner in names:
            d = ds.put_doc(s, owner=owner, doc_id=f"doc.{name}", title=name, text=_TEXT)
            assert d.get("executed"), d
            docs[name] = (d["doc_id"], owner)
            cids = _cids(s, d["doc_id"])
            assert len(cids) >= 3, "여러 청크 문서여야 꼬리를 뗄 수 있다"
            r = ds.apply_embeddings(s, d["doc_id"], {c: [1.0, 0.0, 0.0, 0.0] for c in cids})
            assert r.get("cleared"), r
            if physical:
                _physical(s, d["doc_id"], owner)
            elif name in ("fwd", "rev"):
                _physical(s, d["doc_id"], owner, chunks=name == "fwd", vectors=name == "rev")
        cid = lambda n, i: _cids(s, docs[n][0])[i]                         # noqa: E731
        s.run("MATCH (c:Chunk) WHERE c.doc_id = $d DETACH DELETE c", {"d": docs["gone"][0]})
        s.run("MATCH (v:ChunkVec) WHERE v.chunk_id = $c SET v.owner_id = 'user:b'",
              {"c": cid("cross", 1)})
        s.run("MATCH (v:ChunkVec) WHERE v.chunk_id = $c DETACH DELETE v", {"c": cid("hole", 0)})
        for n in ("ok", "tail", "gap"):
            doc = docs[n][0]
            s.run("MATCH (d:_DocMeta) WHERE d.doc_id = $d SET d.chunk_count = -1", {"d": doc})
            s.run("MATCH (v:ChunkVec) WHERE v.doc_id = $d DETACH DELETE v", {"d": doc})
        s.run("MATCH (c:Chunk) WHERE c.chunk_id = $c DETACH DELETE c", {"c": cid("tail", -1)})
        s.run("MATCH (c:Chunk) WHERE c.chunk_id = $c DETACH DELETE c", {"c": cid("gap", 0)})
        s.ensure_search_indexes(dim=4)
        s.run("CHECKPOINT")
    finally:
        s.close()
    return docs


def _run_cli(src, dst, *args, env=None):
    r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "repair_hub_store.py"),
                        "--data", src, "--out", dst, *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300, env={**os.environ, **(env or {})})
    return r.returncode, json.loads(r.stdout), r.stdout


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class RepairHubStoreTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.src = os.path.join(self.dir, "db")
        self.dst = os.path.join(self.dir, "db.repaired")

    def _check_all_three(self, rc, rep, out, docs):
        self.assertEqual(rc, 0, rep.get("problems"))
        # 소프트 재큐: needs_embed=0 이던 여섯 전부(청크를 잃은 gone 도 메타는 있다).
        rq = rep["requeue"]
        self.assertEqual(rq["requeued"], 6)
        self.assertEqual(rq["after"]["docs_needs_embed_0"], 0)
        self.assertEqual(rq["after"]["vectors"], rq["before"]["vectors"], "재큐가 벡터를 지웠다")
        self.assertEqual(rq["vector_arm_gate_after"], "on")
        # 고아 벡터: gone 의 청크 수 + 남의 소유자로 붙은 하나.
        orph = rep["orphans"]
        self.assertEqual(orph["before"]["orphan_vectors"], _N + 1)
        self.assertEqual((orph["deleted"], orph["after"]["orphan_vectors"]), (_N + 1, 0))
        self.assertEqual(orph["after"]["docs_with_a_chunk_lacking_a_vector"],
                         orph["before"]["docs_with_a_chunk_lacking_a_vector"],
                         "고아를 지웠는데 구멍 수가 바뀌었다")
        # 멈춘 쓰기 창: 본문을 증명하는 하나만 마무리, 둘은 소유자 re-put.
        st = rep["stuck"]
        self.assertEqual((st["finalized"], st["reput_text_differs_from_meta"],
                          st["reput_chunks_not_intact"]), (1, 1, 1))
        self.assertEqual(st["after"]["docs_stuck_in_write_window"], st["needs_owner_reput"])
        self.assertEqual(rep["census_after"]["docs_stuck_in_write_window"], 2)
        self.assertEqual(rep["census_after"]["chunk_vectors_without_chunk"], 0)
        # 키 모양이 한결같으면 센서스의 '청크 없는 벡터'는 전부 진짜 고아다.
        kb = rep["key_forms_before"]
        self.assertEqual((kb["of_which_key_form_only"], kb["of_which_no_chunk_under_owner"]),
                         (0, _N))
        self.assertEqual(kb["orphans_the_census_does_not_count"], 1)
        self.assertIn("repair_seconds", rep)
        # 수만 낸다 — 문서 id·소유자·제목이 로그에 새면 안 된다.
        for doc, owner in docs.values():
            self.assertNotIn(doc, out)
            self.assertNotIn(owner, out)

    def _check_copy(self, docs):
        s = _open(self.dst)
        try:
            ok = ds.get_doc(s, docs["ok"][0])
            self.assertTrue(ok.get("verified"), ok)
            self.assertEqual(ok["text"], _TEXT)
            self.assertTrue(ok["needs_embed"])
            # 꼬리가 빠진 문서를 마무리했다면 앞부분을 온전한 문서로 답했을 것이다.
            tail = ds.get_doc(s, docs["tail"][0])
            self.assertFalse(tail.get("executed"), "꼬리 없는 문서가 온전한 것으로 읽힌다")
        finally:
            s.close()

    def test_three_repairs_on_logical_keys_leave_the_source_alone(self):
        docs = _seed(self.src)
        rc, rep, out = _run_cli(self.src, self.dst)
        self._check_all_three(rc, rep, out, docs)
        self._check_copy(docs)
        s = _open(self.src)
        try:
            n = s.run("MATCH (d:_DocMeta) WHERE d.chunk_count < 0 RETURN count(d) AS c")[0]["c"]
            self.assertEqual(n, 3, "원본이 고쳐졌다 — 사본만 고쳐야 한다")
        finally:
            s.close()

    def test_physical_keys_with_the_flag_on_delete_the_same_vectors(self):
        """물리 키 스토어를 플래그 켠 채 고친다(워크플로가 허브 값을 넘긴다). 기본 읽기는 키를
        벗겨서, 그 값으로 지우면 아무것도 안 지워진다 — 저장값 그대로 지워야 한다."""
        docs = _seed(self.src, physical=True)
        rc, rep, out = _run_cli(self.src, self.dst, env={"VINC_PK_NAMESPACED": "on"})
        self._check_all_three(rc, rep, out, docs)
        self._check_copy(docs)
        s = _open(self.dst)
        try:
            left = s.run("MATCH (v:ChunkVec) RETURN v.vec_id AS v", raw=True)
            self.assertTrue(left and all(_SEP in r["v"] for r in left), "물리 키 벡터가 사라졌다")
        finally:
            s.close()

    def test_mixed_key_forms_are_not_orphans_and_the_census_gap_is_split_by_shape(self):
        """허브 measure(36672062376)의 모양을 재현한다: 센서스는 청크 없는 벡터 91 · 벡터 구멍 23,
        수리는 고아 0 · 지움 0. 차이는 전부 키 모양이다 — 같은 소유자의 청크가 다른 모양으로
        있어서 고아가 아니고, 저장값 조인만 못 닿는다. 그 벡터는 지우지 않고 원인별로 센다."""
        _seed(self.src, mixed=True)
        rc, rep, out = _run_cli(self.src, self.dst, "--repairs", "orphans")
        self.assertEqual(rc, 0, rep.get("problems"))
        orph = rep["orphans"]
        # 지우는 것은 같은 소유자 밑에 청크가 없는 벡터뿐이다(gone 의 _N + 남의 소유자 1).
        self.assertEqual((orph["before"]["orphan_vectors"], orph["deleted"],
                          orph["after"]["orphan_vectors"]), (_N + 1, _N + 1, 0))
        # 키 모양 쌍은 소유자별 구멍도 아니다(cross·hole·ok·tail·gap 다섯만 구멍).
        self.assertEqual(orph["before"]["docs_with_a_chunk_lacking_a_vector"], 5)
        kb, ka = rep["key_forms_before"], rep["key_forms_after"]
        self.assertEqual(rep["census_before"]["chunk_vectors_without_chunk"], 3 * _N)
        self.assertEqual((kb["census_vectors_without_chunk"], kb["of_which_key_form_only"],
                          kb["of_which_no_chunk_under_owner"]), (3 * _N, 2 * _N, _N))
        self.assertEqual(kb["key_form_chunk_ids_by_shape"],
                         {"chunk_logical_vector_physical": _N,
                          "chunk_physical_vector_logical": _N})
        self.assertEqual((kb["key_form_vectors"], kb["key_form_docs"]), (2 * _N, 2))
        self.assertEqual((kb["chunks_with_physical_key"], kb["vectors_with_physical_chunk_id"]),
                         (_N, _N))
        self.assertEqual(kb["orphans_the_census_does_not_count"], 1)
        # 수리 뒤 센서스에 남는 것은 전부 키 모양으로 설명된다.
        self.assertEqual(rep["census_after"]["chunk_vectors_without_chunk"], 2 * _N)
        self.assertEqual((ka["of_which_key_form_only"], ka["of_which_no_chunk_under_owner"]),
                         (2 * _N, 0))
        self.assertNotIn("doc.fwd", out)
        self.assertNotIn("user:", out)

    def test_the_semantic_arm_stays_on_after_the_requeue(self):
        """재큐는 모델을 안 바꾸므로 이행이 아니다 — 전 문서가 pending 이어도 게이트가 안 걸리고
        옛 벡터가 그대로 답한다. 이행 표식이 있으면 같은 재큐가 팔을 끈다(그래서 거절한다)."""
        from api.auth import SessionAuth
        from api.server import VincApp
        _seed(self.src)
        rc, rep, _ = _run_cli(self.src, self.dst, "--repairs", "requeue")
        self.assertEqual(rc, 0, rep.get("problems"))
        s = _open(self.dst)
        try:
            st = ds.embed_model_state(s)
            self.assertEqual((st["pending"], st["docs"], st["stored"]), (6, 6, "model-a"))
            self.assertFalse(st["migrating"], st)
            s.ensure_search_indexes(dim=4)
            self.assertTrue(s.vector_search([1.0, 0.0, 0.0, 0.0], 3), "옛 벡터가 답하지 않는다")
            app = VincApp(store=s, auth=SessionAuth("t"))
            try:
                self.assertEqual(app._embed_migrating(), "", "서버 게이트가 벡터 팔을 내렸다")
            finally:
                app.jobs.shutdown(drain=True)
            s.run("MERGE (m:_SchemaMeta {key: 'embed_migration_model'}) SET m.value = 'model-a'")
            self.assertTrue(ds.embed_model_state(s)["migrating"],
                            "전제: 표식이 있으면 같은 pending 이 팔을 끈다")
        finally:
            s.close()

    def test_a_migration_marker_refuses_the_requeue_and_keeps_no_copy(self):
        _seed(self.src)
        s = _open(self.src)
        try:
            s.run("MERGE (m:_SchemaMeta {key: 'embed_migration_model'}) SET m.value = 'model-a'")
            s.run("CHECKPOINT")
        finally:
            s.close()
        rc, rep, _ = _run_cli(self.src, self.dst, "--repairs", "requeue")
        self.assertEqual(rc, 1)
        self.assertEqual(rep["requeue"]["refused"], "migration_marker_present")
        self.assertFalse(os.path.exists(self.dst), "거절했는데 사본을 남겼다")


if __name__ == "__main__":
    unittest.main()
