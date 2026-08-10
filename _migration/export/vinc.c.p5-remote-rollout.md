<!-- vinc-export
doc_id: vinc.c.p5-remote-rollout
title: P5 실행 계획 — 원격 프로파일 롤아웃
origin: Vinc-docs/vinc/C/vinc.c.p5-remote-rollout.md@5090690
chunks: 5
verified: true
content_hash: ae94bb0fa8d355c1fa88c52c51fa7504876966ff31eaa0862ce4fdd60a2e6f1a
updated_at: 2026-08-01T16:20:28.145Z
-->

---
doc_id: vinc.c.p5-remote-rollout
layer: C
domain: p5-remote-rollout
title: P5 실행 계획 — 원격 프로파일 롤아웃 (도메인·VM 확보 이후)
status: draft
authority: SSoT
version: v0.1
owner: vinc
last_updated: 2026-07-16
inherits_from: [vinc.d.remote-profile, vinc.d.desktop-shell, vinc.c.p3-execution]
tags: [vinc, C, plan, p5, remote, mcp, connector, homepage, ops]
---

# P5 실행 계획 — 원격 프로파일 롤아웃

> **기점(발주자, 2026-07-11)**: 도메인 구매와 클라우드 VM 생성은 발주자가 직접
> 수행한다. 본 계획서는 **그 두 가지가 확보된 시점부터**의 작업 순서 정본이다.
> 설계 근거는 [[vinc.d.remote-profile]](D36)이 정본이며, 본 문서는 그 §7
> 구현 순서를 실행 가능한 단계·게이트·산출물로 전개한다.
>
> 순서 원칙은 P3·P4 와 동일: 각 Phase 는 게이트 PASS 후에만 다음으로 넘어간다.
> 로컬 기본 프로파일(D7·F4)은 어떤 Phase 에서도 변경하지 않는다.

## 0. 두 트랙 분리 — 서버를 기다리지 않는 작업

도메인·VM 이 없어도 지금 시작할 수 있는 **선행 트랙(코드·정적 산출물)**과,
확보 후에만 가능한 **서버 트랙**을 분리한다. 선행 트랙은 전부 기존 레포와
verify 게이트 안에서 진행되므로 대기 비용이 없다.

| 트랙 | 항목 | 서버 필요 |
|---|---|---|
| 선행 | A. MCP 어댑터 코어 + stdio 전송 | 불필요 |
| 선행 | B. Streamable HTTP 전송 + OAuth 게이트 | 불필요(로컬 실측 가능) |
| 선행 | C. 홈페이지 정적 사이트(토큰 재사용) | 불필요(빌드 산출물) |
| 선행 | D. 배포 템플릿(Caddyfile·systemd·cron) | 불필요(템플릿 저작) |
| 서버 | 1~6단계(§2) | 필요 |

## 1. 선행 트랙 상세

### A. MCP 어댑터 코어 (remote-profile §7-1)

- 사이드카 계약(D11 도구군: ingest fragment · graph · search · recall ·
  suggest-ties · share/import · domains)을 MCP 도구로 노출하는 **공통 코어**
  1모듈. 코어는 전송을 모른다 — 루프백 HTTP 클라이언트(기존 `api` 계약)만 안다.
- 산출: `mcp/` 패키지 + 단위 테스트(도구 스키마·플랜 패리티). 게이트: verify.
- stdio 전송을 먼저 붙여 **로컬 등록**(Claude Code·Desktop)부터 성립시킨다.
  `vinc mcp install --client code|desktop` 자동 등록(§7-2, 경로 3종 백업 후
  병합)까지가 A 의 완료선.

### B. Streamable HTTP 전송 + OAuth 게이트 (remote-profile §7-3)

- 같은 코어를 Streamable HTTP 로 감싸는 두 번째 전송. 포트 8790 루프백 바인딩
  (공개는 Caddy 전담 — F4 유지).
- OAuth 는 MCP 스펙 최소 범위(등록·토큰·검증)로 구현하되, **클라이언트
  시크릿·서명 키는 서버 env 로만 주입**(번들·레포 반입 금지 — D36 제약).
- 로컬 실측: 어댑터를 루프백으로 띄우고 MCP Inspector 급 수동 검증 +
  단위 테스트. 도메인 없이 검증 가능한 마지막 지점까지 여기서 끝낸다.

