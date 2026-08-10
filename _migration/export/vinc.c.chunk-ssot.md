<!-- vinc-export
doc_id: vinc.c.chunk-ssot
title: Chunk=SSoT — 문서 정본 계약
origin: Vinc-docs/vinc/C/vinc.c.chunk-ssot.md@5090690
chunks: 12
verified: true
content_hash: 2a40a116fae768e3d76f64ec2d25964a5475a161df4dabf5e4d4566fcbcec572
updated_at: 2026-08-01T16:20:22.303Z
-->

---
doc_id: vinc.c.chunk-ssot
layer: C
domain: chunk-ssot
title: Chunk=SSoT — 문서 정본 계약 (DEC D45)
status: draft
authority: SSoT
version: v0.2
owner: vinc
last_updated: 2026-07-29
inherits_from: [vinc.c.dataplane, vinc.arch]
tags: [vinc, C, chunk, ssot, docstore, docsync, d45, lossless, sec-1]
---

# Chunk=SSoT — 문서 정본 계약

> **DEC D45 — 채택(v0.6.5 출하 2026-07-24, 현행 v0.6.7).** 사용자 원문 텍스트의
> **정본(SSoT)을 그래프 안 Chunk 로 내재화**한다 — 별도 문서 blob·외부 파일에
> 의존하지 않는다(순수형 결정). 구현체: `Vinc/api/docstore.py`(정본 CRUD·파생),
> `Vinc/api/docsync.py`(문서 단위 동기화 P3), `Vinc/store/embedded_schema.py`
> (Chunk·`_DocMeta`·`_DocTombstone` 스키마 v4~v6), 표면은 HTTP `/doc/*` ·
> CLI `vinc doc` · MCP `vinc_doc_*` · 앱 편집기/드롭존.
>
> ⚠ 이 문서는 **as-built 정본화**다. 코드가 먼저 서고(v0.6.5) 설계 문서가 비어
> 있던 구간을 메운다 — `Vinc/CHANGELOG.md` v0.6.5 와 `Vinc/api/docsync.py:4`
> 가 이 파일의 §번호를 인용하고 있었다. DEC 레지스터([[vinc.b.architecture]])는
> D44 에서 멈춰 있어 D45 등재는 후속 과제다(§8).

포지셔닝 정본 문장(발주자 승인): *"설치 없이 브라우저에서 되는 유일한 지식 그래프.
원문을 정본으로 보관해 모든 개념·관계에 근거 문장과 원문 오프셋이 붙는다."*
뒷문장의 기술적 실체가 이 계약이다.

---

## 1. 결정과 범위

### 1.1 결정

1. **정본은 Chunk 다.** 사용자 원문은 `Chunk.text` 의 오프셋 연결로만 존재한다.
   문서 blob 열도, 원본 파일 참조도 두지 않는다(`api/docstore.py:2-16` 모듈
   독스트링). 파일은 **입력 편의**일 뿐이고(CLI `--file`, 앱 드롭존), 적재
   이후 원본 파일이 사라져도 정본은 온전하다.
2. **쓰기·읽기 단일 지점.** `put_doc` / `get_doc` / `delete_doc` / `list_docs`
   가 정본의 유일한 문. HTTP·CLI·MCP·앱·팩 import·배치 파이프라인이 전부 이
   네 함수로 수렴한다(§7). **`docstore` 안에 파일 I/O 는 없다.**
3. **문서의 조인 키는 경로가 아니라 `doc_id`.** `source_file`/`origin` 은 출처
   **표시**로 강등됐다 — 조인에 쓰지 않는다(`store/embedded_schema.py:73-74`,
   열 이름은 하위호환 위해 유지).
4. **무소유 정본 금지(SEC-1).** `owner` 없는 `put_doc` 은 거부되고, 읽기·쓰기·
   삭제·추출·재임베딩 전부 소유 가드를 지난다(`docstore.py:227,240,342,384,467,689`).

### 1.2 지식 입력 세 경로에서의 위치

Vinc 로 지식이 들어오는 길은 셋이다: ① MCP `vinc_ingest`(호스트가 저작한
fragment) ② `.vincpack`/`.json` 팩 import ③ **문서 정본(D45)**. ③ 만이 원문을
그대로 받고, 개념·관계를 **원문에서 파생**한다(§6). 파생은 마크다운 헤딩을
기계적으로 읽는 결정론 규칙이며 **LLM·네트워크·외부 API 는 0** 이다.

### 1.3 범위 밖

- `_DocMeta` 는 **본문을 갖지 않는다** — 정체성·변경감지 메타뿐
  (`embedded_schema.py:134-147`). 문서 본문을 여기 복제하면 정본이 둘이 된다.
- `_DocMeta` 에는 **주제(domain) 열이 없다.** 문서의 주제는 저장값이 아니라
  `(Chunk)-[:MENTIONS]->(Concept)` 의 domain 집계로 **유도**한다(`list_docs`,
  `docstore.py:407-451`). 링크가 아직 없는 문서는 주제가 **빈 문자열**이다 —
  억지 배정하지 않는다.
