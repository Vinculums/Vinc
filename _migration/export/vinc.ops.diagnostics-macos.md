<!-- vinc-export
doc_id: vinc.ops.diagnostics-macos
title: macOS 사이드카 기동 조사 — 2026-07-16
origin: Vinc-docs/vinc/DIAGNOSTICS-macos-2026-07.md@5090690
chunks: 3
verified: true
content_hash: 151fbafd9e8602860ade6aff6983ffc81a4bfab6b6b26afd53ed1b03bcd35e92
updated_at: 2026-08-01T16:20:44.594Z
-->

# macOS 사이드카 기동 조사 — 2026-07-16

> 필드 리포트 1건을 추적한 기록. **가설 3개가 전부 반증**됐고, 진짜 결함은 다른 데
> 있었다. 재조사 방지 + 조사 기법 재사용을 위해 남긴다. 정본 아님(조사 기록).

## 1. 증상 (필드 리포트, v0.1.6 aarch64)

```
the sample pack을(를) 읽지 못했습니다. …
진단: Load failed · base=http://127.0.0.1:49358 token=64ch · health TIMEOUT
```

이 한 줄이 이미 많은 걸 배제했다:

| 단서 | 의미 |
|---|---|
| `base=…:49358` | 동적 포트 정상 = v0.1.6 확실, 셸이 포트 배정 성공 |
| `token=64ch` | 세션 토큰 전달 정상 = 셸↔프론트 IPC 핸드셰이크 무사 |
| `health TIMEOUT` | 그 포트에 **아무도 응답하지 않음** |
| `Load failed` | WebKit(WKWebView)의 fetch 실패 — Chromium 의 "Failed to fetch" 등가물 |

즉 Windows 에서 겪은 결함들(CORS·토큰 전달·8787 포트 충돌)과 **완전히 다른 층**이다.

## 2. 조사 기법 — Windows 에서 macOS 실측하기 (재사용 가치 높음)

발주자·개발 환경 모두 Windows 라 Mac 이 없었다. **GitHub Actions 의 macOS 러너**
(`macos-latest` = Apple Silicon, 공개 리포라 무료)에 **배포본 `.dmg` 를 실제로 설치**해
실측했다. 핵심 요령 3가지:

1. **다운로드 격리 재현** — `curl` 다운로드엔 격리 속성이 안 붙는다. 브라우저 다운로드를
   흉내내려면 직접 부여:
   ```bash
   xattr -wr com.apple.quarantine "0081;$(printf '%x' $(date +%s));Safari;$(uuidgen)" Vinc.app
   xattr -d com.apple.quarantine Vinc.app     # 우클릭→열기 승인 흉내(번들에만)
   ```
2. **Gatekeeper 강제 활성화** — 러너가 꺼놨으면 실사용과 달라진다: `sudo spctl --master-enable`
3. **결과를 스텝 결론으로 인코딩** — Actions 로그 API 는 익명 접근이 **403**이라 로그를
   못 읽는다. 반면 **잡/스텝의 conclusion 은 공개 API 로 읽힌다**. 그래서 각 프로브를
   `continue-on-error: true` 스텝으로 쪼개 **성공/실패 자체가 답**이 되게 설계했다.
   (`GET /repos/{o}/{r}/actions/runs/{id}/jobs` → `steps[].conclusion`)

조사용 워크플로는 `diag/macos` 브랜치에 있었고 목적 달성 후 삭제했다(3차까지 진행).

## 3. 반증된 가설 3개 — **재조사 금지**

### 가설 1: 격리(quarantine)가 사이드카를 죽인다 → **반증**
논리: 미서명 배포라 `.dmg` 내려받으면 앱 내부 실행파일 전부에 `com.apple.quarantine`
이 붙는다. 우클릭→열기는 **앱 본체**만 승인하니, 앱이 자식으로 띄우는 `vinc-sidecar` 는
격리 상태로 남아 Gatekeeper 가 조용히 SIGKILL 한다 — 대화상자도 없다(프로그램이 띄운
프로세스라). Windows 는 SmartScreen 이 exe 첫 실행만 경고하고 자식은 안 죽이니 설명도 됐다.

**실측 결과**: 격리 부여 상태에서도 사이드카 exec **성공**. 실사용 재현(격리 + 앱본체만
승인 → `open` → 15s 후 확인)에서도 **사이드카 생존**. 격리는 원인이 아니다.

### 가설 2: 콜드 스타트가 느려 health 폴링(5s)이 짧다 → **반증**
논리: 미서명 바이너리 첫 실행엔 Gatekeeper 악성코드 스캔이 붙고, PyInstaller onefile
(30MB)은 매 실행 압축을 푼다 — 둘 다 macOS 에서만 비싸다.

**실측 결과**: 앱 실행 → `/health` 200 까지 **5초 이내** 도달(`RESULT-B` PASS).
폴링 창은 충분했다.

