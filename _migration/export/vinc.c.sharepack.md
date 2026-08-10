<!-- vinc-export
doc_id: vinc.c.sharepack
title: Vinc Share Pack — 계정 간 데이터 전달 계약
origin: Vinc-docs/vinc/C/vinc.c.sharepack.md@5090690
chunks: 3
verified: true
content_hash: 290bb68f10f54be916d5562c5110da68bc1189ddfea69323ffc4a2263aa53d36
updated_at: 2026-08-01T16:20:28.893Z
-->

---
doc_id: vinc.c.sharepack
layer: C
domain: sharepack
title: Vinc Share Pack — 계정 간 데이터 전달 계약 (DEC D19)
status: draft
authority: SSoT
version: v0.1
owner: vinc
last_updated: 2026-07-10
inherits_from: [vinc.c.dataplane, vinc.mcp]
tags: [vinc, C, sharepack, export, import, dec, d19]
---

# Vinc Share Pack — 계정 간 데이터 전달 계약

> **DEC D19 — 채택(발주자 승인 2026-07-10).** 한 계정이 자기 컴퓨터에 적재한
> 데이터를 **다른 사람에게 전달**하기 위한 패키징(내보내기/들여오기) 계약.
> 구현체: `Vinc/api/share.py`(정본), CLI `vinc share`/`vinc import`,
> 사이드카 `GET /share`·`POST /ingest/pack`.

## 1. 설계 결정 3가지

1. **포맷 = MCP fragment 동형** ([[vinc.mcp]] §5). 팩 본문이 `{nodes, edges}`
   fragment 그 자체라서, 들여오기는 `api/fragment.merge_fragment`(id 정확 매칭
   MERGE·멱등) **재사용 하나로 끝난다** — 별도 import 기계 없음. GUI 로 만든 팩을
   MCP 상류(Claude)가 읽고 재저작하는 것도 같은 스키마라 자유.
2. **커스텀 스코프** — "도메인 전체"가 아니라 "이 영역까지만"을 1급 지원:
   - `domain`: 데이터셋 구획 하나
   - `tags`: 태그 교집합(any-overlap) 필터
   - `seed`+`depth`(1–4): **Atlas L2 와 동일한 ego 서브그래프** — 그래프 화면에서
     보고 있는 영역을 그대로 잘라 패키징한다(D16 과 같은 순회 플랜 공유).
3. **preference 신호 미포함.** `pinned`/`access_count`/`last_access` 는 원 계정의
   개인 신호다 — 공유 투영이 해당 열을 **선택하지 않는다**(스크럽 단계 불요,
   구조적 미유출). 수신자는 자신의 owner 로 재스탬핑해 들여온다.

> D4(pack.json 에 preference 완전 수록)와의 관계: D4 는 **본인 마이그레이션**
> (무손실 이관) 계약이고, D19 share pack 은 **타인 전달** 계약이다 — 목적이 달라
> 신호 정책이 반대다. 두 팩은 `kind` 필드로 구별한다(`share`).

## 2. 포맷 (v1)

```json
{
  "vinc_pack": 1,
  "kind": "share",
  "manifest": {
    "created": "2026-07-10T05:30:00Z",
    "scope": { "domain": null, "tags": [], "seed": "math-…-집합론-논리-증명", "depth": 1 },
    "counts": { "concepts": 9, "records": 0, "edges": 45, "documents": 3 },
    "note": "Preference signals are not included — they stay with the origin account."
  },
  "fragment": { "nodes": [ … ], "edges": [ … ] },
  "documents": [
    { "doc_id": "…", "title": "…", "origin": "…",
      "content_hash": "sha256…", "updated_at": "…", "text": "<원문 전문>" }
  ]
}
```

- 노드: fragment 노드 5종([[vinc.mcp]] §5). Episode(레코드)는 `date`/`summary`
  콘텐츠 필드를 동반(수입 시 열로 사상). Concept 의 `topic`/`one_liner` 는
  `props{}` 로 이동.
- 엣지: `relates_to | prerequisite_of | derives_from | inherits_from` — 스코프
  경계 밖으로 나가는 엣지는 절단(잘린 영역이 곧 팩의 전부).
- **`documents` (선택, D45 — 자기완결 팩)**: 팩 개념이 참조하는 원문 문서를 함께
  싣는다(Chunk=SSoT의 복원 텍스트 — `{doc_id, title, origin, content_hash,
  updated_at, text}`, `export_docs` 동형). **`embedding·청크 오프셋은 안 보낸다`**
  (수신 측 재청킹·재생성). 빌드는 `Chunk-[:MENTIONS]->Concept` 역추적으로 그 팩의
  개념이 언급된 문서만 고른다(자기완결). **없으면 개념만 담긴 팩**(D45 이전과 동일).
- 버전: `vinc_pack` 정수. 미지원 버전은 들여오기 거부(명시 오류). **`documents` 는
  버전을 올리지 않는 additive 선택 필드** — 구버전 앱은 이 필드를 무시하고 개념만
  들여온다(하위호환).

## 3. 들여오기 규약

