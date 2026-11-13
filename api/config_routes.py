# -*- coding: utf-8 -*-
"""저장 위치와 모드 전환 라우트의 본문: `GET /config`·`GET /config/inspect`·`POST /config`·
`POST /config/inspect-database`·`POST /config/move`·`POST /sync/push-all`·`POST /sync/pull-all`.

함수는 사이드카 앱(`api.server.VincApp`)을 첫 인자로 받고, 앱의 같은 이름 메서드가 그대로 넘긴다
(`_config_view` → :func:`config_view` 처럼 앱 쪽 이름은 그대로다). 앱에서 읽는 것은 설정(`cfg`)·
스토어(`store`)·고정 경로 여부(`_data_pinned`)·이동 잡 접수(`_submit_move`)이고, 본문끼리도 앱의 메서드를
거쳐 부른다. 그래서 인스턴스에서 바꿔 끼운 메서드가 원래처럼 닿는다.

데스크톱의 저장 설정은 `~/.vinc/config.json` 한 곳이다(`store.config`). 이 라우트들은 스토어 없이도 답한다:
폴더를 잘못 골라 스토어가 안 열렸을 때 다시 고를 길이 여기뿐이다.
"""
from __future__ import annotations

import os

from store import config as store_config
from store import migrate
from store.config import load_config
from store.envflag import truthy
from store.location import (AccountLocation, DatabaseLocation, FolderLocation, LocationError,
                            env_location, parse_database, parse_location, read_location)
from store.neo4j_backend import BLOCKING_RISKS

from . import localmode
from . import sync


def _check_database(body: dict) -> tuple[dict | None, dict | None]:
    """연결 확인 한 번. (확인 결과, None) 아니면 (None, 400 바디). 비밀번호는 결과에 없다."""
    try:
        loc = parse_database({**{k: body.get(k) for k in ("uri", "database", "user")},
                              "secret_ref": "check"})
    except LocationError as exc:
        return None, {"error": "bad_location", "note": str(exc)}
    password = body.get("password")
    if not isinstance(password, str) or not password:
        return None, {"error": "missing field: password"}
    from store.neo4j_backend import inspect_database
    return inspect_database(loc.uri, user=loc.user, password=password, database=loc.database), None


#: 고정 경로로 뜬 프로세스가 위치를 바꾸는 요청을 거절할 때의 문장. 화면과 스크립트가 이 문장을 그대로 본다.
_PINNED_NOTE = "this process was started with a fixed data path (--data or MEMORY_DATA_DIR)"


def pinned(extra: str = "") -> tuple[int, dict]:
    """409 `pinned`. `extra` 는 문장 끝에 그대로 붙는다(이동은 무엇을 할 수 있는지 덧붙인다)."""
    return 409, {"error": "pinned", "note": _PINNED_NOTE + extra}


def database_gate(check: dict, body: dict, *, writer_lease: bool = False) -> tuple[int, dict] | None:
    """`_check_database` 의 확인 결과로 이 데이터베이스를 받을지 가른다. 받으면 None, 아니면 (코드, 바디).

    순서가 답이다: 연결 실패(400) → 막는 위험(400) → [writer_lease 면] 다른 Vinc 가 쓰는 중(409) →
    확인이 필요한 위험인데 `accept_risk` 가 없음(409)."""
    if not check["ok"]:
        return 400, {"error": check["problem"], "check": check}
    blocking = [r for r in check["risks"] if r in BLOCKING_RISKS]
    if blocking:
        return 400, {"error": blocking[0], "check": check}
    if writer_lease and (check.get("writer_lease") or {}).get("active"):
        return 409, {"error": "another_writer", "check": check,
                     "note": "another Vinc process is writing to this database"}
    if check["risks"] and not truthy(body.get("accept_risk")):
        return 409, {"error": "risky_database", "check": check}
    return None


