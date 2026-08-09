# -*- coding: utf-8 -*-
"""그래프 스토어의 **작업본 / 영속 스냅샷 분리** (2026-08-09).

왜 이 모양인가
--------------
허브의 그래프 DB 는 Azure Files(SMB) 마운트 위에서 직접 read-write 로 살았다. 그
계층에서 하드다운이 세 번 났다 — FTS 인덱스 손상 2회(2026-08-04·08-07), 재생 불가
WAL 1회(08-08, 5시간 반의 쓰기 유실). 매번 다른 증상이었지만 공통점이 하나다:
**잠금과 동기화를 네트워크 파일 공유에 맡겼다.**

이건 이 리포가 이미 배운 교훈이다. 같은 볼륨에 MCP 의 OAuth SQLite 를 올렸을 때
첫 스키마 쓰기가 ``database is locked`` 로 죽어 프로덕션 리비전이 Failed 로 떨어졌고
(2026-08-03), 거기서 내린 결론이 ``vinc_mcp/store.py`` 에 이렇게 적혀 있다:

    "읽고 쓰는 곳과 남기는 곳을 분리한다. SQLite 는 로컬 디스크에서 평소대로 돌고,
     영속 볼륨에는 커밋된 DB 파일을 통째로 복사해 둔다. 복사는 SMB 가 확실히
     지원하는 연산이다."

그 처방을 OAuth SQLite 에만 적용하고 **정작 본체인 그래프 DB 에는 적용하지 않았다.**
이 모듈이 그 빠진 절반이다.

외부 근거도 같은 방향이다 — SQLite 공식 문서는 네트워크 파일시스템의 잠금 구현에
버그가 있어 동시 접근이 **DB 손상으로 이어질 수 있다**고 하고, DuckDB 성능 문서는
NFS·SMB·Samba 위 read-write 를 권하지 않으며 증상으로 "spurious errors" 를 든다.
둘 다 네트워크 **블록** 스토리지(관리 디스크 등)는 괜찮고 네트워크 **파일 공유**가
문제라고 구분한다.

계약
----
- ``VINC_STORE_SNAPSHOT`` 가 **비어 있으면 이 모듈은 아무것도 하지 않는다.** 기존
  동작 그대로다(데스크탑·로컬 개발은 영향 없음). 켜는 것은 배포의 의도적 행위다.
- 켜면: 스토어는 ``data_dir``(로컬 디스크)에서 열리고, 영속 볼륨에는 **복사만** 한다.
- 기동 시 작업본이 없으면 스냅샷에서 복원한다 — 리비전이 바뀌어도 데이터가 산다.
- **저장 실패는 fail-soft, 복원 실패는 fail-closed.** 이 갈래가 이 모듈의 급소다.

  이 자리에 오래 "전 구간 fail-soft" 라고만 적혀 있었고, 2026-08-09 에 그것이
  **데이터 전손 경로**임이 실증됐다. 복원이 `PermissionError` 로 실패했는데 상태
  문자열만 남기고 서비스가 떴다 → 스토어가 **빈 DB 로 새로 생성**됐다 → 다음
  주기 스냅샷이 그 빈 DB 를 영속본 위에 덮었다(60.72MB → 0.00MB).

  두 실패의 성질이 다르다:

  * **저장 실패** — 작업본에는 옳은 데이터가 있다. 계속 도는 것이 가용성을 지키고
    잃는 것은 "이번 회차 스냅샷" 뿐이다. fail-soft 가 맞다.
  * **복원 실패** — 서비스에 데이터가 **없다.** 계속 도는 것은 가용성을 지키지
    않는다. 빈 그래프를 서빙하면서 영속본까지 파괴한다. 여기서 멈추는 것은
    "저장소 문제로 서비스를 내리는" 것이 아니라 **없는 데이터를 있는 척하지 않는
    것**이다. 멈추면 사람이 보고 고치지만, 진행하면 조용히 원본이 사라진다.

  판정은 :func:`restore_state_is_fatal` 하나에 있고 기동 경로가 그것을 본다
  (``api/cli.py`` serve).

잃을 수 있는 것
--------------
마지막 스냅샷 이후의 쓰기다. 그래서 주기가 곧 **손실 창**이고, 기본 300초다.
OAuth 쪽은 DB 가 수 KB 라 쓰기마다 스냅샷하지만 그래프는 수십 MB 라 그럴 수 없다.
이 창을 0 으로 만들려면 연속 복제(litestream 계열)가 필요하고, 그건 별도 과제다.
"""
from __future__ import annotations

