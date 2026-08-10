<!-- vinc-export
doc_id: vinc.ops.azure-cost
title: AZURE-COST-MANAGEMENT — 비용 관리 · 요금 폭탄 위험 (as-built)
origin: Vinc-docs/vinc/AZURE-COST-MANAGEMENT.md@5090690
chunks: 3
verified: true
content_hash: 5b5ed40007195e26f8fab6fbfcfc0e1ad5a54de9f150959a8f7d573eab5c7d48
updated_at: 2026-08-01T16:20:43.853Z
-->

> 정본은 리포 파일입니다. 이 사본은 검색용 스냅샷(2026-08-02)입니다.

# AZURE-COST-MANAGEMENT — 비용 관리 · 요금 폭탄 위험 (as-built)

> 2026-07-29 작성. 아키텍처는 `AZURE-ARCHITECTURE.md`, 배포 절차는 `DEPLOYMENT.md`
> 참조. **이 문서는 안전장치 설정 내역 + 위험 시나리오 진단**이다 — 실제 청구 금액은
> Azure Portal → Cost Management + Billing 에서 확인할 것(CLI `az consumption
> usage list` 는 이 구독 오퍼 타입에서 지원 안 됨, 실측 확인됨).

## 1. 이미 설정된 방어장치

| 장치 | 설정값 | 효과 |
|---|---|---|
| **Budget** `vinc-monthly-budget` | 월 $150, 알림 50%/75%/90%(실측) + 100%(예측치) → `weg1456@gmail.com` | 리소스를 자동으로 끄지는 않음(순수 알림) |
| **Log Analytics 일일 캡** `workspace-vincssM9K` | 1GB/일 (도입 전엔 **무제한**이었음 — 실측) | 그날 남은 시간 로그 수집만 중단, 서비스 자체엔 무영향 |
| **Container Apps 스케일 상한** `vinc-account`·`vinc-hub` | `minReplicas=maxReplicas=1` (오토스케일 룰 없음) | 트래픽 폭증에도 레플리카가 안 늘어남 — 안전하지만 트래픽 실제로 늘면 응답 지연/거부로 나타남(비용 문제는 아님) |
| **RBAC 범위** | OIDC 서비스 주체가 `vincsRG` 에만 Contributor | GitHub Actions 워크플로가 실수로 다른 리소스 그룹/구독에 영향 못 줌 |

**Budget 조정**: `weg1456@gmail.com` 외 추가 수신자나 임계값 변경은
`az rest --method patch --uri ".../budgets/vinc-monthly-budget?api-version=2021-10-01"`
로 `notifications`/`amount` 만 갈아끼우면 된다(전체 재생성 불필요).

## 2. 리소스별 요금 폭탄 시나리오와 방어

### 2.1 Container Apps (`vinc-account`, `vinc-hub`)

**구조**: Consumption 플랜은 vCPU-초·GiB-초 종량제 + 요청 건수. 지금은 `min=max=1`
고정이라 **오토스케일 자체가 없다** — 이게 1차 방어선.

| 위험 시나리오 | 발생 조건 | 현재 노출도 |
|---|---|---|
| 오토스케일 폭주(수십 레플리카) | `maxReplicas` 를 나중에 5·10 등으로 올리고 HTTP 동시 요청 임계치를 못 걸어둠 | **낮음**(지금 max=1) — **maxReplicas 를 올릴 때마다 이 표를 다시 볼 것** |
| DDoS/봇 트래픽으로 vCPU-초 폭증 | 외부 무제한 노출 + 레이트리밋 없음 | **중간** — `vinc-account`·`vinc-hub` 둘 다 ingress `external: true`, 애플리케이션 레벨 레이트리밋 없음(계정 백엔드에 `firstPartyGuard` 는 Origin 검증이지 요청량 제한이 아님) |
| 상시 idle 과금 | `min=1` 고정 — 트래픽 0 이어도 24시간 컴퓨트 계속 청구 | **확정 발생**(설계상 의도됨 — 허브는 인메모리 세션 때문에 스케일다운 불가) |
| 이미지 pull 시 대역폭 | 배포(CD)마다 ACR→Container Apps 이미지 전송 | **낮음**(같은 리전이라 대역폭 자체는 저렴, 배포 빈도가 낮으면 무시 가능 수준) |

