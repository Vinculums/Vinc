# -*- coding: utf-8 -*-
"""그래프 스토어의 **작업본 / 영속 스냅샷 분리**.

스토어는 로컬 디스크에서 열고, 네트워크 파일 공유(Azure Files)에는 **복사만** 한다.
공유 위에서 직접 read-write 하면 잠금과 동기화를 그 계층에 맡기게 되고, 이 프로젝트는
그 자리에서 하드다운을 네 번 겪었다: [[record:network-file-shares-break-embedded-databases-block-storage-does-not]].
운영 절차는 [[vinc.ops.deployment]].

계약
----
- ``VINC_STORE_SNAPSHOT`` 가 **비어 있으면 이 모듈은 아무것도 하지 않는다**(데스크탑·
  로컬 개발 무영향). 켜는 것은 배포의 의도적 행위다.
- 켜면 스토어는 ``data_dir``(로컬)에서 열리고 영속 볼륨에는 복사만 한다. 기동 시
  작업본이 없으면 스냅샷에서 복원한다 — 리비전이 바뀌어도 데이터가 산다.
- **저장 실패는 fail-soft, 복원 실패는 fail-closed.** 이 갈래가 이 모듈의 급소다:
  저장이 실패하면 작업본에 옳은 데이터가 있으니 잃는 것은 이번 회차뿐이지만, 복원이
  실패하면 서비스에 데이터가 **없고** 계속 도는 것은 빈 그래프를 서빙하며 영속본까지
  파괴하는 일이다. 판정은 :func:`restore_state_is_fatal` 하나에 있고 기동 경로
  (``api/cli.py`` serve)가 그것을 본다. 실증: [[record:a-failed-restore-must-stop-the-process]].

잃을 수 있는 것
--------------
마지막 스냅샷 이후의 쓰기다. 주기가 곧 **손실 창**이고 기본 300초 — OAuth 쪽은 DB 가
수 KB 라 쓰기마다 스냅샷하지만 그래프는 수십 MB 라 그럴 수 없다.

``VINC_DURABLE_ACK`` 를 켜면 이 창이 ACK 된 쓰기에 대해서는 0 이 된다(:class:`DurableAck`):
쓰기 잡은 영속 세대(``<스냅샷>.generations``)를 게시한 뒤에야 DONE·200 을 보인다.
기본은 꺼짐이고, 꺼져 있으면 이 모듈의 동작은 예전과 같다.
근거: [[vinc.plan.vin82-durable-ack-2026-09-29]]
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time

from . import durable_generation as dg
from .envflag import truthy

#: 스냅샷 주기 기본값(초) = 최대 손실 창. env `VINC_STORE_SNAPSHOT_EVERY`.
DEFAULT_SNAPSHOT_EVERY = 300.0

#: 스냅샷 스테이징 디렉터리 접두. **영속 볼륨 안**에 만든다 — 다른 파일시스템이면
#: `os.replace` 가 rename 이 아니라 복사로 강등돼 스테이징의 의미가 사라진다.
#: 점으로 시작해 사람이 목록에서 데이터 파일과 구분할 수 있게 한다.
STAGE_PREFIX = ".stage-"

#: 직전 세대를 물려 두는 접미. **이것이 있어야 스냅샷 교체가 안전하다.**
#:
#: 스테이징만으로는 부족했다(2026-08-12 실증). 바이트 복사는 스테이징에서 끝나지만,
#: 마지막 `os.replace(stage, dest)` 는 SMB 에서 "목적지를 지우고 rename" 으로 구현될
#: 수 있고 그 사이에 프로세스가 죽으면 **영속본이 통째로 사라진다**. 그날 허브가 딱
#: 그렇게 됐다: 15:47 에 멀쩡히 복원되던 `/data/db.recovered5` 가 리비전 종료 직후
#: 16:27 에 흔적도 없었고, 사흘치를 트랜스크립트에서 되살려야 했다.
#:
#: 그래서 교체를 **두 단계**로 가른다. 먼저 옛 세대를 rename 으로 `.prev` 에 물리고,
#: 그다음 새 세대를 자리에 옮긴다. 어느 지점에서 죽어도 둘 중 하나는 온전하다.
PREV_SUFFIX = ".prev"

#: 쓰기 자격 표식의 접미. 스냅샷 옆에 **자격을 가진 리비전 이름 한 줄**을 둔다.
#:
#: 배포마다 구 리비전이 한 번 더 뜬다. ACA 는 단일 리비전 모드의 롤링 전환에서
#: 방금 내린 구 리비전에 트래픽을 되돌려 다시 띄우고, 그것은 /data 에서 복원해 1분쯤
#: 살다가 종료 훅의 최종 스냅샷을 **새 리비전 뒤에** 영속본 위에 쓴다. 표식은 그 쓰기를
#: 막는다: 나보다 번호가 큰 리비전이 자격을 가져갔으면 나는 영속본을 건드리지 않는다.
#: 근거: [[record:aca-reboots-the-stopped-hub-revision-during-every-deploy-and-it-writes-a-late-final-snapshot-2026-09-25]]
WRITER_SUFFIX = ".writer"

#: ACA 가 컨테이너에 넣어 주는 리비전 이름(`vinc-hub--0000235`). **없으면 울타리는
#: 꺼진다** — 데스크탑·테스트·로컬 docker 는 리비전이 하나라 막을 상대가 없다.
REVISION_ENV = "CONTAINER_APP_REVISION"
#: 같은 리비전의 레플리카를 가르는 이름. 플랫폼이 넣는다. 없으면 리비전만으로 예전처럼 돈다.
REPLICA_ENV = "CONTAINER_APP_REPLICA_NAME"
#: writer 이름 = ``<리비전>@<레플리카>``. 레플리카를 모르면 리비전만 적는다(레플리카 이전의 표식과 같다).
REPLICA_SEP = "@"

_REVISION_NO = re.compile(r"--(\d+)$")

#: ACK 전에 영속 세대를 게시하는 모드의 스위치(VIN-82). **기본 꺼짐.** 스냅샷 경로가
#: 없으면 켜도 꺼진 것이다(복사해 둘 두 번째 자리가 없다).
DURABLE_ENV = "VINC_DURABLE_ACK"

#: 세대 루트 = 스냅샷 경로 + 이 접미. 스냅샷과 같은 영속 볼륨이다.
GENERATIONS_SUFFIX = ".generations"

#: 이 스냅샷이 뜰 때 이미 커밋돼 있던 최신 세대 번호 한 줄. 기동 복원은 이 번호보다 큰
#: 세대만 스냅샷보다 새것으로 본다. 세대 루트가 있을 때만 쓴다(플래그와 무관) — 꺼 둔
#: 동안의 쓰기가 스냅샷에만 있는데 다시 켜고 기동하면 낡은 세대를 고르게 되기 때문이다.
SEEN_SUFFIX = ".generation-seen"

#: 남기는 커밋 세대 수: 최신 + 직전 하나(`.prev` 가 한 세대만 드는 것과 같은 관례).
DURABLE_KEEP = 2


def durable_ack_enabled(snapshot_path: str | None = None) -> bool:
    snapshot_path = snapshot_path if snapshot_path is not None else resolve_snapshot_path()
    return bool(snapshot_path) and truthy(os.environ.get(DURABLE_ENV))


def generations_root(snapshot_path: str) -> str:
    return snapshot_path + GENERATIONS_SUFFIX


def _snapshot_seen(snapshot_path: str) -> int:
    """스냅샷이 담은 세대 번호. 표식이 없으면 0(세대보다 먼저 뜬 스냅샷), 있는데 못
    읽으면 예외다 — 여기서 0 으로 넘어지면 낡은 세대가 새 스냅샷을 이긴다."""
    try:
        with open(snapshot_path + SEEN_SUFFIX, encoding="utf-8") as f:
            return int(f.read().strip())
    except FileNotFoundError:
        return 0


def _mark_seen(snapshot_path: str) -> None:
    root = generations_root(snapshot_path)
    if not os.path.isdir(root):
        return                       # 세대 모드를 켠 적이 없다 — 예전과 똑같이 아무것도 안 쓴다
    marker = snapshot_path + SEEN_SUFFIX
    tmp = marker + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"{dg.latest_sequence(root)}\n")
    os.replace(tmp, marker)


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
        # 자리에 온전한 세대가 없으면 **직전 세대**(.prev)를 본다. 교체 도중 죽으면
        # 자리는 비고 `.prev` 만 남는 순간이 있는데, 여기 폴백이 없으면 그 순간이
        # 그대로 `empty` 로 읽혀 기동이 막힌다(그리고 사람은 데이터가 사라진 줄 안다).
        source, state = snapshot_path, "restored"
        if not os.path.exists(snapshot_path):
            prev = snapshot_path + PREV_SUFFIX
            if not os.path.exists(prev):
                return "empty"
            source, state = prev, "restored-previous"
        os.makedirs(os.path.dirname(os.path.abspath(live_path)) or ".", exist_ok=True)
        if os.path.isdir(source):
            shutil.copytree(source, live_path, dirs_exist_ok=True)
        else:
            for src, required in _parts(source):
                if not os.path.exists(src):
                    if required:
                        return "empty"
                    continue
                dst = live_path + src[len(source):]
                shutil.copy2(src, dst)
        return state
    except Exception as exc:                                   # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}"


def restore_newest(live_path: str, snapshot_path: str | None = None) -> str:
    """전역 또는 기존 전환 publisher가 켜진 기동은 게시한 세대를 복원한다.

    켜져 있으면 스냅샷이 담은 번호(:data:`SEEN_SUFFIX`)보다 최신 커밋 세대를 검증한다.
    그 세대가 깨졌으면 ``error`` 로 기동을 막는다. 이전 세대나 스냅샷으로 후퇴하면 이미
    ACK 한 쓰기를 잃는다. 스냅샷이 최신 커밋까지 담았거나 커밋이 없을 때만 스냅샷을 쓴다.
    근거: [[vinc.plan.vin82-durable-ack-2026-09-29]]
    작업본이 있으면 아무것도 안 한다(``present``) — 이 계약은 두 경로가 같다."""
    snapshot_path = snapshot_path if snapshot_path is not None else resolve_snapshot_path()
    # 전환 publisher는 전역 ACK가 꺼져도 영수증을 게시한다. 복원만 같은 범위를 따른다.
    # 일반 쓰기의 ACK 정책은 바꾸지 않는다. 양쪽이 꺼진 설치는 기존 스냅샷 복원이다.
    scoped = bool(snapshot_path) and os.environ.get("VINC_TOPIC_CONVERSION") == "1"
    if not (durable_ack_enabled(snapshot_path) or scoped):
        return restore_if_missing(live_path, snapshot_path)
    if os.path.exists(live_path):
        return "present"
    try:
        state = dg.restore(generations_root(snapshot_path), live_path,
                           newer_than=_snapshot_seen(snapshot_path))
    except Exception as exc:                                   # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}"
    if state.startswith("restored:"):
        return "restored-generation:" + state[len("restored:"):]
    return restore_if_missing(live_path, snapshot_path)


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

    진행해도 되는 것은 넷뿐이다:
      ``off``                기능이 꺼져 있다 — 지킬 것이 없다
      ``present``            작업본이 이미 있다 — 흔한 재시작
      ``restored``           복원했다
      ``restored-previous``  자리가 비어 **직전 세대**에서 복원했다 — 교체 도중 죽은
                             흔적이다. 데이터는 온전하지만 마지막 스냅샷 주기만큼
                             뒤처져 있으므로 로그에서 눈에 띄어야 한다

    ``empty`` 는 :data:`BOOTSTRAP_ENV` 가 켜져 있을 때만 통과한다(위 주석 참조).
    ``error: …`` 는 언제나 막는다. ``restored-generation:N`` 은 세대에서 복원했다는 뜻이고
    통과한다. ``; skipped …`` 꼬리(깨진 세대를 건너뜀)는 판정에 쓰지 않는다.
    """
    head = state.split("; ", 1)[0]
    if (head in ("off", "present", "restored", "restored-previous")
            or head.startswith("restored-generation:")):
        return None
    if head == "empty":
        if truthy(os.environ.get(BOOTSTRAP_ENV)):
            return None
        return (f"영속 스냅샷이 보이지 않는다({resolve_snapshot_path()}). 진짜 첫 기동이면 "
                f"{BOOTSTRAP_ENV}=1 로 한 번만 켤 것. 그게 아니면 마운트·권한 문제이며, "
                f"이대로 진행하면 빈 그래프로 서빙하다가 영속본을 덮는다")
    return (f"영속 스냅샷 복원 실패 — {state}. 이대로 진행하면 빈 그래프로 서빙하면서 "
            f"다음 스냅샷이 영속본을 덮는다(2026-08-09 실증). 스토어를 열지 않는다")


