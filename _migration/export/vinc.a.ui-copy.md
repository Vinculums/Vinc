<!-- vinc-export
doc_id: vinc.a.ui-copy
title: Vinc 앱 UI 카피 전수 인벤토리
origin: Vinc-docs/vinc/A/vinc.a.ui-copy.md@615e9e0
chunks: 11
verified: true
content_hash: 44af60b2fbcfdd72c2045c690c9ea1a2846ff2db675970203923884c7e26c9c7
updated_at: 2026-08-02T12:10:49.900Z
-->

---
doc_id: vinc.a.ui-copy
layer: A
domain: ui-copy
title: Vinc 앱 UI 카피 전수 인벤토리 (실사용 용어·문장 수집)
status: draft
authority: inventory
version: v0.5
owner: weg-9000
last_updated: 2026-08-02
inherits_from: [vinc.a.terms]
tags: [vinc, A, ui-copy, i18n, copy-deck, inventory]
---

# Vinc 앱 UI 카피 전수 인벤토리

> **실제 앱이 사용자에게 보여주는 모든 용어·문장의 전수 수집본.**
> 코드 정본은 `Vinc/app/src/copy.ts`(덱)이며, 본 문서는 그 **전수 목록 + 검수 뷰**다.
> copy.ts 를 바꾸면 본 문서도 갱신한다(§0 동기화 규칙). 표기·금지 어휘 규칙은
> [[vinc.a.terms]] 가 상위 정본이다.
>
> ⚠ **부분 재동기(2026-07-29):** 본 인벤토리 본문은 **2026-07-16 스냅샷**이다. 이번에는
> D45(문서 정본) 로 뒤집힌 `intakeUi.mcpTitle`/`mcpBody` 두 행만 `copy.ts` 실측으로
> 재동기했다 — 구 문구("never reads your files itself" · "파일을 직접 읽지 않습니다")는
> 앱이 원문 문서를 직접 읽게 되면서 **거짓이 됐다**([[vinc.c.chunk-ssot]]). 셸 개편
> (D56 상단 바 단일·D57·D58) 이후의 나머지 행은 아직 전수 재수집되지 않았으니, 개별 값이
> 어긋나면 `copy.ts` 가 정본이다.

## 0. 수집 범위 · 동기화 규칙

| 출처 파일 | 내용 | 본 문서 절 |
|---|---|---|
| `app/src/copy.ts` | EN/KO 카피 덱 전체(D18·D42) | §3–§14 |
| `app/index.html` | 셸 정적 라벨(레일·타이틀) | §2 |
| `app/src/surfaces/settings.ts` | 설정 팝오버 하드코딩(테마·언어 값) | §15 |
| `app/src/surfaces/atlas.ts` + `atlas-prefs.ts` | 크럼 이름·보기 칩·배치 값 | §15 |
| `app/src/surfaces/search.ts` | 매치 표기(≡/≈)·연산자 예시 | §15 |
| `app/src/surfaces/packs.ts` · `sheets.ts` | 팩 파일명 패턴·입력 플레이스홀더 | §15 |
| `app/src/main.ts` | 단축키·팔레트 명령 | §13·§16 |

- **원칙(D42):** 한국어는 번역이 아닌 저작. EN 보이스 = precise·warm·craftsmanlike,
  동사 우선 CTA. 승인 동사: mine, map, trace, surface, bind, connect.
- **금지(F2):** 학습/기억 서사 어휘 금지 — [[vinc.a.terms]] §5. 본 인벤토리 전수 스캔
  결과 위반 0건(2026-07-12, `gate_forbidden.py` PASS).
- **함수형 카피:** `(n)` 등 자리표시는 런타임 값. 본 문서는 자리표시 그대로 기재한다.
- **재동기화 이력(2026-07-16):** v0.1.1(07-13) 팩 import 정직화가 본 인벤토리(07-12 작성)에
  미반영이었다 — `copy.ts` 대조로 정정. §4 idle 3키(`Add knowledge to Vinc.`·`Import a pack`) 갱신 ·
  §14 **코드에서 제거된 자리표시자**(`intakeUi.dropLater`·`chooseLater`) 삭제 + `mcpTitle`/`mcpBody`
  (내 문서 = Claude/MCP 안내)·`importing`·`imported`·`someFailed` 추가 · §9 `settingsUi.mcpDesktop`~
  `mcpNoShell` 6키 추가. **미대조 잔여: §10–§13**(`ui.meta`·`ui.common`·`ui.panel`·`ui.atlasx`·
  `ui.palette`) — 후속 전수 대조 필요.
