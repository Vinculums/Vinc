<!-- vinc-export
doc_id: vinc.ops.incident-2026-08-04-easyauth
title: 사고 기록 · EasyAuth 가 회원 키 검증을 막았다 (2026-08-04)
origin: graph-native
chunks: 2
verified: true
content_hash: 88b23ff660007e6928f47ce6638508d5f94ef2d04355884c3c0015c648822bd1
updated_at: 2026-08-04T18:06:09.881Z
-->

---
doc_id: vinc.ops.incident-2026-08-04-easyauth
layer: ops
domain: incident
title: 사고 기록 · EasyAuth 가 회원 키 검증을 막았다 (2026-08-04)
status: final
authority: SSoT
version: v1.0
owner: vinc
last_updated: 2026-08-04
inherits_from: [vinc.ops.deployment, vinc.c.account-policy]
---

# 요약

호스티드 MCP(`mcp.vincs.io`)의 **모든 회원 키(`vinc_sk_*`)가 401** 이었다. 원인은
`vinc-account` Container App 에 켜져 있던 **플랫폼 인증(EasyAuth)** 이다. Azure Static
Web Apps 의 linked backend 로 걸면 자동으로 붙는 그 인증이, 허브가 인그레스로 직접
호출하는 `/internal/mcp/verify-key` 를 **앱에 닿기도 전에** 끊었다. `entitlement.verify_key`
는 실패를 전부 무효로 강등(fail-closed)하므로 밖에서는 `POST /mcp 401` 만 보였다.

웹 계정 기능은 멀쩡해 보였다. 브라우저는 `vincs.io`(SWA)를 거쳐 오기 때문에 통과한다.
사고 당일에도 키 발급은 성공했다(`account.apikey_create ok`).

# 왜 발견이 늦었나

`DEPLOYMENT.md` 에 기록된 직전 사고(`VINC_ACCOUNT_INTERNAL_URL` 결손 → `127.0.0.1:8080`
폴백)와 **증상이 같고 원인이 다르다**. 그 env 는 이미 `https://api.vincs.io` 로 올바르게
박혀 있었다. 즉 기록된 원인을 확인하고 "고쳐져 있다"고 판단하면 그대로 막힌다.

이것은 이 프로젝트가 세 번째로 겪은 같은 계열의 고장이다.

| 시점 | 원인 | 증상 |
|---|---|---|
| 2026-07-30 | `VINC_MCP_ISSUER` 누락 | OAuth 전면 정지 |
| 2026-08-04 | `VINC_ACCOUNT_INTERNAL_URL` 누락 | 회원 키 401 |
| 2026-08-04 | 계정 EasyAuth 가 `/internal` 차단 | 회원 키 401 |

공통점은 **기동 정상 · `/health` 200 · 기능만 조용히 죽음**이다.

⚠ **env 존재 게이트(`check_hub_env.py`)로는 세 번째를 원리상 못 잡는다.** 허브 env 는
전부 정상이었고 결함은 목적지 쪽에 있었다. 표면 검증에 "실제 `vinc_sk_` 키로 200" 을
넣지 않는 한 같은 것이 또 지나간다.

# 진단 절차 (재사용 가능)

1. **401 의 본문을 본다.** Hono 게이트는 `{"error":"unauthorized"}` JSON 을 준다.
   본문이 비고 `WWW-Authenticate: Bearer realm=<host>` 가 붙었으면 앱이 아니라
   인그레스가 끊은 것이다. **상태코드만으로는 구분되지 않는다.**
2. **계정 로그의 침묵을 읽는다.** `internal.ts` 의 `gate()` 거부는 감사 로그를 남기지
   않고, 키 조회까지 가면 `mcp.verify_key invalid` 가 찍힌다. 그 줄이 없으면 실패
   지점이 DB 조회보다 위다. 즉 키가 아니라 경로 문제다.
3. **컨테이너 안에서 재현한다.** `verify_key()` 는 예외를 전부 삼켜 밖에서는 신호가
   없다. `az containerapp exec` 로 `entitlement._sign_assertion` + `urllib` 왕복을
   직접 돌려 상태코드와 본문을 본다.

## 배제한 가설

| 가설 | 확인 결과 |
|---|---|
| `VINC_ACCOUNT_INTERNAL_URL` 결손 | 이미 `https://api.vincs.io` (정상) |
| 허브 시드와 계정 핀 불일치 | 유도 공개키 == `MCP_PUBLIC_KEY` (일치) |
| 허브 컨테이너 이그레스 차단 | 0.19초 도달 |
| 컨테이너 시계 오차로 어서션 만료 | 정상 (120초 여유) |
| 키 폐기 | 신규 발급분도 동일 401 |

**교훈**: 키쌍 재발급으로 달려들었으면 헛수고였다. 값을 대조하는 진단이 먼저다.

# 조치

```
az containerapp auth update -n vinc-account -g vincsRG \
  --excluded-paths "/internal/mcp/verify-key,/internal/mcp/plan"
az containerapp revision restart -n vinc-account -g vincsRG --revision <rev>
```

⚠ **재시작이 필수다.** `auth show` 에 `excludedPaths` 가 보여도 인증 사이드카는 옛
설정으로 계속 401 을 준다(5분 폴링해도 그대로였다).

⚠ **Git Bash 에서 실행하면 안 된다.** MSYS 경로 변환이 `/internal/mcp/verify-key` 를
`D:/git/internal/mcp/verify-key` 로 바꿔 넣어 조치가 절반만 적용됐다. 따옴표는 소용
없다. PowerShell 에서 실행하거나 `MSYS2_ARG_CONV_EXCL='*'` 를 붙인다. 커밋 `4c71b7c`
의 `VINC_DATA_DIR` 사고와 같은 함정이고 이번이 두 번째다.

# 남은 위험

`/internal` 두 경로가 이제 공개 도달 가능하다. 방어는 Ed25519 어서션(`iss=mcp`·
`aud=account`·120초 이내) **한 겹뿐**이다. `/internal` 공개 차단은 가비아 Caddy 시절
성질이고 Azure 에서는 EasyAuth 가 **우연히** 그 역할을 대신하고 있었다. `internal.ts`
상단의 "Caddy 미프록시 · 127.0.0.1 바인딩" 주석도 이미 stale 이다.

# 같은 날 함께 처리한 것

- `VINC_FREE_MAX_DOMAINS=5` 를 허브에 투입(AP-1c 의 주제 상한, 그전까지 꺼져 있었다).
  이때 **허브가 단일 라이터라 리비전 롤링 전환이 안 된다**는 것이 드러났다. 구 리비전이
  `/data/db.recovered` 락을 쥐고 있어 신본이 `driver: null` 로 죽는다. `hub-deploy.yml`
  은 구 리비전을 deactivate 한 뒤 기다리는데, 손으로 `az containerapp update` 만 하면
  그 단계가 빠져 Failed 리비전이 트래픽 100 퍼센트를 달고 남는다.
- 비 UTF-8 본문이 `_body()` 의 `.decode("utf-8")` 에서 `do_POST` 를 관통해 죽이던 것을
  400 으로 강등(브랜치 `fix/hub-non-utf8-body-503`, 커밋 `2bb5f58`). **미배포.**
