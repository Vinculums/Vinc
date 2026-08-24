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
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import docstore as ds  # noqa: E402
from store import pkns  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402


def _pk(nid: str, owner: str = "user:a") -> str:
    """논리 슬러그 → 스토어 바인딩용 **물리 키**.

    이 파일의 e2e 시드·검증은 `merge_fragment` 를 거치지 않고 스토어를 **직접**
    읽고 쓴다. PK 네임스페이싱(design A)이 켜지면 API 쪽 쓰기가 owner 를 합성하므로,
    직접 층도 같은 말을 써야 서로를 찾는다. 플래그가 꺼져 있으면 항등이다."""
    return pkns.key(owner, nid)

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

    def test_토큰_기준_청킹도_무손실이다(self):
        """토큰 모드가 D45 복원 계약을 깨면 안 된다.

        문자 기준 청킹은 언어에 따라 토큰 수가 5배 이상 벌어져(실측: 같은 400토큰 창이
        한국어 482자 · 영어 2,660자) 한국어 청크의 42~82% 가 임베딩 때 조용히 잘렸다.
        토큰 기준으로 자르면 그 손실이 0 이 되는데, **그 대가로 복원이 깨지면 안 된다** —
        Chunk 는 정본이다(D45). 경계 목록만 만들고 슬라이스는 손대지 않으므로 무손실은
        구성으로 성립하지만, 그 구성이 유지되는지 검사한다."""
        for text in _SAMPLES:
            for mt in (20, 100, 400):
                chunks = ds.chunk_doc(text, max_tokens=mt)
                self.assertEqual("".join(c["text"] for c in chunks), text,
                                 f"토큰 모드 손실: max_tokens={mt}")
                prev = 0
                for c in chunks:
                    self.assertEqual(c["start"], prev)
                    prev = c["end"]
                self.assertEqual(prev, len(text))

    def test_토큰_상한이_실제로_지켜진다(self):
        """세어 보고 넘치면 줄이는 **수리 루프**가 있어야 한다.

        ⚠ 토큰 수는 부분합이 아니다 — `tokenize(A+B) ≠ tokenize(A)+tokenize(B)`.
        자/토큰 비로 창을 잡아 놓고 끝내면 경계가 상한을 넘길 수 있다. 여기서는 주입한
        count 로 검사하므로 수리 루프가 빠지면 곧바로 깨진다."""
        def count(s):                       # 결정론 대역 — 공백 단위
            return len(s.split()) + 1

        text = ("단어 " * 300 + "\n\n") * 4 + "word " * 500
        for mt in (10, 40, 120):
            chunks = ds.chunk_doc(text, max_tokens=mt, count=count)
            self.assertEqual("".join(c["text"] for c in chunks), text)
            over = [count(c["text"]) for c in chunks if count(c["text"]) > mt]
            self.assertEqual(over, [], f"상한 초과 {len(over)}건(max_tokens={mt}): {over[:5]}")

    def test_추정기가_과소평가하지_않는다(self):
        """토크나이저 없는 데스크탑용 추정기는 **넉넉해야** 한다.

        과소평가하면 청크가 상한을 넘겨 임베딩 때 잘린다 — 이 작업이 없애려는 바로 그
        손실이다. 과대평가는 청크가 조금 짧아질 뿐이다. 그래서 실제 코퍼스 회귀값
        (한글 1.034 · CJK 1.440 · 기타 0.307 토큰/문자)에 1.3배 여유를 곱해 뒀다.

        ⚠ 처음엔 합성 문장 하나로 상수를 잡았다가 30% 넘게 빗나갔다(청크 84건이 512
        초과). 반복 문장은 흔한 조각이 단일 토큰으로 잡혀 효율을 과대평가한다."""
        # 한글은 영문보다 문자당 토큰이 훨씬 많다 — 그 순서가 뒤집히면 추정이 무의미하다.
        ko = ds.estimate_tokens("양자역학의 해밀토니안 형식은 대응원리를 통해")
        en = ds.estimate_tokens("The Hamiltonian formulation of classical mechanics")
        self.assertGreater(ko / 26, en / 50, "한글이 영문보다 문자당 토큰이 적게 나왔다")
        self.assertEqual(ds.estimate_tokens(""), 0)
        self.assertGreaterEqual(ds.estimate_tokens("a"), 1)

    def test_기본은_여전히_문자_기준이다(self):
        """토큰 모드는 **옵트인**이다. max_tokens 를 안 주면 동작이 바뀌면 안 된다 —
        기존 문서의 청크 경계가 말없이 달라지면 정본이 흔들린다."""
        for text in _SAMPLES:
            self.assertEqual(ds.plan_boundaries(text),
                             ds.plan_boundaries(text, max_chars=ds.DEFAULT_MAX_CHARS))
            self.assertEqual([c["text"] for c in ds.chunk_doc(text)],
                             [c["text"] for c in ds.chunk_doc(text, max_tokens=0)])

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

    def test_highlight_ranges_pure(self):
        # D45-V2 2단계 — 토큰 span 밑줄 범위(순수). text[start:end] 가 그 낱말이어야.
        text = "Quantum 얽힘은 quantum 상태를 공유한다."
        rngs = ds.highlight_ranges(text, {"quantum", "얽힘은"})
        got = [text[r["char_start"]:r["char_end"]] for r in rngs]
        self.assertEqual(got, ["Quantum", "얽힘은", "quantum"])  # 대소문자 무관·전 occurrence
        self.assertEqual(ds.highlight_ranges(text, set()), [])   # 빈 terms → 빈 범위
        self.assertEqual(ds.highlight_ranges("", {"x"}), [])

    def test_concept_terms(self):
        t = ds._concept_terms("양자 얽힘 이론", "quantum")
        self.assertIn("양자", t); self.assertIn("얽힘", t); self.assertIn("quantum", t)
        self.assertNotIn("", t)

    def test_extract_concepts_deterministic(self):
        # D45-V 옵션1 — 헤딩→후보, H1 제외, one_liner = 다음 첫 비빈 줄, slug 멱등.
        text = ("# 문서제목\n\n서문.\n\n## 양자 얽힘\n\n두 입자가 상태를 공유한다.\n"
                "### 벨 부등식\n실험 검증 기준.\n\n## 양자 얽힘\n중복 헤딩.\n")
        cands = ds.extract_concepts(text)
        self.assertEqual([c["title"] for c in cands], ["양자 얽힘", "벨 부등식"])  # H1·중복 제외
        self.assertEqual(cands[0]["one_liner"], "두 입자가 상태를 공유한다.")
        self.assertEqual(cands, ds.extract_concepts(text))   # 결정론
        self.assertEqual(ds.extract_concepts("헤딩 없음\n그냥 본문"), [])

    def test_extract_concepts_underline_headings(self):
        """밑줄식(setext·RST) 헤딩도 받는다 — ATX 만 보던 시절엔 후보 0개였다."""
        text = ("문서 제목\n=========\n\n서문.\n\n"
                "경계 슬라이스\n------------\n무손실이 정의상 성립한다.\n\n"
                "근거 문장\n~~~~~~~~~\n관계마다 출처 문장이 붙는다.\n")
        got = ds.extract_concepts(text)
        # `===` 밑줄은 문서 제목 층 → 제외(ATX 의 `#` 과 같은 취급).
        self.assertEqual([c["title"] for c in got], ["경계 슬라이스", "근거 문장"])
        # 밑줄이 one_liner 로 새지 않는다(다음 헤딩 제목 줄에서 멈춘다).
        self.assertEqual(got[0]["one_liner"], "무손실이 정의상 성립한다.")
        self.assertEqual(got, ds.extract_concepts(text))     # 결정론

    def test_extract_concepts_rejects_lookalikes(self):
        """구분선·프론트매터·표·코드펜스는 헤딩이 아니다 — 오탐이 그래프를 오염한다."""
        # 프론트매터: 닫는 `---` 가 바로 위 `tags:` 를 제목으로 둔갑시키면 안 된다.
        fm = "---\ntitle: 문서\ntags: [a, b]\n---\n\n본문.\n\n## 진짜 헤딩\n내용.\n"
        self.assertEqual([c["title"] for c in ds.extract_concepts(fm)], ["진짜 헤딩"])
        # 구분선(앞이 빈 줄) · 표 구분행 · 목록
        self.assertEqual(ds.extract_concepts("문단.\n\n---\n\n다음 문단.\n"), [])
        self.assertEqual(ds.extract_concepts("| a | b |\n|---|---|\n| 1 | 2 |\n"), [])
        self.assertEqual(ds.extract_concepts("- 항목\n-----\n"), [])
        # 코드펜스 안의 헤딩·밑줄은 코드다
        fence = "```\n## 코드 속 주석\n제목처럼 보임\n-------------\n```\n\n## 밖의 헤딩\n내용.\n"
        self.assertEqual([c["title"] for c in ds.extract_concepts(fence)], ["밖의 헤딩"])
        # 긴 문단 아래 구분선이 그 문단을 통째로 개념으로 만들지 않는다
        self.assertEqual(ds.extract_concepts("가" * 200 + "\n-----\n"), [])

    def test_extract_concepts_rejects_structure_and_clauses(self):
        """구조어·절 문장·6단어 초과 헤딩은 후보가 아니다 — 캐논형 문서 실측에서
        후보 11/11 이 전부 이런 헤딩이었다(근거: [[d6064cc741e9e121f]] §2)."""
        text = ("# 문서\n\n## Method\n측정 방법.\n\n## Provenance\n출처 표.\n\n"
                "## Overall verdict\n판정 요약.\n\n"
                "## What is on the table precisely\n절 문장이다.\n\n"
                "## It reintroduces both defects that were just removed\n일곱 단어 초과.\n\n"
                "## Optimistic concurrency control\n낙관적 동시성 제어.\n")
        got = [c["title"] for c in ds.extract_concepts(text)]
        self.assertEqual(got, ["Optimistic concurrency control"])


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

    def test_쓰기가_FTS_인덱스를_끊지_않는다(self):
        """문서 저장·삭제·임베딩 적용이 도는 동안에도 **키워드 검색이 답해야** 한다.

        예전엔 이 네 경로가 전부 벡터+FTS 인덱스를 통째로 드롭했다. 재빌드는 검색 경로에
        위임돼 있어서, 배경 임베딩이 도는 동안 사용자 검색은 "인덱스 부재 → 잡 큐 재빌드
        → 최대 60초 대기"를 밟았다. 실측하니 임베딩 60라운드 내내 FTS 성공이 **0회**였다.
        지금 유일하게 동작하는 검색 경로가 FTS 인데 배경 작업이 그걸 끄고 있었던 것이다.

        이제 벡터 인덱스만 드롭한다(그건 세그폴트 방어라 필수 — `drop_vector_index` 주석).
        이 테스트는 FTS 드롭이 쓰기 경로로 되돌아오면 깨진다."""
        try:
            self.store.build_search_indexes(dim=4)
        except Exception as exc:                       # 확장 미가용(오프라인 미번들)
            self.skipTest(f"검색 확장 미가용: {str(exc)[:80]}")

        def fts_alive(word="정본검색어"):
            try:
                return len(self.store.fulltext_search(word, 5)) > 0
            except Exception:
                return False

        d1 = ds.put_doc(self.store, owner="user:a",
                        text="정본검색어 alpha\n\n" + "본문 하나. " * 60, title="A")
        self.assertTrue(d1["executed"])
        self.assertTrue(fts_alive(), "문서 저장이 FTS 를 끊었다")

        # 워커가 벡터를 채우는 경로 — 여기는 벡터 인덱스를 드롭하지만 FTS 는 살아야 한다.
        ids = self.store.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS id",
                             {"d": d1["doc_id"]})
        ds.apply_embeddings(self.store, d1["doc_id"],
                            {r["id"]: [0.1, 0.2, 0.3, 0.4] for r in ids})
        self.assertTrue(fts_alive(), "임베딩 적용이 FTS 를 끊었다 — 회귀")

        d2 = ds.put_doc(self.store, owner="user:a", text="버릴 문서 beta", title="B")
        ds.delete_doc(self.store, d2["doc_id"], owner="user:a")
        self.assertTrue(fts_alive(), "문서 삭제가 FTS 를 끊었다")

    def _chunk_ids(self, doc_id):
        return [r["id"] for r in self.store.run(
            "MATCH (c:Chunk) WHERE c.doc_id=$d RETURN c.chunk_id AS id "
            "ORDER BY c.chunk_index", {"d": doc_id})]

    def _count(self, cypher, params=None):
        r = self.store.run(cypher, params)
        return int(r[0][list(r[0])[0]]) if r else 0

    def test_청크당_벡터를_여럿_단다__재청킹이_아니다(self):
        """v8 부모-자식 — 저장 청크는 그대로 두고 벡터만 쪼갠다.

        청크는 2000자 단위인데 모델 입력 상한은 512토큰이라 한 벡터로는 앞부분만
        담긴다(실측 유실: 한국어 42.6% · 영어 12.3%). 토큰 기준 재청킹도 답이지만
        정본(D45)을 전부 다시 쓰는 일이고 개념 링크를 −2.5%/−10% 깎는다.

        벡터만 쪼개면 검색 품질은 재청킹과 **같고**(벡터 집합이 문자 그대로 동일 —
        실측 nDCG 0.659) MENTIONS·발췌·주제 유도는 기준선 그대로다. 이 테스트의 핵심
        단언은 마지막 줄이다: **청크가 안 변했다.**"""
        text = "첫 문단. alpha\n\n둘째 문단. beta\n\n셋째. gamma\n\n넷째. delta"
        d = ds.put_doc(self.store, owner="user:a", text=text, title="T", max_chars=25)
        cids = self._chunk_ids(d["doc_id"])
        self.assertGreaterEqual(len(cids), 2)

        r = ds.apply_embeddings(self.store, d["doc_id"], {
            cids[0]: [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]],   # 자식 3
            cids[1]: [[0, 0, 0, 1.0]]})                                   # 자식 1
        self.assertTrue(r["executed"])
        self.assertEqual(r.get("vectors"), 4, "청크당 여러 벡터가 안 저장됐다")
        self.assertEqual(
            self._count("MATCH (v:ChunkVec) WHERE v.chunk_id=$i RETURN count(v) AS c",
                        {"i": cids[0]}), 3)
        self.assertEqual(self._chunk_ids(d["doc_id"]), cids,
                         "청크가 변했다 — 이건 재청킹이 아니어야 한다")

    def test_단일_벡터_형식도_받는다__하위호환(self):
        """구 워커(벡터 하나)가 보내도 자식 1개로 정규화돼야 한다."""
        d = ds.put_doc(self.store, owner="user:a", text="본문 하나. " * 30, title="T")
        cids = self._chunk_ids(d["doc_id"])
        r = ds.apply_embeddings(self.store, d["doc_id"], {cids[0]: [1.0, 0, 0, 0]})
        self.assertTrue(r["executed"])
        self.assertEqual(r.get("vectors"), 1)

    def test_재임베딩이_옛_자식을_남기지_않는다(self):
        """구·신 벡터가 한 색인에 섞이면 **에러 없이 검색만 나빠진다** — 이 리포가
        모델 전환마다 경계해 온 실패 양식이다. 자식을 먼저 지우고 새로 만든다."""
        d = ds.put_doc(self.store, owner="user:a", text="본문. " * 40, title="T")
        cid = self._chunk_ids(d["doc_id"])[0]
        ds.apply_embeddings(self.store, d["doc_id"],
                            {cid: [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]]})
        ds.apply_embeddings(self.store, d["doc_id"], {cid: [[0, 0, 0, 1.0]]})
        self.assertEqual(
            self._count("MATCH (v:ChunkVec) WHERE v.chunk_id=$i RETURN count(v) AS c",
                        {"i": cid}), 1, "옛 자식이 남았다 — 구·신 벡터 혼재")

    def test_문서_삭제가_자식_벡터까지_지운다(self):
        """고아 자식은 조용히 사라지지 않는다 — 인덱스에 남아 상위 k 자리를 잡아먹고
        부모 조인에서 빈 결과가 된다."""
        d = ds.put_doc(self.store, owner="user:a", text="본문. " * 40, title="T")
        cid = self._chunk_ids(d["doc_id"])[0]
        ds.apply_embeddings(self.store, d["doc_id"], {cid: [[1.0, 0, 0, 0]]})
        self.assertGreater(self._count("MATCH (v:ChunkVec) RETURN count(v) AS c"), 0)
        ds.delete_doc(self.store, d["doc_id"], owner="user:a")
        self.assertEqual(self._count("MATCH (v:ChunkVec) RETURN count(v) AS c"), 0)

    def test_벡터검색이_자식이_아니라_부모를_돌려준다(self):
        """호출부(RRF 융합·발췌)는 청크 계약을 본다. 자식 id 가 새 나가면 그 전부가 깨진다.
        같은 부모의 자식들이 상위 k 를 독식하지 않도록 부모 단위로 접는 것도 함께 본다."""
        text = "첫 문단. alpha\n\n둘째. beta\n\n셋째. gamma\n\n넷째. delta"
        d = ds.put_doc(self.store, owner="user:a", text=text, title="T", max_chars=25)
        cids = self._chunk_ids(d["doc_id"])
        ds.apply_embeddings(self.store, d["doc_id"], {
            cids[0]: [[1.0, 0, 0, 0], [0.9, 0.1, 0, 0], [0.8, 0.2, 0, 0]],
            cids[1]: [[0, 0, 0, 1.0]]})
        try:
            self.store.build_search_indexes(dim=4)
        except Exception as exc:
            self.skipTest(f"검색 확장 미가용: {str(exc)[:60]}")
        hits = self.store.vector_search([1.0, 0, 0, 0], 5)
        ids = [h["id"] for h in hits]
        self.assertTrue(ids, "벡터검색이 비었다")
        self.assertTrue(set(ids) <= set(cids), f"자식 id 가 새 나왔다: {ids}")
        self.assertEqual(len(ids), len(set(ids)), f"부모로 접히지 않았다: {ids}")
        self.assertTrue(all(h.get("text") for h in hits), "부모 본문이 안 실렸다")

    def test_모델_전환이_전량_재임베딩을_건다(self):
        """🔴 **모델을 바꿔도 저절로는 아무 일도 일어나지 않는다.**

        `needs_embed` 는 문서를 저장할 때만 켜진다. 그래서 모델만 갈아끼우면 옛 모델로
        만든 벡터가 그대로 남아 새 쿼리 벡터와 **다른 공간에서** 비교된다 — 에러 없이
        검색만 나빠지는, 이 리포가 반복해서 당해 온 실패 양식이다. (같은 뿌리의 다른
        사례: 청킹 정책을 바꿔도 `content_hash` 가 그대로라 재청킹이 아예 안 일어난다.)

        전환이 하는 일 셋을 전부 검사한다: 옛 벡터 제거(혼재 차단) · 전량 재큐 · 기록."""
        d = ds.put_doc(self.store, owner="user:a", text="본문. " * 40, title="T")
        cid = self._chunk_ids(d["doc_id"])[0]
        ds.switch_embed_model(self.store, "model-A")
        ds.apply_embeddings(self.store, d["doc_id"], {cid: [[1.0, 0, 0, 0]]})
        self.assertEqual(self._count("MATCH (v:ChunkVec) RETURN count(v) AS c"), 1)

        # 같은 모델 → 아무 일도 하지 않는다(멱등). 벡터가 살아 있어야 한다.
        r = ds.switch_embed_model(self.store, "model-A")
        self.assertFalse(r.get("changed"))
        self.assertEqual(self._count("MATCH (v:ChunkVec) RETURN count(v) AS c"), 1,
                         "같은 모델인데 벡터를 지웠다")

        # 다른 모델 → 옛 벡터 제거 + 전량 재큐
        r = ds.switch_embed_model(self.store, "model-B")
        self.assertTrue(r.get("changed"))
        self.assertEqual(self._count("MATCH (v:ChunkVec) RETURN count(v) AS c"), 0,
                         "옛 벡터가 남았다 — 구·신 모델 벡터가 한 색인에 섞인다")
        self.assertEqual(
            self._count("MATCH (d:_DocMeta) WHERE d.needs_embed=1 RETURN count(d) AS c"), 1,
            "재임베딩이 큐에 안 걸렸다 — 전환이 아무 일도 안 한 것이다")
        self.assertEqual(ds.embed_model_state(self.store)["stored"], "model-B")

    def test_기록이_없으면_출처를_모르므로_지운다(self):
        """벡터가 어느 모델로 만들어졌는지 기록이 없으면 **맞다고 가정하지 않는다.**
        지금 배포가 정확히 그 경우다(옛 bge 벡터 + 기록 없음)."""
        d = ds.put_doc(self.store, owner="user:a", text="본문. " * 40, title="T")
        cid = self._chunk_ids(d["doc_id"])[0]
        ds.apply_embeddings(self.store, d["doc_id"], {cid: [[1.0, 0, 0, 0]]})
        self.assertEqual(ds.embed_model_state(self.store)["stored"], "")
        r = ds.switch_embed_model(self.store, "model-X")
        self.assertTrue(r.get("changed"))
        self.assertEqual(self._count("MATCH (v:ChunkVec) RETURN count(v) AS c"), 0)

    def test_이행_상태가_정직하게_보고된다(self):
        """`migrating` 은 '기록이 있고 + 재임베딩 대기가 남았다' 일 때만 참이다.
        서버가 이 값으로 이행 중 벡터 검색을 끈다(발주자 결정: 안전한 방향)."""
        d = ds.put_doc(self.store, owner="user:a", text="본문. " * 40, title="T")
        cid = self._chunk_ids(d["doc_id"])[0]
        ds.switch_embed_model(self.store, "model-A")
        st = ds.embed_model_state(self.store)
        self.assertTrue(st["migrating"] and st["pending"] == 1)
        ds.apply_embeddings(self.store, d["doc_id"], {cid: [[1.0, 0, 0, 0]]})
        st = ds.embed_model_state(self.store)
        self.assertFalse(st["migrating"], "재임베딩이 끝났는데 이행 중이라고 한다")
        self.assertEqual(st["pending"], 0)

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
        # 주제 없는 문서(프론트매터 domain 도, 파생 주제도 없음)는 반영 거부 —
        # 무주제 개념 양산 금지(근거: [[d6064cc741e9e121f]] §2).
        deny = ds.extract_doc(self.store, did, owner="user:a", apply=True)
        self.assertFalse(deny["executed"])
        self.assertIn("no topic", deny["error"])
        # 프론트매터로 주제를 밝히면 반영되고, 개념이 그 주제를 물려받는다.
        ds.put_doc(self.store, owner="user:a", doc_id=did, title="노트",
                   text="---\ndomain: physics\n---\n" + text)
        ap = ds.extract_doc(self.store, did, owner="user:a", apply=True)
        self.assertEqual(ap["applied"]["created"], 1)
        self.assertEqual(ap["domain"], "physics")
        self.assertGreater(ap["mentions"], 0)
        row = self.store.run("MATCH (c:Concept) WHERE c.concept_id=$i "
                             "RETURN c.status AS s, c.one_liner AS ol, c.domain AS d",
                             {"i": _pk("concept:quantum")})
        self.assertEqual(row[0]["s"], "proposed")
        self.assertEqual(row[0]["ol"], "얽힘의 핵심 정리.")
        self.assertEqual(row[0]["d"], "physics")
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

    def test_domains_census_counts_untopiced(self):
        """무주제 노드는 "(none)" 행 — 걸러 내면 부재가 비존재로 읽혀 센서스가
        자기가 못 세는 것을 숨긴다(근거: [[d6064cc741e9e121f]] §1.1)."""
        from api import query as q
        self.store.run("MERGE (c:Concept {concept_id: $i}) SET c.owner_id = 'user:a', "
                       "c.title = 'untopiced', c.domain = ''",
                       {"i": _pk("concept:untopiced")})
        res = q.domains(owner="user:a", store=self.store)
        self.assertTrue(res["executed"])
        row = next((r for r in res["rows"] if r["domain"] == "(none)"), None)
        self.assertIsNotNone(row, "무주제 행이 센서스에 없다")
        self.assertGreaterEqual(row["concepts"], 1)

    def test_extracted_concept_enters_the_next_delta(self):
        """추출로 태어난 개념이 **다음 delta 에 실리는가**(2026-08-17 실측 결함).

        이 경로는 fragment 를 안 거치고 직접 MERGE 하는데 추적열을 안 찍었다. 열
        기본값이 없어 `sync_seq` 가 NULL 이 되고 `c.sync_seq > $since` 가 NULL 을
        배제하므로, 최초 전량 push 이후에 만들어진 개념은 **어느 기기에도 못 갔다**.
        전량 push(`since=0`)는 술어 자체가 없어 통과하니, 회귀를 잡으려면 커서를
        올린 **뒤**를 봐야 한다 — 그래서 이 테스트는 두 번 뽑는다."""
        from api import sync
        text = ("---\ndomain: physics\n---\n"
                "# 노트\n\n## entropy\n\n무질서의 척도.\n\n본문에서 entropy 를 또 다룬다.\n")
        did = ds.put_doc(self.store, owner="user:a", text=text, title="노트")["doc_id"]
        # 1회차 전량 push — 커서를 올려 delta 국면으로 넘긴다.
        first = sync.export_delta(owner="user:a", since=0, domains=[""], store=self.store)
        cursor = first["next_cursor"]

        ap = ds.extract_doc(self.store, did, owner="user:a", apply=True)
        self.assertEqual(ap["applied"]["created"], 1)
        row = self.store.run("MATCH (c:Concept) WHERE c.concept_id=$i "
                             "RETURN c.sync_seq AS seq, c.rev AS rev, c.created AS cr, "
                             "c.updated_at AS ua", {"i": _pk("concept:entropy")})[0]
        self.assertIsNotNone(row["seq"], "추적열 미기록 — delta 가 이 개념을 영영 못 본다")
        self.assertGreater(int(row["seq"]), cursor)
        self.assertEqual(int(row["rev"]), 1)
        # 출생 시각은 스탬프와 같은 HLC 다 — 기기 간 화해가 사전식 비교라 형식이 계약.
        self.assertEqual(row["cr"], row["ua"])

        # 추출 개념은 문서의 주제를 물려받는다 — 그 도메인으로 실어 뽑는다.
        got = sync.export_delta(owner="user:a", since=cursor, domains=["physics"],
                                store=self.store)
        self.assertIn("concept:entropy", [n["id"] for n in got["nodes"]])

    def test_node_context_projects_record_note(self):
        # no-llm-chat 연구 §3.3/§9 — Record ATTACHES_TO 부착은 id/title/domain 만
        # 주고 카드 본문(note)을 버렸다(I6 미개통 원인). note 우선, 없으면 one_liner.
        import json
        from api import query as q
        self.store.run("MERGE (c:Concept {concept_id:$i}) "
                       "SET c.title='X', c.owner_id='user:a'",
                       {"i": _pk("concept:x")})
        self.store.run(
            "MERGE (r:Record {record_id:$i}) "
            "SET r.title='카드1', r.owner_id='user:a', r.props=$p",
            {"i": _pk("rec:1"),
             "p": json.dumps({"note": "이건 주의할 점이다."}, ensure_ascii=False)})
        self.store.run(
            "MERGE (r:Record {record_id:$i}) "
            "SET r.title='카드2', r.owner_id='user:a', r.props=$p",
            {"i": _pk("rec:2"),
             "p": json.dumps({"one_liner": "one_liner 로만 실린 경우."}, ensure_ascii=False)})
        for rid in ("rec:1", "rec:2"):
            self.store.run(
                "MATCH (r:Record {record_id:$r}), (c:Concept {concept_id:$c}) "
                "MERGE (r)-[:ATTACHES_TO]->(c)",
                {"r": _pk(rid), "c": _pk("concept:x")})
        ctx = q.node_context(owner="user:a", node="concept:x", store=self.store)
        notes = {i["id"]: i["note"] for i in ctx["links"]["items"] if i["type"] == "ATTACHES_TO"}
        self.assertEqual(notes["rec:1"], "이건 주의할 점이다.")
        self.assertEqual(notes["rec:2"], "one_liner 로만 실린 경우.")

    def test_ledger_drilldown_matches_aggregate(self):
        # D45-V2 3단계 — 드릴다운 개념 목록 수 == 원장 셀 값(공유 분류 보장).
        from api import query as q
        # 도메인 phys: 개념 3개 — a(에피소드 부착), b(레코드 부착), c(무출처·고아)
        for cid in ("a", "b", "c"):
            self.store.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$id, "
                           "n.domain='phys', n.owner_id='user:a'", {"id": f"concept:{cid}"})
        self.store.run("MERGE (e:Episode {episode_id:'ep1'}) SET e.owner_id='user:a', "
                       "e.title='E'")
        self.store.run("MATCH (e:Episode {episode_id:'ep1'}), (c:Concept {concept_id:'concept:a'}) "
                       "MERGE (e)-[:ABOUT]->(c)")
        self.store.run("MERGE (r:Record {record_id:'rc1'}) SET r.owner_id='user:a', r.title='R'")
        self.store.run("MATCH (r:Record {record_id:'rc1'}), (c:Concept {concept_id:'concept:b'}) "
                       "MERGE (r)-[:ATTACHES_TO {type:'x'}]->(c)")
        # a-b 연결(둘 다 connected, 같은 도메인이라 gateway 아님)
        self.store.run("MATCH (a:Concept {concept_id:'concept:a'}), (b:Concept {concept_id:'concept:b'}) "
                       "MERGE (a)-[:RELATED_TO]->(b)")
        led = q.ledger(owner="user:a", domain="phys", store=self.store)
        row = next(r for r in led["rows"] if r["domain"] == "phys")
        for mark in ("episodic", "record", "unsourced", "connected", "gateway", "orphan"):
            dd = q.ledger_drilldown(owner="user:a", domain="phys", mark=mark, store=self.store)
            self.assertTrue(dd["executed"])
            self.assertEqual(dd["total"], row[mark],
                             f"{mark}: 드릴다운 {dd['total']} != 원장 {row[mark]}")
        # episodic 구간엔 개념 a 가 있고 title/one_liner 를 돌려준다
        epi = q.ledger_drilldown(owner="user:a", domain="phys", mark="episodic", store=self.store)
        self.assertEqual([i["concept_id"] for i in epi["items"]], ["concept:a"])
        # 미지 mark 거부
        self.assertIn("error", q.ledger_drilldown(owner="user:a", domain="phys",
                                                  mark="bogus", store=self.store))

    def _seed_mention_doc(self, owner="user:a"):
        """(phys: beta·delta) · (math: gamma) 를 언급 밀도가 다르게 문 문서 1건.

        의도적으로 **전역 최다 개념(gamma, 3청크)이 대표 주제(phys, 4)에 없게** 짰다.
        대표 개념을 전역 최다로 뽑으면 문서가 phys 밑에 걸린 채 math 노드로 날아간다
        — 그 회귀를 여기서 잡는다."""
        for cid, dom in (("concept:beta", "phys"), ("concept:delta", "phys"),
                         ("concept:gamma", "math")):
            self.store.run(
                "MERGE (n:Concept {concept_id:$id}) SET n.title=$t, n.domain=$d, "
                "n.owner_id=$o", {"id": _pk(cid, owner), "t": cid.split(":")[1],
                                  "d": dom, "o": owner})
        paras = ["beta gamma 문단 하나.", "beta gamma 문단 둘.", "delta gamma 문단 셋.",
                 "delta 문단 넷.", "무관한 문단 다섯."]
        text, starts, pos = "", [], 0
        for p in paras:                       # 문단마다 청크 경계를 강제(언급 수 = 청크 수)
            starts.append(pos)
            block = p + "\n\n"
            text += block
            pos += len(block)
        out = ds.put_doc(self.store, owner=owner, text=text, title="언급",
                         boundaries=starts, max_chars=64)
        self.assertEqual(out["chunks"], len(paras))
        return out["doc_id"]

    def test_list_docs_primary_concept_in_topic(self):
        # D69 — 좌측 트리·문서 목록의 착지 노드. 대표 주제 안에서 고르고, 동률은 id 순.
        did = self._seed_mention_doc()
        doc = next(d for d in ds.list_docs(self.store, owner="user:a")["docs"]
                   if d["doc_id"] == did)
        self.assertEqual(doc["topic"], "phys")            # 4(beta 2+delta 2) > 3(gamma)
        self.assertEqual(doc["topics"], ["phys", "math"])
        # 전역 최다는 gamma(3)지만 대표 개념은 주제 안에서 — 동률 beta/delta 는 id 순.
        self.assertEqual(doc["primary_concept_id"], "concept:beta")
        self.assertEqual(doc["primary_concept_title"], "beta")

    def test_list_docs_unmentioned_doc_has_no_landing(self):
        # 언급 0 문서는 주제도 착지 노드도 없다 — 억지 배정 금지(D45-V).
        out = ds.put_doc(self.store, owner="user:a", text="아무 개념도 안 부르는 글.",
                         title="고아")
        doc = next(d for d in ds.list_docs(self.store, owner="user:a")["docs"]
                   if d["doc_id"] == out["doc_id"])
        self.assertEqual((doc["topic"], doc["topics"]), ("", []))
        self.assertEqual((doc["primary_concept_id"], doc["primary_concept_title"]), ("", ""))

    def test_mention_tally_owner_isolated(self):
        # SEC-1 — 남의 개념이 내 문서의 대표 주제·대표 개념을 흔들지 못한다.
        self._seed_mention_doc(owner="user:a")
        self.store.run("MERGE (n:Concept {concept_id:$i}) SET n.title='gamma', "
                       "n.domain='math', n.owner_id='user:b'",
                       {"i": _pk("concept:gamma-b", "user:b")})
        tally = ds.mention_tally(self.store, owner="user:a")
        self.assertNotIn("concept:gamma-b", tally.cdom)
        self.assertEqual(ds.mention_tally(self.store, owner="user:b").con, {})

    def test_doc_context_gives_concepts_sections_and_chunks(self):
        # 리더 머리 — 문서→노드(여태 없던 방향) + 목차 + 청크 착지 오프셋.
        did = self._seed_mention_doc()
        ctx = ds.doc_context(self.store, did, owner="user:a")
        self.assertTrue(ctx["executed"])
        # 언급 많은 것부터. gamma 는 3청크, beta·delta 는 2청크(_seed_mention_doc).
        self.assertEqual(ctx["concepts"][0]["id"], "concept:gamma")
        self.assertEqual({c["id"] for c in ctx["concepts"]},
                         {"concept:beta", "concept:delta", "concept:gamma"})
        self.assertEqual(ctx["concepts"][0]["count"], 3)
        # 청크 오프셋은 문단 경계와 같아야 한다 — 검색의 청크 히트가 여기로 착지한다.
        text = ds.get_doc(self.store, did)["text"]
        self.assertEqual(len(ctx["chunks"]), 5)
        self.assertEqual([c["index"] for c in ctx["chunks"]], [0, 1, 2, 3, 4])
        self.assertTrue(text[ctx["chunks"][2]["char_start"]:].startswith("delta gamma 문단 셋."))
        self.assertEqual(ctx["sections"], [])   # 헤딩 없는 문서는 목차도 없다

    def test_doc_context_outline_reads_every_depth_with_the_guards(self):
        # 목차는 H1~H6 전부 본다(개념 추출의 H2/H3 컷은 추출 쪽 사정이다). 프론트매터와
        # 코드펜스 가드는 `_headings` 것을 그대로 쓰므로 여기서도 서야 한다.
        text = ("---\ntags: [a]\n---\n\n# 제목\n\n본문\n\n## 절\n\n"
                "```\n## 펜스 안은 제목이 아니다\n```\n\n#### 깊은 절\n\n끝\n")
        did = ds.put_doc(self.store, owner="user:a", text=text, title="목차")["doc_id"]
        ctx = ds.doc_context(self.store, did, owner="user:a")
        self.assertEqual([(s["title"], s["level"]) for s in ctx["sections"]],
                         [("제목", 1), ("절", 2), ("깊은 절", 4)])
        for s in ctx["sections"]:
            self.assertTrue(text[s["char_start"]:].startswith("#"), s)

    def test_doc_context_counts_decisions_not_only_concepts(self):
        # 결정도 MENTIONS 대상이다(engines.mentions_link._LINK_TARGETS). `Concept` 만
        # 세면 결정만 언급한 문서가 빈손이 된다 — 같은 함정이 링커의 MERGE 사본에서
        # 이미 한 번 났고 그때는 결정 링크가 통째로 0건이었다.
        self.store.run("MERGE (n:Decision {decision_id:$i}) "
                       "SET n.title='gamma', n.domain='phys', n.owner_id='user:a'",
                       {"i": _pk("decision:gamma-call")})
        did = self._seed_mention_doc()
        ctx = ds.doc_context(self.store, did, owner="user:a")
        self.assertIn("decision:gamma-call", [c["id"] for c in ctx["concepts"]])

    def test_doc_context_owner_guard(self):
        # SEC-1 — 남의 문서는 못 읽고, 없는 문서는 실패로 답한다(빈 성공 금지).
        did = self._seed_mention_doc(owner="user:a")
        self.assertFalse(ds.doc_context(self.store, did, owner="user:b")["executed"])
        self.assertFalse(ds.doc_context(self.store, "d-nope", owner="user:a")["executed"])

    def test_needs_embed_and_reembed(self):
        # D45-E 후속3 — 임베딩 없이 저장하면 needs_embed=1, reembed 가 채우고 0 으로.
        out = ds.put_doc(self.store, owner="user:a", text="벡터 없는 편집 저장. " * 40,
                         title="편집")   # embed 미지정 → 벡터 없음
        did = out["doc_id"]
        self.assertTrue(out["needs_embed"])
        self.assertEqual(ds.get_doc(self.store, did)["needs_embed"], True)
        self.assertIn(did, ds.stale_docs(self.store, owner="user:a"))
        # embed 를 주면 needs_embed=0(배치 경로 동형)
        emb = lambda ts: [[1.0, 0.0, 0.0, 0.0] for _ in ts]
        r = ds.reembed_doc(self.store, did, owner="user:a", embed=emb)
        self.assertTrue(r["executed"])
        self.assertGreater(r["embedded"], 0)
        self.assertFalse(ds.get_doc(self.store, did)["needs_embed"])
        self.assertNotIn(did, ds.stale_docs(self.store, owner="user:a"))
        # SEC-1: 타인 재임베딩 거부
        self.assertIn("error", ds.reembed_doc(self.store, did, owner="user:b", embed=emb))

    def test_doc_highlights_e2e(self):
        # D45-V2 2단계 — 전문 내 개념 언급 char 범위(밑줄용). 범위는 토큰 매칭 계산.
        text = "# 노트\n\n양자 얽힘을 정리한다. 양자 상태는 공유된다.\n"
        out = ds.put_doc(self.store, owner="user:a", text=text, title="노트")
        did = out["doc_id"]
        self.store.run("MERGE (c:Concept {concept_id:$i}) SET c.title='양자', "
                       "c.stem='양자', c.owner_id='user:a'", {"i": _pk("concept:q")})
        hl = ds.doc_highlights(self.store, did, "concept:q", owner="user:a")
        self.assertTrue(hl["executed"])
        self.assertEqual(hl["method"], "tokens")
        got = [text[r["char_start"]:r["char_end"]] for r in hl["ranges"]]
        self.assertEqual(got, ["양자", "양자"])   # 전문에서 "양자" 두 번, 정확 범위
        # SEC-1: 타인 doc/concept 거부
        self.assertIn("error", ds.doc_highlights(self.store, did, "concept:q", owner="user:b"))
        self.assertIn("error", ds.doc_highlights(self.store, did, "concept:missing",
                                                 owner="user:a"))

    def test_docs_in_range_e2e(self):
        # D45-V2 4단계 — 기간 브러시: [start,end) 의 날짜 있는 Episode/Concept.
        from api import query as q
        for eid, d in (("e1", "2026-03-10"), ("e2", "2026-06-01"), ("e3", "2026-09-20")):
            self.store.run("MERGE (e:Episode {episode_id:$id}) SET e.owner_id='user:a', "
                           "e.title=$id, e.date=$d, e.domain='phys'", {"id": eid, "d": d})
        self.store.run("MERGE (c:Concept {concept_id:'concept:k'}) SET c.owner_id='user:a', "
                       "c.title='K', c.created='2026-05-05', c.domain='phys'")
        # 반개구간 [2026-04, 2026-08) → e2 + concept:k (e1·e3 밖)
        r = q.docs_in_range(owner="user:a", start="2026-04-01", end="2026-08-01",
                            store=self.store)
        self.assertTrue(r["executed"])
        ids = {i["id"] for i in r["items"]}
        self.assertEqual(ids, {"e2", "concept:k"})
        self.assertEqual([i["ts"] for i in r["items"]], ["2026-05-05", "2026-06-01"])  # 정렬
        # domain 렌즈 + 소유 격리
        self.assertEqual(q.docs_in_range(owner="user:b", start="2020", end="2030",
                                         store=self.store)["total"], 0)

    def test_edge_context(self):
        # D45-V 1단계 — 흐름 선 클릭: evidence·confidence + 양끝 발췌.
        from api import query as q
        for cid, t in (("concept:foo", "Foo"), ("concept:bar", "Bar")):
            self.store.run("MERGE (c:Concept {concept_id:$id}) SET c.title=$t, "
                           "c.one_liner=$ol, c.owner_id='user:a'",
                           {"id": _pk(cid), "t": t, "ol": f"{t} 한줄"})
        self.store.run(
            "MATCH (a:Concept {concept_id:$a}), (b:Concept {concept_id:$b}) "
            "MERGE (a)-[r:PREREQUISITE_OF]->(b) SET r.evidence='foo 를 먼저 알아야 bar 가 선다', "
            "r.confidence=0.8", {"a": _pk("concept:foo"), "b": _pk("concept:bar")})
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
            "MATCH (a:Concept {concept_id:$a}), (b:Concept {concept_id:$b}) "
            "MERGE (a)-[r:RELATED_TO]->(b) SET r.evidence='', r.confidence=0.0",
            {"a": _pk("concept:foo"), "b": _pk("concept:bar")})
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


