<!-- vinc-export
doc_id: vinc.ops.risk-audit-2026-08-05
title: 전면 위험 감사 (2026-08-05) · 장애·보안·운영·비용·계정간
origin: graph-native@2026-08-05 · 코드 3리포 + Azure as-built 실측
chunks: 10
verified: true
content_hash: 75be53aa163944c363e2bccb2af40e381329142ca224369110c61772a239b1f3
updated_at: 2026-08-04T18:27:37.154Z
-->

---
doc_id: vinc.ops.risk-audit-2026-08-05
layer: ops
domain: risk
title: 전면 위험 감사 (2026-08-05) · 장애·보안·운영·비용·계정간
status: final
authority: SSoT
version: v1.0
owner: vinc
last_updated: 2026-08-05
inherits_from: [vinc.ops.deployment, vinc.c.dataplane, vinc.c.web-vincs-app, vinc.ops.azure-cost]
tags: [vinc, ops, risk, security, incident-prevention]
---

# 요약

라이브 코드 세 리포(`Vinc` 엔진·허브·데스크탑, `Vinc-homepage` 계정·사이트, `Vinc-docs`
운영 정본)와 실제 Azure 구성, 그리고 그래프에 적재된 as-built 문서를 함께 훑어 위험을
정리했다. 카테고리는 장애·보안·운영·비용·인프라 붕괴·MCP 외부연결·계정 간 오류다.

**머리 결론 두 가지.**

1. **호스티드에서 owner 술어가 빠진 경로가 아직 남아 있다.** SEC-1/SEC-3 은 노드 조회와
   fragment 쓰기에는 걸려 있으나, **검색의 청크 팔**과 **동기화 apply 경로 전체**가 그
   밖에 있다. 전자는 남의 문서 본문이 검색 결과 발췌로 나가고, 후자는 유효한 회원 키
   하나로 남의 개념과 엣지를 지울 수 있다. 둘 다 이번에 코드로 직접 확인했다.
2. **계정 서버의 매직링크 토큰이 목적(purpose)을 검증하지 않는다.** 팀 초대 링크(7일
   유효, 남에게 메일로 나감)나 계정삭제 확인 링크를 `POST /api/auth/verify` 에 넣으면
   그 이메일의 완전한 로그인 세션이 발급된다. 계정 탈취 경로다. 역시 코드로 확인했다.

나머지는 "조용히 나빠지는" 계열이다. 이 프로젝트가 반복해 겪은 실패 모양은 늘 같다.
**기동 정상 · `/health` 200 · 기능만 죽음.** 이번 감사에서도 그 모양의 새 사례가 여럿
나왔다(사이드카 사망 후에도 200, 문서 동기화 미배선, 결제 스윕 미호출, 스타터팩 수동 시딩).

# 0. 이번에 코드로 직접 확인한 것과 그렇지 않은 것

정본에 사실로 적기 전에 **아래 아홉 건은 파일을 열어 확인**했다. 그 밖의 항목은 근거
라인을 인용하되 재확인하지 않았으므로 조치 전에 한 번 더 볼 것.

| 확인 | 내용 |
|---|---|
| 확인됨 | `api/query.py:3355` `_chunk_arms(..., filters)` 에 owner 미전달. owner 는 `_node_fallback_search` 에만 간다(`:3345`). `api/server.py:277` 의 filters 는 layer·doc_id 뿐 |
| 확인됨 | `Chunk.owner_id` 열은 존재하고(`store/embedded_schema.py:234` v4 ALTER), `_filter_clause` 는 `owner_id` 를 이미 지원한다(`store/embedded_backend.py:69`). 즉 **막을 수단이 있는데 안 쓴 것** |
| 확인됨 | `api/sync.py:526` 스탬프 SET · `:547` 노드 툼스톤 DETACH DELETE · `:571` 엣지 툼스톤 DELETE 전부 owner 술어 없음 |
| 확인됨 | `account/src/repos/authTokens.ts:123,134` `peek`/`consume` 에 purpose 조건 없음. 대조군 `consumeDelete:71`·`consumeTeamInvite:111` 은 걸려 있다 |
| 확인됨 | `account/src/routes/auth.ts:86,108` 이 그 둘을 그대로 쓰고 `:111` 에서 `findOrCreateByEmail(row.email)` 로 세션 발급 |
| 확인됨 | `setCsrf` 호출부는 `auth.ts:120`(로그인) 한 곳뿐. `session.ts:21` 은 세션만 슬라이딩 |
| 확인됨 | `billing/service.ts:135` `sweepExpired` 호출부 0건 |
| 확인됨 | `repos/teamInvites.ts:93` `findByTokenHash` 가 status 무시 · `revoke:111` 은 `team_invites` 만 갱신(`auth_tokens` 행은 살아 있음) |
| 확인됨 | `vinc_mcp/bridge.py:40` 이 `os.kill(pid, 0)`. 같은 리포 `api/server.py:1240` 은 그 함정을 문서화하고 Windows 전용 `OpenProcess(SYNCHRONIZE)` 로 우회한다 |

