<!-- vinc-export
doc_id: vinc.ops.release-0-7-2
title: v0.7.2 릴리스 묶음 — 스트림 3개와 핸드오프
origin: graph-native@2026-08-02
chunks: 4
verified: true
content_hash: a50167efe687ceb0c92699170a1ff51519a666110535dd57930bcaba8657fe0f
updated_at: 2026-08-02T12:07:23.322Z
-->

---
doc_id: vinc.ops.release-0-7-2
layer: ops
domain: release
title: v0.7.2 릴리스 묶음 — 스트림 3개와 핸드오프
status: draft
authority: SSoT (계획)
version: v0.2
owner: vinc
last_updated: 2026-08-02
inherits_from: [vinc.ops.deployment, vinc.c.account-policy]
tags: [vinc, ops, release, 0.7.2, handoff, account-policy, search]
---

# v0.7.2 릴리스 묶음

> **직전 릴리스 = v0.7.1.** 다음 릴리스 v0.7.2 에 세 갈래를 **함께** 싣는다(발주자 결정
> 2026-08-02). 셋 중 하나가 늦으면 릴리스 전체가 기다린다 — 그러니 순서는 §4 를 따른다.
>
> 이 문서는 **핸드오프**다. v0.2(2026-08-02 저녁): 스트림 B 완료·C 판정 완료 —
> **남은 것은 §4 릴리스 절차뿐이다**(버전 범프 · 태그 · 스트림 A env 선행 배포).

---

## 1. 무엇을 묶는가

| 스트림 | 내용 | 상태 | 정본 |
|---|---|---|---|
| **A** | 계정 정책 개편(개인·팀 티어 · 주제 상한 · 결제 · 팀 협업 · 체험 기간 · 화면) | **코드 완료·커밋(fbd6f72)·미배포** | [[vinc.c.account-policy]] (DEC AP-1) |
| **B** | 검색·노드 재작업 = GM-14·GM-13·GM-11a + ingest warning | **코드 완료·커밋 4개(2026-08-02)** | §3.1 · `record:release-0-7-2-scope` |
| **C** | 기존 v0.7.2 배포 대기분 | **없음(판정 완료)** | §3.2 |

---

## 2. 스트림 A — 계정 정책(AP-1)

이 세션에서 P0~P4 + 체험 기간까지 코드가 섰다. **배포는 하지 않았다.**

### 2.1 착지한 것

| 단계 | 내용 |
|---|---|
| P0 | 등급 값 공간에 `team` 추가 · 미지값 강등 시 경고 로그 · **OAuth 등급 동결 해소**(TTL 재조회 + 계정 `/internal/mcp/plan`) |
| P1 | 무료 일일 한도 **15 → 2,000**(기본값 자체를 정책값으로) · **주제 상한 5개**(`merge_fragment` 관문) · 사이트 문구 7곳 정정 |
| P2 | 마이그레이션 011·012 · 결제사 추상화 + Paddle · 웹훅(서명·DB 멱등·역순 가드) · 상태기계 · `users.plan` 투영 · 계정 삭제 순서에 구독 해지 삽입 |
| P3 | 마이그레이션 013~018 · 허브 `proxy-team`/`purge-team` 유형 분리 + 역할별 op 표 · 팀 저장소·라우트·초대 메일·핸들 검색 · 그래프 `?team=` 스코프 |
| P4 | `site/pricing/` 신설(GNB·sitemap 등재) · 계정 플랜 카드 · `site/account/team/` |
| 체험 | `TRIAL_UNTIL` 날짜 스위치 · 체험을 **진짜 구독 행**으로(`provider='manual'`) |

검증: 허브 584 통과(신규 45) · 계정 108 통과(신규 31) · 금지어 게이트 3개 리포 PASS.
~~로컬 pytest 는 항상 7건 실패한다~~ **정정(2026-08-02 실측)**: 이 개발기(.venv, ladybug
설치됨)에서는 실패 0 이다 — 스트림 B 착수 전 기준선 **717 passed · 6 skipped**.
"항상 7건 실패"는 다른 환경의 기록이었다. 다른 파일이 실패하면 그건 진짜 회귀다.

**커밋 확정(2026-08-02)**: 세 리포의 스트림 A 미커밋 변경을 스트림별 커밋 지침대로
확정했다 — `Vinc@fbd6f72` · `Vinc-homepage`(결제·팀·요금제) · `Vinc-docs`(동반 문서).

### 2.2 데스크탑 릴리스에 실리는 것 · 아닌 것

