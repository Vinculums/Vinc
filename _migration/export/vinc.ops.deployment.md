<!-- vinc-export
doc_id: vinc.ops.deployment
title: DEPLOYMENT — Vinc 배포 정책 (as-built, Azure)
origin: Vinc-docs/vinc/DEPLOYMENT.md@5090690
chunks: 15
verified: true
content_hash: 88b73aa9ede35a1231902017fca8ac79310761f0dba8cf71361b515124989488
updated_at: 2026-08-01T16:20:41.780Z
-->

> 정본은 리포 파일입니다. 이 사본은 검색용 스냅샷(2026-08-02)입니다.

# DEPLOYMENT — Vinc 배포 정책 (as-built, Azure)

> **성격**: 이 문서는 *이상적 절차*가 아니라 **실제로 지금 그렇게 돌아가는 방식**의
> 기록이다(as-built). 관련 문서: `AZURE-ARCHITECTURE.md`(리소스 인벤토리·다이어그램),
> `AZURE-COST-MANAGEMENT.md`(비용 관리·요금 폭탄 위험). A/B/C/D 계층 검증 대상이 아니다.
>
> **⚠ 2026-07-28/29 전면 이관 — 가비아 삭제 → Azure.** 이전 버전의 이 문서(가비아 VM
> 단일 서버 + Caddy + systemd)는 더 이상 as-built 가 아니다. 가비아 클라우드 자원(VM
> `1.201.120.125`, 관리형 MySQL)이 전량 삭제되어, Container Apps(사이드카+MCP·계정
> 백엔드) · MySQL Flexible Server · Static Web Apps · GitHub Actions CD 로 재구축했다.
> 아래 내용 전부가 이번에 새로 실측 검증됐다(실 그래프 쓰기/검색/재시작 후 데이터
> 영속성, 실 HTTPS 커스텀 도메인 3종, Linked Backend 프록시).

---

## §0 한눈에 — 배포 표면 5개

| # | 표면 | 소스 | 산출물 | 트리거 | 자동화 |
|---|---|---|---|---|---|
| 1 | **데스크탑 앱** | `Vinc/`(전체) | GitHub Releases(`Vinclums/Vincs-app`) | `v*` 태그 push | **CI 있음**(+ 사람이 발행) — 무변경 |
| 2 | **정적 사이트 + 웹 Vinc** | `Vinc-homepage/site/` + `Vinc/app/` | Azure Static Web Apps(`vinc-site`) | `site/**` push (main) | **CD 있음**(`site-deploy.yml`) |
| 3 | **계정 백엔드** | `Vinc-homepage/account/` | Azure Container Apps(`vinc-account`) | `account/**` push (main) | **CD 있음**(`account-deploy.yml`) |
| 4 | **허브(사이드카+MCP)** | `Vinc/`(api·store·engines·vinc_mcp) | Azure Container Apps(`vinc-hub`) | 해당 경로 push (main) | **CD 있음**(`hub-deploy.yml`) |
| 5 | **DB** | `account/db/migrations/` | Azure Database for MySQL Flexible Server | 수동 SQL 적용 | 없음(전진 전용) |

**요약**: 표면 1(데스크탑)은 기존 그대로. 표면 2~4는 이번에 **처음으로 CD가 생겼다**
(가비아 시절엔 전부 사람이 SSH로 수동 배포했다). 표면 5(DB 스키마)만 여전히 수동 —
관리형 서비스가 바뀌어도 "마이그레이션을 사람이 SQL로 직접 적용" 관행은 유지된다.

### Azure 리소스 공통

| 항목 | 값 |
|---|---|
| 구독 | `vincs` (`5d3dbbfd-9770-460a-aede-0f13c0e1683c`), 테넌트 `74b27512-...` |
| 리소스 그룹 | `vincsRG` (Container Apps·MySQL·ACR·Storage는 Korea Central, Static Web App은 East Asia — SWA 미지원 리전) |
| 도메인 | `vincs.io`(apex, A 레코드) · `api.vincs.io`(계정, CNAME) · `mcp.vincs.io`(허브, CNAME) — 전부 관리형 TLS 인증서 |
| CD 인증 | OIDC(federated credential) — 장기 시크릿 없음. Azure AD App `vinc-github-actions-deploy`(`7e9c111d-...`), `Vinclums/Vinc`·`Vinclums/Vinc-homepage` 양쪽 main 브랜치에 각각 federated credential 등록, RBAC는 **`vincsRG` 범위로 한정**(Contributor) |
| 상세 인벤토리 | `Vinc-docs/vinc/AZURE-ARCHITECTURE.md` |
| 비용·예산 | `Vinc-docs/vinc/AZURE-COST-MANAGEMENT.md` — Budget `vinc-monthly-budget`(월 $150, 50/75/90%+예측100% 알림) 이미 설정됨 |

**⚠ 왜 VM 이 아니라 Container Apps/MySQL Flexible/Static Web Apps 인가**: 이 구독은
신규 생성 직후라 **Microsoft.Compute(VM) 프로비저닝이 전 세계 모든 리전에서
`NotAvailableForSubscription`으로 막혀 있었다**(quota 자체는 정상 표시, 실제 승인만
안 됨 — 신규 구독 사기방지 정책으로 추정, region access request 티켓 별도 필요). VM
계열이 아닌 다른 리소스 프로바이더(`Microsoft.App`·`Microsoft.DBforMySQL`·
`Microsoft.Web`)는 전부 정상 동작해 이쪽으로 우회했다. **IaaS(VM) 옵션이 원래
1순위였다** — Support 승인이 나면 재검토 여지가 있으나, 지금 이 아키텍처(3종
관리형 PaaS)로 이미 실사용 검증까지 끝났으므로 급하게 바꿀 이유는 없다.

---

## §1 표면 1 — 데스크탑 앱

**정본**: `Vinc/.github/workflows/release.yml`. 3-OS 매트릭스·PyInstaller·Tauri·draft
발행 후 사람이 Publish. Azure 전환과 무관하다.

### 1.5 버전 범프 — 스크립트가 정본 (2026-07-29 신설)

버전이 박히는 자리는 **10곳**(파일 6개)이다. 손으로 세던 시절 실제로 어긋났다 —
`app/package-lock.json` 이 7릴리스(0.6.0→0.6.7) 방치됐다(`Cargo.lock` 은 체크리스트에
있는데 이건 빠진 **비대칭**이 원인, `npm ci` 는 의존성 트리만 대조하므로 조용히 통과).

```bash
python scripts/version_sync.py --check          # 검증(전 자리 일치 확인)
python scripts/version_sync.py --set 0.6.8      # 10곳 일괄 갱신
```

