# __generated__ by Terraform
# Please review these resources and move them into your main configuration files.

# __generated__ by Terraform from "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG"
resource "azurerm_resource_group" "vincs" {
  location   = "koreacentral"
  managed_by = null
  name       = "vincsRG"
  tags       = {}
}

# __generated__ by Terraform from "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.Insights/components/vinc-uptime"
resource "azurerm_application_insights" "vinc_uptime" {
  application_type                     = "web"
  daily_data_cap_in_gb                 = 100
  daily_data_cap_notifications_enabled = true
  force_customer_storage_for_profiler  = false
  internet_ingestion_enabled           = true
  internet_query_enabled               = true
  ip_masking_enabled                   = true
  local_authentication_enabled         = true
  location                             = "koreacentral"
  name                                 = "vinc-uptime"
  resource_group_name                  = "vincsRG"
  retention_in_days                    = 90
  sampling_percentage                  = 0
  tags                                 = {}
  workspace_id                         = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.OperationalInsights/workspaces/workspace-vincssM9K"
}

# __generated__ by Terraform from "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.ContainerRegistry/registries/vincacr"
# __generated__ by Terraform
resource "azurerm_mysql_flexible_server" "vinc_mysql01" {
  administrator_login               = "vincadmin"
  backup_retention_days             = 7
  create_mode                       = null
  delegated_subnet_id               = null
  geo_redundant_backup_enabled      = false
  location                          = "koreacentral"
  name                              = "vinc-mysql01"
  point_in_time_restore_time_in_utc = null
  private_dns_zone_id               = null
  # 공개 접근은 끈다. 계정 앱과 DB job 은 VNet(vinc-vnet)의 프라이빗 엔드포인트 vinc-mysql01-pe 로 붙는다.
  # 다시 켜면 방화벽 규칙이 없어도 공개 주소가 열린다. 긴급 경로는 런북 vinc.ops.vnet-cutover-2026-11 의 14절.
  public_network_access = "Disabled"
  replication_role      = "None"
  resource_group_name   = "vincsRG"
  sku_name              = "B_Standard_B1ms"
  source_server_id      = null
  tags                  = {}
  version               = "8.0.21"
  zone                  = jsonencode(3)
  storage {
    auto_grow_enabled   = true
    io_scaling_enabled  = false # 쓰는 IOPS 가 기본 360 의 4% 미만인데 Paid IO 가 매일 약 730원이었다(2026-10-17)
    iops                = 360
    log_on_disk_enabled = false
    size_gb             = 20
  }
}

# __generated__ by Terraform from "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.RecoveryServices/vaults/vincs-backup"
resource "azurerm_recovery_services_vault" "vincs_backup" {
  classic_vmware_replication_enabled = false
  cross_region_restore_enabled       = false
  immutability                       = "Disabled"
  location                           = "koreacentral"
  name                               = "vincs-backup"
  public_network_access_enabled      = true
  resource_group_name                = "vincsRG"
  sku                                = "Standard"
  storage_mode_type                  = "GeoRedundant"
  tags                               = {}
  monitoring {
    alerts_for_all_failover_issues_enabled         = true
    alerts_for_all_job_failures_enabled            = true
    alerts_for_all_replication_issues_enabled      = true
    alerts_for_critical_operation_failures_enabled = true
    email_notifications_for_site_recovery_enabled  = true
  }
}

# __generated__ by Terraform
resource "azurerm_container_app_environment" "vinc_cae" {
  dapr_application_insights_connection_string = null # sensitive
  infrastructure_resource_group_name          = null
  infrastructure_subnet_id                    = null
  location                                    = "koreacentral"
  log_analytics_workspace_id                  = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.OperationalInsights/workspaces/workspace-vincssM9K"
  logs_destination                            = "log-analytics"
  mutual_tls_enabled                          = false
  name                                        = "vinc-cae"
  public_network_access                       = "Enabled"
  resource_group_name                         = "vincsRG"
  tags                                        = {}
  workload_profile {
    maximum_count         = 0
    minimum_count         = 0
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

# __generated__ by Terraform
# __generated__ by Terraform
# __generated__ by Terraform from "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.OperationalInsights/workspaces/workspace-vincssM9K"
resource "azurerm_log_analytics_workspace" "vincs" {
  allow_resource_only_permissions         = true
  cmk_for_query_forced                    = false
  daily_quota_gb                          = 1
  data_collection_rule_id                 = null
  immediate_data_purge_on_30_days_enabled = false
  internet_ingestion_enabled              = true
  internet_query_enabled                  = true
  local_authentication_enabled            = null
  location                                = "koreacentral"
  name                                    = "workspace-vincssM9K"
  reservation_capacity_in_gb_per_day      = null
  resource_group_name                     = "vincsRG"
  retention_in_days                       = 30
  sku                                     = "PerGB2018"
  tags                                    = {}
}

# __generated__ by Terraform from "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.Insights/actionGroups/vinc-alerts"
resource "azurerm_monitor_action_group" "vinc_alerts" {
  enabled             = true
  location            = "global"
  name                = "vinc-alerts"
  resource_group_name = "vincsRG"
  short_name          = "vincalert"
  tags                = {}
  email_receiver {
    # 기계가 보내는 메일의 받는 주소다. Cloudflare 가 운영자 Gmail 로 넘긴다.
    email_address           = "infra@vincs.io"
    name                    = "owner"
    use_common_alert_schema = false
  }
}