- 검색용 미세 분할(토큰 기준)은 임베딩 파이프라인 몫이고, 여기 청킹은 **정본
  보관 단위**다(`docstore.py:46-48`). 그래서 기본 크기가 다소 크다.

---

## 2. 무손실 청킹 계약

### 2.1 경계 슬라이스 구성

청킹은 "자르기"가 아니라 **경계 목록의 슬라이스**다. `b0=0 < b1 < … < bn=len(text)`
에서 `chunk_i = text[b_i:b_{i+1}]`. 이 구성 때문에

```
"".join(chunks) == text
```

가 **정의상** 성립한다 — 겹침·탈락이 구조적으로 불가능하다. 경계 산출이 어그러져도
"못생긴 분할"이 될 뿐 "깨진 원문"이 되지 않는다(`docstore.py:78-84`).

### 2.2 내장 경계 우선순위

`plan_boundaries(text, max_chars=DEFAULT_MAX_CHARS)` — **`DEFAULT_MAX_CHARS = 2000`**
(`docstore.py:48`). 남은 길이가 상한을 넘는 동안, 창 `(pos, pos+max_chars]` 안에서
다음 순서로 자른다(`docstore.py:89-100`):

| 순위 | 경계 | 산출 |
|---|---|---|
| 1 | 문단 경계 | `re.finditer(r"\n\s*\n", text)` 의 `end()` 중 창 안 **최댓값** |
| 2 | 행 경계 | `re.finditer(r"\n", text)` 의 `end()` 중 창 안 **최댓값** |
| 3 | 하드 컷 | `pos + max_chars` (상한 보장) |

빈 문서는 `[0]` → **단일 빈 청크**(`(start,end)=(0,0)`)로 귀결한다
(`tests/test_docstore.py:62-66`).

### 2.3 외부 경계 정규화 (P2)

`normalize_boundaries(text, starts)` 는 외부 청커(chonkie 등)의 시작 오프셋 후보를
(a) `[0, len)` 클램프 (b) 정렬·중복제거 (c) `b0=0` 강제 로 정규화한다
(`docstore.py:112-114`). 외부 청커가 경계에서 공백을 떨궈도, 슬라이스 구성이
그 공백을 어느 청크엔가 반드시 흡수하므로 무손실이 **구성으로** 봉합된다.
`chunk_doc(..., boundaries=[...])` 가 진입점이고, 경계 미지정 시 §2.2 내장 경계다.

### 2.4 청크 레코드와 저장 열

`chunk_doc` 산출 = `{index, start, end, text, section_title}`. `section_title` 은
그 청크 안 **첫 마크다운 헤딩**(`^(#{1,6})\s+(.+)$`)의 캡처, 없으면 빈 문자열.

저장 시 `Chunk` 에 박히는 열(`docstore.py:266-279`, 선언은 `embedded_schema.py:75-80`):
`chunk_id` · `doc_id` · `owner_id` · `source_file`(=origin) · `section_title` ·
`chunk_index` · `layer` · `char_start` · `char_end` · `char_count` ·
`token_count`(0 고정) · `text` · (선택) `embedding`.

> ⚠ Cypher 급소: `start`/`end` 는 예약어라 `$start`/`$end` 를 파서가 거부한다
> (실측 ladybug 0.18). `$cs`/`$ce` 로 우회한다(`docstore.py:266-269` 주석).

### 2.5 복원과 검증

`get_doc` 은 `chunk_index` 순으로 `text` 를 이어 붙이고 두 가지를 함께 검사한다
(`docstore.py:330-372`):

```
verified = (b0 == 0 AND char_start_i == char_end_{i-1} AND char_end >= char_start)
           AND sha256(복원문) == _DocMeta.content_hash
```

`verified=false` 는 실패가 아니라 **보고**다 — 레거시(오프셋 미기록) 또는 부분
기록이며 `note` 에 "re-put 으로 치유 가능"이 실린다. 앱 Reader 의 무손실 배지가
이 값을 그대로 표시한다.

> ⚠ 오프셋 판독에 `v or -1` 을 쓰면 **정당한 오프셋 0 이 falsy 로 강등**된다.
> `_off()` 가 `None` 만 -1 로 본다(`docstore.py:350-351`).

### 2.6 계약 핀(테스트)

`Vinc/tests/test_docstore.py` 가 이 절을 고정한다 — 경계 조건을 겨냥한 8표본
(빈 문서·개행 0·꼬리 개행·빈 줄 런·5000자 무개행·헤딩+장문·선두 빈 줄·CRLF 혼입)
× `max_chars ∈ {50, 500, 2000}` 전수:

- `test_lossless_by_construction` — `"".join == text`
- `test_offsets_contiguous_and_bounded` — 연속·상한·전체 커버
- `test_prefers_paragraph_boundaries` — 창 100 에서 하드 컷(100) 아닌 문단 경계(62)
- `test_external_boundaries_normalized_lossless` — 음수·초과·중복·비정렬 후보 정규화
- `test_put_get_roundtrip_verified` — 실엔진 왕복 바이트 동일

---

## 3. 정체성 (doc_id · chunk_id · origin)

### 3.1 doc_id — 두 산식

