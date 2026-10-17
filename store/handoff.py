# -*- coding: utf-8 -*-
"""허브 writer 인계 — 새 리비전이 구 리비전의 마지막 스냅샷을 **기다렸다가** 복원한다.

배포는 구 리비전을 먼저 멈추지 않는다. 새 리비전은 포트를 열고 요청을 붙잡은 채로
이 모듈의 `decide()` 를 기다린다. 구 리비전은 SIGTERM 에서 마지막 스냅샷을 올린 뒤
`mark_released()` 로 "넘김" 표식을 남긴다. 새 리비전은 그 표식을 본 뒤에야 복원하므로,
복원은 언제나 마지막 스냅샷 **뒤에** 온다(시각이 아니라 인과로).

스냅샷 옆의 파일 둘:

- ``<snap>.writer``   — 쓰기 자격을 가진 리비전(`store.persistence.claim_writer`)
- ``<snap>.released`` — 마지막 스냅샷을 **성공적으로** 올리고 자격을 놓은 리비전

리비전 이름(`CONTAINER_APP_REVISION`)이나 스냅샷 경로가 없으면 인계는 꺼진다 —
데스크탑·테스트·로컬 docker 는 기다릴 상대가 없다.

설계: [[vinc.plan.hub-writer-handoff-2026-10-17]]

    python -m store.handoff wait     # exit 0 = 진행, 3 = 낡은 리비전(복원·자격 금지)
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable

from .persistence import REVISION_ENV, WRITER_SUFFIX, _revision_no, resolve_snapshot_path

RELEASED_SUFFIX = ".released"
#: 이전 writer 의 넘김 표식을 기다리는 상한(초). 넘으면 이전 writer 가 넘기지 못하고 죽은
#: 것으로 보고 진행한다 — 이 경우 잃는 양은 급사와 같다(마지막 주기 스냅샷 이후의 쓰기).
WAIT_ENV = "VINC_HANDOFF_WAIT_S"
DEFAULT_WAIT_S = 120.0

EXIT_PROCEED = 0
EXIT_STALE = 3


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _write(path: str, value: str) -> None:
    tmp = f"{path}.{value}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    os.replace(tmp, path)


def _newer(a: str, b: str) -> bool:
    """a 가 b 보다 번호가 큰 리비전인가. 번호를 못 읽으면 False(울타리 없이 예전처럼)."""
    na, nb = _revision_no(a), _revision_no(b)
    return na is not None and nb is not None and na > nb


def mark_released(snapshot_path: str | None = None) -> str:
    """마지막 스냅샷이 ``ok`` 로 끝난 **뒤에만** 부른다. ``off`` · ``released`` ·
    ``skipped: <이유>`` · ``error: …``

    자격을 가진 리비전만 넘길 수 있다: ``.writer`` 가 내가 아니면(이미 새 리비전이
    가져갔거나 표식이 없으면) 쓰지 않는다. 넘김 표식이 거짓이면 새 리비전이 마지막
    스냅샷보다 먼저 복원한다 — 이 표식이 지키는 유일한 순서가 그것이다."""
    snapshot_path = snapshot_path if snapshot_path is not None else resolve_snapshot_path()
    mine = (os.environ.get(REVISION_ENV) or "").strip()
    if not snapshot_path or not mine:
        return "off"
    try:
        holder = _read(snapshot_path + WRITER_SUFFIX)
        if holder != mine:
            return f"skipped: writer is {holder or '(none)'}"
        _write(snapshot_path + RELEASED_SUFFIX, mine)
        return "released"
    except Exception as exc:                                   # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}"


def decide(snapshot_path: str | None = None, *, wait_s: float | None = None,
           poll_s: float = 1.0, clock: Callable[[], float] = time.monotonic,
           sleep: Callable[[float], None] = time.sleep,
           log: Callable[[str], None] = lambda m: print(m, file=sys.stderr, flush=True)) -> str:
    """기동 순서의 첫 판정. ``proceed: <이유>`` 또는 ``stale: <자격 보유자>``.

    ``stale`` 이면 이 리비전은 복원도 자격 주장도 하지 않는다 — 나보다 새 리비전이
    이미 writer 다. 나머지는 전부 진행이고, 기다림은 이전 writer 가 넘김 표식을 남길
    시간을 주는 것뿐이다."""
    snapshot_path = snapshot_path if snapshot_path is not None else resolve_snapshot_path()
    mine = (os.environ.get(REVISION_ENV) or "").strip()
    if not snapshot_path or not mine:
        return "proceed: off"
    if wait_s is None:
        try:
            wait_s = float(os.environ.get(WAIT_ENV) or DEFAULT_WAIT_S)
        except ValueError:
            wait_s = DEFAULT_WAIT_S
    writer_path, released_path = snapshot_path + WRITER_SUFFIX, snapshot_path + RELEASED_SUFFIX
    writer = _read(writer_path)
    if not writer:
        return "proceed: no writer"
    if _newer(writer, mine):
        return f"stale: {writer}"
    if writer == mine:
        # 같은 리비전의 재시작은 기다리지 않는다(정상 종료로 넘김을 남겼어도). 새 리비전이 이미
        # 자격을 가져갔다면 위에서 stale 로 갈렸다. 남은 내 넘김 표식은 지운다 — 그래야 다음
        # 새 리비전이 이번 기동 뒤의 마지막 스냅샷을 다시 기다린다.
        if _read(released_path) == mine:
            try:
                os.remove(released_path)
            except OSError:
                pass
        return "proceed: own restart"

    # 이전 writer 가 넘김 표식을 남길 때까지 기다린다. 그사이 나보다 새 리비전이 자격을
    # 가져가면 내가 낡은 쪽이다.
    log(f"vinc: handoff — waiting for {writer} to release (up to {wait_s:.0f}s)")
    deadline = clock() + wait_s
    while True:
        current = _read(writer_path)
        if current and _newer(current, mine):
            return f"stale: {current}"
        if _read(released_path) == writer:
            return f"proceed: {writer} released"
        if clock() >= deadline:
            break
        sleep(poll_s)
    log(f"vinc: handoff — ⚠ {writer} did not release in {wait_s:.0f}s; restoring the last snapshot "
        f"(writes after its last periodic snapshot may be missing)")
    return f"proceed: {writer} did not release"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["wait"]:
        print("usage: python -m store.handoff wait", file=sys.stderr)
        return 2
    verdict = decide()
    print(f"vinc: handoff — {verdict}", file=sys.stderr, flush=True)
    return EXIT_STALE if verdict.startswith("stale") else EXIT_PROCEED


if __name__ == "__main__":
    raise SystemExit(main())
