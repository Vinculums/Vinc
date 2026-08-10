# 웹 계정 + 이메일 백엔드 구축 계획서 (매직링크 · Cloud DB for MySQL · Resend)

> ✅ **완료 — 2026-07 프로덕션 라이브.** 본 계획서는 빌드 당시 기록으로 보존한다.
> **as-built 정본 = [[vinc.c.web-vincs-app]]**(`vinc/WEB-vincs-app.md`).
> 아래 본문의 "현재 상태: 백엔드 코드/DB/DNS는 0에서 시작"과 전제표의 미확인·미생성·충돌·미결정은
> **전부 해소됐다.** 계획 대비 실제와 다른 확정 사항:
> - **DB 스키마·유저:** 계획의 `vinc_account` 스키마 + 최소권한 2종(`vinc_acct_app`/`ddl`)은
>   관리형 DB 제약(CREATE DATABASE/USER 불가)으로 **미채택** → 기존 `vinc` DB·계정 직접 사용.
> - **MySQL 버전:** 계획 8.0 → 실제 **8.4**. · **발신 주소:** 계획 `vincs@` → 실제 **`news@vincs.app`**.
> - **리포:** 계획의 별도 `Vinc-account` → 실제 **`Vinc-homepage`** 안의 `account/` 서브디렉토리.
> - 본문이 인용하는 `Vinc/site/*` 경로는 **분리 이전의 구본**이다 → 정본 = `Vinc-homepage/site/`.

> **목표:** `vincs.app`의 선택적 웹 계정(패스워드리스 매직링크) + 뉴스레터(더블 옵트인) 백엔드를, 오프라인 데스크톱 앱과 **물리적·인증적으로 완전 분리된** 상태로 프로덕션에 올린다. **현재 상태:** 스펙·UI 완료(SSoT = `D:/Projects/AIProjects/vinc-project/Vinc/emails/ARCHITECTURE.md`, `/login`·`/account` 정적 페이지 구현됨), **백엔드 코드/DB/DNS는 0에서 시작**. 이 트랙이 남은 작업 중 가장 큼.

이 계획서는 아래 4개 소스에 근거한다. 모든 단계는 스펙 조항으로 역참조한다.
- SSoT: `D:/Projects/AIProjects/vinc-project/Vinc/emails/ARCHITECTURE.md` (이하 **ARCH §n**)
- `D:/Projects/AIProjects/vinc-project/Vinc/emails/README.md` (이하 **README**)
- `D:/Projects/AIProjects/vinc-project/Vinc/site/login/index.html`
- `D:/Projects/AIProjects/vinc-project/Vinc/site/account/index.html`

**절대 불변식(어느 단계도 위반 금지):** ① 데스크톱 앱은 계정 API를 절대 호출하지 않고 오프라인으로 완전 동작(ARCH B1) · ② 계정 API는 로컬 vault·sidecar(`:8787`)·mcp(`:8790`)를 절대 읽지 않고 시크릿을 공유하지 않음(ARCH B2) · ③ 명시적 `.vincpack` 업로드 외 어떤 조용한 동기화도 없음(ARCH B3).

---

## 전제·의존 (preconditions/dependencies)

