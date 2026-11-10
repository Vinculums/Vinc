# -*- coding: utf-8 -*-
"""그래프가 사는 곳 — 저장 위치 하나의 합 타입(`StorageLocation`).

계획 `vinc.plan.bring-your-own-database-2026-11-08` §1·B5. 세 가지뿐이다:

- `AccountLocation`  — 계정 그래프. 이 기기의 스토어는 기본 폴더(`~/.vinc/db`)다.
- `FolderLocation`   — 이 기기의 폴더. `path` 가 None 이면 기본 폴더다.
- `DatabaseLocation` — 사용자가 운영하는 Neo4j. 주소·데이터베이스·사용자·**비밀 참조**만 든다.

불가능한 상태는 만들 수 없게 한다: 경로 없는 폴더 위치는 기본 폴더이고, 데이터베이스 위치에는
비밀번호 칸이 없다. `secret_ref` 는 OS 키링 항목의 이름이고, 비밀번호 자체는 프로세스 환경
(`SECRET_ENV`)으로만 사이드카에 온다. 설정 파일·로그·응답에 비밀번호가 실리는 길은 없다.

설정은 읽을 때 **한 번** 검사한다(`read_location`·`parse_location`). 검사를 통과한 값은 안쪽에서
다시 의심하지 않는다. 파일(`~/.vinc/config.json`)은 예전 쌍(`mode`·`data_path`)에 `mode: database` 와
`database` 덩이를 더한 모양이라, 이 판보다 앞선 설정은 뜻이 바뀌지 않는다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

#: 사이드카가 데이터베이스 비밀번호를 받는 환경 변수. 셸이 키링에서 꺼내 자식 환경에만 싣고,
#: `vinc serve` 는 읽자마자 환경에서 지운다(그 아래 자식에게 물려주지 않는다).
SECRET_ENV = "VINC_DATABASE_SECRET"

#: 받는 연결 주소의 스킴. `+s` 는 검증하는 TLS, `+ssc` 는 자체 서명 인증서를 받는 TLS 다.
NEO4J_SCHEMES = ("neo4j", "neo4j+s", "neo4j+ssc", "bolt", "bolt+s", "bolt+ssc")
#: 암호화하는 스킴. 나머지는 평문이라 연결 확인이 위험으로 알린다.
ENCRYPTED_SCHEMES = ("neo4j+s", "neo4j+ssc", "bolt+s", "bolt+ssc")

# Neo4j 데이터베이스 이름 규칙(영문자로 시작, 영숫자·점·대시, 3~63자). 별칭은 받지 않는다.
_DB_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{2,62}$")
_SECRET_REF = re.compile(r"^[A-Za-z0-9._:/@\-]{1,200}$")


class LocationError(ValueError):
    """설정의 저장 위치가 틀렸다. 메시지는 사람에게 그대로 보여도 되는 문장이다(비밀 없음)."""


@dataclass(frozen=True)
class AccountLocation:
    kind = "account"

    def to_dict(self) -> dict:
        return {"kind": self.kind}


@dataclass(frozen=True)
class FolderLocation:
    path: str | None = None          # None = 기본 폴더(`~/.vinc`)
    kind = "folder"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "path": self.path}


@dataclass(frozen=True)
class DatabaseLocation:
    uri: str
    database: str
    user: str
    secret_ref: str
    engine: str = "neo4j"
    kind = "database"

    @property
    def encrypted(self) -> bool:
        return urlsplit(self.uri).scheme in ENCRYPTED_SCHEMES

    @property
    def label(self) -> str:
        """사람에게 보이는 이름 `host/db`. 주소의 사용자 정보는 싣지 않는다."""
        return f"{urlsplit(self.uri).hostname or self.uri}/{self.database}"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "engine": self.engine, "uri": self.uri,
                "database": self.database, "user": self.user, "secret_ref": self.secret_ref}


StorageLocation = AccountLocation | FolderLocation | DatabaseLocation


def _text(raw: dict, key: str) -> str:
    v = raw.get(key)
    if not isinstance(v, str) or not v.strip():
        raise LocationError(f"database location needs {key}")
    return v.strip()


def parse_database(raw: dict) -> DatabaseLocation:
    """데이터베이스 위치 하나를 검사해 만든다. 연결은 하지 않는다."""
    engine = raw.get("engine", "neo4j")
    if engine != "neo4j":
        raise LocationError(f"unsupported database engine {engine!r} (supported: neo4j)")
    if "password" in raw or "secret" in raw:
        # 비밀번호를 설정에 실으려는 시도는 받지 않는다. 받아 두고 버리면 파일에는 이미 남은 뒤다.
        raise LocationError("a database location holds a secret reference, never the password")
    uri = _text(raw, "uri")
    parts = urlsplit(uri)
    if parts.scheme not in NEO4J_SCHEMES:
        raise LocationError(f"database uri must start with one of {', '.join(NEO4J_SCHEMES)}")
    if not parts.hostname:
        raise LocationError("database uri has no host")
    if parts.username or parts.password:
        raise LocationError("database uri must not hold a user or password")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise LocationError("database uri is host and port only; the database name goes in database")
    try:
        parts.port
    except ValueError:
        raise LocationError("database uri has an invalid port") from None
    database = _text(raw, "database")
    if not _DB_NAME.match(database):
        raise LocationError("database name must start with a letter and use letters, digits, dots "
                            "or dashes (3 to 63 characters)")
    secret_ref = _text(raw, "secret_ref")
    if not _SECRET_REF.match(secret_ref):
        raise LocationError("secret_ref must be a keyring entry name")
    return DatabaseLocation(uri=uri.rstrip("/"), database=database, user=_text(raw, "user"),
                            secret_ref=secret_ref)


def parse_location(raw) -> StorageLocation:
    """`{"kind": ...}` 한 덩이를 저장 위치로. 틀리면 `LocationError`."""
    if not isinstance(raw, dict):
        raise LocationError("location must be an object with a kind")
    kind = raw.get("kind")
    if kind == "account":
        return AccountLocation()
    if kind == "folder":
        path = raw.get("path")
        if path in (None, ""):
            return FolderLocation()
        if not isinstance(path, str):
            raise LocationError("folder path must be text")
        path = os.path.expanduser(path.strip())
        if not os.path.isabs(path):
            # 사이드카의 작업 디렉터리는 띄우는 쪽마다 다르다. 상대경로는 프로세스마다 다른 폴더다.
            raise LocationError("folder path must be absolute")
        return FolderLocation(path)
    if kind == "database":
        return parse_database(raw)
    raise LocationError("location kind must be account, folder or database")


def read_location(conf: dict) -> StorageLocation | None:
    """설정 dict(`~/.vinc/config.json`)에서 저장 위치를 읽는다. 아직 아무것도 고르지 않았으면 None.

    파일 모양은 예전 쌍을 그대로 넓힌다: `mode` 가 account·local·**database** 이고, local 이면
    `data_path`(없으면 기본 폴더), database 면 `database` 덩이(엔진·주소·이름·사용자·비밀 참조)를
    읽는다. 폴더로 되돌아가도 `database` 덩이는 남는다(다시 고를 때 채워 보이려고).

    database 인데 덩이가 없거나 틀렸으면 `LocationError` 다. 기본 폴더로 물러서지 않는다:
    다른 스토어가 조용히 열리면 빈 그래프가 "데이터가 사라졌다"로 읽힌다."""
    mode = conf.get("mode")
    if mode == "account":
        return AccountLocation()
    if mode == "local":
        path = conf.get("data_path")
        if isinstance(path, str) and path.strip() and os.path.isabs(os.path.expanduser(path.strip())):
            return FolderLocation(os.path.expanduser(path.strip()))
        return FolderLocation()
    if mode == "database":
        raw = conf.get("database")
        if not isinstance(raw, dict):
            raise LocationError("storage mode is database but no database is configured")
        return parse_database(raw)
    return None


def take_secret(environ=None) -> str | None:
    """환경에서 데이터베이스 비밀번호를 꺼내고 **지운다**. 자식 프로세스가 물려받지 않게."""
    env = os.environ if environ is None else environ
    value = env.pop(SECRET_ENV, None)
    return value or None
