# PLAN — 사용자 ID(username) 도입

계정에 이메일과 별개로 **사용자가 고른 공개 핸들(username)** 을 도입한다. 1차 목적은
커뮤니티 갤러리에 **게시자 ID 표기**([[vinc-community-sharing]] 추후작업 #1)이고, 계정
프로필·멘션 등으로 확장 가능. 작성 2026-07-20.
**상태(2026-07-21): Phase A/B/C/D 전부 완료·배포됨** — 아래 §7 참조. 헤더의 "미구현"은
작성 시점 표기였다.

## 0. 결론(설계 불변식)

**username 은 표시용 핸들이며, 소유권/격리 키가 아니다. owner 는 지금처럼 `user:{public_id}`
(UUIDv7 고정)를 유지한다.** 코드 근거:

- 호스티드 그래프 owner = `user:{public_id}`, 세션 서명 `sub`에서만 강제 주입, 클라/바디 owner
  불신 — `account/src/routes/graph.ts:2-3`, `services/mcpProxy.ts:5-7`, MCP `http_server.py:244-246`.
- 격리 안전성이 **UUID 비재사용**에 명시적으로 의존 — MCP `http_server.py:205-206`.
- 삭제 캐스케이드가 email→public_id→`user:{public_id}`로 그래프 특정 — `repos/users.ts:38-48`,
  `services/mcpPurge.ts:32-38`.
- 전 노드가 `owner_id`(=public_id)로 스탬핑·쿼리 — `query.py:81,328,435`, `sync.py`.

→ username 을 owner 로 쓰면 변경/양도/재사용 시 SEC-1/SEC-3 교차테넌트 격리와 "지웠다는데 안
지워짐" 프라이버시가 즉시 깨진다. **username 은 `public_id`를 절대 대체하지 않는다.**

준비된 배선(추가 작업 적음):
- `users.name VARCHAR(120) NULL` 컬럼 **이미 존재**(`001_users.sql`)하나 어디서도 설정 안 함.
- `/api/me`·세션(`loadForAuth`)·verify 응답이 **이미 name 을 실어 나름**.
- 없는 것: **설정/변경 엔드포인트 + 정책 검증 + 유일성 + 프론트 입력 UI + 커뮤니티 조인**.

## 1. 정책

- **형식**: `^[a-z][a-z0-9_]{2,19}$` — 3~20자, 소문자로 시작, 소문자 영숫자+언더스코어만.
  `,` `.` `-` · 공백 · 기타 기호 · 유니코드 · 대문자 **불가**. (연속/말미 언더스코어 금지는 옵션.)
- **대소문자**: 소문자 강제 저장·표시(대소문자 무시 유일성 자동 성립).
- **유일성**: `uq_users_username` UNIQUE(대소문자 무시 ai_ci). 기존 계정은 NULL 허용(다수 NULL 가능).
- **예약어 차단**: admin, administrator, root, support, help, api, mcp, www, vinc, vincs,
  official, staff, mod, moderator, system, about, account, login, signup, settings, null,
  undefined, me, you, anonymous, deleted. (blocklist, 소문자 비교)
- **변경**: 허용 + **30일 쿨다운**(핸들 스쿼팅·잦은 교체 방지), 구 핸들은 즉시 해제(예약 안 함). ✅확정
- **필수/선택**: 첫 로그인 시 **입력 유도(프롬프트)하되 "나중에" 스킵 가능**, 단 **커뮤니티 게시
  전에는 필수**(게시자 표기용). ✅확정

## 2. DB — 마이그레이션 009

```sql
-- migrate:up  (009_user_username)
ALTER TABLE users
  ADD COLUMN username VARCHAR(20) NULL AFTER email,
  ADD COLUMN username_changed_at DATETIME(3) NULL AFTER username,   -- 쿨다운용(옵션)
  ADD UNIQUE KEY uq_users_username (username);
-- migrate:down
ALTER TABLE users DROP COLUMN username, DROP COLUMN username_changed_at,
  DROP KEY uq_users_username;
```

적용: 서버에 dbmate 미설치 → 앱 유저 `vinc`(ALL PRIVILEGES + role_ddl)가 커밋된 up SQL 을
mysql2 로 실행 + `schema_migrations '009'` 기록(008 커뮤니티와 동일 방식, [[vinc-community-sharing]]).

> `name`(free-form 120)은 그대로 두고 **별도 `username` 컬럼** 신설 — 의미(표시명 vs 고유핸들)
> 를 분리. 향후 자유 표시명이 필요하면 `name` 재활용.

## 3. 백엔드 (account, Node+Hono)

- `lib/username.ts`: `normalizeUsername(raw)`(trim+소문자), `validateUsername(u)`(regex+길이+예약어)
  → `{ ok, error }`.
- `repos/users.ts`:
  - `getByUsername(username)` — 유일성/충돌 조회.
  - `setUsername(userId, username)` — `UPDATE users SET username=?, username_changed_at=NOW()
    WHERE id=?`; `ER_DUP_ENTRY(1062)` → `taken`. 쿨다운 검사(옵션).
- 라우트(`routes/account.ts` 또는 신설 `routes/username.ts`):
  - `GET /api/username/check?u=<h>` → `{ valid, available, reason? }`. 미인증 허용하되
    **rate-limit**(스크래핑 억제, `lib/ratelimit`). 열거는 원래 공개 핸들이라 위험도 낮음.
  - `POST /api/account/username`(requireAuth+requireCsrf) → validate → setUsername →
    성공 `{ username }` / 정책위반 400 / 중복 409 / 쿨다운 429. `audit("account.username")`.
- `routes/me.ts`: 화이트리스트에 `username` 추가(있으면). `public_id`는 계속 **비노출** 유지
  (`me.ts:1` "화이트리스트만" 경계 준수).
- verify 응답/`/api/me`로 프론트가 `username===null` 판정 → 입력 스텝 노출.

## 4. 프론트(site) 플로우

- **첫 로그인 후 핸들 선택**: verify 성공 → `/api/me`에 username 없으면 "핸들 정하기" 화면
  (신규 `/account/username` 또는 로그인 성공 스텝). 실시간 가용성 체크(debounced
  `GET /api/username/check`), 정책 힌트, 제출 → `POST /api/account/username` → `/account` 또는 `/app`.
  "나중에" 스킵 링크(정책 D1 따라).
- **계정 페이지**(`/account/`): username 표시(아바타 이니셜을 email→username 로), "핸들 변경"
  컨트롤(가용성 체크 + 쿨다운 안내). 현재 email/plan/keys/newsletter/delete 옆에 배치.
- **커뮤니티 게시 전 가드**(웹앱 Packs→공유): username 없으면 먼저 설정 유도.

## 5. 데스크탑 영향 (최소 — 표시 전용)

owner/sync/MCP 는 **public_id 기반이라 무변경**. username 은 순수 표시:
- 현 저장: `vinc.sync.key`+`vinc.sync.email`만(`settings.ts:146-148`), public_id 미저장.
  UI Account 탭은 email 표기(`settings.ts:109-111`).
- (옵션·후순위) connect 승인 페이로드(`connect.ts:50-66`)나 sync 응답에 username 을 실어
  설정 화면에 "@username"으로 표기. **핵심 아님** — 커뮤니티 표기와 무관하게 나중에.
- 데스크탑 스키마·격리·쿼터·삭제 경로는 이 계획으로 **일절 바뀌지 않음**.

## 6. 커뮤니티 연계 (1차 목적)

- `repos/communityPacks.ts` `listGallery`: `JOIN users u ON u.id = cp.user_id`, `SELECT u.username`
  → 갤러리 응답에 `authorUsername` 포함(**공개 핸들만**, email·public_id 불노출).
- `site/community/index.html` 타일: `◆ {dominantDomain} · @{authorUsername}` 표기. username
  NULL(구 게시자)이면 생략 또는 "anonymous".
- 게시 시 username 필수(정책 D1)면 이후 게시물은 전부 핸들 보유.

## 7. 단계(rollout) — 진행 상태

- **A. DB+백엔드 ✅완료·배포(2026-07-20)**: 009 적용(username·username_changed_at, uq_users_username),
  `lib/username.ts`(정규식+예약어), `repos/users`(isUsernameAvailable/setUsername, ER_DUP_ENTRY→taken,
  쿨다운), `GET /api/username/check`, `POST /api/account/username`(bad_request/username_taken 409/429),
  `/api/me`·세션·User/AuthUser 에 username. 라이브 검증: check 4케이스·E2E(유일성/쿨다운/갤러리) 통과.
- **B. 프론트 ✅완료·배포**: `site/account/index.html` — Username 카드(입력+실시간 가용성체크+저장),
  프로필 @핸들·아바타 이니셜 반영, EN/KO. 핸들 없으면 카드 강조(첫 로그인 유도). 리다이렉트로 첫 로그인 자연 진입.
- **C. 커뮤니티 표기 ✅완료·배포(목적)**: `communityPacks.list` users JOIN → `authorUsername`,
  `site/community/index.html` 타일 `by @핸들` + 검색 포함. E2E 로 authorUsername 반환 확인.
- **D-web. 웹앱 계정 표시 ✅완료·배포(2026-07-20)**: `Vinc/app` 웹모드(vincs.app/app) 설정→Account
  탭에 `/api/me` 로 **"로그인: @username"**(없으면 이메일) + 계정관리 링크. `settings.ts renderAccount`
  웹 분기(카피 인라인, 셸 편집 충돌 회피). 데스크탑 셸(WEB=false)엔 미영향.
- **D-desktop. 데스크탑 셸 ✅완료·배포(v0.5.2, 2026-07-21 발행)**: 실제 체인은
  connect.ts(CRA/AA, MCP용)가 아니라 **디바이스 플로우**(사이드카 메일박스)였다 — 데스크탑
  `settings.ts startConnect()` → 사이드카 `/connect/start`(로컬 8787) → 시스템 브라우저
  `vincs.app/connect-app/` → `/api/me`+`POST /api/account/keys` → 로컬 사이드카
  `/connect/callback`(무인증, state 단일사용) → 데스크탑 `/connect/status` 폴링 수령.
  - `Vinc/api/server.py`: `_connect` 메일박스에 `username` 필드 추가(`connect_callback`
    저장·`read_connect_status` 반환). 테스트 2건 추가(`tests/test_server.py` — 왕복·미포함
    폴백), **28/28 통과**.
  - `Vinc-homepage/site/connect-app/index.html`: `/api/me`의 `username` 읽어 화면 표시 +
    로컬 콜백 POST body에 포함. **라이브 배포됨**(vincs.app/connect-app, 200).
  - `Vinc/app/src/surfaces/settings.ts`: `drawAcct()`가 `vinc.sync.username`(신설 키) 우선
    표시(`@handle` > 이메일 > 키 앞부분), 연결성공 시 저장, 해제 시 삭제.
  - tsc 통과. **owner/AA sub 는 무변경**(여전히 public_id) — username 은 이 체인에서도 순수 표시.
  - ⚠ **배포 성격 차이**: connect-app 페이지는 웹 콘텐츠라 즉시 라이브 반영됨. 그러나
    `api/server.py`(사이드카)·`Vinc/app`(Tauri 프런트)는 **설치된 데스크탑 앱 안에 번들**되므로
    서버 배포로 안 닿는다 — 실제 사용자에게 가려면 **새 버전 태그+릴리스(build→draft→Publish)**
    가 필요하다([[vinc-live-server]] 릴리스 절차 참조). 그때까지는 기존 설치본에 미반영.

각 단계 독립 배포됨(사이트=no-cache 즉시, 계정=재빌드+재기동, DB=009 1회 적용).

## 8. 확정된 결정 (2026-07-20, 발주자 승인)

- **D1 필수/선택 → 프롬프트+스킵, 커뮤니티 게시 전 필수.**
- **D2 변경정책 → 변경 허용 + 30일 쿨다운 + 구 핸들 즉시 해제.**
- **D3 형식 → `^[a-z][a-z0-9_]{2,19}$` (소문자 영숫자+언더스코어, 3~20자, `,.-`·점·기호 불가).**
- **D4 표시명 → v1은 소문자 핸들만. 자유 표시명(`name` 재활용)은 후속.**

착수 순서: §7 A(DB 009 + 백엔드) → B(프론트 입력/변경) → C(커뮤니티 @핸들 표기) → D(옵션 데스크탑).
