# -*- coding: utf-8 -*-
"""로컬 모드의 폴더(VIN-130) — 사람이 고른 폴더가 그래프를 담아도 되는가.

데스크톱은 기본으로 계정 그래프를 보고, 설정에서 로컬 모드를 켜면 이 기기의 폴더에
그래프를 둔다. 여기서는 그 폴더를 **받기 전에** 살핀다:

  - 절대경로이고 폴더인가(없으면 만들 수 있는가), 쓸 수 있는가
  - 이미 Vinc 스토어(`db`)가 있는가 — 있으면 그것을 연다(덮거나 지우지 않는다)
  - 동기화 폴더(Dropbox·OneDrive·iCloud·Google Drive 등)나 네트워크 드라이브인가

⚠ 마지막 항목이 이 파일의 이유다. 임베디드 DB 와 그 WAL 을 다른 writer(동기화 클라이언트·
SMB)가 만지는 폴더에 두면, 허브가 세 번 통째로 죽은 것과 같은 계열의 사고가 개인 기기에서
난다(SMB 위 임베디드 DB). 그래서 막지는 않되 **말하고, 사람이 한 번 더 확인해야** 받는다
(`POST /config` 가 `accept_risk` 없이 409 로 돌려보낸다).
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

#: 경로 조각(대소문자 무시)으로 알아보는 동기화 폴더. 앞에 있는 것이 이긴다.
#: macOS 는 새 클라이언트들이 `~/Library/CloudStorage/<공급자-계정>` 아래로 옮겨 가서
#: 그 자리 자체를 따로 본다.
_SYNCED_MARKERS: tuple[tuple[str, str], ...] = (
    ("com~apple~clouddocs", "iCloud Drive"),
    ("mobile documents", "iCloud Drive"),
    ("icloud drive", "iCloud Drive"),
    ("icloudrive", "iCloud Drive"),
    ("dropbox", "Dropbox"),
    ("onedrive", "OneDrive"),
    ("google drive", "Google Drive"),
    ("googledrive", "Google Drive"),
    ("my drive", "Google Drive"),
    ("box sync", "Box"),
    ("pcloud drive", "pCloud"),
    ("nextcloud", "Nextcloud"),
    ("owncloud", "ownCloud"),
    ("yandex.disk", "Yandex Disk"),
    ("mega", "MEGA"),
)

#: 네트워크 파일시스템(리눅스 `/proc/mounts`·macOS `mount` 의 형식 이름).
_NETWORK_FS = frozenset({
    "nfs", "nfs4", "cifs", "smb3", "smbfs", "smb", "afpfs", "webdav", "davfs",
    "fuse.sshfs", "sshfs", "fuse.rclone", "9p", "ncpfs", "fuse.davfs2",
})


def _synced_provider(path: pathlib.Path) -> str | None:
    parts = [p.lower() for p in path.parts]
    for i, part in enumerate(parts):
        # `~/Library/CloudStorage/OneDrive-Personal` 처럼 공급자 이름이 한 칸 뒤에 온다.
        if part == "cloudstorage" and i > 0 and parts[i - 1] == "library":
            nxt = path.parts[i + 1] if i + 1 < len(path.parts) else ""
            return nxt.split("-")[0] or "a cloud storage provider"
        for marker, name in _SYNCED_MARKERS:
            # "mega" 같은 짧은 표식은 조각 전체가 같을 때만(오탐: "megaproject").
            if (len(marker) <= 4 and part == marker) or (len(marker) > 4 and marker in part):
                return name
    return None


def _mount_fstype(path: pathlib.Path) -> str | None:
    """경로가 올라탄 마운트의 파일시스템 형식(리눅스·macOS). 모르면 None."""
    target = str(path)
    rows: list[tuple[str, str]] = []
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/mounts", encoding="utf-8") as f:
                for line in f:
                    bits = line.split()
                    if len(bits) >= 3:
                        rows.append((bits[1].replace("\\040", " "), bits[2]))
        elif sys.platform == "darwin":
            out = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=2)
            for line in out.stdout.splitlines():
                # "//u@srv/share on /Volumes/share (smbfs, nodev, ...)"
                if " on " not in line or "(" not in line:
                    continue
                mp = line.split(" on ", 1)[1].rsplit(" (", 1)[0]
                fstype = line.rsplit("(", 1)[1].split(",")[0].strip(" )")
                rows.append((mp, fstype))
    except (OSError, subprocess.SubprocessError):
        return None
    best = ""
    kind = None
    for mp, fstype in rows:
        if (target == mp or target.startswith(mp.rstrip("/") + "/")) and len(mp) > len(best):
            best, kind = mp, fstype
    return kind


def _is_network(path: pathlib.Path) -> bool:
    raw = str(path)
    if raw.startswith("\\\\") or raw.startswith("//"):
        return True                                  # UNC 경로
    if sys.platform == "win32":
        try:
            import ctypes
            drive = os.path.splitdrive(raw)[0]
            if drive:
                # 4 = DRIVE_REMOTE(매핑된 네트워크 드라이브)
                return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4  # type: ignore[attr-defined]
        except Exception:                            # noqa: BLE001 — 모르면 네트워크 아님
            return False
        return False
    fstype = _mount_fstype(path)
    return bool(fstype and fstype.lower() in _NETWORK_FS)


def _nearest_existing(path: pathlib.Path) -> pathlib.Path:
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def inspect_folder(raw: str) -> dict:
    """폴더 하나를 살핀다. `ok` 가 False 면 받을 수 없고, `risks` 가 있으면 확인이 필요하다."""
    out: dict = {"path": raw, "ok": False, "exists": False, "has_store": False,
                 "store_bytes": 0, "writable": False, "synced": None, "network": False,
                 "risks": [], "problem": None}
    if not isinstance(raw, str) or not raw.strip():
        out["problem"] = "empty"
        return out
    path = pathlib.Path(os.path.expanduser(raw.strip()))
    if not path.is_absolute():
        out["problem"] = "not_absolute"
        return out
    out["path"] = str(path)
    if path.exists() and not path.is_dir():
        out["problem"] = "not_a_folder"
        return out
    out["exists"] = path.is_dir()
    db = path / "db"
    if db.is_file():
        out["has_store"] = True
        try:
            out["store_bytes"] = db.stat().st_size
        except OSError:
            pass
    elif db.exists():
        # `db` 라는 이름의 폴더가 있으면 스토어 파일을 그 자리에 만들 수 없다.
        out["problem"] = "db_is_a_folder"
        return out
    probe = path if out["exists"] else _nearest_existing(path)
    out["writable"] = os.access(probe, os.W_OK)
    if not out["writable"]:
        out["problem"] = "not_writable"
        return out
    out["synced"] = _synced_provider(path)
    out["network"] = _is_network(probe)
    if out["synced"]:
        out["risks"].append("synced")
    if out["network"]:
        out["risks"].append("network")
    out["ok"] = True
    return out


def suggested_folder(home: pathlib.Path) -> str:
    """처음 켤 때 권하는 폴더: 이미 스토어가 있는 `~/.vinc`, 없으면 `Documents/Vinc`."""
    if (home / "db").is_file():
        return str(home)
    docs = pathlib.Path.home() / "Documents"
    return str((docs if docs.is_dir() else pathlib.Path.home()) / "Vinc")
