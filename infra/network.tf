# 계정 DB(vinc-mysql01)를 사설 엔드포인트 뒤로 옮기기 위한 네트워크 층.
#
# 이 파일은 새 리소스만 선언한다. 기존 vinc-cae · 앱 셋 · vinc-mysql01 의 공용 접근과 방화벽은
# 여기서 건드리지 않는다. 앱을 새 환경으로 옮기는 일과 공용 접근을 끄는 일은 각자 별도 변경이다.
#
# 모양:
#   vinc-vnet (10.40.0.0/16)
#     cae-infra          10.40.0.0/23   Microsoft.App/environments 위임, vinc-cae-vnet 전용
#     private-endpoints  10.40.2.0/28   사설 엔드포인트 NIC 자리
#   vinc-cae-vnet        VNet 통합 workload profiles 환경, 외부(공용) 인그레스
#   vinc-mysql01-pe      vinc-mysql01 의 mysqlServer 하위 리소스로 가는 사설 엔드포인트
#   privatelink.mysql.database.azure.com  VNet 에 연결된 사설 DNS 영역
#
# 이름 풀이가 핵심이다. 앱은 지금처럼 vinc-mysql01.mysql.database.azure.com 으로 붙는다. 그 이름은
# privatelink 영역으로 CNAME 되고, 이 VNet 안에서는 사설 영역이 엔드포인트의 사설 IP 를 준다.
# 호스트 이름이 그대로라 TLS 인증서 검증도 그대로 통과한다. VNet 밖(기존 vinc-cae, 러너)은 계속
# 공용 IP 로 풀린다.
#
# NSG 와 NAT Gateway 는 두지 않는다. Container Apps 는 서브넷에 NSG 를 요구하지 않고, 이 환경의
# 나가는 트래픽은 플랫폼이 관리 리소스 그룹에 두는 공용 IP 로 나간다.

locals {
  # vinc-cae 와 vinc-mysql01 이 있는 지역. 사설 엔드포인트는 VNet 과 같은 지역이어야 한다.
  network_location = "koreacentral"
}

# 구독에 다른 VNet 이 없고 피어링도 없다. 주소는 Container Apps 예약 대역(169.254/16 ·
# 172.30/16 · 172.31/16 · 192.0.2/24 · 100.100/16 일부)과 겹치지 않으면 된다.
resource "azurerm_virtual_network" "vinc" {
  name                = "vinc-vnet"
  resource_group_name = var.resource_group_name
  location            = local.network_location
  address_space       = ["10.40.0.0/16"]
}