**실측 프로브(2026-08-05).** `mcp.vincs.io/health` 200(`sidecar.reachable:true`,
`oauth_state.restore_state:"restored"`) · `/.well-known/oauth-protected-resource` 200 ·
`POST /mcp` 의 `www-authenticate` 가 `https://` · `vincs.io/api/health` 200 ·
`POST https://api.vincs.io/internal/mcp/verify-key` 가 **본문 있는 앱 게이트 401**
(`{"error":"unauthorized"}`)이다. 즉 2026-08-04 EasyAuth 조치(`--excluded-paths`)는
지금도 유효하다. 동시에 그 경로가 **공개 도달 가능**하다는 사실도 그대로 확인된다.

# 1. 계정 간 오류 (교차테넌트) · 최우선

호스티드 허브는 단일 스토어를 다중 소유자로 돌린다. 그래서 owner 술어 누락은 곧 사고다.

## 1.1 [P0] 검색 청크 결과에 owner 필터가 없다

`vinc_search` 의 `rows[]` 는 **다른 회원의 문서 본문 발췌**를 실을 수 있다. `node_hits`
는 막혀 있어 눈에 잘 안 띄고, 회귀 테스트도 노드 폴백만 본다
(`tests/test_owner_isolation.py`). 벡터 팔·FTS 팔 양쪽 모두 해당한다.

조치는 한 줄에 가깝다. `read_search` 에서 `filters["owner_id"] = owner` 를 넣고,
owner 가 강제된 경로(호스티드·웹)에서 filters 가 비면 **거부**하도록 fail-closed 로 둔다.
그리고 `test_owner_isolation` 에 청크 팔 계약 테스트를 추가한다. 지금 이 테스트의 공백이
이 결함을 살려 뒀다.

## 1.2 [P0] 동기화 apply 가 남의 노드를 지우고 갱신한다

`apply_delta` 는 세 자리에서 owner 를 안 본다.

- **노드 툼스톤**(`:547`): 유효한 `vinc_sk_*` 키를 가진 아무 회원이
  `{"tombstones":[{"node_id":"concept:...","label":"Concept","deleted_at":"9999-..."}]}`
  을 push 하면 남의 개념이 지워진다. 유일한 가드는 `updated_at > deleted_at` 인데
  `deleted_at` 이 **공격자 제어값**이라 무력하다.
- **엣지 툼스톤**(`:571`): 같은 경로로 남의 관계를 지운다. 더 조용하다.
- **스탬프 루프**(`:526`): `merge_fragment` 가 SEC-3 로 "owned by another tenant"
  스킵을 냈는데도, 그 뒤 SET 이 **스킵된 노드의** rev·sync_seq 를 올린다. 파괴는 아니지만
  피해자의 3-way base 가 어긋나 조용한 동기화 손상이 된다.

조치: 세 쿼리에 `x.owner_id = $owner` 를 넣고, `deleted_at` 을 서버 HLC 로 클램프하고,
스킵된 노드를 `writes` 에서 빼낸다.

## 1.3 [P1] `_SyncBase` 가 owner 차원 없이 node_id 를 PK 로 쓴다

두 회원이 같은 concept_id 를 쓰면 3-way 기준본이 서로를 덮어써 임의의 상대 값과 병합이
일어난다. 에러 없이 데이터가 틀어진다. PK 를 `(owner_id, node_id)` 복합키로 올려야 한다.
같은 뿌리로 동기화 커서(`cursor:push`/`cursor:pull`)·`selected_domains`·`last_hlc` 도
owner 네임스페이스가 없다 (2026-07-21 pull 100% 빈값 사고와 동일 계보다. 그때는 우회로만
막았고 뿌리는 남았다).

## 1.4 [P1] 관례 문서 `graph.md` 가 전 테넌트 공유 단일 doc_id

`CONVENTIONS_DOC_ID = "graph.md"` 이고 `_DocMeta` 의 PK 는 doc_id 다. 호스티드에서는
**먼저 [다시 생성]을 누른 회원이 그 문서를 소유**하고, 나머지 전원은 build 가 400
`owner mismatch`, 읽기는 조용히 기본 템플릿으로 강등된다. doc_id 를 owner 파생으로
바꾸고 마이그레이션해야 한다.

## 1.5 [P1] 주제·타입 어휘가 프로세스 전역 파일

ThemeStore 는 `<data_dir>.themes.json` 단일 파일이고 앱당 하나다. 호스티드에서는 회원 A
가 만든 개방 타입 이름이 공유 어휘에 proposed 로 들어가고, 회원 B 의 `vinc_conventions`
가 그것을 "이 그래프의 어휘"로 읽는다. 사용자가 지은 타입명이 새어 나가고 오염된다.