| # | 전제 항목 | 상태 | 시작 차단 여부 |
|---|---|---|---|
| P1 | 클라우드 콘솔 접근권(관리형 **Cloud DB for MySQL** 프로비저닝 · VPC/ACG 설정). ARCH §4.7의 `ACG`·`10.%`·VPC 사설망 용어상 **NCP/NHN 계열** 관리형 MySQL로 추정 → owner 확인 필요 | 미확인 | **DB 트랙(1·2) 차단** |
| P2 | VM이 DB와 **동일 VPC 사설 서브넷**에 있고 사설 IP(`10.x`)를 가짐. 공개 IP `1.201.120.125`는 SSH·Caddy용, DB 연결은 사설망 전용 | 미확인 | **차단** |
| P3 | Gabia DNS 관리 접근권(SPF/DKIM/DMARC TXT·CNAME 추가). Resend 검증 레코드 입력에 필요(ARCH §6, README 27행) | 미확인 | **Resend 트랙(4) 차단** |
| P4 | Resend 계정 + **send-only API 키** + 도메인 `vincs.app` 추가 권한 + 웹훅 서명 시크릿(Svix) | 미생성 | **트랙 4 차단** |
| P5 | VM 시크릿: `server_pepper`(32B), 세션/CSRF 시크릿, DB 앱/DDL 계정 비밀번호 — CSPRNG 생성 후 systemd `EnvironmentFile`(0600, 전용 OS 유저 소유)에 저장(ARCH §7, §4.7). 리포지토리·프로세스 인자에 절대 노출 금지 | 미생성 | 트랙 3 차단 |
| P6 | 계정 서비스 전용 **OS 유저**(`vinc-acct`, `vinc`/`vinc-sidecar`/`vinc-mcp`와 분리) 생성 권한(passwordless sudo 보유 확인됨) | 준비 | — |
| P7 | 발신 주소 확정: ARCH §6은 `Vinc <news@vincs.app>` 명시, 반면 태스크/메모리는 `vincs@vincs.app` 검증 메일함 존재 언급 → **충돌**. 착수 전 owner 결정(결정 D1) | **충돌** | 트랙 4 착수 전 |
| P8 | 계정 서비스 배치 위치 결정: 앱 소스 리포(`Vinc`, 사설)에 넣으면 CI 앱-격리 grep(ARCH §1.3)과 혼선 → **별도 사설 리포 `Vinc-account` 권장**(결정 D3) | 미결정 | 트랙 3 착수 전 |

**의존 그래프:** P1·P2 → 트랙1 → 트랙2 → 트랙3(API 코어) → 트랙5(레이트리밋·퍼지·모니터링). 트랙4(Resend/DNS)는 트랙3과 병렬 착수 가능하나, `/auth/request`가 실제 메일을 보내려면 트랙4의 도메인 검증 완료가 선행돼야 E2E가 성립.

---

## 단계 (phased tasks)

owner 표기: **C** = Claude가 산출(코드·설정·마이그레이션·테스트·문서), **O** = owner가 수행(콘솔·DNS·시크릿·크리덴셜·승인).

### 트랙 0 — 시크릿·전제 준비 (착수 게이트)

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 0-1 클라우드/VPC/사설IP·Gabia·Resend 접근 확인 | **O** | 3개 콘솔 로그인 성공 스크린샷, VM 사설 IP(`10.x`) 확인 | P1–P4 |
| 0-2 시크릿 생성(`server_pepper`, session/csrf, DB 2계정 pw) | **O**(C가 생성 명령 제공) | `openssl rand -base64 32` × N, `/etc/vinc-account/env`(0600, `vinc-acct` 소유)에 기입 | ARCH §7, §4.7 |
| 0-3 전용 OS 유저 `vinc-acct` 생성(로그인 셸 없음) | **C**(스크립트) → **O**(실행) | `id vinc-acct` 존재, `vinc`/`vinc-sidecar`/`vinc-mcp`와 그룹 분리 | ARCH §2, Appx A #4 |
| 0-4 발신주소·리포위치·런타임·마이그레이션툴 결정 확정 | **O** | 결정 D1–D5 확정 표 서명 | 아래 결정 절 |

### 트랙 1 — Cloud DB for MySQL 프로비저닝 (태스크 a) → ARCH §2, §4.7

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 1-1 관리형 MySQL 8.0 인스턴스 생성(동일 VPC **사설 서브넷**, **공개 엔드포인트 없음**) | **O** | 사설 IP만 노출, 인터넷에서 `3306` 접속 거부 확인 | ARCH §2, §4.7 |
| 1-2 파라미터 그룹 `require_secure_transport = ON` | **O** | `SHOW VARIABLES LIKE 'require_secure_transport'` = ON | ARCH §4.7, §2 |
| 1-3 ACG(방화벽): `3306`을 **VM 사설 IP에서만** 허용 | **O** | 타 호스트 접속 타임아웃, VM에서만 성공 | ARCH §4.7 |
| 1-4 스키마 생성 `vinc_account`(`utf8mb4_0900_ai_ci`) | **C**(DDL) → **O**(관리자 1회 실행) | `SHOW CREATE DATABASE vinc_account` 콜레이션 일치 | ARCH §4 상단 |
| 1-5 최소권한 계정 2종 생성: `vinc_acct_app`(DML만, `'10.%'`, REQUIRE SSL) / `vinc_acct_ddl`(DDL, 배포 파이프라인 전용) | **C**(GRANT 스크립트) → **O**(실행) | app 계정으로 `CREATE TABLE` 시도 시 거부, sidecar/mcp 스키마에 GRANT 0건 | ARCH §4.7, Appx B #12 |
| 1-6 서버 TLS CA 인증서 확보 → VM에 배치(드라이버 `ssl.ca`로 검증 강제) | **O**+**C** | app 계정이 `--ssl-mode=VERIFY_IDENTITY` 상당으로 연결 성공 | ARCH §4.7 |

