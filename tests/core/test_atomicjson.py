# -*- coding: utf-8 -*-
"""`store.atomicjson.write_json_0600`: JSON 파일 하나를 반쪽 없이 바꿔 쓰는 단 하나의 함수.

잠그는 것:
- 쓴다: UTF-8 로 `json.dump` 한 그대로(끝 줄바꿈 없음, 비ASCII 는 이스케이프)이고 모드는 0600 이다.
- 바꾼다: 이미 있는 파일(0644, 더 긴 내용)을 통째로 바꾸고 모드도 0600 이 된다.
- 쓰다 실패해도 반쪽이 안 남는다: 직렬화가 중간에 죽어도, 옮기기(`os.replace`)가 죽어도 목적지는
  이전 내용 그대로이고 임시 파일은 지워진다.
- `store/` 맨 아래 층 규칙: 표준 라이브러리만 가져온다. 셀프호스트 모듈에는 둘째 사본이 없다.
"""
from __future__ import annotations

import ast
import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from store import atomicjson  # noqa: E402
from store.atomicjson import write_json_0600  # noqa: E402

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX 파일 모드")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def umask():
    """테스트 안에서 umask 를 바꾸고 돌려놓는다."""
    saved = os.umask(0o022)
    os.umask(saved)

    def setter(value: int) -> None:
        os.umask(value)

    yield setter
    os.umask(saved)


PAYLOADS = [
    {},
    {"a": 1, "b": [1, 2, 3], "c": {"d": None, "e": True, "f": 1.5}},
    {"name": "홍길동", "note": "셀프호스트 좌석 — 한 사람"},
    {"s": "\U0001F600 line1\nline2\t\"quoted\" \\ back   \x00"},
    {"big": 10 ** 30, "list": list(range(500))},
]


@pytest.mark.parametrize("payload", PAYLOADS, ids=range(len(PAYLOADS)))
def test_json_dump_와_같은_바이트를_쓴다(tmp_path, payload):
    target = tmp_path / "state.json"
    write_json_0600(str(target), payload)
    raw = target.read_bytes()
    assert raw == json.dumps(payload).encode("utf-8")
    assert not raw.endswith(b"\n") and b"\r" not in raw
    assert json.loads(raw.decode("utf-8")) == payload


@posix_only
@pytest.mark.parametrize("mask", [0o022, 0o077, 0o000])
def test_새_파일의_모드는_umask_와_무관하게_0600(tmp_path, umask, mask):
    umask(mask)
    target = tmp_path / "state.json"
    write_json_0600(str(target), {"k": "v"})
    assert _mode(target) == 0o600


@posix_only
def test_있던_파일을_통째로_바꾸고_모드도_0600_이_된다(tmp_path):
    target = tmp_path / "state.json"
    target.write_text('{"old": "content that is much longer than the replacement"}', encoding="utf-8")
    os.chmod(target, 0o644)
    write_json_0600(str(target), {"n": 1})
    assert target.read_bytes() == b'{"n": 1}'
    assert _mode(target) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_직렬화가_중간에_죽으면_목적지는_그대로이고_임시_파일은_없다(tmp_path):
    target = tmp_path / "state.json"
    write_json_0600(str(target), {"keep": True})
    before = target.read_bytes()
    # 앞부분은 이미 임시 파일에 쓰인 뒤에 직렬화할 수 없는 값을 만난다.
    with pytest.raises(TypeError):
        write_json_0600(str(target), {"head": "x" * 100_000, "tail": object()})
    assert target.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_목적지가_없던_자리에서_죽으면_아무_파일도_안_생긴다(tmp_path):
    target = tmp_path / "state.json"
    with pytest.raises(TypeError):
        write_json_0600(str(target), {"head": "x" * 100_000, "tail": object()})
    assert list(tmp_path.iterdir()) == []


def test_옮기기가_죽어도_목적지는_그대로이고_임시_파일은_없다(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    write_json_0600(str(target), {"keep": True})
    before = target.read_bytes()

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(atomicjson.os, "replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        write_json_0600(str(target), {"new": True})
    assert target.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_없는_디렉터리면_파일시스템_오류를_그대로_올린다(tmp_path):
    with pytest.raises(OSError):
        write_json_0600(str(tmp_path / "missing" / "state.json"), {})


def test_표준_라이브러리만_가져온다():
    tree = ast.parse((ROOT / "store" / "atomicjson.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names <= {"__future__", "json", "os"}


@pytest.mark.parametrize("path", sorted((ROOT / "vinc_mcp").glob("selfhost*.py")), ids=lambda p: p.name)
def test_셀프호스트_모듈에는_둘째_쓰기_사본이_없다(path):
    hits = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        if re.search(r"\bos\.replace\(|\bos\.fdopen\(|\.tmp\b", code):
            hits.append(f"{path.name}:{i}: {line.strip()}")
    assert hits == []
