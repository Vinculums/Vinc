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

ROOT = Path(__file__).resolve().parents[2]
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


def _chunk_cols(s):
    return [r["name"] for r in s.run("CALL TABLE_INFO('Chunk') RETURN *")]


def _chunk_rows(s, doc):
    """문서의 청크 행 전부를 chunk_index 순으로, PK 를 뺀 모든 열과 함께."""
    cols = [c for c in _chunk_cols(s) if c != "chunk_id"]
    return s.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN "
                 + ", ".join(f"c.{c} AS {c}" for c in cols) + " ORDER BY c.chunk_index",
                 {"d": doc}, raw=True)


def _physical(s, doc, owner, *, chunks=True, vectors=True):
    """문서의 청크·벡터 키를 물리 키(owner U+001F id)로 다시 쓴다 — PK 네임스페이싱 이관본의 모양.

    한쪽만 옮기면 키 모양이 어긋난 쌍이 된다. 청크만 옮긴 것이 허브의 모양이다: 이관이 합성한
    청크 PK 는 재구축을 거쳐 그대로 남고, 워커는 벗긴 논리 id 로 벡터를 쓴다."""
    phys = lambda k: f"{owner}{_SEP}{pkns.strip(k)}"                       # noqa: E731
    if chunks:
        # 벡터 열(legacy embedding, NULL)만 빼고 put_doc 이 쓴 열을 전부 옮긴다 — keyform 이
        # 열을 하나라도 흘리면 _chunk_rows 대조가 잡도록.
        cols = [c for c in _chunk_cols(s) if c not in ("chunk_id", "embedding")]
        rows = s.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS chunk_id, "
                     + ", ".join(f"c.{c} AS {c}" for c in cols), {"d": doc}, raw=True)
        s.run("MATCH (c:Chunk) WHERE c.doc_id = $d DETACH DELETE c", {"d": doc})
        for r in rows:
            s.run("CREATE (c:Chunk {chunk_id: $chunk_id, "
                  + ", ".join(f"{c}: ${c}" for c in cols) + "})",
                  {**r, "chunk_id": phys(r["chunk_id"])})
    if vectors:
        rows = s.run("MATCH (v:ChunkVec) WHERE v.doc_id = $d RETURN v.vec_id AS vid, "
                     "v.chunk_id AS cid", {"d": doc}, raw=True)
        s.run("MATCH (v:ChunkVec) WHERE v.doc_id = $d DETACH DELETE v", {"d": doc})
        for r in rows:
            s.run("CREATE (v:ChunkVec {vec_id: $vid, chunk_id: $cid, doc_id: $d, owner_id: $o, "
                  "seq: 0, embedding: [1.0, 0.0, 0.0, 0.0]})",
                  {"vid": phys(r["vid"]), "cid": phys(r["cid"]), "d": doc, "o": owner})


def _rekey_one(s, old, new):
    """청크 한 행의 PK 만 바꾼다(시험용 — 열은 그대로, 벡터는 안 건드린다)."""
    r = s.run("MATCH (c:Chunk) WHERE c.chunk_id = $id RETURN c.doc_id AS d, c.owner_id AS o, "
              "c.chunk_index AS ix, c.char_start AS cs, c.char_end AS ce, c.text AS t",
              {"id": old}, raw=True)[0]
    s.run("MATCH (c:Chunk) WHERE c.chunk_id = $id DETACH DELETE c", {"id": old})
    s.run("CREATE (c:Chunk {chunk_id: $id, doc_id: $d, owner_id: $o, chunk_index: $ix, "
          "char_start: $cs, char_end: $ce, text: $t})", {"id": new, **r})