**완료 게이트(1):** VM에서 사설 IP·SSL로 `vinc_acct_app` 연결 성공 & 외부·평문 연결 모두 거부 & app 계정 DDL 거부.

### 트랙 2 — 스키마 마이그레이션 (태스크 b) → ARCH §4.1–4.8

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 2-1 forward-only 마이그레이션 툴 도입(**dbmate 권장**: 순수 SQL·단일 바이너리·ORM 오토싱크 없음) | **C** | `db/migrations/` 구조 + `schema_migrations` 추적 확립 | ARCH §4.7 말미, §4.6 |
| 2-2 `001_users.sql` (§4.1: `public_id` UUIDv7, `uq_users_email` ai_ci 대소문자 무시) | **C** | 테이블·2 유니크키 생성, 대/소문자 이메일 중복 삽입 실패 | ARCH §4.1, Appx B #8·9 |
| 2-3 `002_auth_tokens.sql` (§4.2: **users FK 없음**, `token_hash`/`challenge_hash` BINARY(32), `purpose` ENUM, 3 인덱스) | **C** | 스키마 일치, `ix_auth_tokens_email_created`·`ix_auth_tokens_expires` 존재 | ARCH §4.2, Appx B #9 |
| 2-4 `003_sessions.sql` (§4.3: PK=`token_hash`, `fk_sessions_user` CASCADE) | **C** | user 삭제 시 세션 CASCADE 동작 확인 | ARCH §4.3, §3.4 |
| 2-5 `004_subscribers.sql` (§4.4: `status` ENUM, `unsub_token` CHAR(43) 사용가능 저장, `ix_subscribers_pending_cleanup`) | **C** | 스키마 일치, 뉴스레터 SSoT 테이블 확정 | ARCH §4.4, Appx B #8 |
| 2-6 (지연) `subscriptions`·`shared_packs`는 **마이그레이션 보류**(Pro/공유 런칭 시 forward-only 추가). 지금은 리뷰용으로만 스펙 보존 | **C** | 미적용 사유 주석화(결정 D6) | ARCH §4.5·4.6, §10 |
| 2-7 배포 파이프라인이 **`vinc_acct_ddl`로만** 마이그레이션 적용(프로덕션 대상 destructive diff 금지) | **C** | CI 로그에 DDL 유저로 적용, 롤백 아닌 forward-only 확인 | ARCH §4.7 |

**완료 게이트(2):** 4개 코어 테이블·인덱스 존재, ai_ci 대소문자 무시 identity 검증, app 유저 DML-only 재확인.

### 트랙 3 — 계정 API 서비스 (태스크 c) → ARCH §3, §5, §7

**런타임·구조(결정 D2·D3):** 기본값 = **Node 22 LTS + Hono**(보안 장수 서비스에 생태계 성숙도 우선; Bun은 옵션), `mysql2` 풀(5–10, keepalive/validation, ARCH §2), 전용 리포 `Vinc-account`, 루프백 **`127.0.0.1:8080`**(8787/8790 회피). systemd 유닛은 `vinc-acct` 유저·`EnvironmentFile=/etc/vinc-account/env`(0600), `NoNewPrivileges`·`ProtectSystem=strict`·`PrivateTmp` 강화.

