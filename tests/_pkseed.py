# -*- coding: utf-8 -*-
"""스토어에 **직접** 심는 e2e 픽스처용 시드 실행기.

테스트 파일 둘(`test_embedded_backend`·`test_view_viewpoints`)이 같은 모양의
픽스처를 갖고 있어서 사본 두 벌 대신 여기 한 벌로 둔다. 이 리포의 테스트는
보통 자족적이지만, 이 헬퍼는 **PK 계약**을 구현하는 것이라 갈라지면 한쪽만
고쳐지는 형태가 된다(그 함정은 `api/procutil.py` 에서 이미 한 번 물렸다).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import pkns  # noqa: E402


#: 픽스처가 Cypher 안에 **리터럴로** 박아 둔 노드 PK 를 찾는 표현. 여섯 소유 라벨의
#: PK 만 본다 — `chunk_id` 처럼 owner 로 합성되지 않는 키는 건드리면 안 된다
#: (PR-B 가 합성하는 라벨은 Concept·Record·Episode·Decision·Bridge·Entity 뿐).
_PK = r"(concept_id|record_id|episode_id|decision_id|bridge_id|entity_id)"
_PK_LIT = re.compile(_PK + r"\s*(:|=)\s*'([^']*)'")
#: 같은 자리에 **파라미터**가 온 형태. 이름 목록을 손으로 들지 않고 Cypher 에서
#: 읽는다 (목록은 낡고 Cypher 는 그 자리에 있다).
_PK_PARAM = re.compile(_PK + r"\s*(?::|=)\s*\$(\w+)")


def _seed(runner, cypher: str, *, owner: str = "u", **params):
    """픽스처를 스토어에 **직접** 심는 실행기.

    이 파일의 e2e 픽스처는 질의 엔진·검색 팔을 재는 것이 목적이라 API
    (`merge_fragment`·`put_doc`)를 일부러 우회한다. 그런데 PK 네임스페이싱(design A)이
    켜지면 읽기 플랜이 owner 로 합성한 **물리 키**로 묻기 때문에, 시드가 논리 슬러그로
    심으면 조회가 0행이 된다. 그래서 Cypher 에 박힌 PK 리터럴을 파라미터로 빼고 여기서
    한 번 합성한다. 플래그가 꺼져 있으면 항등이라 종전과 같은 값이 들어간다.

    리터럴을 문자열로 되붙이지 않고 **파라미터로** 빼는 이유: 값을 Cypher 문자열에
    끼워 넣는 것은 이 리포가 게이트로 막는 형태다(`scripts/gate_escape.py`).
    근거: [[vinc.plan.node-pk-namespacing]] §7"""
    bound: dict[str, str] = {}

    def _sub(m):
        name = f"_pk{len(bound)}"
        bound[name] = pkns.key(owner, m.group(3))
        return f"{m.group(1)}{m.group(2)} ${name}"

    out = _PK_LIT.sub(_sub, cypher)
    # 파라미터 자리도 같이 합성한다. ⚠ 그러니 **PK 자리에 오는 파라미터를 값
    #    자리에 재사용하지 말 것** — 제목에 분리자가 박힌다(실측으로 물렸다).
    #    `{concept_id:$id}) SET n.title=$id` 같은 시드는 이름을 갈라 놓았다.
    for m in _PK_PARAM.finditer(out):
        key = m.group(2)
        if key in params and isinstance(params[key], str):
            params[key] = pkns.key(owner, params[key])
    return runner(out, **params, **bound)
