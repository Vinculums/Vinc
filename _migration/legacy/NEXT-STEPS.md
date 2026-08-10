# Vinc — 후속 작업 계획서 (NEXT-STEPS)

> 작성 2026-07-12. 현재까지 작업은 두 레포 `main`에 머지 완료
> (Vinc `c6cbb35`, Vinc-docs `f0fb650`). 이 문서는 그 이후의 남은 일을
> 우선순위·담당·의존·완료선으로 정리한 실행 계획이다.
> 담당 표기: **C** = Claude가 진행 · **O** = 발주자(오너) 직접 액션.
>
> **상세 계획서** (각 트랙 실행안 → [`plans/`](plans/)):
> [웹 계정·이메일](plans/PLAN-web-account-email-backend.md) ·
> [Figma 재개](plans/PLAN-figma-design-resume.md) ·
> [뉴스레터·Sign-in](plans/PLAN-newsletter-signin-ui.md) ·
> [코드 서명·공증](plans/PLAN-code-signing-notarization.md)
>
> **업데이트(2026-07-13):** v0.1.0 게시·다운로드 라이브(§2 완료). 설치관리자(NSIS)
> per-user·한/영 설계 반영. 첫 실행 SmartScreen/Gatekeeper 경고 제거는 코드서명 계획서 참조.
>
> **업데이트(2026-07-16):** 릴리스 **v0.1.6**까지 라이브 · **MCP 커넥터 claude.ai 등록 완료**(07-15,
> §1.2 해소) · 원격 스토어 프로비저닝 픽스(사이드카 v0.1.6 교체) · **웹 계정/이메일 프로덕션 라이브**
> (§4 설계→구현 완료, as-built = [[vinc.c.web-vincs-app]]) · 코드서명 방향 확정(DEC D44).

> **발견(2026-08-01) — `vinc` 명령이 실재하지 않는다 (O 결정 대기):** 문서·README·앱 카피가
> 한결같이 `vinc mcp install` · `vinc search` 처럼 안내하는데, **그 이름의 실행 파일을 얻는 경로가
> 없다.** `pyproject.toml` 에 `[project.scripts]` 가 없고(`py-modules = []` · "PyPI 미배포" 명시),
> 릴리스 산출물은 Tauri 번들 + 그 안의 `vinc-sidecar-<triple>` 뿐이며(`release.yml:98`),
> PATH 등록도 없다. 실제 실행 경로는 둘: 소스에서 `python -m api.cli …`, 설치본에서 동봉된
> 사이드카 실행 파일(`vinc_mcp/install.py:56` `_server_command` 가 그 둘을 그대로 분기한다).
> **조치:** 문서 사이트 `/docs/cli/` 에 §명령 얻기를 신설해 사실대로 적었다(2026-08-01).
> **남은 것:** ① `README.md:128` ② 앱 카피 `copy.ts` `mcpOtherHint`(EN/ko 둘 다) 가 같은 가정을
> 그대로 쓴다. ③ 진짜 해결은 `vinc` 를 배포 가능한 명령으로 만들지(entry point + 패키징 방침
> 변경) 아니면 이름을 현실에 맞출지의 **발주자 결정**이다.

---

## 0. 지금까지 완료 (요약)

- **원격 서버 라이브**(vincs.app / 1.201.120.125): 하드닝·DNS·TLS·사이드카·MCP·홈페이지·백업(HTTP 무중단 스냅샷). 서버 트랙 1·3·4·5·6·8·9 PASS. **MCP 커넥터 등록·왕복 라이브**(07-15) + 원격 스토어 v0.1.6 프로비저닝 픽스.
- **사이트 전면 리디자인**: 홈·다운로드(`/download`)·릴리스(`/release`)·가이드(`/guide`) + `/login`·`/account`. 지구본 언어모달·해달 테마토글·수렴 하프톤 마우스추종 배경·통일 푸터·EN/KO. 전부 라이브.
- **앱 카피** SaaS 톤 개선(문서/주제/데이터/화면/사이드바) + **UI 카피 전수 인벤토리**(`vinc.a.ui-copy` v0.2).
- **Vinc 로고** 정본 + favicon.
- **웹 계정/이메일 설계**: 결정(매직링크 · 관리형 Cloud DB for MySQL · Resend) + 이메일 템플릿(`emails/welcome.html`·`update.html`) + **하드닝 스펙**(`emails/ARCHITECTURE.md`).
- **Figma**: `Marketing`·`App` 페이지 스켈레톤(섹션·케이스별 프레임, 디자인시스템 변수 바인딩) + **Home 히어로** 실제 빌드. 구화면 `Screens` 페이지 삭제.