```python
make_doc_id(owner, content_hash, title="", origin="")
    basis = f"{owner}|{title}|{origin}|{content_hash}"
    return "d" + sha256(basis).hexdigest()[:16]          # docstore.py:61-67

doc_id_for_origin(owner, origin)
    return "d" + sha256(f"{owner}|origin|{origin}").hexdigest()[:16]   # :173-177
```

- **`make_doc_id`** (기본) — 같은 문서의 중복 **최초** 적재가 자연 dedupe 되고,
  기기 간 오프라인 재적재도 같은 정체성으로 수렴한다. 내용 해시가 basis 에 들어가지만
  **편집 후에도 id 는 불변**이다: `put_doc` 에 `doc_id` 를 넘겨 갱신하기 때문.
- **`doc_id_for_origin`** (P2) — `origin`(파일 경로 등 안정 외부 식별자) 기반이라
  **편집에도 id 가 불변**. 배치 파일 이관의 정체성 원천이며
  `engines/embed_pipeline.py:326` 이 이 산식을 쓴다.
- `new_doc_id()` = `"d" + uuid4().hex[:16]` 은 내용 무관 신규 정체성이 필요할 때의
  대안이며 **기본 경로가 아니다**(`docstore.py:558-561`).

> **클라이언트 급소.** `make_doc_id` 가 `content_hash` 를 포함하므로, **id 를 주지
> 않으면 편집본은 언제나 새 문서**가 된다. "같은 파일 재드롭 = 갱신" 의미론은
> 클라이언트가 `origin → doc_id` 색인을 들고 id 를 찾아 줘야 성립한다
> (`app/src/doc-import.ts` `originIndex`/`importDocFile`).

### 3.2 chunk_id — 위치 안정

```python
chunk_id_of(doc_id, index) -> f"{doc_id}#{index:05d}"     # docstore.py:70-73
```

내용이 아니라 **(문서, 순번)** 에 종속한다. 편집·재청킹에도 같은 자리의 청크가
같은 id 로 MERGE 되고, 줄어든 꼬리만 삭제된다.

### 3.3 origin 은 조인 키가 아니다

`origin`(저장 열 이름은 `Chunk.source_file`, `_DocMeta.origin`)은 출처 표시일 뿐이다.
MCP 도구 스키마도 그렇게 못박는다 — `"Provenance label only (not a join key)."`
(`vinc_mcp/tools.py:406`). 앱은 origin 을 화면에 **표시하지 않는다**(남의 기기 경로일
수 있다, `doc-import.ts:60-61`).

### 3.4 소유

`Chunk.owner_id` 는 D45(스키마 v4)에서 붙었다. `''` 는 docstore 이전 **레거시 청크**
(파일 배치 파생물이라 소유 원천이 없어 백필하지 않는다 — Bridge v2 전례).
`owner_scoped_labels()` 가 `owner_id` 열 선언에서 파생되므로 계정 삭제 purge 가
Chunk·`_DocMeta` 를 **자동으로 덮는다**(`embedded_schema.py:155-164`,
`tests/test_docstore.py:test_purge_cascade_covers_ssot`).

---

## 4. 변경감지와 쓰기 순서 (자기치유)

### 4.1 변경감지 — 내용 기반

`_DocMeta.content_hash` = 정본 텍스트의 sha256. `put_doc` 은 **`content_hash` 가
같고 `title` 도 같을 때만** 스킵한다(`docstore.py:242-245`) — 제목만 바뀌어도
재기록한다. 반환은 `executed=True, skipped=True` 로, **실패가 아니라 성공**이다.
배치 경로는 청킹·임베딩 **전에** `doc_hash(store, doc_id)` 로 같은 판정을 내려
비용을 아낀다(`docstore.py:180-184`, `embed_pipeline.py:327`).

이것이 파일명 기반 체크포인트를 대체한 지점이다(코드 주석의 **F5 해소**).

### 4.2 소유 가드 (SEC-1)

기존 문서가 있고 `prev.owner_id` 가 호출 owner 와 다르면 `put_doc` 은 즉시 거부한다 —
`"owner mismatch — 남의 문서에는 쓸 수 없다(SEC-1)"`. 재-put 으로 남의 정본을 탈취할
수 없다. `get_doc`·`delete_doc`·`reembed_doc`·`extract_doc`·`doc_highlights` 도 같은
가드를 갖는다. 실엔진 테스트가 put/get/delete 3종 거부를 핀으로 박아 두었다
(`test_owner_isolation_sec1`).

### 4.3 쓰기 순서

`put_doc` 의 순서는 **자기치유의 근거**다(`docstore.py:252-296`):

| # | 단계 | 근거 |
|---|---|---|
| ① | `drop_search_indexes()`(멱등) | 인덱스 걸린 열(`text`·`embedding`)은 수정 불가 |
| ② | 구 청크 `DETACH DELETE`(수 = `replaced`) | 재청킹으로 개수가 줄어도 **고아 0**(F3 해소). 부착 `MENTIONS` 도 함께 소멸 |
| ③ | 신 청크 `MERGE (c:Chunk {chunk_id: $id}) SET …` | 위치 안정 id 라 같은 자리 갱신 |
| ④ | `_DocMeta` MERGE — `content_hash`·`chunk_count`·`updated_at`·`sync_seq`·`needs_embed` | **해시는 마지막** |
| ⑤ | `_clear_doc_tombstone` | 재생성이 낡은 삭제를 상쇄(P3, §5) |
| ⑥ | `_relink_mentions`(`link=True` 일 때) | 규칙은 E-G1 링커 재사용 |