import os
import shutil
import time

#: 스냅샷 주기 기본값(초) = 최대 손실 창. env `VINC_STORE_SNAPSHOT_EVERY`.
DEFAULT_SNAPSHOT_EVERY = 300.0


def resolve_snapshot_path() -> str | None:
    """영속 스냅샷 경로: env ``VINC_STORE_SNAPSHOT``. 미설정이면 None = 기능 꺼짐.

    **여기가 영속 볼륨(SMB)이다.** 이 경로에는 DB 를 열지 않는다 — 파일 복사만
    한다. 그래서 네트워크 파일시스템의 잠금 의미론과 무관하다."""
    p = (os.environ.get("VINC_STORE_SNAPSHOT") or "").strip()
    return p or None


def resolve_snapshot_every() -> float:
    raw = (os.environ.get("VINC_STORE_SNAPSHOT_EVERY") or "").strip()
    try:
        v = float(raw) if raw else DEFAULT_SNAPSHOT_EVERY
    except ValueError:
        return DEFAULT_SNAPSHOT_EVERY
    return v if v > 0 else 0.0


def _parts(path: str) -> list[tuple[str, bool]]:
    """DB 를 이루는 경로들 — (경로, 필수인가). 단일 파일 + ``.wal`` 사이드카이거나
    디렉터리다. 둘 다 지원한다(레이아웃은 드라이버 버전에 따라 다르다)."""
    return [(path, True), (path + ".wal", False)]


def restore_if_missing(live_path: str, snapshot_path: str | None = None) -> str:
    """작업본이 없으면 스냅샷에서 복원한다. 상태 문자열을 돌려준다.

    ``off``(기능 꺼짐) · ``present``(작업본 있음, 복원 안 함) · ``restored`` ·
    ``empty``(스냅샷 없음 — 첫 기동) · ``error: …``

    ⚠ 작업본이 **이미 있으면 절대 덮어쓰지 않는다.** 컨테이너가 재시작만 하고
    로컬 디스크가 살아 있는 흔한 경우에, 낡은 스냅샷으로 최신 작업본을 밀어버리는
    것이 이 함수가 낼 수 있는 최악의 사고다."""
    snapshot_path = snapshot_path if snapshot_path is not None else resolve_snapshot_path()
    if not snapshot_path:
        return "off"
    try:
        if os.path.exists(live_path):
            return "present"
        if not os.path.exists(snapshot_path):
            return "empty"
        os.makedirs(os.path.dirname(os.path.abspath(live_path)) or ".", exist_ok=True)
        if os.path.isdir(snapshot_path):
            shutil.copytree(snapshot_path, live_path, dirs_exist_ok=True)
        else:
            for src, required in _parts(snapshot_path):
                if not os.path.exists(src):
                    if required:
                        return "empty"
                    continue
                dst = live_path + src[len(snapshot_path):]
                shutil.copy2(src, dst)
        return "restored"
    except Exception as exc:                                   # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}"


#: 스냅샷이 아직 없는 상태(첫 기동)를 **명시적으로 허용**하는 env. 켜지 않으면
#: `empty` 는 치명으로 본다.
#:
#: 왜 옵트인인가. `empty` 는 "스냅샷 파일이 안 보인다" 이지 "이 배포에 데이터가 없다"
#: 가 아니다. 마운트가 늦게 붙거나 읽기 권한이 없어도 같은 값이 나오고, 그때 그냥
#: 진행하면 **빈 그래프로 서빙하다가 영속본을 덮는다** — 2026-08-09 에 `error` 갈래로
#: 실제로 당한 그 사고와 결과가 같다. 진짜 첫 기동은 인생에 한 번뿐이므로 그때만
#: 사람이 켜 주는 편이 싸다.
BOOTSTRAP_ENV = "VINC_STORE_ALLOW_EMPTY"