#### 3-공통 인프라

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 3-0a systemd 유닛 + `EnvironmentFile` 로더(시크릿을 인자 아닌 env로) | **C** | `systemctl status vinc-account` active, `ps` 인자에 시크릿 0 | ARCH §7, §2 |
| 3-0b Caddy `reverse_proxy /api/* 127.0.0.1:8080`(**api.vincs.app 없음**) + 정적 인터스티셜 라우팅 | **C** | `vincs.app/api/*`만 프록시, 동일 오리진 first-party | ARCH §2, Appx B #1 |
| 3-0c 보안 헤더(Caddy): HSTS(단, `includeSubDomains`/`preload`는 **보류** — 모든 서브도메인 HTTPS 후), CSP(`frame-ancestors 'none'`, 서드파티 오리진 0), `X-Content-Type-Options`, `Referrer-Policy: strict-origin-when-cross-origin` 전역 + `/auth/*`엔 `no-referrer`; `/auth/*` 액세스로그 **쿼리스트링 제거** | **C** | 헤더 스냅샷 테스트 통과 | ARCH §7, §10, §1.3 |
| 3-0d 토큰 유틸: 256-bit CSPRNG base64url, 저장은 `HMAC-SHA256(pepper, token)`만, 비교는 `crypto.timingSafeEqual` | **C** | 단위테스트: 원문 토큰 DB 미저장, 타이밍-세이프 비교 | ARCH §3.1, Appx B #3 |
| 3-0e 공통 미들웨어: Origin/Referer allowlist·`Content-Type: application/json` 강제(웹훅/one-click 예외), 에러 봉투 `{error:{code,message,request_id}}` | **C** | CSRF/오리진 위반 시 `403`, 봉투 포맷 일치 | ARCH §5.0, §7 |

#### 3a — 매직링크 request/verify (코어)

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 3a-1 `POST /api/auth/request` — 이메일 **syntax만** 검증, 유저 생성 안 함, 15분 토큰 발급 + `__Host-lc` 챌린지 쿠키 세팅, prior 미사용 토큰 무효화, 메일 **비동기 enqueue** | **C** | 존재/스로틀 무관 **동일 200**, `Set-Cookie __Host-lc`, per-email 1/60s·5/h·per-IP 20/h | ARCH §5.1, §3.1, §3.3, Appx B #9·11 |
| 3a-2 `GET /auth/verify` **정적 인터스티셜**(상태변경 0, 스캐너 프리페치 생존), JS가 `#token` 읽고 `history.replaceState`로 제거, 명시 클릭에만 POST | **C** | 정적 200 text/html, `Referrer-Policy: no-referrer`, 서드파티 애셋 0 | ARCH §3.2, §5.2, Appx B #4 |
| 3a-3 `POST /api/auth/verify` — **원자적 single-use consume**(`ROW_COUNT()=1`만 진행), 챌린지 대조: 일치→자동 진행, 부재/불일치→**confirm-context** 페이로드(요청시각+대략 IP/위치) 반환(하드 실패 아님), 이메일 정규화 find-or-create, 세션 **로그인마다 회전** | **C** | 재사용/더블서브밋 차단 스냅샷 통과, 크로스디바이스는 confirm 화면으로 | ARCH §3.1–3.4, §5.3, Appx B #6·10 |
| 3a-4 세션 발급: `__Host-session`(HttpOnly·Secure·SameSite=Lax·Path=/·no Domain), rolling 30d idle·absolute 90d, `HMAC(pepper,id)`만 저장; `__Host-csrf`(JS-readable) 세팅 | **C** | 쿠키 플래그 assert 통과, `__Host-` 프리픽스 검증 | ARCH §3.4, §7, Appx B #2·7 |
| 3a-5 `/verify` 인터스티셜 페이지 신규 구축(states: verifying·success→`/account`·expired-or-used·invalid·**confirm-context**), EN/KO 카피 그대로, Ink&Paper 재사용 | **C** | ARCH §8.8 카피 일치, 기존 `login/index.html` 스타일 상속 | ARCH §8.1·8.8 |

#### 3b — 세션 소비 엔드포인트

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 3b-1 `GET /api/me`(인증) — **화이트리스트** `{email,name?,plan,newsletter,created_at}`만(디바이스/vault 유래 0) | **C** | 응답 키 화이트리스트 초과 0, 미인증 401 | ARCH §5.5, B5, Appx B #15 |
| 3b-2 `POST /api/auth/logout`(인증·CSRF) — 세션 row DELETE·쿠키 clear | **C** | 204, row 즉시 무효화, CSRF 실패 403 | ARCH §5.4, §3.4 |
| 3b-3 `account/index.html` 배선: `/api/me` 로드, 미인증 시 `/login` 리다이렉트 | **C** | 정적 목업 → 실데이터, boundary 배너 유지 | ARCH §8.2·8.9 |

