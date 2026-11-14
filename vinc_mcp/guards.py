# -*- coding: utf-8 -*-
"""허브의 두 입구(MCP 도구 호출 `tools.call`, 웹 프록시 `proxy_ops.proxy_op`)가 같이 쓰는 가드 둘.

- `strip_client_authority`: 클라이언트가 인자로 실어 보낸 권한 값을 벗긴다. 소유자·테마·진단·자격은
  서버가 인증에서 해석한 값만 믿는다.
- `run_state_writes`: 실행 기록의 프로토콜 상태를 고치려는 노드를 찾는다. 규칙은 `store/run_state.py`
  `writes` 한 벌이고, 도구 쓰기(`vinc_ingest`)와 프록시 쓰기(`ingest-fragment`·`ingest-pack`)가 이
  함수로 부른다. 기기 동기화는 거절하지 않고 `store/run_state.py` `keep_own` 이 받는 스토어의 값을 남긴다.

이 모듈은 `store/` 만 가져온다. `tools` 와 `proxy_ops` 가 모두 이것을 가져오므로 둘 중 어느 쪽도 가져오지 않는다.
"""
from __future__ import annotations

from store import run_state

#: 클라이언트가 보내도 믿지 않는 인자. 서버가 인증에서 정한다.
#: - owner: 어느 그래프에 닿는가(다른 회원의 스코프에 닿지 못하게 덮어쓴다)
#: - theme_id: 어휘 테마. 테마 파일에는 소유 열이 없어 고르게 두면 남의 어휘에 타입이 꽂힌다
#: - debug: 실행 계획(Cypher·파라미터)을 싣는 로컬 진단 스위치
#: - _entitlements: 주제 상한 같은 자격. 서버가 해석한 값만 실린다
CLIENT_AUTHORITY = ("owner", "theme_id", "debug", "_entitlements")


def strip_client_authority(args: dict, *, decided: tuple[str, ...] = ()) -> dict:
    """`args` 에서 `CLIENT_AUTHORITY` 의 이름을 뺀 사본. 남는 이름의 순서와 값은 그대로다.

    `decided` 는 호출자가 이미 서버의 값으로 덮어써 둔 이름이라 벗기지 않는다. 덮어쓴 값까지 벗기면
    그 값을 잃는다. 기본값은 빈 튜플이라 넷을 모두 벗기고, 부르는 쪽이 자기 값을 나중에 싣는다.
    """
    return {k: v for k, v in args.items() if k in decided or k not in CLIENT_AUTHORITY}


def run_state_writes(nodes) -> list[str]:
    """실행 기록의 프로토콜 상태 필드나 역할을 고치려는 노드 id 들(공백을 벗긴 id). 없으면 빈 목록.

    도구로 `state`·`approved`·`answer` 를 바꿀 수 있으면 에이전트 자격이 사람만 답하는 대기를 스스로
    풀 수 있다. 판정은 `store/run_state.py` `writes` 이고 여기서 다시 쓰지 않는다."""
    return run_state.writes(nodes)
