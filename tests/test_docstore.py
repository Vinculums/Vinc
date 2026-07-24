# -*- coding: utf-8 -*-
"""api/docstore — Chunk=SSoT(D45) 계약 테스트.

  1. **순수 층**(드라이버 불요): 무손실 경계 청킹 — 어떤 입력에도
     ``"".join(chunks) == text`` (경계 슬라이스 구성이라 구조적으로 성립해야 함),
     오프셋 연속성, 크기 상한, 정체성(결정론 doc_id·위치 안정 chunk_id).
  2. **실엔진 층**(ladybug 미설치 시 skip — CI 는 항상 설치): put→get 복원 검증,
     편집 재청킹 시 고아 0(F3), 무변경 스킵(F5), owner 격리(SEC-1), 삭제,
     purge 캐스케이드가 Chunk/_DocMeta 를 커버(F2), MENTIONS doc 단위 재링크.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import docstore as ds  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()

# 무손실 속성 검증용 표본 — 경계 조건을 겨냥해 고른다(무작위 아님·결정론).
_SAMPLES = [
    "",                                              # 빈 문서
    "한 줄",                                          # 개행 없음
    "끝에 개행\n",                                     # 꼬리 개행
    "문단 하나.\n\n문단 둘.\n\n\n문단 셋(빈 줄 런).",      # 문단 경계 런
    "가" * 5000,                                      # 개행 0 — 하드 컷 강제
    ("# 제목\n\n" + "본문 한국어 텍스트와 English mixed. " * 80 + "\n\n"
     + "## 절\n" + "다음 절 내용입니다.\n" * 60),        # 헤딩 + 장문
    "\n\n선두 빈 줄\n\n" + "x\n" * 1500,               # 선두 경계 + 행 경계 다수
    "CRLF\r\n\r\n다음 문단\r\n마지막",                   # CRLF 혼입
]


class PureChunkingTests(unittest.TestCase):
    def test_lossless_by_construction(self):
        # D45 핵심 계약: 복원은 근사가 아니라 **정의상 동일**이어야 한다.
        for text in _SAMPLES:
            for max_chars in (50, 500, ds.DEFAULT_MAX_CHARS):
                chunks = ds.chunk_doc(text, max_chars=max_chars)
                self.assertEqual("".join(c["text"] for c in chunks), text,
                                 f"손실 발생: max_chars={max_chars}")

    def test_offsets_contiguous_and_bounded(self):
        for text in _SAMPLES:
            chunks = ds.chunk_doc(text, max_chars=100)
            prev_end = 0
            for c in chunks:
                self.assertEqual(c["start"], prev_end)        # 연속(구멍·겹침 없음)
                self.assertGreaterEqual(c["end"], c["start"])
                prev_end = c["end"]
                if c is not chunks[-1]:
                    self.assertLessEqual(len(c["text"]), 100)  # 상한(꼬리 제외 아님
                    # — 꼬리도 while 조건상 ≤ max 지만 마지막 비교로 단순화)
            self.assertEqual(prev_end, len(text))              # 전체 커버

    def test_empty_doc_single_empty_chunk(self):
        chunks = ds.chunk_doc("")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["text"], "")
        self.assertEqual((chunks[0]["start"], chunks[0]["end"]), (0, 0))

    def test_prefers_paragraph_boundaries(self):
        text = ("A" * 60 + "\n\n" + "B" * 60 + "\n\n" + "C" * 60)
        chunks = ds.chunk_doc(text, max_chars=100)
        # 100자 창 안의 마지막 문단 경계(62)에서 잘려야 한다 — 하드 컷(100) 아님.
        self.assertEqual(chunks[0]["end"], 62)
        self.assertTrue(chunks[0]["text"].endswith("\n\n"))

    def test_identity_deterministic_and_stable(self):
        h = ds._sha("본문")
        a = ds.make_doc_id("user:a", h, "제목", "노트")
        self.assertEqual(a, ds.make_doc_id("user:a", h, "제목", "노트"))  # 결정론
        self.assertNotEqual(a, ds.make_doc_id("user:b", h, "제목", "노트"))  # owner 분리
        self.assertEqual(ds.chunk_id_of(a, 7), f"{a}#00007")   # 위치 안정(F4)

    def test_section_title_first_heading(self):
        chunks = ds.chunk_doc("서문\n\n# 큰제목\n내용\n## 소제목\n", max_chars=10_000)
        self.assertEqual(chunks[0]["section_title"], "큰제목")

    def test_external_boundaries_normalized_lossless(self):
        # P2 — chonkie 류 외부 경계를 정규화해도 무손실이어야 한다(공백 봉합).
        text = "문단 하나입니다.\n\n문단 둘.\n\n문단 셋 마지막."
        # 일부러 어긋난 후보: 범위 밖(음수·초과)·중복·비정렬·공백 위 경계
        starts = [10, 0, 10, 8, 999, -3, 20]
        chunks = ds.chunk_doc(text, boundaries=starts)
        self.assertEqual("".join(c["text"] for c in chunks), text)  # 무손실
        self.assertEqual(chunks[0]["start"], 0)                     # b0=0 강제
        for i in range(1, len(chunks)):
            self.assertEqual(chunks[i]["start"], chunks[i - 1]["end"])  # 연속

    def test_empty_boundaries_and_empty_text(self):
        self.assertEqual(ds.chunk_doc("", boundaries=[3, 5])[0]["text"], "")
        # 경계가 전부 무효면 단일 청크(=원문)로 귀결
        self.assertEqual("".join(c["text"] for c in ds.chunk_doc("abc", boundaries=[9])), "abc")

    def test_origin_stable_id_survives_edit(self):
        # P2 — 파일 재적재의 정체성은 origin(경로)이지 내용이 아니다(편집 불변).
        a = ds.doc_id_for_origin("user:a", "CONTEXT/x.md")
        self.assertEqual(a, ds.doc_id_for_origin("user:a", "CONTEXT/x.md"))
        self.assertNotEqual(a, ds.doc_id_for_origin("user:a", "CONTEXT/y.md"))
        self.assertNotEqual(a, ds.doc_id_for_origin("user:b", "CONTEXT/x.md"))

    def test_extract_concepts_deterministic(self):
        # D45-V 옵션1 — 헤딩→후보, H1 제외, one_liner = 다음 첫 비빈 줄, slug 멱등.
        text = ("# 문서제목\n\n서문.\n\n## 양자 얽힘\n\n두 입자가 상태를 공유한다.\n"
                "### 벨 부등식\n실험 검증 기준.\n\n## 양자 얽힘\n중복 헤딩.\n")
        cands = ds.extract_concepts(text)
        self.assertEqual([c["title"] for c in cands], ["양자 얽힘", "벨 부등식"])  # H1·중복 제외
        self.assertEqual(cands[0]["one_liner"], "두 입자가 상태를 공유한다.")
        self.assertEqual(cands, ds.extract_concepts(text))   # 결정론
        self.assertEqual(ds.extract_concepts("헤딩 없음\n그냥 본문"), [])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class DocstoreE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
        }.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())
        self.store.provision(dim=4)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _count_chunks(self, doc_id):
        rows = self.store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d RETURN count(c) AS c", {"d": doc_id})
        return int(rows[0]["c"])

    def test_put_get_roundtrip_verified(self):
        text = "# 정본\n\n" + "무손실 복원 검증용 본문입니다. " * 120
        out = ds.put_doc(self.store, owner="user:a", text=text, title="정본")
        self.assertTrue(out["executed"] and not out.get("skipped"))
        got = ds.get_doc(self.store, out["doc_id"], owner="user:a")
        self.assertTrue(got["verified"], got.get("note"))
        self.assertEqual(got["text"], text)                    # 바이트 동일 복원
        self.assertGreater(out["chunks"], 1)                   # 실제로 분할됐는가

    def test_edit_rechunk_no_orphans_and_skip_unchanged(self):
        long_text = "긴 본문. " * 800
        out = ds.put_doc(self.store, owner="user:a", text=long_text, title="편집")
        did = out["doc_id"]
        n_long = self._count_chunks(did)
        self.assertEqual(n_long, out["chunks"])
        # 무변경 재-put → 스킵(F5 — 내용 기반 변경감지)
        again = ds.put_doc(self.store, owner="user:a", text=long_text,
                           doc_id=did, title="편집")
        self.assertTrue(again["skipped"])
        # 대폭 축소 편집 → 청크 수 감소 + 고아 0(F3)
        short_text = "짧아진 본문."
        edited = ds.put_doc(self.store, owner="user:a", text=short_text,
                            doc_id=did, title="편집")
        self.assertFalse(edited["skipped"])
        self.assertEqual(edited["replaced"], n_long)
        self.assertEqual(self._count_chunks(did), edited["chunks"])
        got = ds.get_doc(self.store, did)
        self.assertTrue(got["verified"])
        self.assertEqual(got["text"], short_text)

    def test_owner_isolation_sec1(self):
        out = ds.put_doc(self.store, owner="user:a", text="alice 의 정본", title="t")
        did = out["doc_id"]
        for res in (
            ds.put_doc(self.store, owner="user:b", text="탈취 시도", doc_id=did),
            ds.get_doc(self.store, did, owner="user:b"),
            ds.delete_doc(self.store, did, owner="user:b"),
        ):
            self.assertIn("owner mismatch", res.get("error", ""), res)
        self.assertEqual(self._count_chunks(did), out["chunks"])   # 무손상

    def test_delete_removes_chunks_and_meta(self):
        out = ds.put_doc(self.store, owner="user:a", text="지울 문서", title="d")
        did = out["doc_id"]
        res = ds.delete_doc(self.store, did, owner="user:a")
        self.assertTrue(res["executed"] and res["deleted_meta"])
        self.assertEqual(self._count_chunks(did), 0)
        self.assertIn("error", ds.get_doc(self.store, did))        # 정체성도 소멸
        # 멱등
        self.assertTrue(ds.delete_doc(self.store, did)["executed"])

    def test_purge_cascade_covers_ssot(self):
        # F2 해소의 실증 — 계정삭제가 정본 텍스트·문서 정체성까지 지운다.
        from api import query as q
        ds.put_doc(self.store, owner="user:gone", text="탈퇴 회원의 정본 " * 50)
        ds.put_doc(self.store, owner="user:stay", text="남는 회원의 정본")
        out = q.purge_owner("user:gone", store=self.store)
        self.assertTrue(out["executed"])
        self.assertGreater(out["deleted"].get("Chunk", 0), 0)
        self.assertGreater(out["deleted"].get("_DocMeta", 0), 0)
        rows = self.store.run(
            "MATCH (c:Chunk) WHERE c.owner_id = $o RETURN count(c) AS c",
            {"o": "user:stay"})
        self.assertGreater(int(rows[0]["c"]), 0)                   # 남은 소유자 무손상

    def test_p2_file_ingest_via_docstore(self):
        # P2 — 파일 이관 시뮬레이션: 외부 경계 + fake 임베딩으로 put_doc 왕복 + 증분.
        text = "# 문서\n\n" + "본문. " * 300
        origin = "CONTEXT/reference-docs/A/001-doc.md"
        did = ds.doc_id_for_origin("user:a", origin)
        # fake chonkie 경계(≈120자마다) + fake 4d 임베딩
        starts = list(range(0, len(text), 120))
        emb = lambda texts: [[float(len(t) % 7), 0.0, 0.0, 1.0] for t in texts]
        res = ds.put_doc(self.store, owner="user:a", text=text, doc_id=did,
                         title="문서", origin=origin, layer="G-A",
                         boundaries=starts, embed=emb, link=False)
        self.assertTrue(res["executed"] and not res["skipped"])
        self.assertGreater(res["chunks"], 1)
        # doc_hash 로 무변경 증분 판정 → 재적재 스킵
        self.assertEqual(ds.doc_hash(self.store, did), res["content_hash"])
        again = ds.put_doc(self.store, owner="user:a", text=text, doc_id=did,
                           title="문서", origin=origin, layer="G-A",
                           boundaries=starts, embed=emb, link=False)
        self.assertTrue(again["skipped"])
        # 무손실 복원 + layer 보존(검색 pre-filter)
        got = ds.get_doc(self.store, did, owner="user:a")
        self.assertTrue(got["verified"])
        self.assertEqual(got["text"], text)
        lyr = self.store.run("MATCH (c:Chunk) WHERE c.doc_id=$d RETURN DISTINCT c.layer AS l",
                             {"d": did})
        self.assertEqual([r["l"] for r in lyr], ["G-A"])

    def test_extract_apply_and_node_context(self):
        # D45-V e2e — 정본→노드(extract apply) → 노드→정본(node_context) 왕복.
        from api import query as q
        text = ("# 노트\n\n## quantum\n\n얽힘의 핵심 정리.\n\n본문에서 quantum 을 "
                "여러 번 다룬다.\n")
        out = ds.put_doc(self.store, owner="user:a", text=text, title="노트")
        did = out["doc_id"]
        # 제안(apply=False) — 후보만, 노드 미생성
        prop = ds.extract_doc(self.store, did, owner="user:a")
        self.assertEqual([c["title"] for c in prop["candidates"]], ["quantum"])
        self.assertNotIn("applied", prop)
        # 반영(apply=True) — proposed 개념 생성 + doc 재링크
        ap = ds.extract_doc(self.store, did, owner="user:a", apply=True)
        self.assertEqual(ap["applied"]["created"], 1)
        self.assertGreater(ap["mentions"], 0)
        row = self.store.run("MATCH (c:Concept) WHERE c.concept_id='concept:quantum' "
                             "RETURN c.status AS s, c.one_liner AS ol")
        self.assertEqual(row[0]["s"], "proposed")
        self.assertEqual(row[0]["ol"], "얽힘의 핵심 정리.")
        # 재반영 = matched(덮어쓰기 금지·멱등)
        ap2 = ds.extract_doc(self.store, did, owner="user:a", apply=True)
        self.assertEqual(ap2["applied"], {"created": 0, "matched": 1})
        # 타인 추출 거부(SEC-1)
        self.assertIn("error", ds.extract_doc(self.store, did, owner="user:b"))
        # 노드→정본: 클릭 상세가 one_liner + 근거 발췌를 돌려준다
        ctx = q.node_context(owner="user:a", node="concept:quantum", store=self.store)
        self.assertTrue(ctx["executed"])
        self.assertEqual(ctx["one_liner"], "얽힘의 핵심 정리.")
        self.assertEqual(ctx["arm"], "mentions")
        self.assertGreater(len(ctx["excerpts"]), 0)
        self.assertEqual(ctx["excerpts"][0]["doc_title"], "노트")
        self.assertIn("quantum", ctx["excerpts"][0]["excerpt"])
        # 미지 노드 — 실패 아님(executed=False + note)
        miss = q.node_context(owner="user:a", node="concept:ghost", store=self.store)
        self.assertFalse(miss["executed"])

    def test_edge_context(self):
        # D45-V 1단계 — 흐름 선 클릭: evidence·confidence + 양끝 발췌.
        from api import query as q
        for cid, t in (("concept:foo", "Foo"), ("concept:bar", "Bar")):
            self.store.run("MERGE (c:Concept {concept_id:$id}) SET c.title=$t, "
                           "c.one_liner=$ol, c.owner_id='user:a'",
                           {"id": cid, "t": t, "ol": f"{t} 한줄"})
        self.store.run(
            "MATCH (a:Concept {concept_id:'concept:foo'}), (b:Concept {concept_id:'concept:bar'}) "
            "MERGE (a)-[r:PREREQUISITE_OF]->(b) SET r.evidence='foo 를 먼저 알아야 bar 가 선다', "
            "r.confidence=0.8")
        # foo 를 언급하는 정본 1건 — src 발췌 소스
        ds.put_doc(self.store, owner="user:a", text="Foo 개념을 정리한 문서.", title="근거")
        ctx = q.edge_context(owner="user:a", src="concept:foo", dst="concept:bar",
                             etype="PREREQUISITE_OF", store=self.store)
        self.assertTrue(ctx["executed"])
        self.assertEqual(ctx["evidence"], "foo 를 먼저 알아야 bar 가 선다")
        self.assertAlmostEqual(ctx["confidence"], 0.8)
        self.assertEqual(ctx["src"]["title"], "Foo")
        self.assertEqual(ctx["src"]["one_liner"], "Foo 한줄")
        self.assertIsNotNone(ctx["src"]["excerpt"])          # MENTIONS 대표 발췌
        self.assertIn("Foo", ctx["src"]["excerpt"]["excerpt"])
        # 무방향 RELATED_TO — 저장 방향과 반대로 물어도 잡힌다
        self.store.run(
            "MATCH (a:Concept {concept_id:'concept:foo'}), (b:Concept {concept_id:'concept:bar'}) "
            "MERGE (a)-[r:RELATED_TO]->(b) SET r.evidence='', r.confidence=0.0")
        rev = q.edge_context(owner="user:a", src="concept:bar", dst="concept:foo",
                             etype="RELATED_TO", store=self.store)
        self.assertTrue(rev["executed"])
        self.assertEqual(rev["evidence"], "")                # 빈 evidence = Empty 상태용
        # 화이트리스트 밖 타입 거부(Cypher 주입 차단) · 소유 밖 = 미지 엣지
        bad = q.edge_context(owner="user:a", src="a", dst="b", etype="MENTIONS",
                             store=self.store)
        self.assertIn("unsupported", bad.get("error", ""))
        other = q.edge_context(owner="user:b", src="concept:foo", dst="concept:bar",
                               etype="PREREQUISITE_OF", store=self.store)
        self.assertFalse(other["executed"])

    def test_mentions_relink_per_doc(self):
        # 개념을 심고 그 제목 토큰을 담은 문서를 put → MENTIONS 생성;
        # 토큰을 뺀 편집 → 링크 소멸(DETACH + doc 단위 재계산).
        self.store.run(
            "MERGE (c:Concept {concept_id: 'k-quantum'}) "
            "SET c.title = 'quantum', c.stem = 'quantum', c.owner_id = 'user:a', "
            "c.source_file = ''")
        out = ds.put_doc(self.store, owner="user:a",
                         text="오늘은 quantum 얽힘을 정리했다.", title="노트")
        self.assertGreater(out["mentions"], 0)
        edited = ds.put_doc(self.store, owner="user:a", text="무관한 내용.",
                            doc_id=out["doc_id"], title="노트")
        self.assertEqual(edited["mentions"], 0)
        rows = self.store.run(
            "MATCH (ch:Chunk)-[:MENTIONS]->(:Concept) WHERE ch.doc_id = $d "
            "RETURN count(ch) AS c", {"d": out["doc_id"]})
        self.assertEqual(int(rows[0]["c"]), 0)


if __name__ == "__main__":
    unittest.main()
