# -*- coding: utf-8 -*-
"""허브의 저장 위치는 환경이 말한다(`store.location.env_location`) — 계획 S5.

잠그는 것:
  ① `VINC_DATABASE_URI` 가 없으면 위치는 지금처럼 폴더다(`--data`).
  ② 있으면 설정 파일과 같은 검사(`parse_database`)를 거친 Neo4j 위치이고, `--data` 보다 이긴다.
     틀린 값은 폴더로 물러서지 않고 스토어를 열지 않는다(`open_blocked`).
  ③ 이 기계 밖으로 가는 평문 연결은 `VINC_DATABASE_ACCEPT_PLAINTEXT` 없이는 거절한다.
  ④ 비밀번호는 `VINC_DATABASE_SECRET` 이나 `VINC_DATABASE_SECRET_FILE` 의 첫 줄이다.
  ⑤ `vinc serve` 를 환경만으로 띄우면 Neo4j 를 열고 쓰고 읽는다(CI 의 Neo4j 가 있을 때만).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from store import config as sc  # noqa: E402
from store.location import (ENV_SECRET_REF, LocationError, env_location,  # noqa: E402
                            env_secret)

_KEYS = ("VINC_DATABASE_URI", "VINC_DATABASE_NAME", "VINC_DATABASE_USER", "VINC_DATABASE_SECRET",
         "VINC_DATABASE_SECRET_FILE", "VINC_DATABASE_ACCEPT_PLAINTEXT", "MEMORY_DATA_DIR")


@pytest.fixture()
def clean_env():
    saved = {k: os.environ.pop(k) for k in _KEYS if k in os.environ}
    yield os.environ
    for k in _KEYS:
        os.environ.pop(k, None)
    os.environ.update(saved)


def test_주소가_없으면_폴더다(clean_env):
    assert env_location({}) is None
    home = Path(tempfile.mkdtemp()) / ".vinc"
    folder = Path(tempfile.mkdtemp()) / "db"
    with mock.patch.object(sc, "vinc_home", lambda: home):
        cfg = sc.app_store_config(str(folder), home=home)
    assert cfg.backend != "neo4j" and cfg.data_dir and cfg.data_dir.endswith("db")


def test_주소가_있으면_검사한_Neo4j_위치다():
    loc = env_location({"VINC_DATABASE_URI": "neo4j+s://db.example.com:7687/"})
    assert (loc.uri, loc.database, loc.user, loc.secret_ref) == (
        "neo4j+s://db.example.com:7687", "neo4j", "neo4j", ENV_SECRET_REF)
    loc = env_location({"VINC_DATABASE_URI": "bolt://127.0.0.1:7687", "VINC_DATABASE_NAME": "vinc",
                        "VINC_DATABASE_USER": "me"})
    assert (loc.database, loc.user) == ("vinc", "me")      # 이 기계의 평문은 받는다


@pytest.mark.parametrize("env, says", [
    ({"VINC_DATABASE_URI": "http://db.example.com"}, "must start with"),
    ({"VINC_DATABASE_URI": "neo4j+s://u:p@db.example.com"}, "user or password"),
    ({"VINC_DATABASE_URI": "neo4j+s://db.example.com/vinc"}, "host and port only"),
    ({"VINC_DATABASE_URI": "neo4j+s://db.example.com", "VINC_DATABASE_NAME": "1x"}, "database name"),
    ({"VINC_DATABASE_URI": "bolt://db.example.com:7687"}, "VINC_DATABASE_ACCEPT_PLAINTEXT"),
])
def test_틀린_값은_사유를_말하고_거절한다(env, says):
    with pytest.raises(LocationError, match=says):
        env_location(env)


def test_평문은_명시로만_받는다():
    loc = env_location({"VINC_DATABASE_URI": "bolt://db.example.com:7687",
                        "VINC_DATABASE_ACCEPT_PLAINTEXT": "on"})
    assert loc.encrypted is False


def test_비밀번호는_환경이나_비밀_파일이다(tmp_path):
    assert env_secret({"VINC_DATABASE_SECRET": "s1"}) == "s1"
    f = tmp_path / "pw"
    f.write_text("s2\nignored\n", encoding="utf-8")
    assert env_secret({"VINC_DATABASE_SECRET_FILE": str(f)}) == "s2"
    assert env_secret({}) is None
    with pytest.raises(LocationError, match="cannot read"):
        env_secret({"VINC_DATABASE_SECRET_FILE": str(tmp_path / "missing")})


def test_환경의_데이터베이스가_data_를_이기고_틀리면_열지_않는다(clean_env, tmp_path):
    home = tmp_path / ".vinc"
    f = tmp_path / "pw"
    f.write_text("from-file\n", encoding="utf-8")
    clean_env.update({"VINC_DATABASE_URI": "neo4j+s://db.example.com",
                      "VINC_DATABASE_SECRET_FILE": str(f)})
    with mock.patch.object(sc, "vinc_home", lambda: home):
        cfg = sc.app_store_config(str(tmp_path / "db"), home=home)
        assert cfg.backend == "neo4j" and cfg.data_dir is None
        assert cfg.database == {"uri": "neo4j+s://db.example.com", "database": "neo4j", "user": "neo4j"}
        assert cfg.database_secret == "from-file"
        assert "from-file" not in repr(cfg)
        clean_env["VINC_DATABASE_URI"] = "bolt://db.example.com"
        blocked = sc.app_store_config(str(tmp_path / "db"), home=home)
    assert "VINC_DATABASE_ACCEPT_PLAINTEXT" in blocked.extra["open_blocked"]
    assert blocked.data_dir is None


def test_CLI_의_비밀번호도_환경_위치를_따른다(clean_env, tmp_path):
    from api.cli_storage import current_secret
    f = tmp_path / "pw"
    f.write_text("p\n", encoding="utf-8")
    clean_env.update({"VINC_DATABASE_URI": "neo4j+s://db.example.com",
                      "VINC_DATABASE_SECRET_FILE": str(f)})
    assert current_secret() == "p"


URI = os.environ.get("VINC_CONFORMANCE_NEO4J_URI")


@pytest.mark.skipif(not URI, reason="VINC_CONFORMANCE_NEO4J_URI 없음")
def test_neo4j_환경만으로_사이드카가_열고_쓰고_읽는다(tmp_path):
    """허브 엔트리포인트와 같은 모양으로 띄운다: 토큰과 환경, 그리고 예전처럼 `--data` 도 준다
    (환경의 데이터베이스가 이겨야 한다). ⚠ 그 데이터베이스에 노드 하나를 남긴다."""
    pw = tmp_path / "pw"
    pw.write_text(os.environ["VINC_CONFORMANCE_NEO4J_PASSWORD"] + "\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("MEMORY_DATA_DIR", "VINC_DATABASE_SECRET", "PYTHONHOME", "PYTHONPATH")}
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), VINC_DATABASE_URI=URI,
               VINC_DATABASE_USER=os.environ.get("VINC_CONFORMANCE_NEO4J_USER", "neo4j"),
               VINC_DATABASE_SECRET_FILE=str(pw))
    proc = subprocess.Popen([sys.executable, "-m", "api.cli", "serve", "--port", "0", "--token", "T",
                             "--data", str(tmp_path / "db")], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        info = json.loads(proc.stdout.readline())
        base = f"http://127.0.0.1:{info['port']}"

        def call(method, path, body=None):
            req = urllib.request.Request(base + path, method=method,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Authorization": "Bearer T",
                                                  "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())

        view = call("GET", "/config")
        assert view["engine"] == "neo4j" and view["location_source"] == "environment", view
        assert view["location"]["kind"] == "database" and view["location"]["uri"] == URI
        call("POST", "/ingest", {"owner": "tenant:local", "nodes": [
            {"id": "concept:hub-env-location", "type": "concept", "title": "Hub env location",
             "domain": "vinc/test"}]})
        got = call("GET", "/node?node_id=concept:hub-env-location&owner=tenant:local")
        assert "Hub env location" in json.dumps(got), got
        assert not (tmp_path / "db").exists()          # 폴더 스토어는 만들지 않았다
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_컨테이너_안의_CLI_는_엔트리포인트의_사이드카로_간다(monkeypatch):
    """`docker exec … vinc storage move` 는 셸의 session.json 이 없다. 스토어는 엔트리포인트가 띄운
    사이드카가 쥐고 있으니 이 프로세스에서 열면 안 된다: 루프백 8787 과 그 토큰으로 간다."""
    from api import cli_storage
    from vinc_mcp import bridge as vb
    seen = []

    class _Bridge:
        def __init__(self, base=None, token=None, **_):
            self.base, self.token = base, token
            seen.append((base, token))

        def _read_session(self):
            return None

        def health(self):
            return self.token == "T"

    monkeypatch.setattr(vb, "Bridge", _Bridge)
    monkeypatch.setenv("VINC_SIDECAR_TOKEN", "T")
    monkeypatch.delenv("VINC_SIDECAR_PORT", raising=False)
    got = cli_storage._sidecar()
    assert got is not None and (got.base, got.token) == ("http://127.0.0.1:8787", "T")
    monkeypatch.delenv("VINC_SIDECAR_TOKEN")
    assert cli_storage._sidecar() is None


def test_차원_재구축은_데이터베이스_위치에서_건너뛴다(tmp_path):
    """엔트리포인트는 사이드카 앞에서 늘 `migrate-dim --data` 를 부른다. 데이터베이스 위치면 폴더를
    만들지도 열지도 않고 0 으로 끝나야 기동이 이어진다."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
    env.update(HOME=str(tmp_path), VINC_DATABASE_URI="neo4j+s://db.example.com")
    p = subprocess.run([sys.executable, "-m", "api.cli", "migrate-dim", "--data", str(tmp_path / "db")],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and "skipped" in p.stderr, p.stderr[-500:]
    assert not (tmp_path / "db").exists()


def test_docker_exec_는_볼륨의_토큰_파일로_사이드카를_찾는다(monkeypatch, tmp_path):
    """`docker exec` 는 엔트리포인트가 export 한 토큰을 물려받지 않는다. 셀프호스트 허브는 첫 기동 때
    토큰을 볼륨에 두므로 그 파일로 찾는다. 못 찾으면 기본 경로의 빈 스토어를 옮겨 버린다."""
    from api import cli_storage
    from vinc_mcp import bridge as vb

    class _Bridge:
        def __init__(self, base=None, token=None, **_):
            self.base, self.token = base, token

        def _read_session(self):
            return None

        def health(self):
            return self.token == "FILE"

    (tmp_path / "sidecar-token").write_text("FILE\n", encoding="utf-8")
    monkeypatch.setattr(vb, "Bridge", _Bridge)
    monkeypatch.delenv("VINC_SIDECAR_TOKEN", raising=False)
    monkeypatch.delenv("VINC_SIDECAR_PORT", raising=False)
    monkeypatch.setenv("VINC_SELFHOST_STATE", str(tmp_path))
    got = cli_storage._sidecar()
    assert got is not None and (got.base, got.token) == ("http://127.0.0.1:8787", "FILE")


def test_셀프호스트_허브에서_사이드카가_없으면_옮기지_않는다(monkeypatch, tmp_path):
    from api import cli_storage
    monkeypatch.setattr(cli_storage, "_sidecar", lambda: None)
    monkeypatch.setenv("VINC_SELF_HOSTED", "on")
    monkeypatch.setenv("HOME", str(tmp_path))
    out = []
    args = type("A", (), {"storagecmd": "move", "to": "folder", "path": str(tmp_path / "x"),
                          "accept_risk": False, "uri": None, "database": None, "user": None,
                          "secret_ref": None})()
    assert cli_storage.run(args, lambda o: out.append(o) or 0) == 1
    assert out[-1]["error"] == "hub_sidecar_unreachable"
    assert not (tmp_path / ".vinc" / "config.json").exists()