def config_view(app) -> dict:
    home = store_config.vinc_home()
    conf = store_config.read_local_config(home)
    cur = getattr(app.cfg, "data_dir", None)
    pinned = app._data_pinned()
    want = store_config.resolve_data_dir(create=False)
    mode = conf.get("mode")
    location, location_error, source = None, None, "settings"
    try:
        # 환경이 데이터베이스를 말하면(셀프호스트 허브) 그것이 이 프로세스의 저장 위치다.
        location = env_location()
        if location is not None:
            source = "environment"
        else:
            location = read_location(conf)
    except LocationError as exc:
        location_error = str(exc)
    opened_db = (getattr(app.cfg, "backend", "") == "neo4j"
                 and dict(getattr(app.cfg, "database", None) or {}))
    if pinned:
        restart = False
    elif isinstance(location, DatabaseLocation):
        want_db = {"uri": location.uri, "database": location.database, "user": location.user}
        restart = opened_db != want_db
    elif opened_db:
        restart = True                  # 데이터베이스를 열고 있는데 설정은 폴더·계정이다
    else:
        # 설정이 가리키는 파일과 지금 연 파일이 다르다 = 셸이 사이드카를 다시 띄워야 한다.
        restart = bool(cur) and os.path.abspath(want) != os.path.abspath(cur)
    return {
        "executed": True,
        # 아직 안 정했으면 None 이다. 앱이 첫 기동에 정해 적는다(기존 설치의 데이터를
        # 보고). 여기서 "account" 로 채우면 앱은 "이미 정했다"로 읽고 그 판단을 건너뛴다.
        # 데이터베이스 위치도 "local" 로 알린다: 앱의 모드는 요청을 어디로 보낼지(계정 허브냐 이 기기의
        # 사이드카냐)이고, 데이터베이스는 사이드카가 연다. 어디에 사는지는 `location` 이 말한다.
        "mode": "local" if mode == "database" else mode if mode in ("account", "local") else None,
        # 사람이 고른 모드인가. 앱이 자동으로 정한 값과 가른다: 0.9.4·0.9.5 는
        # 이 기기에 데이터가 있으면 말없이 로컬로 정했고, 그 설치는 한 번 물어야 한다.
        # 제거 조건: 없음(키가 없으면 '고르지 않음'인 규칙은 새 설치도 쓴다). 위 0.9.4·0.9.5 문장만 그 판의 업그레이드를 더 받지 않을 때 뺀다.
        "mode_chosen": conf.get("mode_chosen") is True,
        "data_path": conf.get("data_path") or None,
        "data_file": cur,
        "default_folder": str(home),
        "suggested_folder": localmode.suggested_folder(home),
        "pinned": pinned,
        "restart_needed": restart,
        "store_open": app.store is not None,
        # 저장 위치(`store.location`). 비밀번호는 어디에도 없다 — secret_ref 는 키링 항목의 이름이다.
        # 설정이 틀렸으면 location 은 None 이고 location_error 가 사람용 문장이다.
        "location": location.to_dict() if location is not None else None,
        "location_error": location_error,
        # 위치를 어디서 읽었나: 설정 파일(settings)이나 프로세스 환경(environment). 환경이면 화면에서
        # 바꿔도 다음 기동에 환경이 이긴다.
        "location_source": source,
        # 지금 연 스토어의 엔진(embedded·neo4j). 스토어가 없으면 None.
        "engine": getattr(app.store, "driver_name", None) if app.store is not None else None,
    }


def read_config(app, p):
    """저장 설정과 지금 연 스토어. 설정 화면의 데이터원."""
    return 200, app._config_view()


def read_config_inspect(app, p):
    """폴더 하나를 받기 전에 살핀다(`api.localmode.inspect_folder`). 아무것도 안 쓴다."""
    return 200, {"executed": True, **localmode.inspect_folder(str(p.get("path") or ""))}


def write_config_inspect_database(app, body):
    """사용자 데이터베이스 하나를 고르기 전에 살핀다(`store.neo4j_backend.inspect_database`).

    바디: `uri`·`database`·`user`·`password`. 연결해 보고 아무것도 남기지 않는다(임대도 안 집는다).
    주소·이름이 틀렸으면 400 이고, 연결 실패·비밀번호 틀림은 200 의 `ok: false` 와 `problem` 이다
    (화면이 그 문장을 그린다). 비밀번호는 이 호출 안에서만 쓰고 버린다."""
    check, err = _check_database(body)
    if err:
        return 400, err
    return 200, {"executed": True, **check}