def _revision_no(name: str) -> int | None:
    m = _REVISION_NO.search(split_identity(name)[0])
    return int(m.group(1)) if m else None


def writer_identity() -> str:
    """이 프로세스가 표식에 적는 이름. ``<리비전>@<레플리카>`` · 레플리카를 모르면 리비전만 ·
    리비전이 없으면(데스크탑·테스트) ``""``.

    같은 리비전의 재시작은 새 레플리카를 먼저 띄우고 이전 것을 나중에 내린다. 리비전 이름만
    적으면 겹친 동안 둘 다 writer 로 보이고, 그 사이 앞 레플리카가 받은 쓰기를 잃는다."""
    rev = (os.environ.get(REVISION_ENV) or "").strip()
    if not rev:
        return ""
    rep = (os.environ.get(REPLICA_ENV) or "").strip()
    return f"{rev}{REPLICA_SEP}{rep}" if rep else rev


def split_identity(name: str) -> tuple[str, str]:
    """``(리비전, 레플리카)``. 레플리카가 없는 이름은 ``(리비전, "")``."""
    rev, _, rep = (name or "").strip().partition(REPLICA_SEP)
    return rev, rep


def _newer_revision(holder: str, mine: str) -> bool:
    held, own = _revision_no(holder), _revision_no(mine)
    return held is not None and own is not None and held > own


