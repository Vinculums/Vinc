"""실행 프로토콜이 여는 실행의 episode id 가 클라이언트의 `run_episode_id` 와 같은가.

허브(vinc_mcp)와 클라이언트는 서로 import 하지 않는다. 같은 run_id 가 어느 쪽에서 열려도 같은
노드여야 호스트가 실행 기록을 두 번 쓰지 않는다.
"""
import sys
from pathlib import Path

import pytest

from vinc_client.run import run_episode_id

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from vinc_mcp import run_protocol  # noqa: E402


@pytest.mark.parametrize("run_id", ["r-1", "Run.With:Mixed-Case_9", "a" * 128, "x", "A", "a b"[:1]])
def test_the_hub_and_the_client_name_a_run_the_same_way(run_id):
    assert run_protocol.episode_id(run_id) == run_episode_id(run_id)
