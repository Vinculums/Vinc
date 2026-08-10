# 배포 의뢰 프롬프트 — GRAPH.md(GM-1~GM-8) + 히어로 카피

> 이 파일은 **그대로 복사해 쓰는 프롬프트**다. 아래 `---` 아래 전체를 새 세션에 붙여 넣는다.
> 작성 2026-08-01. 작업 자체는 끝났고 **배포만 남았다**.

---

## 역할

너는 Vinc 의 배포를 집행한다. 새 기능을 만들지 말고, **이미 만들어져 워킹 트리에 있는
변경을 다섯 표면에 올바른 순서로 내보내는 일**만 한다.

작업 루트: `D:/Projects/AIProjects/vinc-project` (하위 `Vinc`, `Vinc-docs`, `Vinc-homepage`)

## 반드시 지킬 규율

- **배포 정본은 `Vinc-docs/vinc/DEPLOYMENT.md` 다.** 이 프롬프트와 어긋나면 그쪽이 맞다.
  as-built 문서이므로 "이래야 한다"가 아니라 "실제로 이렇게 돈다"를 적은 것이다.
- **순서를 지켜라.** §7.3 의 원칙(허브 → 계정 → 정적 사이트)이 이번 변경에 그대로 적용된다.
  역순이면 계정 프록시가 허브에 없는 op 를 호출해 그 화면만 404 로 죽는다.
- **커밋 전에 `git status` 를 세 리포 모두 확인하라.** 이 변경은 **두 커밋에 걸쳐 흩어져
  있다**(아래 §커밋 상태 이상). 그대로 push 하면 절반만 나간다.
- 배포는 되돌리기 어렵다. 각 단계 뒤 **검증 명령이 초록인 것을 확인하고 다음으로** 간다.
- 발주자 결정이 필요한 것(데스크탑 릴리스 여부·버전 번호)은 **묻고 기다려라**. 네가 태그를
  찍지 마라.

---

## 무엇을 배포하는가

GRAPH.md — 어시스턴트가 그래프에 쓰기 전에 읽는 관례 파일. 규격 정본은
`Vinc-docs/vinc/C/vinc.c.graph-md.md`(VINC-C-011), 판정 기록은 `plans/PLAN-graph-md.md`.

| 결정 | 내용 |
|---|---|
| GM-1 | 채택 |
| GM-2 | 배치 = 리포 파일 → 스토어 문서 → **읽기 도구**(MCP resources 는 보류) |
| GM-3 | 구역별 정본 — 생성 구역은 스토어, 산문은 파일 |
| GM-4 | `graph-md build/check`, 형상 9규칙, exit 0·1·2 |
| GM-5 | 문서 사이트에 **GRAPH.md 독립 섹션 4쪽** |
| GM-7 | 관례 문서를 검색·문서 목록·좌측 트리에서 제외 |
| GM-8 | 상단 바 아이콘 + 제안 뱃지 + 통제 시트(승인·거부·개명) |

여기에 **홈페이지 히어로 카피 교체**가 함께 나간다(EN `Add your data. Build your ontology.`,
ko `데이터를 가져오고, 온톨로지를 만드세요.` + `<title>`·meta description).

## 확정 사실 — 이번 변경의 성격

- **DB 스키마 변경 없음.** 마이그레이션 불요(표면 5 해당 없음). `_DocMeta`·`Chunk` 등 기존
  표만 쓴다.
- **허브 저장 포맷 변경 없음.** 고정 `doc_id = "graph.md"` 문서 한 건이 늘 뿐이다.
  ⇒ DEPLOYMENT §4.2 의 `workflow_dispatch` 수동 게이트 조건에 **해당하지 않는다**. 자동 push
  트리거로 나가도 된다.
- **새 라우트 2개**: `GET /conventions` · `POST /conventions/build`.
- **새 MCP 도구 1개**: `vinc_conventions`(19번째, 읽기 전용) + `vinc_ingest` 설명문 1문장.
- **프록시 허용목록 3곳**이 짝을 이뤄야 한다: 허브 `_PROXY_OPS` · 계정 `GET_OPS`/`POST_OPS` ·
  vite dev `API_RE`. 하나라도 빠지면 그 표면만 조용히 죽는다.

