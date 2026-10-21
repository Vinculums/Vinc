# 임포트 목록 — 손으로 리소스 블록을 쓰지 않는다.
#
# Terraform 1.5 의 `import` 블록은 선언적이라 PR 에서 리뷰되고, `terraform plan` 에
# `-generate-config-out` 을 붙이면 **설정을 기계가 써 준다**:
#
#     terraform init
#     terraform plan -generate-config-out=generated.tf
#
# 그 산출물을 다듬어 generated.tf 로 커밋했다(2026-08-30). plan 은 `12 to import,
# 0 to add, 2 to change, 0 to destroy` 다. 남은 둘은 drift 가 아니라 **공급자가 요구하는
# 정규화**다: log analytics 의 `local_authentication_enabled`(실물 disableLocalAuth 가
# null = 이미 켜짐)와 metric alert 의 `metric_namespace`(공급자 필수, 실물엔 없음).
# 둘 다 같은 값을 다시 쓰는 것이라 동작이 안 바뀐다. 0 으로 만들려면 ignore_changes 로
# 진짜 관리해야 할 필드까지 덮어야 해서 그게 더 나쁘다.
#
# 손으로 열두 벌을 받아쓰면 실물과 어긋난 자리가 생기고, 그 어긋남은 `plan` 이 초록일
# 때까지 안 보인다.
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
# ⚠ **vincacr · vincstorage01 · vinc-site 도 빠졌다(2026-09-01에 뺐다).** 1판에서는
#    들어 있었는데, 그 셋이 정확히 `listCredentials`·`listKeys`·`listSecrets` 를 태우는
#    것들이다 — 레지스트리 자격증명, 스토리지 계정 키, 배포 토큰. 로컬 state 에서도
#    나빴고 원격 state 에서는 더 나쁘다(blob 에 눕는다).
#
#    빼면서 무엇을 잃는지 정직하게: 그 셋의 drift 를 이 설정이 못 본다. 대신 얻은 것이
#    둘이고 둘 다 이 디렉터리의 목적에 직결된다.
#      - **state 에 시크릿이 하나도 없다.** 그래서 원격 backend 를 걸 수 있고, 그래야
#        CI 가 apply 할 수 있다.
#      - **읽기 전용 신원으로 `plan` 이 끝까지 간다.** 1판에서는 그 셋 때문에 403 이
#        나서 `-target` 없이는 못 돌렸다. 에이전트가 스스로 검증할 수 있게 된다.
#
#    다시 들이려면 Key Vault 나 그에 준하는 자리가 먼저다. 그때는 컨테이너 앱 셋도
#    함께 볼 것 — 막힌 이유가 같다.
#
# ⚠ `Application Insights Smart Detection` 도 없다. 플랫폼이 스스로 만드는 작업
#    그룹이고 이름에 공백이 있다. 사람이 만든 것이 아니므로 사람이 선언할 것도 아니다.
#
# 그래서 이 설정이 덮는 것은 **없어지면 되세울 근거가 없고, 시크릿을 열지 않는 층**이다.

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
  to = azurerm_recovery_services_vault.vincs_backup
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.RecoveryServices/vaults/vincs-backup"
}

# ── 관측 ──────────────────────────────────────────────────────────────────────
# 장애 메일은 계정 앱의 감시 루프가 보내지만, 그 루프가 죽었다는 메일은 이 셋을 지나서만 온다
# (alerts.tf 1번). 조용히 사라지면 감시 루프가 멈춘 것을 아무도 모른다.

import {
  to = azurerm_log_analytics_workspace.vincs
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.OperationalInsights/workspaces/workspace-vincssM9K"
}

# ⚠ **Microsoft.Insights 아래 ID 는 대소문자가 계약이다.** `az resource list` 는
# `webtests`·`actiongroups`·`metricalerts` 로 소문자를 주는데 azurerm 의 파서는
# `webTests`·`actionGroups`·`metricAlerts` 를 요구하고, 어긋나면 "parsed Resource ID
# was missing a value for the segment at position 6" 라는 **자리 번호로만 말하는**
# 오류가 난다. az 출력을 그대로 붙여넣지 말 것.
import {
  to = azurerm_application_insights.vinc_uptime
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.Insights/components/vinc-uptime"
}

import {
  to = azurerm_monitor_action_group.vinc_alerts
  id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.Insights/actionGroups/vinc-alerts"
}