### C. 홈페이지 정적 사이트 (remote-profile §5)

- 정적 전용(동적 CMS 금지). 제품의 Ink & Paper 토큰(`DESIGN/design-tokens.json`)
  · Notation 아이콘 · 다크 기본(D38)을 재사용해 제품과 같은 얼굴로.
- 구성(1페이지 + 2보조로 시작): 소개/스크린샷 · 다운로드(GitHub Releases 링크
  — P4 파이프라인 산출물) · 문서 링크. en/ko 는 D42 덱 어휘를 재사용.
- 산출: 빌드 산출물 폴더(`/srv/www` 로 복사만 하면 되는 형태). 게이트:
  gate_forbidden(카피 F2)·라이트하우스 급 수동 점검.

### D. 배포 템플릿

- `deploy/` 에 Caddyfile · `vinc-sidecar.service` · `vinc-mcp.service` ·
  백업 cron · UFW/fail2ban 설정 스크립트를 **도메인만 치환하면 되는 템플릿**으로
  저작. 서버 트랙 1~3단계가 이 템플릿의 적용이 되도록.

## 2. 서버 트랙 — 도메인·VM 확보 직후 순서

각 단계 끝의 **게이트**를 통과해야 다음 단계로 간다.

### 1단계 — 서버 기초 공사 (하드닝 먼저, 앱 나중)

- SSH 키 인증 전용(패스워드 로그인 비활성) · 방화벽 UFW 는 22/80/443 만 ·
  fail2ban · unattended-upgrades(보안 패치 자동) · 스왑 2GB(권장 사양 4GB RAM 기준 — 실 VM 은 8GB, §5) ·
  배포 전용 비루트 계정.
- **게이트**: 외부 포트 스캔에서 22/80/443 외 응답 없음. SSH 패스워드 로그인 거부.

### 2단계 — DNS · TLS

- 레지스트라에서 A(필요시 AAAA) 레코드: 루트(홈페이지) · `mcp.`(커넥터) 2건.
  `updates.`(Tauri 업데이트 피드, D28)는 **예약만** 하고 후속.
