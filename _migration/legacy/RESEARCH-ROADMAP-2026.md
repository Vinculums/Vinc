# 2026 기술 로드맵 분석 — v0.3 설계 대비 반영 결정

| | |
|---|---|
| 지위 | 외부 리서치 입력 분석(결정 근거) — 설계 SSoT 아님 |
| 원천 | "Advancing Vinc: A 2026 Technical Roadmap" (발주자 제공, 출처 인용 포함) |
| 대상 | [[weg.c.memory-standalone]] v0.3 대비 CONFIRM/CHANGE/RESOLVE/ENRICH |
| 후속 | 확정분은 설계 v0.4 로 승격 + PLAN.md 마일스톤 반영 |
| 작성일 | 2026-07-04 |

## 0. 요지

로드맵은 v0.3 의 핵심 전략(전략 B·LadybugDB·GraphStore 추상화·RRF·pre-filter·
lazy 모델 다운로드·onedir 동결)을 **대부분 실측으로 확인**했다. 변경·해소·보강
분은 설계 **v0.4** 로 반영했다(SSoT 규율).

> **⚠ 언어 방침(발주자 지시 — v0.4):** Vinc 는 **영어 기본·중점**이다.
> 로드맵의 한국어 특화 권고(KURE-v1·kiwi 형태소 토크나이저)는 **본 제품에
> 미적용**한다. 아래 분석에서 한국어 특화 항목은 취소선/각주로 표시하고, 로드맵의
> **언어 무관 발견**(retrieval-tuning 우위·RRF·pre-filter·LadybugDB 휠·네이티브
> 순회·Sigma.js·사이드카 heartbeat·int8 양자화)만 채택한다.

## 1. CONFIRM — v0.3 결정이 실측으로 확인됨 (무변경)

| 항목 | v0.3 | 로드맵 확인 |
|---|---|---|
| 전략 B (임베디드 그래프) | 채택 | LadybugDB 가 in-process 로 Cypher+HNSW 벡터+BM25 FTS 보존 — 정확한 선택 |
| GraphStore 추상화 | P0 선행 | "포크 생태계 불확실성에 대한 진짜 보험" — 유지 강조 |
| RRF 융합(백엔드 밖 순수 함수) | §4-2 | RRF(k≈60)가 zero-shot 최적 기본값 — best practice 로 확인 |
| 스코프 필터 = pre-filter | §4-2 필터 계약 | 단일 사용자 소규모(수백~수만 노드)에선 **pre-filter 가 정답** — post-filter 는 선택적 필터에서 <k 결과·recall 급락 |
| lazy 모델 다운로드 | D2 기본안 | 설치본 소형(코드만)+최초 실행 다운로드 — 프로덕션 Tauri+PyInstaller 패턴과 일치 |
| onedir 동결(≠onefile) | §9 | onefile 사이드카는 clean kill 안 됨 — onedir 확인 |
| 루프백+세션 토큰 | §3-1 | 이미 best practice |
| 단일 파일 백업 스냅샷 | §3-1-1 | 단일 `.lbug` 파일 → 백업·provenance 단순 — `vinc backup` 강화 |

## 2. CHANGE — 설계 사실 변경 필요 (→ v0.4)

### C1. 임베딩 모델: mpnet(범용 STS) → **영어 retrieval-tuned bge-base-en** (영어 우선)

로드맵의 **언어 무관 핵심 통찰**: v0.3 의 `paraphrase-multilingual-mpnet-base-v2`
는 **retrieval-tuned 가 아닌 범용 STS/paraphrase 모델(128 토큰 상한)**이라 검색
품질이 열위다. 이는 언어를 불문하고 성립하므로, **영어 우선** 방침에서도 모델을
retrieval-tuned 로 올려야 한다.

- **채택(영어 우선):** 기본 **`BAAI/bge-base-en-v1.5`(768d, MIT, retrieval-tuned)**.
  차원 768 유지 → §5-4 프로비저닝 무변경·재임베딩 최소. 품질 옵션
  **`bge-large-en-v1.5`(1024d)**. voyage-3(API) 옵션 유지.
- ~~KURE-v1(1024d, 한국어 특화)~~ — **영어 우선 방침으로 미적용.** 한국어 리더보드
  근거(nlpai-lab)는 본 제품 대상 언어가 아니므로 참조만.
- **영향:** §8 모델 식별자만 교체(768d 유지 → 인덱스 무변경). 모델 스탬프
  불일치 감지(v0.3 §8)가 전환 안전장치.
- **결정:** DEC D10(모델 기본값 = bge-base-en) — 아래 §5.

### C2. 다중 홉 방언(M3): UNWIND 재작성 → **네이티브 가변 길이 우선**

v0.3 §6-2 M3 은 `MATCH path=(n)-[*1..d]-(m)` 를 "홉 단위 UNWIND/UNION 재작성"
으로 규정하고 사이클·행 폭발 리스크를 격상했다. 로드맵은 **LadybugDB 가 Kùzu
계보의 네이티브 `*min..max`(factorized join + CSR adjacency)를 보존**하며 이것이
바로 중간결과 폭발을 줄이도록 설계됐다고 지적한다(Kùzu 30-홉 질의 ms 단위 보고).

- **영향:** M3 을 "수기 UNWIND 재작성 강제"에서 **"네이티브 가변 길이 우선,
  UNWIND 는 폴백. 양자 벤치마크"**로 완화. 리스크 R2·작업량 동시 감소.
- **가드(로드맵 권고, 유지):** 깊이 상한(`*1..4`)·소스별 방문 노드 추적(사이클
  차단·중복 제거)·술어 조기 push·경로 길이 추론 가지치기.
- **결정:** DEC D11 신설(네이티브 vs UNWIND) — P1-0 벤치마크 후 확정.

