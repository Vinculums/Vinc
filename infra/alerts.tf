# 장애 메일은 계정 앱의 감시 루프가 보낸다. Azure 에는 그 루프가 볼 수 없는 것만 남긴다.
#
# 루프(Vinc-homepage `account/src/services/incidentMonitor.ts`)는 60초마다 허브 `/health?live=1`,
# OAuth discovery, 계정 DB 와 스키마를 본다. 연속 두 번 실패하면 메일 한 통, 회복하면 한 통을 보내고,
# 배포·재구축 동안은 그 워크플로가 연 창 안에서 메일을 막는다. 그래서 같은 것을 5~15분마다 보면서
# 계획 작업에도 울리던 가용성 테스트와 Sev1 규칙은 여기서 걷었다.
#
# 남은 것:
#   1. 감시자의 감시: 루프가 멈췄거나 계정 앱·SWA 가 죽으면 루프는 메일을 보낼 수 없다.
#   2. 자원 경보 셋(DB 스토리지·계정 메모리·허브 레플리카 겹침): 루프가 보지 않는 신호다.
#
# 허브 내부 경로가 앞문이 아니라 앱까지 닿는지는 여기서 보지 않는다. 그 불변식은 허브 배포에서만
# 바뀌므로 hub-deploy 의 배포 뒤 단계("Verify the hub internal path is answered by the app")가 본다.
#
# 남은 테스트도 상태 코드가 아니라 본문을 본다. 살아 있음만 보는 프로브는 기능 장애를 못 본다.
# 근거: [[vinc.plan.incident-mail-automation-2026-10-18]] §4
#       [[record:the-only-alert-watches-the-signal-that-was-green-in-all-three-outages]]

locals {
  insights_id     = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.Insights/components/vinc-uptime"
  action_group_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/microsoft.insights/actionGroups/vinc-alerts"
  # 테스트는 실행 횟수 × 지역 수로 청구된다. 남은 테스트는 고장 지점이 하나라 지역을 늘려도 얻는 것이 없다.
  probe_locations = ["apac-jp-kaw-edge"]
}

# ── 1. 감시자의 감시 ─────────────────────────────────────────────────────────
# `/api/health/monitor` 는 루프의 마지막 틱이 세 주기(3분) 안일 때만 200 과 `"monitor":"ok"` 를 준다.
# 루프가 멈추면 503 `stale`, 계정 앱이나 SWA 가 죽으면 답이 없다. 어느 쪽이든 장애 메일이 나갈 수 없는
# 상태라 이 테스트의 메일이 유일한 신호다.
#
# ⚠ 루프를 끄면(`INCIDENT_MONITOR_ENABLED`) 이 경로는 200 `off` 를 주고 본문 대조가 실패해 운다.
# 의도다. 이 파일에 가용성 테스트가 없으니 루프가 꺼지면 장애를 알릴 것이 아무것도 없다.
#
# SWA 를 거친다(`vincs.io`). 계정 컨테이너 FQDN 을 직접 부르면 앞문이 401 을 준다.
resource "azurerm_application_insights_standard_web_test" "incident_monitor" {
  name                    = "vinc-incident-monitor"
  resource_group_name     = var.resource_group_name
  location                = "koreacentral"
  application_insights_id = local.insights_id
  geo_locations           = local.probe_locations
  frequency               = 300
  timeout                 = 30
  enabled                 = true
  retry_enabled           = true
  description             = "계정 앱의 장애 감시 루프가 돌고 있는가"

  request {
    url       = "https://vincs.io/api/health/monitor"
    http_verb = "GET"
  }

  validation_rules {
    expected_status_code = 200
    ssl_check_enabled    = true

    content {
      content_match      = "\"monitor\":\"ok\""
      pass_if_text_found = true
      ignore_case        = false
    }
  }
}

# ── 경보 ────────────────────────────────────────────────────────────────────
# 기존 vinc-alerts 작업 그룹으로 간다.
#
# ⚠ 차원 이름은 availabilityResult/name 으로 단수 Result 다(복수인 것은 메트릭 이름 쪽뿐이다). 틀리면
#   필터가 아무것도 맞히지 못해 경보가 조용히 안 운다.
#
# ⚠ auto_mitigate 는 true 다. false 인 메트릭 경보는 상태 없이 평가마다 다시 울어서, 계획된 허브 정지
#   한 번에 같은 "Activated" 메일이 평가 주기마다 쌓였다. true 면 울림 한 통과 해소 한 통이다.

