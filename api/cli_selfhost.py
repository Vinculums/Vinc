# -*- coding: utf-8 -*-
"""`vinc selfhost` — 셀프호스트 허브의 비밀번호·사람·좌석 연결.

계획 `vinc.plan.self-hosted-hub-web-2026-11-11` S2·S8. 허브 컨테이너 안에서
`docker exec -it <hub> python -m api.cli selfhost ...` 로 부른다(상태는 볼륨에 산다).

- `hash-password`: `VINC_LOCAL_PASSWORD_HASH` 값을 만든다(한 사람 허브).
- `add-user NAME` · `remove-user NAME` · `list-users`: 사람 표. 좌석 자격보다 많이 더하지 않는다.
- `link [--name N]` · `unlink` · `seats`: 계정 연결(기기 코드), 끊기, 지금 자격 보기.

비밀번호는 화면에 안 보이게 두 번 묻거나 표준 입력 첫 줄에서 읽고, 어디에도 남기지 않는다.
"""
from __future__ import annotations

import getpass
import json
import sys


def add_parser(sub) -> None:
    sh = sub.add_parser("selfhost", help="self-hosted hub: passwords, people and seats")
    shs = sh.add_subparsers(dest="selfhostcmd", required=True)
    shs.add_parser("hash-password", help="read a password (hidden prompt, or stdin) and print "
                                         "the value for VINC_LOCAL_PASSWORD_HASH")
    a = shs.add_parser("add-user", help="add a person who can sign in (needs a seat)")
    a.add_argument("username")
    r = shs.add_parser("remove-user", help="remove a person")
    r.add_argument("username")
    shs.add_parser("list-users", help="list people and whether each can write")
    lk = shs.add_parser("link", help="link this hub to your vincs.io account to use bought seats")
    lk.add_argument("--name", default=None, help="a name for this hub on the account page")
    shs.add_parser("unlink", help="forget the account link (back to one person)")
    shs.add_parser("seats", help="show the seats this hub may use now")


def _password() -> str:
    if sys.stdin.isatty():
        first = getpass.getpass("Password: ")
        if getpass.getpass("Again: ") != first:
            raise ValueError("the two passwords differ")
        return first
    return sys.stdin.readline().rstrip("\r\n")


def run(args) -> int:
    from vinc_mcp import selfhost_seats, selfhost_users
    from vinc_mcp.selfhost_web import hash_password
    cmd = args.selfhostcmd
    try:
        if cmd == "hash-password":
            print(hash_password(_password()))
            return 0
        if cmd == "add-user":
            allowed = selfhost_seats.entitlement()["users_allowed"]
            row = selfhost_users.add(args.username, _password(), allowed=allowed)
            print(f"added {row['username']}")
            return 0
        if cmd == "remove-user":
            if selfhost_users.remove(args.username):
                print(f"removed {args.username}")
                return 0
            print(f"no such person: {args.username}", file=sys.stderr)
            return 1
        if cmd == "list-users":
            allowed = selfhost_seats.entitlement()["users_allowed"]
            print(json.dumps({"users_allowed": allowed,
                              "users": selfhost_users.listing(allowed)}, indent=2))
            return 0
        if cmd == "link":
            return selfhost_seats.link(args.name)
        if cmd == "unlink":
            return selfhost_seats.unlink()
        if cmd == "seats":
            print(json.dumps(selfhost_seats.entitlement(), indent=2))
            return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 2