`release.yml` 의 **`version-gate` 잡이 태그와 파일을 대조**한다(`--expect ${태그#v}`) —
어긋나면 빌드 자체가 안 나간다. 이것이 "업데이트 토스트 무한 반복" 함정의 근본 해소다
(`latest.json` 의 version 은 git 태그에서, 클라이언트 자기 버전은 `tauri.conf.json` 에서
온다 — 태그만 올리고 파일을 안 올리면 설치해도 버전이 그대로다).

`CHANGELOG.md` 는 스크립트 대상이 아니다(사람이 쓰는 서술).

#### 1.5.1 ⚠ `ladybug` 버전 핀 — 네 자리가 **전부** 같아야 한다 (2026-08-01 신설)

드라이버 핀이 박히는 자리는 **넷**이다. 하나만 빠져도 조용히 다른 것이 배포된다.

| 자리 | 값 |
|---|---|
| `.github/workflows/ci.yml` | `ladybug==0.18.0` |
| `.github/workflows/hub-deploy.yml` | `ladybug==0.18.0` |
| `.github/workflows/release.yml` | `ladybug==0.18.0` |
| `Dockerfile.hub` | `ladybug==0.18.0` |

**사고(v0.7.0)**: `release.yml` **하나만** 핀이 없었다. 2026-07-30 에 PyPI 에 0.19.0 이
올라오자 그날부터 릴리스만 0.19.0 을 집었고, Windows 빌드가 깨졌다:

```
RuntimeError: Could not find lbug C API shared library.
              Set LBUG_C_API_LIB_PATH or ... LBUG_LIB_KIND=shared
```

0.19.0 이 Windows 휠에서 C API 공유 라이브러리를 분리한 것으로 보인다.

**더 조용했던 쪽**: 핀이 없는 동안 v0.6.7 은 그날의 최신(0.18.3)으로 빌드돼 나갔는데
`ci.yml` 은 0.18.0 으로 테스트하고 있었다 — **테스트한 것과 배포한 것이 달랐다.**
깨지지 않았을 뿐 근거가 없던 상태였고, 핀을 맞추면서 함께 해소됐다.

§1.5 의 `package-lock.json` 비대칭과 **같은 계열**이다: 체크리스트에 있는데 한 자리만
빠지면 그 자리가 조용히 다른 것을 배포한다. 드라이버를 올릴 때는 **네 자리를 같이** 올릴 것.

#### 1.5.2 ⚠ 드라이버 버전 선택은 **두 축에 동시에 묶여 있다** (2026-08-01)

`ladybug` 버전을 고를 때 확인할 것이 둘이고, **하나만 보면 반드시 다른 쪽이 깨진다.**

| 버전 | Windows 휠(C API) | 확장 서버 `extension.ladybugdb.com` |
|---|---|---|
| 0.18.0 | ✅ | ✅ `v0.18.0/{linux_amd64,win_amd64,osx_arm64}` 200 |
| 0.18.3 | ✅ | ❌ **404** — 확장이 없다 |
| 0.19.0 | ❌ C API 공유 라이브러리 분리 | ✅ |

**0.18.0 만이 두 축을 모두 만족한다.** 그래서 이 핀은 임의 선택이 아니다 — 올릴 때는
위 표를 다시 실측할 것(`curl -o /dev/null -w "%{http_code}" https://extension.ladybugdb.com/v<버전>/<플랫폼>/vector/libvector.lbug_extension`).

**그리고 0.18.0 의 내장 다운로더는 쓰지 않는다.** `INSTALL vector` 가 CI 에서
`ERROR: SSL server verification failed` 로 죽는다 — 클라이언트 CA 번들이 낡았다.
같은 URL 이 `curl` 로는 200 이고 바이트도 리포에 커밋된 `packaging/lbug_extensions/`
와 일치한다. `release.yml` 은 그래서 **curl 로 `~/.lbdb/extension/` 를 직접 채운다**.

> 이 함정이 늦게 드러난 이유: 로컬은 캐시(`~/.lbdb/extension/0.18.0/...`)가 예전에
> 채워져 재다운로드가 없었고, CI 는 핀이 없어 0.19.0 을 쓰고 있었다. **핀을 맞추는
> 순간** 처음으로 0.18.0 이 fresh download 를 시도해 드러났다.

#### 1.5.3 `::error::` 를 뱉는 스크립트는 테스트에서 stderr 를 가둘 것

`scripts/check_hub_env.py` 는 누락을 `::error::` 로 찍는다(GitHub 워크플로 명령).
그 스크립트를 **일부러 실패시키는 테스트**가 stderr 를 흘리면, 테스트가 통과해도
잡 주석에 빨간 오류가 상시로 뜬다(실측: 매 CI 실행마다 "10 errors").

상시 빨강은 사람이 진짜 오류를 무시하게 만든다 — 이 리포가 반복한 "조용히 나빠지는"
사고와 같은 계열이다. `contextlib.redirect_stderr` 로 가두고, 이왕 잡은 김에
**메시지가 그 키를 지목하는지**까지 검증할 것(`tests/test_check_hub_env.py` 가 본보기).

### 1.6 ⚠ 업데이터 서명 키 — 단일 실패점, 백업 필수

| 서명 | 목적 | 상태 |
|---|---|---|
| **업데이터 서명**(minisign/Ed25519) | 업데이트 번들 진위 검증 | **적용 중** |
| **OS 코드서명**(Authenticode/공증) | SmartScreen·Gatekeeper 경고 제거 | **미적용** — 결정 대기(아래) |

**키를 잃으면 복구 경로가 없다.** 공개키가 `tauri.conf.json` 에 박혀 있어 클라이언트가
서명 불일치 번들을 거부한다. 새 키로 바꾸면 그 pubkey 는 **새 설치본에만** 들어가므로
**이미 배포된 전 사용자가 자동 업데이트에서 영구 이탈**하고 수동 재설치 외에 길이 없다.

- 키 파일: `vinc-updater-signing.key`(개인, 348B) + `.key.pub`(공개, 152B) — 워크스페이스
  루트(리포 **바깥**). 세 리포 전부 커밋 이력 0건(`git log --diff-filter=A` 실측).
- `.gitignore` 에 `*.key`·`*.pem`·`*.p12`·`*.pfx`(Vinc `:21`) — 누가 리포 안으로 옮겨도
  커밋되지 않는다.