# 감시자의 감시는 15분 창에서 가용률 평균이 50 미만일 때 운다. 5분 주기 세 번 중 두 번이 실패해야
# 한다는 뜻이라, 계정 배포의 리비전 전환 같은 한 번의 실패로는 안 운다. 테스트 자신도 실패를 짧은
# 간격으로 두 번 더 재시도한 뒤에야 실패로 센다.
resource "azurerm_monitor_metric_alert" "incident_monitor_down" {
  name                = "incident-monitor-down"
  resource_group_name = var.resource_group_name
  scopes              = [local.insights_id]
  description         = "장애 감시 루프가 멈췄거나 계정 앱·SWA 가 답하지 않는다. 이 동안은 장애 메일이 나가지 않는다."
  severity            = 1
  frequency           = "PT5M"
  window_size         = "PT15M"
  auto_mitigate       = true

  criteria {
    metric_namespace = "microsoft.insights/components"
    metric_name      = "availabilityResults/availabilityPercentage"
    aggregation      = "Average"
    operator         = "LessThan"
    threshold        = 50

    dimension {
      name     = "availabilityResult/name"
      operator = "Include"
      values   = [azurerm_application_insights_standard_web_test.incident_monitor.name]
    }
  }

  action {
    action_group_id = local.action_group_id
  }
}

# ── 되돌릴 수 없는 것 하나에만 감시가 없었다 ────────────────────────────────
# 2026-09-02 실측: 이 리소스 그룹의 비용 벡터 중 **유일하게 되돌릴 수 없는 것**이
# MySQL 스토리지 자동 증가인데, 그것만 경보가 없었다. 가용성 사고는 고치면
# 원상복구된다. 이건 아니다:
#
#   auto-grow 는 조용히 터진다        → 알림이 없다
#   늘어난 스토리지는 못 줄인다        → 서버를 다시 만들지 않는 한
#   행을 지워도 요금은 안 내려간다      → 되돌리는 행위 자체가 없다
#
# 그래서 이 하나는 "고장을 알리는 경보"가 아니라 **되돌릴 수 없는 사건을 되돌릴 수
# 있는 경고로 바꾸는 장치**다. 성격이 달라서 severity 도 다르다.
#
# 왜 60인가. 같은 날 실측한 storage_percent 7일 최대가 **3.00** 이다(20GB 중 0.64GB,
# 여유 30배). 3에서 60까지는 지금 증가 속도로 몇 주가 걸리므로, 60은 "곧 터진다"가
# 아니라 "이제 사람이 볼 시간이 있다"는 뜻이다. 90으로 잡으면 여유가 며칠로 줄고,
# 30으로 잡으면 지금 당장 울린다.
#
# ⚠ 증가원 자체는 이미 다 잡혀 있다 — 계정 앱의 시간당 purge 크론이 세션·토큰·
# 대기구독자·내린 팩 본문을 수거하고, 사이드카가 툼스톤을 일 1회 수거한다(라이브
# 로그로 확인). 그러니 이것은 밀린 청소의 대체물이 아니라, 청소가 언젠가 조용히
# 멈췄을 때 그 사실이 드러나는 자리다.
# 근거: [[record:the-unbounded-growers-are-already-reaped-what-is-missing-is-the-tripwire]]
#
# severity 2 인 이유: 1은 "제품이 안 된다"에 쓴다(가용성). 이건 지금 아무것도 안
# 망가진 상태에서 몇 주 뒤를 경고하는 것이라 같은 급으로 울리면 안 된다 — 1이
# 흔해지면 1이 무의미해진다.
resource "azurerm_monitor_metric_alert" "db_storage_ratchet" {
  name                = "db-storage-approaching-autogrow"
  resource_group_name = var.resource_group_name
  scopes              = [azurerm_mysql_flexible_server.vinc_mysql01.id]
  description         = "MySQL 스토리지가 60% 를 넘었다 — auto-grow 는 되돌릴 수 없다(늘어난 용량은 서버 재생성 없이는 못 줄이고, 행을 지워도 요금이 안 내려간다). 지금 정리하면 원복 가능한 유일한 창이다."
  severity            = 2
  frequency           = "PT15M"
  window_size         = "PT1H"
  auto_mitigate       = true

  criteria {
    metric_namespace = "Microsoft.DBforMySQL/flexibleServers"
    metric_name      = "storage_percent"
    # Maximum 이다. Average 로 재면 하루 대부분이 낮은 값이라 스파이크를 못 본다.
    aggregation = "Maximum"
    operator    = "GreaterThan"
    threshold   = 60
  }

  action {
    action_group_id = local.action_group_id
  }
}

