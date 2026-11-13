#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vinc-mysql01 영구 방화벽 규칙의 원천 목록이 컨테이너 앱 환경의 실제 아웃바운드 IP 와 같은가.

infra/mysql-firewall.tf 는 infra/vinc-cae-outbound-ips.json 의 IP 하나마다 규칙 하나를 만든다.
vinc-cae 는 VNet 없는 환경이라 플랫폼이 아웃바운드 IP 를 바꿀 수 있고, 목록이 실물보다 낡으면
apply 가 vinc-account 의 새 연결을 막는다. 그래서 infra 워크플로가 plan 뒤, apply 전에 이것을 돈다.

사용(CI):
    az containerapp show --name vinc-account -g vincsRG \
      --query properties.outboundIpAddresses -o json | python scripts/check_mysql_firewall.py

stdin 으로 IP 배열 JSON 을 받는다. Azure 없이 테스트할 수 있게 한 설계다(check_hub_env.py 와 같다).
종료코드: 0 같다, 1 다르다, 2 입력이나 목록 파일이 틀렸다.
"""
from __future__ import annotations

import ipaddress
import json
import sys
from pathlib import Path

DECLARED = Path(__file__).resolve().parents[1] / "infra" / "vinc-cae-outbound-ips.json"

#: Azure Database for MySQL Flexible Server 한 대의 방화벽 규칙 상한. 러너 임시 규칙 몫을 남긴다.
RULE_CAP = 500
RUNNER_HEADROOM = 20

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass


def parse(raw: str, where: str) -> list[str]:
    """IPv4 문자열 배열 JSON 을 읽는다. 모양이 틀리면 ValueError."""
    data = json.loads(raw)
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise ValueError(f"{where}: IP 문자열 배열이 아니다")
    for x in data:
        ipaddress.IPv4Address(x)
    if len(set(data)) != len(data):
        raise ValueError(f"{where}: 같은 IP 가 두 번 있다")
    return data


def compare(declared: list[str], live: list[str]) -> tuple[list[str], list[str]]:
    """(실물에만 있는 것, 목록에만 있는 것). 둘 다 비면 같다."""
    key = ipaddress.IPv4Address
    missing = sorted(set(live) - set(declared), key=key)
    stale = sorted(set(declared) - set(live), key=key)
    return missing, stale


def main(stdin_text: str, declared_text: str) -> int:
    try:
        declared = parse(declared_text, DECLARED.name)
        live = parse(stdin_text, "stdin")
    except (ValueError, json.JSONDecodeError) as e:
        print(f"::error::{e}", file=sys.stderr)
        return 2
    if not live:
        print("::error::stdin 의 아웃바운드 IP 가 비었다. 조회가 실패했을 수 있다", file=sys.stderr)
        return 2
    if len(declared) > RULE_CAP - RUNNER_HEADROOM:
        print(f"::error::영구 규칙 {len(declared)}개는 상한 {RULE_CAP} 에서 러너 몫 {RUNNER_HEADROOM} 을 "
              "남기지 못한다", file=sys.stderr)
        return 1
    missing, stale = compare(declared, live)
    if not missing and not stale:
        print(f"vinc-cae 아웃바운드 IP {len(live)}개가 {DECLARED.name} 과 같다")
        return 0
    print(f"::error::{DECLARED.name} 이 vinc-cae 의 실제 아웃바운드 IP 와 다르다 "
          f"(실물에만 {len(missing)}개, 목록에만 {len(stale)}개). 목록을 실물로 맞춘 뒤 plan 을 다시 볼 것",
          file=sys.stderr)
    for ip in missing:
        print(f"  + {ip}  (실물에 있고 목록에 없다: 이 IP 로 나가는 연결이 막힌다)")
    for ip in stale:
        print(f"  - {ip}  (목록에 있고 실물에 없다)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.stdin.read(), DECLARED.read_text(encoding="utf-8")))
