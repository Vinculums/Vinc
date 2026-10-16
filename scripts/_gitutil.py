"""운영 스크립트의 git 호출 한 벌.

언제나 `-c core.quotepath=off` 를 붙이고(한글 경로가 `"\355..."` 로 따옴표째 오지
않게) utf-8 로 읽는다. 윈도우에서 `text=True` 만 주면 cp949 로 읽어 한글 커밋 제목·
경로가 깨지거나 예외가 난다. 실패는 기본으로 `CalledProcessError` 이고, 실패를 값으로
바꾸는 것(빈 문자열·None·자기 예외)은 부르는 쪽의 몫이다.
"""
from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path


def _run(args: Sequence[str], cwd: str | Path | None, timeout: float | None):
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def git(args: Sequence[str], *, cwd: str | Path | None = None, check: bool = True,
        timeout: float | None = 300) -> str:
    """stdout 을 그대로 돌려준다(strip 하지 않는다). `check` 면 0 이 아닌 종료에 예외."""
    r = _run(args, cwd, timeout)
    if check and r.returncode:
        raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    return r.stdout


def git_ok(args: Sequence[str], *, cwd: str | Path | None = None,
           timeout: float | None = 300) -> bool:
    """종료 코드만 묻는 호출(`merge-base --is-ancestor`, `cat-file -e`)."""
    return _run(args, cwd, timeout).returncode == 0
