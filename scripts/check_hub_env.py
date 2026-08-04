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
    # 2026-08-04 사고. 코드 기본값이 `http://127.0.0.1:8080` 인데 그건 **가비아 VM
    # 시절 계정과 허브가 같은 호스트에 있던 때의 값**이다. Azure 에서 둘은 서로
    # 다른 Container App(vinc-hub:8790 / vinc-account:8080)이라 루프백에 아무것도
    # 없다 → verify_key 가 연결 실패 → fail-closed → **모든 `vinc_sk_*` 키가 401**.
    # /health 에도, 로그에도 원인이 안 남는다. VINC_MCP_ISSUER(§4.4.1)와 같은
    # 균열의 두 번째 사례이고, 그때 만든 이 게이트가 이 키를 안 보고 있었다.
    "VINC_ACCOUNT_INTERNAL_URL": "회원 API 키 검증 루프백. 없으면 127.0.0.1:8080 "
                                 "폴백 → vinc_sk_* 키 전부 401(2026-08-04 사고)",
}

# 있어야 정책이 맞는 것들(없어도 기동은 한다) — 경고만.
EXPECTED: dict[str, str] = {
    "VINC_QUOTA": "on 이 아니면 툴 호출 무제한. 사이트는 '하루 2,000회'로 안내 중",
    "VINC_FREE_MAX_DOMAINS": "없으면 무료 주제 상한이 꺼진다 — AP-1 의 유일한 무료/유료 차별축",
}

# 값까지 보는 항목(2026-08-03). 위 규율("존재만 본다")의 의도적 예외다 — 이건
# 시크릿이 아니라 **경로**이고, 틀린 값이 배포를 두 번이나 망가뜨렸다.
#
# 배경(사고 둘):
#  ① 상태 DB 가 `~/.vinc/mcp`(컨테이너에선 /root = 이미지 레이어)로 폴백해 배포마다
#     OAuth 등록이 날아갔다 → 커넥터가 `400 unknown client_id` 로 영구히 막혔다.
#  ② 그래서 상태 DB 를 영속 볼륨(`/data/mcp`)으로 옮겼더니 **SQLite 가 SMB 에서
#     `database is locked` 로 기동 실패**해 허브가 내려갔다.
#
# 그래서 지금 구조는 **작업본은 로컬, 스냅샷만 영속 볼륨**이다. 검사 방향이 ①때와
# 정반대라는 점이 중요하다: `VINC_MCP_STATE_DIR`(작업본)이 `/data` 를 가리키면
# **그게 ②를 재발시키는 값**이다.
_STATE_DIR_KEY = "VINC_MCP_STATE_DIR"
_SNAPSHOT_KEY = "VINC_MCP_STATE_SNAPSHOT"
#: 영속 마운트 접두(Azure Files 가 /data 에 붙는다 — DEPLOYMENT.md §4.1).
_PERSISTENT_PREFIX = "/data/"

#: 시크릿이 아니라 **주소**인 값들. 비어 있으면(secretRef 로 넣었으면) 검증할 수
#: 없으므로 그것 자체를 오류로 본다 — 위 두 경로 키와 같은 규율이다.
_PLAINTEXT_KEYS = ("VINC_DATA_DIR", "VINC_ACCOUNT_INTERNAL_URL",
                   "VINC_CONNECT_URL", "VINC_MCP_ISSUER")

#: A 레코드가 없는 죽은 존. 발신(Resend) 레코드 4종만 남아 있다 —
#: WEB-vincs-app.md §6.1. 여기를 가리키는 URL 은 **연결 자체가 실패**한다.
#: 전례가 둘이다: `VINC_CONNECT_URL`(§4.4.1) · Resend 웹훅 엔드포인트(2026-08-04
#: 에 Resend 가 7/30 부터의 연속 실패로 웹훅을 자동 비활성화했다).
_DEAD_HOSTS = ("vincs.app",)


def _value_of(env_list: list[dict], key: str) -> str | None:
    """env 배열에서 이름으로 값을 찾는다. 미설정이면 None, 값 없음이면 ''."""
    for e in env_list:
        if isinstance(e, dict) and e.get("name") == key:
            return (e.get("value") or "").strip()
    return None


def _check_data_dir(env_list: list[dict]) -> list[str]:
    """`VINC_DATA_DIR` 이 **영속 마운트 안**을 가리키는지 본다.

    존재만 보던 항목에 값 검사를 붙인 이유(2026-08-04 사고): 복구 배포에서 이 값이
    `D:/git/data/db` 로 들어갔다. Windows Git Bash 가 `az` 인자의 `/data/db` 를
    MSYS 경로 변환으로 `D:\\git\\data\\db` 로 바꿔 심은 것이다(DEPLOYMENT.md §6 의
    리소스 ID 함정과 같은 원인, 다른 표면). 리눅스 컨테이너에서 그 값은 `D:` 라는
    이름의 **상대 경로**라 기동은 정상이고 `/health` 도 200 인데, 허브가 Azure
    Files 대신 이미지 레이어에 **빈 그래프를 새로 만들어** 서비스했다. 게이트는
    "존재함"만 보고 통과시켰다.
    """
    v = _value_of(env_list, "VINC_DATA_DIR")
    if not v:
        return []                        # 부재·공백은 필수/평문 검사가 잡는다
    if v.startswith(_PERSISTENT_PREFIX):
        return []
    return [f"VINC_DATA_DIR='{v}' 가 영속 마운트({_PERSISTENT_PREFIX}) 밖이다 — 허브가 "
            f"이미지 레이어에 빈 그래프를 만들어 서비스한다(기동·/health 는 정상이라 "
            f"드러나지 않는다). Windows Git Bash 에서 az 에 경로를 넘겼다면 MSYS "
            f"변환을 의심할 것(MSYS_NO_PATHCONV=1)"]


