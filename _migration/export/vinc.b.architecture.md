<!-- vinc-export
doc_id: vinc.b.architecture
title: Vinc 아키텍처 불변 원칙·확정 결정 (정본)
origin: Vinc-docs/vinc/B/vinc.b.architecture.md@5090690
chunks: 9
verified: true
content_hash: 19a9e39201a8292a3f02ed11ac3d4665f1cbc8b9043e71c2934702097426ba89
updated_at: 2026-08-01T16:20:19.888Z
-->

---
doc_id: vinc.b.architecture
layer: B
domain: architecture
title: Vinc 아키텍처 불변 원칙·확정 결정 (정본)
status: draft
authority: SSoT
version: v0.2
owner: weg-9000
last_updated: 2026-07-29
inherits_from: [weg.c.memory-standalone]
tags: [vinc, B, architecture, invariants, decisions, embedded, dialect]
---

# Vinc 아키텍처 불변 원칙·확정 결정 (정본)

> **아키텍처 SSoT.** Vinc 구현에서만 유효한 **불변 원칙 Delta**·**확정 결정(DEC)**·
> **P1 실측 반영**의 정본이다. 제품 설계 사실은 재기재하지 않고
> [[weg.c.memory-standalone]] 를 `#§N` 으로 참조한다(B 는 SSoT 루트, C 는 본 문서를
> 참조만 — 재기재 금지). 코드 권위는 `Vinclums/Vinc` 라이브 코드다.
>
> 이 문서는 위험 E1(정본-코드 드리프트) 완화의 인스턴스 측 축이다 — P1-0·P1
> 실측이 설계 정본에 반영될 때 그 **Vinc 바인딩·확정치**를 여기 동반 기록한다.

## 1. 불변 원칙 Delta