---

## 1. 발주자 액션 (짧고 블로킹) — 우선순위 ★★★

| # | 항목 | 담당 | 완료선 |
|---|---|---|---|
| 1.1 | 클라우드 **보안그룹** 인바운드 22/80/443만 확인(서버 2단계) | O | 그 외 규칙 없음 |
| ~~1.2~~ | ✅ **완료(2026-07-15)** — claude.ai 커넥터 등록·왕복 확인. URL `https://mcp.vincs.app/mcp`, DCR Client ID·Secret 빈칸, 인가 403 시 `&key=<sudo grep VINC_OAUTH_ADMIN_KEY /etc/vinc/mcp.env>`. ⚠ vinc-mcp 재시작 시 OAuth 세션(인메모리) 초기화 → **재연결 필요** | O | — |
| 1.3 | 이메일용 **DNS(가비아)**: `vincs.app`에 Resend 발급 **SPF·DKIM·DMARC(p=reject)** 레코드 | O | Resend 도메인 verified |
| 1.4 | **Resend 계정** 생성 + `news@vincs.app` 발신 아이덴티티 | O | 테스트 메일 도달 |

---

## 2. 첫 릴리스 (P4) — ✅ 완료 (다운로드 라이브) ☆

> ✅ **해소됨** — 구 서술("`/download` 는 릴리스가 없어 **404**")은 더 이상 사실이 아니다.
> **v0.1.0 → v0.1.6 게시 완료**, 다운로드 라이브(mac dmg · win exe · linux AppImage/deb 전부 200).
> 아래 표는 당시 절차 기록으로 보존한다. 릴리스 게시는 **수동**임에 주의: `release.yml` 은
> `Vincs-app` 에 **draft** 로 발행하므로 사람이 **Publish** 를 눌러야 `latest/download` 가 열린다
> ([[vinc.c.p5-remote-rollout]] §5).

| 단계 | 내용 | 담당 |
|---|---|---|
| 2.1 | `v0.1.0` 태그 푸시 → `release.yml`이 3-OS 설치본 빌드 → **draft 릴리스** 생성 | O(태그) / C(파이프라인 점검) |
| 2.2 | draft 릴리스 **게시(publish)** | O |
| 2.3 | 실제 자산 파일명과 `/download`의 추정 파일명(`Vinc_0.1.0_aarch64.dmg` 등) 일치 확인, 불일치 시 링크 수정 | C |
| 2.4 | `/release` 페이지에 v0.1.0 변경사항 기록(빈 상태 → 실데이터), `update.html`로 첫 뉴스레터 발송 | C(문서)/O(발송) |
| 2.5 | ~~코드사인·공증(P4 잔여)은 시크릿 확보 후 `release.yml`에 주입~~ → **DEC D44 로 방향 확정**: 리포 public 전환 → Windows = SignPath Foundation 무료 OSS 서명 · macOS = 미서명 유지 + 우회 안내. 배선은 후속 | 후속 |

---

## 3. Figma 디자인 빌드 (재개) ★★☆

> 파일: `Vinc — Design System (Ink & Paper)`. `Marketing`/`App` 페이지에 케이스별 프레임 스켈레톤 준비됨. 디자인시스템 `color` 변수(Light/Dark) 바인딩, Space Grotesk/Inter/Noto.