def displaces(holder: str, mine: str) -> bool:
    """표식의 주인 ``holder`` 가 나를 밀어낸 writer 인가 — 스냅샷과 ACK 의 울타리가 쓴다.

    나보다 새 리비전이면 그렇다. 같은 리비전의 **다른 레플리카**여도 그렇다: 표식은 마지막에
    자격을 가져간 쪽이 들고 있으므로, 표식에 없는 레플리카는 재시작으로 밀려난 앞 레플리카다.
    어느 한쪽이라도 레플리카를 모르면 같은 리비전끼리는 가르지 않는다. 번호를 못
    읽으면 False(울타리 없이 쓴다). 자격 주장(:func:`claim_writer`)은 이 함수를 쓰지 않는다 —
    넘김을 본 새 레플리카는 같은 리비전의 표식을 덮어써야 한다."""
    if _newer_revision(holder, mine):
        return True
    h_rev, h_rep = split_identity(holder)
    m_rev, m_rep = split_identity(mine)
    return bool(h_rev and h_rev == m_rev and h_rep and m_rep and h_rep != m_rep)


def newer_writer(snapshot_path: str | None = None) -> str | None:
    """나를 밀어낸 writer(:func:`displaces`)가 쓰기 자격을 가져갔으면 그 이름, 아니면 None.

    표식이 없거나 못 읽거나 이름에서 번호를 못 읽으면 None 이다 — 울타리 없이 예전처럼
    쓴다. 여기서 닫힌 쪽으로 넘어지면 SMB 딸꾹질 한 번에 살아 있는 writer 의 스냅샷이
    멈춘다. 이 울타리가 막는 것은 배포 중 1분짜리 경합 하나뿐이다."""
    snapshot_path = snapshot_path if snapshot_path is not None else resolve_snapshot_path()
    mine = writer_identity()
    if not snapshot_path or not mine:
        return None
    try:
        with open(snapshot_path + WRITER_SUFFIX, encoding="utf-8") as f:
            holder = f.read().strip()
    except OSError:
        return None
    return holder if displaces(holder, mine) else None