def _seed(path, *, physical=False, mixed=False, keyform=False):
    """문서 여섯(소유자 둘)을 임베딩까지 끝낸 뒤 망가뜨린다. 망가뜨린 것:

    - gone : 청크만 지워 벡터가 고아로 남는다(청크 _N 개 → 고아 _N)
    - cross: 벡터 하나의 소유자를 남으로 바꾼다 → 소유자별로는 고아 1, 그 청크는 구멍
    - hole : 벡터 하나를 지운다(needs_embed=0 인데 구멍)
    - ok   : 쓰기 창 표식만 남았다(①에서 끊김) — 청크가 메타의 본문 그대로
    - tail : 표식 + 꼬리 청크가 없다(② 도중 끊김) — 이어지지만 본문이 메타와 다르다
    - gap  : 표식 + 0번 청크가 없다

    ``mixed`` 면 키 모양이 어긋난 두 문서를 더한다(고아가 아니다 — 청크가 같은 소유자 밑에 있다):
    - fwd  : 청크만 물리 키(허브의 모양). 0번 청크가 개념 하나를 MENTIONS 한다
    - rev  : 벡터만 물리 키

    ``keyform`` 이면 ``mixed`` 에 더해 keyform 이 **건너뛰어야** 하는 청크를 하나씩 심는다
    (셋 다 fwd 모양이고 0번 청크만 다르다):
    - coll : 0번 청크의 논리 키가 이미 다른 소유자의 Chunk PK 다(충돌)
    - both : 0번 청크에 물리 키 벡터가 하나 더 있다(섞인 모양 — 지금 붙어 있다)
    - pfx  : 0번 청크 키의 앞머리가 owner_id 와 다르다"""
    mixed = mixed or keyform
    s = _open(path)
    try:
        s.provision(dim=4)
        ds.switch_embed_model(s, "model-a")
        docs = {}
        names = [("gone", "user:a"), ("cross", "user:a"), ("hole", "user:a"),
                 ("ok", "user:a"), ("tail", "user:b"), ("gap", "user:b")]
        if mixed:
            names += [("fwd", "user:a"), ("rev", "user:b")]
        if keyform:
            names += [("coll", "user:a"), ("both", "user:a"), ("pfx", "user:a")]
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
            elif name in ("fwd", "rev", "coll", "both", "pfx"):
                _physical(s, d["doc_id"], owner, chunks=name != "rev", vectors=name == "rev")
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
        if mixed:
            s.run("CREATE (k:Concept {concept_id: 'concept:hub-store', owner_id: 'user:a', "
                  "title: 'hub store'})")
            s.run("MATCH (c:Chunk), (k:Concept) WHERE c.chunk_id = $c "
                  "AND k.concept_id = 'concept:hub-store' "
                  "CREATE (c)-[:MENTIONS {method: 'title'}]->(k)", {"c": cid("fwd", 0)})
        if keyform:
            s.run("CREATE (c:Chunk {chunk_id: $id, doc_id: 'doc.stray', owner_id: 'user:b', "
                  "chunk_index: 0, char_start: 0, char_end: 5, text: 'stray'})",
                  {"id": pkns.strip(cid("coll", 0))})
            s.run("CREATE (v:ChunkVec {vec_id: $vid, chunk_id: $cid, doc_id: 'doc.both', "
                  "owner_id: 'user:a', seq: 9, embedding: [1.0, 0.0, 0.0, 0.0]})",
                  {"vid": cid("both", 0) + "#009", "cid": cid("both", 0)})
            _rekey_one(s, cid("pfx", 0), "user:z" + _SEP + pkns.strip(cid("pfx", 0)))
        s.ensure_search_indexes(dim=4)
        s.run("CHECKPOINT")
    finally:
        s.close()
    return docs


_OLD_RUNTIME = "ort=1.29.0;tok=0.23.2;np=2.5.3"


