<!-- vinc-export
doc_id: vinc.plan.methodology-redesign
title: 방법론 재구성 제안: 실측 스냅샷 기반 4축 통합 설계 (2026-08-02)
origin: chat:advanced-research@2026-08-02
chunks: 7
verified: true
content_hash: 4553c25a71018dad087a4c51611964b868f94e5f9bf8d9d1d89050225913dec7
updated_at: 2026-08-02T10:07:27.870Z
-->

# 방법론 재구성 제안: 실측 스냅샷 기반 4축 통합 설계

> **적재 주석 (2026-08-02).** 이 보고서는 외부 리서치 산출물이며 적재 시점에 스토어 기록과
> 대조해 아래 3건을 정정한다. 본문은 원본 그대로 두고 정정은 여기에 모은다.
>
> 1. **단계 0 "재임베딩 워커 발동"은 신규 권고가 아니다.** `vinc.plan.embedding-retrieval`
>    Step 4c 에서 모델 전환과 재임베딩 트리거가 이미 코드 완료(커밋 `cc4845e`)이고,
>    배포 후 5분마다 16문서씩 저절로 채워진다. `vector_note` 의 "104 docs pending" 은
>    결함이 아니라 **정상 이행 진행도**다. 따라서 단계 0 은 "발동"이 아니라 **"완료 확인"**
>    으로 읽어야 한다. 확인 지표는 `arms` 가 `fts` 에서 `fts+vector` 로 돌아오는 것.
> 2. **한국어 82% 잘림은 bge 기준이다.** e5 로 재면 2000자 한국어 청크의 잘림은 **42.6%**
>    (실측, `vinc.plan.embedding-retrieval` Step 4). 보고서의 37~83% 추정 구간은 실측치로
>    대체된다.
> 3. **목표 토큰과 청킹 방식은 이미 정해졌다.** 발주자 결정(2026-08-01)으로 목표 토큰 **500**,
>    방식은 **부모-자식 벡터**(청크는 2000자 유지, 벡터만 500토큰 이하 조각으로 분할,
>    스키마 v8 `ChunkVec`). 즉 DEC GM-9 는 "재청킹"이 아니라 **부모-자식 벡터의 자식 단위
>    토큰 예산 확정**으로 좁혀 읽어야 한다.
>
> 정정 3건을 반영하고 나면 이 보고서의 실질 신규 기여는 **축 2(frontmatter) · 축 4(도메인
> 계층) · GM-11 · GM-14** 넷이다. 축 3 의 검색 아키텍처는 대부분 기존 계획과 겹친다.

작성일 2026-08-02 · 근거 = Vinc MCP 커넥터 실측(로컬 스토어) + 웹 선례. 화면 텍스트 규약(em 대시 금지)을 이 문서 전체에 적용함.

## TL;DR