- **권위 순환 주의:** `copy.ts` 헤더 주석은 본 문서를 SSoT 라 적고, 본 문서(§0)는 `copy.ts` 를
  코드 정본이라 적는다. **실사용 문자열의 판정 기준은 코드**(배포되는 것) — 문서는 검수 뷰다.

## 1. 고유명사 · 핵심 용어 (미번역/정본 표기)

| 용어 | 표기 규칙 | EN | KO | 비고 |
|---|---|---|---|---|
| 제품명 | 항상 `Vinc` | Vinc | Vinc | [[vinc.a.terms]] §1 |
| 표면 1 | 미번역 고유명사 | Intake | Intake | 레일 라벨 |
| 표면 2 | 미번역 고유명사 | Records | Records | |
| 표면 3 | 미번역 고유명사 | Atlas | Atlas | 문장 안에서도 `Atlas` 유지("Atlas에서 열기") |
| 표면 4 | 미번역 고유명사 | Search | Search | |
| 표면 5 | 미번역 고유명사 | Packs | Packs | |
| 설정 | 미번역 | Settings | Settings | 레일 하단 버튼 |
| 팩 | — | pack | 팩 | 확장자 `.vincpack` |
| 문서(=기록) | 2026-07-12 개선 | document | 문서 | 데이터 최소 단위(내부 스키마명 record) |
| 주제(=도메인) | 2026-07-12 개선 | topic | 주제 | 데이터에서 발견되는 분야(내부명 domain, 고정 목록 아님) |
| 개념 | — | concept | 개념 | 그래프 노드종 |
| 게이트웨이 | — | gateway | 게이트웨이 | 주제 경계 노드 |
| 연결(=잇기) | 2026-07-12 개선 | link | 연결 | suggest-ties → 연결 후보(내부명 tie/bind) |
| 연결 수 | — | connection | 연결 | 간선 수 표기 |
| 고정 | — | pin | 고정 | 선호 신호 |
| 태그 | — | tag | 태그 | 선호 신호 |
| 작업 공간 | — | workspace | 작업 공간 | owner 스코프 표시명 |
| 샘플 팩 | — | sample pack | 샘플 팩 | D30 온보딩 |
| 데이터(=말뭉치) | 2026-07-12 개선 | data | 데이터 | intake 대상(옛 corpus/말뭉치) |
| 분석·추가(=채굴) | 2026-07-12 개선 | analyze/add | 분석·추가 | ingest 동작(옛 mine/채굴) |
| 화면(=표면) | 2026-07-12 개선 | screen | 화면 | 5개 뷰(옛 surface/표면) |
| 사이드바(=레일) | 2026-07-12 개선 | sidebar | 사이드바 | 좌측 내비(옛 rail/레일) |

## 2. 셸 정적 라벨 (`app/index.html`)

| 위치 | 값 |
|---|---|
| 문서 타이틀 | `Vinc · Map of knowledge.` |
| 브랜드(레일 상단) | `V` + 펼침 시 `inc` |
| 레일 라벨 | Intake · Records · Atlas · Search · Packs · Settings |
| 레일 aria-label | `Surfaces` |

## 3. 브랜드 카피 (copy.ts 루트)

| 키 | EN | KO |
|---|---|---|
| tagline | Discover the core of your knowledge. | 흩어진 지식의 핵심을 발견하세요. |
| sovereignty | Vinc runs entirely on your device. Nothing ever leaves. | Vinc는 현재 기기에서만 안전하게 동작합니다. 어떤 데이터도 외부로 유출되지 않습니다. |

## 4. Intake (`copy.intake`)

