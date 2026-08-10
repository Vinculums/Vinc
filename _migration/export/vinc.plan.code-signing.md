<!-- vinc-export
doc_id: vinc.plan.code-signing
title: 코드 서명·공증 실행 계획서 (P4 잔여)
origin: Vinc-docs/plans/PLAN-code-signing-notarization.md@5090690
chunks: 7
verified: true
content_hash: 356b718e228d38841b686a8a9d4c630df3319d561c9688171bafa787dc067899
updated_at: 2026-08-01T16:20:53.376Z
-->

# 코드 서명·공증 실행 계획서 (P4 잔여)

> ⚠️ **SUPERSEDED — 방향이 DEC D44(2026-07-13)로 뒤집혔다.** 본 계획서는 **유료 경로**(Apple
> Developer $99/yr 필수 · SSL.com eSigner OV ~$180–249/yr · 1년차 $215–350/yr)와 **비공개 레포**를
> 전제하지만, D44 는 **$0 경로**를 확정했다: `Vinclums/Vinc` **public 전환** → **Windows =
> SignPath Foundation 무료 OSS 서명**, **macOS = 미서명 유지 + 다운로드 페이지 첫 실행 우회 안내**.
> Apple Developer 는 macOS 서명을 원할 때만. 또한 본문의 "비공개 소스 레포"·"v0.1.0→v0.1.1 bump"
> 전제도 현행과 다르다(레포 public · 릴리스 **v0.1.6** 게시).
> **정본 = [[vinc.b.architecture]] §2 D44 · [[vinc.d.desktop-shell]] §6 D44.** 아래는 D44 이전의
> 조사·비교 기록으로 보존한다(인증서 조달 옵션 비교·CI 배선 위치는 여전히 참고 가치가 있다).

> **목표**: 첫 실행 시 뜨는 macOS Gatekeeper "확인되지 않은 개발자" / Windows SmartScreen "PC 보호됨" 경고를 제거해, 다운로드→실행 마찰을 없앤다.
> **현황**: 파이프라인은 서명 없이 "빌드만" 하도록 이미 준비돼 있음. `.github/workflows/release.yml:9`가 *"서명(코드사인·공증)은 후속 마일스톤 — 시크릿이 갖춰지면 tauri-action env로 주입"*이라고 명시. 즉 **배선은 비어 있고, 인증서 조달(사람 게이트)만 하면 코드는 곧바로 붙일 수 있는 상태**. `tauri.conf.json`의 `bundle`(26–48행)에는 `windows`/`macOS` 서명 키가 전무하고, 업데이터 플러그인도 미설정.

---

## 전제·의존 (preconditions/dependencies)

현재 코드 근거:

| 근거 파일 | 관찰 | 의미 |
|---|---|---|
| `.github/workflows/release.yml:78-81`(리포 루트 — 구 `app/` 접두는 오기) | `tauri-apps/tauri-action@v0` 을 `projectPath: app`만 주고 build-only로 호출, `env:` 블록 없음 | 서명 env 주입 지점이 여기 하나로 확정. 서명은 **build 잡 안에서** 끝남(publish 잡 100–127행은 손댈 필요 없음) |
| `release.yml:30-31` | macOS 매트릭스가 `macos-latest` + `aarch64-apple-darwin` 하나뿐 | 공증은 arm64 `.dmg`/`.app`에만 적용됨. **Intel(x86_64) Mac 미커버** (결정 항목 참조) |
| `release.yml:41-57` | 사이드카는 PyInstaller 단일 바이너리(`vinc-sidecar`)로 빌드돼 `externalBin` 위치에 배치 | 공증 시 **번들 내부의 이 바이너리도 서명·hardened runtime 대상** → 리스크 1의 핵심 |
| `tauri.conf.json:26-48` | `bundle`에 `windows`·`macOS` 서명 블록 없음, `externalBin: ["binaries/vinc-sidecar"]`(32행) | Windows는 `bundle.windows.signCommand`(또는 thumbprint)를, macOS는 서명 identity를 여기/또는 env로 추가해야 함 |
| `tauri.conf.json` 전체 | `plugins.updater` 없음 | 오토업데이터 미설정 → 본 계획의 Phase 4에서 **사전 배선만** (별도 마일스톤) |