def claim_writer(snapshot_path: str | None = None) -> str:
    """쓰기 자격을 가져온다. ``off`` · ``claimed`` · ``fenced: <이름>`` · ``error: …``

    **HTTP 를 열기 직전에** 불러야 한다(``api/server.py`` serve). 기동 순서는 이렇다:
    MCP 가 붙잡기 모드로 8790 을 연다 → ACA 의 TCP 준비 판정 → 구 리비전에 SIGTERM →
    구 리비전이 최종 스냅샷을 올리고 넘김 표식을 쓴다 → 새 리비전이 그 표식을 보고
    복원한다(``store/handoff.py``) → 사이드카가 여기서 자격을 가져간다. 그래서 정상
    인계에서는 구 리비전의 최종 스냅샷이 이 표식보다 **먼저** 끝나 있다.

    이 울타리가 막는 것은 인계가 어긋난 경우다: 구 리비전이 상한 안에 넘기지 못해 새
    리비전이 그대로 진행했으면, 그 뒤에 오는 구 리비전의 스냅샷은 여기에 막혀 새 사본을
    덮지 못한다.

    표식이 이미 내 이름이면 다시 쓰지 않는다. 같은 리비전의 다른 레플리카 이름이면 덮어쓴다 —
    기동 순서(`store/handoff.py`)가 그 레플리카의 넘김을 본 뒤에야 여기 온다. 막는 것은 나보다 새 리비전뿐이다.

    ⚠ 자격을 가져간 뒤 몇 초 안에 죽기를 되풀이하는 리비전이면 ACA 는 구 리비전으로
    계속 서빙하는데 그 스냅샷은 여기에 막힌다. 그때는 표식 파일을 지우면 풀린다."""
    snapshot_path = snapshot_path if snapshot_path is not None else resolve_snapshot_path()
    mine = writer_identity()
    if not snapshot_path or not mine:
        return "off"
    marker = snapshot_path + WRITER_SUFFIX
    try:
        try:
            with open(marker, encoding="utf-8") as f:
                holder = f.read().strip()
        except OSError:
            holder = ""
        # 나보다 새 리비전만 막는다. 같은 리비전의 다른 레플리카 표식은 덮어쓴다 — 기동 순서
        # (store/handoff.py)가 그 레플리카의 넘김을 본 뒤에야 여기 온다.
        if holder and _newer_revision(holder, mine):
            return f"fenced: {holder}"
        if holder == mine:
            return "claimed"
        os.makedirs(os.path.dirname(os.path.abspath(marker)) or ".", exist_ok=True)
        tmp = f"{marker}.{mine}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(mine + "\n")
        os.replace(tmp, marker)
        return "claimed"
    except Exception as exc:                                   # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}"