| 키 | EN | KO |
|---|---|---|
| idleTitle | Add knowledge to Vinc. | Vinc에 지식 추가하기 |
| idleBody | Import a knowledge pack — a .vincpack or .json file. Drag it here, or choose a file. | 지식 팩 파일(.vincpack 또는 .json)을 가져오세요. 여기에 끌어다 놓거나 파일을 선택하면 됩니다. |
| idleCta | Import a pack | 팩 가져오기 |
| loading(n,m,d) | Analyzing {n} of {m} documents. {d} topics so far. | {m}개의 문서 중 {n}개 분석 중... 지금까지 발견된 주제 {d}개. |
| success(n,m) | {n} documents added across {m} topics. | {m}개 주제에 걸쳐 {n}개의 문서가 추가되었습니다. |
| successCta | Open in Atlas | Atlas에서 열기 |
| error(src) | Couldn't read {src}. Check the file and try again. Everything already added is safe. | {src}을(를) 읽지 못했습니다. 파일을 확인하고 다시 시도하세요. 이미 추가된 문서는 안전합니다. |
| retryCta | Try again | 다시 시도 |

## 5. Records (`copy.records`)

| 키 | EN | KO |
|---|---|---|
| idle | No documents yet. | 아직 추가된 문서가 없습니다. |
| idleCta | Import files | 파일 가져오기 |
| header(n,m) | {n} documents · {m} topics | 문서 {n}개 · 주제 {m}개 |
| error | Documents couldn't load. Retry anytime. Nothing was changed. | 문서를 불러오지 못했습니다. 언제든 다시 시도하세요. 바뀐 것은 없습니다. |
| actions.atlas | Show in Atlas | Atlas에서 보기 |
| actions.pin | Pin | 고정 |
| actions.tag | Tag | 태그 |

## 6. Atlas (`copy.atlas`)

| 키 | EN | KO |
|---|---|---|
| idle | No knowledge map yet. Add files or run a search — the map builds itself from your documents. | 아직 지식 지도가 없습니다. 파일을 추가하거나 검색을 실행해 보세요. 추가된 문서를 바탕으로 지도가 자동으로 생성됩니다. |
| idleCta | Import files | 파일 가져오기 |
| loading(d) | Arranging {d} topics… | {d}개의 주제 구성 중... |
| domainTip(dom,n,k) | {dom}: {n} documents · {k} links to other topics. Click to see more. | {dom}: 문서 {n}개 · 다른 주제와 연결 {k}개. 클릭하여 자세히 보기. |
| gatewayTip(dom) | Crosses into {dom}. Click to follow. | '{dom}' 주제로 이어집니다. 클릭해서 이동하세요. |
| error | The map couldn't load. Retry anytime. Your documents are untouched. | 지도를 불러오지 못했습니다. 언제든 다시 시도하세요. 문서는 그대로입니다. |
| retryCta | Retry | 다시 시도 |

## 7. Search (`copy.search`)

| 키 | EN | KO |
|---|---|---|
| idle | Search all your documents, by meaning or by keyword. | 추가한 모든 문서를 의미와 키워드로 검색해 보세요. |
| empty(q) | No results for '{q}'. Try a different term, or open the Atlas instead. | '{q}'에 대한 검색 결과가 없습니다. 다른 검색어를 입력하거나 Atlas(지식 지도)를 확인해 보세요. |
| emptyCta | Open Atlas | Atlas 열기 |
| matches(n) | {n} results | {n}개의 결과 |
| error | Search couldn't run. Retry anytime. The index is intact. | 검색을 실행하지 못했습니다. 언제든 다시 시도하세요. 색인은 온전합니다. |
| searching | Searching… | 검색 중… |
| migrating(n) (v0.7.2 GM-13) | Updating the meaning index: keyword results only for now ({n} documents left) | 의미 색인을 새로 만드는 중입니다. 지금은 키워드 결과만 보여드려요 (남은 문서 {n}개) |
| actions.atlas | Show in Atlas | Atlas에서 보기 |
| actions.open | Open | 열기 |

## 8. Packs (`copy.packs`)