중간에 죽으면 ④가 실행되지 않아 해시가 갱신되지 않고, **다음 `put_doc` 이 스킵되지
않고 전체를 재기록**한다. 임베디드 단일 프로세스라 다중 문 트랜잭션 없이 수렴한다
(`docstore.py:25-27`).

### 4.4 인덱스 수명주기

- **drop**: 변이 전 `drop_search_indexes()` — 벡터(HNSW)·FTS(BM25) 인덱스 제거, 멱등
  (`store/embedded_backend.py:307-317`).
- **재빌드**: 여기서 하지 않는다. **검색 경로의 지연 보장**
  `ensure_search_indexes()`(T3b 자기복구)가 부재 시에만 만든다 — FTS 는 항상,
  벡터는 `dim` 이 주어질 때만(`embedded_backend.py:327-339`). 문서 1건 편집마다
  전체 HNSW 를 재빌드해 수 초를 태우지 않기 위함이다.
- 확장(vector/fts) 미가용 스토어에서 drop 실패는 **삼킨다** — 인덱스가 없는 스토어일
  뿐이고, **정본 쓰기를 막을 이유가 아니다**(`docstore.py:194-205`). SSoT 는 검색
  확장 없이도 성립해야 한다.

### 4.5 임베딩 결측 표식 (needs_embed)

`embed` 콜러블이 주어지면 `needs_embed=0`, 없으면 `1`(`docstore.py:283`). 앱 편집·
동기화 수신 경로는 1 로 적재돼 **FTS 는 즉시, 벡터는 지연**이다. 채우는 길 둘:

- `reembed_doc(store, doc_id, owner, embed)` — 재청킹 없이 `embedding` 열만 SET →
  `needs_embed=0`. `stale_docs()` 가 대상을 낸다.
- `pending_embed_chunks(limit)` → 워커 → `apply_embeddings(doc_id, {chunk_id: vec})`.
  전자는 **doc 단위**로 낸다(청크 캡으로 쪼개면 큰 문서가 영영 안 끝난다), 후자는
  **전 청크가 채워졌을 때만** `needs_embed` 를 내린다. 허브 자체호스팅 임베딩 워커용
  **루프백 내부 표면**이며 원문은 스토어 밖으로 나가지 않는다(`docstore.py:490-543`,
  `api/server.py:534-548`).

---

## 5. 동기화 (P3) — 문서 단위 LWW

> `Vinc/api/docsync.py` 가 이 절을 `[[vinc.c.chunk-ssot]] §5-P3` 으로 인용한다.

### 5.1 왜 doc 단위인가

노드 동기화(`api/sync`)는 fragment 노드(Concept/Episode/…) 중심의 **필드 3-way
병합**이다. Chunk 를 그 레일에 태우면 (a) 재청킹으로 `chunk_id` 집합이 통째 바뀌고
(b) `embedding` 이 delta 번들(3MB 한계)을 잠식한다(`docsync.py:5-8`). 그래서 **문서를
동기화 단위**로 삼고, `sync.py` 를 건드리지 않는 **독립 채널**로 둔다.

| 축 | 규칙 |
|---|---|
| 전송 | 문서 정체성(`_DocMeta`) + **복원 텍스트만**. embedding·청크 오프셋은 안 보냄 |
| 수신 | `put_doc` 로 **로컬 재청킹**(임베딩은 기기별 재생성) |
| 충돌 | doc 단위 **LWW**(`updated_at` HLC 비교) — 필드 병합 없음(문서는 원자 단위) |
| 삭제 | `_DocTombstone` 전파 |
| 커서 | `revstamp.next_seq(store, f"doc:{owner}")` — 노드 sync 와 **네임스페이스 분리** |

`Chunk` 는 `_TRACKED_LABELS` 에 들어 있지 않다 — 추적열(`updated_at`/`rev`/`sync_seq`)이
없고, 동기화는 오직 doc 단위로만 일어난다(`embedded_schema.py:102-107`).

### 5.2 export (`export_docs`)

`sync_seq > since` 인 `_DocMeta` + 같은 조건의 `_DocTombstone` 을 낸다.
문서 payload = `{doc_id, title, origin, content_hash, updated_at, text}` — `text` 는
`get_doc` 복원문이다. 반환 `cursor` = 이번 배치의 **최대 sync_seq**(docs·tombstones
통틀어)이고 다음 pull 의 `since` 가 된다(`docsync.py:25-58`).

### 5.3 import (`import_docs`) — 결정론 LWW

인바운드 문서마다(`docsync.py:68-107`):

1. 로컬 없음 → `put_doc(..., updated_at=원격 HLC)` (원격 시각을 그대로 박는다).
2. 로컬 owner 불일치 → **skip**(SEC-1 — payload 의 owner 를 신뢰하지 않고 호출
   owner 로 고정).
