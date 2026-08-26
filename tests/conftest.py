# -*- coding: utf-8 -*-
"""테스트 전역 가드 한 벌.

여기 있는 것은 **테스트가 서로를 오염시켜 초록이 거짓이 되는 길**을 막는 것뿐이다.
픽스처 편의 기능은 두지 않는다 — 이 리포의 테스트는 파일마다 자족적이고, 그 관례를
바꾸려고 만든 파일이 아니다.
"""
from __future__ import annotations

import os

import pytest

from _tmpjail import install as _install_tmp_jail

# 임시 디렉터리 우리. **수집보다 먼저** 걸어야 한다 — 모듈 최상위에서
# mkdtemp 를 부르는 파일이 있어서 픽스처로는 늦는다. 이유는 tests/_tmpjail.py.
_install_tmp_jail()

#: PK 네임스페이싱 스위치. `core-pkns` CI 잡은 이 값을 켜고 **전량**을 돈다.
_FLAG = "VINC_PK_NAMESPACED"


@pytest.fixture(autouse=True)
def _flag_is_not_leaked():
    """테스트가 `VINC_PK_NAMESPACED` 를 원래대로 되돌려 놓았는지 본다.

    실측으로 물린 고장이다(2026-08-23). `tests/test_pk_compose_write.py` 가 플래그를
    켜고 끝에서 **pop** 했는데, 그러면 켠 모드 잡에서 그 파일 뒤에 도는 모든 테스트가
    조용히 꺼진 채 돈다. 그 잡의 초록은 "전량이 켠 모드로 통과했다"가 아니라
    "앞부분만 돌았다"였고, 그 뒤에 숨어 있던 실패가 7건이었다(test_view_viewpoints).

    자기 테스트 안에서 켜고 끄는 것은 정상이다 — 여기서 재는 것은 **끝난 뒤의 값**
    하나뿐이다. 근거: [[d11223a9a5d109de5]] §7"""
    before = os.environ.get(_FLAG)
    yield
    after = os.environ.get(_FLAG)
    assert after == before, (
        f"{_FLAG} 를 {before!r} → {after!r} 로 두고 나갔다. 뒤에 도는 테스트가 "
        "다른 모드로 돌게 되고, 켠 모드 잡의 초록이 거짓이 된다 — 원래 값을 "
        "되돌려 놓을 것(monkeypatch 를 쓰면 자동이다)")