## ⚠ 커밋 상태 이상 — 이것부터 처리하라

`api/server.py`(104줄)와 `api/docstore.py`(64줄)의 GRAPH.md 변경이 **이미 커밋돼 있다.**
그런데 들어간 커밋이 `cc4845e feat(embed): 모델을 e5 로 전환 …` 이다 — 임베딩 작업 커밋에
관례 라우트가 섞여 들어갔다.

```bash
git -C Vinc log --oneline -S"read_conventions" -- api/server.py   # → cc4845e
```

따라서 지금 상태는 **기능이 반쯤 커밋된 상태**다.

- 커밋됨: `/conventions` 라우트·`write_conventions_build`·`list_docs` 제외
- 미커밋: `api/graphmd.py`(신설) · `api/cli.py` · `api/query.py` · `api/docsync.py` ·
  `vinc_mcp/tools.py` · `vinc_mcp/http_server.py` · `app/**` · `tests/**`

**HEAD 를 그대로 push 하면 라우트는 있는데 `graphmd` 모듈이 없어 허브가 import 에서
죽는다.** 반드시 나머지를 함께 커밋해서 내보내라.

발주자에게 물을 것: **cc4845e 를 그대로 둘 것인가.** 되돌리면(revert) 관례 라우트가 조용히
사라진다. 권고는 "그대로 두고, 후속 커밋 메시지에 이 사실을 적는다"이다 — 이미 push 된
커밋을 다시 쓰는 위험이 이득보다 크다.

## 변경 파일 — 리포별

**Vinc** (허브 표면 4 + 데스크탑 표면 1)
```
신설  api/graphmd.py · app/src/conventions.ts · tests/test_graphmd.py
수정  api/cli.py · api/query.py · api/docsync.py
      vinc_mcp/tools.py · vinc_mcp/http_server.py
      app/index.html · app/vite.config.ts
      app/src/{api,copy,main}.ts · app/src/app.css
      tests/test_mcp.py
커밋됨 api/server.py · api/docstore.py  (cc4845e — 위 참조)
```

**Vinc-homepage** (표면 2 정적사이트 + 표면 3 계정)
```
신설  site/docs/graph-md/{,build,spec,check}/index.html
수정  account/src/routes/graph.ts          ← 표면 3
      site/assets/docs.js · site/sitemap.xml
      site/index.html                       ← 히어로·title·meta
      site/docs/{index,cli,use-cases}/index.html
      site/docs/mcp/{index,tools}/index.html
```

**Vinc-docs** (배포 대상 아님 — 커밋만)
```
신설  vinc/C/vinc.c.graph-md.md · plans/PLAN-graph-md.md · plans/PROMPT-deploy-graph-md.md
수정  master-id-map.yml · vinc/C/README.md · vinc/MCP-INTERFACE.md · NEXT-STEPS.md
```

---

## 배포 순서

### 0. 사전 검증 (배포 전 반드시)

```bash
cd Vinc && .venv/Scripts/python.exe -m pytest tests -q
cd Vinc/app && npx tsc --noEmit
cd Vinc-homepage/account && npx tsc --noEmit
```

기준선(2026-08-01 실측): 파이썬 **651 passed / 6 skipped**, tsc 양쪽 exit 0.

허용목록 3곳이 짝인지 눈으로 확인:

```bash
grep -n "conventions" Vinc/vinc_mcp/http_server.py
grep -n "conventions" Vinc-homepage/account/src/routes/graph.ts
grep -n "conventions" Vinc/app/vite.config.ts
```

각각 **읽기와 build 둘 다** 나와야 한다.

### 1. 허브 (표면 4) — 가장 먼저

`Vinc` 의 나머지 변경을 커밋해 `main` push. `hub-deploy.yml` 이 `api/**`·`store/**`·
`vinc_mcp/**` 경로에서 자동 트리거된다.