def write_config(app, body):
    """저장 설정을 바꾼다: `mode`(account|local) · `mode_chosen`(사람이 골랐다) ·
    `data_path`(폴더, 빈 값 = 기본).

    폴더는 받기 전에 살핀다. 쓸 수 없으면 400, 동기화 폴더·네트워크 드라이브면
    `accept_risk` 없이 409 다(사람이 경고를 보고 한 번 더 골라야 받는다). 폴더는
    **만들기만 한다**: 안에 있던 것은 지우지도 덮지도 않는다. 이미 스토어(`db`)가
    있으면 다음 기동이 그것을 연다.

    설정만 쓴다. 새 폴더로 옮기는 것은 셸의 재기동이고(`restart_needed`), 데이터를
    나르는 것은 `/sync/pull-all`·`/sync/push-all` 이다."""
    updates: dict = {}
    info = None
    check = None
    if "location" in body:
        # 저장 위치 한 덩이(`store.location`). 예전 키(mode·data_path)와 섞어 보내지 않는다.
        if any(k in body for k in ("mode", "data_path")):
            return 400, {"error": "send location alone, not with mode or data_path"}
        try:
            loc = parse_location(body["location"])
        except LocationError as exc:
            return 400, {"error": "bad_location", "note": str(exc)}
        if isinstance(loc, DatabaseLocation):
            if app._data_pinned():
                return pinned()
            check, err = _check_database({**loc.to_dict(), "password": body.get("password")})
            if err:
                return 400, err
            gate = database_gate(check, body)
            if gate:
                return gate
            d = loc.to_dict()
            d.pop("kind")
            updates.update({"mode": "database", "database": d})
        elif isinstance(loc, AccountLocation):
            updates["mode"] = "account"
        else:
            # 폴더는 예전 길(mode local + data_path)로 적는다. 폴더 검사도 그 길이 한다.
            body = {**body, "mode": "local", "data_path": loc.path or ""}
    if "mode" in body:
        mode = body.get("mode")
        if mode not in ("account", "local"):
            return 400, {"error": "mode must be 'account' or 'local'"}
        updates["mode"] = mode
    if "mode_chosen" in body:
        if not isinstance(body.get("mode_chosen"), bool):
            return 400, {"error": "mode_chosen must be true or false"}
        updates["mode_chosen"] = body["mode_chosen"]
    if "data_path" in body:
        if app._data_pinned():
            return pinned()
        raw = body.get("data_path")
        if raw in (None, ""):
            updates["data_path"] = None
        else:
            info = localmode.inspect_folder(str(raw))
            if not info["ok"]:
                return 400, {"error": info["problem"], "inspect": info}
            if info["risks"] and not truthy(body.get("accept_risk")):
                return 409, {"error": "risky_folder", "inspect": info}
            try:
                os.makedirs(info["path"], exist_ok=True)
            except OSError as exc:
                return 400, {"error": "not_writable", "inspect": info,
                             "note": f"{type(exc).__name__}: {exc}"}
            home = store_config.vinc_home()
            same = os.path.normcase(os.path.abspath(info["path"])) \
                == os.path.normcase(os.path.abspath(str(home)))
            # 기본 폴더를 고른 것은 설정이 없는 것과 같다. 적어 두면 홈을 옮겼을 때
            # 더는 없는 경로를 가리키는 설정만 남는다.
            updates["data_path"] = None if same else info["path"]
    if not updates:
        return 400, {"error": "nothing to change (mode, mode_chosen, data_path)"}
    store_config.write_local_config(updates)
    view = app._config_view()
    if info is not None:
        view["inspect"] = info
    if check is not None:
        view["check"] = check
    return 200, view