- `vinc import --pack <file> --owner user:me` — fragment 에 수신자 owner 를 명시
  주입해 MERGE. **멱등**: 재수입은 `matched` 로 집계되고 owner 를 덮어쓰지 않는
  기존 노드 규칙(fragment 계약)을 따른다.
- id 는 원 계정의 id 그대로 MERGE 되므로, 같은 원천에서 갱신된 팩을 다시 받으면
  제자리 갱신된다(원본 exact-match 동작과 동형).
- **`documents` 들여오기 (D45)**: fragment(개념) MERGE **다음에** 원문을 `put_doc`
  으로 복원한다(MENTIONS 재링크가 방금 들어온 개념에 붙게 하는 순서). **doc_id 는
  재사용하지 않고 수신 owner 로 새로 파생한다** — 개념 id 와 달리 문서는 교차-owner
  공유에서 원저작자 doc_id 를 쓰면 호스티드 단일 스토어의 그의 `_DocMeta`(owner
  불일치)에 부딪혀 거부되기 때문. `(수신owner, content_hash, title)` 파생 → 충돌
  회피 + 내용 동일 재수입은 멱등(`content_hash` 매치 시 skip, 중복 문서 0).

## 4. 실측 (weg-9000 실데이터, 2026-07-10)

weg9000-hub 학습 노트를 `concepts_ingest`/`bridges_ingest` 로 적재한 실스토어에서
왕복 검증:

| 단계 | 결과 |
|---|---|
| Alice 적재 | :Concept 91(4개 도메인) · intra 관계 645 · :Bridge 11 |
| 팩 A `--domain math` | 11 concepts · 59 edges |
| 팩 B `--seed math-foundations-01-집합론-논리-증명 --depth 1` | **9 concepts · 45 edges — 커스텀 영역** |
| Bob 수입 | 9 노드 전부 `owner_id=user:bob` 재스탬핑, seed 서브그래프 탐색 가능 |
| 재수입 | created 0 / matched 9 (멱등) |
| 유출 검사 | pinned·access_count·last_access 0건, Alice 원본 무손상(91 · user:alice) |

## 5. 범위 밖 (후속 DEC 대상)

- 팩 서명/암호화(전달 채널 신뢰는 사용자 몫 — 로컬 파일). ⚠ **부분 해소(D55)**:
  zip 컨테이너로 바뀌면서 **CRC 무결성**은 공짜로 따라온다(전송 중 손상 감지).
  서명(출처 인증)은 여전히 미도입 — 커뮤니티 갤러리가 라이브인 만큼 후속 후보.

## 파일 형식 — zip 컨테이너 (D55, 2026-07-26)

`.vincpack` 은 여태 **확장자만 있고 컨테이너가 없었다**(매직넘버·압축·무결성 전무,
pretty-print JSON). D45 가 팩에 원문(`documents`)을 싣기 시작하면서 세 가지가 한꺼번에
아팠다: 크기(개념 74개짜리가 이미 1.6MB) · 통짜 파싱(문서 하나만 못 꺼냄) ·
바이너리(이미지·PDF 를 넣으려면 base64 로 33% 부풀음).

```
manifest.json          {vinc_pack, kind, manifest{...}}
fragment.json          {nodes, edges}
documents/0001.json …  원문 한 건당 한 파일
```

- **읽기는 두 형식 모두, 쓰기만 zip.** 커뮤니티 갤러리에 구 JSON 팩이 이미 올라가
  있으므로 하위호환이 핵심 계약이다. 판별은 첫 바이트(`PK` = zip, `{` = 구 JSON).
- **`vinc_pack` 정수는 올리지 않는다** — 컨테이너가 바뀐 것이지 내용 계약이 아니다.
- **결정론**: zip 헤더 시각을 1980-01-01 로 고정해 같은 팩이면 같은 바이트가 나온다
  (해시 비교·중복 판정이 의미를 갖는다).
- 구현: 사이드카 `api/packfile.py`(stdlib `zipfile`) · 앱 `app/src/packfile.ts`
  (zip 최소 골격 직접 작성 + 네이티브 `CompressionStream("deflate-raw")` — 이 앱엔
  zip 라이브러리가 없고 CSP 가 CDN 을 막으므로 **의존성 0** 을 지킨다).
- 실측: 1.62MB → 461KB(28%). 양방향 상호운용 확인(TS 산출물을 파이썬 `zipfile` 이
  CRC 통과로 읽고, 그 역도 성립 — 한글·이모지·5000자 문서 무손실).
- ~~Chunk/임베딩 동반~~ → **§2·§3 `documents` 로 구현(2026-07-24, D45).** 단
  **임베딩은 여전히 미동반** — 수신 측 재생성이 정본(모델 불일치 회피). ⚠ 현재
  shipped 사이드카(허브·데스크탑)는 임베딩 스택을 번들에서 제외(sidecar.spec) →
  공유 원문은 **FTS 즉시·벡터 지연**(어느 표면도 아직 재생성 안 함, 별도 DEC).
- 과금·계정 연동 일체(F1 하드 룰)

## 부록 Z. 미분류 원문 사실
(없음)