커밋 메시지에 **cc4845e 사실을 적어라** — 이 기능의 절반이 임베딩 커밋에 들어가 있다는 것.

검증:
```bash
curl -s https://mcp.vincs.io/health
# MCP 도구가 19종인지 (원격 키가 있으면)
curl -s -X POST https://mcp.vincs.io/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | grep -c vinc_conventions
```

⚠ 허브는 임베디드 DB 단일 writer라 롤링 업데이트가 구조적으로 실패한다(DEPLOYMENT §4.3).
워크플로가 이미 그 순서를 강제하므로 **손으로 `az containerapp update` 하지 마라.**

### 2. 계정 백엔드 (표면 3)

`Vinc-homepage/account/**` push → `account-deploy.yml` 자동. **허브가 초록인 것을 확인한
뒤에** 한다 — 순서가 뒤집히면 계정이 허브에 없는 `conventions` op 를 호출한다.

검증: 웹 Vinc 로그인 후 `/api/graph/conventions?owner=…` 가 200 인지.

### 3. 정적 사이트 + 문서 (표면 2)

`Vinc-homepage/site/**` push → `site-deploy.yml` 자동.

검증(라이브):
- `https://vincs.io/` 히어로가 새 카피인지, `<title>` 이 `Add your data. Build your ontology.` 인지
- `https://vincs.io/docs/graph-md/` 4쪽 전부 200, 사이드바 섹션이 **Vinc · MCP · CLI · GRAPH.md**
- `https://vincs.io/docs/mcp/graph-md/` 는 **404 여야 한다**(구 경로, 배포된 적 없음)
- `sitemap.xml` 에 graph-md 4건

### 4. 데스크탑 앱 (표면 1) — 발주자 결정

앱의 관례 시트(GM-8)는 **릴리스로만** 사용자에게 닿는다. 지금 낼지 묻고, 낸다면:

```bash
python scripts/version_sync.py --check
python scripts/version_sync.py --set <새 버전>    # 10곳 일괄
```

`CHANGELOG.md` 는 사람이 쓴다. 태그 push → CI 3-OS 초록 → draft Publish(사람) →
`latest.json` 검증. **네가 태그를 찍지 마라.**

### 5. 웹 Vinc 의 app/ 반영 — 잊기 쉬운 자리

`site-deploy.yml` 은 `site/**` 에서만 트리거된다. **`Vinc/app/` 변경은 안 걸린다.**
관례 시트를 웹 Vinc 에도 반영하려면 `site-deploy.yml` 을 `workflow_dispatch` 로 수동
트리거해야 한다(DEPLOYMENT §9 정적/웹앱 체크리스트).

---

## 배포 후 할 일

1. `Vinc-docs` 커밋(배포 대상 아님, 기록용).
2. `vinc.c.graph-md.md` §10 Phase 표에 배포 일자를 적는다.
3. `NEXT-STEPS.md` 의 **`vinc` 명령 실재하지 않음** 항목은 그대로 남긴다(별건, 미해결).
4. 4주 뒤 지표 판정 예약 — P(제안 적체) · D(도메인 분열). 기준·중단 조건은
   `plans/PLAN-graph-md.md` §8.

## 하지 말 것

- **DB 마이그레이션을 찾지 마라.** 이번 변경에 스키마 변경은 없다.
- **`workflow_dispatch` 수동 게이트를 허브에 쓰지 마라.** 저장 포맷이 안 바뀌었다(§4.2).
- **`az containerapp update` 를 손으로 치지 마라.** 워크플로가 순서를 강제한다(§4.3).
- **롤백을 전제로 배포하지 마라.** DB 는 전진 전용이고, 허브·계정은 리비전 롤백이 되지만
  정적 사이트는 SWA 히스토리 스왑이라 즉시성이 다르다(§8).
- 카피를 "개선"하지 마라. 히어로 문구는 다섯 개 톤을 비교해 확정된 것이다.

---

# 배포 후기 (2026-08-01 실행 기록)