def _rm(path: str) -> None:
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.exists(path):
        os.remove(path)


def _retire_generation(snapshot_path: str) -> None:
    """자리에 있는 세대를 :data:`PREV_SUFFIX` 로 물린다 — **rename 이지 복사가 아니다**.

    필수 파트를 옮기는 그 한 번의 rename 이 세대 교대 지점이다: 그 전에 죽으면 자리에
    옛 세대가 온전히 있고, 그 후에 죽으면 `.prev` 에 온전히 있다.

    ``.prev`` 에는 **체크포인트 본만** 남기고 WAL 사이드카는 버린다. 옛 WAL 을 함께
    물려 두면 나중에 그것이 다른 세대의 본 파일과 짝이 될 여지가 생기는데, 이 프로젝트가
    사고 때마다 실제로 쓴 복구 수법이 정확히 그 반대다 — "WAL 없는 본 파일로 열면
    마지막 체크포인트 상태로 정상 기동한다"(2026-08-04·08-07·08-09). 그러니 `.prev` 는
    처음부터 그 모양으로 둔다."""
    if not os.path.exists(snapshot_path):
        return                       # 첫 스냅샷 — 물릴 세대가 없다
    prev = snapshot_path + PREV_SUFFIX
    for stale, _required in _parts(prev):
        _rm(stale)                   # 전전 세대는 여기서 버린다(한 세대만 든다)
    os.replace(snapshot_path, prev)  # ← 이 한 줄이 세대 교대다
    for src, required in _parts(snapshot_path):
        if not required:
            _rm(src)                 # 짝 없는 WAL 이 남지 않게


