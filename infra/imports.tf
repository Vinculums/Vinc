# 임포트 목록 — 손으로 리소스 블록을 쓰지 않는다.
#
# Terraform 1.5 의 `import` 블록은 선언적이라 PR 에서 리뷰되고, `terraform plan` 에
# `-generate-config-out` 을 붙이면 **설정을 기계가 써 준다**:
#
#     terraform init
#     terraform plan -generate-config-out=generated.tf
#
# 그 산출물을 읽고 다듬어 커밋하는 것이 이 디렉터리의 다음 걸음이다. 손으로 열두 벌을
# 받아쓰면 실물과 어긋난 자리가 생기고, 그 어긋남은 `plan` 이 초록일 때까지 안 보인다.
#
# ── 무엇을 넣지 않았나, 그리고 왜 ──────────────────────────────────────────────
#
# ⚠ **컨테이너 앱 셋(vinc-hub · vinc-account · vinc-encode)이 없다.** 두 이유다:
#
#   ① 시크릿. 허브 env 필수 아홉 중 다섯이 시크릿이다(`check_hub_env.py` REQUIRED —
#      VINC_SIDECAR_TOKEN · VINC_MCP_TOKEN · VINC_OAUTH_ADMIN_KEY ·
#      VINC_MCP_ED25519_SEED · VINC_ACCOUNT_PUBLIC_KEY). 임포트하면 state 가 그것을
#      평문으로 든다. 원격 backend 와 Key Vault 가 정해지기 전에는 들이지 않는다.
#   ② 이미지 태그. `hub-deploy` 가 배포마다 `vinc-hub:<sha>` 로 바꾼다. 관리 대상에
#      넣으면 매 배포가 drift 가 되고, `terraform apply` 가 방금 나간 리비전을
#      **되돌리려 든다**. 들일 때는 `lifecycle { ignore_changes = [...] }` 가 함께 와야
#      한다. 근거: [[vinc.ops.deployment]].
#
# ⚠ `Application Insights Smart Detection` 도 없다. 플랫폼이 스스로 만드는 작업
#    그룹이고 이름에 공백이 있다. 사람이 만든 것이 아니므로 사람이 선언할 것도 아니다.
#
# 그래서 1판이 덮는 것은 **없어지면 되세울 근거가 없는데 검사도 없는 층**이다.

import {
  to = azurerm_resource_group.vincs
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG"
}

# 컨테이너 앱 셋이 사는 환경. 볼륨 정의(Azure Files → /data)가 여기 있고, 그것이
# 그래프 DB 의 자리다. 이 리소스를 잃으면 앱 셋의 마운트를 재구성할 근거가 사라진다.
import {
  to = azurerm_container_app_environment.vinc_cae
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.App/managedEnvironments/vinc-cae"
}

import {
  to = azurerm_container_registry.vincacr
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.ContainerRegistry/registries/vincacr"
}

# 그래프 DB 가 아니라 **계정 DB** 다. `vinc-account` 가 DB_HOST/DB_NAME 으로 붙고
# `vinc_account` 스키마가 여기 있다. 그래프는 Azure Files 위 임베디드 스토어다 —
# 둘을 섞지 말 것.
import {
  to = azurerm_mysql_flexible_server.vinc_mysql01
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.DBforMySQL/flexibleServers/vinc-mysql01"
}

# 허브 DB 가 사는 스토리지 계정(hub-data 파일 공유). 스냅샷·핸들 강제 종료가 전부
# 이 계정 위에서 일어난다 — packaging/DEPLOY-RUNBOOK.md 참조.
import {
  to = azurerm_storage_account.vincstorage01
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.Storage/storageAccounts/vincstorage01"
}

import {
  to = azurerm_static_web_app.vinc_site
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.Web/staticSites/vinc-site"
}

import {
  to = azurerm_recovery_services_vault.vincs_backup
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.RecoveryServices/vaults/vincs-backup"
}

# ── 관측 ──────────────────────────────────────────────────────────────────────
# 이 넷이 "허브가 죽었다"를 사람에게 알리는 유일한 경로다. 하드다운 넷을 겪고 세운
# 것이라 조용히 사라지면 다음 하드다운을 아무도 모른다.

import {
  to = azurerm_log_analytics_workspace.vincs
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.OperationalInsights/workspaces/workspace-vincssM9K"
}

import {
  to = azurerm_application_insights.vinc_uptime
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.Insights/components/vinc-uptime"
}

import {
  to = azurerm_monitor_action_group.vinc_alerts
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.Insights/actiongroups/vinc-alerts"
}

import {
  to = azurerm_monitor_metric_alert.hub_health_down
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.Insights/metricalerts/hub-health-down"
}

# ⚠ 웹테스트는 azurerm 에 두 벌이다 — 고전(`azurerm_application_insights_web_test`)과
# 표준(`..._standard_web_test`). ARM 타입은 둘 다 `Microsoft.Insights/webtests` 라
# 구분이 안 된다. `plan` 이 갈라 줄 것이고, 틀렸으면 여기 한 줄을 바꾼다.
import {
  to = azurerm_application_insights_standard_web_test.vinc_hub_health
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.Insights/webtests/vinc-hub-health"
}