#### 3c — 뉴스레터 더블 옵트인

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 3c-1 `POST /api/subscribe` — honeypot `hp`, upsert `pending`, 24h confirm 토큰, per-email 3/h·per-IP 20/h, 비동기 enqueue | **C** | 존재/기존확인 무관 동일 200, 봇 필드 채워지면 무음 드롭 | ARCH §5.6, §5.0 |
| 3c-2 `GET /subscribe/confirm` 정적 인터스티셜(프래그먼트 토큰) + `POST /api/subscribe/confirm` — 원자적 `confirmed_at` 세팅, `unsub_token` 생성, `welcome` enqueue | **C** | confirmed 200, 만료/무효 400, 인터스티셜 신규 구축 | ARCH §5.7, §8.4 |
| 3c-3 홈 푸터/`/news` 구독 블록에 **honeypot 필드 추가**(현재 폼에 없음) + enumeration-parity 카피(ARCH §8.3) | **C** | 봇 필드 존재, "받은 편지함 확인" 카피 일치 | ARCH §5.0, §8.3 |
| 3c-4 `/account` "릴리스 소식" 토글 → **subscribers row** 읽기/쓰기(users 불리언 금지), 매직링크 유저 ON 시 `confirmed_at` 즉시(2차 옵트인 없음), optimistic-with-rollback | **C** | 토글이 SSoT row만 갱신, 로그인마다 상태 reconcile | ARCH §8.2, §4.4, Appx B #8 |

#### 3d — 원클릭 구독해지 (RFC 8058)

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 3d-1 `POST /api/unsubscribe?token=` — 미인증·멱등, `unsub_token`은 저감도 bearer라 쿼리스트링 허용(RFC 8058) | **C** | 200 unsubscribed, 무효 400, 재호출 멱등 | ARCH §5.8, Appx B #4·14 |
| 3d-2 `GET /unsubscribe?token=` 정적 "해지 확인" 인터스티셜(POST에서만 실제 해지 → 스캐너 프리페치가 조용히 해지 못함) | **C** | 신규 구축, resubscribe 오퍼 포함(ARCH §8.5) | ARCH §5.8, §8.1·8.5 |
| 3d-3 마케팅 발송에 `List-Unsubscribe` + `List-Unsubscribe-Post: List-Unsubscribe=One-Click` 헤더 | **C** | 실제 발송 헤더에 2 주소(https·mailto) 존재 | ARCH §5.8 |

#### 3e — 계정 삭제 (2단계·비가역)

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 3e-1 `POST /api/account/delete`(인증·CSRF) — 15m delete-confirm 토큰, `account_delete_confirm` enqueue | **C** | 202 confirmation_sent, 미인증 401·CSRF 403 | ARCH §5.9, Appx B #10 |
| 3e-2 `POST /api/account/delete/confirm {token}` — 단일 트랜잭션 하드삭제(provider cancel 선행→`auth_tokens`/`users` CASCADE/`subscribers`), Resend identity purge, 세션 clear. **vault 절대 미접촉** | **C** | 200 deleted, 트랜잭션 원자성 테스트, soft-delete/tombstone 0 | ARCH §5.9, §9, Appx B #10 |
| 3e-3 `/account` 삭제 다이얼로그 배선(로컬 데이터 무영향 안내 유지) | **C** | ARCH §8.9 카피 일치, 확인 다이얼로그 후에만 실행 | ARCH §8.2·8.9 |

#### 3f — Resend 웹훅 수신 (트랙4와 접점)

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 3f-1 `POST /api/webhooks/resend` — Svix `svix-id/timestamp/signature` 검증, 미서명/무효 400 | **C** | 서명 검증 실패 시 거부 스냅샷 | ARCH §5.10 |
| 3f-2 suppression 테이블(신규 마이그레이션) + `bounced`/`complained` 반영, 발송 전 항상 조회 | **C** | 하드바운스→suppress, complaint→subscriber `complained/unsubscribed` | ARCH §5.10, §6 |

**완료 게이트(3):** ARCH §1.3 보안 스냅샷 6종(토큰 single-use 레이스·만료 경계·enumeration 타이밍 패리티·쿠키 플래그·오픈리다이렉트 거부·헤더 스냅샷) 전부 통과 + 전 엔드포인트 계약 테스트 그린.