class FailureHintTests(unittest.TestCase):
    """F7 — 실패 응답이 **다음 행동**을 안내한다. 실엔진으로 건다(후보 계산이 스토어를
    실제로 훑기 때문에 스텁으로는 계약이 안 잡힌다).

    실측(2026-08-07)한 이전 상태:
        doc_get(오타)       → {"error": "unknown doc_id"}   후보도 다음 수도 없음
        doc_delete(없는 문서) → {"executed": true}            **성공과 구분 불가**
    """

    def setUp(self):
        # ⚠ **데이터 디렉토리를 반드시 격리한다.** 이 파일의 다른 e2e 가 하는 그대로다.
        #    빠뜨렸다가 공용 스토어에 문서를 남겨, "DB 가 없을 때" 를 전제하는 다른
        #    테스트들(test_activate · DeleteEdgeRouteTests)이 스토어를 **찾아버려**
        #    executed=False 단언이 깨졌다. 격리 없는 실엔진 테스트는 남의 검사를 바꾼다.
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())
        if self.store is None:
            self.skipTest("드라이버 미가용")
        self.store.provision(dim=4)
        ds.put_doc(self.store, owner="user:a", doc_id="vinc.design.system",
                   text="# 디자인 시스템\n본문", title="Vinc 디자인 시스템")

    def tearDown(self):
        if getattr(self, "store", None):
            self.store.close()
        for k, v in getattr(self, "_env", {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(getattr(self, "_tmp", ""), ignore_errors=True)

    def test_없는_문서는_후보와_다음_수를_준다(self):
        res = ds.get_doc(self.store, "vinc.design.systm", owner="user:a")   # 오타
        self.assertFalse(res["executed"])
        self.assertEqual(res["error"], "unknown doc_id")
        self.assertIn("vinc_doc_list", res["hint"])
        self.assertIn("vinc.design.system", res.get("candidates", []),
                      f"오타 한 글자인데 후보를 못 냈다: {res.get('candidates')}")

    def test_아무것도_안_지웠으면_그렇다고_말한다(self):
        """멱등이라 오류는 아니지만, **오타로 지운 줄 알고 넘어가는 것**이 실제 피해다."""
        res = ds.delete_doc(self.store, "vinc.design.systm", owner="user:a")
        self.assertTrue(res["executed"])          # 멱등 — 실행은 됐다
        self.assertFalse(res["found"])            # 그러나 아무것도 안 맞았다
        self.assertIn("nothing was deleted", res["error"])
        self.assertIn("vinc.design.system", res.get("candidates", []))

    def test_진짜_삭제는_found_참이다(self):
        res = ds.delete_doc(self.store, "vinc.design.system", owner="user:a")
        self.assertTrue(res["executed"] and res["found"])
        self.assertNotIn("error", res)