3. `content_hash` 동일 → **skip**(no-op).
4. `_newer(원격, 로컬)` 이 아니면 → **skip**. 동률·구버전은 **로컬 유지**(결정론).
5. 적용 후 `revstamp.observe_hlc(원격 시각)` 로 로컬 HLC 를 전진시킨다 — 이후 로컬
   쓰기가 원격보다 뒤로 가지 않게.

`_newer(a, b)` 는 **문자열 비교**다: HLC 는 단조 정렬 문자열이고 빈 값이 최과거
(`docsync.py:122-124`).

### 5.4 삭제 전파

하드삭제만으로는 상대 사본이 다음 delta 에 실려 **부활**한다. 그래서:

- `delete_doc(..., tombstone=True)`(기본) → `_DocTombstone{doc_id, owner_id,
  deleted_at, sync_seq}` 기록.
- 수신측은 **로컬 `updated_at` 이 `deleted_at` 보다 새면 삭제를 무시**한다 —
  **재생성이 삭제를 이긴다**(`docsync.py:113-114`).
- 적용할 때는 `delete_doc(..., tombstone=False)` — 동기화가 만든 삭제가 다시 툼스톤을
  낳아 왕복하지 않게.
- `put_doc` 은 `_clear_doc_tombstone` 으로 낡은 삭제 표식을 상쇄한다(§4.3 ⑤).

### 5.5 시각 원천 (HLC)

`_stamp_updated_at` = `revstamp.hlc_now(store)` — 스토어 내 단조 HLC. 벽시계 스큐에도
LWW 비교가 성립한다. revstamp 미가용(경량 스토어)이면 벽시계
(`%Y-%m-%dT%H:%M:%SZ`)로 **강등**한다(`docstore.py:153-160`). `sync_seq` 도 같은
방식으로 미가용 시 0 으로 강등한다(`:163-170`).

### 5.6 표면

`GET /sync/docs-export` · `POST /sync/docs-apply`(`api/server.py:909,943`).
둘 다 사이드카 내부 표면이며 허브 웹 프록시 허용목록(`_PROXY_OPS`)에 **의도적으로
미등재**다 — `sync/*` 는 데스크탑↔계정 동기화 기계라 웹 그래프 프록시의 대상이
아니다(`vinc_mcp/http_server.py:42-46` 주석).

---

## 6. 개념 파생 (정본 → 노드)

### 6.1 결정론 추출 (`extract_concepts`)

순수 함수 — LLM·네트워크·스토어 없음. 어디서든 같은 입력 = 같은 후보
(`docstore.py:638-667`):

- 대상 = **`##`/`###` 헤딩만**. `#`(H1)은 문서 제목으로 보고 제외.
- `title` = 헤딩 텍스트(선두 `*` 제거), `slug` = `[^a-z0-9가-힣]+ → "-"` 후 60자 절단.
- 같은 slug 는 **첫 등장만**(멱등).
- `one_liner` = 헤딩 **다음 첫 비빈 줄** ≤400자. 다음 헤딩을 만나면 중단(빈 값).
- 상한 `max_candidates=24`.

헤딩이 없는 문서는 후보 0 이다(§8).

### 6.2 반영 (`extract_doc`)

`apply=False`(기본)는 **후보 제안**만 돌려주고 `applied` 키 자체가 없다.
`apply=True` 일 때만(`docstore.py:670-734`):

- `Concept` MERGE — `concept_id = f"concept:{slug}"`, `status='proposed'`,
  `domain=''`(도메인 귀속은 후속 편집·UI 몫), `stem=slug`, `source_file=origin`.
- **기존 개념은 덮어쓰지 않는다** — 내 것이면 `matched` 로 세고 건너뛴다(사용자가
  다듬은 title/one_liner 보호). **남의 개념은 아예 손대지 않는다**(SEC-1).
- 이어 `_relink_mentions`(새 개념이 즉시 뷰·검색 그래프 암에 합류) → `backfill_evidence`.
- 외부 후보(옵션2 — LLM/사용자가 다듬은 목록)를 `candidates` 로 실을 수 있으나
  **id 는 서버가 slug 로 재파생**한다. 외부 id 주입 금지.

재반영은 `{"created": 0, "matched": n}` 으로 멱등하다(`test_extract_apply_and_node_context`).

### 6.3 MENTIONS 재링크

`_relink_mentions` 는 **이 doc 의 청크만** 대상으로 `:MENTIONS` 를 다시 만든다.
링크 규칙·우선순위는 `engines/mentions_link.compute_links` 를 **그대로 재사용**한다 —
규칙 분기 금지(SSoT 하나). 구 링크는 §4.3 ②의 `DETACH DELETE` 가 이미 지웠다.
실패는 적재를 무효화하지 않는다(증강, `docstore.py:305-327`).

### 6.4 언급 위치 (하이라이트)

`MENTIONS` 는 `method` 만 저장하고 **오프셋이 없다**. 그래서 언급 위치는 **읽기 시점
토큰 매칭으로 계산**한다(`highlight_ranges`, `docstore.py:577-593`):

- 토큰 = `[0-9A-Za-z가-힣]+`(mentions_link 와 같은 어휘), 대상 terms 는 개념 제목의
  유의 토큰(≤6, 2자 이상) + `stem`.