**권고**: 트래픽이 실제로 늘어 `maxReplicas` 를 올리기 전에, HTTP 스케일 규칙에
`concurrentRequests` 임계치를 명시하고 **Budget 알림과는 별개로 vCPU-초 사용량
알림**(Azure Monitor 메트릭 알림, `UsageNanoCores`)을 추가 검토할 것.

### 2.2 Azure Database for MySQL Flexible Server (`vinc-mysql01`)

**⚠ 가장 주의해야 할 항목 — `storage.autoGrow: Enabled`.**

| 위험 시나리오 | 메커니즘 | 대응 |
|---|---|---|
| **스토리지 자동 확장 후 축소 불가** | 사용량이 임계치(통상 여유 공간 10% 이하) 도달 시 Azure 가 자동으로 스토리지를 증설한다. **한 번 커진 스토리지는 절대 줄일 수 없다**(재생성 없이는) — 즉 일시적 대량 데이터(예: 잘못된 마이그레이션, 로그 테이블 폭증)로 20GB→512GB 까지 뛰면, 그 데이터를 지워도 스토리지 요금은 계속 512GB 기준으로 청구된다 | 정기적으로 `az mysql flexible-server show --query storage.storageSizeGb` 로 실측 확인. 급격한 증가가 보이면 즉시 원인(대량 INSERT·로그 테이블) 조사 |
| 실수로 컴퓨트 티어 상향 | Burstable→General Purpose 등으로 잘못 변경 시 시간당 요금이 몇 배로 뜀 | 변경 전 항상 `--sku-name`·`--tier` 재확인 |
| 실수로 HA(고가용성) 활성화 | Zone-redundant HA 는 스탠바이 인스턴스가 별도로 떠서 **컴퓨트 요금이 2배** | 지금 미설정(`Disabled` 확인됨) — 향후 변경 시 인지하고 진행 |
| 백업 스토리지 초과 | `backupRetentionDays` 상향(7→35 등) 시 백업 스토리지 요금 증가(스토리지 크기에 비례) | 지금 7일 기본값 유지 중, 상향 필요시 비용 재계산 |

### 2.3 Container Registry (`vincacr`, Basic)

| 위험 시나리오 | 대응 |
|---|---|
| Basic 티어 스토리지 10GB 초과(추가 스토리지 과금) | 이미지 태그가 계속 쌓임(`vinc-account:latest`+`:<sha>` 매 배포마다 신규 태그) — retention policy 를 켜서 오래된 sha 태그 자동 정리 검토(지금 `retentionPolicy.status: disabled`, 실측 확인) |
| Geo 간 이미지 pull egress | 지금 ACR·Container Apps 가 같은 리전(Korea Central)이라 무관 |

### 2.4 Log Analytics Workspace (`workspace-vincssM9K`)

**⚠ 실측 확인된 위험** — 일일 캡이 원래 **무제한**이었다(`dailyQuotaGb: -1`). 애플리케이션
버그로 에러 로그가 초당 수백~수천 건씩 무한 반복되는 상황(예: 재연결 루프, 크래시
루프백)이면 수집량이 그날 안에 수십~수백 GB 까지 뛸 수 있고, PerGB2018 SKU 는
**GB 당 종량 과금**이라 그대로 청구된다.

**이번 작업으로 1GB/일 캡을 설정함**(보존 기간 30일 기본값은 유지). 정상 운영
트래픽 규모(현재 테스트 수준)에서는 하루 로그량이 캡의 몇 % 도 안 될 것으로
예상되며, 캡에 걸리면 그날 남은 로그만 유실되고 **서비스 자체는 영향 없음**.

### 2.5 Static Web App (`vinc-site`, Standard)