- 실측 결과 한 그래프 안에 개발 지식(vinc/* 8도메인, 개념 약 149·에피소드 42)과 사용자 학습 자료(물리·수학·예술 약 60문서, 전부 Concept 테이블)가 뒤섞여 있고, 학습 자료 개념 6개는 완전 고아이며 벡터 다리는 아직 이행 중이다. 직전 조사의 (a)(b)(c)(d) 4개 문제는 모두 이번 실측으로 확증됐고, 특히 (b) 범위 문제는 "한 그래프 한 주제 제품 권고 + 관례의 주제(theme) 이관" 혼합안이 지지된다.
- frontmatter 는 폐기가 아니라 "정본에는 보존 + 파싱 후 크롬 승격"이 정답이다. vinc.* 문서의 raw YAML frontmatter 가 청크 `#00000` 에 그대로 들어가 FTS 노이즈가 되고 _DocMeta 와 값이 갈라지는 것(§7 원리 위반)을 실측으로 확인했다.
- 도메인 분리 뷰는 이미 동작하는 Louvain 커뮤니티(vinc_diagnose)를 L0 영역으로 쓰고 자유 문자열 도메인을 상위 축으로 얹는 다축 group-in-a-box 레이아웃이 sigma/three 제약 안에서 유일하게 실현 가능한 안이다.

## 축 1. 현재 적재 상태 실측

모든 값은 도구 호출 반환값이며 추정이 아니다.

| 항목 | 실측치 | 도구·근거 |
|---|---|---|
| 문서 총수 | 103개 | vinc_doc_list count=103 |
| 개발 지식 도메인 분포 | vinc/design 55 · vinc/timeline 42 · vinc/architecture 30 · vinc/governance 26 · vinc/infra 26 · vinc/security 18 · vinc/mcp 17 · vinc/web 16 | vinc_domains |
| 학습 자료 문서 | 물리·수학·예술 약 60문서(origin=WORKSPACES/learning/*) | vinc_doc_list |
| 에피소드 총수 | 42 (전부 vinc/timeline) | vinc_domains |
| 개념 총수 | 약 149 (도메인 집계 합, 하한) | vinc_domains |
| 연결 컴포넌트 | 7개 (거대 컴포넌트 139 + 단독 6) | vinc_diagnose components |
| 고아 노드 | 6개 전부 art-* 개념(Coco Chanel · Frida Kahlo · John Cage · Chinua Achebe · Murasaki Shikibu · east-west-aesthetics) | vinc_diagnose orphans |
| 커뮤니티 | 11개 (Louvain, 최대 size 20 = vinc/governance) | vinc_diagnose communities |
| 병합 후보 | 20쌍(subset·token_overlap, 예: chunk-ssot 대 dec-d45) | vinc_merge_candidates |
| Bridge 실제 비율 | 개념 간 직접 크로스도메인 엣지 0인 구조에서만 의미를 갖는데 도그푸딩 그래프에 실체 거의 없음 | vinc.d.graph-structure-analysis 청크 |
| 벡터 상태 | needs_embed=true 전량 · vector_note "embedding migration in progress (104 docs pending), FTS only" | vinc_doc_get · vinc_search |
| 한국어 문서 비율 | 학습 자료는 영어 본문(한국어 파일명), vinc.* 운영 문서는 한국어 본문. 두 언어가 도메인별로 갈림 | vinc_doc_get |

관측된 도구 동작(직전 조사 결함 재검증):

- **vinc_diagnose 가 이제 동작한다.** components·communities 모두 executed=true 를 반환. 직전 결함 #4(algo_unsupported, graph_algo 확장 없음)는 **반박됨**. 단일 스토어 스냅샷 근거.
- **vinc_search 는 이제 재현적이다.** 동일 쿼리("chunk ssot")를 2회 호출했을 때 바이트 동일 결과. 직전 결함 #3(쿼리마다 반환 집합 상이)은 **부분 반박**. 그러나 새 실패 모드가 관측됐다: 출력이 72,000자를 넘으면 전량 거부한다("Result too large: 184,136 characters"). limit=100 aesthetics 쿼리가 이 상한에 걸려 아무것도 못 받았다. 전수 열거는 여전히 불가능하나 원인이 "조용한 누락"에서 "명시적 거부"로 바뀌었다.
- **개체가 Concept 로 들어온 사례 확증.** art-coco-chanel · art-frida-kahlo 등 인물이 concept:* 로 존재하고 vinc_neighbors 결과 이웃 0개(by_rel 빈 객체). 결정론 개념 파생이 인물·조직 개체를 Concept 로 승격시켰다.

## 축 2. frontmatter 판정: 폐기하지 말고 파싱해 크롬으로 올려라

실측 근거:

- vinc.c.chunk-ssot 를 vinc_doc_get 으로 꺼내면 본문이 `---` 다음 `doc_id: ...` `tags: [...]` `---` 로 시작하고, 이 raw YAML 이 vinc_search 에서 청크 id `#00000` 의 첫 텍스트로 **그대로 반환된다.** frontmatter 가 FTS 인덱스와 청크 텍스트에 물리적으로 포함돼 있음이 확정됐다. 벡터가 켜지면 이 YAML 은 벡터 노이즈도 된다.
- 학습 자료(Statistical Mechanics 등)는 frontmatter 가 아예 없는 깨끗한 마크다운이고 verified=true 로 무손실 복원된다. 즉 frontmatter 는 vinc.* 리포 유래 문서에만 있는 국소 오염원이다.
- _DocMeta 가 이미 doc_id·title·content_hash·updated_at 을 보유하는데 frontmatter 도 doc_id·title·last_updated 를 중복 보유한다. GRAPH.md §7 "스토어가 아는 것은 스토어가 쓴다, 스토어가 모르는 것만 사람이 쓴다" 원리 위반이 실제로 진행 중이다(두 정본이 갈라짐).

웹 선례(원문 정본 보존 + 읽기 화면 단순화를 동시 달성한 표준): Obsidian 은 Properties/Live Preview 에서 frontmatter 를 속성 UI 로 승격해 읽기 화면에서 raw YAML 을 숨긴다. Hugo/Jekyll 는 빌드 시 frontmatter 를 파싱해 메타로만 쓰고 본문 렌더에서 제거한다. 두 경우 모두 디스크의 정본은 frontmatter 를 유지하되 렌더 층에서 파싱해 크롬으로 올린다. 마크다운 렌더러 라이브러리 0 제약에서도 frontmatter 파싱은 선두 블록 정규식 한 줄 수준이라 비용이 낮다.

## 축 3. MCP 검색 경로: 지금은 사실상 FTS 단독

실측: vinc_search 는 arms="fts", vector_note 가 "FTS only" 를 명시한다. RRF 의 벡터 다리가 아직 안 붙어 node_hits 는 title·graph arm 으로만 채워진다. 즉 현재 검색은 하이브리드가 아니라 **BM25 FTS + 제목/그래프 근접의 RRF 융합**이다. vinc_recall 로 2026-08-01 "허브 e5 전환 배포"(schema v8, 부모-자식 벡터)가 확인되므로 이는 결함이 아니라 이행 상태다.

웹 근거(2025~2026):

- **GraphRAG 계열 합의는 유보적이다.** Graph Praxis(Shereshevsky, 2025)는 그래프 RAG 의 가치가 쿼리 복잡도에 비례하며 잘못된 아키텍처는 단순 방법 대비 미미한 이득에 컴퓨트를 낭비한다고 본다. Microsoft 계층 커뮤니티 방식은 엔터프라이즈 벤치마크에서 86% 정확도(baseline RAG 32%)이나, arXiv 2506.05690 은 GraphRAG 가 Natural Questions 에서 vanilla RAG 보다 13.4% 낮고 시간 민감 쿼리에서 특히 부진하다고 보고한다. Vinc 처럼 코퍼스가 작고 오프라인·무LLM 인 환경에서는 full GraphRAG 도입 근거가 약하다.
- **경량 경로가 확실히 우세하다.** Microsoft Research(2024-11)는 LazyGraphRAG 의 인덱싱 비용이 벡터 RAG 와 같고 full GraphRAG 의 0.1%, 쿼리 비용은 700배 이상 낮다고 보고한다. HippoRAG 2 는 MuSiQue 인덱싱에서 9M 대 115M 토큰. LightRAG 는 문서당 약 0.15달러 대 4~7달러. 결론: Vinc 는 무거운 GraphRAG 가 아니라 **이미 가진 결정론 MENTIONS 이부 그래프 + ego-PPR(E-G1~G7)를 LazyGraphRAG 류 드릴다운 패턴으로 쓰는 것**이 정합적이다.
- **RRF 튜닝.** k=60 은 원논문(Cormack·Clarke·Büttcher, SIGIR 2009)에서 유래해 OpenSearch·Elasticsearch·Azure AI Search·MongoDB Atlas·Weaviate 의 문서화된 기본값이다. 코퍼스가 작을수록 BM25 비중이 상대적으로 크므로 벡터가 붙기 전 FTS 우세가 재앙은 아니다. 벡터 복구 후에는 rank_constant 를 낮춰(예: k=10~20) 밀집 신호를 키우는 실험이 선례에서 지지된다.
- **청킹은 토큰 기준이어야 한다.** 512토큰 + 10~20% 오버랩이 벤치마크 검증 기본값이다. 다국어는 문자 기준 청킹이 위험하다. Vinc 는 발주자 결정으로 목표 500토큰·부모-자식 방식을 이미 채택했다.

MCP 도구가 결과를 노출하는 설계(웹 선례): MCP 스펙은 커서 기반 페이지네이션을 권장하고 2026 로드맵도 스트림·참조형 결과를 "On the Horizon" 으로만 둔다. JetBrains·Axiom 실무 지침은 (1) 서버 상한 (2) 요약·상세 분리 (3) total_count·has_more 반환 (4) 무효 커서는 조용한 폴백이 아니라 명시적 오류를 권한다. Vinc 의 72,000자 하드 거부는 이 중 (1)만 있고 (2)(3)이 없어 어시스턴트가 재시도 루프에 빠진다.

## 축 4. 도메인별 영역 분리 뷰와 계층 설계

실측: vinc_diagnose communities 가 이미 11개 Louvain 커뮤니티를 dominant_domain 과 함께 반환한다(커뮤니티 2 size 20 governance, 커뮤니티 10 size 15 design 등). **영역 분리에 필요한 클러스터링 백엔드가 이미 존재하고 동작한다.** 그러나 자유 문자열 도메인은 vinc/* 8개로 아직 통제되나 학습 자료(art·physics·math)가 도메인 빈 문자열로 떠 있어 6개 고아 개념을 낳았다. D 지표(도메인 분열)는 아직 낮지만 학습 자료 유입으로 임박한 위험이다.

웹 선례:

- **group-in-a-box**(Chaturvedi 2014, Computer Graphics Forum 33:52-68): treemap 기판에 커뮤니티 크기 비례 사각 영역을 만들고 각 영역 안에서 개별 force 레이아웃을 돌린다. 인터커뮤니티 관계가 많을 때 Croissant-Doughnut·Force-Directed 메타레이아웃으로 영역 배치를 최적화.
- **GMap/MapSets/Cartograph**(Kobourov): 그래프를 수정 Voronoi 지도 은유로 그려 클러스터를 국가 영역으로 표현. 레이아웃과 클러스터링이 분리되면 영역이 파편화되므로 함께 최적화해야 한다.
- **hexmap 과학 지도**(PMC5959036): 지도 은유가 노드-링크보다 recall 정확도를 높인다.
- 상용: Obsidian graph view 는 그룹(색), Kumu 는 타입 연결, Infranodus 는 betweenness 기반 topical cluster + gap 검출, Gephi/Cytoscape 는 Louvain 후 영역 분할.
- **모노크롬 + 초점색 1개 제약에서 영역 구분**: 색 대신 경계선(convex hull/Voronoi border) · 여백(영역 간 간격) · 배경 톤 미세차(잉크 농도 단계) · 상시 라벨 · 밀도로 표현한다. D33 5단 잉크 농도 문법이 이미 이 어휘를 갖고 있다.

## 직전 조사 (a)(b)(c)(d) 확증·반박

- **(a) 태그 3분류 필요: 확증.** GRAPH.md 실물에서 `[vinc]` 태그가 노드 5종·관계 6종(못 바꾸는 스키마 강제)에, `[core]` 가 개념 크기·id 접두(바꿔야 하는 것)에 붙어 있어 의미 역전이 확인됨. `[스키마 고정]` / `[이 그래프가 정함]` / `[주제마다 다름]` 3분류로 바꿔야 한다. 무게 중, 비용 낮음.
- **(b) 범위 불일치: 확증.** 한 스토어에 vinc/* 개발 도메인 8개와 art/physics/math 학습 주제가 공존하나 GRAPH.md 생성 구역은 vinc/* 도메인만 열거하고 학습 자료는 도메인 빈 문자열로 떠 있다. GRAPH.md 실측 58줄로 60줄 예산이 이미 꽉 찼다(여유 2줄). **3선택지 중 실측이 지지하는 것은 혼합안**이다: "한 그래프 한 주제" 를 제품 권고로 못 박되 입도·id·관계 해석은 주제(theme, D11-R2 단위) 어휘 블록으로 이관한다. 순수 다중 GRAPH.md 는 60줄 곱하기 N 으로 폭발하고(예산 실측이 이미 만원), 순수 이관은 사람 산문(정의·입도·금지)이 갈 곳을 잃기 때문이다. 무게 높음, 비용 높음.
- **(c) 입도가 개념/개체 미구분: 확증.** 인물 6명이 Concept 고아로 존재하고 이웃 0. "X 는 ...이다 두 문장" 테스트가 개체를 전부 통과시켜 재사용되는 관념과 세상의 개체가 한 테이블에 섞였다. 무게 중, 비용 낮음.
- **(d) 5종이 학습일지 편향: 확증.** Bridge 는 개념 간 직접 크로스도메인 엣지 0 이라는 구조에서만 의미를 갖는데 도그푸딩 그래프에 실체가 미미하고, 학습 자료 인물은 개방층(Entity)이 아니라 Concept 로 흘러 라벨이 자료 형상과 어긋난다. 무게 중, 비용 낮음.

## 실행 순서와 중단 기준

**단계 0 (선결 · 무게 높음 · 비용 낮음): 재임베딩 이행 완료 확인.** e5 전환은 배포됐고 워커가 5분마다 16문서씩 채운다. 이것이 끝나야 축 3 의 하이브리드와 축 4 의 임베딩 배치가 의미를 갖는다. **중단 기준:** 예상 소요를 넘겨도 `arms` 가 `fts+vector` 로 돌아오지 않으면 워커 배선 결함으로 판정하고 즉시 원인 조사(2026-07-30 임베딩 워커 소실 사고 재발 여부 확인).

**단계 1 (무게 높음 · 비용 중): 자식 벡터 토큰 예산 확정.** 부모-자식 방식에서 자식 조각을 500토큰 이하로 고정하고 언어 자동 분기(한국어 약 350~500자, 영어 약 2000자)와 10~20% 오버랩을 적용한다. **중단 기준:** 전환 후 한국어 문서 재임베딩 시 실제 토크나이저 측정 잘림률이 5% 초과면 토큰 카운터 결함으로 판정.

**단계 2 (무게 중 · 비용 낮음): frontmatter 파싱·크롬 승격.** 정본 청크에는 남기되(무손실 verified·content_hash 유지) 청킹 파이프라인에서 선두 frontmatter 블록을 분리해 별도 메타로 보관하고 FTS·MENTIONS·개념 파생에서 제외한다. Atlas Reader 는 frontmatter 를 접어서 또는 속성 칩으로 표시한다. **중단 기준:** content_hash 가 바뀌면 정본 훼손이므로 롤백. frontmatter 를 청크에서 빼되 복원 시 재삽입해 verified 가 깨지지 않아야 한다.

**단계 3 (무게 높음 · 비용 높음): GRAPH.md 주제화.** 태그를 3분류로 개정하고, 입도·id·관계 해석을 주제 어휘 블록으로 이관하고, "한 그래프 한 주제" 제품 권고를 명문화하고, 개체/개념 구분 규칙(개체는 Entity 또는 record 로, Concept 는 재사용 관념만)을 입도 절에 추가한다. **중단 기준:** 60줄 예산 초과가 불가피하면 다중 파일 대신 접이식 주제 섹션으로 흡수한다(다중 GRAPH.md 금지).

**단계 4 (무게 높음 · 비용 높음): 도메인 분리 Atlas.** Louvain 커뮤니티를 L0 영역으로, 자유 도메인을 상위 축으로 하는 다축 계층을 만든다. L0 = 도메인/커뮤니티 대륙, L1 = 클러스터, L2 = 노드. sigma 2D 는 group-in-a-box(treemap 기판 + 영역 내 개별 레이아웃), three 3D 는 force 에 도메인 인력 항을 추가한다. 모노크롬 경계선·배경 톤·상시 라벨로 영역을 구분한다(색 금지). 크로스도메인 엣지는 영역 경계를 가로지르는 저농도 헤어라인(D33 cross 농도 재사용). 데이터 모델은 GET /node-context 와 GET /search 에 domain·community_id 필드를 추가하고 도메인 파티션 집계 쿼리는 vinc_domains 플랜을 재사용한다. **중단 기준:** 도메인 3~5개는 L0 지도가 답이고, 6개 이상에서 Coupling Matrix 를 기본 노출하며, 20개 분열 시 카드 9개(3 곱하기 3) 상한을 초과하므로 "외 N개" 롤업으로 강등한다. 커뮤니티가 도메인과 어긋나면(dominant_domain 혼재) 도메인을 상위 축으로 우선한다.

## 한계

- 축 1 수치는 전부 단일 스토어 스냅샷(2026-08-02) 근거이며 웹 선례로 뒷받침되지 않는 실측 전용이다. 웹 선례로 뒷받침되는 주장은 축 2~4 의 선례·표준(Obsidian/Hugo, LazyGraphRAG·HippoRAG·RRF k=60, group-in-a-box·GMap, MCP 페이지네이션)에 한한다.
- vinc_domains 는 domain IS NOT NULL 만 집계하므로 도메인 빈 문자열인 학습 개념이 149 합계에 안 잡힐 수 있다. 실제 개념 수는 149 보다 크다(하한값).
- vinc_diagnose·vinc_search 의 개선(동작·재현성)은 이번 스냅샷에서만 확인됐고 시간에 따라 되돌아갈 수 있다(2026-07-30 워커 소실 같은 회귀 이력 존재).

## DEC 후보

기존 GM-1~GM-8 과 비충돌 넘버링.

- **GM-9** 자식 벡터 토큰 예산 확정(500토큰 이하 · 언어 자동 분기 · 10~20% 오버랩). 근거: 스토어 스냅샷 + 웹 선례(512토큰 표준). 적재 주석 정정 3 적용.
- **GM-10** frontmatter 파싱·크롬 승격, FTS/MENTIONS/개념 파생에서 제외, 정본 청크·verified 불변. 근거: 스토어 스냅샷(청크 #00000 raw YAML) + 웹 선례(Obsidian/Hugo).
- **GM-11** GRAPH.md 3분류 태그 + 입도·id·관계의 주제 어휘 이관 + "한 그래프 한 주제" 제품 권고 + 개체/개념 구분 규칙. 근거: 스토어 스냅샷(태그 역전·60줄 만원·도메인 빈칸·개체 고아).
- **GM-12** Louvain 기반 도메인 분리 Atlas L0(group-in-a-box + 도메인 인력, 모노크롬 경계). 근거: 스토어 스냅샷(11 커뮤니티 이미 존재) + 웹 선례(group-in-a-box·GMap).
- **GM-13** 재임베딩 이행 완료 확인 + vector_note 상태 배지(FTS only 일 때 사용자에게 이행 중임을 알림). 근거: 스토어 스냅샷 + 운영 이력(워커 소실 사고).
- **GM-14** vinc_search 에 커서 페이지네이션 + total_count·has_more 도입, 72,000자 하드 거부를 요약·상세 분리로 대체. 근거: 스토어 스냅샷(184,136자 거부) + 웹 선례(MCP 페이지네이션 스펙·JetBrains 지침).