### 트랙 4 — Resend 통합 (태스크 d) → ARCH §6

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 4-1 Resend에 `vincs.app` 도메인 추가 → SPF/DKIM/DMARC 레코드 획득 | **O** | Resend 대시보드에 레코드 값 표시 | ARCH §6, README 27행 |
| 4-2 **Gabia DNS**에 레코드 입력: SPF TXT, DKIM CNAME(들), **DMARC TXT `p=reject`** (로그인 메일 스푸핑 차단) | **O** | Resend "Verified", `dig` 상 3레코드 확인 | ARCH §6, Appx B #15 |
| 4-3 발신 아이덴티티 확정(D1) + **transactional/marketing 스트림 분리**(마케팅 컴플레인이 인증메일을 막지 못하게) | **C**+**O** | 2 스트림 분리 구성, magic_link=transactional | ARCH §6 |
| 4-4 템플릿: 기존 `welcome.html`·`update.html`(README 표) 재사용 + **신규 `magic_link`·`subscribe_confirm`·`account_delete_confirm`** 생성(ARCH §8.7/§8.4/§8.9 카피, merge tag 매핑, **외부 이미지·트래킹 픽셀 0**) | **C** | EN/KO 카피 일치, 본문에 **오파크 토큰 링크만**(vault/앱 데이터 0) | ARCH §6, §8.4·8.7·8.9, README 33–41행 |
| 4-5 멱등: 매 발송 `Idempotency-Key="<type>:<tokenId>"`; 재시도 3회 지수백오프; 총실패도 **호출자엔 동일 200**(enumeration 오라클 방지) | **C** | 재시도가 이중발송 0, 실패 시 로그+재큐 | ARCH §6, §5.1 |
| 4-6 send-only 키·웹훅 서명 시크릿을 `EnvironmentFile`에 배치 | **O** | 키 노출 0, 트랙 3f와 연동 | ARCH §7 |

**완료 게이트(4):** DMARC 정렬 통과(합법 발송 pass) + 스푸핑 발송 reject + 4종 트랜잭션 메일 테스트 수신(EN/KO) + Litmus/Email-on-Acid 프리뷰(README 41행).