| 키 | EN | KO |
|---|---|---|
| idle | Bundle your knowledge into a portable pack, or share it safely with someone else. | 내 지식을 패키지로 묶어 휴대하거나, 다른 사람에게 안전하게 공유하세요. |
| idleCta | Create a pack | 팩 만들기 |
| shareCta | Share a pack | 팩 공유하기 |
| importCta | Import a pack | 팩 불러오기 |
| loading(n) | Packing {n} documents… | {n}개의 문서를 팩으로 묶는 중... |
| importing | Importing… | 불러오는 중… |
| success(n,size) | Pack ready: {n} documents, {size}. | 팩 준비 완료: 문서 {n}개, {size}. |
| shareReady(c,e) | Pack ready: {c} concepts, {e} connections. | 팩 완성! 개념 {c}개, 연결 {e}개가 담겼습니다. |
| shareNote | The pack carries document content only. Your pins and tags are never shared. | 팩에는 순수한 문서 내용만 담깁니다. 개인적인 고정 및 태그 정보는 공유되지 않습니다. |
| imported(created,matched) | Imported: {created} new, {matched} already here. | 불러왔습니다: 새로 {created}개, 이미 있던 것 {matched}개. |
| successCta | Reveal in folder | 폴더에서 보기 |
| error | The pack didn't finish. Try again. Your documents are untouched. | 팩을 완성하지 못했습니다. 다시 시도하세요. 문서는 그대로입니다. |

## 9. Settings (`copy.settings` + `ui.settingsUi`)

| 키 | EN | KO |
|---|---|---|
| settings.lock | Lock Vinc with a passphrase. Optional. Your data stays on this device either way. | 암호로 Vinc를 잠급니다. 선택 사항입니다. 어느 쪽이든 데이터는 기기에 안전하게 남습니다. |
| settings.mcp | Tool calls from connected clients appear here, as they happen. | 연결된 클라이언트의 도구 호출이 실시간으로 여기에 표시됩니다. |
| settings.data | Everything Vinc knows lives in this one folder. | Vinc의 모든 데이터가 이 폴더 하나에 저장됩니다. |
| settings.dataCta | Back up now | 지금 백업 |
| settingsUi.lock | Lock | 잠금 |
| settingsUi.mcp | MCP | MCP |
| settingsUi.data | Data | 데이터 |
| settingsUi.workspace | Workspace | 작업 공간 |
| settingsUi.workspaceHint | The space you're viewing. Switch back after trying the sample. | 지금 보고 있는 공간입니다. 샘플 체험 후 되돌리세요. |
| settingsUi.theme | Theme | 테마 |
| settingsUi.language | Language | 언어 |
| settingsUi.advanced | Advanced | 고급 |
| settingsUi.engine | Engine address | 엔진 주소 |
| settingsUi.secret | Connection secret | 연결 시크릿 |
| settingsUi.secretHint | Filled in automatically by the app. Only change this if you run the engine yourself. | 앱이 자동으로 채웁니다. 엔진을 직접 운영할 때만 변경하세요. |
| settingsUi.mcpDesktop | Connect Claude Desktop | Claude Desktop에 연결 |
| settingsUi.mcpCode | Connect Claude Code | Claude Code에 연결 |
| settingsUi.mcpInstalling | Connecting… | 연결 중… |
| settingsUi.mcpDone(client) | Registered with Claude {Desktop / Code}. Restart it to reach Vinc. | Claude {Desktop / Code}에 등록됐습니다. 재시작하면 Vinc에 접근합니다. |
| settingsUi.mcpFail | Couldn't connect | 연결 실패 |
| settingsUi.mcpNoShell | Available in the desktop app only. | 데스크톱 앱에서만 가능합니다. |

## 10. 표면 메타 · 공통 (`ui.meta` · `ui.common`)

| 키 | EN | KO |
|---|---|---|
| meta.intake | Documents in, knowledge out. | 문서를 가져오면 지식이 됩니다. |
| meta.records | All your knowledge, in one place. | 내가 모은 지식을 한곳에. |
| meta.search | Meaning and keywords, together. | 의미와 키워드를 함께. |
| meta.packs | Bundle, share, merge. | 묶고, 나누고, 결합하고. |
| common.cancel | Cancel | 취소 |
| common.loading | Loading… | 불러오는 중… |
| common.none | none | 없음 |

## 11. Atlas 보기 패널 (`ui.panel`)