def save_snapshot(store, snapshot_path: str | None = None) -> str:
    """작업본을 영속 볼륨으로 복사한다. 상태 문자열을 돌려준다.

    ``store.snapshot(dest_dir)`` 에 위임한다 — 그쪽이 CHECKPOINT 로 WAL 을 본 파일에
    반영하고, 락에 막히면 드라이버를 닫았다가 **같은 경로로 다시 열어** 준다.
    그래서 이 함수는 **쓰기 스레드에서 불려야 한다**(잡 워커). 다른 스레드에서
    부르면 재오픈 중인 커넥션을 다른 쪽이 쓰게 된다.

    ⚠ 영속 볼륨에 **직접 쓰는 순간이 여기뿐**이고, 그것도 복사다.

    **항상 스테이징을 거친다**(2026-08-09). 예전에는 작업본과 스냅샷의 basename 이
    같으면 — `VINC_STORE_LOCAL=on` 의 **기본 구성이 정확히 그 경우다** —
    `store.snapshot(dest_dir)` 이 영속본 위에 **직접** 수십 MB 를 덮어썼다. 그
    복사 도중 프로세스가 죽으면 유일한 영속본이 찢어진다. 지금은 임시 디렉터리에
    떨군 뒤 자리로 **옮긴다**: 옮기기 전에 죽으면 기존 영속본이 그대로 산다.

    (rename 이 CIFS 에서 POSIX 수준으로 원자적이라는 보장은 없다. 다만 서버측
    메타데이터 연산이라 **수 초짜리 바이트 복사보다 창이 비교할 수 없이 좁다.**
    "원자적"이라고 적지 않는 이유다.)

    **그리고 그 좁은 창에 실제로 물렸다**(2026-08-12). 스테이징만으로는 부족하다:
    마지막 `os.replace(stage, dest)` 가 SMB 에서 "목적지를 지우고 rename" 으로
    구현되면, 그 사이에 죽었을 때 **영속본이 통째로 사라진다**. 그날 허브가 그렇게
    됐고 사흘치를 트랜스크립트에서 되살렸다.

    그래서 교체를 두 단계로 가른다:

      1. 옛 세대를 rename 으로 :data:`PREV_SUFFIX` 에 물린다(바이트 복사 아님)
      2. 새 세대를 자리로 옮긴다 — **선택 파트 먼저, 필수 파트를 마지막에**

    필수 파트의 존재가 곧 "이 세대는 온전하다"는 표식이고, `restore_if_missing` 은
    그것만 보고 판단해 없으면 `.prev` 로 물러선다. 어느 지점에서 죽어도 둘 중
    하나는 온전하다 — 이것이 지키려는 유일한 불변식이다."""
    snapshot_path = snapshot_path if snapshot_path is not None else resolve_snapshot_path()
    if not snapshot_path or store is None:
        return "off"
    snap = getattr(store, "snapshot", None)
    if not callable(snap):
        return "off"     # 백엔드가 스냅샷을 모른다(테스트 스텁 등) — 조용히 지나간다
    # 새 리비전이 자격을 가져갔으면 나는 영속본을 건드리지 않는다(:data:`WRITER_SUFFIX`).
    holder = newer_writer(snapshot_path)
    if holder:
        return f"fenced: {holder}"
    stage = ""
    try:
        dest_dir = os.path.dirname(os.path.abspath(snapshot_path)) or "."
        os.makedirs(dest_dir, exist_ok=True)
        # 스테이징은 **같은 파일시스템 안**이어야 한다 — 그래야 os.replace 가 복사가
        # 아니라 rename 이 된다. 그래서 dest_dir 밑에 둔다.
        stage = os.path.join(dest_dir, STAGE_PREFIX + os.path.basename(snapshot_path))
        shutil.rmtree(stage, ignore_errors=True)   # 앞선 급사가 남긴 찌꺼기
        # 디렉터리는 **여기서** 만든다. 예전에는 dest_dir 이 이미 있는 영속 디렉터리라
        # 아무도 신경 쓸 필요가 없었는데, 스테이징은 매번 새로 생긴다. 만드는 일을
        # `snap()` 에 맡기면 그걸 하지 않는 구현에서 조용히 깨진다(기존 테스트의
        # 페이크가 정확히 그런 구현이었고, 그래서 이 결함을 잡아 줬다).
        os.makedirs(stage, exist_ok=True)
        produced = snap(stage)
        if not produced or not os.path.exists(produced):
            return "error: snapshot produced nothing"
        # 옛 세대를 물리기 **전에** 새 세대가 쓸 만한지 본다 — 아니면 아무것도
        # 건드리지 않는다(멀쩡한 영속본을 헛되이 .prev 로 미루지 않는다).
        parts = [(src, req, snapshot_path + src[len(produced):])
                 for src, req in _parts(produced)]
        if not all(os.path.exists(s) for s, req, _ in parts if req):
            return "error: snapshot produced nothing"
        # 복사는 수십 초 걸린다(757MB 에 25초 안팎). 그 사이에 새 리비전이 자격을
        # 가져갔을 수 있으므로 세대를 물리기 **직전에** 한 번 더 본다.
        holder = newer_writer(snapshot_path)
        if holder:
            return f"fenced: {holder}"

        _retire_generation(snapshot_path)

        # 선택 파트 먼저(False < True), **필수 파트를 마지막에**. 그 존재가 완료 표식이다.
        for src, required, dst in sorted(parts, key=lambda p: p[1]):
            if os.path.exists(src):
                os.replace(src, dst)
            elif os.path.exists(dst):
                # ⚠ 새 스냅샷에 사이드카(.wal)가 없는데 목적지의 **낡은 것**을 남기면
                # 새 본 파일과 옛 WAL 이 짝이 되어, 다음 복원이 어긋난 재생을 한다.
                # `_parts` 가 .wal 을 선택으로 두는 것은 "없을 수 있다"는 뜻이지
                # "있으면 그대로 둔다"가 아니다. 예전 코드는 그걸 그대로 뒀다.
                os.remove(dst)
        # 세대 루트가 있으면 이 스냅샷이 어느 세대까지 담았는지 적는다(:data:`SEEN_SUFFIX`).
        # 못 적으면 `ok` 가 아니다 — 다음 주기가 다시 시도하게 둔다.
        _mark_seen(snapshot_path)
        return "ok"
    except Exception as exc:                                   # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}"
    finally:
        if stage:
            shutil.rmtree(stage, ignore_errors=True)


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
        #: 마지막으로 **성공한** 스냅샷 시각. 시도 시각(`_last`)과 다르다 — 실패가
        #: 반복되면 `_last` 는 계속 갱신되는데 실제로 남은 것은 옛것이기 때문이다.
        self._last_ok: float | None = None
        self.last_state = "pending" if resolve_snapshot_path() else "off"

    def mark_dirty(self) -> None:
        self._dirty = True

    def due(self) -> bool:
        if not self.every or not self._dirty:
            return False
        return (self._clock() - self._last) >= self.every

    def done(self, state: str) -> None:
        self._last = self._clock()
        # A failed copy did not persist the write. Keep it pending so an idle
        # tick retries after one interval, even if no further job arrives.
        # "off" is the normal no-snapshot configuration and has no retry target.
        self._dirty = state not in ("ok", "off")
        self.last_state = state
        if state == "ok":
            self._last_ok = self._last

    def detail(self) -> dict:
        """밖에서 보는 진단. **"돌고 있다고 믿는 백업이 없는" 상태를 드러내는 것**이
        이 메서드의 존재 이유다.

        상태 문자열만으로는 부족하다 — `"ok"` 는 "언젠가 됐다"는 뜻이지 "방금 됐다"가
        아니다. `age_s` 가 곧 **지금 잃을 수 있는 양의 하한**이고, `None` 이면 이
        프로세스가 사는 동안 **한 번도 성공한 적이 없다**는 뜻이다(가장 위험한 값).

        2026-08-09 에 이 값이 없어서, 스냅샷이 도는지를 파일 mtime 으로 재다가
        20분을 헤맸다(Azure REST API 의 lastModified 와 컨테이너가 CIFS 로 보는
        mtime 이 달라 오판까지 했다)."""
        out = {"state": self.last_state,
               "every_s": self.every,
               "pending_write": self._dirty,
               "age_s": None if self._last_ok is None
                        else round(self._clock() - self._last_ok)}
        # `state:"off"` 옆에 `every_s:300` 이 나란히 있으면 "5분마다 돌아야 하는
        # 무언가가 멈춰 있다"로 읽힌다 — 2026-09-03 에 실제로 그렇게 읽혔고, 켜는
        # 경로를 찾다가 시간을 썼다. 켤 것이 아니다: 이 기능은 작업본을 **영속
        # 볼륨으로 복사**하는 것이고, 켜는 것은 배포의 의도적 행위다(모듈 §계약).
        # 복사해 둘 두 번째 자리가 없는 설치(데스크탑)에서는 꺼진 것이 정상이다.
        # 필드는 그대로 둔다 — 이것을 읽는 알람이 이미 있다(test_snapshot_visibility).
        # 대신 그 자리에서 뜻을 말한다.
        if self.last_state == "off":
            out["note"] = ("영속 스냅샷은 배포에서 켠다(VINC_STORE_SNAPSHOT). 복사해 둘 "
                           "두 번째 자리가 없는 설치에서는 꺼진 것이 정상이고, every_s 는 "
                           "그 경우 아무 일정도 뜻하지 않는다. 내구성은 POST /backup 이 맡는다.")
        return out


