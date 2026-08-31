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
    body      = "e30=" # {} 의 base64. 서명이 없으므로 앱은 401 로 답한다

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