설계 [[weg.c.memory-standalone#§1]] 불변 원칙의 Vinc 인스턴스 구속을 명시한다
(원칙 본문은 참조, 재기재 금지).

| # | 원칙 | Vinc 구속 | 참조 |
|---|---|---|---|
| I1 | 스코프 1차 | owner/domain/tenant 필터가 **검색 경로에도** 선적용(pre-filter). 스코프 밖 데이터 혼입 0 은 게이트 조건 | [[weg.c.memory-standalone#§1]]·§4-2 |
| I2 | 플랜 순수성 | 스토어 도달 불가·미프로비저닝 시 `executed=False` + 순수 플랜 반환(무 크래시). `verify.py`(무 DB)가 백엔드 불문 상시 선행 | [[weg.c.memory-standalone#§11]] |
| I3 | 원자성 미보장 | `GraphStore` 는 다문장 원자성을 계약하지 않는다 — 호출부가 멱등(MERGE 키)으로 방어 | [[weg.c.memory-standalone#§4]] |
| I4 | 단일 writer | 쓰기는 잡 큐 단일 워커로 직렬화(임베디드 단일 프로세스 동시성 상한 R4) | [[weg.c.memory-standalone#§3]] |
| I5 | 오프라인 1차 | 런타임 외부 다운로드·API 키 없이 기동(F5). 임베딩 기본 `local-en`(키 불요) | [[weg.c.memory-standalone#§1]]·§8 |

## 2. 확정 결정 (DEC)

설계 §13 DEC 중 Vinc 에서 **확정된** 값. 미확정분은 설계 §13 이 정본.

| DEC | 확정치 | 근거 참조 |
|---|---|---|
| D14 | **역전·현행(v0.5.3, 2026-07-21):** 임베디드 백엔드 = **LadybugDB(`ladybug`, Kùzu 계보, v0.18.x) 단일**. 드라이버 취득은 무조건 임포트 1줄(`store/embedded_backend.py:_import_driver` → `__import__("ladybug")`)이고, extras 도 `embedded = ["ladybug"]` 하나다. 벡터(HNSW)·FTS(BM25)·algo 는 정적 링크가 아니라 **확장**(런타임 다운로드형)이라 오프라인 배포 시 `.lbug_extension` 번들 동봉이 필수다(G0 — `_ext_path`/`_load_extensions`). ⟨원 결정 2026-07-16: 기본 = **kuzu 0.11.3**(벡터/FTS 정적 링크로 오프라인 즉시), ladybug 는 `embedded_driver` 옵트인(R1 교체 보험).⟩ 개정 경위: v0.5.0 전환 레일(저장 포맷 비호환 → 논리 export/import 만 가능) → v0.5.1 ladybug 3중 실증(앱 완전 호환·G0 오프라인 확장 번들·교차 이관 무손실)로 신규 설치 ladybug + 마커(`<store>.driver`) 기반 안전 전환 → **v0.5.3 에서 테스트 기간·기존 사용자 없음 확인(발주자)으로 kuzu 완전 제거**, 듀얼드라이버(마커·자동감지·이관 레일) 전면 폐기. R1(계보 포크 교체 보험)이 실제로 행사된 사례이며, 어댑터 1파일 상한 덕에 교체 비용이 그 파일에 갇혔다 | `Vinc/CHANGELOG.md` v0.5.0·v0.5.1·v0.5.3 · `Vinc/pyproject.toml` extras · [[weg.c.memory-standalone#§13]]·§2-2·§9 |
| §8 | 임베딩 **영어 1차** — 기본 `local-en`(bge-base-en-v1.5, 768d). `--model` 어휘는 store.config 단일 출처와 동일(local-en/local-en-large/anthropic/local) | [[weg.c.memory-standalone#§8]] |
| D6 | pack export 기본 `include_model=False` — learner 섹션 미노출(학습 프레이밍 미승계). 마이그레이션(A+2) 무손실 이관 시에만 `True` | [[weg.c.memory-standalone#§13]] |
| D4 | pack.json 에 preference/learner 신호 완전 수록(마이그레이션 선행 조건) | [[weg.c.memory-standalone#§10]] |
| D7 | **무암호 단일 프로필 기본** — 로그인은 첫 화면이 아니라 Settings 옵트인 잠금(passphrase). 발주자 승인 2026-07-10 | [[vinc.d.ux-research#§2]] |
| D15 | **표면 트리 = 5표면 + Settings**(Intake·Records·Atlas·Search·Packs) — 8화면(D8) 개정. 로그인 강등·온보딩 흡수·분포는 Atlas L0 로. 발주자 승인 2026-07-10 | [[vinc.d.ux-research#§2]]·[[vinc.d.index]] v0.2 |
| ⤷ D15 개정 | **개정·현행(v0.6.7, 2026-07-24~27):** 좌측 레일 폐지 → **상단 바 단일 셸(D56)**, 라우팅되는 표면은 **3**(`atlas`=사용자 표기 `Home` · `search` · `packs`)이고 구 표면은 별칭으로 흡수된다(`intake→packs`·`records→search` — `app/src/main.ts` `SURFACES`/`ORDER`/`ALIAS` 실측). 검색이 조회의 **제1 진입점**(D57), 첫 화면은 **주제 표 + 결합 미니그래프**(D58). D15 의 원 취지(로그인 강등·온보딩 흡수)는 살아 있고 표면 **수**만 접혔다 | `Vinc/CHANGELOG.md` v0.6.7 · `app/src/main.ts` |
| D16 | **Atlas 3-레벨 시맨틱 줌**(L0 도메인 지도 / L1 도메인 렌즈+게이트웨이 / L2 노드 포커스). 발주자 승인 2026-07-10 | [[vinc.d.ux-research#§4]]·[[vinc.d.graph]] v0.2 |
| ⤷ D16 개정 | **개정·현행(v0.6.0~v0.6.6):** Atlas 는 **관점 4종**(3D · 2D · 타임라인 · 합계)으로 재편됐다 — 흐름 관점 제거(D53), 2D 는 독립 렌더러(D46, `app/src/surfaces/atlas2d.ts` · sigma), 평면 관점은 L0 에서 **전량** 렌더(D46-S), 합계는 3열 생키(D47), 툴팁 자체 구현(D48-T). 시맨틱 줌 L0/L1/L2 축 자체는 유지. 관점 4종은 코드 실측(`app/src/atlas-prefs.ts` `AtlasView = "3d"\|"2d"\|"timeline"\|"totals"` · `VIEWS`) — v0.6.0 이 5종(흐름 포함)으로 출하됐다가 D53 으로 흐름이 빠졌다(**CHANGELOG v0.6.7 에 2026-07-30 보정 등재**). ⚠ 흐름이 관계 근거 패널(D45-V)의 유일한 입구였으므로 진입점을 **2D 선 클릭**으로 이설한 것이 이 개정의 급소다 | `Vinc/CHANGELOG.md` v0.6.0·v0.6.6·v0.6.7 · `app/src/atlas-prefs.ts` · [[vinc.d.atlas-visual-language]] |
| ⤷ D16 개정 2 | **지도 크롬·리본(v0.6.7~, 2026-07-27~28):** 지도 손잡이를 하단 바 하나로 통합(D65 — 렌즈·관점·지표), 화면 위에 뜨는 것의 규격 통일(D63 시트), 노드 우클릭 메뉴(D64). 합계 관점은 경로 보존 리본(D63 리본)에서 **barycenter lode 배치**(D68, 신규 순수 모듈 `app/src/surfaces/atlas-lodes.ts`)로 갔다 — ⚠ **교차 총수는 배치의 불변량**이라(stratum 순서 고정 + 띠 정합 하의 정리, 실측 41=41·124=124) 배치가 바꾸는 것은 교차의 갭 분산과 리본 기울기(방랑 J −8.6%)다. 최적화 대상을 "교차 감소"로 적으면 틀린다. **D63 이 시트와 리본에 이중 사용** 중이며 그 백로그는 D층에 기록돼 있다 | [[vinc.d.graph-views]] §2.3.2 · [[vinc.d.alluvial-research]] · `app/src/surfaces/atlas-lodes.ts` |
| ⤷ D16 개정 3 | **원장(`/ledger`) 집계축 수정(2026-07-26)** — 합계 관점을 떠받치는 집계가 두 군데서 틀려 있었다. ① **D52**: `/ledger` 가 **항상 도메인 단위**로만 집계해, 주제를 눌러 들어가도 가운데 열이 그 주제 하나로 쪼그라들었다(실측 `?domain=physics` → 행 1개). `by=concept` 모드를 신설해 L1 에서 개념 알갱이를 요청하고, 응답 `by` 에코로 구 사이드카를 판별해 우아 강등한다 ② **D54**: 문서 유입 경로가 둘인데(구 `Record` ATTACHES_TO + D45 `Chunk` MENTIONS) 원장이 **구 축만 봐서** 앱에서 문서를 넣거나 팩에 `documents` 를 실어 와도 출처가 영원히 "없음"이었다(실측: 개념 74개 전부 unsourced). Chunk 분기를 UNION 에 더해 같은 `record` 칸으로 접는다 — 화면 문구 "파일에서"가 두 물리 표현을 같은 뜻으로 덮는다. owner 는 Chunk `owner_id` 로 양 끝 가드(SEC-1) | [[vinc.d.atlas-visual-language]] D52·D54 |
| D17 | **그래프 레이아웃 사전 계산** — 사이드카가 결정론 좌표를 산출, UI 는 소비만. 발주자 승인 2026-07-10 | [[vinc.d.ux-research#§4.4]] |
| D18 | **영어 카피 시스템** — verb-first·서사 금지(F2) 보이스 가이드 + 표면 네이밍 + 4-State 카피 덱. 발주자 승인 2026-07-10 | [[vinc.d.ux-research#§6]] |
| D19 | **Share Pack** — 계정 간 데이터 전달 팩. MCP fragment 동형 포맷, 커스텀 스코프(domain·tags·seed+depth), preference 신호 구조적 미포함, 수신자 owner 재스탬핑. 발주자 승인 2026-07-10 | [[vinc.c.sharepack]] |
| ⤷ D19 개정 | **`.vincpack` 이 zip 컨테이너가 됐다(D55, `api/packfile.py`)** — 그전까지 확장자만 있고 컨테이너가 없었다(그냥 pretty-print JSON). D45 가 팩에 **원문(`documents`)** 을 싣기 시작하며 셋이 한꺼번에 아팠다: 크기(개념 74개가 이미 1.6MB) · 통짜 파싱 · 바이너리 base64 팽창. 레이아웃 = `manifest.json` + `fragment.json` + `documents/NNNN.json`. ⚠ **핵심 계약은 하위호환이다 — 읽기는 두 형식 모두, 쓰기만 zip**(첫 바이트 `PK`/`{` 로 판별). `vinc_pack` 정수는 올리지 않는다: 컨테이너가 바뀐 것이지 내용 계약이 바뀐 것이 아니다. ⚠ **커뮤니티 갤러리는 JSON 전용**이다(`pack_json` MEDIUMTEXT · 다운로드 `Content-Type: application/json`) — 갤러리에 넣을 때는 zip 을 풀어 JSON 으로 저장하는 것이 **정상 경로**다(이걸 모르고 zip 을 그대로 시딩하려다 실패한 사례 있음, 2026-07-30) | `Vinc/api/packfile.py` · [[WEB-vincs-app]] §8.2 |
| D31 | **Atlas 표현 강도 커스터마이징 2층 정책** — 열린 층(2D↔3D 토글·크기·라벨·화살촉·산개 = 사용자 소유, localStorage) / 닫힌 층(타입→형태·포인트색=초점·모노크롬 = 스펙 소유). 발주자 승인 2026-07-10 | [[vinc.d.atlas-visual-language#§1.5]] |
| D32 | **Atlas MOC 구조 규율** — 허브 승격 서열(pin>degree, 레이아웃·크기), 고아 최소화(Unlinked 필터 + `/suggest-ties` 태그·도메인 근사 → fragment MERGE 로 Bind), hover 이징 페이드+엣지 후퇴, L2 딥링크. 발주자 승인 2026-07-10 | [[vinc.d.atlas-visual-language#§1.6]] |
| D33 | **Atlas 엣지·재질 개정** — 엣지 헤어라인(튜브 폐기)+곡률 0.18, 농도=가중치(굵기 개정), 노드 플랫 셰이딩 Standard+반구광. 발주자 지시 2026-07-10 | [[vinc.d.atlas-visual-language#§1.2]] |
| D34 | **Atlas 레이아웃 프리셋** — `/graph?layout=` 5종(순수 함수, D17 유지) + View 패널 선택 + Multiples 카드별 오버라이드 + 그리드 미니 3D(단일 렌더러 뷰포트 분할). 발주자 승인 2026-07-10 | [[vinc.d.atlas-visual-language#§1.7]] |
| ⤷ D34 개정 | **프리셋 목록 개편(D51, 2026-07-26)** — ① `shell` **퇴출**: 반경이 정렬 순위일 뿐 연결을 안 보고 간격이 노드 수에 적응하지 않아 74노드에서 **54%가 이웃과 겹쳤다**(실측). 함수는 `organic` 초기 배치로만 존치, 기본값은 **radial**(겹침 1/74) ② `dendro` 신설(좌→우 tidy tree, 뼈대는 **방향 엣지만** — 전체 엣지는 평균 차수 17.2 라 3층에서 끝나고 방향만 쓰면 9층) ③ **밀도 적응 노드 크기**: sigma 는 auto-fit 인데 노드 크기는 절대 px 라 `spread` 가 2D 에서 무효였다. 함께 **`rank` 프리셋 신설(D50)** — 좌표가 전역 위상 순서를 인코딩(x=랭크)하며 도메인 클러스터를 만들지 않는다(계보가 도메인을 가로지르므로). D17 의 "순수 함수·결정론" 은 전부 유지 | [[vinc.d.atlas-visual-language]] D50·D51 · [[vinc.d.view-redesign-research]] |
| D35 | **Atlas 프리미티브 재배정** — Concept=구·Domain=구+링(토성형)·Episode/Record=작은 정육각기둥(전 모서리 등길이)·Bridge=정팔면체 유지. 발주자 지시 2026-07-10 | [[vinc.d.atlas-visual-language#§1.1]] |
| D36 | **원격 MCP 프로파일**(옵트인) — 클라우드 VM: Caddy(TLS)+MCP 어댑터(Streamable HTTP+OAuth)→루프백 사이드카(무변경, F4 유지), 정적 홈페이지 동거, 로컬 기본(D7·F4) 불변. 발주자 결정 2026-07-10 | [[vinc.d.remote-profile]] |
| D37 | **Liquid Glass 층 분리 수용** — 부유 크롬만 글래스(backdrop-filter, 불투명도 하한 .58, 폴백=현행 카드), 콘텐츠 평면은 Ink & Paper 유지. 시안 채택 — 발주자 검토 대기 2026-07-10 | [[vinc.d.glass-language]] |
| D38 | **기본 다크 + 개발자 정보 은닉** — 표면 카피에서 엔드포인트·CLI·내부 용어 제거/자연어화, Settings Advanced 접힘. 발주자 지시 2026-07-10 | [[vinc.d.desktop-shell#§DEC]] |
| D39 | **다크 Atlas 반전 팔레트** — 어두운 배경(투명 캔버스+대기) + 밝은 잉크 마크, fog·엣지·라벨·초점 동반 반전. 발주자 지시 2026-07-10 | [[vinc.d.atlas-visual-language#§6]] |
| D40 | **글래스 노드 재질** — 프로스티드 Physical(불투명도 하한: 노드 0.72/게이트웨이 0.35) + 환경맵 반사, 모노크롬 유지. 발주자 지시 2026-07-10 | [[vinc.d.atlas-visual-language#§6]] |
| D41 | **macOS 컨트롤 문법** — 세그먼티드 컨트롤(.seg) + mac 버튼 폴리시. 발주자 지시 2026-07-10 | [[vinc.d.desktop-shell#§DEC]] |
| D42 | **표면 언어 en/ko 단일 배포** — 카피 덱 이중화 + Settings 언어 세그 + OS 로케일 기본. 한국어 덱은 F2 통과 어휘로 저작. 발주자 승인 2026-07-10 | [[vinc.d.desktop-shell#§DEC]] |
| D43 | **기본 데이터 홈 `~/.vinc` + P4 릴리스 파이프라인** — CLI 진입점만 `~/.vinc/db` 해석(라이브러리 기본 None 유지), PyInstaller 사이드카 단일 바이너리 → Tauri externalBin + `.vincpack` 연결(D29 이행) + Actions 3-OS 드래프트 릴리스. 발주자 승인 2026-07-11 | [[vinc.d.desktop-shell#§DEC]] |
| D44 | **코드서명·리포 공개 방향($0 경로)** — 미서명 배포의 첫 실행 경고(Windows SmartScreen·macOS Gatekeeper) 대응: `Vinclums/Vinc` public 전환(히스토리 시크릿 감사 클린 — 키·`.env` 커밋 0건) → **Windows = SignPath Foundation 무료 OSS 서명**, **macOS = 미서명 유지 + 다운로드 페이지 첫 실행 우회 안내(라이브)**. 공개 전환으로 CI 무료화(비공개 mac 러너 10배 가중 비용 회피). Apple Developer($99/yr)는 macOS 서명 원할 때만. 발주자 결정 2026-07-13 | [[vinc.d.desktop-shell#§DEC]]·`plans/PLAN-code-signing-notarization.md` |
| D45 | **문서 정본(Chunk=SSoT)** — 사용자 원문 텍스트의 정본을 그래프 안 `Chunk` 로 내재화한다(문서 blob 열·원본 파일 참조 미신설, 파일은 입력 편의). 문서의 조인 키는 경로가 아니라 `doc_id`(`origin`/`source_file` 은 출처 표시로 강등), 무소유 정본 금지(SEC-1). 개념·관계는 원문에서 **결정론 파생**(마크다운 헤딩 규칙 — LLM·외부 API 0)이라 지식 입력의 **제3 경로**가 된다(① MCP `vinc_ingest` ② 팩 import ③ 문서 정본). 표면: HTTP `/doc/*` · MCP `vinc_doc_*` · CLI `vinc doc` · 앱 편집기/드롭존. v0.6.5 출하 2026-07-24(현행 v0.6.7) | [[vinc.c.chunk-ssot]]·`Vinc/CHANGELOG.md` v0.6.5 |

## 3. 임베디드 백엔드 계약

- **드라이버 취득 단일 지점:** `store.backend.open_store` / `store.embedded_backend._import_driver`
  1곳만 드라이버명에 결합한다(R1 — 계보 포크 교체를 어댑터 1파일로 상한). 현행은
  **ladybug 단일 무조건 임포트**이며, 미설치면 `(None, None)` 을 돌려 호출부
  (`EmbeddedBackend.open`)가 스토어 unreachable 로 강등한다(I2 — 무크래시,
  `/health` 로 원인 노출). 상세 [[weg.c.memory-standalone#§4]].
  - **폐기된 정책(v0.5.3):** "ladybug·kuzu 는 한 프로세스 공존 불가(네이티브 타입
    재등록 충돌) → **첫 성공 임포트에서 반환**" 이라는 다중 드라이버 폴백 레일은
    kuzu 완전 제거(D14 개정)와 함께 죽었다. 같은 폐기 묶음: 마커 파일
    (`<store>.driver`) 자동감지·안전 이관 레일·드라이버별 사이드카 변형 빌드.
    `store/migrate.py` 는 전환-레일 프레이밍만 걷어내고 **범용 논리 export/import
    도구**로 남았다. 공존 불가 자체는 사실이었으나, 후보가 하나뿐인 지금
    **구속력 있는 규율이 아니다** — 새 계보 포크를 다시 후보에 올릴 때만 되살아난다.
- **스키마 선행:** 노드/관계 테이블을 먼저 선언한다(schema-free 아님). 열 명세는
  엔진 SET 전수 조사가 SSoT — 투기적 열 금지. 상세 [[weg.c.memory-standalone#§5]].
  - **"투기적 열 금지" ≠ "타입 값 고정"(D11-R2 명확화):** 이 원칙은 *컬럼(스키마)* 투기를
    막는 것이지 *타입 값(데이터)* 을 고정하는 게 아니다. 사용자 저작 타입은 `Entity{...,type,
    theme_id,...}` 의 고정 컬럼 안에 **값**으로 담기므로 스키마는 여전히 사전선언·비투기적이다
    (열은 fragment 노드의 settable 상한이라 하중을 진다). 타입 *어휘* 는 아예 그래프 밖 메타
    (`themes.json`)에 산다 — 스키마 원칙과 무충돌. `Entity`/`TYPED` 캐리어를 content 저장으로
    승인(DEC — D11-R2 [[vinc.b.theme]]).
  - **경계(예외의 국한):** 이 승인은 **`Entity`/`TYPED` 두 테이블에만** 적용된다. (ㄱ) 이 두
    테이블에도 새 열을 **투기적으로 늘리지 않는다** — 열은 fragment 노드의 settable 상한
    (`id/type/title/owner/theme_id/tags` · rel `type`)에서만 자란다. (ㄴ) 이 예외는 **다른
    테이블의 투기적 열을 정당화하지 않는다**. 새 사용자 저작 타입은 열 증설이 아니라 `type`
    **값**으로 흡수되므로, "Entity에도 열 하나 더" 식의 원칙 침식은 이 경계 위반이다.
- **검색 인덱스 생명주기:** 벡터(HNSW)·FTS(BM25)는 인덱스가 걸린 embedding 열을
  수정 불가하게 한다 → **적재 전 drop, 적재 후 build**(embed 잡이 관리). 상세
  [[weg.c.memory-standalone#§5]]·§6-2.
- **읽기·쓰기 동시성 (2026-07-31 실측 확정 — 그전엔 가정이었다):**
  - **MVCC 는 성립한다.** 라이터가 쉬지 않고 쓰는 동안에도 조회 지연은 중앙 1.33ms 로
    유휴 시(1.25ms)와 사실상 같다. → 코드에 예고돼 있던 "불성립 시 reader 직렬화"
    폴백은 **필요 없고 도입해서도 안 된다** — 그게 곧 §11 P2 게이트를 깨는 일이다.
  - **그러나 쓰기 트랜잭션은 하나뿐이다.** 조회 커넥션이 쓰기를 하나라도 발행하면
    그동안 잡 워커의 평범한 MERGE 조차 `Cannot start a new write transaction` 으로
    거절된다. 즉 I4(단일 writer)는 성능 규약이 아니라 **정확성 규약**이다.
  - **"쓰기"는 SET 만이 아니다.** 판별 기준은 *쓰기 트랜잭션을 요구하는가* —
    DML·DDL 은 물론 **`LOAD EXTENSION` 도 해당**한다(이미 로드된 확장을 다시 LOAD
    해도 그렇다). 이 좁은 해석 때문에 세 가지가 조용히 새 나갔었다: 검색의 매회
    확장 재로드(→ 검색 49%·저장 26% 실패), 지연 인덱스 빌드, 그리고 그 반작용으로
    조회 경로에서 아예 빠져 **상시 강등돼 있던** 구조 진단(graph_algo).
  - **규칙:** 조회 중 불가피한 쓰기는 `_ReaderView` 의 주입된 쓰기 지점 통로
    (`writer=(label, fn, timeout)`)로 잡 큐에 태운다. 현재 해당하는 것은 지연 인덱스
    빌드와 graph_algo 둘뿐 — 여기에 더하기 전에 정말 조회 중에 써야 하는지 의심할 것.
  - 확장 로드는 **Database 스코프**라 프로세스당 1회면 충분하다(로드 뒤 만든 커넥션도
    자기 LOAD 없이 쓴다). 기동 시 1회 로드하고 캐시한다.
- **하이브리드 검색:** RRF 융합은 백엔드 밖 순수 함수(`rrf_fuse`, k=60), 쿼리
  임베딩 주입형(검색 코드는 모델 무관). 상세 [[weg.c.memory-standalone#§4]].

## 4. 방언 정합 (P1-0·P1 실측 반영)

**Kùzu 계보 방언** 실측 확정치. 방언 표 본문은 [[weg.c.memory-standalone#§6]] 가
정본이며, 여기는 **Vinc 코드 반영 지점**을 기록한다.

> ⚠ **측정 엔진 표기:** 아래 실측은 P1-0·P1 당시 엔진(kuzu 0.11.3)에서 얻은 값이고,
> 현행 엔진은 **같은 계보의 ladybug v0.18.x**(D14 개정)다. ladybug 로 **재측정하지
> 않았으므로** 이 표는 "kuzu 확정치"가 아니라 **계보 방언 기준 서술**로 읽는다 —
> 각 항목의 정합 코드는 v0.5.3 이후 ladybug 실엔진 테스트를 통과하고 있다
> (`tests/test_embedded_backend.py`·`test_migrate_driver.py`). 계보 분기로
> 어긋나는 항목이 나오면 그 항목만 ladybug 실측으로 갱신한다.

| 항목 | 실측·정합 | 코드 지점 |
|---|---|---|
| M4 | `type(r)` 부재 → `label(r)` | `api/query.py` graph_plan |
| M3 | 네이티브 가변길이 `*1..d` 는 WALK(엣지 재사용 사이클 폭증) → `* TRAIL 1..d` 가드(키워드 범위 앞) | decide_plan |
| M7 | 라벨없는 MATCH 는 전 테이블 부재 속성 참조 시 Binder 오류 → 실재 식별자(`concept_id`/`bridge_id`/`title`)로 매칭(`n.id` 제거) | decide_plan |
| M8 | 날짜는 STRING 저장(엔진이 ISO 문자열 기록) | 스키마·엔진 |
| P-param | Kùzu 계보(ladybug)는 **미사용 파라미터 거부** → Cypher 에 안 쓰는 파라미터 미전달 | preference recall (`api/query.py:111`·`engines/pack_ingest.py:82`) |
| P-listcomp | 리스트 컴프리헨션(`[t IN $x WHERE …]`) 미지원 → `list_distinct(list_cat(...))` 로 tag 합집합 정합(임베디드 e2e 확인) | `api/preference.py` tag_plan |

## 5. 스키마 타입 확정 (§5-2 SSoT 이벤트 반영)

Neo4j schema-free 가 은닉하던 DDL·엔진 SET 타입 모순을 kuzu 포팅이 표면화했다
(당시 엔진 = kuzu, 현행 = 같은 계보 ladybug — 스키마 선행 전제가 동일하므로 아래
확정 타입은 그대로 유효하며, 현행 DDL 은 `store/embedded_schema.py` 가 코드 정본이다).
확정 판정은 [[weg.c.memory-standalone#§5]] 가 정본이며 요지는 다음과 같다.

| 열 | 확정 타입 | 판정 |
|---|---|---|
| `Episode.completed_items` | STRING[] | 원본 `completed:BOOL` 은 소스(frontmatter 부재)·소비처(읽는 곳 없음) 모두 부재한 감사 오류 → 완료 항목 **목록**으로 개명·재타입. 투기적 BOOL 미신설 |
| `Episode.patterns` | STRING[] | 발견된 패턴 후보 항목 목록 |
| `LearnerPattern.observations` | INT64 | 관측 **횟수**(파서가 n 셀을 int, activate 가 `n={}` 표시) — 리스트 아님 |
| (파서) | h2·h3 both-capture | 실데이터·템플릿은 `###`(h3)만 사용 → `##` 만 분리하던 파서가 섹션 전부 공백 처리하던 버그 동반 수정 |

> **전 노드 타입 SET↔DDL 대조 스윕 1회 완료:** 위 3열 외 추가 불일치 없음
> (Concept·Bridge·Domain·SkillExecution·Chunk 및 preference 계열 정합 확인).

## 6. 마이그레이션 오버레이 (A+2 2단계)

`engines/pack_ingest.py` — 서버측 pack.json 을 임베디드 스토어로 import(Vinc 는
Neo4j 미접속). `episode_id` 멱등 MERGE 로 preference 신호(pinned/tags/access_count/
last_access)를 오버레이하되 구조 필드는 재수집 우선(`coalesce`). learner 오버레이는
현재 pack 투영에 `pattern_id` 부재로 범위 밖(문서화). 상세 [[weg.c.memory-standalone#§10]].

## 참조 문서 (개발·기획 원천)

| 원천 | 위치 | 관계 |
|---|---|---|
| 설계 SSoT | [[weg.c.memory-standalone]] (weg-9000-docs, v0.4) | 제품 사실 상속(`inherits_from`) — 본 문서는 §1·§4·§5·§6·§8·§10·§13 의 Vinc 인스턴스 Delta·확정치 반영 |
| 흡수 절차 | `weg-9000-docs/WORK-ORDERS/WO-MEM-SA-001.md` §3 | 반입 금지 하드 룰(F1·F2·F7) |
| 용어·표기 | [[vinc.a.terms]] (동일 인스턴스 A 계층) | 네이밍 바인딩·금지 어휘 정본 |
| 구현 1차 | `Vinclums/Vinc` (라이브 코드) | 아키텍처 계약의 구현체(store/·engines/·api/) |
| 추출 원본 | `weg-9000-memory` (읽기 전용) | 코드 계보만(F7 — 명칭 미승계) |
| 실행 계획 | `Vinclums/Vinc-docs PLAN.md` | 마일스톤·게이트 매트릭스 |