## 3. RESOLVE — v0.3 미결(DEC/P1-0)이 근거와 함께 해소

| v0.3 미결 | 로드맵 해소 |
|---|---|
| **D8** BM25 토크나이저 | **영어 우선 → 표준(공백) 토크나이저 기본.** 영어는 공백 분절로 충분해 형태소 분석기 불필요. ~~kiwi 한국어 형태소~~ 는 **비영어 코퍼스 유입 시에만** 선택 옵션(AutoRAG 근거는 참조만). D8 사실상 해소 |
| **P1-0 ① 휠 커버리지** | LadybugDB **v0.18.0 이 cp313/cp314 프리빌트 휠**(macOS arm64/x64·Windows amd64/arm64·manylinux/musllinux) 제공 — **3 플랫폼 커버 확인**. Kùzu 핀 폴백은 포크 이탈 대비로만 유지 |
| **P1-0 패키지 식별자** | **실측 해소(2026-07-04, 개발 환경):** `pip install ladybug` → `import ladybug` = **LadybugDB v0.18.0 그래프 DB 본체**(`Database`·`Connection`·`QueryResult`·`_lbug` 네이티브). 설계 §2-2 가 우려한 건축분석 도구 아님. kuzu 0.11.3 폴백도 설치·실행 확인. 실제 Cypher(DDL·MERGE 멱등·파라미터 바인딩) 실행 검증. 빌드 시 버전 핀만 유지 |
| **P1-0 필터 전략** | pre-filter 확정(§1 CONFIRM) |
| **R1** 포크 계보 불안정 | LadybugDB 최활성(v0.18.0, Arun Sharma), RyuGraph·Kùzu 0.11.3 폴백 유효. 추상화가 보험 |

## 4. ENRICH — 신규 작업 항목 (v0.3 무·로드맵 추가)

| # | 항목 | 근거 | 마일스톤 |
|---|---|---|---|
| E1 | BM25 **표준(영어) 토크나이저 기본**. 형태소(CJK) 토크나이저는 비영어 유입 시 선택 옵션 | §3 D8(영어 우선) | M3 |
| E2 | **int8 임베딩 양자화**(4× 축소, near-lossless). 대형 개인 코퍼스엔 binary+int8-rescore | HF/Sentence-Transformers | M3(선택)·M6 |
| E3 | **GraphRAG-local 패턴**(벡터 시드→이웃 확장→재랭크, LLM 불요) + PageRank/Louvain(LadybugDB 네이티브) — recall/decide 흐름 | Edge et al. 2024 / HippoRAG | M3~M5(선택) |
| E4 | **Sigma.js + graphology(WebGL)** GraphView — 1–2k 노드 초과 시 필수. 이웃 우선(seed→클릭 확장)·LOD·클러스터링 | Obsidian 교훈(그래프=탐색, 검색=내비게이션) | M5 |
| E5 | **마이닝 파워 기능**: 저장 질의/뷰·패싯 필터(타입·도메인·시간)·Episode 타임라인·Bridge(CROSSES) 크로스도메인 발견 뷰 | 로드맵 UX §2 | M5 |
| E6 | **사이드카 부모 프로세스 heartbeat**(clean shutdown) + 제어는 Tauri IPC·데이터는 HTTP | onefile kill 버그 | M4 |
| E7 | **PyInstaller `.spec`**(--onefile 금지) + `collect_dynamic_libs`/`collect_data_files`(torch/네이티브) | 로드맵 패키징 | M6 |
| E8 | 모든 인덱스를 **단일 DB 파일**에 수용(HNSW+BM25+Cypher) → 백업·provenance 단순. NaviX(네이티브 predicate-agnostic 벡터 인덱스, arXiv 2506.23397) 필터 의미 실측 | 로드맵 store §2 | M3 |

## 5. 신규/갱신 DEC 제안

| # | 결정 | 기본안 | 기한 |
|---|---|---|---|
| D8 | BM25 토크나이저 | **영어 표준 토크나이저 기본**(영어 우선). 형태소는 비영어 시 옵션 | (확정) |
| D10 | 임베딩 기본 모델 | **`bge-base-en-v1.5`(768d) 1순위 · `bge-large-en`(1024d) 품질 옵션 · voyage-3 옵션** — 영어 우선. P1-0 하드웨어 지연·리프트 실측 | P1 착수 전 |
| D11 | 다중 홉: 네이티브 `*min..max` vs UNWIND 재작성 | 네이티브 우선·UNWIND 폴백, P1-0 양자 벤치마크 | P1-0 |
| D12 | int8 양자화 적용 시점 | 기본(M3) 또는 M6 footprint 최적화 | P1 완료 후 |
| D13 | GraphView 렌더러 | Sigma.js/graphology(WebGL) — viz GraphView 자산과 정합 확인 | M5 착수 전 |

## 6. 미반영·유보 (근거)

- **DuckDB+DuckPGQ / CozoDB / SurrealDB / LanceDB** — 대안 백엔드로 검토했으나
  drop-in 아님(DuckPGQ: 커뮤니티 확장·버전 핀·Kleene* 미완 / Surreal: BSL 소스가용
  — MIT 자세 위반 / Lance: 그래프 DB 아님). **GraphStore 뒤 미래 옵션**으로만 기록.
- Lance 검색 사이드카 분리 — 그래프 엔진 벡터 인덱스가 규모에서 한계일 때만(선택).

> **주의(로드맵 caveat 승계):** 포크 생태계는 아직 초기·급변 — 버전·휠 매트릭스·
> PyPI 명칭·"승자 포크"는 **빌드 시점 실측 확정**한다. 벤치마크 수치는 리더보드·
> AutoRAG·벤더 출처이며 Vinc 실제 코퍼스에서 로컬 검증 필수.
