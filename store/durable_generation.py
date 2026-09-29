# -*- coding: utf-8 -*-
"""불변 DB/WAL 세대와 검증 가능한 커밋 표식.

이 모듈은 아직 ACK 경로에 연결되지 않은 VIN-82 원시기능이다. 열려 있는 DB를 직접
복사하지 말고, 단일 writer가 만든 일관된 snapshot의 본 파일과 WAL만 입력한다.
근거: [[vinc.plan.vin82-durable-ack-2026-09-29]]
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path


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


def _validated(root: Path, sequence: int, marker: Path) -> tuple[Path, Path | None]:
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        if record["format"] != 1 or record["sequence"] != sequence:
            raise ValueError("wrong format or sequence")
        generation = root / f"generation-{sequence}"
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


def restore(root: str | os.PathLike[str], live_path: str | os.PathLike[str]) -> str:
    """최신 커밋 세대만 복원한다. 최신 표식이 깨졌으면 이전 세대로 후퇴하지 않는다.

    최신 세대가 이미 ACK됐을 수 있어 묵시적 후퇴는 데이터 손실이다. 표식 없는
    generation/stage는 아직 ACK되지 않았으므로 무시한다.
    """
    root = Path(root)
    live = Path(live_path)
    if live.exists():
        return "present"
    if Path(f"{live}.wal").exists():
        raise GenerationError("orphan live WAL after interrupted restore")
    commits = _commit_paths(root)
    if not commits:
        return "empty"
    seq, marker = commits[0]
    base, wal = _validated(root, seq, marker)
    live.parent.mkdir(parents=True, exist_ok=True)
    stage = live.with_name(live.name + ".restore-stage")
    if stage.exists() or Path(f"{stage}.wal").exists():
        raise GenerationError("stale restore stage requires inspection")
    try:
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
        return f"restored:{seq}"
    except Exception:
        # 복구 실패의 스테이징은 다음 기동이 무심히 덮지 않도록 보존한다.
        raise