# ── 계정 앱: 언제 올릴지를 사람이 기억하지 않아도 되게 ──────────────────────
# 2026-09-02 실측. 이 앱은 이미 0.5 vCPU / 1Gi 다 — 위험 문서가 적어 둔 0.25/0.5 는
# 낡았고(그 값으로 답했다가 정정했다), 상향은 account-deploy.yml 91~92 줄에 선언돼
# 있다. 그러니 지금 필요한 것은 상향이 아니라 **다음 상향의 시점을 놓치지 않는 것**이다:
#
#   WorkingSetBytes 7일 최대  212MB / 1024MB = 21%
#   UsageNanoCores  7일 최대  0.001 vCPU / 0.5 = 0.2%
#   RestartCount    7일       0
#
# 임계값 75%(805,306,368 바이트)의 뜻: 21%에서 75%까지는 지금 부하로 한참 걸리므로
# "곧 죽는다"가 아니라 "이제 자원을 올릴 때다"이다. OOM 은 여기서 아직 멀고, 이
# 앱은 로그인·결제·팀·모든 호스티드 키 검증을 진다 — 죽고 나서 아는 자리가 아니다.
#
# aggregation 이 Maximum 인 이유: 이 메트릭의 primary 는 Average 인데, 평균으로 재면
# 하루 대부분이 낮아 스파이크를 못 본다. 우리가 사려는 것은 스파이크의 여유다.
#
# severity 3 인 이유: 1은 "제품이 안 된다"(가용성), 2는 "되돌릴 수 없다"(DB 래칫).
# 이건 아무것도 안 망가진 상태에서 "슬슬 올려라"라고 말하는 것이라 셋째 급이다.
# 급을 아껴 쓰지 않으면 급이 정보를 잃는다.
#
# ⚠ 이 앱은 terraform 이 안 든다(generated.tf 에 없다) — 그래서 리소스 참조가 아니라
# id 문자열이다. 위 web test 들이 insights_id 를 그렇게 쓰는 것과 같은 이유다.
locals {
  account_app_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.App/containerApps/vinc-account-vnet"
}

resource "azurerm_monitor_metric_alert" "account_memory_headroom" {
  name                = "account-memory-headroom-gone"
  resource_group_name = var.resource_group_name
  scopes              = [local.account_app_id]
  description         = "계정 앱 메모리가 1Gi 의 75% 를 넘었다 — 자원을 올릴 때다. 이 앱이 로그인·결제·팀·호스티드 키 검증을 전부 진다."
  severity            = 3
  frequency           = "PT15M"
  window_size         = "PT1H"
  auto_mitigate       = true

  criteria {
    metric_namespace = "Microsoft.App/containerApps"
    metric_name      = "WorkingSetBytes"
    aggregation      = "Maximum"
    operator         = "GreaterThan"
    threshold        = 805306368 # 1 GiB 의 75%
  }

  action {
    action_group_id = local.action_group_id
  }
}

