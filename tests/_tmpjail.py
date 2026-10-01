# -*- coding: utf-8 -*-
"""테스트가 만드는 임시 디렉터리를 우리 하나에 가두고, 끝나면 통째로 지운다.

이 리포의 테스트는 `tempfile.mkdtemp()` 를 쉰여덟 군데에서 부르는데, `tearDown` 은
스토어를 닫고 env 를 되돌릴 뿐 **디렉터리를 지우지 않는다**. 그 mkdtemp 가 대부분
`setUp` 에 있으니 테스트 메서드 하나당 하나씩 시스템 임시 디렉터리에 쌓인다.

2026-08-26 에 그게 CI 를 통째로 세웠다. WSL 러너의 `/tmp` 가 15,902건까지 불어난
상태에서 데스크탑을 전원 재투입했더니 `systemd-tmpfiles-setup` 이 `/tmp` 를 훑느라
40분 넘게 `__wait_on_buffer` 에 묶였고, 그게 `sysinit.target` → dbus → 러너 서비스
둘(`actions.runner.Vinculums.vinc-wsl-linux`·`-2`)을 차례로 막았다. GitHub 에는
러너가 offline 로 보였고 모든 PR 의 모든 잡이 실패하거나 무한 대기했다. **증상이
"테스트 위생"이 아니라 "러너가 죽었다"로 오는 것**이 이 고장의 급소다.

고치는 자리를 호출부 쉰여덟 군데가 아니라 **`tempfile` 의 기본 디렉터리 하나**로
잡은 이유:

- **러너가 둘이다.** CI 본 잡(`core`)은 `verify.py`(stdlib unittest)로 돌고
  `core-pkns` 잡만 pytest 로 돈다. pytest 픽스처(`tmp_path`)로 고치면 정작 본 잡이
  그대로 샌다 — 게다가 이 리포의 테스트는 대부분 `unittest.TestCase` 라서 픽스처
  주입 자체가 안 된다.
- **모듈 최상위에서 부르는 곳이 있다**(`test_fragment_edge_tenancy.py`). 픽스처는
  수집(=테스트 모듈 import)이 끝난 뒤에 도니 그건 애초에 못 잡는다.
- 임시 경로를 **제품 코드가** 만드는 경우(`rag_eval/tooluse.py`)도 같이 덮인다.
- 앞으로 새로 들어오는 mkdtemp 도 손대지 않고 자동으로 갇힌다.

`TMPDIR`/`TEMP`/`TMP` 까지 같이 옮기는 것은 테스트가 띄우는 **자식 프로세스**도 우리
안에 두기 위해서다(사이드카·엔트리포인트 테스트가 파이썬을 새로 띄운다).
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

#: 우리 경로. `install()` 이 멱등이라는 사실을 담는 자리이기도 하다.
_ROOT: str | None = None

_ENV_KEYS = ("TMPDIR", "TEMP", "TMP")


def install() -> str:
    """임시 파일의 기본 자리를 우리 안으로 옮기고, 프로세스 종료 시 우리를 지운다.

    러너 둘에서 각각 불리므로 **멱등**이다. 반환값은 우리 경로.
    """
    global _ROOT
    if _ROOT is not None:
        return _ROOT
    # 우리 자신은 시스템 임시 디렉터리에 만든다(이 한 건만 밖에 남는다).
    _ROOT = tempfile.mkdtemp(prefix="vinc-tests-")
    tempfile.tempdir = _ROOT          # 이후 mkdtemp/mkstemp/NamedTemporaryFile 전부
    for key in _ENV_KEYS:
        os.environ[key] = _ROOT       # 자식 프로세스까지
    atexit.register(_remove)
    return _ROOT


def _remove() -> None:
    """우리를 지운다. 실패해도 종료를 막지 않는다.

    `ignore_errors` 인 이유는 Windows 파일 잠금(ladybug DB)과 살아남은 자식
    프로세스 때문이다. 그 경우 남는 것은 우리 **하나**뿐이라 무한히 쌓이지 않는다.
    """
    if _ROOT:
        shutil.rmtree(_ROOT, ignore_errors=True)
