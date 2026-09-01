# vincsRG 를 코드로 — 지금 도는 것과 적힌 것을 diff 할 수 있게 한다.
#
# 왜 생겼나. `hub-deploy.yml` 이 스스로 적어 뒀다: "허브 런타임 env 는 IaC 가 아니라
# 손으로 넣은 값이라 앱을 재생성하면 조용히 빠진다." `scripts/check_hub_env.py` 가 그
# 구멍을 **배포 직전에** 막고 있지만, 검사는 "있나"를 볼 뿐 **없어진 것을 되세우지
# 못한다**. vincsRG 의 나머지 열여섯 리소스는 그 검사조차 없다.
#
# 인증. azurerm 은 `ARM_*` 를 먼저 보고 없으면 `AZURE_*` 를 본다. `vinc-local-ops`
# 서비스 주체(vincsRG Contributor)를 env 로 넣어 두면 대화형 로그인 없이 돈다.
# 근거와 발급 절차: [[vinc.spec.azure-infrastructure]].

terraform {
  required_version = ">= 1.5" # `import` 블록이 1.5 부터다. 아래 imports.tf 가 그걸 쓴다.

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }

  # ── 원격 state (2026-09-01) ────────────────────────────────────────────────
  # 로컬 state 로는 CI 가 apply 할 수 없다. 워크플로는 매번 빈 디스크로 시작하므로
  # 상태를 들고 있을 자리가 없고, 그러면 apply 가 영원히 사람의 셸에만 살아서
  # **무엇이 언제 적용됐는지가 어디에도 안 남는다.** 이 리포가 배포에서 이미 겪은
  # 형태다(컨테이너 안에서 난 검증이 기록을 안 남겨 `verify-hosted` 를 만들었다).
  #
  # ⚠ **state 에서 시크릿을 없앤 뒤에야 이걸 걸 수 있었다.** 그 전에는 세 리소스가
  # `listKeys`·`listSecrets`·`listCredentials` 를 태워 스토리지 키·배포 토큰·레지스트리
  # 자격증명을 state 로 끌어왔다. 원격 state 는 그것을 blob 에 눕히는 것이라 더 나쁘다.
  # 그래서 imports.tf 가 그 셋을 관리 대상에서 뺐고, **지금 이 설정이 만지는 것 중
  # 시크릿을 여는 것은 하나도 없다.** 컨테이너 앱 셋도 같은 이유로 계속 빠져 있다.
  #
  # ⚠ **계정 키를 쓰지 않는다.** `use_azuread_auth` 로 Entra 신원이 blob 에 직접 붙는다.
  # 계정 키를 쓰면 그 키를 CI 시크릿에 넣어야 하고, 그러면 `listKeys` 를 없앤 의미가
  # 사라진다. 대신 신원마다 데이터 평면 역할이 하나씩 필요하다(README 아닌 PR 본문 참조):
  #   CI(OIDC)          Storage Blob Data Contributor  → apply
  #   vinc-agent-reader Storage Blob Data Reader       → plan (`-lock=false` 로)
  # 그 두 줄이 곧 plan 과 apply 의 경계다. 제어 평면의 Reader/Contributor 분리와 같은
  # 모양이 데이터 평면에서 한 번 더 선다.
  backend "azurerm" {
    resource_group_name  = "vincsRG"
    storage_account_name = "vincstorage01"
    container_name       = "tfstate"
    key                  = "vincsrg.tfstate"
    use_azuread_auth     = true
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id

  # ⚠ 끄지 않으면 **plan 자체가 죽는다.** azurerm 은 기본으로 리소스 공급자 40여 개를
  # `/subscriptions/<id>` 스코프에서 등록하려 드는데, `vinc-local-ops` 는 일부러
  # vincsRG 로 좁혀 둔 신원이라 전부 403 이 난다(실측 2026-08-30).
  #
  # 고치는 방향은 **RBAC 을 넓히는 쪽이 아니다.** 여기 쓰는 공급자는 리소스가 이미
  # 도는 이상 전부 등록돼 있고, 등록은 구독 관리자의 일회성 행위지 배포마다 할 일이
  # 아니다. 좁은 신원이 이 사실을 드러낸 것이므로 신원을 넓혀 덮지 않는다.
  resource_provider_registrations = "none"
}

variable "subscription_id" {
  description = "vincs 구독. tenant 는 SP env(ARM_TENANT_ID)가 정한다."
  type        = string
  default     = "5d3dbbfd-9770-460a-aede-0f13c0e1683c"
}

variable "resource_group_name" {
  type    = string
  default = "vincsRG"
}