### 트랙 5 — 레이트리밋·허니팟·퍼지 크론·모니터링 (태스크 e) → ARCH §5, §7, §4.8

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 5-1 레이어드 레이트리밋(결정 D4: **인프로세스 토큰버킷 + auth_tokens 카운트 조회** 조합, Redis 미도입): `/auth/request` 1/60s·5/h per-email·20/h per-IP, `/auth/verify` 30/h per-IP, `/subscribe` 3/h per-email·20/h per-IP | **C** | 초과 시 uniform-200 무음 드롭(퍼IP 남용만 429), `RateLimit-*` 헤더 | ARCH §5.1·5.3·5.6, §7, Appx B #11 |
| 5-2 **글로벌 일일 발송 상한(fail-closed)** + 상한 접근 시 **운영자 사전 알림**(Appx A #3: 메일밤 DoS 완충) | **C** | 상한 도달 시 발송 차단, 임계 접근 알림 발화 | ARCH §7, Appx A #3 |
| 5-3 허니팟: 공개 폼(구독·필요시 로그인) 히든 필드, 채워지면 무음 처리 | **C** | 봇 제출 200이나 무액션 | ARCH §5.0, §7 |
| 5-4 만료 퍼지: **앱-크론(항상 켜진 서비스 인터벌)**이 3 DELETE 실행(auth_tokens 24h grace·sessions 만료·pending subscribers 7d). EVENT 스케줄러는 파라미터그룹 허용 확인 후 **선택적 가속기로만**(결정 D7) | **C** | 주기 실행 로그, single-use row·PII 미축적 확인 | ARCH §4.8, §9, Appx A #5, Appx B #13 |
| 5-5 모니터링/알림(결정 D8): 발송 실패율·상한 접근·**퍼지 미실행**·서비스 헬스. systemd `Restart=on-failure` + 워치독. 알림 채널은 앱과 무관하게(순환 회피) | **C**+**O** | 서비스 다운·퍼지 정지 시 알림 도달 | ARCH §7, Appx A #5 |
| 5-6 감사 로깅: auth 이벤트(request/verify/logout/delete)를 **해시 식별자+결과**로만, 토큰/시크릿/PII 절대 미기록 | **C** | 로그에 원문 토큰·이메일 평문 0 | ARCH §7 |

### 트랙 6 — 배포 하드닝 & 런칭 검증 → Appx A·B, §1.3

| 작업 | owner | 완료선 | 스펙 |
|---|---|---|---|
| 6-1 **배포시 격리 검증 자동화**(Appx A #4: 단일 오배포가 vault 데이터 경로에 재결합 방지): OS 유저·MySQL GRANT·네트워크 스코프·`EnvironmentFile` 퍼미션 점검 | **C** | 배포 파이프라인 게이트, 위반 시 배포 중단 | ARCH §2, Appx A #4, Appx B #12 |
| 6-2 CI 가드 3종(ARCH §1.3): **앱-격리 grep**(앱 소스가 계정 호스트/`/api/*` 참조 시 빌드 실패), **금칙어 린트**("never connects to internet"/"air-gapped" 등), 보안 스냅샷 | **C** | 앱 리포 CI에 통합, 위반 재현 테스트 | ARCH §1.3, §7, Appx A #6 |
| 6-3 **README 카피 드리프트 수정**: README §C가 아직 "email + password"·"/signup"·"password reset" 명시(29·78행) → ARCH B6(패스워드리스 only)와 **모순**. SSoT 기준으로 정정 | **C** | README가 매직링크·no-password로 정렬 | ARCH §3, B6; README 14·78행 |
| 6-4 오픈리다이렉트 하드닝: 로그인 후 리다이렉트는 고정 `/account`, 리턴패스 필요시 `^/[^/]`만 허용(`//`·스킴 거부) | **C** | 오픈리다이렉트 거부 스냅샷 | ARCH §7 |
| 6-5 go-live 체크리스트 실행(E2E: 구독→확인→welcome, 로그인→verify→account, 해지, 삭제) | **C**+**O** | 전 플로우 프로덕션 스모크 통과 | ARCH §8.1 전 상태 |

---

## 결정 필요 (owner decisions)

| ID | 결정 | 권장 기본값 | 근거 |
|---|---|---|---|
| **D1** | 발신 주소: `news@vincs.app`(ARCH §6) vs `vincs@vincs.app`(검증 메일함 존재) | **`news@` 유지**(스펙·헤더·`unsub@` 관례와 일치), `vincs@`는 reply-to/관리·mailto 해지 창구로 병용 | ARCH §6이 SSoT, §5.8이 `unsub@vincs.app` 언급 |
| **D2** | API 런타임: Node 22 LTS vs Bun | **Node 22 LTS + Hono**(장수 보안 서비스 생태계 성숙); Bun은 성능 옵션 | ARCH는 "Node/Bun" 병기 |
| **D3** | 계정 서비스 코드 위치 | **별도 사설 리포 `Vinc-account`** (앱 리포와 물리 분리 → CI 앱-격리 grep 명료) | ARCH §1.3, Appx A #4 |
| **D4** | 레이트리밋 저장소 | **인프로세스(단일 인스턴스) + MySQL 카운트**(Redis 미도입, 추가 서비스/시크릿 회피). 수평확장 시 재검토 | ARCH §5, KISS |
| **D5** | 마이그레이션 툴 | **dbmate**(순수 SQL·단일 바이너리·오토싱크 없음) | ARCH §4.7 "dbmate/Flyway/Knex" |
| **D6** | `subscriptions`/`shared_packs` 지금 마이그레이션? | **보류**(Pro/공유 런칭 시 forward-only 추가; 지금은 리뷰용 스펙만) | ARCH §10, §4.5·4.6 |
| **D7** | confirm-context(§3.3) 위치정보 소스 | **로컬 GeoLite2 오프라인 조회 또는 IP만 표기**(런타임 외부호출 0 → CSP/무추적 유지) | ARCH §3.3, Appx A #1·2 |
| **D8** | EVENT 스케줄러 사용 | **미사용**(앱-크론이 1차); 파라미터그룹이 `event_scheduler` 허용 확인 시에만 선택 가속기 | ARCH §4.8, Appx B #13 |
| **D9** | 모니터링 알림 채널 | **앱과 무관한 별도 채널**(예: 운영자 개인메일·푸시), Resend 순환 의존 회피 | Appx A #3·5 |

---

## 리스크·함정

1. **동일 오리진 결합(Appx A #1):** API를 `vincs.app/api`로 서빙 → 마케팅/사이트 페이지의 XSS가 `__Host-session`과 오리진 공유. **엄격 CSP·서드파티 애셋 0·HttpOnly 쿠키가 옵션이 아닌 필수 완충**. 현행 `login`/`account`는 인라인 스타일/스크립트(마케팅 CSP 허용)이나 인터스티셜은 반드시 서드파티 0 유지.
2. **크로스디바이스 매직링크(Appx A #2):** 다른 브라우저에서 열면 confirm-context가 유일 방어. 위치정보 부재·부주의 클릭 시 소셜 포워드가 뚫릴 수 있음 → 15분 TTL + "본인이 시작한 경우만" 카피가 보상통제. 하드 409로 되돌리지 말 것(합법 크로스디바이스 파괴).
3. **메일밤 DoS(Appx A #3):** 글로벌 일일 상한 fail-closed → 소진 공격 시 당일 로그인/뉴스레터 불가. per-email(1/60s·5/h)·per-IP가 **먼저** trip하고 상한 접근을 운영자에 사전 알림해야 함.
4. **격리 재결합(Appx A #4):** 서비스/DB/시크릿 격리는 **코드가 아닌 운영**으로 강제(전용 OS 유저·최소권한 MySQL·0600 EnvironmentFile). 단일 오배포로 vault 데이터 경로 재결합 가능 → 6-1 배포시 검증 필수.
5. **퍼지 정지(Appx A #5):** 관리형 MySQL이 흔히 `event_scheduler` 비활성 → 앱-크론 의존. 서비스 다운 시 single-use 토큰 row·PII 축적 → 퍼지 실행 모니터링·알림 필수.
6. **카피 드리프트(Appx A #6):** "인터넷에 절대 연결 안 함"/"완전 에어갭" 같은 절대 주장을 옵셔널 웹계정이 거짓으로 만듦. **claim matrix + 금칙어 CI 린트가 권위**. 현 README §C(패스워드 방식 잔존)도 6-3에서 정정.
7. **enumeration 오라클:** 발송 실패·존재 여부·스로틀이 응답에 새면 안 됨 → 항상 동일 200·비동기 발송·존재 분기 없음(첫 verify 시 유저 생성이 이를 자연스럽게 함).
8. **HSTS preload 조기 적용 금지:** `mcp.vincs.app` 등 전 서브도메인 HTTPS 확인 전 `includeSubDomains`/`preload` 켜면 서브도메인 접근 불능 위험(ARCH §10).

---

## 개략 공수 (rough effort)

| 트랙 | 범위 | 공수(집중 기준) | 대기/의존 |
|---|---|---|---|
| 0 | 시크릿·전제·결정 확정 | **0.5일** | O 콘솔 접근 |
| 1 | Cloud DB 프로비저닝·격리 | **0.5–1일** | O 콘솔(VPC/ACG) |
| 2 | 스키마 마이그레이션(4 코어) | **0.5일** | 트랙1 완료 |
| 3 | 계정 API(3a–3f, 인터스티셜 3종, 페이지 배선) — **최대 트랙** | **6–10일** | 트랙2 완료 |
| 4 | Resend·DNS·템플릿 3종 | **1–2일 작업 + DNS/검증 전파 대기** | O DNS·Resend, 트랙3f 접점 |
| 5 | 레이트리밋·허니팟·퍼지·모니터링 | **1.5–2일** | 트랙3 코어 |
| 6 | 배포 하드닝·CI 가드·런칭 검증 | **1–1.5일** | 전 트랙 |
| — | **합계** | **≈ 2.5–3.5주** | 트랙4는 트랙3과 병렬로 대기시간 흡수 |

**권장 실행 순서:** 0 → 1 → 2 → (3 코어 3a·3b **와** 4 DNS 병렬 착수) → 3c·3d·3e·3f → 5 → 6. DNS/도메인 검증 전파 지연을 트랙3 개발과 겹쳐 크리티컬 패스에서 제거.
