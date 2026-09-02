# 기능을 보는 경보 — 살아 있음을 보는 경보로는 이 프로젝트의 사고를 하나도 못 잡았다.
#
# 2026-09-01 실측: 이 리소스 그룹의 경보 규칙은 **하나**였고(`hub-health-down`),
# 그것이 보는 `https://mcp.vincs.io/health` 는 세 번의 기능 장애에서 **세 번 다 200**
# 이었다:
#
#   2026-07-30  VINC_MCP_ISSUER 누락 → OAuth 전면 불가            /health 200
#   2026-08-04  앞문이 verify-key 차단 → 모든 호스티드 키 401     /health 200
#   2026-08-30  excludedPaths 누락 → 팀 OAuth 가 하루 넘게 사망   /health 200 · 배포도 초록
#
# 세 번째는 사람이 화면을 보고 알았다. 살아 있음 프로브는 기능 장애를 원리적으로 못 본다.
# 근거: [[record:the-only-alert-watches-the-signal-that-was-green-in-all-three-outages]]
#       [[vinc.ops.infrastructure-risk-2026-09-01]] §4
#
# 여기 셋은 각각 위 사고 하나씩에 대응한다. 전부 content match 를 쓴다 — 상태 코드만
# 보면 앞문이 내는 401 과 앱이 내는 401 이 구분되지 않고, 그 구분이 정확히 08-04 와
# 08-30 을 놓친 자리다.
#
# 왜 크론 워크플로가 아닌가: 5분 주기면 Actions 분을 월 1400분 넘게 태운다. 가용성
# 테스트가 이 일에 맞는 도구다.

locals {
  insights_id     = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.Insights/components/vinc-uptime"
  action_group_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/microsoft.insights/actionGroups/vinc-alerts"
  probe_locations = ["apac-jp-kaw-edge", "apac-sg-sin-azr", "us-ca-sjc-azr"]
}

# ── 1. OAuth discovery — 2026-07-30 형 사고 ─────────────────────────────────
# VINC_MCP_ISSUER 가 빠지면 이 경로가 404 가 되고 커넥터가 discovery 를 포기한다.
# hub-deploy.yml 이 배포 직후 같은 것을 확인하지만 그건 배포 시점 한 번뿐이라, env 가
# 나중에 사라지는 경우를 못 본다(앱을 재생성하면 조용히 빠진다 — 그 사고가 그것이었다).
#
# 본문 실측 2026-09-01:
#   {"resource": "https://mcp.vincs.io/mcp", "authorization_servers": [...]}
resource "azurerm_application_insights_standard_web_test" "oauth_discovery" {
  name                    = "vinc-oauth-discovery"
  resource_group_name     = var.resource_group_name
  location                = "koreacentral"
  application_insights_id = local.insights_id
  geo_locations           = local.probe_locations
  frequency               = 300
  timeout                 = 30
  enabled                 = true
  retry_enabled           = true
  description             = "OAuth discovery 가 200 인가 — issuer 가 빠지면 404 다"

  request {
    url       = "https://mcp.vincs.io/.well-known/oauth-protected-resource"
    http_verb = "GET"
  }

  validation_rules {
    expected_status_code = 200
    ssl_check_enabled    = true

    # 200 만 봐서는 부족하다. issuer 가 비면 본문 모양이 달라지므로 실제 필드를 본다.
    content {
      content_match      = "authorization_servers"
      pass_if_text_found = true
      ignore_case        = false
    }
  }
}