- Caddy 설치 + 템플릿(§1-D) 적용 → 자동 TLS(Let's Encrypt) 발급 확인.
- **게이트**: `https://도메인`·`https://mcp.도메인` 이 유효 인증서로 응답
  (콘텐츠는 아직 플레이스홀더).

### 3단계 — 사이드카 배포

- **P4 산출물 재사용**: 릴리스 파이프라인(D43)의 리눅스 `vinc-sidecar` 단일
  바이너리를 그대로 올린다(서버에 파이썬 환경 불요). 데이터 홈은 D43 기본
  `~/.vinc` 를 그대로 사용.
- systemd `vinc-sidecar.service`(127.0.0.1:8787, 토큰은 유닛 env) 기동.
- **게이트**: VM 내부에서 `/health` OK · 외부에서 8787 직접 접근 불가.

### 4단계 — MCP 어댑터 배포 + 커넥터 등록

- §1-B 어댑터를 `vinc-mcp.service`(127.0.0.1:8790, After=vinc-sidecar)로 기동,
  Caddy `mcp.` 블록 연결. OAuth 시크릿은 서버 env 파일(권한 600)로만.
- claude.ai 커넥터 등록(수동 1회) → 전 표면 검증: claude.ai 웹 · Claude
  Desktop · Claude Code 3곳에서 도구 호출 왕복.
- **게이트**: 커넥터에서 graph/search/recall 실호출이 실데이터로 응답.
  무토큰·무OAuth 접근은 401.

### 5단계 — 홈페이지 게시

- §1-C 산출물을 `/srv/www` 에 배포(rsync 1회성 → 후속은 CI 훅 검토).
- **게이트**: 루트 도메인에서 다운로드 링크가 최신 GitHub Release 로 연결.

### 6단계 — 운영 안착 (D36 §6 런북 가동)

- R9 `backup` 야간 cron + 오브젝트 스토리지 업로드(이탈 백업). **복원
  리허설 1회 실측**(스냅샷 → 새 디렉토리 복원 → /health·/domains 확인)까지가
  완료선 — 백업은 복원이 증명될 때까지 백업이 아니다.
- 헬스체크 모니터링(외부 무료 uptime 1개면 충분) + 디스크 사용 알림.
- **게이트**: 복원 리허설 PASS 기록을 본 문서 장부에 남긴다.

## 3. 명시적 비범위 (본 계획에서 하지 않는 것)

- **스토어 동기화**: 로컬 Vinc ↔ 원격 Vinc 는 1단계에서 **독립**(D36 §1).
  이관·병합은 `.vincpack`(D19) 수동 왕복만. 실시간 동기화는 별도 DEC 사안.
- `updates.` Tauri 자동 업데이트 피드(D28) — 서명 체계 확보 후 후속.
- 코드사인·공증(P4 잔여) — 시크릿 확보 후 release.yml 에 주입.
- 다중 사용자/테넌트 — Vinc 는 단일 사용자 규모(R5)가 전제.

## 4. 리스크 · 되돌림

| 리스크 | 대응 |
|---|---|
| OAuth 구현 지연 | 4단계만 뒤로 미루고 1~3·5단계는 독립 진행 가능(의존 없음) |
| VM 사양 부족(임베딩 상주) | 1단계에서 BM25 만으로 시작 — 임베딩은 옵션 주입(F5)이라 무변경 켜기 가능 |
| 도메인/DNS 전파 지연 | 2단계 게이트를 `dig` 전파 확인으로 분리, 대기 중 3단계(루프백) 선진행 |
| 서버 분실·손상 | 6단계 이탈 백업 + 복원 리허설이 보증. 홈페이지는 재빌드로 복구 |
| 되돌림 | 커넥터 삭제 + DNS 제거 + VM 폐기로 전체 롤백 — 로컬 프로파일은 무영향 |

## 5. 진행 장부

| 일자 | 항목 | 결과 |
|---|---|---|
| 2026-07-11 | 계획서 작성(본 문서) | — |
| 2026-07-11 | 선행 A — `vinc_mcp/` 코어+stdio+`mcp install`(code·desktop 라이브 실측, 동결 바이너리 stdio 왕복 포함) | PASS |
| 2026-07-11 | 선행 B — Streamable HTTP(루프백 8790)+OAuth 게이트(PKCE 전 구간 라이브, 무토큰 401) | PASS |
| 2026-07-11 | 선행 C — site/ 정적 홈페이지(외부 요청 0, 다크·라이트·en/ko 실측) | PASS |
| 2026-07-11 | 선행 D — deploy/ 템플릿(Caddyfile·systemd 2종·harden.sh·backup.cron·런북) | PASS |
| 2026-07-11 | 검증 — verify 203 tests·gate_forbidden 양 레포·test_mcp 25종 | PASS |
| 2026-07-11 | 도메인 확보 — **vincs.app**(발주자 구매, .app = HSTS preload → Caddy 자동 TLS 로 충족). 커넥터 주소 = `mcp.vincs.app` 확정 | PASS |
| 2026-07-11 | VM 확보 — Ubuntu 24.04 · 4vCPU/8GB/150GB · 공인 IP 1.201.120.27(발주자 생성) | PASS |
| 2026-07-11 | 운영자 런북 저작 — `deploy/OPERATOR-RUNBOOK.md`(vincs.app·이 VM 기준 복붙 실행, 시크릿 온서버 생성) | PASS |
| 2026-07-12 | VM 재확보 — 공인 IP `1.201.120.125`(재생성), 로그인 계정 `ubuntu`(클라우드 키 주입) | PASS |
| 2026-07-12 | 서버 3단계 — 로컬 작업트리 tar 전송(GitHub 배포키 생략) + `harden.sh`. UFW 22/80/443·SSH 비번인증 거부·fail2ban·스왑 | PASS |
| 2026-07-12 | 서버 4단계 — 가비아 DNS A 2건(`@`·`mcp`) → `1.201.120.125` 전파 확인 | PASS |
| 2026-07-12 | 서버 5단계 — Caddy v2.11.4 + Let's Encrypt TLS(vincs.app·mcp.vincs.app 인증서) | PASS |
| 2026-07-12 | 서버 6단계 — 사이드카 PyInstaller 동결(17MB) + systemd 기동, `/health` OK, 8787 외부차단, 데이터홈 자동생성 | PASS |
| 2026-07-12 | 서버 7단계(서버측) — MCP 어댑터 8790 기동, 무토큰 `POST /mcp` 401, OAuth 메타 노출. **커넥터 등록(claude.ai)은 발주자 GUI 대기** | 서버 PASS |
| 2026-07-12 | 서버 8단계 — 홈페이지 `/srv/www` 게시, `https://vincs.app` 200 렌더 | PASS |
| 2026-07-12 | 서버 9단계 — 백업 메커니즘 입증(HTTP `/backup` 무중단 스냅샷 + 복원 리허설). 빈 DB → 첫 ingest 후 실데이터 왕복 재확인 | PASS(메커니즘) |
| 2026-07-12 | 브링업 중 버그 3건 수정 — (a) harden.sh sshd 드롭인 우선순위(cloud-init `60-cloudimg` 우회, `00-` 접두), (b) `backup` CLI 데이터홈 미해석, (c) `backup.cron` 락 실패 → HTTP `/backup` 헬퍼(`deploy/vinc-backup.sh`)로 전환. + `.gitattributes` LF 강제(CRLF 방지) | PASS |
| 2026-07-13 | GitHub org → **Vinclums** 이관(4 리포: `Vinc` public·`Vinc-docs`·`Vincs-app`(릴리스 호스트)·`Vinc-design`). 릴리스 파이프라인 cross-repo — `release.yml`이 `Vinc`에서 3-OS 빌드 → softprops로 `Vincs-app`에 발행(시크릿 `RELEASE_TOKEN` = Vincs-app contents:write PAT) | PASS |
| 2026-07-13~15 | 릴리스 **v0.1.0→v0.1.6** 게시 — `release.yml`은 `Vincs-app`에 **draft**로 발행(`draft:true`) → 사람이 GitHub UI에서 **Publish** 눌러야 `latest/download` 공개. 다운로드 라이브(vincs.app/download → Vincs-app releases, mac dmg·win exe·linux AppImage/deb 전부 200) | PASS |
| 2026-07-15 | 서버 7단계 완료 — **MCP 커넥터 claude.ai 등록·왕복 확인**(URL `https://mcp.vincs.app/mcp`, DCR Client ID·Secret 빈칸=`/register` 201, 인가 403 시 URL에 `&key=<VINC_OAUTH_ADMIN_KEY>` 첨부 재로드). 원격 그래프 데모 concept 왕복 OK. ⚠ **vinc-mcp 재시작 시 OAuth 세션(인메모리) 초기화 → 커넥터 재연결 필수** | PASS |
| 2026-07-15 | 원격 스토어 프로비저닝 픽스 — 배포돼 있던 사이드카가 7/12자(v0.1.1 프로비저닝 픽스 이전)라 `/home/vinc/.vinc` 스토어 스키마 미생성 → 전 쿼리 "Table Episode/Chunk does not exist". 해결 = `/opt/vinc/vinc-sidecar`를 **v0.1.6로 교체**(`.deb`에서 추출, 구본 백업 `.bak-*`) → serve의 `VincApp.open`이 기동 시 멱등 프로비저닝. 이후 `/domains` executed=true. 원격 사이드카 갱신 시 이 방식 재사용 | PASS |
| (발주자 대기) | 2단계 클라우드 보안그룹 인바운드 22/80/443만 확인(7단계 커넥터 등록은 2026-07-15 완료) | — |
| 2026-07-16 | 스토리지 용량 점검(질의응답 기반, 코드·설정 실측) — 그래프는 구조화 fragment만 저장(원문 문서 미보관)이라 스토어는 수십MB 규모, 실 VM 150GB vs 설계 추정([[vinc.d.remote-profile]] §디스크) 40~60GB 대비 여유 확인. `deploy/backup.cron` 7일 자동 로테이션(`find -mtime +7 -exec rm`) 정상 확인 — 스냅샷 무한 누적 아님 | 과부하 저위험. **6단계 잔여 미착수 재확인**: 이탈 백업(rclone, `backup.cron` 내 주석 처리 상태) · 디스크 사용 알림 — 둘 다 미구현 |

## 부록 Z. 미분류 원문 사실
(없음)