- **암호는 빈 문자열이다(2026-07-30 실측).** `tauri signer sign -p ""` 로 서명이 성공한다.
  파일 헤더가 `rsign encrypted secret key` 라 암호가 걸린 것처럼 보이지만 **실질 보호는
  0** 이다. 따라서 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` 시크릿도 빈 값이고, **복구해야 할
  암호가 없다**(잊은 게 아니라 처음부터 안 걸었다).

**두 시크릿의 값이 무엇이어야 하는가** — GitHub 시크릿은 생성 후 **조회 경로가 없다**
(덮어쓰기만 가능). 그래서 여기 적어 둔다:
| 시크릿 | 값 |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | `vinc-updater-signing.key` **파일 내용 그대로**(348자 base64) |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | **빈 문자열** |

#### 위험 모델 (2026-07-30 정정)

이전 서술("백업이 로컬 파일 하나뿐, 이 머신이 죽으면 복구 불가")은 **부정확했다.**
사본은 둘이다 — 로컬 파일 + GitHub 시크릿. 시크릿은 읽어올 수 없지만 CI 가 **쓸 수는
있다**. 그래서 실제 시나리오는 이렇다:

| 잃는 것 | 결과 |
|---|---|
| 로컬 파일만 | **릴리스는 계속 된다**(CI 가 서명). 단 다른 CI·리포로 이전하거나 로컬 서명은 영구 불가 |
| GitHub 시크릿만 | 로컬 파일로 복구 |
| **둘 다** | 영구 상실. 공개키가 `tauri.conf.json` 에 박혀 있어 클라이언트가 다른 키의 번들을 거부하므로, **이미 배포된 전 사용자가 자동 업데이트에서 영구 이탈**하고 수동 재설치 외에 길이 없다 |

즉 즉시 사망이 아니라 **"다시는 어디에도 키를 넣을 수 없게 되는" 상태**가 된다.

> **⚠ 미완 조치**: 파일 2개를 **암호 관리자에 첨부**해 오프사이트 보관할 것. USB·클라우드
> 폴더에 평문으로 두면 안 된다 — 키에 암호가 없으므로 **파일 자체가 곧 시크릿**이고, 이
> 파일을 가진 사람은 업데이트에 서명해 **모든 설치본에 자동 설치**시킬 수 있다. 암호
> 관리자는 저장이 암호화되므로 빈 암호를 보완한다.
>
> 키에 암호를 새로 거는 것은 Tauri CLI 로 불가하다(`sign`·`generate` 뿐 — 실측). 별도
> 도구(minisign/rsign)가 필요하고, 공개키는 안 바뀌므로 사용자 영향은 없지만 GitHub
> 시크릿 두 개를 함께 갱신해야 한다. 암호 관리자 보관이 되면 이득이 크지 않아 우선순위
> 낮음으로 둔다.

### 1.7 OS 코드서명 — **v1.0.0 까지 미서명 유지** (발주자 결정 2026-07-30)

**결정: 안 B(미서명 유지)를 v1.0.0 까지.** 기한 없는 보류가 아니라 **시점이 박힌 결정**
이다 — 1.0.0 시점에 재검토한다.

배경: DEC D44 는 "리포 public 전환 → Windows = SignPath Foundation 무료" 를 확정했으나
**실측 결과 `Vinclums/Vinc` 는 비공개**(GitHub API 404)라 전제가 성립하지 않았다.
SignPath Foundation 은 공개 OSS 리포만 지원한다. Azure Trusted Signing(월 $9.99)은
**미국·캐나다 개인 또는 EU·영국 조직**만 자격이 있어 한국에서는 배제된다. 선택지 3개의
비교는 `DEPLOY-PIPELINE-RESEARCH.md` §1.1.

**D44 는 이 결정으로 대체된다** — "public 전환 → 무료 서명" 경로는 채택하지 않는다.
소스 전면 공개는 되돌릴 수 없는 사업 판단이고, 서명 하나를 위해 1.0.0 전에 감행할
사안이 아니라고 판단했다.

**카피는 고치지 않는다.** 다운로드 페이지는 이미 정직하다 — "아직 코드 서명 전이라 처음
실행 시 경고할 수 있습니다, 안전합니다" + 첫 실행 안내(SmartScreen·Gatekeeper)가 EN·ko
양쪽에 있다. 유일한 미래 서술인 `winFoot`("서명된 빌드는 후속 마일스톤에서 제공됩니다")은
**이제 실제 결정이 뒷받침한다.** 연구 문서가 이 줄을 "모순"으로 지적했던 것은 뒤에 결정이
없어서였고, 그 조건이 해소됐다. 버전 번호를 카피에 박지는 않는다 — 공개 약속을 특정
버전에 묶을 이유가 없다.

**1.0.0 재검토 시 확인할 것**: ① 그때도 리포 비공개 방침인가(A 가 되살아나는 유일한 조건)
② 유료 OV/EV(연 $200~350 + macOS 별도 $99/yr)를 감당할 단계인가 ③ Azure Artifact
Signing 의 지역 자격이 확대됐는가.

---

## §2 표면 2 — 정적 사이트 + 웹 Vinc (Azure Static Web Apps)

**리소스**: `vinc-site`(Standard tier, East Asia). **SKU 가 Standard 인 이유**: Free
tier 는 Linked Backend(§2.2)를 지원하지 않는다 — Free 로는 `/api/*` 를 계정 백엔드로
자동 프록시할 수 없어 프론트가 크로스 오리진 크레덴셜 요청을 해야 하는데, 계정
백엔드 세션 쿠키에 `domain` 속성이 없어(§3 참조) 서브도메인 간 공유가 안 되고 CORS
미들웨어도 전무해 그 경로는 즉시 깨진다(실측: `/api/me` 404). Standard(+월 $9)로
Linked Backend 를 쓰는 쪽이 코드 변경 0으로 원래 동작(같은 오리진)을 재현한다.

### 2.1 CD — `Vinc-homepage/.github/workflows/site-deploy.yml`

```
site/** 변경 push(main)
  └─ Vinc-homepage 체크아웃(site/)
  └─ Vinclums/Vinc 체크아웃(app/, VINC_REPO_PAT 로 cross-repo read)
  └─ app/ 를 VITE_BASE=/app/ 로 빌드
  └─ site/* → deploy/, app/dist/* → deploy/app/ 로 병합
  └─ Azure/static-web-apps-deploy@v1 로 업로드
```

**cross-repo 체크아웃이 필요한 이유**: 웹 Vinc(`app/`)의 정본 소스가 데스크탑과 같은
`Vinc/app/`(Vite, `VITE_BASE` 만 다름)에 있고, 정적 사이트(`site/`)는 `Vinc-homepage`
레포에 있다. 사람이 수동 배포하던 시절에도 로컬에 두 레포를 다 갖고 있다가 병합해서
올렸다 — 이 워크플로는 그 수작업을 그대로 자동화한 것뿐이다.

**필요 리포지토리 시크릿**(`Vinc-homepage`, 사람이 GitHub 웹에서 등록 — `gh` CLI
미설치 환경이라 CLI 로 대신 등록 못 함):

| 이름 | 값 | 비고 |
|---|---|---|
| `AZURE_CLIENT_ID`·`AZURE_TENANT_ID`·`AZURE_SUBSCRIPTION_ID` | §0 표 참조 | OIDC |
| `VINC_REPO_PAT` | `Vinclums/Vinc` 에 대한 **read-only** 신규 PAT | ⚠ 기존 `RELEASE_TOKEN`(Vincs-app 에 대한 contents:write)과 **용도·범위가 다르다 — 재사용 금지** |

> **SWA 배포 토큰은 시크릿으로 두지 않는다.** 119자라 사람이 복사하다 잘리기 쉽고
> (실제로 그렇게 잘려 `deployment_token provided was invalid` 로 두 번 실패했다 —
> Azure 원본 토큰은 정상이었고 같은 값으로 수동 배포는 성공했었다), 토큰이 회전되면
> 시크릿도 같이 갱신해야 하는 이중 관리가 생긴다. 워크플로가 OIDC 로그인 후
> `az staticwebapp secrets list` 로 **런타임에 직접 조회**하고 `::add-mask::` 로
> 로그를 가린다.

### 2.2 Linked Backend — `/api/*` 프록시 계약

`az staticwebapp backends link`(`vinc-site` ↔ `vinc-account`, `koreacentral`)로 연결.
브라우저 관점에서 `vincs.io/api/*` 요청이 자동으로 계정 백엔드 Container App 으로
프록시되어 **같은 오리진처럼 보인다** — 세션 쿠키·CSRF·CORS 코드를 하나도 안 건드려도
로그인이 그대로 동작하는 근거가 이것이다. Linked Backend 는 **정적 사이트 리소스에
딸린 설정**이라 재배포(`site-deploy.yml`)해도 유지된다(별도 재연결 불필요).

> **⚠ 부수 효과 — 계정 백엔드는 이제 공개 도달되지 않는다(실측 2026-07-29).**
> 링크를 걸면 Azure 가 그 Container App 에 **내장 인증(EasyAuth)을 자동으로 켠다**.
> 컨테이너 FQDN 을 직접 부르면 `401` + `www-authenticate: Bearer` 이고, SWA 를
> 거친 요청만 통과한다. 보안상 이득이지만(가비아 시절 Caddy 가 하던 경계를 되찾은
> 셈) **헬스체크·모니터링이 직접 FQDN 을 때리고 있으면 조용히 깨진다** — 실제로
> `account-deploy.yml` 의 검증 스텝이 이 때문에 실패했고, SWA 호스트명 경유로
> 바꿔 고쳤다.
>
> 대비: **허브(`vinc-hub`)는 SWA 에 링크돼 있지 않아** 이 보호를 못 받는다 —
> `mcp.vincs.io/internal/*` 가 여전히 공개 도달 가능하고 Ed25519 서명만이 관문이다
> (`DEPLOY-PIPELINE-RESEARCH.md` §4 참조).

### 2.3 캐시 정책

SWA 는 정적 자산에 자체 CDN 캐시 정책을 적용한다(파일 해시 기반 자동 무효화) — 가비아
시절 Caddy 의 수동 `no-cache`/`immutable` 구분이 더 이상 필요 없다.

---

## §3 표면 3 — 계정 백엔드 (Azure Container Apps)

Node 22 + Hono + mysql2. **리소스**: `vinc-account`(0.25 vCPU/0.5Gi, `min=max=1`).

### 3.1 컨테이너화 — `Vinc-homepage/account/Dockerfile`

Node 22-slim 멀티스테이지. **급소 2가지**(코드 실측으로 확인됐던 것):
1. `emails/` 는 `dist/` 의 **형제 디렉토리**여야 한다(`mailer.ts` 가
   `fileURLToPath(import.meta.url)` 기준 `../../emails` 상대경로로 참조 — VM 배포
   시절과 동일 제약, 컨테이너 COPY 순서에서도 유지).
2. `env.ts` 의 `host` 를 `process.env.HOST ?? "127.0.0.1"` 로 변경(원래 하드코딩
   127.0.0.1 이었음) — Container Apps ingress 는 컨테이너 네트워크 네임스페이스
   **밖**에서 붙으므로 `0.0.0.0` 청취가 필요하다. Dockerfile 에서 `ENV HOST=0.0.0.0`.

### 3.2 CD — `Vinc-homepage/.github/workflows/account-deploy.yml`

```
account/** 변경 push(main)
  └─ test(typecheck+vitest, account-ci.yml 과 별개로 재실행 — 머지 커밋 자체는
     PR 게이트를 다시 안 타므로 배포 직전 한 번 더 검증)
  └─ az acr build (ACR Tasks, 클라우드 빌드 — 러너에 Docker 데몬 불요)
  └─ az containerapp update --image (태그만 갱신, 나머지 설정 유지)
  └─ /api/health curl 검증
```

**필요 리포지토리 시크릿**(`Vinc-homepage`): `AZURE_CLIENT_ID`·`AZURE_TENANT_ID`·
`AZURE_SUBSCRIPTION_ID`(OIDC, 값은 §0 표 참조 — 시크릿 자체가 아니라 식별자지만
관례상 Secrets 로 등록).

### 3.3 DB 연결 · 시크릿

`vinc_acct_app`(DML 전용, host `%` — 관리형 MySQL 방화벽이 Azure 서비스 IP 전체를
1차 필터링하므로 유저 host 제한은 방화벽 다음 층일 뿐) 계정으로 연결. 런타임 시크릿
(`DB_PASSWORD`·`SERVER_PEPPER`·`SESSION_SECRET`·`CSRF_SECRET`)은 Container App 의
`secrets`(Azure 관리, 평문 노출 없음)에 저장 — 가비아 시절 `/etc/vinc-account/env`
(0600 파일)의 대체.

**⚠ 알려진 제약(변경 안 함, 리스크로만 기록)**: 세션 쿠키에 `domain` 속성이 없다
(`account/src/lib/cookies.ts`) — Linked Backend(같은 오리진) 전제라 지금은 문제
없지만, 만약 나중에 `api.vincs.io` 를 직접 호출하는 별도 클라이언트(예: 모바일 앱)가
생기면 그 경로에선 세션 쿠키가 안 먹는다. CORS 미들웨어도 전무 — 같은 이유로 지금은
불필요.

---

## §4 표면 4 — 허브(사이드카+MCP) (Azure Container Apps + Azure Files)

**리소스**: `vinc-hub`(0.5 vCPU/1Gi, `min=max=1` — 인메모리 OAuth/세션 상태 때문에
스케일다운 불가, 상시 idle 과금이 발생한다는 뜻이기도 함→ 비용 문서 참조).

### 4.1 컨테이너화 — `Vinc/Dockerfile.hub`

데스크탑(PyInstaller+Tauri)과 **완전히 다른 빌드 경로**다. Docker 컨테이너는
정적분석 사각(동적 import) 문제와 무관하므로 PyInstaller 프리징 자체가 불필요 —
그냥 `python -m api.cli`로 직접 실행한다.

**드라이버 버전 핀**: `ladybug==0.18.0`(pyproject.toml 주석 근거와 동일).

**G0 확장 시드 — 실측으로 확정된 방식(중요, 재현 시 반드시 따를 것)**:
- ❌ 빌드 컨테이너 안에서 `INSTALL vector/fts/algo` 실행 → **SSL 검증 실패로 다운로드
  불가**(Docker Desktop 빌드 환경 특유 문제로 실측 확인 — 로컬 curl 로는 같은 URL 이
  정상 응답해 서버/네트워크 문제가 아니라 빌드 컨테이너 국한 문제였다).
- ✅ `packaging/lbug_extensions/{lib{vector,fts,algo}.lbug_extension}` 을 **로컬에서
  미리 받아 레포에 커밋**하고, Dockerfile 이 `ENV VINC_LBUG_EXT_DIR=/srv/vinc/lbug_extensions`
  로 명시 경로 로드시킨다(`store/embedded_backend.py` `_bundled_ext_path` ②번 탐색
  경로 — PyInstaller frozen 전용이 아니라 이 env 오버라이드도 지원한다는 게 이번에
  확인된 사실). **평평한 디렉토리 구조 필수**(`envdir/lib{ext}.lbug_extension` —
  서브폴더로 나누면 매칭 안 됨, 실제로 한 번 이 실수로 fts 만 안 먹혔었다).

**데이터 영속성 — Azure Files 볼륨**: Container Apps Environment(`vinc-cae`)에
Azure Files 스토리지(`hub-storage` → Storage Account `vincstorage01`, 공유
`hub-data`)를 등록하고, 컨테이너의 `/data` 에 마운트(`VINC_DATA_DIR=/data/db`).
**실측 검증**: 컨테이너 재시작(revision restart) 후에도 그래프 데이터가 그대로
남아있음을 실제로 확인(쓰기→검색→재시작→같은 데이터 재검색 성공).

### 4.2 CD — `Vinc/.github/workflows/hub-deploy.yml`

```
api/·store/·engines/·vinc_mcp/·Dockerfile.hub·pyproject.toml·scripts/check_hub_env.py
·packaging/{docker-entrypoint.sh,embed_worker.py,bake_embed_model.py,lbug_extensions/**}
변경 push(main)
  └─ az acr build (ACR Tasks)
  └─ az containerapp update --image
  └─ MCP tools/list curl 검증(정적 bearer 토큰)
```

⚠ `packaging/` 는 **전체가 아니라 열거된 파일만** 트리거다(위는 워크플로 실측). 여기에
`packaging/version_info.txt` 는 없다 — 데스크탑 버전 범프가 허브를 재배포하지 않는
것은 의도된 것이다. 반대로 **새 빌드 스크립트를 `packaging/` 에 추가하면 트리거 목록에
같이 등재**해야 한다. 빠지면 그 파일을 고쳐도 재배포가 안 걸린다.

### 4.2.1 ⚠ ACR Tasks 는 Dockerfile heredoc 을 다루지 못한다 (2026-08-01 신설)

로컬 `docker build` 는 통과하는데 ACR 에서만 터지는 함정이 **둘** 있다. 실측으로 둘 다 겪었다.

1. **빌드 전 "dependency scanning"** 단계가 Dockerfile 을 자체 파서로 훑는데 heredoc
   본문을 구분하지 못한다. 본문의 `from X import Y` 를 대소문자 무시한 `FROM` 지시자로
   읽고 `unable to understand line from ...` 으로 **3초 만에 죽는다**.
2. 그걸 피해도 **heredoc 자체가 안 먹는다.** 클래식 빌더에서
   `RUN a && python - <<'PY' && b` 는 줄 이음으로 한 줄이 된 뒤 셸이 본문을 받지 못해
   `python -` 이 **빈 stdin 으로 즉시 종료**한다. → **빌드는 초록인데 산출물이 없다.**

②가 더 위험하다. 실제로 e5 모델 굽기가 통째로 건너뛰어졌는데 워크플로는 성공이었고,
드러난 지점은 **이미지 크기(120.8MB → 126MB, 정상은 346MB)** 뿐이었다. 런타임에는
fp32(1GB) 폴백으로 OOM 이 나 MCP 까지 죽는 결말이었다.

**규칙**: Dockerfile 에 인라인 스크립트를 heredoc 으로 넣지 말 것. **별도 파일로 COPY 후
실행**하고, 그 스크립트가 **자기 산출물을 검증해 실패시킬 것**
(`packaging/bake_embed_model.py` 가 그 본보기 — 200MB 미만이면 빌드를 세운다).

**그리고 배포 검증에 산출물 자체의 지표(크기·개수)를 하나는 넣을 것.** 워크플로 초록은
산출물을 보지 않는다.

release.yml(데스크탑)과 **독립된 파이프라인**이다 — 서버 배포가 데스크탑 릴리스에
얽매이지 않는다(가비아 시절 원칙 "데스크탑과 소스는 같지만 배포는 완전히 분리"를
CI 레벨에서도 승계).

**⚠ 저장 포맷이 바뀌는 변경**: 이 워크플로는 이미지 교체만 한다 — 그래프 DB 스키마
자체가 바뀌는 변경(예: 드라이버 마이그레이션)이면 자동 CD 로 내보내지 말고
`workflow_dispatch` 로 수동 트리거하며, 배포 전 별도로 데이터 이관 절차를 밟을 것
(가비아 시절 §4.4 사고 사례 — kuzu→ladybug 전환 시 자동 배포했다가 기존 데이터
포맷 불일치로 장애 — 의 재발 방지 원칙을 그대로 승계).

### 4.3 ⚠ 임베디드 DB 단일 writer — 롤링 업데이트가 구조적으로 실패한다

**Container Apps 의 기본 배포 방식(새 리비전을 띄운 뒤 구 리비전을 내림)이 이 앱에는
그대로 통하지 않는다.** 두 리비전이 같은 Azure Files 볼륨을 물고, LadybugDB 는 **배타
파일 락**을 쓴다. 그래서 새 리비전의 사이드카가 스토어를 못 열고(`"driver": null`)
entrypoint 헬스체크가 타임아웃 → 리비전 `Failed`.

**실측(2026-07-29)**: 2차 배포에서 정확히 이 증상. 구 리비전을 `revision deactivate`
해 락을 놓게 하자 새 리비전이 `Failed` → `RunningAtMaxScale` 로 **자동 회복**했다.
1차 배포는 선행 리비전이 없어 우연히 통과했던 것 — 즉 **첫 배포만 되고 그 뒤로는
전부 실패하는** 함정이었다.

`hub-deploy.yml` 이 이 순서를 강제한다:
```
이미지 갱신 → 구 리비전 전부 deactivate(락 해제) → 새 리비전 Running 확인
```
단일 writer 라 **배포 중 짧은 다운타임은 불가피**하다(계정 백엔드는 무상태라 무관).

> 손으로 배포할 때도 같은 순서를 지킬 것. `az containerapp update` 만 하고 끝내면
> 새 리비전이 조용히 Failed 로 남고 구 리비전이 계속 서빙한다 — **배포가 안 됐는데
> 서비스는 정상**이라 알아채기 어렵다.

**필요 리포지토리 시크릿**(`Vinc`): `AZURE_CLIENT_ID`·`AZURE_TENANT_ID`·
`AZURE_SUBSCRIPTION_ID`(OIDC) · `VINC_MCP_TOKEN`(배포 후 헬스 검증용 정적 bearer,
현재 프로덕션 컨테이너 앱의 시크릿 값과 동일해야 함).

### 4.4 ⚠ 허브 런타임 env — 정본 목록 (2026-07-30 신설)

**이 목록은 어디에도 코드화돼 있지 않다.** `az containerapp create/update` 로 손으로
넣은 값이고 IaC 도 워크플로도 이걸 검증하지 않는다. 그래서 플랫폼을 옮기거나 앱을
재생성하면 **조용히 빠진다** — 아래 4.4.1 이 실제로 그렇게 터진 사고다.

| env | 필수 | 없으면 |
|---|---|---|
| `VINC_SIDECAR_TOKEN` | ✅ | 엔트리포인트가 즉시 종료(`:?` 가드) |
| `VINC_DATA_DIR` | ✅ | 기본 `/data/db`. Azure Files 마운트 지점과 반드시 일치 |
| `VINC_MCP_TOKEN` | ✅ | 정적 bearer 없음 → CD 헬스검증 불가 |
| **`VINC_MCP_ISSUER`** | **✅** | **OAuth 전체가 꺼진다. 아래 4.4.1** |
| `VINC_OAUTH_ADMIN_KEY` | ✅ | 부트스트랩 자동승인(`/authorize?key=`) 불가 |
| `VINC_MCP_ED25519_SEED` | ✅ | CRA 서명 불가 → 회원 승인 흐름 비활성 |
| `VINC_ACCOUNT_PUBLIC_KEY` | ✅ | AA 검증 불가 → 회원 승인 흐름 비활성 |
| `VINC_CONNECT_URL` | ✅ | 코드 기본값은 `https://vincs.io/connect`. **env 로 덮어쓸 때 도메인 확인 필수** |
| `VINC_QUOTA` | 정책 | `on` 이 아니면 **툴 호출 무제한**. 사이트는 "무료 15회/일"로 안내 중이라 어긋난다 |
| `VINC_EMBED` | 선택 | 기본 `on`. `off` 면 임베딩 워커 미기동 → 신규 문서가 FTS 만 되고 벡터가 빈다(§4.5) |
| `VINC_EMBED_INTERVAL` | 선택 | 기본 `300`(초). pending 이 빌 때 대기 |
| `VINC_EMBED_CACHE` | 선택 | 기본 `/data/embed-model`. **영속 볼륨이어야** 재시작마다 440MB 재다운로드를 피한다 |

회원 승인 흐름(B-B3)은 **네 가지가 전부 있어야** 켜진다
(`http_server.py` `member_connect_enabled()`): `oauth` 게이트 + `mcp_seed` +
`account_public` + `connect_url`. 하나만 빠져도 관리키 경로로 조용히 강등된다.

#### 4.4.1 사고 — `VINC_MCP_ISSUER` 누락으로 커넥터 전면 불가 (2026-07-30)

**증상**: claude.ai 커넥터가 `https://mcp.vincs.io/mcp` 에 "연결할 수 없습니다".
그런데 `/health` 는 200 `{"status":"ok"}` — **서버는 멀쩡해 보였다.**

**연쇄**:
```
VINC_MCP_ISSUER 없음
  → docker-entrypoint.sh 가 --oauth 를 안 붙임   (env 있을 때만 붙이는 조건부)
  → api/cli.py 에서 gate = None
  → /.well-known/oauth-protected-resource = 404 {"error":"oauth disabled"}
  → _issuer() 가 http://{Host} 로 폴백
  → 401 의 WWW-Authenticate 가 http:// 를 가리킴(https 아님)
  → 클라이언트 discovery 실패
```

**교훈**: 허브의 OAuth 는 **env 하나로 통째로 켜지고 꺼진다**. 그리고 꺼진 상태가
`/health` 에 전혀 드러나지 않는다. `/health` 200 은 OAuth 가 살아있다는 증거가
**아니다**.

같은 점검에서 `VINC_CONNECT_URL` 이 **삭제된 도메인 `https://vincs.app/connect`**
를 가리키고 있던 것도 함께 발견해 고쳤다(도메인 이전 때 놓친 잔재).

#### 4.4.2 배포·재생성 후 반드시 이 4줄을 실측할 것

`/health` 만 보고 끝내지 말 것.

```bash
curl -s https://mcp.vincs.io/health                                   # {"status":"ok"}
curl -s https://mcp.vincs.io/.well-known/oauth-protected-resource     # 200, 404 아님
curl -sI -X POST https://mcp.vincs.io/mcp | grep -i www-authenticate  # https:// 여야 함
curl -s "https://mcp.vincs.io/authorize?...&client_id=..." -i | grep -i location  # vincs.io/connect
```

#### 4.4.3 시크릿 평문 저장 → secretRef 이관·회전 완료 (2026-07-30)

`VINC_MCP_ED25519_SEED`(허브 **서명 개인키**)와 `VINC_OAUTH_ADMIN_KEY` 가
`secretRef` 가 아니라 `value` 로 저장돼 있었다. `VINC_SIDECAR_TOKEN`·`VINC_MCP_TOKEN`
은 제대로 `secretRef` 를 쓰고 있어 **같은 앱 안에서 취급이 갈렸다**. 계정 백엔드는
자기 `ACCOUNT_ED25519_SEED` 를 `secretRef` 로 두고 있으므로 규율은 허브만 어긋나
있었다. 컨테이너 앱에 Reader 권한만 있어도 `az containerapp show` 로 평문 조회됐다.

유출된 서명키의 실제 위험: 그 키로 **위조 CRA 를 서명**할 수 있고, 그러면 동의 화면이
공격자가 정한 `client_name`·`redirect_uri` 를 "서버가 검증한" 값으로 표시한다
(`connect.ts` 의 describe 가 존재하는 이유가 바로 그 위조 방어다). 그래서 이관만 하지
않고 **키를 새로 발급해 회전**했다.

⚠ **회전은 양쪽 동시 변경이다.** 허브 시드는 계정이 `MCP_PUBLIC_KEY` 로 핀한 공개키로
파생된다(실측 확인). 한쪽만 바꾸면 커넥터 승인이 CRA 검증 실패로 죽는다.
**허브를 먼저** 끝내고 계정을 나중에 바꿀 것 — 허브가 단일 writer 라 리비전 수렴이
느리므로 반대로 하면 불일치 창이 3배 길다. 허브가 안 뜨면 계정을 건드리지 말 것:
그러면 기존 키 조합이 그대로 유효해 롤백이 불필요하다.

**검증 방법(세션 없이 가능)** — 새 키로 실제 서명하는지는 CRA 서명을 직접 검증한다.
`/register` → `/authorize` 로 CRA 를 받아 `vinc_mcp.edverify.verify_assertion` 으로
신·구 공개키 양쪽에 대해 돌린다. 실측 결과:
```
NEW key -> VERIFIED  typ=cra iss=mcp aud=account
OLD key -> rejected
```
**구 키가 거부되는 것까지 봐야 한다** — 신 키만 확인하면 무효 회전(값이 안 바뀐 경우)을
통과로 오독한다.

> ⚠ PowerShell 회전 스크립트를 쓸 때: 이 기기엔 **Windows PowerShell 5.1 뿐**이다
> (`pwsh` 없음). 5.1 은 BOM 없는 `.ps1` 을 cp949 로 읽어 한국어 주석이 깨지고 그
> 깨진 바이트가 따옴표·중괄호로 오인돼 **구문 오류로 죽는다**(실측). 스크립트는
> **UTF-8 BOM** 으로 저장할 것. `Invoke-WebRequest -SkipHttpErrorCheck` 도 6+ 전용이라
> 5.1 에서는 `curl.exe` 를 쓴다.

### 4.5 임베딩 워커 — 이관 때 사라졌다가 복구됨 (2026-07-30)

가비아 시절엔 **systemd timer**(`vinc-embed-worker.timer`, 5분 간격)가 별도 venv 의
sentence-transformers 를 돌려 `needs_embed` 문서의 벡터를 채웠다. Azure 이관 때 그
timer 가 없어졌고 허브 컨테이너에는 embed 언급이 **0건**이었다. 결과: 웹 편집·팩
임포트·파일 드롭으로 들어온 문서가 전부 FTS 만 되고 벡터가 비었다 — **에러 없이
의미검색만 열화**되므로 알아챌 신호가 없었다.

지금은 **컨테이너 안에서** 엔트리포인트가 사이드카 헬스 뒤에 워커를 띄운다
(`packaging/embed_worker.py`). 순서가 뒤바뀌면 첫 라운드가 무의미하게 실패한다.

**torch 가 아니라 ONNX 인 이유(측정 근거)**:
- 허브 실측 `WorkingSetBytes` = **115MiB / 1024MiB**. 여유는 있지만 torch 는
  ~800MiB RSS 라 한도에 붙고 **OOM 은 컨테이너 전체(=MCP)를 죽인다**.
- 피하려 1.0vCPU/2GiB 로 올리면 상시 과금이 배증한다(§비용 문서의 "확정 발생" 항목).
  ONNX 는 ~660MiB 로 끝나 증설이 불필요하다. 이미지 260MB→500MB(torch 면 +2.7GB).

⚠ **벡터 공간 동일성이 이 전환의 전부다.** 기존 벡터는 sentence-transformers 산이고
새 벡터가 같은 색인에 섞인다 — 어긋나면 에러 없이 검색만 나빠진다. 폐기 컨테이너에서
코사인 대조로 검증했고(**최악 0.99999994**, 512토큰 초과·빈 문자열 포함) 모델은 BAAI
공식 export, 풀링은 모델 리포의 `1_Pooling/config.json` 근거다(추측 아님).
**임베딩 파이프라인을 건드릴 때 그 검증을 다시 돌릴 것** — 절차는
`packaging/embed_worker.py` 주석.

**워커가 죽어도 컨테이너를 내리지 않는다.** 부가 기능인데 재시작을 걸면 단일 writer
락 회수까지 얽혀 MCP 가용성이 배경 작업의 인질이 된다. `VINC_EMBED=off` 로 재빌드
없이 물러설 수 있다.

---

## §5 DB — Azure Database for MySQL Flexible Server

**리소스**: `vinc-mysql01`(Burstable B1ms, 20GB Premium_LRS, `autoGrow: Enabled`,
MySQL 8.0.46-azure). 관리형 자동 백업 매일 1회·7일 보관(`geoRedundantBackup:
Disabled`).

### 5.1 계정 2종 (가비아 시절과 동일한 최소권한 원칙)

- `vinc_acct_app`(host `%`, DML 전용) — 런타임 앱이 사용.
- `vinc_acct_ddl`(host `%`, ALL PRIVILEGES on `vinc_account.*`) — 마이그레이션
  전용, 실제로는 admin(`vincadmin`)으로 최초 부트스트랩만 하고 이후는 이 계정으로
  적용하는 것을 권장(아직 첫 마이그레이션은 admin 으로 실행함 — 최소권한 정합은
  다음 마이그레이션부터 맞출 것).

가비아 시절엔 관리형 DB가 `CREATE USER` 를 막아 어쩔 수 없이 단일 계정(`vinc`)을
썼지만, Azure MySQL Flexible Server 의 admin 은 `CREATE USER`/`GRANT` 가 가능해
**원래 설계(계획서 트랙 1-5)대로 계정을 분리할 수 있었다** — 이번에 실제로 분리
적용함.

### 5.2 마이그레이션 — 여전히 수동(dbmate 는 여기서도 안 씀)

`account/db/bootstrap/00_create_schema.sql`·`01_create_users_grants.sql`(단,
host 를 `192.168.0.%`→`%` 로 조정 — 가비아 사설망 전제가 더 이상 안 맞는다) +
`account/db/migrations/*.sql` 의 `-- migrate:up` 섹션을 순서대로 실행하고
`schema_migrations` 테이블에 파일명(확장자 제외)을 수기 기록. **dbmate 바이너리는
여전히 설치돼 있지 않다** — 문서(README)와 실제 관행의 괴리는 가비아 시절과 동일.

적용 여부는 추론 말고 조회:
```sql
SELECT version FROM schema_migrations ORDER BY version;
```

### 5.3 ⚠ `storage.autoGrow: Enabled` — 비용 문서 필독

스토리지가 임계치를 넘으면 **자동으로 커지고, 한 번 커지면 다시 줄일 수 없다**(Azure
MySQL Flexible Server 공통 특성). 상세 위험도와 방어 조치는
`AZURE-COST-MANAGEMENT.md` §MySQL 참조.

---

## §6 CI/CD 공통 — OIDC 인증 (재현 절차)

이번에 처음 구축. 다른 레포/새 서비스에 CD 를 추가할 때 재사용할 절차:

```bash
# 1) Azure AD App + Service Principal (1회, 이미 완료)
az ad app create --display-name "vinc-github-actions-deploy"
az ad sp create --id <appId>
az role assignment create --assignee <appId> --role Contributor \
  --scope /subscriptions/<sub>/resourceGroups/vincsRG   # 구독 전체가 아니라 RG 한정

# 2) 레포별 federated credential (레포 추가 시 반복)
az ad app federated-credential create --id <objectId> --parameters '{
  "name": "<repo>-main-branch",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:Vinclums/<repo>:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

워크플로 쪽은 `azure/login@v2` + `id-token: write` 권한만 있으면 끝(`az acr build`·
`az containerapp update` 등 이후 CLI 호출은 그 세션을 그대로 쓴다). **장기 시크릿
(client secret)이 전혀 없다** — 유출 위험 자체가 구조적으로 없음.

**⚠ `defaults.run.working-directory` 는 워크플로 전체에 걸린다**(실측 함정):
`account-deploy.yml` 이 최상단에 `working-directory: account` 를 뒀더니 test job 뿐
아니라 build-and-deploy job 까지 `account/` 에서 실행돼 `az acr build ./account` 가
`account/account` 를 찾다 실패했다 — ACR 빌드 이력에 흔적조차 안 남아 원인 파악이
늦었다. **job 단위로 한정**할 것.

**`Azure/static-web-apps-deploy@v1` 에 `skip_api_build` 입력은 없다**(실측: 워크플로가
`Unexpected input(s)` 경고를 냈다). API 빌드를 끄려면 `api_location: ""` 을 명시한다.

**Git Bash 함정(로컬 재현 시)**: `/subscriptions/...` 로 시작하는 리소스 ID 를 Bash
도구에 넘기면 MSYS 가 이를 Windows 경로로 오인해 `D:/git/subscriptions/...` 로
변환해버린다(실제로 이 때문에 `linkedBackends` 호출이 404 났었다) — 로컬에서
`az` 명령에 리소스 ID 문자열을 직접 넘길 땐 `MSYS_NO_PATHCONV=1` 접두. GitHub Actions
러너(순수 Linux)에서는 이 문제가 없다.

---

## §7 교차 표면 순서 제약

### 7.1 스키마가 바뀌는 계정 변경
```
마이그레이션 적용(DB) → account-deploy.yml 트리거(코드 배포) → (필요 시) site-deploy.yml
```
가비아 시절과 동일한 순서 원칙 — 스키마가 코드보다 먼저여야 한다.

### 7.2 허브 저장 포맷 변경
```
데이터 이관 절차(수동) → workflow_dispatch 로 hub-deploy.yml 수동 트리거
```
자동 push 트리거에 맡기지 말 것(§4.2 참조).

### 7.3 purge/삭제 캐스케이드 관련 변경
```
허브(vinc-hub) 먼저 → 계정 백엔드(vinc-account) → 정적 사이트
```
가비아 시절 원칙 무변경 — 역순이면 계정의 서명된 purge 호출이 404 를 맞아
fail-closed(503)로 막히는 창이 생긴다.

---

## §8 롤백 — 현실

| 표면 | 되돌리기 |
|---|---|
| 데스크탑 | 이전 릴리스를 다시 latest 로(무변경) |
| 정적/웹앱 | SWA 는 배포 히스토리를 자체 보관 — 포탈에서 이전 배포로 스왑 가능(가비아의 "덮어쓰기라 되돌릴 수 없음"보다 개선됨) |
| 계정 백엔드 | Container Apps 는 **리비전** 을 자동 보관 — `az containerapp revision list`로 이전 리비전에 트래픽 100% 재배정하면 즉시 롤백(가비아의 "dist 백업 없음"보다 크게 개선됨) |
| 허브 | 동일(리비전 롤백). 단 **데이터 포맷이 바뀌는 배포였다면** 리비전만 되돌려도 데이터는 이미 새 포맷일 수 있다 — §4.2 사전 확인이 애초에 이걸 막기 위한 것 |
| DB | 전진 전용 — 롤백 금지(무변경). 관리형 자동 백업(매일 1회·7일)에서 콘솔 복원 |

---

## §9 실전 체크리스트

### 데스크탑 릴리스 (무변경)
- [ ] 버전 6곳 범프 · CHANGELOG · 태그 push · CI 3-OS 초록 · draft Publish · latest.json 검증

### 계정 백엔드 / 허브 배포 (신규 — CD 가 대부분 자동 수행)
- [ ] 스키마 변경이면 마이그레이션 먼저(§7.1) — `schema_migrations` 조회로 확인
- [ ] `git push`(main) 하면 자동으로 test→build→deploy→health 검증까지 실행됨
- [ ] Actions 탭에서 워크플로 성공 확인(실패 시 이전 리비전 그대로 서빙 중이니
      트래픽 영향 없음 — Container Apps 는 배포 실패 시 자동으로 신규 리비전에
      트래픽을 안 준다)
- [ ] 허브는 저장 포맷 변경 여부 먼저 판단(§4.2) — 그렇다면 자동 push 대신
      `workflow_dispatch`

### 정적/웹앱 배포
- [ ] `site/**` 변경 push 하면 자동(app/ 변경만으로는 안 트리거됨 — app/ 는 데스크탑
      릴리스 시점에만 반영되므로, app/ 만 급히 반영해야 하면 `workflow_dispatch` 로
      수동 트리거)

### Cost 점검(신규)
- [ ] 월 1회 이상 `AZURE-COST-MANAGEMENT.md` 의 점검 목록 확인(Budget 알림 도착
      시에는 즉시)

---

## §10 관련 문서

| 문서 | 내용 |
|---|---|
| `Vinc-docs/vinc/AZURE-ARCHITECTURE.md` | 리소스 인벤토리·다이어그램·도메인 구성(신규) |
| `Vinc-docs/vinc/AZURE-COST-MANAGEMENT.md` | 비용 구조·Budget·요금 폭탄 위험 시나리오(신규) |
| `Vinc/.github/workflows/{release,hub-deploy}.yml` | 데스크탑·허브 CD 정의 |
| `Vinc-homepage/.github/workflows/{account-ci,account-deploy,site-deploy}.yml` | 계정·정적 CD 정의 |
| `Vinc-homepage/account/db/README.md` | DB 마이그레이션 절차(dbmate 명목·수동 실제는 무변경) |
| `Vinc/CHANGELOG.md` | 릴리스별 변경 |