# ── 2. 내부 경로가 앱까지 닿는가 — 2026-08-04 · 2026-08-30 형 사고 ──────────
# SWA Linked Backend 가 계정 앱에 EasyAuth 를 자동으로 켜므로, 허브에서 계정으로 가는
# 호출은 excludedPaths 에 든 경로만 앱에 닿는다. 그 목록이 틀리면 앱은 멀쩡한데
# 인그레스에서 끊긴다 — 배포도 초록이고 /health 도 200 이다.
#
# 판별자는 401 의 모양이다(Vinc-homepage#54 가 이걸 단위 검사로 박았다):
#   앱   → {"error":"unauthorized"}   content-type application/json
#   앞문 → 빈 본문 + www-authenticate: Bearer realm="api.vincs.io"
# 그래서 401 을 기대하고 본문에 unauthorized 가 있는지 본다. 앞문이 가로채면 본문이
# 비어 불일치가 되고 경보가 울린다.
#
# 프로브 대상이 team-membership 인 이유: verify-key 는 처음부터 목록에 있었고
# team-membership 은 나중에 추가된 것이라, 새 목록이 실제로 적용됐는지를 보는 쪽이
# 이것이다. 2026-08-31 에 한 경로만 찔러 보고 인그레스 전체를 판정했다가 반만 맞은
# 전례가 있다.
#
# 이 프로브가 못 보는 것: 라우트를 새로 늘리면서 목록에 안 넣는 경우. 그건
# account/test/internal.test.ts 가 PR 단계에서 잡는다. 둘은 다른 고장을 잡는다.
resource "azurerm_application_insights_standard_web_test" "internal_path_reaches_app" {
  name                    = "vinc-internal-path-reaches-app"
  resource_group_name     = var.resource_group_name
  location                = "koreacentral"
  application_insights_id = local.insights_id
  geo_locations           = local.probe_locations
  frequency               = 300
  timeout                 = 30
  enabled                 = true
  retry_enabled           = true
  description             = "내부 경로가 앞문이 아니라 앱까지 닿는가 (401 의 모양으로 가른다)"

  request {
    url       = "https://api.vincs.io/internal/mcp/team-membership"
    http_verb = "POST"
    # ⚠ 여기는 **디코드된 값**을 쓴다. provider 가 쓸 때는 이 문자열을 그대로 넘기고
    #   읽을 때는 base64 로 디코드해서 돌려주므로, base64 를 적으면 config 와 state 가
    #   영영 안 맞고 매 plan 이 같은 변경을 제안한다(2026-09-01 실측: apply 직후
    #   `body = jsonencode({}) -> "e30="`). 기능은 멀쩡한데 "plan 이 비었나"가 신호로
    #   못 쓰이게 되고, 그게 이 디렉터리에서 가장 비싼 손실이다.
    #
    #   Azure 에는 여전히 e30= 가 저장되고 프로브는 통과한다 — 서명이 없으므로 앱이
    #   401 로 답하고 본문에 unauthorized 가 있어 판별자가 성립한다(4/4 Passed).
    body = "{}"

    header {
      name  = "Content-Type"
      value = "application/json"
    }
  }

  validation_rules {
    # 401 이 정상이다. 서명 없이 찔렀으니 앱이 어서션 게이트로 거절하는 것이 맞다.
    expected_status_code = 401
    ssl_check_enabled    = true

    content {
      content_match      = "unauthorized"
      pass_if_text_found = true
      ignore_case        = false
    }
  }
}

# ── 3. 계정 앱이 살아 있고 스키마가 코드를 따라잡았는가 ─────────────────────
# 계정 앱에는 지금 경보가 하나도 없는데, 로그인·결제·팀과 모든 호스티드 키의 검증이
# 여기 있다. 허브가 키를 확인할 때마다 이 앱을 지난다.
#
# /api/health 는 연결만 보지 않고 마이그레이션 격차도 본다(2026-08-30 사고 이후).
# 격차가 있으면 503 에 {"status":"schema_behind",...} 를 낸다.
# 본문 실측 2026-09-01: {"status":"ok"}
#
# SWA 를 경유해서 찌른다. 컨테이너 앱 FQDN 을 직접 때리면 앞문이 401 을 낸다(설계대로다
# — account-deploy.yml 의 헬스체크 주석 참조).
resource "azurerm_application_insights_standard_web_test" "account_health" {
  name                    = "vinc-account-health"
  resource_group_name     = var.resource_group_name
  location                = "koreacentral"
  application_insights_id = local.insights_id
  geo_locations           = local.probe_locations
  frequency               = 300
  timeout                 = 30
  enabled                 = true
  retry_enabled           = true
  description             = "계정 앱이 살아 있고 스키마 격차가 없는가"

  request {
    url       = "https://vincs.io/api/health"
    http_verb = "GET"
  }

  validation_rules {
    expected_status_code = 200
    ssl_check_enabled    = true

    # 상태 코드만 보면 안 된다: schema_behind 는 503 이라 잡히지만, 앞으로 다른 저하
    # 상태가 200 으로 나올 수 있다. 값 자체를 본다.
    content {
      content_match      = "\"status\":\"ok\""
      pass_if_text_found = true
      ignore_case        = false
    }
  }
}