# ── 단일 writer 인데 플랫폼이 두 레플리카로 서지했다 ────────────────────────
# 2026-09-04 실측: vinc-hub 은 Single 모드 · min=max=1 인데도 **노드 재스케줄** 때
# 플랫폼이 잠깐 두 레플리카로 겹친다(그날 ~99초). 배포가 아니라 같은 리비전의 재배치라
# hub-deploy.yml 의 직렬화(내리고 → 레플리카 0 대기 → 올린다)가 이걸 못 막는다.
#
# 겹치는 동안 무슨 일이 나는가: 새 레플리카가 낡은 스냅샷에서 복원하는데 구 레플리카는
# 계속 쓰고 있고, 살아남은 쪽의 다음 주기 스냅샷이 상대의 final snapshot 을 볼륨에서
# 덮는다 — 최대 한 스냅샷 주기(현재 60초)어치 쓰기가 **무음으로** 사라진다.
# 근거: [[record:concurrent-replica-overlap-clobbers-the-final-snapshot-2026-09-04]]
#       [[decision:detect-the-replica-overlap-and-defer-the-single-writer-fence-2026-09-04]]
#
# 이 경보는 원천 고침이 아니다(그건 동시 writer 를 막는 별도 결정이다). 그 사고를
# **무음에서 꺼내는** 장치다: 겹침이 나면 사람이 그 시각 근처의 쓰기를 확인해 필요하면
# 다시 쓴다.
#
# 왜 Replicas > 1 이 정확한 판별자인가: 제대로 직렬화된 배포는 1→0→1 로 지나가 절대
# 2가 안 된다. 그러니 이 경보가 울리는 것은 오직 직렬화 안 된 재스케줄 서지뿐이다.
# 오탐이 구조적으로 없다.
#
# aggregation 이 Maximum 인 이유: 겹침은 짧다(초 단위). Average 면 5분 창에 묻힌다.
# 창을 PT5M 로 짧게, 평가를 PT1M 로 잦게 둬 초 단위 겹침도 한두 분 안에 잡는다.
#
# ⚠ vinc-hub 은 terraform 이 안 든다(generated.tf 에 없다 — 컨테이너 앱 셋은 시크릿
# 때문에 관리 대상에서 뺐다). 그래서 리소스 참조가 아니라 id 문자열이다. 위
# account_memory_headroom 이 같은 이유로 그렇게 한다.
#
# severity 2 인 이유: 1은 "제품이 안 된다"(가용성). 이건 제품은 도는데 되돌리기
# 어려운 쓰기 유실이 났을 수 있다는 신호라, DB 래칫과 같은 급이다("되돌릴 수 없는
# 사건을 사람이 볼 수 있는 창으로").
locals {
  hub_app_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.App/containerApps/vinc-hub-vnet"
}

resource "azurerm_monitor_metric_alert" "hub_replica_overlap" {
  name                = "hub-two-replica-overlap"
  resource_group_name = var.resource_group_name
  scopes              = [local.hub_app_id]
  description         = "vinc-hub 이 두 레플리카로 겹쳤다 — 단일 writer 스토어라 재스케줄 서지 중 최대 한 스냅샷 주기어치 쓰기가 유실될 수 있다. 이 시각 근처의 쓰기를 확인할 것."
  severity            = 2
  frequency           = "PT1M"
  window_size         = "PT5M"
  auto_mitigate       = true

  criteria {
    metric_namespace = "Microsoft.App/containerApps"
    metric_name      = "Replicas"
    aggregation      = "Maximum"
    operator         = "GreaterThan"
    threshold        = 1
  }

  action {
    action_group_id = local.action_group_id
  }
}

# ── 허브 공유가 차기 전에 ──────────────────────────────────────────────────
# 허브는 60초마다 DB 전체를 공유(vinchubssd01/hub-data)에 복사한다. 운영 묶음은 DB 크기의 약
# 네 배를 차지하고(DB · .prev · 세대 둘), 수리·재구축 워크플로는 원본 옆에 새 묶음을 하나 더
# 만든다. 공유가 차면 스냅샷이 실패하고 단일 writer 의 내구 사본이 끊긴다. 그 전에 사람이
# 할당량을 늘리거나(언제든 늘릴 수 있다) 남은 묶음을 정리할 시간을 번다.
#
# 임계값은 할당량 64 GiB 의 70% 다. 할당량을 바꾸면 hub_share_quota_gib 도 같이 바꾼다.
# 용량 메트릭은 한 시간에 한 번 나오므로 평가도 한 시간, 창은 여섯 시간이다.
#
# severity 2 인 이유: 아직 아무것도 망가지지 않았지만, 넘기면 쓰기 유실로 이어진다.
# 근거: [[vinc.manual.platform]]
locals {
  hub_share_quota_gib = 64
  hub_file_service_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.Storage/storageAccounts/vinchubssd01/fileServices/default"
}

resource "azurerm_monitor_metric_alert" "hub_share_capacity" {
  name                = "hub-share-capacity"
  resource_group_name = var.resource_group_name
  scopes              = [local.hub_file_service_id]
  description         = "허브 공유(vinchubssd01/hub-data)가 할당량의 70% 를 넘었다. 차면 60초 스냅샷이 실패한다. 할당량을 늘리거나 남은 DB 묶음을 정리할 것."
  severity            = 2
  frequency           = "PT1H"
  window_size         = "PT6H"
  auto_mitigate       = true

  criteria {
    metric_namespace = "Microsoft.Storage/storageAccounts/fileServices"
    metric_name      = "FileCapacity"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = floor(local.hub_share_quota_gib * 1073741824 * 0.7)
  }

  action {
    action_group_id = local.action_group_id
  }
}