시작을 막는 블로커(전부 owner 게이트):

1. **Apple Developer Program 가입** — macOS 서명·공증의 절대 전제. 승인까지 리드타임 존재(개인 즉시~영업일 수일, 조직은 D-U-N-S 확인으로 더 김).
2. **Windows 코드서명 인증서 확보 + 신원검증** — 발급까지 OV 기준 영업일 1~5일. ⚠️ **Azure Artifact Signing(구 Trusted Signing)은 현재 한국에서 발급 불가**(개인·조직 모두 US/CA 등으로 제한, 2025-04 기준) → 결정 항목에서 대안 확정 필요.
3. **GitHub Secrets 등록 권한** — 비공개 소스 레포 `Vinclums/Vinc`의 Settings→Secrets 접근. 서명은 소스 레포 CI에서 일어나므로 시크릿도 여기(공개 `Vincs-app` 아님)에 등록.
4. (자동) macOS 러너 — 이미 매트릭스에 존재하므로 추가 인프라 불필요.

**HARD INVARIANT 확인**: 공증(notarization)은 **CI 빌드 시점에** Apple 서버로 바이너리를 올리는 것이지 앱 런타임 행위가 아님. 공증 티켓을 스테이플(staple)하면 사용자 기기의 첫 실행 검증이 **오프라인으로 통과** → "앱은 로그인 없이 완전 오프라인" 불변식 위배 없음. (서명/공증은 서버 API·웹 계정과 무관.)

---

## 단계 (phased tasks)

### Phase 0 — 결정·인증서 조달 (사람 게이트, 리드타임 최대)

| # | 작업 | 담당 | 완료선 |
|---|---|---|---|
| 0.1 | 서명 주체 확정(개인 vs 법인 `Vinclums`) — 이후 모든 신원검증에 영향 | **O** | 결정 기록됨(결정 항목 D3) |
| 0.2 | Apple Developer Program 등록·결제($99/yr), 팀 승인 | **O** | App Store Connect 로그인 가능, **Team ID** 확보 |
| 0.3 | **Developer ID Application** 인증서 생성 후 `.p12`로 내보내기(개인키 포함) | **O** | `.p12` 파일 + 내보내기 암호 확보 |
| 0.4 | 공증용 앱 전용 암호(app-specific password) 발급(appleid.apple.com) | **O** | 앱 암호 문자열 확보 |
| 0.5 | Windows 인증서 방식 확정 후 발급 신청·신원검증(결정 D1: **SSL.com eSigner OV** 권장) | **O** | 클라우드 서명 자격증명(계정/credential id/TOTP) 확보 |

### Phase 1 — macOS 서명·공증 배선 (C 주도, O 시크릿 등록)

`tauri-action`은 아래 env가 있으면 **임시 키체인 생성·인증서 임포트·서명·`notarytool` 공증·스테이플을 자동 수행**한다(수동 `security` 명령 불필요). 배선은 `release.yml:78-81` 스텝에 `env:`를 추가하는 것이 전부.

| # | 작업 | 담당 | 완료선 |
|---|---|---|---|
| 1.1 | GitHub Secrets(`Vinclums/Vinc`) 등록: 아래 6개 | **O** | 6개 시크릿 저장됨 |
| 1.2 | `release.yml`의 tauri-action 스텝(78–81행)에 `env:` 블록 추가(아래 스니펫), macOS에서만 적용되도록 매트릭스 조건 유지 | **C** | 커밋 완료, `act`/문법 검증 통과 |
| 1.3 | (선택) `tauri.conf.json > bundle > macOS`에 `signingIdentity` 명시 대신 env `APPLE_SIGNING_IDENTITY`로 주입(설정 파일 오염 최소화) | **C** | env 우선 방식 확정 |

등록할 macOS 시크릿(정확한 이름):

| 시크릿 이름 | 값 |
|---|---|
| `APPLE_CERTIFICATE` | 0.3의 `.p12`를 base64 인코딩한 문자열 |
| `APPLE_CERTIFICATE_PASSWORD` | `.p12` 내보내기 암호 |
| `APPLE_SIGNING_IDENTITY` | `Developer ID Application: <이름> (<TEAMID>)` |
| `APPLE_ID` | Apple 계정 이메일 |
| `APPLE_PASSWORD` | 0.4의 앱 전용 암호 |
| `APPLE_TEAM_ID` | 0.2의 Team ID |

