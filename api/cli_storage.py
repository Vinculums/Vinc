# -*- coding: utf-8 -*-
"""`vinc storage` — 그래프가 사는 곳을 보고, 사용자 데이터베이스를 연결하고, 옮긴다.

계획 `vinc.plan.bring-your-own-database-2026-11-08` §2 B5. 세 동사:

- `status`  : 설정의 저장 위치와, 이 기기의 사이드카가 떠 있으면 그것이 연 엔진.
- `connect` : Neo4j 하나를 살핀 뒤(`/config/inspect-database` 와 같은 확인) 저장 위치로 적는다. 데이터는
              옮기지 않는다(빈 데이터베이스로 시작하거나 이미 있는 Vinc 그래프를 연다).
- `move`    : 지금 그래프를 데이터베이스나 폴더로 옮기고, 다 왔을 때만 위치를 바꾼다(`/config/move`).

이 기기의 사이드카(데스크톱 앱)가 떠 있으면 그쪽 HTTP 로 보낸다. 같은 설정 파일을 두 프로세스가 따로
쓰지 않게, 그리고 이동이 사이드카의 쓰기 큐 안에서 돌게 하려는 것이다. 떠 있지 않으면 이 프로세스가
같은 함수를 부른다.

비밀번호는 `VINC_DATABASE_SECRET` 이 있으면 그것을, 없으면 묻는다(화면에 안 보인다). 받은 비밀번호는
OS 키링(데스크톱 셸과 같은 서비스 이름)에 넣을 수 있으면 넣고, 못 넣으면 그렇다고 말한다 — 그때는
`vinc serve` 를 띄울 때 같은 환경 변수로 준다. 설정 파일·출력에는 싣지 않는다.
"""
from __future__ import annotations

import getpass
import os
import sys
import time
from urllib.parse import urlsplit

#: 데스크톱 셸의 키링 서비스 이름(`app/src-tauri/src/secret.rs` 의 `SERVICE`). 같은 항목을 같이 쓴다.
KEYRING_SERVICE = "io.vincs.app"


def add_parser(sub) -> None:
    st = sub.add_parser("storage", help="where the graph lives: status, connect your own Neo4j, move")
    ss = st.add_subparsers(dest="storagecmd", required=True)
    ss.add_parser("status", help="show the storage location and the engine the sidecar opened")
    c = ss.add_parser("connect", help="check a Neo4j database and make it the storage location "
                                      "(no data is moved)")
    _db_args(c, required=True)
    c.add_argument("--accept-risk", action="store_true",
                   help="accept warnings such as an unencrypted connection or data that is not Vinc's")
    c.add_argument("--check-only", action="store_true", help="check the database and write nothing")
    m = ss.add_parser("move", help="copy the graph to a database or a folder and switch only when "
                                   "every row arrived")
    m.add_argument("--to", choices=("database", "folder"), required=True)
    m.add_argument("--path", help="folder for --to folder (default: the Vinc home)")
    _db_args(m, required=False)
    m.add_argument("--accept-risk", action="store_true")


def _db_args(p, *, required: bool) -> None:
    p.add_argument("uri", nargs=None if required else "?",
                   help="neo4j://, neo4j+s://, bolt:// or bolt+s:// host and port")
    p.add_argument("--database", default=None, help="database name (default: neo4j)")
    p.add_argument("--user", default=None, help="database user (default: neo4j)")
    p.add_argument("--secret-ref", default=None,
                   help="keyring entry name for the password (default: vinc-database:<host>/<database>)")


def _password() -> str:
    from store.location import SECRET_ENV
    value = os.environ.get(SECRET_ENV)
    if value:
        return value
    if not sys.stdin.isatty():
        raise SystemExit(f"no password: set {SECRET_ENV} or run in a terminal to be asked")
    return getpass.getpass("Database password: ")


def _remember(secret_ref: str, password: str) -> str:
    """키링에 넣는다. 결과는 "stored" 나 사람이 할 일을 말하는 문장."""
    try:
        import keyring  # noqa: PLC0415 — 선택 의존. 없으면 환경 변수로 준다
        keyring.set_password(KEYRING_SERVICE, secret_ref, password)
        return "stored"
    except Exception as exc:   # noqa: BLE001 — 키링이 없는 기기가 있다
        from store.location import SECRET_ENV
        return (f"not stored ({type(exc).__name__}); pass the password as {SECRET_ENV} "
                "when you start `vinc serve`, or connect from the desktop app")


def keyring_secret(secret_ref: str) -> str | None:
    """`vinc serve` 의 대체 경로: 환경 변수가 없을 때 키링에서 읽는다. 못 읽으면 None."""
    try:
        import keyring  # noqa: PLC0415
        return keyring.get_password(KEYRING_SERVICE, secret_ref) or None
    except Exception:   # noqa: BLE001
        return None