배포는 완료됐다. 다만 **이 프롬프트의 전제 세 가지가 실제와 달랐고**, GRAPH.md 와 무관한
배포 차단 결함 두 개가 도중에 드러났다. 다음 배포자를 위해 남긴다.

## 프롬프트가 틀렸던 곳

| 프롬프트 서술 | 실제 |
|---|---|
| "이미 push 된 커밋 `cc4845e` 를 다시 쓰는 위험" | `cc4845e` 는 **push 되지 않은 상태**였다(미푸시 25개 중 하나). 권고 자체(그대로 두기)는 유지했다 — 코드는 동일하고 이력만 어긋난 것이라 재작성 이득이 없다 |
| "허브 저장 포맷 변경 없음 ⇒ §4.2 수동 게이트 해당 없음" | **2번 작업만 본 서술**이다. 같은 push 에 e5 작업(스키마 v7→v8·전량 재임베딩)이 함께 실려 저장 포맷이 바뀐다. 같은 브랜치라 분리 배포가 불가능하다 |
| "§5 웹 Vinc 는 `workflow_dispatch` 수동 트리거 필요" | **불필요했다.** `site-deploy.yml` 이 `Vinculums/Vinc` 를 ref 고정 없이 체크아웃하므로, `site/**` 변경이 함께 나가는 배포에서는 이미 push 된 main HEAD 의 `app/` 이 자동으로 빌드된다(웹 번들 0.7.0·관례 시트 포함 실측 확인). 수동 트리거는 **app/ 만** 급히 반영할 때의 이야기다 |

또 프롬프트에 없던 것: `origin/main` 에 CRITICAL 수정(`83d5e41` 커넥터 연결 불가)이
하나 앞서 있어 merge 가 필요했다.

## 배포 차단 결함 2건 — 둘 다 `be46b05`(빌드 타임 양자화) 유래

**이 둘 때문에 e5 배포는 언제 시도했어도 실패했다.** 로컬에서는 재현되지 않는다.

### ① ACR dependency scanning 이 heredoc 의 `from` 을 `FROM` 으로 읽는다

ACR Tasks 는 빌드 전에 Dockerfile 을 자체 파서로 훑는데, heredoc 본문을 구분하지 못한다.

```
unable to understand line from huggingface_hub import hf_hub_download
failed to run step ID: build: failed to scan dependencies: exit status 1
```

run `dek` 이 3초 만에 Failed. 즉 `be46b05` 은 **한 번도 빌드에 성공한 적이 없다.**
고침 `ac380c4` — `from X import Y` 를 `import X` 로.

### ② 그걸 고치자 더 나쁜 게 나왔다 — 빌드가 초록인데 모델이 안 구워진다

ACR 클래식 빌더에서 `RUN a && python - <<'PY' && b` 는 줄 이음으로 한 줄이 된 뒤
셸이 heredoc 본문을 받지 못해 `python -` 이 **빈 stdin 으로 즉시 종료**한다.
로그엔 `pip install onnx` 바로 뒤 `pip uninstall onnx` 만 찍히고 `baked:` 가 없다.

**드러난 지점은 이미지 크기였다**: 120.8MB → 126MB(+5.2MB). 모델이 실렸다면 불가능한 값.

런타임 결말이 심각하다 — `embed_worker.py` `_model_path` 가 `VINC_EMBED_MODEL_PATH`
부재를 보고 fp32(1,058MB)를 받아 1024MiB 컨테이너에 올린다. `pip uninstall -y onnx`
때문에 런타임 재양자화 폴백도 막혀 있어 곧장 fp32 다. **OOM 이 MCP 까지 죽인다.**
배포 직후 `pending=0` 이라 아직 안 터졌을 뿐, 첫 문서 임베딩에서 터질 지뢰였다.

고침 `6576707` — 굽는 코드를 `packaging/bake_embed_model.py` 로 분리하고 **그 스크립트가
결과를 검증해 200MB 미만이면 빌드를 세운다.** 조용히 건너뛴 실패가 다시 초록으로 보이지
않게. hub-deploy 트리거 경로에도 등재(빠지면 수정해도 재배포가 안 걸린다).

