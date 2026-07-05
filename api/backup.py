# -*- coding: utf-8 -*-
"""일관 스냅샷 백업(설계 [[weg.c.memory-standalone]] §11 P2·R9).

``vinc backup`` 은 (1) 스토어 데이터 디렉토리의 일관 스냅샷 + (2) ``packs/``(내보낸
컨텍스트 팩)·``profile.yml``(도메인 프로필) 을 한 아카이브 디렉토리에 모은다 —
사용자가 자기 데이터를 통째로 이관·보존할 수 있는 단일 통로(R9).

**쓰기 정지의 책임**: ``EmbeddedBackend.snapshot`` 은 데이터 디렉토리를 그대로
복사할 뿐 쓰기를 멈추지 않는다. 일관성은 **잡 큐가 보장**한다 — backup 은 잡으로
실행되고, 워커는 하나뿐이라 backup 이 도는 동안 다른 쓰기 잡을 처리하지 않는다
(설계 §11 P2, `store.embedded_backend.snapshot` docstring). 그래서 이 함수는 반드시
워커 스레드에서(=JobQueue 경유) 호출되어야 한다.

stdlib 전용(``shutil``·``pathlib``).
"""
from __future__ import annotations

import shutil
from pathlib import Path


def make_backup(store, out_dir: str | Path, *, packs_dir: str | Path | None = None,
                profile_path: str | Path | None = None) -> dict:
    """스토어 스냅샷 + packs·profile 을 ``out_dir`` 아래로 모은다. 반환은 포함 항목
    경로 맵. **워커 스레드에서 호출**(쓰기 정지 보장 — 위 모듈 docstring)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # (1) 스토어 데이터 디렉토리 일관 스냅샷(§3-1-1). 쓰기 정지는 잡 큐가 보장.
    store_dest = store.snapshot(str(out))

    included: dict[str, str] = {"store": store_dest}

    # (2) 내보낸 컨텍스트 팩 동봉(있으면).
    if packs_dir is not None:
        src = Path(packs_dir)
        if src.is_dir():
            dest = out / "packs"
            shutil.copytree(src, dest, dirs_exist_ok=True)
            included["packs"] = str(dest)

    # (3) 도메인 프로필 동봉(있으면).
    if profile_path is not None:
        src = Path(profile_path)
        if src.is_file():
            dest = out / src.name
            shutil.copy2(src, dest)
            included["profile"] = str(dest)

    return {"archive": str(out), "included": included}