| 위험 시나리오 | 대응 |
|---|---|
| 대역폭 초과(포함 500GB/월 초과분 GB 당 과금) | 정적 자산 위주라 이 규모(개인/소규모 서비스)에서 도달 가능성 낮음. 대량 트래픽 유입 시(바이럴 등) 모니터링 |
| Linked Backend 자체 추가 요금 | 없음(Standard 요금에 포함) |

### 2.6 Storage Account / Azure Files (`vincstorage01`)

| 위험 시나리오 | 대응 |
|---|---|
| 트랜잭션 과금(파일 읽기/쓰기 건당) | 허브 그래프 DB 가 파일 기반이라 쿼리마다 파일 I/O 발생 — 트래픽이 크게 늘면 트랜잭션 비용이 스토리지 용량 비용보다 커질 수 있음(지금 규모에서는 무시 가능) |
| 용량 초과 | 그래프 DB 크기가 커지면 자연 증가 — LRS 저비용 유지 중, 필요시에만 냉장고 필요 |

## 3. 고아 리소스 위험 (실제로 겪은 사고 사례)

이번 작업 중 **테스트 컨테이너 앱(`vinc-test`)이 삭제 명령 실패 후 인지 못한 채
계속 떠 있었던 사고**가 실제로 있었다(짧은 시간이라 비용 영향은 미미했지만, 방치되면
누적된다). 재발 방지:

- **정기 점검(월 1회 권장)**: `az resource list -g vincsRG --output table` 로 실제
  떠 있는 리소스와 `AZURE-ARCHITECTURE.md` §3 인벤토리를 대조. 목록에 없는 리소스가
  있으면 즉시 원인 확인(테스트 잔재인지, 의도된 추가인지).
- 삭제 명령은 **항상 결과를 재확인**할 것(`az containerapp delete` 등이 "Subscription
  is not registered" 같은 일시적 에러로 실패했는데 겉으로는 성공한 것처럼 보일 수
  있다 — 실측으로 겪음).

## 4. 요금 폭탄 발생 시 대응 절차

1. Budget 알림(이메일) 수신 → Azure Portal Cost Management → Cost analysis 에서
   **어느 리소스가 급증했는지** 먼저 특정(리소스 그룹은 `vincsRG` 하나뿐이라 범위는
   좁음).
2. Container Apps 급증이면: `az containerapp revision list` 로 레플리카 수 확인 →
   필요시 `az containerapp update --max-replicas 1`로 즉시 캡.
3. MySQL 스토리지 급증이면: 원인 데이터 삭제해도 **스토리지 요금은 안 줄어든다** —
   장기적으로 비용을 되돌리려면 새 인스턴스로 재생성 + 데이터 이관이 유일한 방법
   (즉시 대응보다 재발 방지가 핵심).
4. Log Analytics 급증이면: 이미 1GB/일 캡이 있으니 자동으로 멈춤 — 원인(어느
   컨테이너의 어느 로그인지) 조사가 급선무.
5. 근본 원인 불명이거나 확산 중이면: 해당 Container App 을 `az containerapp update
   --min-replicas 0 --max-replicas 0`로 즉시 스케일다운(서비스 중단이지만 과금은
   멈춤) — 이건 명백히 **긴급 조치**이니 실행 전 실제로 요금 폭주가 맞는지, 서비스
   중단이 감내 가능한지 판단할 것.

## 5. 이번에 다루지 않은 것 (알아두되 지금은 조치 안 함)

- **Azure Advisor / Cost Management 의 예약 인스턴스·Savings Plan 추천**: 지금
  규모(월 $150 예산 이하)에서는 선구매 할인의 실익이 없음(사용량이 아직 예측 가능한
  안정 궤도가 아님).
- **Container Apps 의 vCPU-초 단위 Azure Monitor 메트릭 알림**: Budget(비용 기준)
  과 별개로 사용량 기준 조기 경보를 원하면 추가 가능 — 지금은 `min=max=1` 고정이라
  우선순위 낮음.
