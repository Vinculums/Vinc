#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""허브 컨테이너 앱의 런타임 env 검증 — 배포 **전** 게이트.

**왜 필요한가.** 허브 런타임 env 는 어디에도 코드화돼 있지 않다. `az containerapp
create/update` 로 손으로 넣은 값이고 IaC 도 워크플로도 검증하지 않았다. 그래서 Azure
이관 때 `VINC_MCP_ISSUER` 하나가 조용히 빠졌고 **claude.ai 커넥터가 전면 불가**였다.

급소는 그 상태가 `/health` 에 전혀 드러나지 않는다는 것이다 — OAuth 가 통째로 꺼져도
200 이 나온다. 사람이 기억해서 4줄을 쳐야 알 수 있었다. 이 스크립트가 그 기억을 대신한다.

사용(CI):
    az containerapp show --name vinc-hub -g vincsRG \
      --query "properties.template.containers[0].env" -o json | python scripts/check_hub_env.py

stdin 으로 env 배열 JSON 을 받는다 — Azure 없이도 테스트할 수 있게 한 설계다.
정본 설명: Vinc-docs/vinc/DEPLOYMENT.md §4.4
"""
from __future__ import annotations

import json
import sys

# Windows 콘솔 기본 인코딩(cp949)에서 한국어 진단 문구가 UnicodeEncodeError 로 죽는다
# — 게이트가 **누락을 알리려다 스스로 터지면** 아무 의미가 없다. api/cli.py 와 같은 규율.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass

# 없으면 **기능이 죽는** 것들. 각 항목의 값은 보지 않는다(시크릿이라 볼 수도 없다) —
# 존재만 본다. 값이 틀린 경우는 배포 후 표면 검증(§4.4.2)이 잡는다.
REQUIRED: dict[str, str] = {
    "VINC_SIDECAR_TOKEN": "사이드카/MCP 내부 인증 — 없으면 엔트리포인트가 즉시 종료",
    "VINC_DATA_DIR": "Azure Files 마운트 지점. 불일치 시 데이터가 엉뚱한 곳에 쌓인다",
    "VINC_MCP_TOKEN": "정적 bearer — 없으면 CD 헬스검증 불가",
    "VINC_MCP_ISSUER": "★ 없으면 OAuth 전체가 꺼진다. /health 에는 안 드러난다(2026-07-30 사고)",
    "VINC_OAUTH_ADMIN_KEY": "부트스트랩 자동승인(/authorize?key=) 경로",
    "VINC_MCP_ED25519_SEED": "CRA 서명 — 없으면 회원 승인 흐름 비활성",
    "VINC_ACCOUNT_PUBLIC_KEY": "AA 검증 — 없으면 회원 승인 흐름 비활성",
    "VINC_CONNECT_URL": "동의 화면 URL. 죽은 도메인을 가리키면 승인이 막힌다",
}

# 있어야 정책이 맞는 것들(없어도 기동은 한다) — 경고만.
EXPECTED: dict[str, str] = {
    "VINC_QUOTA": "on 이 아니면 툴 호출 무제한. 사이트는 '무료 15회/일'로 안내 중",
}


def check(env_list: list[dict]) -> int:
    have = {e.get("name") for e in env_list if isinstance(e, dict)}
    missing = [k for k in REQUIRED if k not in have]
    warn = [k for k in EXPECTED if k not in have]

    for k in warn:
        print(f"::warning::허브 env 권고값 없음: {k} — {EXPECTED[k]}")

    if missing:
        for k in missing:
            print(f"::error::허브 env 필수값 누락: {k} — {REQUIRED[k]}", file=sys.stderr)
        print(f"\n{len(missing)}개 누락. 배포를 중단한다 — 지금 배포하면 "
              f"기동은 되지만 기능이 조용히 죽은 리비전이 나간다.", file=sys.stderr)
        return 1

    print(f"허브 env OK — 필수 {len(REQUIRED)}개 전부 존재"
          + (f", 권고 {len(warn)}개 누락" if warn else ""))
    return 0


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception as e:
        print(f"::error::env JSON 파싱 실패: {e}", file=sys.stderr)
        return 2
    if not isinstance(data, list):
        print("::error::env 배열이 아니다 — az --query 를 확인할 것", file=sys.stderr)
        return 2
    return check(data)


if __name__ == "__main__":
    raise SystemExit(main())