`release.yml` 78–81행에 추가할 형태(개념):

```yaml
      - name: Build Tauri bundles
        uses: tauri-apps/tauri-action@v0
        env:
          # macOS 러너에서만 채워짐(다른 OS에선 빈 값 → 무시)
          APPLE_CERTIFICATE: ${{ secrets.APPLE_CERTIFICATE }}
          APPLE_CERTIFICATE_PASSWORD: ${{ secrets.APPLE_CERTIFICATE_PASSWORD }}
          APPLE_SIGNING_IDENTITY: ${{ secrets.APPLE_SIGNING_IDENTITY }}
          APPLE_ID: ${{ secrets.APPLE_ID }}
          APPLE_PASSWORD: ${{ secrets.APPLE_PASSWORD }}
          APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
        with:
          projectPath: app
```

> 근거: Tauri v2 docs `distribute/sign/macos` + `reference/environment-variables` — `APPLE_SIGNING_IDENTITY` 미설정 시 `APPLE_CERTIFICATE`에서 추론, `APPLE_ID` 설정 시 `APPLE_PASSWORD`+`APPLE_TEAM_ID` 필수(또는 `APPLE_API_KEY`/`APPLE_API_ISSUER` 대체).

### Phase 2 — Windows Authenticode 배선 (C 주도, O 시크릿 등록)

Azure가 한국 불가이므로 **클라우드 HSM 기반 OV**(권장: SSL.com eSigner)로 진행. Tauri v2 `bundle.windows.signCommand`에 `%1`(서명 대상 파일 경로)을 넘기는 커스텀 서명 명령을 등록하고, CI에 CodeSignTool을 설치한다.

| # | 작업 | 담당 | 완료선 |
|---|---|---|---|
| 2.1 | GitHub Secrets 등록: `ESIGNER_USERNAME`, `ESIGNER_PASSWORD`, `ESIGNER_CREDENTIAL_ID`, `ESIGNER_TOTP_SECRET` | **O** | 4개 시크릿 저장됨 |
| 2.2 | `tauri.conf.json > bundle.windows.signCommand` 추가(아래) | **C** | 커밋, JSON 스키마 검증 통과 |
| 2.3 | `release.yml` build 잡에 Windows 전용 스텝 추가: CodeSignTool 설치(캐시) + 위 4개 env 노출 | **C** | Windows 러너에서 CodeSignTool 실행 가능 |
| 2.4 | 셸 확장 이슈 회피용 래퍼(`app/src-tauri/sign.ps1` 또는 `.cmd`) 도입 여부 결정·구현 | **C** | 서명 명령이 러너 셸에서 `%1` 치환·env 참조 정상 동작 |

`tauri.conf.json` 추가(개념):

```json
"bundle": {
  "windows": {
    "signCommand": "CodeSignTool sign -credential_id=$ESIGNER_CREDENTIAL_ID -username=$ESIGNER_USERNAME -password=$ESIGNER_PASSWORD -totp_secret=$ESIGNER_TOTP_SECRET -input_file_path=%1 -override"
  }
}
```

> 근거: Tauri v2 docs `distribute/sign/windows` — `signCommand`가 서명 대상마다 호출되며 `%1`이 파일 경로로 치환됨(Azure 예시 `trusted-signing-cli … %1`과 동일 메커니즘). eSigner CodeSignTool은 헤드리스 CI 서명을 지원하므로 signCommand에 그대로 얹음. **주의**: env 참조 표기(`$VAR` vs `%VAR%`)가 러너 셸에 종속 → 2.4의 래퍼로 안정화 권장.

### Phase 3 — v0.1.1 태그로 검증

