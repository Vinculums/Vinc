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
resource "azurerm_container_registry" "vincacr" {
  admin_enabled                                = true
  anonymous_pull_enabled                       = false
  azuread_authentication_as_arm_policy_enabled = true
  data_endpoint_enabled                        = false
  encryption                                   = []
  export_policy_enabled                        = true
  location                                     = "koreacentral"
  name                                         = "vincacr"
  network_rule_bypass_for_tasks_enabled        = false
  network_rule_bypass_option                   = "AzureServices"
  network_rule_set                             = []
  public_network_access_enabled                = true
  quarantine_policy_enabled                    = false
  resource_group_name                          = "vincsRG"
  retention_policy_in_days                     = 0
  role_assignment_mode                         = "LegacyRegistryPermissions"
  sku                                          = "Basic"
  tags                                         = {}
  trust_policy_enabled                         = false
}

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
  public_network_access             = "Enabled"
  replication_role                  = "None"
  resource_group_name               = "vincsRG"
  sku_name                          = "B_Standard_B1ms"
  source_server_id                  = null
  tags                              = {}
  version                           = "8.0.21"
  zone                              = jsonencode(3)
  storage {
    auto_grow_enabled   = true
    io_scaling_enabled  = true
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
resource "azurerm_storage_account" "vincstorage01" {
  access_tier                       = "Hot"
  account_kind                      = "StorageV2"
  account_replication_type          = "LRS"
  account_tier                      = "Standard"
  allow_nested_items_to_be_public   = false
  allowed_copy_scope                = null
  cross_tenant_replication_enabled  = false
  default_to_oauth_authentication   = false
  dns_endpoint_type                 = "Standard"
  edge_zone                         = null
  https_traffic_only_enabled        = true
  infrastructure_encryption_enabled = false
  is_hns_enabled                    = false
  large_file_share_enabled          = false
  local_user_enabled                = true
  location                          = "koreacentral"
  min_tls_version                   = "TLS1_0"
  name                              = "vincstorage01"
  nfsv3_enabled                     = false
  provisioned_billing_model_version = null
  public_network_access_enabled     = true
  queue_encryption_key_type         = "Service"
  resource_group_name               = "vincsRG"
  sftp_enabled                      = false
  shared_access_key_enabled         = true
  table_encryption_key_type         = "Service"
  tags                              = {}
  blob_properties {
    change_feed_enabled           = false
    default_service_version       = null
    last_access_time_enabled      = false
    versioning_enabled            = false
  }
  network_rules {
    bypass                     = ["None"]
    default_action             = "Allow"
    ip_rules                   = []
    virtual_network_subnet_ids = []
  }
  share_properties {
    retention_policy {
      days = 14
    }
  }
}

# __generated__ by Terraform
resource "azurerm_static_web_app" "vinc_site" {
  # ⚠ 지우지 말 것. 리포 연결(branch/url/token)은 셋이 함께여야 하는데 token 이
  # 시크릿이라 설정에 못 둔다. 이 블록이 없으면 apply 가 살아 있는 GitHub 연결을
  # null 로 밀어 **사이트 배포를 끊는다**(2026-08-30 plan 에서 실제로 그렇게 나왔다).
  lifecycle {
    ignore_changes = [repository_branch, repository_url, repository_token]
  }

  app_settings                       = {}
  configuration_file_changes_enabled = true
  location                           = "eastasia"
  name                               = "vinc-site"
  preview_environments_enabled       = true
  public_network_access_enabled      = true
  resource_group_name                = "vincsRG"
  sku_size                           = "Standard"
  sku_tier                           = "Standard"
  tags                               = {}
}

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
# __generated__ by Terraform
# Please review these resources and move them into your main configuration files.

# __generated__ by Terraform from "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.Insights/webTests/vinc-hub-health"
resource "azurerm_application_insights_standard_web_test" "vinc_hub_health" {
  application_insights_id = "/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/Microsoft.Insights/components/vinc-uptime"
  description             = null
  enabled                 = true
  frequency               = 300
  geo_locations           = ["apac-jp-kaw-edge", "apac-sg-sin-azr", "us-ca-sjc-azr"]
  location                = "koreacentral"
  name                    = "vinc-hub-health"
  resource_group_name     = "vincsRG"
  retry_enabled           = true
  tags                    = {}
  timeout                 = 30
  request {
    body                             = null
    follow_redirects_enabled         = false
    http_verb                        = "GET"
    parse_dependent_requests_enabled = false
    url                              = "https://mcp.vincs.io/health"
  }
  validation_rules {
    expected_status_code        = 0
    ssl_cert_remaining_lifetime = 30
    ssl_check_enabled           = true
  }
}

# __generated__ by Terraform
resource "azurerm_monitor_metric_alert" "hub_health_down" {
  auto_mitigate            = false
  description              = "허브 /health 가 실패 - 사람이 우연히 발견하지 않게"
  enabled                  = true
  frequency                = "PT5M"
  name                     = "hub-health-down"
  resource_group_name      = "vincsRG"
  scopes                   = ["/subscriptions/5d3dbbfd-9770-460a-aede-0f13c0e1683c/resourceGroups/vincsRG/providers/microsoft.insights/components/vinc-uptime"]
  severity                 = 1
  tags                     = {}
  target_resource_location = null
  target_resource_type     = null
  window_size              = "PT15M"
  action {
    action_group_id    = "/subscriptions/5D3DBBFD-9770-460A-AEDE-0F13C0E1683C/resourceGroups/vincsRG/providers/microsoft.insights/actionGroups/vinc-alerts"
    webhook_properties = {}
  }
  criteria {
    aggregation            = "Average"
    metric_name            = "availabilityResults/availabilityPercentage"
    metric_namespace       = "microsoft.insights/components"
    operator               = "LessThan"
    skip_metric_validation = false
    threshold              = 100
  }
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
    email_address           = "weg1456@gmail.com"
    name                    = "owner"
    use_common_alert_schema = false
  }
}
