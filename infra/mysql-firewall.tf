# vinc-mysql01 의 공용 방화벽 규칙.
#
# 영구 규칙은 컨테이너 앱 환경 vinc-cae 의 아웃바운드 IP 하나에 규칙 하나다. 그 환경의 앱 셋이 같은
# 목록을 쓰고, DB 에 붙는 것은 vinc-account 하나다. 목록은 vinc-cae-outbound-ips.json 에 있고,
# 실물과 같은지는 scripts/check_mysql_firewall.py 가 infra 워크플로의 plan·apply 잡에서 본다.
#
# ⚠ 이 환경은 VNet 없이 만들어졌다. 아웃바운드 IP 는 플랫폼 사정으로 바뀔 수 있고(Microsoft 문서:
#   "Outbound IPs might change over time"), 목록에 없는 IP 로 나가면 vinc-account 가 DB 에 새로
#   붙지 못한다. 그때는 그 IP 를 목록에 더해 apply 한다. 이 IP 들은 같은 지역 플랫폼의 다른 고객
#   앱도 나가는 주소라, 규칙이 막는 것은 Azure 전체가 아니라 그 바깥이다.
#
# GitHub 호스티드 러너는 여기 없다. 운영 DB 에 붙는 Vinc-homepage 잡이 자기 IP 하나를
# gha-runner-<epoch>-<run>-<attempt> 이름으로 열고 같은 잡 끝에서 지운다. 그 이름은 apply 가
# 건드리지 않는다(state 에 없는 규칙이다).

locals {
  vinc_cae_outbound_ips = toset(jsondecode(file("${path.module}/vinc-cae-outbound-ips.json")))
}

resource "azurerm_mysql_flexible_server_firewall_rule" "vinc_cae_outbound" {
  for_each = local.vinc_cae_outbound_ips

  name                = "vinc-cae-out-${replace(each.value, ".", "-")}"
  resource_group_name = azurerm_mysql_flexible_server.vinc_mysql01.resource_group_name
  server_name         = azurerm_mysql_flexible_server.vinc_mysql01.name
  start_ip_address    = each.value
  end_ip_address      = each.value
}