def submit_config_move(app, body):
    """그래프를 다른 저장 위치로 옮긴다(잡, 202). 바디:

    - `to`: 저장 위치(`store.location`). 데이터베이스나 폴더. 데이터베이스면 `password` 와(위험이
      있으면) `accept_risk`, 폴더면 위험이 있을 때 `accept_risk`.
    - `from`: 생략하면 이 사이드카가 연 스토어. `"account"` 면 계정 그래프(`token` 필요)를
      데이터베이스로 받는다.

    원본에는 아무것도 쓰지 않는다. 대상에 원본의 키가 전부 있을 때만 설정을 그 위치로 바꾸고
    (`switched`), 하나라도 빠지면 바꾸지 않고 `missing` 을 말한다. 같은 이동을 다시 돌리면 같은
    상태로 수렴한다(MERGE). 바뀐 위치는 셸이 사이드카를 다시 띄울 때 먹는다(`restart_needed`).

    ⚠ 잡은 쓰기 큐에서 돈다: 옮기는 동안 다른 쓰기는 기다리고, 원본은 그 사이 변하지 않는다.
    옮긴 뒤 다시 띄우기 전에 들어온 쓰기는 이전 위치에 남으므로 셸은 곧바로 다시 띄운다."""
    try:
        to = parse_location(body.get("to"))
    except LocationError as exc:
        return 400, {"error": "bad_location", "note": str(exc)}
    # 고정 경로로 뜬 프로세스(허브)는 위치를 설정 파일이 아니라 환경이 정한다. 그래서 이 스토어를
    # 데이터베이스로 **복사만** 한다: 다 왔는지 확인하고 설정은 쓰지 않으며, 바꾸는 것은 사람이
    # 환경(VINC_DATABASE_*)을 넣고 다시 띄우는 일이다. 그 밖의 이동은 거절한다.
    copy_only = app._data_pinned()
    if copy_only and not (isinstance(to, DatabaseLocation) and body.get("from") is None):
        return pinned("; it can only copy its graph to a database")
    source = body.get("from")
    if source not in (None, "account"):
        return 400, {"error": "from must be omitted (this store) or 'account'"}
    if source == "account" and not isinstance(to, DatabaseLocation):
        return 400, {"error": "from account moves only to a database location"}
    if source == "account" and not body.get("token"):
        return 400, {"error": "missing field: token (member key)"}
    hub = None
    if source == "account":
        # 계정에서 옮기기는 허브의 실행 상태를 그대로 믿는다(`pull_everything`). 받을 곳은 기기의 설정이 정한다.
        hub, refused = sync.trusted_hub(body.get("base"))
        if refused:
            return 400, refused
    if isinstance(to, DatabaseLocation):
        check, err = _check_database({**to.to_dict(), "password": body.get("password")})
        if err:
            return 400, err
        gate = database_gate(check, body, writer_lease=True)
        if gate:
            return gate
        password = body["password"]
        run = lambda: app._move_to_database(to, password, source=source, body=body,  # noqa: E731
                                            copy_only=copy_only, hub=hub)
    elif isinstance(to, FolderLocation):
        info = localmode.inspect_folder(to.path or str(store_config.vinc_home()))
        if not info["ok"]:
            return 400, {"error": info["problem"], "inspect": info}
        target = os.path.join(info["path"], "db")
        cur = getattr(app.cfg, "data_dir", None)
        if cur and os.path.normcase(os.path.abspath(cur)) == os.path.normcase(os.path.abspath(target)):
            return 409, {"error": "same_location", "inspect": info}
        if info["has_store"]:
            # 폴더에 이미 그래프가 있으면 옮겨 붓지 않는다. 그것을 열려면 위치만 바꾼다(`POST /config`).
            return 409, {"error": "folder_has_store", "inspect": info}
        if info["risks"] and not truthy(body.get("accept_risk")):
            return 409, {"error": "risky_folder", "inspect": info}
        run = lambda: app._move_to_folder(to, info, target)  # noqa: E731
    else:
        return 400, {"error": "move to the account with /sync/push-all"}
    if source is None and app.store is None:
        from .server import store_unavailable   # server 가 이 모듈을 맨 위에서 들이므로 부를 때 가져온다
        return 503, {"executed": False, **store_unavailable()}
    return app._submit_move("config:move", run)


def switch_to(app, updates: dict) -> dict:
    store_config.write_local_config({**updates, "mode_chosen": True})
    return app._config_view()