`api/fragment.py`·`api/query.py`·`api/server.py`·`api/share.py` 변경은 **사이드카에 동봉되므로
데스크탑 v0.7.2 에도 실린다.** 다만 로컬에서는 **무해하게 잠들어 있다**: 주제 상한은
`max_domains=None`(무제한)이 기본값이고 로컬 경로는 자격을 실어 보내지 않는다. 이 기본값이
"면제를 잊는 위험"을 "적용을 잊는 안전한 실패"로 뒤집는 장치다([[vinc.c.account-policy]] §3.3).

`vinc_mcp/*` 변경은 허브 배포로만 발효한다(데스크탑은 stdio 경로라 `forced_owner` 가 없다).
**`app/` 은 이번에 손대지 않았다** — 데스크탑 등급 배지는 미착수(§5).

### 2.3 배포 전 필요한 것

**환경변수가 코드보다 먼저**다(`az containerapp update` 는 env·시크릿을 반영하지 않는다):

- 허브(`vinc-hub`): `VINC_QUOTA=on` · `VINC_FREE_MAX_DOMAINS=5`
  (없으면 **주제 상한 기능 자체가 꺼진다** — 무료/유료의 유일한 차별축이다)
- 계정(`vinc-account`): `BILLING_ENABLED=1` · `PADDLE_API_KEY` · `PADDLE_WEBHOOK_SECRET` ·
  `PADDLE_ENV` · `PADDLE_PRICE_PRO` · `PADDLE_PRICE_TEAM` · (체험 시)`TRIAL_UNTIL` ·
  (팀 GA 시)`TEAMS_ENABLED=1`
- DB: 마이그레이션 **011~018** `dbmate up`

순서: **DB → 허브 → 계정 → 스위치**. 어서션 유형 분리 덕에 순서를 틀려도 오동작이 아니라
"팀 기능이 아직 안 열림"으로 끝나지만, 그래도 이 순서가 맞다.

### 2.4 발주자 외부 절차(리드타임이 병목)

Paddle 가입·상품 심사(가격 ID·API 키·웹훅 시크릿이 여기서 나온다, 웹훅 URL 은
`https://vincs.io/api/billing/webhook` — `api.vincs.io` 직접 호출은 EasyAuth 때문에 401) ·
세무사 확인(MoR 경유 통신판매업 신고 요부·해외 매출 처리) · Paddle 원화 정산 가능 여부 ·
support 메일 수신 경로(현재 MX 없음).

---

## 3. 스트림 B·C — 확정 (2026-08-02)

### 3.1 스트림 B — 검색·노드 재작업 = PROMPT-vinc-0.7.2 (GM 패치 4종)

발주자 작업 지시서 `PROMPT-vinc-0.7.2.md`(스토어 근거: [[vinc.plan.methodology-redesign]]
의 GM-9~GM-14 후보)가 스트림 B 의 정본이었다. **T1~T4 전부 코드 완료·커밋(2026-08-02)**:

| T | 내용 | 커밋 |
|---|---|---|
| T1 | GM-14 `vinc_search` 커서 페이지네이션(total_count·has_more·next_cursor, 무효/실효 커서 명시 오류) + 요약·상세 분리(snippet 기본, detail=full 옵트인) + MCP 봉투 shrinker(72k 초과 시 거부 대신 잘린 페이지) | `Vinc@1dbdf8d` |
| T2 | GM-13 이행 배지 — `/search` 에 `embed_migration:{pending}` 가산, 검색 화면 Success 하위 배지(EN/KO 카피 덱, em 대시 0, 색 비의존) | `Vinc@693cb52` |
| T3 | GM-11a 태그 3분류([스키마 고정]/[이 그래프가 정함]/[주제마다 다름], 신·구 겸용 파서) + 입도 절 개체·개념 구분 2줄 → 템플릿 EN/ko 60/60줄 | `Vinc@c16c68c` · `Vinc-homepage@1fb1ee2` · `Vinc-docs@f83663d`(VINC-C-011 v0.2) |
| T4 | ingest 엣지 미선언 warning 구분·조치 문구(nodes 최소 형태 동봉 안내). 구조 수정은 `concept:dec-gm-15` 제안으로만 | `Vinc@9c3033b` |

검증 게이트(전부 통과): 전량 744 passed·6 skipped(기준선 717 + 신규 27) ·
`graph-md check` en/ko exit 0 · MCP 계약(도구 19종 불변) · 프록시 등재 회귀 ·
변경 화면 텍스트 em 대시 0 · EN/KO 실렌더 + 콘솔 오류 0(이행 상태 실스토어로 배지 확인).

의도적 제외(끌어오지 말 것): GM-9·GM-10(e5 이행 후 한 배치) · GM-11b(**08-29 P·D 판정
오염** — 특히 "한 그래프 한 주제"는 D 분모를 구조적으로 바꾼다) · GM-12(0.8.0 후보).

**스키마 무변경** — §4 의 workflow_dispatch 특례 불필요, 통상 배포 절차로 간다.

