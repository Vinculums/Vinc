# -*- coding: utf-8 -*-
"""셀프호스트 허브의 사람들: 이름과 scrypt 해시 몇 줄을 볼륨의 0600 파일 하나에 둔다.

계획 `vinc.plan.self-hosted-hub-web-2026-11-11` S8. 모두가 같은 그래프 하나(`selfhost.owner()`)를
쓴다 — v1 은 "그래프 하나, 사람 여럿"이다. 사람 수는 좌석 자격(`selfhost_seats.entitlement`)이 정한다.

- 표가 비어 있으면 S2 의 한 사람 로그인(`VINC_LOCAL_PASSWORD_HASH` 나 `local-password` 파일)이 그대로
  돈다. 처음 사람을 더할 때 그 비밀번호가 있으면 첫 줄로 옮겨 아무도 잠기지 않게 한다.
- 자격이 줄면 먼저 더한 사람부터 그 수만큼 쓰기를 갖고, 나머지는 읽기만 한다(아무도 읽기에서
  잠기지 않는다).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

USERS_FILE = "selfhost-users.json"
_NAME = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
_lock = threading.Lock()


def _path() -> str:
    from .selfhost_web import state_dir
    return os.path.join(state_dir(), USERS_FILE)


def _load() -> list[dict]:
    try:
        with open(_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    rows = data.get("users") if isinstance(data, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and r.get("username") and r.get("hash")]


def _save(rows: list[dict]) -> None:
    path = _path()
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"users": rows}, fh)
    os.replace(tmp, path)


def legacy_name() -> str:
    return (os.environ.get("VINC_LOCAL_USER_NAME") or "local").strip() or "local"


def users() -> list[dict]:
    """더한 순서대로. 각 줄은 {username, hash, created}."""
    return sorted(_load(), key=lambda r: float(r.get("created") or 0))


def find(username: str) -> tuple[int, dict] | None:
    """(더한 순서의 자리, 줄). 이름은 대소문자를 가리지 않는다."""
    want = username.strip().lower()
    for rank, row in enumerate(users()):
        if row["username"].lower() == want:
            return rank, row
    return None


class UserError(ValueError):
    pass


def add(username: str, password: str, *, allowed: int) -> dict:
    """사람 하나를 더한다. 자격(`allowed`)보다 많아지면 거절한다."""
    from .selfhost_web import hash_password, password_hash
    name = username.strip()
    if not _NAME.fullmatch(name):
        raise UserError("a username is 1 to 64 letters, digits or . _ @ -")
    with _lock:
        rows = users()
        if not rows:
            legacy = password_hash()
            if legacy and name.lower() != legacy_name().lower():
                rows.append({"username": legacy_name(), "hash": legacy, "created": time.time() - 1})
        if any(r["username"].lower() == name.lower() for r in rows):
            raise UserError(f"{name} already exists")
        if len(rows) + 1 > allowed:
            raise UserError(f"this hub has {allowed} seat(s) for people and {len(rows)} in use: "
                            "buy more on your vincs.io account, then run `vinc selfhost link`")
        row = {"username": name, "hash": hash_password(password), "created": time.time()}
        rows.append(row)
        _save(rows)
    return {"username": name, "created": row["created"]}


def remove(username: str) -> bool:
    with _lock:
        rows = users()
        keep = [r for r in rows if r["username"].lower() != username.strip().lower()]
        if len(keep) == len(rows):
            return False
        _save(keep)
        return True


def listing(allowed: int) -> list[dict]:
    """이름·더한 시각·쓰기 가능 여부. 해시는 싣지 않는다."""
    return [{"username": r["username"], "created": r.get("created"), "can_write": rank < allowed}
            for rank, r in enumerate(users())]