| # | 작업 | 담당 | 완료선 |
|---|---|---|---|
| 3.1 | `version` 0.1.0→0.1.1로 bump(`tauri.conf.json:4`) + CHANGELOG | **C** | 커밋 |
| 3.2 | `git tag v0.1.1 && push` → `release.yml` 트리거(13–14행) | **O** | build·publish 잡 성공, draft 릴리스 생성 |
| 3.3 | macOS 검증: 산출 `.dmg`에서 `codesign -dv --verbose=4 Vinc.app`, `spctl -a -vvv -t install Vinc.app`, `stapler validate Vinc.app` | **C** | 3개 모두 accepted/valid, `source=Notarized Developer ID` |
| 3.4 | Windows 검증: `signtool verify /pa /v Vinc_*-setup.exe`(또는 PowerShell `Get-AuthenticodeSignature`) | **C** | 서명 유효, 타임스탬프 존재 |
| 3.5 | 실기기 스모크: 깨끗한(또는 새 사용자) macOS·Windows에서 다운로드→첫 실행, 경고 유무·오프라인 실행 확인 | **O** | macOS 경고 없음; Windows는 경고 소멸 또는 "게시자 확인됨"으로 완화(리스크 2) |

### Phase 4 — 오토업데이터 사전 배선(연결만, 본 마일스톤 범위 밖)

오토업데이터는 **Authenticode/Apple 서명과 별개인 Tauri 자체 서명 키페어**를 요구(업데이트 매니페스트 위조 방지용). 지금 키를 만들어 두면 나중 마일스톤이 곧바로 시작 가능.