def _seed_requeued(path, *, physical=False):
    """2026-09-30 재큐 뒤의 허브 모양을 짓는다: 전 문서 needs_embed=1, 옛 벡터는 도장 없음.

    - done1·done2(user:a)·done3(user:b): 전 청크에 벡터가 있다(도장 없음) → 되돌려야 한다
    - other(user:c): 벡터에 다른 도장이 찍혀 있다 → 도장을 안 덮고 pending 으로 남긴다
    - partial(user:a): 청크 하나에 벡터가 없다 → pending
    - split(user:a): 청크는 물리 키, 벡터는 논리 키 — 벡터는 있지만 검색의 저장값 조인이 못
      닿는다 → pending(플래그를 켜고 벗긴 id 로 대조하면 되돌려 버린다)
    - fresh(user:a): 재큐 **뒤에** 새로 쓴 문서, 벡터 0 → pending
    노드 미러: user:a 의 도장 없는 행 둘과 이미 도장 찍힌 행 하나."""
    s = _open(path)
    try:
        s.provision(dim=4)
        ds.switch_embed_model(s, "model-a")
        docs = {}
        for name, owner in (("done1", "user:a"), ("done2", "user:a"), ("done3", "user:b"),
                            ("other", "user:c"), ("partial", "user:a"), ("split", "user:a")):
            d = ds.put_doc(s, owner=owner, doc_id=f"doc.{name}", title=name, text=_TEXT)
            docs[name] = (d["doc_id"], owner)
            cids = _cids(s, d["doc_id"])
            if name == "partial":
                cids = cids[1:]
            ds.apply_embeddings(s, d["doc_id"], {c: [1.0, 0.0, 0.0, 0.0] for c in cids},
                                _OLD_RUNTIME if name == "other" else "")
            if name == "split":
                _physical(s, d["doc_id"], owner, vectors=False)
            elif physical:
                _physical(s, d["doc_id"], owner)
                if name == "other":
                    s.run("MATCH (v:ChunkVec) WHERE v.doc_id = $d SET v.encoder_runtime = $r",
                          {"d": d["doc_id"], "r": _OLD_RUNTIME})
        s.run("MATCH (d:_DocMeta) SET d.needs_embed = 1")          # 재큐(09-30 apply)
        d = ds.put_doc(s, owner="user:a", doc_id="doc.fresh", title="fresh", text=_TEXT)
        docs["fresh"] = (d["doc_id"], "user:a")
        s.run("UNWIND $rows AS r CREATE (:NodeVec {vec_id: r.vid, node_id: r.id, kind: 'concept', "
              "owner_id: 'user:a', title: r.id, text: r.id, model: 'model-a', "
              "embedding: [0.0, 1.0, 0.0, 0.0], encoder_runtime: r.rt})",
              {"rows": [{"vid": f"user:a#concept:n{i}", "id": f"concept:n{i}", "rt": rt}
                        for i, rt in enumerate(("", "", rh_hub_runtime()))]})
        s.ensure_search_indexes(dim=4)
        s.create_node_vector_index()
        s.run("CHECKPOINT")
    finally:
        s.close()
    return docs


def rh_hub_runtime() -> str:
    import repair_hub_store as rh
    return rh.hub_runtime()


