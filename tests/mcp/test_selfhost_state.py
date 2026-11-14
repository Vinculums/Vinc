# -*- coding: utf-8 -*-
"""셀프호스트 허브의 상태 디렉터리(`vinc_mcp/selfhost_state.state_dir`).

잠그는 것:
- 우선순위: `VINC_SELFHOST_STATE` > `VINC_MCP_STATE_SNAPSHOT` 의 부모 > OAuth 작업본(`VINC_MCP_STATE_DIR`).
  디렉터리가 없으면 만든다.
- 로그인·좌석·사람 모듈은 같은 함수를 쓴다. 좌석과 사람이 로그인 모듈(`selfhost_web`)을 거꾸로 가져오지
  않는다.
- 세 상태 파일(좌석·사람·세션)은 모두 0600 이고 `state_dir` 아래에 만들어진다.
"""
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vinc_mcp import selfhost_seats, selfhost_state, selfhost_users, selfhost_web  # noqa: E402

_ENVS = ("VINC_SELFHOST_STATE", "VINC_MCP_STATE_SNAPSHOT", "VINC_MCP_STATE_DIR")


@pytest.fixture
def clean_env(monkeypatch):
    for name in _ENVS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_명시한_경로가_먼저고_없으면_만든다(clean_env, tmp_path):
    want = tmp_path / "explicit" / "deeper"
    clean_env.setenv("VINC_SELFHOST_STATE", str(want))
    clean_env.setenv("VINC_MCP_STATE_SNAPSHOT", str(tmp_path / "snap" / "oauth.snapshot"))
    assert selfhost_state.state_dir() == str(want)
    assert want.is_dir()


def test_스냅샷이_있으면_그_부모다(clean_env, tmp_path):
    (tmp_path / "volume").mkdir()
    clean_env.setenv("VINC_MCP_STATE_SNAPSHOT", str(tmp_path / "volume" / "oauth.snapshot"))
    clean_env.setenv("VINC_MCP_STATE_DIR", str(tmp_path / "work"))
    assert selfhost_state.state_dir() == str(tmp_path / "volume")


def test_둘_다_없으면_OAuth_작업본_경로다(clean_env, tmp_path):
    clean_env.setenv("VINC_MCP_STATE_DIR", str(tmp_path / "work"))
    assert selfhost_state.state_dir() == str(tmp_path / "work")
    assert (tmp_path / "work").is_dir()


def test_세_모듈이_같은_함수를_쓴다():
    assert selfhost_web.state_dir is selfhost_state.state_dir
    assert selfhost_seats.state_dir is selfhost_state.state_dir
    assert selfhost_users.state_dir is selfhost_state.state_dir


@pytest.mark.parametrize("name", ["selfhost_seats.py", "selfhost_users.py", "selfhost_state.py"])
def test_좌석_사람_상태_모듈은_로그인_모듈을_최상위에서_가져오지_않는다(name):
    text = (ROOT / "vinc_mcp" / name).read_text(encoding="utf-8")
    top = [ln for ln in text.splitlines() if re.match(r"(from|import)\s", ln)]
    assert [ln for ln in top if "selfhost_web" in ln] == []
    assert "def state_dir" not in text or name == "selfhost_state.py"


@pytest.mark.skipif(os.name == "nt", reason="POSIX 파일 모드")
def test_세_상태_파일은_state_dir_아래_0600_이다(clean_env, tmp_path):
    clean_env.setenv("VINC_SELFHOST_STATE", str(tmp_path))
    selfhost_seats._write(selfhost_seats.SEATS_FILE, {"token": "t"})
    selfhost_users._save([{"username": "a", "hash": "h"}])
    store = selfhost_web.SessionStore(os.path.join(selfhost_state.state_dir(), selfhost_web.SESSIONS_FILE))
    store.create()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == sorted([selfhost_seats.SEATS_FILE, selfhost_users.USERS_FILE, selfhost_web.SESSIONS_FILE])
    for p in tmp_path.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p.name