# 환경 전용 서브넷. 환경을 만든 뒤에는 크기를 바꿀 수 없다(바꾸려면 환경을 다시 만든다).
# workload profiles 환경의 최소는 /27 이고, /23 이면 Consumption 레플리카 약 2,490 개까지 받는다.
# 단일 리비전 모드의 교체 동안 주소가 잠깐 두 배로 들고, 스키마 준비용 잡도 이 서브넷을 쓴다.
#
# ⚠ 위임의 actions 는 Azure 가 붙이는 값과 같아야 한다. 다르면 다음 plan 이 이 서브넷을 바꾸려 든다.
resource "azurerm_subnet" "cae_infra" {
  name                 = "cae-infra"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vinc.name
  address_prefixes     = ["10.40.0.0/23"]

  delegation {
    name = "Microsoft.App.environments"

    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

# 사설 엔드포인트 NIC 만 사는 서브넷. 환경 서브넷은 환경 전용이라 엔드포인트를 따로 둔다.
# 위임하지 않는다. /28 은 Azure 예약 5개를 빼고 11개를 쓸 수 있다.
resource "azurerm_subnet" "private_endpoints" {
  name                 = "private-endpoints"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vinc.name
  address_prefixes     = ["10.40.2.0/28"]
}

# 새 Container Apps 환경. vinc-cae 와 같은 Consumption 프로필과 같은 Log Analytics 작업 영역을 쓴다.
#
# ⚠ internal_load_balancer_enabled 는 false 여야 한다. api.vincs.io · mcp.vincs.io 와 SWA 연결
#   백엔드가 인터넷에서 이 환경의 앱에 닿아야 하기 때문이다. true 로 바꾸는 것은 환경 재생성이다.
# ⚠ infrastructure_subnet_id · infrastructure_resource_group_name · zone_redundancy_enabled 는
#   바꾸면 환경을 지우고 다시 만든다(ForceNew). 그 위의 앱도 함께 사라진다.
#
# 관리 리소스 그룹(ME_ 접두)은 플랫폼이 만들고 관리한다. 거기에 인그레스용 공용 IP, 나가는
# 트래픽용 공용 IP, Standard Load Balancer 가 생기고 과금된다. 그 그룹 안은 손대지 않는다.
resource "azurerm_container_app_environment" "vinc_cae_vnet" {
  name                               = "vinc-cae-vnet"
  resource_group_name                = var.resource_group_name
  location                           = local.network_location
  infrastructure_subnet_id           = azurerm_subnet.cae_infra.id
  infrastructure_resource_group_name = "ME_vinc-cae-vnet_vincsRG_koreacentral"
  internal_load_balancer_enabled     = false
  public_network_access              = "Enabled"
  zone_redundancy_enabled            = false
  mutual_tls_enabled                 = false
  log_analytics_workspace_id         = azurerm_log_analytics_workspace.vincs.id
  logs_destination                   = "log-analytics"

  # Consumption 프로필은 공급자가 최소·최대 개수를 보내지 않는다. 이름과 유형만 둔다.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

# 사설 DNS 영역. 이름은 MySQL 유연한 서버의 Private Link 가 요구하는 그대로여야 한다.
resource "azurerm_private_dns_zone" "mysql" {
  name                = "privatelink.mysql.database.azure.com"
  resource_group_name = var.resource_group_name
}

# 이 VNet 안의 이름 풀이만 사설 영역을 본다. 자동 등록은 끈다(엔드포인트 레코드는 아래 영역
# 그룹이 넣는다).
resource "azurerm_private_dns_zone_virtual_network_link" "mysql" {
  name                  = "vinc-vnet"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.mysql.name
  virtual_network_id    = azurerm_virtual_network.vinc.id
  registration_enabled  = false
}

# vinc-mysql01 로 가는 사설 엔드포인트. 같은 리소스 그룹의 서버라 적용하는 신원이 승인 권한을
# 가지므로 연결은 자동 승인된다(is_manual_connection = false).
#
# ⚠ 이 엔드포인트가 생겨도 서버의 공용 접근과 방화벽 규칙은 그대로다. 공용 접근을 끄는 것은
#   앱이 모두 이 VNet 으로 옮긴 뒤의 별도 변경이다. 그 전에 끄면 지금의 앱과 워크플로가 끊긴다.
# ⚠ 이 엔드포인트를 지우면 공용 접근이 꺼진 서버에는 아무도 닿지 못한다.
resource "azurerm_private_endpoint" "mysql" {
  name                          = "vinc-mysql01-pe"
  resource_group_name           = var.resource_group_name
  location                      = local.network_location
  subnet_id                     = azurerm_subnet.private_endpoints.id
  custom_network_interface_name = "vinc-mysql01-pe-nic"

  private_service_connection {
    name                           = "vinc-mysql01"
    private_connection_resource_id = azurerm_mysql_flexible_server.vinc_mysql01.id
    subresource_names              = ["mysqlServer"]
    is_manual_connection           = false
  }

  # 엔드포인트의 사설 IP 를 위 영역에 A 레코드(vinc-mysql01)로 넣고, IP 가 바뀌면 따라 고친다.
  private_dns_zone_group {
    name                 = "mysql"
    private_dns_zone_ids = [azurerm_private_dns_zone.mysql.id]
  }
}