#: 도장·재큐 되돌리기 전의 실행 모양(기본값이 stamp,unrequeue 로 바뀌었다).
_OLD_FOUR = ("--repairs", "keyform,requeue,orphans,stuck")


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
        # 키 모양이 한결같은 스토어에서 keyform 은 아무것도 안 한다.
        self.assertEqual((rep["keyform"]["plan"]["to_rewrite"], rep["keyform"]["rewritten"]),
                         (0, 0))
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
        rc, rep, out = _run_cli(self.src, self.dst, *_OLD_FOUR)
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
        rc, rep, out = _run_cli(self.src, self.dst, *_OLD_FOUR, env={"VINC_PK_NAMESPACED": "on"})
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

    def _keyform_run(self, env):
        docs = _seed(self.src, keyform=True)
        rc, rep, out = _run_cli(self.src, self.dst, *_OLD_FOUR, env=env)
        self.assertEqual(rc, 0, rep.get("problems"))
        self.assertEqual(rep["repairs"], ["keyform", "requeue", "orphans", "stuck"])
        kf = rep["keyform"]
        # 대상: fwd 전부 + coll·both·pfx 의 0번을 뺀 나머지. 셋은 건너뛰고 센다.
        self.assertEqual(kf["plan"]["physical_chunks"], 4 * _N)
        self.assertEqual(kf["plan"]["to_rewrite"], 4 * _N - 3)
        self.assertEqual((kf["plan"]["skipped_collision"], kf["plan"]["skipped_mixed_vector_forms"],
                          kf["plan"]["skipped_prefix_not_owner"]), (1, 1, 1))
        self.assertEqual((kf["rewritten"], kf["rewritten_chunks_joined"]), (4 * _N - 3, 4 * _N - 3))
        self.assertEqual(kf["fts_probe"], {"probed": 4 * _N - 3, "found": 4 * _N - 3})
        # 끝까지 붙은 문서: fwd 와 both(0번은 원래 물리 키 벡터로 붙어 있다). coll·pfx 는 0번이 남는다.
        self.assertEqual((kf["docs_rewritten"], kf["docs_fully_joined"]), (4, 2))
        self.assertEqual(kf["after"]["chunks"], kf["before"]["chunks"], "청크를 잃었다")
        self.assertEqual(kf["census_key_form_left"], 2)
        # 다른 모양(벡터만 물리 키)은 손대지 않고 센다.
        self.assertEqual(rep["key_forms_after"]["key_form_chunk_ids_by_shape"],
                         {"chunk_logical_vector_physical": _N,
                          "chunk_physical_vector_logical": 2})
        # 재큐는 keyform 뒤에 돈다 — 고친 청크의 문서까지 전부 덮는다.
        self.assertEqual(rep["requeue"]["after"]["docs_needs_embed_0"], 0)
        for doc, owner in docs.values():
            self.assertNotIn(doc, out)
        self.assertNotIn("user:", out)
        return docs

    def _check_keyform_copy(self, docs):
        fwd = docs["fwd"][0]
        logical = [f"{fwd}#{i:05d}" for i in range(_N)]
        src, dst = _open(self.src), None
        try:
            ids = {r["id"] for r in src.vector_search([1.0, 0.0, 0.0, 0.0], 200)}
            self.assertFalse(ids & set(logical), "전제: 고치기 전에는 조인이 fwd 에 못 닿는다")
            meta_src = src.run("MATCH (d:_DocMeta) WHERE d.doc_id = $d RETURN d.content_hash AS h, "
                               "d.chunk_count AS n", {"d": fwd})[0]
            touched = ("doc.fwd", "doc.coll", "doc.both", "doc.pfx")
            rows_src = {d: _chunk_rows(src, d) for d in touched}
        finally:
            src.close()
        dst = _open(self.dst)
        try:
            # 키 말고는 한 열도 안 바뀐다 — 스크립트의 자기 대조와 따로, 표의 모든 열로 본다.
            for d in touched:
                self.assertEqual(_chunk_rows(dst, d), rows_src[d], f"{d}: 청크 열이 바뀌었다")
            ids = {pkns.strip(r["id"]) for r in dst.vector_search([1.0, 0.0, 0.0, 0.0], 200)}
            self.assertTrue(set(logical) <= ids, "벡터 조인이 고친 청크에 다시 닿지 않는다")
            hits = {pkns.strip(r["id"]) for r in dst.fulltext_search("Paragraph hub store", 50,
                                                                     {"doc_id": fwd})}
            self.assertTrue(set(logical) <= hits, "FTS 가 새 키의 청크를 못 찾는다")
            got = ds.get_doc(dst, fwd)
            self.assertTrue(got.get("verified"), got)
            self.assertEqual(got["text"], _TEXT)
            meta = dst.run("MATCH (d:_DocMeta) WHERE d.doc_id = $d RETURN d.content_hash AS h, "
                           "d.chunk_count AS n", {"d": fwd})[0]
            self.assertEqual(meta, meta_src, "keyform 이 문서 메타를 바꿨다")
            edge = dst.run("MATCH (c:Chunk)-[m:MENTIONS]->(k:Concept) WHERE c.doc_id = $d "
                           "RETURN c.chunk_id AS c, m.method AS m", {"d": fwd}, raw=True)
            self.assertEqual(edge, [{"c": logical[0], "m": "title"}], "관계가 새 청크로 안 옮겨졌다")
            left = {r["c"] for r in dst.run("MATCH (c:Chunk) WHERE c.doc_id IN $d "
                                            "RETURN c.chunk_id AS c", {"d": ["doc.coll", "doc.both",
                                                                            "doc.pfx"]}, raw=True)}
            self.assertEqual(sum(1 for c in left if _SEP in c), 3, "건너뛸 청크 셋을 건드렸다")
        finally:
            dst.close()

    def test_keyform_rewrites_only_the_hub_shape_with_namespacing_off(self):
        """청크 물리 키 · 벡터 논리 키인 쌍만 논리 키로 다시 세운다. 벡터 조인과 FTS 가 새 키에
        닿고, 열·관계·문서 메타는 그대로다. 충돌·섞인 벡터·앞머리 불일치·반대 모양은 건드리지
        않고 센다."""
        self._check_keyform_copy(self._keyform_run({"VINC_PK_NAMESPACED": "off"}))

    def test_keyform_rewrites_only_the_hub_shape_with_namespacing_on(self):
        self._check_keyform_copy(self._keyform_run({"VINC_PK_NAMESPACED": "on"}))

    def _stamp_unrequeue(self, *, physical, env):
        """기본 실행(stamp,unrequeue)을 재큐 뒤 모양의 스토어에 돌리고 판정한다."""
        from api import query as q
        docs = _seed_requeued(self.src, physical=physical)
        value = rh_hub_runtime()
        rc, rep, out = _run_cli(self.src, self.dst, env=env)
        self.assertEqual(rc, 0, rep.get("problems"))
        self.assertEqual(rep["repairs"], ["stamp", "unrequeue"])
        st = rep["stamp"]
        self.assertEqual((st["value"], st["source"]), (value, "Dockerfile.hub"))
        # 도장 없는 것만 찍는다: 청크 벡터(done 셋의 3N + partial 의 N-1 + split 의 N)와 노드
        # 미러 둘. other 의 N 개는 이미 다른 도장이라 그대로다.
        self.assertEqual(st["stamped"], {"ChunkVec": 5 * _N - 1, "NodeVec": 2})
        self.assertEqual(st["runtimes_after"],
                         {"ChunkVec": {value: 5 * _N - 1, _OLD_RUNTIME: _N},
                          "NodeVec": {value: 3}})
        for table in ("ChunkVec", "NodeVec"):
            probe = st["vector_probe"]
            self.assertGreater(probe["before"][table]["probed"], 0)
            self.assertEqual(probe["after"][table]["found"], probe["after"][table]["probed"],
                             f"{table} 색인이 도장 뒤에 제 행을 못 찾는다")
        self.assertEqual(st["fts_probe"]["before"], st["fts_probe"]["after"])
        self.assertGreater(st["fts_probe"]["after"][1], 0)
        un = rep["unrequeue"]
        self.assertEqual((un["pending_before"], un["reset"], un["still_pending"]), (7, 3, 4))
        # partial·fresh·split 은 저장값 조인으로 벡터가 없는 청크가 있다.
        self.assertEqual(un["still_pending_by_reason"],
                         {"missing_vector": 3, "vector_not_stamped_with_hub_runtime": 1,
                          "no_chunks": 0, "chunks_do_not_match_meta": 0})
        self.assertEqual(un["after"]["vectors"], un["before"]["vectors"])
        # 남는 '청크 없는 벡터'는 split 의 키 모양뿐이다(도장·되돌리기는 키를 안 건드린다).
        self.assertEqual(rep["census_after"]["chunk_vectors_without_chunk"], _N)
        for doc, owner in docs.values():
            self.assertNotIn(doc, out)
            self.assertNotIn(owner, out)
        src, dst = _open(self.src), _open(self.dst)
        try:
            # 원본은 그대로 · 사본에서는 벡터를 다 가진 문서만 되돌렸다.
            ne = lambda s: {r["d"]: r["n"] for r in s.run(          # noqa: E731
                "MATCH (d:_DocMeta) RETURN d.doc_id AS d, d.needs_embed AS n")}
            self.assertEqual(set(ne(src).values()), {1})
            self.assertEqual(ne(dst), {"doc.done1": 0, "doc.done2": 0, "doc.done3": 0,
                                       "doc.other": 1, "doc.partial": 1, "doc.split": 1,
                                       "doc.fresh": 1})
            # 검색이 읽는 경고 그대로: 질의 판이 도장과 같으면 경고가 사라진다.
            self.assertEqual(q._encoder_runtime_warning(src, "user:a", value),
                             q.UNSTAMPED_RUNTIME_NOTE, "전제: 고치기 전에는 도장 없음 경고")
            self.assertEqual(q._encoder_runtime_warning(dst, "user:a", value), "")
            self.assertIn("mismatch", q._encoder_runtime_warning(dst, "user:c", value),
                          "다른 도장은 덮지 않는다 — 그 경고는 남아야 한다")
            self.assertTrue(dst.vector_search([1.0, 0.0, 0.0, 0.0], 50), "벡터 검색이 죽었다")
        finally:
            src.close()
            dst.close()

    def test_stamp_and_unrequeue_with_namespacing_off(self):
        self._stamp_unrequeue(physical=False, env={"VINC_PK_NAMESPACED": "off"})

    def test_stamp_and_unrequeue_on_physical_keys_with_namespacing_on(self):
        self._stamp_unrequeue(physical=True, env={"VINC_PK_NAMESPACED": "on"})

    def test_the_stamp_is_what_the_hub_encoder_reports(self):
        """허브는 설치된 판으로 `fingerprint()` 를 짓는다. 이미지의 고정 판을 그 함수에 그대로
        넣으면 스크립트가 짓는 도장과 한 글자도 다르지 않아야 한다."""
        import re
        from unittest import mock
        from store import encoder_runtime
        import repair_hub_store as rh
        line = next(ln for ln in (ROOT / "Dockerfile.hub").read_text(encoding="utf-8").splitlines()
                    if ln.startswith("RUN pip install") and "onnxruntime" in ln)
        pins = dict(re.findall(r"([A-Za-z][\w.-]*)==(\d[\w.]*)", line))
        with mock.patch.object(encoder_runtime, "version", side_effect=pins.__getitem__):
            self.assertEqual(encoder_runtime.fingerprint(), rh.hub_runtime())
        self.assertEqual(rh.hub_runtime(), "ort=1.30.0;tok=0.23.2;np=2.5.3")

    def test_requeue_and_unrequeue_are_refused_together(self):
        import repair_hub_store as rh
        with self.assertRaises(SystemExit):
            rh._parse("requeue,unrequeue")
        self.assertEqual(rh._parse("unrequeue,stamp,keyform"), ["keyform", "stamp", "unrequeue"])

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

    def _marker_left_by(self, pending: str):
        """끝난 이행의 표식을 남긴다: `pending` 문서 하나만 needs_embed=1 로 남는다."""
        docs = _seed(self.src)
        s = _open(self.src)
        try:
            s.run("MERGE (m:_SchemaMeta {key: 'embed_migration_model'}) SET m.value = 'model-a'")
            s.run("MATCH (d:_DocMeta) WHERE d.doc_id = $d SET d.needs_embed = 1",
                  {"d": docs[pending][0]})
            s.run("CHECKPOINT")
        finally:
            s.close()

    def test_unmark_lifts_a_marker_held_only_by_a_document_without_chunks(self):
        """VIN-697. 청크 없는 문서는 워커가 못 보므로 대기열에 영영 남고, 표식은 대기 0 에서만
        내려간다. unmark 가 그 표식을 내리면 같은 실행의 requeue 가 거절되지 않고 팔도 켜져 있다."""
        self._marker_left_by("gone")
        rc, rep, _ = _run_cli(self.src, self.dst, "--repairs", "requeue,unmark")
        self.assertEqual(rc, 0, rep.get("problems"))
        self.assertEqual(rep["repairs"], ["unmark", "requeue"])
        um = rep["unmark"]
        self.assertEqual((um["marker"], um["removed"], um["pending"], um["pending_without_chunks"]),
                         ("model-a", True, 1, 1))
        self.assertEqual(um["after"]["vectors"], um["before"]["vectors"])
        self.assertEqual(rep["requeue"]["requeued"], 5)
        self.assertEqual(rep["requeue"]["vector_arm_gate_after"], "on")
        s = _open(self.dst)
        try:
            st = ds.embed_model_state(s)
            self.assertEqual((st["migration_model"], st["stored"], st["pending"]), ("", "model-a", 6))
            self.assertFalse(st["migrating"], st)
        finally:
            s.close()

    def test_unmark_refuses_while_a_document_with_chunks_is_pending(self):
        self._marker_left_by("cross")
        rc, rep, _ = _run_cli(self.src, self.dst, "--repairs", "unmark")
        self.assertEqual(rc, 1)
        self.assertEqual(rep["unmark"]["refused"], "pending_documents_with_chunks")
        self.assertFalse(os.path.exists(self.dst), "거절했는데 사본을 남겼다")

    def test_unmark_without_a_marker_changes_nothing(self):
        _seed(self.src)
        rc, rep, _ = _run_cli(self.src, self.dst, "--repairs", "unmark")
        self.assertEqual(rc, 0, rep.get("problems"))
        self.assertEqual((rep["unmark"]["marker"], rep["unmark"]["removed"]), ("", False))


if __name__ == "__main__":
    unittest.main()