수정 후 실측: `baked: model=278648397B tokenizer=17082660B`(문서값 279MB·17MB 와 일치),
이미지 **346MB**, 빌드 1분 42초.

## 교훈 — 이 프로젝트가 반복하는 실패 형태

②는 **"빌드 초록 · 결과 없음"** 이다. 이 리포가 반복해 온 "에러 없이 조용히 나빠지는"
사고와 같은 계열이고(임베딩 워커 실종·벡터 write-only·`VINC_MCP_ISSUER` 누락),
전부 **성공 신호가 실제 산출물을 보지 않는다**는 공통점이 있다.

배포 검증에 **크기·개수 같은 산출물 자체의 지표**를 하나는 넣을 것. 이번에 결함을 잡은
것은 워크플로 초록이 아니라 `126MB` 라는 숫자였다.

## 릴리스 CI 에서 연달아 터진 것 — 핀을 맞추자 잠복이 깨어났다

`release.yml` 만 `ladybug` 핀이 없었다(§DEPLOYMENT 1.5.1). 그것을 고치자 **두 번 더** 터졌다.
원인은 하나다 — **핀이 없는 동안 CI 는 0.19.0 을 쓰고 있었고, 로컬은 캐시로 살아 있었다.**
핀을 맞추는 순간 0.18.0 이 처음으로 fresh 경로를 타면서 잠복이 전부 드러났다.

| 회차 | 증상 | 실제 원인 |
|---|---|---|
| 1 | Windows 만 실패 · `Could not find lbug C API shared library` | `release.yml` 만 핀 없음 → 0.19.0 을 집음(Windows 휠에서 C API 분리) |
| 2 | Linux 실패 · `ERROR: SSL server verification failed` | 0.18.0 **내장 다운로더의 CA 번들이 낡음**. 같은 URL 이 curl 로는 200 이고 바이트도 리포 커밋본과 일치 |

**버전으로는 빠져나갈 수 없다**(실측):

| 버전 | Windows 휠 C API | 확장 서버 |
|---|---|---|
| 0.18.0 | ✅ | ✅ 3플랫폼 200 |
| 0.18.3 | ✅ | ❌ 404 |
| 0.19.0 | ❌ | ✅ |

그래서 버전이 아니라 **다운로더를 바꿨다** — 시스템 CA 를 쓰는 curl 로
`~/.lbdb/extension/{ver}/{plat}/{ext}/` 를 직접 채운다(G0 오프라인 원칙과 같은 방향).
3-OS 9/9 다운로드·spec 글롭 3/3 을 푸시 전에 로컬에서 실측했다.

곁가지로 **CI 주석 오염**도 고쳤다 — `check_hub_env` 게이트를 일부러 실패시키는
테스트가 `::error::` 를 흘려, 통과하는데도 매 실행 "10 errors" 가 떴다. 상시 빨강은
사람을 진짜 오류에 둔감하게 만든다(§DEPLOYMENT 1.5.3).

## 완료 (2026-08-01)

전 표면 배포 완료 · **v0.7.0 Publish 완료**.

| 확인 | 값 |
|---|---|
| 릴리스 | `v0.7.0` draft=false · 자산 12종(3-OS 전부) |
| `latest.json` | version **0.7.0** · darwin-aarch64 / linux-x86_64 / windows-x86_64 **서명 3종 전부** · 자산 URL 200 |
| 무한 토스트 함정 | 없음 — `tauri.conf.json` 0.7.0 == `latest.json` 0.7.0 |
| 허브 | `vinc-hub--0000013` · health ok · oauth 200 · MCP 19종에 `vinc_conventions` |
| 계정 | `vinc-account--0000019` · `/api/health` ok · `conventions` 401(등재) |
| 사이트 | 홈·`/docs/graph-md/`·`/app/` 200 · 구 경로 404 |
| 테스트 | 652 passed / 6 skipped |

**남은 것**: 4주 지표 판정(P 제안 적체 · D 도메인 분열) — **2026-08-29**.
기준·중단 조건은 `PLAN-graph-md.md` §8.