def restore_state_is_fatal(state: str) -> str | None:
    """복원 상태가 **기동을 막아야 하는가**. 막아야 하면 사람이 읽을 사유, 아니면 None.

    진행해도 되는 것은 셋뿐이다:
      ``off``      기능이 꺼져 있다 — 지킬 것이 없다
      ``present``  작업본이 이미 있다 — 흔한 재시작
      ``restored`` 복원했다

    ``empty`` 는 :data:`BOOTSTRAP_ENV` 가 켜져 있을 때만 통과한다(위 주석 참조).
    ``error: …`` 는 언제나 막는다.
    """
    if state in ("off", "present", "restored"):
        return None
    if state == "empty":
        if (os.environ.get(BOOTSTRAP_ENV) or "").strip().lower() in ("1", "true", "on"):
            return None
        return (f"영속 스냅샷이 보이지 않는다({resolve_snapshot_path()}). 진짜 첫 기동이면 "
                f"{BOOTSTRAP_ENV}=1 로 한 번만 켤 것. 그게 아니면 마운트·권한 문제이며, "
                f"이대로 진행하면 빈 그래프로 서빙하다가 영속본을 덮는다")
    return (f"영속 스냅샷 복원 실패 — {state}. 이대로 진행하면 빈 그래프로 서빙하면서 "
            f"다음 스냅샷이 영속본을 덮는다(2026-08-09 실증). 스토어를 열지 않는다")


def save_snapshot(store, snapshot_path: str | None = None) -> str:
    """작업본을 영속 볼륨으로 복사한다. 상태 문자열을 돌려준다.

    ``store.snapshot(dest_dir)`` 에 위임한다 — 그쪽이 CHECKPOINT 로 WAL 을 본 파일에
    반영하고, 락에 막히면 드라이버를 닫았다가 **같은 경로로 다시 열어** 준다.
    그래서 이 함수는 **쓰기 스레드에서 불려야 한다**(잡 워커). 다른 스레드에서
    부르면 재오픈 중인 커넥션을 다른 쪽이 쓰게 된다.

    ⚠ 영속 볼륨에 **직접 쓰는 순간이 여기뿐**이고, 그것도 복사다."""
    snapshot_path = snapshot_path if snapshot_path is not None else resolve_snapshot_path()
    if not snapshot_path or store is None:
        return "off"
    snap = getattr(store, "snapshot", None)
    if not callable(snap):
        return "off"     # 백엔드가 스냅샷을 모른다(테스트 스텁 등) — 조용히 지나간다
    try:
        dest_dir = os.path.dirname(os.path.abspath(snapshot_path)) or "."
        os.makedirs(dest_dir, exist_ok=True)
        produced = snap(dest_dir)
        # snapshot() 은 원본 basename 으로 떨군다. 스냅샷 경로의 이름이 다르면 옮긴다
        # (작업본 이름과 영속 이름을 따로 두고 싶을 수 있다).
        if produced and os.path.abspath(produced) != os.path.abspath(snapshot_path):
            for src, required in _parts(produced):
                dst = snapshot_path + src[len(produced):]
                if os.path.exists(src):
                    os.replace(src, dst)
                elif required:
                    return "error: snapshot produced nothing"
        return "ok"
    except Exception as exc:                                   # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}"


class SnapshotPolicy:
    """"언제 스냅샷할까" 만 든다 — 시간 기준이다.

    왜 시간인가. 잡 개수는 **잃는 양과 무관하다**(잡 하나가 거대할 수도, 잘 수도
    있다). 여기서 지키려는 것은 "최대 몇 분어치를 잃을 수 있는가"이므로 단위는
    시간이어야 한다. 2026-08-08 사고 때 잡 카운터로 체크포인트를 걸었다가 같은
    이유로 걷어냈다(``api/jobs.py`` 주석 참조).

    그리고 **쓰기가 있었을 때만** 뜬다 — 읽기만 오가는 밤중에 63MB 를 SMB 로 계속
    밀어 봐야 비용만 든다."""

    def __init__(self, *, every: float | None = None, clock=time.monotonic):
        self.every = resolve_snapshot_every() if every is None else float(every)
        self._clock = clock
        self._last = clock()
        self._dirty = False
        self.last_state = "pending" if resolve_snapshot_path() else "off"

    def mark_dirty(self) -> None:
        self._dirty = True

    def due(self) -> bool:
        if not self.every or not self._dirty:
            return False
        return (self._clock() - self._last) >= self.every

    def done(self, state: str) -> None:
        self._last = self._clock()
        self._dirty = False
        self.last_state = state