- `text[char_start:char_end]` 가 정확히 그 낱말이 되도록 토큰 경계로 자른다.
- 인접·중복 범위를 **병합하지 않는다** — 각 낱말이 독립 밑줄이지 문장 전체를 칠하지
  않는다. 상한 60범위.

### 6.5 관계 근거 (`backfill_evidence`)

스키마에 `evidence`/`confidence` 열이 있는데 **채우는 곳이 하나도 없어** 관계 패널이
언제나 "근거 없음"이었다(실측: 방향 엣지 80개 중 보유 0). 그 공백을 정본에서 메운다
(`docstore.py:748-830`):

- 규칙: 한 청크가 두 개념을 함께 언급하고, 두 제목이 **같은 문장** 안에 나타나는 첫
  문장을 근거로 삼는다. **같은 문서에 있었다는 사실만으로는 근거가 아니다.**
- 문장 분해 = `(?<=[.!?])\s+|\n+` (NLP 없음).
- `confidence = round(max(0.5, 0.9 - gap/400.0), 3)` — 두 제목 사이 거리가 짧을수록
  높고 **상한 0.9**. 사람이 확인한 근거가 아니라는 뜻을 수치가 스스로 말한다.
- `evidence` ≤ **300자**(`_EVIDENCE_MAX`), 공백 정규화.
- **이미 근거가 있는 엣지는 건드리지 않는다** — 반복 실행이 안전하다.
- `doc_id` 를 주면 그 문서가 언급하는 개념 사이 엣지만(편집 직후 증분), 없으면 owner
  전체. 기본 `limit=400`.

### 6.6 앱의 확인 흐름

파일 드롭 → `/doc/put`(origin=파일명) → `/doc/extract` **`apply:false` 미리보기** →
사용자가 한 번 누르면 `apply:true`(`app/src/doc-import.ts:87-116`). 자동 반영을 하지
않는 이유가 코드에 적혀 있다: 임의 문서의 헤딩이 그대로 개념이 되면 그래프가 오염되고,
들어간 개념은 `status='proposed'` 라 정리도 사람 일이다.

---

## 7. 표면

### 7.1 HTTP (사이드카 `api/server.py`)

| 메서드 | 경로 | 소관 | 비고 |
|---|---|---|---|
| GET | `/doc` | `docstore.get_doc` | 복원 + `verified` |
| GET | `/docs` | `docstore.list_docs` | 목록 + 주제 파생 |
| GET | `/doc/highlights` | `docstore.doc_highlights` | Reader 밑줄(D45-V2) |
| POST | `/doc/put` | `docstore.put_doc` | owner 미전송 시 **400** |
| POST | `/doc/delete` | `docstore.delete_doc` | 툼스톤 기본 on |
| POST | `/doc/extract` | `docstore.extract_doc` | owner 미전송 시 **400** |
| POST | `/doc/reembed` | `reembed_doc` + 임베딩 스택 | 스택 없으면 `note` 강등 |
| POST | `/evidence/backfill` | `docstore.backfill_evidence` | owner 필수 |
| GET | `/sync/docs-export` · POST `/sync/docs-apply` | `docsync` | §5.6 |
| GET | `/embed/pending` · POST `/embed/apply` | 자체호스팅 워커 | **루프백 내부**(프록시 미노출) |

정본 옆에서 같은 데이터를 읽는 **뷰별 원문 표면**(D45-V/V2)은 `api/query.py` 소관이며
이 계약의 소비자다: `/node-context`(노드 클릭 → one_liner + 근거 발췌),
`/edge-context`(흐름 선 클릭 → evidence·confidence), `/ledger-drilldown`(합계 구간 →
개념 목록), `/docs-in-range`(타임라인 기간 브러시).

쓰기는 전부 `jobs.run_sync` 를 지난다(단일 writer 직렬화).

### 7.2 CLI (`api/cli.py:145-165, 288-316`)

```
vinc doc put --owner … [--id …] [--title …] [--origin …] [--file …]   # 미지정 시 stdin
vinc doc get --id … [--owner …]
vinc doc rm  --id … [--owner …]
vinc doc ls  [--owner …]
```

`--file` 은 **편의 입력**일 뿐 정본은 스토어다(코드 주석 그대로). 매 호출
`store.provision(dim=…)` 으로 스키마 마이그레이션이 멱등 선행한다.

### 7.3 MCP (`vinc_mcp/tools.py`)

도구 **18종** 중 문서 정본은 **4종**이다 — 읽기 `vinc_doc_get`·`vinc_doc_list`,
쓰기 `vinc_doc_put`·`vinc_doc_delete`. 어노테이션: `vinc_doc_put` 은
`destructiveHint=false, idempotentHint=true`(같은 텍스트 재-put 은 no-op),
`vinc_doc_delete` 는 `destructiveHint=true`.

> ⚠ **MCP 에는 extract/reembed/highlights 도구가 없다** — 그 셋은 HTTP 전용이다
> (허브 프록시 op 로는 존재: `doc-extract`·`doc-reembed`).
> 또한 D45 로 쓰기 도구가 늘었으므로 "단일 write 표면(vinc_ingest 하나)" 서술은
> 더 이상 참이 아니다: 쓰기는 `vinc_ingest`·`vinc_delete`·`vinc_delete_edge`·
> `vinc_merge_nodes`(SEC-3)·`vinc_doc_put`·`vinc_doc_delete` **6종**이다.

