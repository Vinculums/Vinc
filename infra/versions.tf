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
  required_version = ">= 1.5"   # `import` 블록이 1.5 부터다. 아래 imports.tf 가 그걸 쓴다.

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }

  # ⚠ 원격 backend 를 **아직 안 건다.** 그것 자체가 새 리소스(blob 컨테이너)를 만드는
  # 상태 변경이고, 이 리포는 상태 변경을 사람의 행위로 묶어 뒀다. 1판은 로컬 state 로
  # 임포트와 drift 만 본다.
  #
  # ⚠ **state 는 평문 시크릿을 담는다.** 그래서 `.gitignore` 가 막고, 아래 imports.tf 가
  # 컨테이너 앱 셋을 관리 대상에서 뺀다 — 허브 env 아홉 중 다섯이 시크릿이라
  # (`check_hub_env.py` REQUIRED), 그것을 임포트하는 순간 state 파일이 그 다섯을
  # 평문으로 들고 있게 된다. 원격 backend 를 걸기 전에는 들이지 않는다.
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