## 1.6 [P1] `/jobs/{id}` 가 owner 에 안 묶여 있다

잡 id 가 `job-{단조정수}` 라 열거가 쉽고, 조회에 owner 대조가 없다. 다른 테넌트의
ingest·sync·backup 잡 결과(파일 경로·건수·warnings)를 읽을 수 있다. 웹 프록시도 op `job`
을 그대로 통과시킨다. id 를 난수 토큰으로 바꾸고 owner 를 기록·대조한다.

## 1.7 [P1] 팀 구독이 개인 등급으로 새어 나간다

`billing/service.ts:29` 가 `rows.some(isEntitled) ? "pro" : "free"` 라 **plan 종류를 안
본다**. 팀 좌석 하나만 결제해도 그 사람의 개인 그래프가 Pro 가 된다(`/api/me` 도
`verify-key` 도 pro 를 반환). 반대 방향 결함도 있다. 팀 삭제가
`cancelSubscriptionsFor(user)` 로 **그 사람의 모든 구독**을 해지해 개인 Pro 까지 끊는다
(`repos/subscriptions.ts:62` 의 필터에 `team_id` 가 없다).

## 1.8 [P2] 계정 삭제 purge 의 잔여

`_purge_labels()` 에 `_EdgeTombstone`(owner_id 보유)이 빠져 있고 `_SyncBase` 도 안 지운다.
계정 삭제 후 엣지 삭제 표식과 병합 기준본이 남는다. 커뮤니티 팩은 반대로 CASCADE 로
사라져 **다른 회원의 갤러리 링크가 404** 가 된다. 표면마다 정책이 다르다.

# 2. 인증·세션 (계정 서버)

## 2.1 [P0] 토큰 목적 미검증 · 계정 탈취

`peek`/`consume` 에 `purpose='login'` 조건이 없다. 따라서
`POST /api/auth/verify {token: <팀초대토큰>, confirmed: true}` 는

1. `peek` 통과(초대 토큰도 미사용·미만료라 그대로 나온다),
2. `confirmed:true` 가 flow-binding(`__Host-lc`) 검사를 건너뛰게 하고,
3. `consume` 통과,
4. `findOrCreateByEmail(초대받은 이메일)` 로 **그 이메일의 로그인 세션 발급**

으로 끝난다. 초대 토큰은 **7일** 유효하고 제3자에게 메일로 나가며 "팀 참여 링크"라
전달·공유되기 쉽다. 계정이 없으면 새로 만들어 준다. 삭제 확인 토큰도 같은 방식으로
로그인 토큰으로 승격된다.