| 키 | EN | KO |
|---|---|---|
| view | View | 보기 |
| viewChip(d) | View · {d} | 보기 · {d} |
| nodeSize | Node size | 노드 크기 |
| byConnections | by connections | 연결수 비례 |
| uniform | uniform | 균일 |
| labels | Labels | 라벨 |
| onHover | on hover | 호버 시 |
| off | off | 끔 |
| arrows | Direction arrows | 방향 화살표 |
| on | on | 켬 |
| unlinkedNodes | Unlinked nodes | 연결 안 된 노드 |
| show | show | 표시 |
| hide | hide | 숨김 |
| layout | Layout | 배치 |
| spread | Spread | 간격 |
| fixedNote | Shapes and the point color stay fixed. They carry meaning, so a shared pack reads the same everywhere. | 형태와 포인트 색상은 일관되게 유지됩니다. 의미를 담고 있어, 공유한 팩이 어디서나 같은 모습으로 보입니다. |

## 12. Atlas 탐색·잇기 (`ui.atlasx`)

| 키 | EN | KO |
|---|---|---|
| grid | Grid | 그리드 |
| matrix | Matrix | 매트릭스 |
| traceCta | Trace from here | 여기서 추적 |
| domain(d) | topic: {d} | 주제: {d} |
| connections(n) | {n} connections | 연결 {n}개 |
| outIn(o,i) |  · → {o} out · ← {i} in |  · → 나감 {o} · ← 들어옴 {i} |
| gateway |  · gateway |  · 게이트웨이 |
| shareSlice(n) | Share as a pack ({n}) | 팩으로 공유 ({n}) |
| tiesLoading | Loading links… | 연결 불러오는 중… |
| noTies | No links yet between these topics. | 이 두 주제 사이에 아직 연결이 없습니다. |
| via(t) | via {t} | {t} 경유 |
| suggestLooking | Looking for possible links… | 연결 후보를 찾는 중… |
| suggestNone | Unlinked. No suggestions yet. Add a tag to surface matches. | 연결 안 됨. 아직 후보가 없습니다. 태그를 달면 후보가 나타납니다. |
| suggestHeader | Unlinked · possible links | 연결 안 됨 · 연결 후보 |
| bind | Link | 연결 |
| bound(t) | Linked to <b>{t}</b> · one connection added. | <b>{t}</b>에 연결했습니다 · 연결 1개 추가. |
| bindFail | Couldn't link. Try again in a moment. | 연결하지 못했습니다. 잠시 후 다시 시도하세요. |
| unavailable | Link suggestions unavailable. | 연결 제안을 사용할 수 없습니다. |

## 13. 커맨드 팔레트 · 단축키 (`ui.palette` + main.ts)

| 키 | EN | KO |
|---|---|---|
| typeCommand | Type a command… | 명령 입력… |
| noCommands | No commands. | 명령이 없습니다. |
| goTo(s) | Go to {s} | {s} 열기 |
| ingest | Add data | 데이터 추가하기 |
| share | Share a pack | 팩 공유하기 |
| settings | Open Settings | 설정 열기 |
| keyboard | Keyboard | 키보드 |
| goSurface | Go to screen | 화면 이동 |
| cmdPalette | Command palette | 커맨드 팔레트 |
| railToggle | Open/close the sidebar | 사이드바 열기/닫기 |
| fit | Fit selection (Atlas) | 선택 맞춤 (Atlas) |
| back | Step back | 한 단계 뒤로 |
| thisSheet | This sheet | 이 도움말 |

**단축키 바인딩(main.ts):** `1–5` 화면 이동 · `⌘/Ctrl K` 팔레트 · `[` 사이드바 토글 · `f` 선택 맞춤(Atlas) · `Esc` 뒤로/닫기 · `?` 단축키 시트.

## 14. 시트 · 온보딩 · 기타 (`ui.sheet` · `ui.intakeUi` · `ui.packsUi` · `ui.recordsUi`)