| # | 작업 | 담당 | 완료선 |
|---|---|---|---|
| 4.1 | `npm run tauri signer generate -- -w vinc_updater.key` 로 키페어 생성 | **C** | 개인키+공개키 산출 |
| 4.2 | 공개키를 `tauri.conf.json > plugins.updater.pubkey`에 커밋(비밀 아님) | **C** | 설정 반영 |
| 4.3 | Secrets 등록: `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | **O** | 저장됨(개인키는 절대 커밋 금지) |
| 4.4 | (별도 마일스톤) updater 아티팩트/`latest.json` 발행·엔드포인트는 본 계획 밖으로 명시 | **C** | 범위 경계 문서화 |

> 주: 이 키페어는 "코드 신뢰(OS 경고 제거)"가 아니라 "업데이트 무결성"을 담당. **불변식 재확인**: 업데이트 엔드포인트는 별도 웹 인프라이며 로컬 볼트를 읽지 않고, 앱을 강제로 phone-home 시키지 않도록 후속 마일스톤에서 opt-in/명시적 확인 설계 필요.

---

## 결정 필요 (owner decisions · 권장 기본값 포함)

| ID | 결정 | 선택지 | 권장 기본값 |
|---|---|---|---|
| **D1** | Windows 인증서 방식 | (a) Azure Artifact Signing — **한국 발급 불가**로 제외 · (b) **SSL.com eSigner OV**(클라우드 HSM, CI 네이티브, ~$180–249/yr) · (c) Certum Cloud OV(SimplySign, 최저가 ~$116/yr지만 CI 자동화 난이도↑) · (d) EV(가장 비쌈, 하드웨어 토큰/클라우드, **2026 MS 정책 변경으로 즉시 SmartScreen 제거 보장 사라짐**) | **(b) SSL.com eSigner OV** — CodeSignTool로 헤드리스 CI 서명이 깔끔, signCommand에 그대로 얹힘. 예산 최소화가 최우선이면 (c) |
| **D2** | macOS Intel(x86_64) 지원 | (a) arm64 전용 유지(현행 매트릭스) · (b) x86_64 매트릭스 추가 · (c) universal 바이너리 | **(a) arm64 전용** — 사이드카가 PyInstaller 아키텍처별 빌드라 (b)/(c)는 매트릭스·사이드카 크로스빌드 부담. 다운로드 페이지에 "Apple Silicon" 명시로 보완 |
| **D3** | 서명 주체 | 개인 vs 법인(`Vinclums`) | **상황별**: 브랜드 신뢰가 중요하면 법인(설치 시 조직명 표시)이나 신원검증이 김. 빠른 출시 우선이면 개인 |
| **D4** | 공증 인증 방식 | (a) Apple ID + 앱 전용 암호 · (b) App Store Connect **API 키** | **(a)** — 배선 단순(env 3개). CI 보안 강화·비밀 회전 자동화가 필요해지면 (b)로 이전 |

---

## 리스크·함정

1. **[중대] PyInstaller 사이드카 공증 실패**: `release.yml:42-43`의 단일 바이너리 `vinc-sidecar`가 번들 내부에 들어감. hardened runtime 하에서 PyInstaller onefile은 (자체 Python 인터프리터의 JIT/메모리 실행, 미서명 내장 Mach-O, `/tmp` 자기추출)로 `notarytool`에서 반려되기 쉬움. 대응: (i) 사이드카에도 서명 적용 확인, (ii) 필요 시 entitlements(`com.apple.security.cs.allow-jit`, `allow-unsigned-executable-memory`, `disable-library-validation`) 추가, (iii) 최후 수단 onefile→onedir 전환 검토. **Phase 3 검증에서 반드시 실기 공증까지 확인**(빌드 성공 ≠ 공증 성공).
2. **[중] OV는 초기 SmartScreen 경고 잔존**: OV 서명은 평판(reputation)이 누적될 때까지 다운로드 초기 경고가 남을 수 있음. 게다가 2026 MS 정책 변경으로 **EV조차 즉시 제거를 보장하지 않음** → EV 프리미엄의 근거 약화. 기대치 설정: "서명은 하되 경고는 다운로드 누적에 따라 사라짐"을 owner에게 사전 공유.
3. **[중] signCommand 셸 확장·CodeSignTool 실행 환경**: env 참조 표기와 러너 셸(cmd/PowerShell/bash) 상호작용, CodeSignTool(Java) 설치·TOTP 시크릿 취급이 까다로움 → Phase 2.4 래퍼로 격리하고 Phase 3에서 실제 서명 산출로만 검증.
4. **[중] 인증서 유효기간 459일 상한(2026-02-27 이후)**: 코드서명 인증서 최대 유효기간이 짧아짐 → 갱신 캘린더 필요. Apple Developer Program은 연 $99 갱신, 앱 전용 암호·인증서 만료도 모니터링.
5. **[중] 시크릿 관리**: `.p12`/개인키/TOTP 시크릿은 비공개 소스 레포에만, 로그 노출 금지(마스킹 확인). 개인키·`vinc_updater.key`는 절대 커밋 금지.
6. **[저] macOS Intel 미커버**(D2 관련): 현행 매트릭스가 arm64뿐 → Intel 사용자 다운로드 시 아키텍처 불일치. 다운로드 UI 라벨로 완화.
7. **[저] 태그/발행 레포 분리 유의**: 서명은 소스 레포 `Vinclums/Vinc` CI에서, 발행은 `Vincs-app`으로(현 `release.yml:117-126`). 시크릿을 발행 레포에 잘못 넣지 않도록 주의. v0.1.1 draft가 이미 있으면 재태그 충돌 주의.

---

## 개략 공수 (rough effort)

| Phase | 내용 | Claude(C) | Owner(O) | 대기·리드타임 |
|---|---|---|---|---|
| 0 | 결정 + 인증서 조달 | ~0.5h(리서치/문서 지원) | ~2–3h 실작업 | **Apple 승인 0~수일 + Windows OV 발급 1~5영업일**(주 경로) |
| 1 | macOS 배선(env 6개 + release.yml) | ~1h | ~20m(시크릿 등록) | — |
| 2 | Windows 배선(signCommand + CodeSignTool 스텝 + 래퍼) | ~2–3h | ~20m(시크릿 등록) | — |
| 3 | v0.1.1 태그 검증(실기 공증·SmartScreen까지) | ~1–2h | ~30m(태그 푸시 + 실기 스모크) | CI 빌드+공증 왕복 ~15–40m/회, 1–2회 반복 예상 |
| 4 | 업데이터 키페어 사전배선(연결만) | ~30m | ~10m(시크릿 2개) | — |

**연간 비용(예상)**: Apple Developer Program **$99/yr**(필수) + Windows OV(**SSL.com eSigner ~$180–249/yr** 권장, 또는 Certum ~$116/yr) → 1년차 합계 **약 $215–350/yr**. 공증은 무료. Azure(~$120/yr, US/CA 한정)는 한국 불가로 제외.

**총 실행 시간**: 코드 작업(C)은 **반나절 이내**에 마무리 가능. **크리티컬 패스는 Phase 0의 인증서 조달 리드타임**(주 단위 대기 가능)이며, 인증서가 손에 들어오는 순간 Phase 1–3은 하루 내 완료·검증 가능. 완료 시 오토업데이터 마일스톤(Phase 4 키페어)까지 선행 해소됨.