def _location(args, remembered: dict | None) -> dict:
    base = dict(remembered or {})
    uri = args.uri or base.get("uri")
    if not uri:
        raise SystemExit("give the database uri (or connect once so it is remembered)")
    database = args.database or (base.get("database") if uri == base.get("uri") else None) or "neo4j"
    user = args.user or (base.get("user") if uri == base.get("uri") else None) or "neo4j"
    host = urlsplit(uri).hostname or "database"
    ref = args.secret_ref or (base.get("secret_ref") if uri == base.get("uri") else None) \
        or f"vinc-database:{host}/{database}"
    return {"kind": "database", "engine": "neo4j", "uri": uri, "database": database, "user": user,
            "secret_ref": ref}


def _sidecar():
    """이 기기에서 도는 사이드카(데스크톱 앱이 띄운 것). 없으면 None."""
    try:
        from vinc_mcp.bridge import Bridge
        probe = Bridge()
        session = probe._read_session()
        if session and session.get("token"):
            base, token = f"http://127.0.0.1:{session['port']}", session["token"]
        elif os.environ.get("VINC_SIDECAR_TOKEN"):
            # 허브 컨테이너 안(`docker exec`): 셸의 session.json 이 없고, 엔트리포인트가 띄운
            # 사이드카가 루프백 8787 에서 같은 토큰으로 돈다. 스토어는 그 프로세스가 쥐고 있다.
            port = os.environ.get("VINC_SIDECAR_PORT") or "8787"
            base, token = f"http://127.0.0.1:{port}", os.environ["VINC_SIDECAR_TOKEN"]
        else:
            return None
        bridge = Bridge(base=base, token=token)
        return bridge if bridge.health() else None
    except Exception:   # noqa: BLE001
        return None


def current_secret() -> str | None:
    """지금 저장 위치가 데이터베이스면 그 비밀번호: 환경 변수, 없으면 키링. 아니면 None."""
    from store.config import read_local_config
    from store.location import (SECRET_ENV, DatabaseLocation, LocationError, env_location,
                                env_secret, read_location)
    if os.environ.get(SECRET_ENV):
        return os.environ[SECRET_ENV]
    try:
        if env_location() is not None:
            return env_secret()          # 셀프호스트 허브: 비밀번호는 환경이나 비밀 파일에 있다
        loc = read_location(read_local_config())
    except LocationError:
        return None
    return keyring_secret(loc.secret_ref) if isinstance(loc, DatabaseLocation) else None


def _local_app():
    """사이드카가 없을 때 이 프로세스에서 같은 핸들러를 부른다. 스토어는 이동할 때만 연다."""
    from .auth import SessionAuth
    from .server import VincApp
    from store.config import app_store_config
    return VincApp(app_store_config(secret=current_secret()), store=None, auth=SessionAuth("cli"))


def run(args, emit) -> int:
    cmd = args.storagecmd
    bridge = _sidecar()
    if cmd == "status":
        out = _local_app()._config_view() if bridge is None else bridge.request("GET", "/config")
        return emit({"executed": True, "location": out.get("location"),
                     "location_error": out.get("location_error"),
                     "sidecar": None if bridge is None else {
                         "engine": out.get("engine"), "store_open": out.get("store_open"),
                         "restart_needed": out.get("restart_needed")}})
    from store import config as sc
    remembered = sc.read_local_config().get("database")
    if cmd == "connect":
        loc = _location(args, remembered)
        password = _password()
        if args.check_only:
            body = {k: loc[k] for k in ("uri", "database", "user")} | {"password": password}
            out = (bridge.request("POST", "/config/inspect-database", body=body) if bridge
                   else _local_app().write_config_inspect_database(body)[1])
            return emit(out) if out.get("ok") else (emit(out) or 1)
        body = {"location": loc, "password": password, "mode_chosen": True,
                "accept_risk": bool(args.accept_risk)}
        out = bridge.request("POST", "/config", body=body) if bridge else _local_app().write_config(body)[1]
        if out.get("error"):
            emit(out)
            return 1
        out["keyring"] = _remember(loc["secret_ref"], password)
        return emit(out)
    if cmd == "move":
        body: dict = {"accept_risk": bool(args.accept_risk)}
        if args.to == "database":
            loc = _location(args, remembered)
            body.update(to=loc, password=_password())
        else:
            body["to"] = {"kind": "folder", "path": os.path.abspath(args.path) if args.path else None}
        if bridge is not None:
            out = bridge.request("POST", "/config/move", body=body)
            job = out.get("job_id")
            while job and out.get("status") in ("queued", "running", None) and not out.get("error"):
                time.sleep(1)
                out = bridge.request("GET", f"/jobs/{job}")
            result = out.get("result") if job else out
        else:
            from store.backend import open_store
            app = _local_app()
            app.store = open_store(app.cfg)
            if app.store is None:
                from store.backend import last_open_error
                emit({"executed": False, "error": "no reachable store", "note": last_open_error()})
                return 1
            try:
                code, out = app.submit_config_move(body)
                if code == 202:
                    j = app.jobs.get(out["job_id"])
                    j._done.wait()
                    out = j.to_dict()
                    result = out.get("result")
                else:
                    result = out
            finally:
                app.close()
        if not result or result.get("error") or not result.get("switched"):
            emit(result or out)
            return 1
        if args.to == "database":
            result["keyring"] = _remember(body["to"]["secret_ref"], body["password"])
        return emit(result)
    return 2