# ── 경보 ────────────────────────────────────────────────────────────────────
# 셋 다 기존 vinc-alerts 작업 그룹을 그대로 쓴다. 새 알림 경로를 만들지 않는다.
#
# dimension 이 이 블록의 급소다. 차원 이름은 availabilityResult/name 이고 단수 Result
# 다(복수인 것은 메트릭 이름 쪽뿐이다). 2026-09-01 에 metrics list-definitions 로
# 확인했다. 틀리면 필터가 아무것도 맞히지 못해 경보가 조용히 안 울린다 — 이 파일이
# 고치려는 바로 그 고장이다.
#
# 셋 다 severity 1 이다. 어느 하나가 죽어도 제품이 안 되는 것이지 느려지는 것이 아니다.
locals {
  functional_probes = {
    "oauth-discovery-down" = {
      probe       = azurerm_application_insights_standard_web_test.oauth_discovery.name
      description = "OAuth discovery 가 실패 — issuer 가 빠지면 커넥터가 전면 불가다 (2026-07-30)"
    }
    "internal-path-blocked" = {
      probe       = azurerm_application_insights_standard_web_test.internal_path_reaches_app.name
      description = "내부 경로가 앞문에서 끊겼다 — 호스티드 키와 팀 주소가 죽는다 (2026-08-04, 08-30)"
    }
    "account-health-down" = {
      probe       = azurerm_application_insights_standard_web_test.account_health.name
      description = "계정 앱이 실패하거나 스키마가 뒤졌다 — 로그인·결제·키 검증이 여기 있다"
    }
  }
}

resource "azurerm_monitor_metric_alert" "functional" {
  for_each = local.functional_probes

  name                = each.key
  resource_group_name = var.resource_group_name
  scopes              = [local.insights_id]
  description         = each.value.description
  severity            = 1
  frequency           = "PT5M"
  window_size         = "PT15M"
  auto_mitigate       = false

  criteria {
    metric_namespace = "microsoft.insights/components"
    metric_name      = "availabilityResults/availabilityPercentage"
    aggregation      = "Average"
    operator         = "LessThan"
    threshold        = 100

    dimension {
      name     = "availabilityResult/name"
      operator = "Include"
      values   = [each.value.probe]
    }
  }

  action {
    action_group_id = local.action_group_id
  }
}

# ── 되돌릴 수 없는 것 하나에만 감시가 없었다 ────────────────────────────────
# 2026-09-02 실측: 이 리소스 그룹의 비용 벡터 중 **유일하게 되돌릴 수 없는 것**이
# MySQL 스토리지 자동 증가인데, 그것만 경보가 없었다. 위 넷은 전부 가용성이고,
# 가용성 사고는 고치면 원상복구된다. 이건 아니다:
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
# severity 2 인 이유: 1은 "제품이 안 된다"에 쓴다(위 넷). 이건 지금 아무것도 안
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
# severity 3 인 이유: 1은 "제품이 안 된다"(가용성 넷), 2는 "되돌릴 수 없다"(DB 래칫).
# 이건 아무것도 안 망가진 상태에서 "슬슬 올려라"라고 말하는 것이라 셋째 급이다.
# 급을 아껴 쓰지 않으면 급이 정보를 잃는다.
#
# ⚠ 이 앱은 terraform 이 안 든다(generated.tf 에 없다) — 그래서 리소스 참조가 아니라
# id 문자열이다. 위 web test 들이 insights_id 를 그렇게 쓰는 것과 같은 이유다.
locals {
  account_app_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.App/containerApps/vinc-account"
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