`authTokens.ts` 주석은 초대 토큰의 위험을 정확히 알고 있다("링크를 넘겨받은 아무나 남의
초대를 가로챈다"). 그 방어는 `consumeTeamInvite` 에만 들어갔고, **같은 표를 읽는 로그인
경로에는 안 들어갔다**. 조치는 `peek`/`consume` 에 purpose 조건 추가와 회귀 테스트다.

## 2.2 [P0] CSRF 쿠키가 세션과 함께 슬라이딩되지 않는다

세션 쿠키는 매 요청 재발급(30일 idle 슬라이딩)되는데 `__Host-csrf` 는 로그인 때 한 번만
발급되고 maxAge 가 같은 30일이다. 따라서 **로그인 후 정확히 30일에** 세션은 살아 있는데
CSRF 쿠키만 사라지고, 그때부터 모든 상태변경이 403 `csrf_failed` 가 된다.
**로그아웃조차 `requireCsrf` 라 사용자가 스스로 빠져나올 수 없다.** 시한폭탄이다.

## 2.3 [P1] 초대 취소가 실효 없다

`findByTokenHash` 가 status 를 무시하고 수락 경로도 `inv.status` 를 안 본다. `revoke` 는
`team_invites.status` 만 바꾸고 `auth_tokens` 행은 살려 둔다. 결과적으로 **취소된 초대
링크로도 팀에 들어온다**. 취소로 좌석이 비어 좌석 검사도 통과한다. 팀 그래프 전체 열람·
편집 노출이다.

## 2.4 [P1] `X-Forwarded-For` 무검증 · 레이트리밋 전면 우회

`lib/net.ts` 가 첫 홉을 그대로 클라이언트 IP 로 쓴다. 헤더를 위조하면 매직링크 20/h,
verify 30/h, 핸들 조회 60/h 상한이 사실상 무한이 된다. 매직링크 무차별 발송은 발신
도메인 평판까지 깎는다.

## 2.5 [P1] 레이트리밋·크론·발송 루프가 전부 인프로세스

`lib/ratelimit.ts` 는 스스로 "단일 인스턴스 전제"라고 적어 뒀다. 리플리카가 N개면 실효
한도가 N배가 되고 purge 크론이 N번 돌며, 릴리스 메일의 **전역** 5회/시간 상한도 리플리카
별로 독립이라 중복 대량 발송이 가능해진다. 지금은 `min=max=1` 이라 우연히 성립하는데,
그 설정이 보안 가정을 떠받치고 있다는 사실이 어디에도 적혀 있지 않다.

## 2.6 [P2] 미사용 시크릿과 단일 pepper

`SESSION_SECRET`·`CSRF_SECRET` 은 필수 env 인데 **코드에서 전혀 쓰이지 않는다**. 실제
보안은 `SERVER_PEPPER` 단일 키에 걸려 있고, 그 회전은 세션·API키·구독확인 토큰을 동시에
무효화한다(절차 없음). "세션 시크릿을 회전했다"는 믿음이 사실과 다르다.

## 2.7 [P2] 죽은 도메인이 신뢰 오리진 목록에 남아 있다

`ALLOWED_ORIGINS` 기본값에 `https://vincs.app` 이 있다. A 레코드는 2026-07-29 에
지워졌으니, 그 IP 를 제3자가 확보하면 인증서를 받아 신뢰 오리진을 장악할 수 있다.
`SameSite=Lax` 가 즉시 탈취까지는 막지만 남겨 둘 이유가 없다.

# 3. 장애·복구

## 3.1 [P0] 허브 그래프 DB 에 백업이 없다

`AZURE-ARCHITECTURE.md` §6 이 스스로 적어 뒀다. Azure Files 스냅샷이 **미적용**이다.
MySQL 만 관리형 백업(매일·7일)이 있고 회원 그래프 전량은 무보호다. 스토리지 사고·오삭제·
포맷 손상 어느 것이든 복구 경로가 없다. 클릭 몇 번짜리 조치이므로 이 문서의 모든 항목
중 **비용 대비 효과가 가장 크다**.

## 3.2 [P0] `/health` 가 거짓말을 한다 (두 층 모두)

- 허브: `sidecar.reachable` 을 진단 필드로 싣기만 하고 상태코드는 늘 200 이다. 사이드카가
  죽어도 외부에서는 정상으로 보인다.
- 엔트리포인트 워치독: 5회 실패 후 `exit 1` 인데 그건 **서브셸만** 끝낸다. PID 1 은 계속
  살아 200 을 준다. 즉 "사이드카가 죽으면 컨테이너를 내린다"는 서술이 구현돼 있지 않다.
- Container Apps liveness 프로브는 미설정이라 자동 재기동도 없다.

조치: 워치독 포기 시 `kill -TERM 1`, `sidecar.reachable=false` 면 503, 그 엔드포인트를
liveness 프로브로 등록.

## 3.3 [P1] WAL 재생 실패가 기본 fail-closed 라 리비전을 갈아도 안 열린다

컨테이너 강제 종료(배포·OOM)로 WAL 이 미완결되면 이후 모든 기동에서 `open()` 이 실패해
`driver: null` 이 되고, 복구가 수동 env 주입 후 재배포뿐이다. 2026-08-03 실측 사고가
코드 주석에 남아 있다. 기동 시 1회 자동 tolerant 재시도와 `/health` 표식이 필요하다.

## 3.4 [P1] `snapshot()` 재오픈 실패 = 스토어 영구 브릭

백업 잡이 락 회피를 위해 커넥션을 닫고 복사한 뒤 `finally` 에서 재오픈하는데, 재오픈이
실패하면 `self._db` 가 `None` 인 채 예외만 올라간다. 사이드카는 살아 있고 모든 조회·쓰기가
죽는다. 재시작 외 복구가 없다.

## 3.5 [P1] MCP 브리지가 Windows 에서 데스크탑 앱을 죽인다

`bridge.py` 의 `_pid_alive` 가 `os.kill(pid, 0)` 이고, 그 pid 는 `session.json` 의
**셸(Tauri) PID** 다. Windows 의 `os.kill` 은 `TerminateProcess` 로 매핑되므로 조회가
아니라 종료다. 같은 리포의 `api/server.py:1240` 이 정확히 이 함정을 문서화하고 안전
경로를 구현해 뒀으니, 그 함수를 공용 모듈로 올려 재사용하면 끝난다.

## 3.6 [P1] 계정 삭제가 부분 실패로 갈라진다

`account.ts` 가 삭제 토큰을 **선소비**한 뒤 그래프 purge 를 부르고, purge 실패 시 503 을
낸다. 허브가 죽어 있으면 사용자는 메일을 다시 받아야 하고 그때마다 토큰이 또 소비된다
(1시간 5회 상한에 걸린다). 반대로 `teams.owner_user_id` 는 `ON DELETE RESTRICT` 인데
가드가 `status<>'deleted'` 인 팀만 세므로, 표식만 남은 팀이 있으면 **그래프는 지워졌는데
계정은 남는** 부분 삭제가 된다.

## 3.7 [P1] 결제 스윕이 호출되지 않는다

`sweepExpired()` 는 정의만 있고 호출부가 0건이다. 기동 크론은 만료 퍼지 하나뿐이다.
따라서 `trialing` 체험이 끝나도, past_due 7일 유예가 지나도, `cancel_at_period_end` 가
도래해도 **상태가 안 바뀌고 Pro 자격이 영구 유지**된다. 수익 유실이고, 아래 5.2 의 등급
fail-open 과 겹치면 사실상 되돌릴 수 없다.

## 3.8 [P2] 메일 실패가 전부 무음

`mailer` 는 예외를 던지지 않고 로그만 남기며 호출부는 전부 `void deliver(...)` 다.
`RESEND_API_KEY` 가 만료되면 매직링크가 하나도 안 나가는데 API 는 계속 200 을 준다.
로그인이 매직링크 전용이므로 이것은 곧 전면 로그인 불가다. 실제 전례도 있다. Resend 가
7/30 부터의 연속 실패로 **웹훅을 자동 비활성화**했고 8/4 에야 발견됐다.

## 3.9 [P2] 결제 웹훅 예외를 삼키고 200 을 준다

`applyEvent` 가 DB 순단으로 터져도 catch 에서 `markFailed` 후 `{ok:true}` 를 준다. Paddle
은 재전송하지 않는다. **결제는 됐는데 Pro 가 아닌 사용자**가 생기고 재생 경로가 없다.

# 4. 관측·운영

## 4.1 [P0] 가용성 모니터링이 하나도 없다

있는 알림은 비용 Budget 뿐이다. 그래서 이 프로젝트의 사고는 전부 사용자 신고나 우연한
점검으로 발견됐다. `VINC_MCP_ISSUER` 결손(커넥터 전면 불가), 빈 그래프 14시간 서비스,
EasyAuth 차단(전 회원 키 401) 셋 다 그랬다. **`mcp.vincs.io/health` 와
`vincs.io/api/health` 에 외부 uptime 체크 5분 간격을 거는 것**이 이 문서에서 두 번째로
값싼 조치다.

## 4.2 [P1] 스타터팩 시딩이 마이그레이션이 아니라 수동 단계다

`starter-packs/README.md` 가 직접 적어 뒀다. 스키마만 마이그레이션이고 행은 사람이
넣는다. Azure 이관에서 실제로 `community_packs` 가 0행이 되어 갤러리가 빈 채 서빙됐다.
DB 를 재생성할 때마다 재발한다. 배포 파이프라인의 멱등 스텝으로 올리고, 0행이면 게이트가
실패해야 한다.

## 4.3 [P1] 문서 정본(Chunk=SSoT)이 동기화되지 않는다

서버 라우트(`/sync/docs-export`·`/sync/docs-apply`)와 엔진(`api/docsync.py`)은 있는데
오케스트레이터 `sync.run` 이 노드 delta 만 다루고 허브 프록시도 `pull-delta`/`apply-delta`
만 노출한다. 즉 [지금 동기화]를 눌러도 **문서 본문은 전혀 오가지 않는다**. 개념과
에피소드만 동기화되고 "정본"이 기기에 갇힌다. 에러도 없다.

## 4.4 [P1] 사이드카가 요청 로그를 통째로 억제한다

`log_message` 가 `pass` 다. 사이드카에서 난 4xx/5xx 가 컨테이너 로그에 전혀 안 남는다.
허브 502 의 원인을 사이드카 쪽에서 확인할 방법이 없다. 허브는 4xx/5xx 만 남기는 규율이
있으니 사이드카도 같게 맞춘다.

## 4.5 [P1] 허브 런타임 env 가 코드 밖에 산다

12개 env 가 `az containerapp update` 로 손으로 들어갔고 IaC 도 워크플로도 그것을 검증하지
않는다. 같은 계열 사고가 이미 **세 번**이다. 게이트(`check_hub_env.py`)는 배포 시에만
돌고 값의 존재만 본다. 컨테이너 앱 정의를 bicep 으로 커밋해 소스가 정본이 되게 하고,
필수 env 자체 점검을 프로세스 안으로 옮겨 `/health` 에 `config_ok` 로 노출한다.

## 4.6 [P2] 프록시 허용목록이 세 곳에 중복되고 CI 가 대조하지 않는다

사이드카 핸들러 · 허브 `_PROXY_OPS` · 계정 `GET_OPS`/`POST_OPS`+역할표. 이미 세 번 드리프트
사고가 있었다(`/ledger` 누락으로 합계 뷰 404, `/nav-tree` 누락으로 사이드바 공백,
`/evidence/backfill` 누락). 빠지면 **그 기능만 조용히 404** 다. 허브가 op 목록을 내는
엔드포인트를 두고 CI 에서 세 목록을 diff 로 게이트한다.

## 4.7 [P2] 문서 드리프트

- 쿼터 안내값이 두 곳에서 다르다. `DEPLOYMENT.md` §4.4 는 "무료 15회/일", `check_hub_env.py`
  는 "하루 2,000회" 라고 적는다. 정책 정본(`vinc.c.account-policy` AP-1)은 2,000회다.
- `Vinc-homepage` 의 README 두 개가 아직 가비아 VM·Caddy 기준이다.
- `POST_OPS` 주석이 `doc-delete` 를 "미노출"이라 적었는데 실제로는 등록돼 있다.
- `internal.ts` 상단의 "Caddy 미프록시 · 127.0.0.1 바인딩" 주석은 stale 이다.

# 5. MCP · 외부 연결

## 5.1 [P1] 브리지 재시도가 문서와 다르다 (쓰기 중복)

독스트링은 "연결 자체가 안 되는 경우만" 재시도한다고 적었으나 실제 `except Exception` 이라
**읽기 타임아웃도 재시도한다**. 브리지 타임아웃은 60초인데 잡 타임아웃은 600초다. 따라서
큰 `doc.put`·`ingest:pack`·`sync/apply-delta` 는 이미 큐에 들어간 쓰기를 두 번 더 보낸다.
MERGE 는 멱등이지만 `_stamp_edge_endpoints` 와 `next_seq` 가 rev·seq 를 중복 증가시켜
동기화 에코를 만든다.

## 5.2 [P1] 등급 fail-open 이 강등을 반영하지 않는다

`_fresh_plan` 은 계정 조회 실패 시 "마지막으로 알던 값"을 유지하고, 프록시 어서션에
`plan` 클레임이 없으면 **상한 자체를 안 건다**. 결제 해지·체험 종료로 free 가 된 사용자의
커넥터가 계정 장애 기간 내내 pro 로 동작한다. 위 3.7(스윕 미호출)과 겹치면 영구다.
폴백에 상한 TTL 을 두고, plan 부재를 카운터로 남긴다.

한편 반대 방향의 계약 공백도 있다. 계정은 무효 키에 404, 어서션 실패에 401, 키 미핀에
503, DB 예외에 500 을 주는데 **재시도 가능 여부를 알리는 필드가 없다**. 허브가 전부
"거부"로 다루면 계정 DB 순단에 전 커넥터가 죽고, 전부 "통과"로 다루면 폐기 키가 살아난다.

## 5.3 [P1] 회원 키가 평문으로 두 곳에 있다

- 데스크탑 웹뷰의 `localStorage["vinc.sync.key"]`. D36 은 "키를 디스크에 두지 않는다"고
  했으나 localStorage 는 디스크다. 웹뷰 XSS 하나면 계정 그래프 전체 read/write 키가 샌다.
- MCP 호스트 설정. 이 워크스테이션의 커넥터 설정에 `VINC_REMOTE_MCP_TOKEN=vinc_sk_...`
  가 평문 env 로 박혀 있고 `claude mcp get vinc` 한 줄로 출력된다. 여기에 더해
  `VINC_REMOTE_MCP_URL` 도 env 라, 그 값을 바꾸면 **같은 키가 임의 엔드포인트로 전송**된다.

## 5.4 [P1] 허브 `/internal/*` 가 공개 도달하고 관문이 서명 하나뿐

Caddy 시절의 `respond 404` 차단층은 가비아와 함께 사라졌고 Azure 인그레스에 대체 규칙이
없다. `/internal/graph-owner`(owner 강제 read/write)와 `/internal/purge-owner`(계정 전량
삭제)가 그 뒤에 있다. 계정 쪽 `/internal` 두 경로도 EasyAuth 제외 처리로 같은 상태다
(2026-08-04 조치의 의도된 부작용). 서명 검증에 리그레션이 하나 생기면 곧바로 최악이다.
인그레스 경로 차단이나 별도 포트 분리로 심층방어를 복원해야 한다.

## 5.5 [P2] MCP 도구 계약과 실동작 불일치

- `vinc_delete`·`vinc_delete_edge`·`vinc_merge_nodes` 스키마에 `owner` 가 없는데 핸들러는
  `args.get("owner")` 를 읽는다. 로컬 stdio 는 항상 `owner=None` 이라 **무스코프 삭제**가
  된다. 문서에 그 사실이 없다.
- `target` 이 모든 도구에 주입되는데 허브에서 `target:"remote"` 가 오면 허브가 아웃바운드를
  시도한다. stdio 프로파일에서만 주입해야 한다.
- 4MB 초과 본문이 조용히 `{}` 로 읽혀 "expected a JSON-RPC message" 400 이 된다. 413 과
  명시 사유로 바꿔야 한다.
- 비 UTF-8 본문이 `do_POST` 를 관통해 죽이던 것은 400 강등 수정이 있으나 **미배포**다
  (브랜치 `fix/hub-non-utf8-body-503`, 커밋 `2bb5f58`).

## 5.6 [P2] 포트 8787 · 동적 포트 재bind 레이스

컨테이너는 8787 고정이라 점유되면 헬스 대기 30초 후 `exit 1` 이다. 데스크탑·브리지는
부모가 port 0 을 선점했다 놓고 자식이 재bind 하는 구조라, Windows 에서 OS 가 준 임시
포트가 Hyper-V/WinNAT 예약 범위와 겹치면 자식 bind 가 실패하고 화면에는 `health TIMEOUT`
만 뜬다. 자식이 직접 bind 해 실제 포트를 stdout 으로 보고하는 경로가 이미 있으니 그쪽으로
통일한다.

# 6. 비용 · 리소스

## 6.1 [P1] 임베딩 워커의 결과 축적이 배치 상한을 우회한다

`encode` 는 8개씩 나눠 돌지만 **결과 벡터 전체를 파이썬 리스트로 들고** 있다가
`/embed/apply` 로 JSON 직렬화한다. 1,500 청크 문서면 조각 수천 개 × 768 float 에 JSON
문자열 사본까지 겹친다. 1GiB 컨테이너에서 커널 OOM 이고, **OOM 은 컨테이너 전체 곧 MCP 를
죽인다**. torch 를 피한 이유가 바로 그것인데 배치 축이 아니라 축적 축에서 같은 위험이
재현된다. 사이드카에 요청 본문 상한이 없다는 점(허브는 4MB 로 막는다)이 이를 키운다.

## 6.2 [P1] MySQL `autoGrow` 는 한 번 커지면 못 줄인다

잘못된 마이그레이션이나 로그 테이블 폭증으로 20GB 가 수백 GB 로 뛰면, 데이터를 지워도
요금은 계속 그 크기 기준이다. 되돌리려면 인스턴스 재생성과 이관뿐이다. 여기에 커뮤니티
팩이 `pack_json` 을 **MySQL 행에 통째로** 저장하고(2MB × 10회/시간/회원), 내려도(unlist)
행이 남는다. 하드 삭제 경로가 없다.

## 6.3 [P1] 커뮤니티 팩 다운로드가 무인증·무제한

`GET /api/community/packs/:id/download` 에 인증도 레이트리밋도 없고 매 호출이 DB 에서
`pack_json` 전량을 읽는다. 2MB 를 초당 수십 회 받으면 대역폭과 DB read 가 그대로 과금된다.
CDN 캐시도 없다.

## 6.4 [P2] 쿼터가 웹 경로에 안 걸린다

`quota_check` 는 `/mcp` 에만 걸리고 `/internal/graph-owner`(웹 Vinc 전 경로)는 면제다.
의도된 설계라고 주석에 적혀 있으나, 결과적으로 무료 회원이 웹으로 무한히 ingest·search 를
돌릴 수 있고 그 비용은 컴퓨트로 전가된다. 계정 쪽 그래프 프록시 상한은 300req/분/사용자라
10명이면 분당 3,000 요청이 허브로 간다.

## 6.5 [P2] 무한 증식하는 것들

- 잡 큐가 `dict` 에 결과를 **영구 보관**한다(삭제 없음). 허브 사이드카는 배포 전까지 산다.
- `sync.gc_tombstones` 는 정의만 있고 호출부가 없다. 툼스톤이 무한 누적되면 delta 번들이
  계속 커진다.
- OAuth `purge_expired` 도 호출부가 없다.
- ACR retention policy 가 꺼져 있고 허브 이미지는 약 780MB 다. 매 배포마다 sha 태그가
  쌓여 Basic 10GB 를 십수 회 배포로 소진한다.
- 유휴 세션 행이 최대 60일 남는다(퍼지 조건이 절대 만료만 본다).

## 6.6 [P2] 벡터 인덱스 드롭이 전역이다

`apply_embeddings` 가 매번 `DROP_VECTOR_INDEX` 를 부르는데 그것은 테이블 전역이다. 워커가
5분마다 돌면 허브의 벡터 팔이 사실상 상시 꺼져 있을 수 있다. **비용은 내고 품질은 FTS
수준**이라는 뜻이다. 드롭과 재빌드를 라운드 끝에 한 번으로 묶어야 한다.

# 7. 인프라가 터질 위험

| 위험 | 상태 |
|---|---|
| 단일 리전(Korea Central) · 무이중화 | MySQL `geoRedundantBackup: Disabled`, Storage `Standard_LRS`, HA 미설정. 리전 장애 시 전부 동시 사망하고 복원할 원본도 없다 |
| 허브 스케일아웃 불가 | 단일 writer 배타 락. 트래픽 증가가 비용이 아니라 **장애**로 나타난다 |
| 허브 배포는 다운타임 필수 | 신·구 리비전이 같은 볼륨을 못 나눠 쓴다. 손으로 `update` 만 하면 새 리비전이 조용히 Failed 로 남고 구 리비전이 계속 서빙한다(배포 안 됐는데 정상으로 보임) |
| 데이터 포맷 변경 배포는 롤백 불가 | 리비전을 되돌려도 볼륨은 신포맷이다. 볼륨 백업이 없으므로(3.1) 복구 경로가 0 |
| apex A 레코드가 SWA IP 하드코딩 | 재발급되면 `vincs.io` 전체가 배포 없이 다운된다 |
| Cloudflare 전부 DNS only | 프록시를 켜면 관리형 TLS 갱신이 깨진다. WAF·CDN·DDoS 완충이 구조적으로 0 이고 개선 경로가 막혀 있다 |
| 인증서 검증 TXT 누락 위험 | `asuid.api`·`asuid.mcp` 가 빠지면 만료일까지 무증상이다가 그날 두 도메인이 동시에 TLS 오류 |
| `vincs.app` Resend 4종 | 지우면 로그인 메일이 죽는다. 이미 A 레코드만 골라 지운 전례가 있다 |
| MySQL 방화벽 Azure 전체 허용 | 임의 Azure 테넌트에서 네트워크 도달 가능. 자격증명이 유일한 방어선 |
| 업데이터 서명키 | 암호가 빈 문자열이고 파일 자체가 곧 시크릿. 로컬 파일과 GitHub 시크릿을 함께 잃으면 **전 사용자가 자동 업데이트에서 영구 이탈** |
| 컨테이너 root 실행 · 의존성 스캐닝 0 | Dependabot·CodeQL·audit 전부 없고 `npm ci --no-audit` 로 명시 비활성. 계정 쪽 dependabot 은 major 를 전부 무시한다 |

# 8. 우선순위

## P0 · 지금 고친다

| # | 항목 | 근거 |
|---|---|---|
| 1 | `peek`/`consume` 에 `purpose='login'` | 초대·삭제 링크로 계정 탈취 (2.1) |
| 2 | 검색 청크 팔에 `owner_id` 필터 | 교차테넌트 본문 유출 (1.1) |
| 3 | sync apply 3자리에 owner 술어 + `deleted_at` 클램프 | 교차테넌트 파괴 (1.2) |
| 4 | CSRF 쿠키 슬라이딩 | 로그인 30일 뒤 전 기능 정지, 자력 복구 불가 (2.2) |
| 5 | Azure Files 스냅샷 켜기 | 회원 그래프 무백업 (3.1) |
| 6 | 외부 uptime 체크 2개 등록 | 모니터링 0 (4.1) |

## P1 · 이번 사이클

`sweepExpired` 크론 연결(3.7) · 초대 수락 시 status 검증(2.3) · `_SyncBase` owner 복합키
(1.3) · `graph.md` owner 파생 doc_id(1.4) · ThemeStore owner 스코프(1.5) · 잡 owner
대조와 난수 id(1.6) · 팀↔개인 등급 분리(1.7) · 워치독 `kill -TERM 1` 과 `/health` 503
(3.2) · 브리지 `_pid_alive` 교체(3.5) · 브리지 재시도 예외 좁히기(5.1) · 문서 동기화 배선
(4.3) · 임베딩 apply 스트리밍 + 사이드카 본문 상한(6.1) · XFF 신뢰 홉 정정(2.4) ·
커뮤니티 다운로드 레이트리밋(6.3) · 스타터팩 시딩 파이프라인화(4.2)

## P2 · 등재해 두고 순차

허브 `/internal` 인그레스 차단(5.4) · 어서션 리플레이 방지(jti) · purge 잔여 라벨(1.8) ·
프록시 허용목록 CI 대조(4.6) · GC·purge_expired 호출(6.5) · ACR retention(6.5) ·
dependabot 3개 리포(7) · MySQL geoRedundantBackup(7) · 문서 드리프트 정리(4.7)

# 9. 이 감사의 한계

- **허브 측 집행을 이 감사로는 끝까지 못 봤다.** 계정 서버의 owner 스코핑은 견고하다
  (요청 바디의 owner 를 절대 싣지 않고 서명 sub 만 쓰며, 팀은 어서션 타입 자체를 분리한다).
  그러나 그 집행이 전부 허브 쪽에 있고, 어서션 검증의 실제 강도는 라이브 키로 왕복해야
  확정된다.
- **동적 검증을 하지 않았다.** 위 P0 두 건(1.1·1.2)은 코드 경로로는 확실하나, 실제
  익스플로잇은 실행하지 않았다. 고치기 전에 재현 테스트를 먼저 쓰는 편이 안전하다.
- **`vinc.c.account-policy`(AP-1)의 구현은 전량 미착수 상태**라 팀 관련 결함(1.7·2.3)은
  아직 사용자에게 노출되지 않았을 수 있다. `TEAMS_ENABLED` 플래그 상태를 확인할 것.
- 데스크탑 앱의 Rust 층과 프론트 번들은 표면만 봤다.