| 키 | EN | KO |
|---|---|---|
| sheet.preparing | Preparing the pack… | 팩 준비 중… |
| sheet.shareTitle | Share as a pack | 팩으로 공유 |
| sheet.counts(c,e,r) | {c} concepts · {e} connections · {r} documents | 개념 {c}개 · 연결 {e}개 · 문서 {r}개 |
| sheet.nothing | Nothing in scope. | 범위에 아무것도 없습니다. |
| sheet.keepNote | Your pins, tags and usage stay with you. The pack carries document content only. | 고정·태그와 사용 기록은 당신에게 남습니다. 팩에는 문서 내용만 담깁니다. |
| sheet.save | Save pack file | 팩 파일 저장 |
| sheet.importTitle | Import a pack | 팩 불러오기 |
| sheet.createdScope(created,scope) | Created {created} · scope {scope} | 작성 {created} · 범위 {scope} |
| sheet.updateNote(c,e,r) | {c} concepts · {e} connections · {r} documents. Safe update: anything already here is refreshed in place, never duplicated. | 개념 {c}개 · 연결 {e}개 · 문서 {r}개. 안전한 업데이트: 이미 있는 문서는 중복되지 않고 최신 내용으로만 갱신됩니다. |
| sheet.importMine | Import as mine | 내 지식으로 가져오기 |
| sheet.viewAtlas | View in Atlas | Atlas에서 보기 |
| intakeUi.importing | Importing… | 가져오는 중… |
| intakeUi.imported(files,nodes,edges) | Imported {files} file(s): {nodes} concept(s), {edges} link(s) added. | {files}개 파일에서 개념 {nodes}개, 연결 {edges}개를 추가했습니다. |
| intakeUi.someFailed(names) | Couldn't read: {names}. | 읽지 못한 파일: {names}. |
| intakeUi.mcpTitle | Go deeper with an assistant | 어시스턴트로 더 깊이 파기 |
| intakeUi.mcpBody | Vinc reads plain documents on its own and keeps them whole. For richer extraction, from code or a format Vinc doesn't read, connect an AI assistant in Settings → MCP and it writes concepts into Vinc for you. | Vinc는 일반 문서를 직접 읽고 원문 그대로 보관합니다. 코드처럼 더 깊은 추출이 필요하거나 Vinc가 읽지 못하는 형식이라면 Settings → MCP에서 AI 어시스턴트를 연결하세요. 어시스턴트가 자료를 읽고 개념을 Vinc에 대신 써넣습니다. |
| intakeUi.trySample | Try the sample pack | 샘플 팩 체험하기 |
| intakeUi.importingSample | Importing the sample… | 샘플 불러오는 중… |
| intakeUi.sampleLoaded | Sample loaded: 6 concepts across 2 topics. You're viewing the sample space now (switch back anytime in Settings). | 샘플을 불러왔습니다: 2개 주제, 개념 6개. 지금은 샘플 공간을 보고 있습니다(Settings에서 언제든 되돌릴 수 있습니다). |
| packsUi.preview | Preview | 미리보기 |
| packsUi.previewHint | Create a pack to see its details here. | 팩을 만들면 상세 정보가 여기에 표시됩니다. |
| packsUi.share | Share | 공유 |
| packsUi.domain | topic | 주제 |
| packsUi.startFrom | start from | 시작 지점 |
| packsUi.reach | reach | 범위 |
| packsUi.offline | Offline-portable. | 오프라인 휴대 가능. |
| recordsUi.filter | Search documents… | 문서 검색... |

## 15. 덱 밖 하드코딩 문자열 (코드 직접 기입)