### 7.4 웹 Vinc (허브 프록시)

허브 `_PROXY_OPS`(`vinc_mcp/http_server.py:46-84`)에 문서 표면이 등재돼 있다 —
읽기 `doc`·`docs`·`doc-highlights`·`node-context`·`edge-context`·`ledger-drilldown`·
`docs-in-range`, 쓰기 `doc-put`·`doc-extract`·`doc-reembed`·`doc-delete`·
`evidence-backfill`. **owner 는 서명된 `sub` 에서 강제 주입**되므로 클라이언트가
남의 owner 를 요청할 수 없다(SEC-3 + 프록시 이중). 데스크톱과 **같은 프론트엔드·같은
그래프 계약**이라 웹에서도 문서 편집이 완전 패리티로 선다.

> 운영 급소: 새 `/doc/*` 라우트는 사이드카 라우트 표 · 허브 `_PROXY_OPS` · 계정 프록시
> 허용목록 **세 곳에 동시 등재**해야 한다. 하나라도 빠지면 그 기능만 웹에서 조용히
> 죽는다(합계 뷰 실사고 전례).

### 7.5 앱 (데스크톱·웹 공통 프론트)

- **원문 파일 직접 수용**(2026-07-29): Intake 드롭존과 상단 바 `가져오기` 가
  `DOC_EXT`(현행 `.md/.markdown/.txt/.text/.rst/.pdf`)를 받는다. 텍스트는 `/doc/put`
  (문자열), **`.pdf` 와 U+FFFD 가 검출된 깨진 디코드(cp949 등)는 `BIN_EXT` 경로로
  `/doc/put-file`**(base64-in-JSON → 서버 `api/textextract.py` `TEXT_EXTS`/`PDF_EXTS`)
  로 갈린다. 확장자 목록을 문서에 박제하지 않는다 — 판별 SSoT 는 `doc-import.ts` 의
  `DOC_EXT`/`BIN_EXT` ↔ `textextract.py` 의 두 상수 쌍이고, 둘이 어긋나면 그 자체가 버그다.
  판별·적재 규칙은 진입점이 둘이라
  `app/src/doc-import.ts` 한 곳에 모았다(`surfaces/intake.ts`·`sheets.ts` 가 호출).
  전에는 드롭존이 팩·프래그먼트만 받아 `.md` 를 놓으면 `JSON.parse` SyntaxError 가
  그대로 노출됐다 — 파이프는 뚫려 있는데 UI 가 못 보여주던 상태.
- **재드롭 = 갱신**: `originIndex(owner)` 가 `/docs` 로 `origin → doc_id` 색인을 만들어
  기존 문서를 갱신한다(중복 문서 안 만듦). 색인 조회가 실패하면 전부 신규로 계속한다 —
  목록 하나 때문에 적재를 막지 않는다.
- **웹 본문 상한 `3_800_000` 바이트** — 허브 `MAX_BODY`(4MB)보다 낮게 잡는다. 허브는
  초과 본문을 413 이 아니라 **빈 본문**으로 돌려 사이드카가 `missing field: text` 400
  으로 받는데, 그러면 사용자에겐 크기 문제가 형식 문제로 둔갑한다. JSON 이스케이프가
  본문을 불리므로 **직렬화 후 길이로 재검**한다. 데스크톱은 사이드카 직결이라 상한 없음.
- **Reader·인라인 편집**: 발췌 → 전문(무손실 검증 배지·언급 밑줄), CodeMirror 6 인라인
  라이브 프리뷰 + 디바운스 자동저장, 원격 충돌 시 "(충돌본)" 스냅샷 보존
  (`Vinc/CHANGELOG.md` v0.6.5).

### 7.6 팩 · 배치

- **공유 팩**(D19, [[vinc.c.sharepack]]): `vinc_pack:1` 의 선택 `documents` 필드에
  원문 전문을 실어 **자기완결 공유**를 만든다. import 는 개념 MERGE **다음에** 원문을
  넣고, `put_doc` 이 **수신 owner 로 재청킹**한다 — 원 `doc_id` 를 재사용하지 않는다
  (수신 owner 기준으로 파생해 충돌·탈취를 원천 차단). 구버전 팩은 `documents` 가 없어
  이 단계를 스킵한다(하위호환, `api/share.py:215-251`).
- **배치 임베딩 파이프라인**: `engines/embed_pipeline.py` 가 parquet 을 버리고
  `doc_id_for_origin` + `doc_hash` 증분 판정 후 **`put_doc` 단일 지점**을 경유한다
  (`:30, 326-370`). chonkie 청크의 `start_index` 목록을 `boundaries` 후보로 넘기고,
  미노출 빌드면 빈 목록 → 내장 경계 폴백.

---

## 8. 한계와 후속

### 8.1 알려진 한계 (설계상 수용)

