<!-- vinc-export
doc_id: vinc.d.ingest
title: 표면 1. Intake
origin: Vinc-docs/vinc/D/vinc.d.ingest.md@5090690
chunks: 4
verified: true
content_hash: 73cddd31c8e90592e8314310cad3ca6c23459888beeaac27a654a849d5c03cd3
updated_at: 2026-08-01T16:20:36.337Z
-->

---
doc_id: vinc.d.ingest
layer: D
domain: ingest
title: Vinc 표면 1 — Intake (적재 + 첫 실행 셋업)
status: draft
authority: SSoT
version: v0.3
owner: vinc
last_updated: 2026-07-29
inherits_from: [vinc.d.index]
tags: [vinc, D, screen, intake, ingest]
---

# 표면 1. Intake

> 소스를 레코드로 적재하는 진입 표면. **온보딩 흡수(D15)** — 첫 실행 셋업(데이터
> 디렉토리·로컬 임베딩 준비)은 별도 화면이 아니라 본 표면의 첫 실행 빈 상태 + 진행
> 표시가 겸한다. 데이터는 사이드카 `/ingest/{engine}`(localhost HTTP + 토큰), 로컬
> 파일 선택은 **HTML File API**(`<input type="file">` + 웹뷰 HTML5 드롭) — 구 계획이던 Tauri IPC
> 다이얼로그는 채택되지 않았다(플러그인은 등록만 되어 있고 파일 선택에 쓰이지 않는다). 웹 Vinc 와
> 같은 코드로 돌기 위한 선택이다. 색·치수는 디자인 토큰만 참조([[vinc.d.index#§3]]).
>
> **지식 입력 모델(as-built, 2026-07-29 갱신):** 앱이 **오프라인·무LLM**인 것은 여전히 참이지만,
> 그것이 "원문 문서를 안 읽는다"는 뜻은 아니다 — 추출이 **결정론**(마크다운 `##`/`###` 헤딩 →
> 개념 후보, `api/docstore.py` `extract_concepts`)이라 LLM 이 필요 없는 것이지 파서가 없는 게
> 아니다. LLM·외부 API 호출은 이 경로에 0. 지식은 **세 경로**로 들어온다: (1) Claude↔MCP
> 커넥터(`vinc_ingest`→`/ingest/fragment`) · (2) `.vincpack`/`.json` 팩 import · (3) **문서
> 정본(D45)** — `/doc/put`·`vinc_doc_put`·`vinc doc put`·앱 내 편집기가 모두 같은 `put_doc`
> 로 수렴한다(§4, 계약 정본 [[vinc.c.chunk-ssot]]). 즉 이 표면의 '소스 드롭'은 팩 파일 **과**
> 원문 문서 둘 다 받는다 — [[vinc.c.dataplane#§8]] 브링업 사가와 함께 as-built.

## 1. 레이아웃
- 상단: 엔진 선택 탭(`Tab` — 활성은 표면 승격 + 텍스트 무게, 솔리드 색 금지).
- 본문: 소스 드롭 영역 + 최근 적재 리스트(`ListRow`, 행마다 카드 금지).
- 진행: `Progress` 트랙(`--muted`) — 적재량은 `bar`/`domains` 표기로 보조.
- 첫 실행: 드롭 영역이 셋업 안내를 겸함(임베딩 모델 준비 진행률 포함).

## 2. 4-State (카피 = D18 보이스 · D42 KO 덱)

> **문자열 정본은 코드** — `Vinc/app/src/copy.ts` `copy.intake`(구현권위). 전수 목록·검수 뷰는
> [[vinc.a.ui-copy#§4]]. 아래는 표면 설계 관점의 상태별 표현이며, 문자열이 다르면 코드가 이긴다.
> 2026-07-12 SaaS 톤 개선(record→document · domain→topic · corpus→data, [[vinc.a.ui-copy#§17]])과
> v0.1.1 팩 import 정직화, **2026-07-29 원문 문서 수용**(§4)이 반영된 현행이다.

| 상태 | 표현 |
|---|---|
| idle(첫 실행) | EmptyGuide: **"Add knowledge to Vinc."** — "Bring in a document (.md, .txt, .rst, .pdf) or a knowledge pack (.vincpack, .json). Drag it here, or choose a file." primary CTA = `Choose a file`(`copy.intake.idleCta`). 보조 액션 = `New document`(파일 없이 빈 정본으로 시작 — D45-E) · `Try the sample pack`(데스크탑 전용). 보조 블록 `intakeUi.mcpTitle`/`mcpBody` 는 유지하되 문구가 뒤집혔다: 이제 "Vinc reads plain documents on its own" 이고 Claude/MCP 는 **더 깊은 추출**(코드·Vinc 가 못 읽는 형식) 담당(§4) |
| loading(Ingesting) | 단일 Progress 가 아니라 **파일별 행 패널**(Figma 42:5): `stateDot` ●◐○ + 진행 바 + 상태 라벨(`stPending`·`stActive`·`stDone`·`stFailed`). 머리말 `importing` + `processing(done,total)` = "{n} / {m} processed · on your device only". 파일 단위 순차 처리라 한 건 실패가 나머지를 막지 않는다 |
| success | 팩과 문서를 **갈라서** 센다 — 한 문장에 섞으면 문서만 넣었을 때 "개념 0개, 연결 0개"로 읽혀 실패처럼 보이기 때문: `imported(files,nodes,edges)`(팩) + `importedDocs(docs)` = "Added {n} documents, kept whole and searchable."(문서). 문서가 하나라도 있으면 그 자리에 추출 제안 `edExtractOffer(n)` = "{n} concept candidates found · Apply" 가 붙는다(미리보기→승인, §4). 일부 실패는 `someFailed` 로 병기. 다음 동선 `Open in Atlas` · `Add more` |
| error | ErrorBanner(`--danger` + `alert-triangle`) — **전량 실패일 때만**. 기본은 "Couldn't read {src}. …Everything already added is safe." + 진단 줄(`sidecarDiag`). 원인이 확정된 실패(웹 크기 초과 `tooLarge`, 서버 강등 코드 `docError.*` — 암호 PDF·스캔 PDF·미지원 형식)는 **그 문장만** 보인다: 사이드카는 멀쩡한데 진단 줄이 사이드카를 의심하게 만들기 때문 |

## 3. 참조
- 데이터 계약: 사이드카 `/ingest/{engine}`(202 + job_id 폴링).
- 문서 정본 계약: 쓰기 `POST /doc/put`(텍스트) · `POST /doc/put-file`(바이트·base64) ·
  `POST /doc/extract`(`apply` 플래그) — 셋 다 잡 큐에서 **동기 실행**(`jobs.run_sync`)이라
  202+job_id 폴링이 아니라 응답이 바로 결과다. 읽기 `GET /docs`(origin 색인)·`GET /doc`.
  계약 정본 [[vinc.c.chunk-ssot]].
- 컴포넌트: `Tab`·`ListRow`·`Progress`·`EmptyGuide`·`ErrorBanner`·`Button`.

## 4. 지식 입력 모델 · 팩 import · 원문 문서 (as-built)

이 표면은 **팩 import 와 원문 문서 적재를 함께** 받는다(위 인트로 세 경로 중 ②·③).

- **세 입력 경로:** (1) Claude↔MCP `vinc_ingest`(→`/ingest/fragment`, 호스트가 저작한 fragment 를
  id 정확 매칭으로 MERGE) · (2) `.vincpack`/`.json` 팩 import(share pack `/ingest/pack` 또는 bare
  fragment `/ingest/fragment`) · (3) **문서 정본(D45)** — 원문을 통째로 `/doc/put` 에 넣어 보관하고
  결정론 추출로 개념 후보를 파생. 상세 도구 계약 [[vinc.mcp]], 데이터플레인
  [[vinc.c.dataplane#§3]], 청킹·정체성·추출 계약 [[vinc.c.chunk-ssot]].
- **v0.1.1 배선(2026-07-13):** Intake에 팩 파일 import(파일 선택/드롭 → HTML File API, 셸은
  `dragDropEnabled:false` 라 웹뷰가 드롭을 받는다). `tenant:local` 스코프에 MERGE.
- **원문 문서 수용(2026-07-29).** 드롭존과 **상단 바 `가져오기`** 가 원문 파일을 직접 받는다.
  파이프는 원래 뚫려 있었고(`/doc/put`·`vinc_doc_put`·편집기가 같은 `put_doc` 로 수렴) 드롭존만
  팩·프래그먼트를 받아, `.md` 를 끌어다 놓으면 팩 파서의 `JSON.parse` 가 먼저 죽고 `SyntaxError`
  원문이 사용자에게 그대로 노출됐다 — 있는 능력을 UI 가 못 보여주던 상태의 해소다. 흐름:
  1. **확장자 판별** — 판별 SSoT 는 `app/src/doc-import.ts` `DOC_EXT` 한 곳(진입점이 둘이라 규칙이
     갈라지면 어느 문을 쓰느냐에 따라 열리는 파일이 달라진다). 텍스트 `.md/.markdown/.txt/.text/.rst`
     + 바이트 `.pdf`, 서버 대응 집합은 `api/textextract.py` `TEXT_EXTS`/`PDF_EXTS`.
     문서로 판정되면 **팩 파서를 아예 거치지 않는다**(거치면 크기·인코딩 문제까지 "형식 오류"로 둔갑).
  2. **적재** — 텍스트는 `/doc/put`(문자열 그대로, `origin` = 파일명). PDF, 그리고 브라우저
     `File.text()` 의 UTF-8 고정 디코드가 망친 텍스트(U+FFFD 검출 — cp949 저장본 등)는 원본
     바이트를 `/doc/put-file`(base64-in-JSON)로 넘겨 서버 추출기에 맡긴다. multipart 가 아닌 이유는
     계정 프록시가 상태변경 요청에 `application/json` 을 강제하기 때문.
  3. **미리보기** — `/doc/extract` `apply:false` 로 개념 후보만 세어 `edExtractOffer(n)` 버튼을 붙인다.
  4. **승인 시 반영** — 같은 라우트 `apply:true`. 자동 반영하지 않는 이유: 임의 문서의 헤딩이 그대로
     개념이 되면 그래프가 오염되고, 들어간 개념은 `status='proposed'` 라 정리도 결국 사람 일이다.
- **재드롭 = 갱신(중복 문서 안 만듦).** `make_doc_id` 의 basis 에 `content_hash` 가 들어가므로 id 를
  주지 않으면 편집본은 **언제나 새 문서**가 된다 — 갱신 의미론은 클라이언트가 id 를 찾아 줘야만
  성립한다. `originIndex()`(`GET /docs`)로 `origin`(파일명) → `doc_id` 색인을 만들어 같은 파일의
  재드롭을 기존 정본 갱신으로 흘린다. 문서가 하나라도 섞였을 때만 조회한다(팩만 넣는 흐름에 왕복을
  더하지 않는다). 색인 조회가 실패하면 전부 신규로 계속한다 — 목록 하나 때문에 적재를 막지 않는다.
  `origin` 은 정체성 산식에 참여할 뿐 **조인 키가 아니고 UI 에도 노출하지 않는다**(남의 기기 경로일 수
  있다, [[vinc.c.chunk-ssot]]).
- **웹 크기 상한은 클라이언트 프리플라이트.** 허브 `MAX_BODY`(4MB, `vinc_mcp/http_server.py`)는 초과
  본문을 413 이 아니라 **빈 본문**으로 돌려서 사이드카가 그걸 `missing field: text` 400 으로 받는다 —
  크기 문제가 사용자에겐 형식 문제로 둔갑한다. 그래서 웹에서는 넘기기 전에 `WEB_BODY_LIMIT`
  (3,800,000바이트, base64 경로는 4/3 팽창분만큼 더 낮게)에서 막고 `intakeUi.tooLarge(name, mb)` 로
  읽히는 문구를 준다. 데스크탑은 사이드카 직결(프록시 없음)이라 상한이 없다.
- **서버 강등은 코드로 받는다.** 추출 실패(암호 PDF·스캔 PDF·추출기 부재·미지원 형식)는 500 이
  아니라 `executed=False` + **안정 코드**로 오고, 앱은 `intakeUi.docError[code]` 표로 문구를 고른다.
  코드를 모르면 원문 메시지를 그대로 들고 간다(조용히 삼키지 않는다).
- **앱 내 저작(D45-E) — Records 는 더 이상 읽기전용이 아니다.** 편집기(`app/src/doc-editor.ts`)는
  세 진입점이 공유한다: **Records** 행 클릭(`surfaces/records.ts`) · **Atlas Reader** 패널
  (`surfaces/atlas.ts` `mountDocEditor`) · **Intake** 의 `New document`(상단 바 가져오기 시트에도 같은
  버튼). 저장은 **디바운스 자동저장 + Ctrl/Cmd+S**, 편집기 문자열을 어떤 변형도 없이 `/doc/put` 으로
  보낸다(무손실 규율 — verified 바이트 동일). 무음 LWW 금지: 원격 해시를 비교해 덮어쓰기/새 버전을
  사람이 고른다. 즉 **앱 내 콘텐츠 직접 생성 경로가 있다**(구 서술 "Records 화면은 읽기전용 — 앱 내
  콘텐츠 직접 생성 경로 없음"은 D45-E 로 폐기).
- **정직 카피(as-built, 갱신).** v0.1.1 의 자리표시자(`intakeUi.dropLater`·`chooseLater`) 제거는
  그대로지만, 그 자리를 채운 `mcpTitle`/`mcpBody` 의 **주장이 2026-07-29 에 뒤집혔다**. 구 문구
  "Vinc stays on your device, so it never reads your files itself" 는 이제 거짓이라 폐기됐고, 현행은
  "Vinc reads plain documents on its own and keeps them whole. For richer extraction, from code or a
  format Vinc doesn't read, connect an AI assistant in Settings → MCP…" 다. 즉 **내 문서 경로 = 앱
  자체**이고, Claude/MCP 는 앱이 못 읽는 것을 맡는 **보완** 경로로 재배치됐다.
- **샘플 팩(in-app):** `intakeUi.trySample`("Try the sample pack") → `sampleLoaded`("6 concepts across
  2 topics") — 신규 유저용 체험 공간. Settings → Workspace 로 복귀(`settingsUi.workspace`).

## 부록 Z. 미분류 원문 사실
(없음)