### 3.2 스트림 C — 기존 v0.7.2 배포 대기분: **없음**

실측(2026-08-02): `git log v0.7.1..HEAD` 의 커밋은 전부 이 세션의 스트림 A·B 뿐이다 —
v0.7.1 태그 시점에 미출시 커밋이 없었다. 스트림 C 는 공집합으로 판정한다.

### 3.3 P·D 지표 스냅샷 (2026-08-29 판정 기준선, 2026-08-02 채취)

- **D(도메인 분열) = 0** — 도메인 8종(vinc/architecture·design·governance·infra·mcp·
  security·timeline·web), 정규화(소문자·구분자 제거) 후 2개 이상 묶이는 그룹 없음.
- **P(proposed 타입 수) = 이 채널로 측정 불가** — `/types` 는 HTTP 전용이고 허브
  `_PROXY_OPS`·계정 프록시가 의도적으로 막는다(재가 비대칭). **배포 세션이 허브
  사이드카 옆에서 `GET /types` 로 P 를 채취해 이 절에 추가할 것.** 참고: 관례 문서의
  재가 어휘 구역은 "위 다섯/여섯 외에 없음"(authored 0 초과분 없음).

---

## 4. 릴리스 순서

```
스트림 B 해결(✅ 2026-08-02) → (A·B·C 합류 ✅) → 버전 범프 → 태그 push → CI 3-OS → draft Publish
                                    ↓
                    계정·허브는 별도 CD (태그와 무관, main push 트리거)
```

**표면이 다르면 배포도 다르다**는 것을 잊지 말 것([[vinc.ops.deployment]]):
데스크탑만 태그로 나가고, 계정·허브·사이트는 `main` push 로 이미 나간다. 즉 스트림 A 의
계정·허브 부분은 **태그를 기다리지 않고 먼저 배포될 수 있다** — 오히려 그 편이 맞다
(팀 어서션은 허브가 먼저 서 있어야 한다). ⚠ 단 **§2.3 env 가 코드보다 먼저**다 —
그래서 이 세션은 커밋만 하고 **push 하지 않았다**(push = 즉시 CD).

버전 범프 지점: 메모리 기록은 **6곳**(`package.json`·`tauri.conf.json`·`Cargo.toml`·
`Cargo.lock`·`version_info.txt`·`CHANGELOG`), [[vinc.ops.deploy-pipeline-research]] 는
`package-lock.json` 포함 **7곳**이라 한다. **태그 전에 실측으로 대조할 것** — 빠뜨리면
업데이트 토스트가 영원히 반복된다.

배포가 실제로 나가면: `record:release-0-7-2-scope` 를 지우고
`episode:2026-XX-XX-release-0-7-2` 로 옮긴다(레코드는 현재 참, 에피소드는 당시 사건) ·
§3.3 의 P 를 채취해 채운다.

---

## 5. v0.7.2 에 넣지 않는 것(의도적)

- **데스크탑 등급 배지** — `app/src/surfaces/settings.ts` 는 이번에 손대지 않았다.
  웹 `/api/me` 가 이미 `plan` 을 주고 있으나 표시하지 않는다.
- **Toss 결제**(국내 경로) — 사업자 서류·PG 심사 리드타임 때문에 2단계다.
- **개인정보 처리방침·이용약관의 팀 조항** — 릴리스가 아니라 **팀 GA 의 선행조건**이다.
  멤버 콘텐츠가 팀 전체에 보이고 **제명 후에도 기여가 남는다**는 사실, 그리고 공개 핸들이
  팀 관리자에게 검색된다는 사실을 고지해야 한다. 사후 고지는 분쟁 소지다.
- **체험 종료 안내 메일** — 체험이 끝나는 날 사용자는 그냥 free 로 내려간다.
  실사용자를 체험에 태울 거면 이게 먼저다.
- **GM-9·GM-10·GM-11b·GM-12** — §3.1 의 의도적 제외 참조.

---

## 6. 다음 세션(=릴리스 세션)이 할 것

1. §2.3 — 스트림 A env·마이그레이션 선행, 그 다음 3개 리포 push(= 계정·허브·사이트 CD).
2. §4 — 버전 범프 지점 실측 대조 → 태그 push → 3-OS draft → Publish.
3. §3.3 — 허브에서 P 채취(`GET /types`, status=proposed 카운트) 후 이 절 갱신.
4. 배포 완료 시 `record:release-0-7-2-scope` → `episode:2026-XX-XX-release-0-7-2` 이관.
5. [[vinc.c.account-policy]] §12 장부가 "미착수"로 남아 있으면 실상태(에피소드
   `2026-08-02-ap1-p0-p2-landed` · `2026-08-02-ap1-p3-teams-landed`)와 맞춘다.

## 부록 Z. 미분류 원문 사실

(없음)