### 가설 3: 웹뷰가 loopback http 를 혼합 콘텐츠로 사전 차단 → **반증**
논리: macOS 웹뷰 origin 은 `tauri://localhost`(보안 스킴)라 `http://127.0.0.1` 요청이
혼합 콘텐츠로 막힐 수 있다 — WebKit 은 Chromium 과 달리 loopback 을 안전 출처로 안
봐준다. Windows 는 origin 이 `http://tauri.localhost` 라 같은 스킴이라 무사하고,
차단 지점이 CORS 보다 앞이라 v0.1.4 CORS 픽스와도 모순이 없었다.

**판별 설계**: 혼합 콘텐츠 차단은 **요청이 네트워크로 나가기 전**에 일어난다 → 차단이면
사이드카 포트에 클라이언트 연결 흔적이 0이어야 한다(curl 은 되는데). 그래서 **curl 하기
전에** ① 우리 JS 실행 여부(WebKit localStorage 에 `vinc.sidecar.*` 존재) ② 포트의
비-LISTEN TCP 흔적(`netstat -an -p tcp`) 을 쟀다.

**실측 결과**: ①②  **둘 다 PASS** — 웹뷰가 페이지를 로드했고 사이드카 포트로 **실제
연결**했다. 차단은 없다.

### 결론
**v0.1.6 은 깨끗한 Apple Silicon macOS 에서 완전히 정상 동작한다.** 원인은 그 기기
고유(구버전 잔존 스토어·보안 소프트웨어 등)다. CI 는 `~/.vinc` 가 없는 새 환경이라
기기별 상태 차이를 재현할 수 없다 — **원격 추측의 한계**.

## 4. 진짜 결함 — 침묵

3차까지 태우고 얻은 교훈: 앱이 `health TIMEOUT` 외엔 **아무것도 말하지 못한다**. 같은
날 MCP 도구에서 잡은 결함(노션 「Vinc MCP 도구 결함 분석」 §R6 *실패 신호 설계 부재*)과
**같은 병**이 앱 계층에 남아 있었다. 계측이 있었으면 첫 리포트에서 끝났을 문제다.

### 유력 후보 (v0.1.8 이 무력화)
`VincApp.open` 이 `store.provision` 예외를 그대로 흘려 **serve 가 즉사**했다 → 포트에
아무도 없음 → 원인 불명 `health TIMEOUT`. **구버전 잔존 스토어·락·손상이면 정확히
재현되는 시나리오**이고, CI 는 새 스토어라 안 걸렸다. (Windows 세션에서 원격 서버의
기존 db 로 프로비저닝이 이상하게 굴던 것과 같은 계보.)

### v0.1.8 조치
| 조치 | 효과 |
|---|---|
| `VincApp.open` 이 provision 예외를 삼키고 기동 계속 | 스토어 문제로 엔진 전체가 죽지 않음 |
| `/health` → `{"status":"degraded", "provision_error"\|"error": …}` | 성치 않은 기동을 숨기지 않음(정상은 `ok` 유지) |
| 셸이 사이드카 **stderr·종료코드·시그널** 수집 → `sidecar_status` 커맨드 | 즉사 사유가 진단에 실림 |
| 진단에 **앱 버전·셸이 준 실제 포트** 각인 | 리포트만으로 버전·포트 혼동 제거 |
| health 대기 5s→12s | 콜드 스타트 여유(가설 2 는 반증됐으나 무해한 여유) |

### 다음 리포트 판독표
| 진단 문구 | 원인 |
|---|---|
| `health DEGRADED[RuntimeError: …]` | **스토어/프로비저닝 문제 확정** → `~/.vinc` 제거 후 재실행으로 즉시 해결 |
| `health TIMEOUT · sidecar[terminated: code=… signal=…]` | 사이드카 즉사 — 사유가 그대로 노출 |
| `health TIMEOUT · sidecar[no sidecar events]` | 사이드카는 살아있는데 웹뷰가 못 닿음 → 전혀 다른 갈래 |

## 5. 남은 사실 (참고)

- macOS 배포본은 **aarch64 전용** — Intel Mac 은 애초에 실행 불가(리포터는 실행됐으니
  Apple Silicon 확정).
- 미서명 앱이라 **Applications 폴더 배치 + 터미널 승인**이 필요한 건 정상 절차이며,
  이번 적재 실패의 원인은 **아니었다**(가설 1 반증). 근본 해결은 서명뿐이다 —
  Windows 는 SignPath(무료 OSS, 공개 리포 조건), macOS 는 Apple Developer $99/yr.
  ⚠ **리포 공개 상태가 SignPath 자격·Actions 무료의 전제**다(2026-07-16 비공개 전환이
  잠시 있었음 — 되돌림).