class DurableAckError(RuntimeError):
    """영속 세대 게시 실패. 로컬 커밋은 되돌아가지 않았으므로 **불확정 커밋**이다."""


def _fence_strict(snapshot_path: str) -> None:
    """ACK 경로의 울타리. :func:`newer_writer` 와 달리 표식을 못 읽으면 **닫힌 쪽**이다.

    표식이 아예 없으면 막지 않는다: 아직 아무도 자격을 가져가지 않았고, 기동 잡은
    ``claim_writer`` 보다 먼저 돈다. 리비전 이름이 없는 곳(데스크탑·테스트)은 울타리가 없다."""
    mine = writer_identity()
    if not mine:
        return
    try:
        with open(snapshot_path + WRITER_SUFFIX, encoding="utf-8") as f:
            holder = f.read().strip()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DurableAckError(f"writer marker unreadable: {exc}") from exc
    if _revision_no(holder) is None or _revision_no(mine) is None:
        raise DurableAckError(f"writer marker unparsable: {holder!r} (mine {mine!r})")
    if displaces(holder, mine):
        raise DurableAckError(f"fenced: {holder}")


def _fingerprint(live_path: str) -> tuple:
    """본 파일과 WAL 의 (크기, mtime_ns). 게시 직후 값과 같으면 그 뒤로 쓰기가 없었다.

    WAL 은 체크포인트 사이에 덧붙기만 해서 커밋마다 크기가 는다. 체크포인트는 WAL 을
    비우고 본 파일을 다시 써 mtime 이 바뀐다.
    ponytail: stat 비교다. 체크포인트 뒤 WAL 이 같은 크기로 다시 차고 본 파일 mtime 이
    같은 시계 틱에 머무는 경우는 못 가른다. 그게 보이면 WAL 해시로 올린다."""
    out = []
    for path in (live_path, live_path + ".wal"):
        try:
            st = os.stat(path)
            out.append((st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            out.append(None)
    return tuple(out)


class DurableAck:
    """쓰기 잡의 ACK 경계(VIN-82). 잡 큐의 ``on_before_done`` 이며 **쓰기 스레드에서** 돈다.

    이 호출이 돌아와야 그 잡의 DONE·200 이 보이고, 예외면 그 잡은 ERROR 다. 순서:
    마지막 게시 뒤로 파일이 그대로면 건너뜀(쓰기가 없던 잡은 게시 비용을 물지 않는다) →
    울타리(못 읽으면 닫힌 쪽) → WAL 만 게시(``dg.publish_wal``) → 거절되면 일관된 전체
    snapshot 을 새 세대로(``dg.publish``) → 오래된 세대 정리.

    게시 실패는 로컬 커밋을 되돌리지 않는다. 응답은 "불확정 커밋"이고 재시도가 안전한지는
    호출자 몫이다. 근거: [[vinc.plan.vin82-durable-ack-2026-09-29]]"""

    def __init__(self, store, live_path: str, snapshot_path: str | None = None, *,
                 clock=time.monotonic):
        self._store = store               # () -> 스토어. 앱은 close 에서 store 를 None 으로 둔다
        self.live_path = live_path
        self.snapshot_path = snapshot_path or resolve_snapshot_path()
        self.root = generations_root(self.snapshot_path)
        self._clock = clock
        self._published: tuple | None = None
        self.sequence = 0
        self.last = "pending"
        self.last_ms: int | None = None
        self.max_ms = 0
        self.publishes = 0
        self.failures = 0

    def __call__(self, job, result) -> None:
        t0 = self._clock()
        try:
            kind = self._publish()
        except Exception as exc:                               # noqa: BLE001
            self.failures += 1
            self.last = f"error: {type(exc).__name__}: {exc}"
            print(f"vinc: durable ack — {job.kind}: {self.last}", file=sys.stderr, flush=True)
            raise DurableAckError(
                "commit uncertain: the write may be applied locally, but its durable "
                f"generation was not published ({type(exc).__name__}: {exc})") from exc
        self.last_ms = round((self._clock() - t0) * 1000)
        self.last = kind
        if kind != "unchanged":
            self.publishes += 1
            self.max_ms = max(self.max_ms, self.last_ms)
        if kind == "full":
            # 드물고 비싸다(DB 전체 복사). 지연을 잴 때 이 줄로 센다.
            print(f"vinc: durable ack — full generation {self.sequence} ({self.last_ms} ms)",
                  file=sys.stderr, flush=True)

    def _publish(self) -> str:
        # 쓰기가 없던 잡은 공유를 한 번도 건드리지 않는다(울타리 읽기도 게시 쪽에만 있다).
        if _fingerprint(self.live_path) == self._published:
            return "unchanged"
        _fence_strict(self.snapshot_path)
        try:
            record = dg.publish_wal(self.root, self.live_path,
                                    sequence=dg.next_sequence(self.root))
            kind = "wal"
        except dg.GenerationError:
            # 첫 세대 · 체크포인트로 본 파일이 바뀜 · Windows 의 본 파일 배타 잠금.
            record = self._publish_full()
            kind = "full"
        self._published = _fingerprint(self.live_path)
        self.sequence = record["sequence"]
        try:
            dg.prune(self.root, keep=DURABLE_KEEP)
        except OSError as exc:            # 게시는 끝났다 — 정리 실패로 ACK 를 거두지 않는다
            print(f"vinc: durable ack — prune failed: {exc}", file=sys.stderr, flush=True)
        return kind

    def _publish_full(self) -> dict:
        store = self._store()
        if store is None or not callable(getattr(store, "snapshot", None)):
            raise DurableAckError("no store to snapshot")
        # 스테이징은 작업본 옆(로컬 디스크)이다. `store.snapshot` 이 CHECKPOINT 뒤 일관된
        # 본 파일(+WAL)을 여기 떨구고, `dg.publish` 가 그것을 영속 볼륨으로 올린다.
        stage = self.live_path + ".durable-stage"
        shutil.rmtree(stage, ignore_errors=True)
        os.makedirs(stage)
        try:
            produced = store.snapshot(stage)
            wal = produced + ".wal"
            return dg.publish(self.root, produced, sequence=dg.next_sequence(self.root),
                              wal=wal if os.path.exists(wal) else None)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def detail(self) -> dict:
        """`/health` 에 싣는 값. 켜기 전에 지연을 재는 재료다."""
        return {"sequence": self.sequence, "last": self.last, "last_ms": self.last_ms,
                "max_ms": self.max_ms, "publishes": self.publishes, "failures": self.failures}