def _host_of(value: str) -> str:
    """URL·주소 문자열에서 호스트만 뽑는다(스킴·인증정보·포트·경로 제거)."""
    rest = value.split("//", 1)[1] if "//" in value else value
    return rest.split("/", 1)[0].split("@")[-1].split(":")[0].lower()


def _check_plaintext_and_hosts(env_list: list[dict]) -> list[str]:
    """주소 값들이 (a) 평문으로 보이는지 (b) 죽은 도메인이 아닌지.

    서브도메인까지 본다 — `mcp.vincs.app` 처럼 존이 죽었으면 그 아래도 다 죽는다.
    """
    errs: list[str] = []
    for key in _PLAINTEXT_KEYS:
        v = _value_of(env_list, key)
        if v is None:
            continue                     # 부재는 REQUIRED 검사의 몫
        if not v:
            errs.append(f"{key} 값이 비어 있다(secretRef?). 주소는 시크릿이 아니고, "
                        f"평문이어야 검증할 수 있다")
            continue
        vhost = _host_of(v)
        for host in _DEAD_HOSTS:
            if vhost == host or vhost.endswith("." + host):
                errs.append(f"{key}='{v}' 가 죽은 도메인 {host} 를 가리킨다 — 그 존에는 "
                            f"Resend 발신 레코드만 남아 있고 A 가 없다(연결 실패). "
                            f"vincs.io 로 바꿀 것")
    return errs


def _check_state_dir(env_list: list[dict]) -> list[str]:
    """상태 경로 두 개의 **방향**을 본다. 반환 = 오류 메시지 목록(빈 목록이면 통과).

    둘 다 미설정이 정상이다 — 엔트리포인트가 기본값을 준다."""
    errs: list[str] = []
    work = _value_of(env_list, _STATE_DIR_KEY)
    if work is not None:
        if not work:
            errs.append(f"{_STATE_DIR_KEY} 값이 비어 있다(secretRef?). 경로는 평문이어야 "
                        f"검증할 수 있다")
        elif work.startswith(_PERSISTENT_PREFIX):
            errs.append(f"{_STATE_DIR_KEY}='{work}' 가 영속 볼륨({_PERSISTENT_PREFIX})을 "
                        f"가리킨다 — SQLite 작업본은 **로컬**이어야 한다. SMB 위에서는 "
                        f"database is locked 로 MCP 가 기동하지 못한다(2026-08-03 장애). "
                        f"영속은 {_SNAPSHOT_KEY}(파일 복사)가 담당한다")
    snap = _value_of(env_list, _SNAPSHOT_KEY)
    if snap is not None:
        if not snap:
            errs.append(f"{_SNAPSHOT_KEY} 값이 비어 있다(secretRef?). 경로는 평문이어야 한다")
        elif not snap.startswith(_PERSISTENT_PREFIX):
            errs.append(f"{_SNAPSHOT_KEY}='{snap}' 가 영속 볼륨({_PERSISTENT_PREFIX}) 밖이다 — "
                        f"배포마다 OAuth 등록이 사라져 커넥터가 400 unknown client_id 로 끊긴다")
    return errs


def check(env_list: list[dict]) -> int:
    have = {e.get("name") for e in env_list if isinstance(e, dict)}
    missing = [k for k in REQUIRED if k not in have]
    warn = [k for k in EXPECTED if k not in have]

    for k in warn:
        print(f"::warning::허브 env 권고값 없음: {k} — {EXPECTED[k]}")

    bad_paths = (_check_state_dir(env_list) + _check_data_dir(env_list)
                 + _check_plaintext_and_hosts(env_list))
    for msg in bad_paths:
        print(f"::error::허브 env 값 오류: {msg}", file=sys.stderr)

    if missing or bad_paths:
        for k in missing:
            print(f"::error::허브 env 필수값 누락: {k} — {REQUIRED[k]}", file=sys.stderr)
        # 무엇 때문에 막혔는지 정확히 적는다 — "0개 누락"으로 끝나면 경로 오류로
        # 막힌 사람이 누락을 찾아 헤맨다(게이트가 스스로 오진을 만드는 꼴).
        reason = ", ".join(
            p for p in (f"필수값 {len(missing)}개 누락" if missing else "",
                        f"값 오류 {len(bad_paths)}건" if bad_paths else "") if p)
        print(f"\n{reason}. 배포를 중단한다 — 지금 배포하면 "
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