1. **레거시 청크는 복원 불가.** `char_start/char_end = -1`(스키마 v4 이전 적재)인 행은
   검색만 가능하고 `verified=false` 로 보고된다. 재-put 이 자연 치유한다.
2. **레거시 무소유 청크(`owner_id=''`)는 백필하지 않는다.** 파일 파생물이라 소유 원천이
   없다 — 소유 스코프 purge·격리에서 자연 제외되지만, 뒤집어 말하면 **그 행들은 소유로
   가를 수 없다**.
3. **다중 문 트랜잭션이 없다.** §4.3 의 순서와 자기치유는 **임베디드 단일 프로세스**
   가정 위에 선다. 다중 writer 환경으로 그대로 옮길 수 없다.
4. **동기화는 doc 단위 LWW** — 같은 문서를 두 기기에서 동시에 고치면 한쪽이 통째로
   진다(필드 병합 없음). 앱이 "(충돌본)" 스냅샷으로 손실을 완화할 뿐, 계약 자체는
   원자 교체다.
5. **임베딩 결측 창이 존재한다.** 앱 편집·동기화 수신 직후 `needs_embed=1` 구간에서는
   FTS 만 서빙한다. 사이드카 바이너리는 임베딩 스택을 번들에서 제외(2.7GB 회피)하므로
   데스크톱의 `/doc/reembed` 는 스택이 없으면 `note` 로 강등되고, 허브는 자체호스팅
   워커가 채운다.
6. **인덱스는 지연 재빌드**(§4.4) — 편집 직후 벡터 검색이 잠시 낡을 수 있다. FTS 는
   즉시 반영된다.
7. **추출은 마크다운 헤딩 구조에 의존한다.** 헤딩 없는 산문·`.txt` 는 후보가 0 이다.
   근거(`evidence`)도 "같은 문장 동시 등장"만 인정하므로 보수적으로 비어 있다.
8. **문서에 주제 열이 없다.** 링크가 생기기 전 문서는 목록에서 주제가 빈 문자열이다
   (§1.3).

### 8.2 후속

- **DEC 등재**: [[vinc.b.architecture]] 의 DEC 레지스터가 D44 에서 멈춰 있어 D45 가
  없다. 이 문서 저작과 함께 `master-id-map.yml`(VINC-C-010) · `vinc/C/README.md` 는
  등재했고, DEC 레지스터 등재가 남아 있다.
- ~~**`vinc.mcp`(MCP-INTERFACE.md) 정정**~~ → **해소(2026-07-29)**: 도구 표에서 코드에 없는
  `vinc_themes_*`/`vinc_types_*` 7종을 걷어내고(grep 0건 — 주제·어휘는 HTTP 전용),
  현행 18종(읽기 12·쓰기 6) + 문서 정본 4종을 반영했다([[vinc.mcp]] v0.4).
  같은 7종 허위 기재가 전파돼 있던 [[vinc.b.theme]] §4 도 HTTP 전용으로 정정됐다
  (`theme_id 필수 → lazy 확정` 포함).
- **Chunk 추적열**: `embedded_schema.py:104-106` 이 "Chunk 를 `_TRACKED_LABELS` 에
  추가 + 마이그레이션 백필" 을 예정으로 적어 두었으나, P3 는 **doc 단위 동기화**로
  귀결해 그 경로를 타지 않았다. 예정 주석과 실제 결정이 어긋나 있어 주석 정리가 필요하다.
- **텍스트 추출기**: ~~PDF·DOCX 등 바이너리 포맷은 범위 밖~~ → **PDF 는 범위 안이 됐다**
  (2026-07-29, `api/textextract.py` + `/doc/put-file`). 인코딩 폴백도 서버측 추출기가
  받는다(깨진 디코드는 바이트로 재전송). 남은 범위 밖 = **DOCX 등 그 밖의 바이너리**이고,
  실패는 `docError.*` 강등 코드(`pdf_encrypted`·`pdf_no_text`·`pdf_support_missing`·
  `unsupported_format`)로 카피에 도달한다.

---

## 참조

- 구현: `Vinc/api/docstore.py` · `Vinc/api/docsync.py` · `Vinc/api/server.py`(`/doc/*`) ·
  `Vinc/api/cli.py`(`doc`) · `Vinc/vinc_mcp/tools.py`(`vinc_doc_*`) ·
  `Vinc/vinc_mcp/http_server.py`(`_PROXY_OPS`) · `Vinc/api/share.py`(`documents`) ·
  `Vinc/engines/embed_pipeline.py` · `Vinc/app/src/doc-import.ts`
- 스키마: `Vinc/store/embedded_schema.py`(Chunk v4 · `_DocMeta` · `_DocTombstone` v5 ·
  `needs_embed` v6), `Vinc/store/embedded_backend.py`(인덱스 수명주기)
- 계약 핀: `Vinc/tests/test_docstore.py`
- 릴리스: `Vinc/CHANGELOG.md` v0.6.5(D45 P0~P4 · V/V2 · E)
- 인접 정본: [[vinc.c.dataplane]](스토어·엔진·마이그레이션) · [[vinc.c.sharepack]](D19 팩) ·
  [[vinc.arch]](아키텍처) · [[vinc.mcp]](MCP 도구 계약)