| 위치 | 값 | 성격 |
|---|---|---|
| settings.ts | `Settings` (h2) | 표면 제목(미번역) |
| settings.ts | `dark` / `light` / `auto` | 테마 칩 값(소문자 그대로 노출) |
| settings.ts | `English` / `한국어` | 언어 칩 라벨 |
| atlas.ts 크럼 | `Atlas` / `Grid` / `Matrix` / `{a} ⇄ {b}` / `›` | 브레드크럼 이름·구분자 |
| atlas.ts 보기 칩 | `2D` / `3D` | 뷰 전환 |
| atlas-prefs.ts | `shell` `radial` `circle` `tree` `organic` | 배치(Layout) 값 — 미번역 노출 |
| search.ts | `≡`(키워드 암) / `≈`(시맨틱 암) | 매치 사유 표기(D24) |
| search.ts placeholder 접미 | `domain:math tag:core "phrase"` | 연산자 예시 |
| records.ts 메타 구분 | `·` (예: `{domain} · {date} · pin`) | 행 메타 |
| records.ts 상태 표기 | `●` 고정 / `◐` 태그됨 / `○` 일반 (stateDot) | 상태는 색이 아닌 형태 |
| packs.ts 입력 기본값 | `all` / `(anywhere)` / `2` | 공유 컨트롤 플레이스홀더 |
| packs.ts 파일명 | `vinc-share-{domain|seed|all}.json` | 다운로드 파일명 |
| sheets.ts 파일명 | `vinc-share-{domain|seed|slice}.vincpack` | 팩 파일 확장자 정본 |
| main.ts aria | `Command palette` / `Keyboard shortcuts` | 다이얼로그 aria-label(EN 고정) |
| index.html | `Vinc — Mine what matters.` | 창 타이틀 |

## 16. 기호 사전 (Notation)

| 기호 | 의미 | 사용처 |
|---|---|---|
| ● | 고정됨(pinned) | Records 행 선두 |
| ◐ | 태그됨(tagged) | Records 행 선두 |
| ○ | 일반(plain) | Records 행 선두 |
| ≡ | 키워드 일치(BM25 암) | Search 결과 행 |
| ≈ | 의미 일치(벡터 암) | Search 결과 행 |
| ⇄ | 두 도메인 비교(Compare) | Atlas 크럼 |
| › | 크럼 구분자 | Atlas 크럼 |
| → / ← | 나감/들어옴(방향 간선 수) | atlasx.outIn |
| · | 메타 구분자 | 행 메타·카운트 전반 |

## 17. 카피 개선 이력 — 개발자 직역 → SaaS UX 라이팅 (2026-07-12 발주자)

**적용 완료.** 발주자 개선본을 홈페이지·사용 가이드뿐 아니라 **앱 덱(copy.ts) 전체에 반영**했다.
§3–§14 표는 이미 개선본이며, 아래는 무엇이 어떻게 바뀌었는지의 이력이다. 내부 스키마명
(record/domain/tie)은 코드·API에서 유지되고, **사용자 표면 어휘만** 전환한다([[vinc.a.terms]] §3 의미 전환 원칙).

| 옛 앱 원문(KO) | 개선본(적용됨) | 키 |
|---|---|---|
| 말뭉치를 들여오세요. | 데이터 추가하기 | intake.idleTitle |
| 출처를 끌어다 놓으면 기록이 됩니다. | 문서를 가져오면 지식이 됩니다. | ui.meta.intake |
| {m}개 중 {n}개 채굴 중. | {m}개의 문서 중 {n}개 분석 중... | intake.loading |
| 조각 공유 | 팩 공유하기 | packs.shareCta |
| 팩 들여오기 | 팩 불러오기 | packs.importCta |
| 기록 / 도메인 | 문서 / 주제 | 카운트 표기 전반 |
| 잇기 / 결합 | 연결 | ui.atlasx.bind·noTies 등 |
| 표면 이동 | 화면 이동 | palette.goSurface |
| 레일 접기/펼치기 | 사이드바 열기/닫기 | palette.railToggle |
| 기록 필터… | 문서 검색... | recordsUi.filter |
| 이미 있는 것은 제자리에서 갱신됩니다. | 안전한 업데이트: 중복되지 않고 최신 내용으로만 갱신됩니다. | sheet.updateNote |
| 출처 선택 | 파일 선택 | intake.idleCta |
| 중요한 것을 캐낸다. | 흩어진 지식의 핵심을 발견하세요. | tagline |

**미반영(의도적 유지):** 표면 고유명사(Intake·Records·Atlas·Search·Packs), 내부 스키마 필드,
개발자 전용 문자열(§15). **홈 히어로 카피는 앱 덱과 별개**(마케팅 톤):
EN "Think with AI. / See your knowledge mapped.", KO "AI와 함께 지식을 연결하고 / 생각의 깊이를 더하세요."

## 부록 Z. 미분류 원문 사실
(없음 — 2026-07-12 전수 수집 기준. 신규 표면·카피 추가 시 본 문서에 병기할 것.)