| 순서 | 대상 | 세부 | 담당 |
|---|---|---|---|
| 3.1 | **Home** 마저 채우기 | 앱 목업 → 3단계 → 기능 6 → 프라이버시 → 최종 CTA → 푸터 | C |
| 3.2 | **Download / Release / Guide** 프레임 | 실제 페이지 레이아웃 반영 | C |
| 3.3 | **App 화면**(케이스별) | Intake(Empty/Ingesting/Success)·Records·Atlas(Graph/Detail/Empty)·Search·Packs·Settings | C |
| 3.4 | **Auth 화면** 추가 | Login(입력/전송) · Account · 뉴스레터 가입 | C |
| 3.5 | 라이트/모바일 변형 + `v0.2 As-built` 페이지 정리(신 App 완성 후 제거 검토) | C/O 확인 |

---

## 4. 웹 계정 + 이메일 백엔드 — ✅ 구현·프로덕션 라이브 (as-built) ★★☆

> ✅ **2026-07 프로덕션 라이브** — as-built 종합은 [[vinc.c.web-vincs-app]](`vinc/WEB-vincs-app.md`).
> 아래 단계표는 빌드 당시 계획으로 보존(전 항목 반영됨).
> 정본 스펙: **`emails/ARCHITECTURE.md`**. 오프라인 앱 불변식(앱 무로그인 · API가 볼트 안읽음 · 무단 동기화 없음) 준수가 최상위.

| 단계 | 내용 | 의존 |
|---|---|---|
| 4.1 | 관리형 **Cloud DB for MySQL** 인스턴스 생성(사설망·최소권한 유저·require SSL) | O 계정 |
| 4.2 | 스키마 마이그레이션(ARCH §4 DDL: users·auth_tokens·subscribers·sessions) | 4.1 |
| 4.3 | 계정 API(Node/Bun) — `vincs.app/api` **동일출처**(Caddy loopback), 매직링크·세션·구독·구독취소·삭제 | 4.2, 1.3/1.4 |
| 4.4 | Resend 연동(매직링크·confirm·welcome·update), RFC 8058 원클릭 구독취소 + 웹훅 억제 | 1.3/1.4 |
| 4.5 | sidecar/mcp와 **완전 격리** 배포 검증(OS유저·DB권한·시크릿 0600·네트워크 스코프) | 4.3 |
| 4.6 | 레이트리밋·honeypot·모니터링(만료 퍼지 크론, 발송 상한 알림) | 4.3 |

---

## 5. 뉴스레터 가입 UI + Sign-in 노출 결정 ★★☆

| # | 항목 | 담당 | 메모 |
|---|---|---|---|
| 5.1 | 공홈에 **"Get release news"** 가입 블록(이메일만, "release news, not your data") | C | double opt-in 진입점 |
| 5.2 | **Sign in 노출 위치 결정**: 마케팅 헤더의 "No sign-up, no login"과 충돌 → (푸터만 / `/account` 내부만 / 미노출) | O 결정 | C 반영 |

---

## 6. 정리·기술부채 ☆

| # | 항목 | 담당 |
|---|---|---|
| 6.1 | ~~서버 트랙 첫 ingest 후 **백업 실데이터 왕복** 재확인(현재 빈 DB)~~ → 원격 스토어는 v0.1.6 사이드카 교체로 프로비저닝됨(`/domains` executed=true, 데모 concept 존재). **실데이터 백업 왕복 1회 실측만 잔여** | C |
| 6.2 | `harden.sh` cloud-init 우선순위 수정은 커밋 완료 — 신규 VM 재배포 시 검증 | C |
| 6.3 | `/Release` 대소문자: 현재 `/release`(소문자). 대문자 원하면 리다이렉트/리네임 | O 결정 |
| 6.4 | 앱 창 타이틀·잔여 카피 최종 점검 | C |

---

## 진행 원칙

- 각 항목 **완료선 통과 후** 다음으로. 배포는 `main`에 머지 후 서버 반영·라이브 확인.
- 파괴적/외부 액션(릴리스 게시·DNS·계정 삭제 API)은 발주자 확인 후.
- 오프라인 앱의 "무계정·오프라인" 약속은 어떤 후속 작업에서도 **불가침**.