def move_to_database(app, to, password: str, *, source, body: dict,
                     copy_only: bool = False, hub: str | None = None) -> dict:
    from store.neo4j_backend import Neo4jBackend, installation_holder
    target = Neo4jBackend(to.uri, user=to.user, password=password, database=to.database,
                          holder=installation_holder(store_config.installation_id()))
    try:
        if target.read_only:
            return {"executed": False, "switched": False, "error": "another_writer",
                    "note": target.read_only_reason}
        if source == "account":
            target.provision(dim=app.cfg.embedding_dim)
            owner = body.get("owner") or "tenant:local"
            rep = sync.pull_everything(target, app.cfg, owner=owner,
                                       remote=sync.RemoteHTTP(body["token"],
                                                              hub or sync.configured_hub()))
        else:
            rep = migrate.copy_store(app.store, target, dim=app.cfg.embedding_dim)
    finally:
        target.close()
    d = to.to_dict()
    d.pop("kind")
    ok = int(rep.get("missing") or 0) == 0
    if copy_only:
        return {"executed": True, "switched": False, "copied": ok, "copy": rep,
                "next": ("set VINC_DATABASE_URI, VINC_DATABASE_NAME, VINC_DATABASE_USER and "
                         "VINC_DATABASE_SECRET for this database and restart the hub") if ok else None}
    view = app._switch_to({"mode": "database", "database": d}) if ok else None
    return {"executed": True, "switched": ok, "copy": rep, "config": view}


def move_to_folder(app, to, info: dict, target_file: str) -> dict:
    from store.backend import open_store
    os.makedirs(info["path"], exist_ok=True)
    dst = open_store(load_config({"data_dir": target_file}))
    if dst is None:
        from store.backend import last_open_error
        return {"executed": False, "switched": False, "error": "folder_store_unavailable",
                "note": last_open_error()}
    try:
        rep = migrate.copy_store(app.store, dst, dim=app.cfg.embedding_dim)
    finally:
        dst.close()
    home = store_config.vinc_home()
    same = os.path.normcase(os.path.abspath(info["path"])) == os.path.normcase(os.path.abspath(str(home)))
    ok = int(rep.get("missing") or 0) == 0
    view = app._switch_to({"mode": "local", "data_path": None if same else info["path"]}) if ok else None
    return {"executed": True, "switched": ok, "copy": rep, "config": view}


def submit_sync_push_all(app, body):
    """로컬 → 계정: 이 기기의 모든 주제를 계정으로(`sync.push_everything`). 202+job.

    이 스토어에는 아무것도 안 쓴다. 결과의 `missing` 이 0 이 아니면 앱은 모드를
    바꾸지 않는다."""
    token = body.get("token")
    if not token:
        return 400, {"error": "missing field: token (member key)"}
    owners = body.get("owners") or ["tenant:local", "user:local"]
    if not isinstance(owners, list) or not all(isinstance(o, str) and o for o in owners):
        return 400, {"error": "owners must be a list of owner ids"}
    # 전부 올리기는 회원 키와 그래프 전부를 싣는다. 보낼 곳은 본문이 아니라 기기의 설정이 정한다.
    hub, refused = sync.trusted_hub(body.get("base"), upload=True)
    if refused:
        return 400, refused
    remote = sync.RemoteHTTP(token, hub)
    return app._submit_move("sync:push-all", lambda: sync.push_everything(
        app.store, app.cfg, owners=owners, remote=remote))


def submit_sync_pull_all(app, body):
    """계정 → 로컬: 계정 그래프 전부를 이 스토어로(`sync.pull_everything`). 202+job."""
    token = body.get("token")
    if not token:
        return 400, {"error": "missing field: token (member key)"}
    owner = body.get("owner") or "tenant:local"
    # 계정 전부 받기는 허브의 실행 상태를 그대로 믿는다. 받을 곳은 기기의 설정이 정한다.
    hub, refused = sync.trusted_hub(body.get("base"))
    if refused:
        return 400, refused
    remote = sync.RemoteHTTP(token, hub)
    return app._submit_move("sync:pull-all", lambda: sync.pull_everything(
        app.store, app.cfg, owner=owner, remote=remote))
