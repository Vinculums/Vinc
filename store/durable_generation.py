# -*- coding: utf-8 -*-
"""불변 DB/WAL 세대와 검증 가능한 커밋 표식.

VIN-82 원시기능이다. ACK 경로 배선은 ``store/persistence.py`` 의 ``DurableAck`` 가
한다. 열려 있는 DB를 직접 복사하지 말고, 단일 writer가 만든 일관된 snapshot의 본
파일과 WAL만 입력한다. 근거: [[vinc.plan.vin82-durable-ack-2026-09-29]]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

# 세대 루트 안의 번호 붙은 항목 전부(표식·임시 표식·세대·stage).
_ENTRY = re.compile(r"(?:commit|stage-commit|generation|stage)-(\d+)(?:\.json)?")


class GenerationError(RuntimeError):
    """커밋 세대가 손상됐거나 요청이 기존 세대를 덮으려 할 때."""


def _digest(path: Path) -> dict[str, int | str]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as src:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            size += len(block)
            h.update(block)
    return {"size": size, "sha256": h.hexdigest()}


def _live_digest(path: Path) -> dict[str, int | str]:
    """열린 스토어의 본 파일을 잰다. 못 읽으면 WAL 승격을 거절한다.

    Windows 에서는 엔진이 본 파일을 배타 잠금해서 열 수 없다. 그 경우도 다른 거절과
    같이 GenerationError 로 올려, 호출자가 전체 snapshot 으로 돌아가게 한다.
    """
    try:
        return _digest(path)
    except OSError as exc:
        raise GenerationError(
            f"live base unreadable ({exc.__class__.__name__}); full snapshot required") from exc


def _copy_synced(src: Path, dst: Path) -> dict[str, int | str]:
    h = hashlib.sha256()
    size = 0
    with src.open("rb") as read, dst.open("xb") as write:
        for block in iter(lambda: read.read(1024 * 1024), b""):
            write.write(block)
            h.update(block)
            size += len(block)
        write.flush()
        os.fsync(write.fileno())
    return {"size": size, "sha256": h.hexdigest()}


def _sync_dir(path: Path) -> None:
    # Windows는 디렉터리 fsync를 제공하지 않는다. 파일 fsync와 검증 표식은 유지한다.
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _commit_paths(root: Path) -> list[tuple[int, Path]]:
    commits = []
    for path in root.glob("commit-*.json"):
        raw = path.name[len("commit-"):-len(".json")]
        if raw.isdecimal() and int(raw) > 0:
            commits.append((int(raw), path))
    return sorted(commits, reverse=True)


def latest_sequence(root: str | os.PathLike[str]) -> int:
    """가장 큰 커밋 표식 번호. 표식의 유효성은 :func:`restore`가 검사한다."""
    paths = _commit_paths(Path(root))
    return paths[0][0] if paths else 0


def next_sequence(root: str | os.PathLike[str]) -> int:
    """다음 게시 번호. 커밋 표식만이 아니라 남은 stage·generation 번호도 넘는다.

    실패한 시도의 stage 는 조사용으로 남는데, 그 번호를 다시 쓰려 하면 게시가 거절되고
    그 뒤의 모든 쓰기가 같은 이유로 막힌다."""
    root = Path(root)
    if not root.is_dir():
        return 1
    numbers = [int(m.group(1)) for path in root.iterdir()
               if (m := _ENTRY.fullmatch(path.name))]
    return max(numbers, default=0) + 1


def prune(root: str | os.PathLike[str], *, keep: int = 2) -> int:
    """최근 커밋 ``keep`` 개와 그것들이 기대는 본 세대만 남기고 더 오래된 항목을 지운다.

    쓰기마다 세대가 하나씩 쌓이므로 상한이 없으면 공유가 찬다. 최신 커밋보다 번호가 큰
    항목(최신 ACK 뒤에 실패한 시도)은 조사용으로 남긴다. 남길 표식을 못 읽으면 아무것도
    지우지 않는다. 표식(파일)을 먼저 지우고 디렉터리를 나중에 지운다: 도중에 죽어도 표식
    없는 디렉터리는 복구 대상이 아니다. 지운 항목 수를 돌려준다."""
    root = Path(root)
    commits = _commit_paths(root)[:keep]
    if not commits:
        return 0
    needed: set[int] = set()
    try:
        for seq, marker in commits:
            needed.add(seq)
            record = json.loads(marker.read_text(encoding="utf-8"))
            if record["format"] == 2:
                needed.add(int(record["base_sequence"]))
    except (OSError, KeyError, TypeError, ValueError):
        return 0
    newest = commits[0][0]
    doomed = [path for path in root.iterdir()
              if (m := _ENTRY.fullmatch(path.name))
              and int(m.group(1)) < newest and int(m.group(1)) not in needed]
    doomed.sort(key=lambda path: path.is_dir())      # 파일(표식) 먼저
    for path in doomed:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    return len(doomed)


def publish(root: str | os.PathLike[str], base: str | os.PathLike[str],
            *, sequence: int, wal: str | os.PathLike[str] | None = None) -> dict:
    """일관된 본 파일/WAL을 새 불변 세대로 올리고 마지막에 표식을 게시한다.

    같은 번호를 다시 쓰지 않는다. 실패한 시도의 stage는 남겨 조사할 수 있고, 커밋
    표식이 없으므로 복구 대상이 아니다. 호출자는 게시 직후 예외가 나면 응답을
    불확정 커밋으로 다뤄야 한다.
    """
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
        raise ValueError("sequence must be a positive integer")
    root = Path(root)
    base = Path(base)
    wal = Path(wal) if wal is not None else None
    if not base.is_file() or (wal is not None and not wal.is_file()):
        raise GenerationError("source pair is incomplete")
    root.mkdir(parents=True, exist_ok=True)
    newest = latest_sequence(root)
    if sequence <= newest:
        raise GenerationError(f"sequence {sequence} is not newer than {newest}")
    stage = root / f"stage-{sequence}"
    generation = root / f"generation-{sequence}"
    marker = root / f"commit-{sequence}.json"
    marker_tmp = root / f"stage-commit-{sequence}.json"
    if any(path.exists() for path in (stage, generation, marker, marker_tmp)):
        raise GenerationError("generation or stage already exists")
    stage.mkdir()
    base_meta = _copy_synced(base, stage / "db")
    wal_meta = _copy_synced(wal, stage / "db.wal") if wal is not None else None
    # 입력이 복사 도중 바뀌었다면 게시하지 않는다. 단일 writer 계약의 실수도 검출한다.
    if _digest(base) != base_meta or (wal is not None and _digest(wal) != wal_meta):
        raise GenerationError("source pair changed during copy")
    _sync_dir(stage)
    os.replace(stage, generation)
    _sync_dir(root)
    record = {"format": 1, "sequence": sequence, "base": base_meta,
              "wal": wal_meta}
    with marker_tmp.open("x", encoding="utf-8", newline="\n") as out:
        json.dump(record, out, sort_keys=True, separators=(",", ":"))
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(marker_tmp, marker)
    _sync_dir(root)
    return record


def publish_wal(root: str | os.PathLike[str], live_base: str | os.PathLike[str],
                *, sequence: int) -> dict:
    """같은 본 파일에서 난 WAL만 새 세대로 게시한다.

    전체 본 파일은 영속 공유에 다시 복사하지 않는다. 대신 로컬 본 파일의 SHA256을
    복사 전후에 검사한다. 자동/수동 체크포인트가 본 파일을 바꾸거나 잔여 짝 파일이
    있으면 게시를 거절한다. 호출자는 그때 일관된 전체 snapshot을 새 세대로 올려야
    한다. 이 검사는 큰 DB의 로컬 읽기 비용을 치른다.
    """
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
        raise ValueError("sequence must be a positive integer")
    root = Path(root)
    live_base = Path(live_base)
    wal = Path(f"{live_base}.wal")
    if not wal.is_file():
        raise GenerationError("live WAL is missing; publish a new full base")
    if any(Path(f"{live_base}{suffix}").exists()
           for suffix in (".wal.checkpoint", ".shadow")):
        raise GenerationError("unfinished checkpoint companion; WAL promotion refused")
    commits = _commit_paths(root)
    if not commits:
        raise GenerationError("no committed base generation")
    newest, marker = commits[0]
    if sequence <= newest:
        raise GenerationError(f"sequence {sequence} is not newer than {newest}")
    # 최신 커밋도 반드시 검증한다. 깨진 최신 세대를 건너뛰고 옛 본 파일에 WAL을
    # 붙이면 이전에 ACK된 쓰기를 잃을 수 있다.
    _validated(root, newest, marker)
    record = json.loads(marker.read_text(encoding="utf-8"))
    base_sequence = (record["sequence"] if record["format"] == 1
                     else record["base_sequence"])
    base_record = json.loads((root / f"commit-{base_sequence}.json").read_text(
        encoding="utf-8"))
    base_meta = base_record["base"]
    if _live_digest(live_base) != base_meta:
        raise GenerationError("local base changed since committed base; full snapshot required")
    stage = root / f"stage-{sequence}"
    generation = root / f"generation-{sequence}"
    next_marker = root / f"commit-{sequence}.json"
    marker_tmp = root / f"stage-commit-{sequence}.json"
    if any(path.exists() for path in (stage, generation, next_marker, marker_tmp)):
        raise GenerationError("generation or stage already exists")
    stage.mkdir()
    wal_meta = _copy_synced(wal, stage / "db.wal")
    if _live_digest(live_base) != base_meta or _digest(wal) != wal_meta:
        raise GenerationError("local base or WAL changed during copy")
    if any(Path(f"{live_base}{suffix}").exists()
           for suffix in (".wal.checkpoint", ".shadow")):
        raise GenerationError("checkpoint began during WAL copy")
    _sync_dir(stage)
    os.replace(stage, generation)
    _sync_dir(root)
    committed = {"format": 2, "sequence": sequence,
                 "base_sequence": base_sequence, "base": base_meta,
                 "wal": wal_meta}
    with marker_tmp.open("x", encoding="utf-8", newline="\n") as out:
        json.dump(committed, out, sort_keys=True, separators=(",", ":"))
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(marker_tmp, next_marker)
    _sync_dir(root)
    return committed


def _validated(root: Path, sequence: int, marker: Path) -> tuple[Path, Path | None]:
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        if record["format"] not in (1, 2) or record["sequence"] != sequence:
            raise ValueError("wrong format or sequence")
        generation = root / f"generation-{sequence}"
        if record["format"] == 2:
            base_sequence = record["base_sequence"]
            if (not isinstance(base_sequence, int) or base_sequence <= 0 or
                    base_sequence >= sequence):
                raise ValueError("invalid base sequence")
            base_record = json.loads((root / f"commit-{base_sequence}.json").read_text(
                encoding="utf-8"))
            if (base_record["format"] != 1 or base_record["sequence"] != base_sequence
                    or base_record["base"] != record["base"]):
                raise ValueError("base generation manifest mismatch")
            base = root / f"generation-{base_sequence}" / "db"
        else:
            base = generation / "db"
        if _digest(base) != record["base"]:
            raise ValueError("base digest mismatch")
        wal = generation / "db.wal"
        if record["wal"] is None:
            if wal.exists():
                raise ValueError("unexpected WAL")
            return base, None
        if _digest(wal) != record["wal"]:
            raise ValueError("WAL digest mismatch")
        return base, wal
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise GenerationError(f"committed generation {sequence} is invalid: {exc}") from exc


def restore(root: str | os.PathLike[str], live_path: str | os.PathLike[str], *,
            newer_than: int = 0, skip_invalid: bool = False) -> str:
    """최신 커밋 세대를 복원한다. 기본은 최신 표식이 깨졌으면 이전 세대로 후퇴하지 않는다.

    최신 세대가 이미 ACK됐을 수 있어 묵시적 후퇴는 데이터 손실이다. 표식 없는
    generation/stage는 아직 ACK되지 않았으므로 무시한다.

    ``newer_than`` 이하 번호는 보지 않는다(다른 사본이 이미 그만큼 담았다).
    ``skip_invalid`` 면 깨진 세대를 건너뛰고 다음 번호를 본다. 건너뛴 것은 반환값
    뒤에 ``; skipped ...`` 로 붙으므로 부르는 쪽이 드러내야 한다.
    반환: ``present`` · ``empty`` · ``restored:N`` (건너뜀이 있으면 그 뒤에 덧붙음)
    """
    root = Path(root)
    live = Path(live_path)
    if live.exists():
        return "present"
    if Path(f"{live}.wal").exists():
        raise GenerationError("orphan live WAL after interrupted restore")
    skipped: list[str] = []
    for seq, marker in _commit_paths(root):
        if seq <= newer_than:
            break
        try:
            base, wal = _validated(root, seq, marker)
        except GenerationError as exc:
            if not skip_invalid:
                raise
            skipped.append(str(exc))
            continue
        live.parent.mkdir(parents=True, exist_ok=True)
        stage = live.with_name(live.name + ".restore-stage")
        if stage.exists() or Path(f"{stage}.wal").exists():
            raise GenerationError("stale restore stage requires inspection")
        # 실패한 복구의 스테이징은 다음 기동이 무심히 덮지 않도록 지우지 않는다.
        shutil.copy2(base, stage)
        if wal is not None:
            shutil.copy2(wal, Path(f"{stage}.wal"))
        # 복사 중 원격 세대가 손상되거나 바뀌어도 작업본을 게시하지 않는다.
        if _digest(stage) != _digest(base) or (wal is not None and
                                            _digest(Path(f"{stage}.wal")) != _digest(wal)):
            raise GenerationError("restore copy changed")
        if wal is not None:
            os.replace(Path(f"{stage}.wal"), Path(f"{live}.wal"))
        os.replace(stage, live)
        return f"restored:{seq}" + _skipped_note(skipped)
    return "empty" + _skipped_note(skipped)


def _skipped_note(skipped: list[str]) -> str:
    return f"; skipped {len(skipped)} invalid: {' | '.join(skipped)}" if skipped else ""
