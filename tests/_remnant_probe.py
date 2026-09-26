# -*- coding: utf-8 -*-
"""이 엔진이 색인 잔해를 **만들기는 하는가** — 잔해 테스트 두 파일이 같이 쓴다.

잔해는 색인 드롭이 WAL 에만 있는 채 프로세스가 죽고, 다음 기동의 WAL 재생이 확장보다
먼저 돌아 생겼다(ladybug 0.18.0 실측). 0.20.4 에서는 같은 자리에서 죽어도 잔해가 남지
않는다 — 재생 뒤 색인 생성이 성공하고 그 표의 쓰기도 멀쩡하다(2026-09-27 실측: FTS·
`ChunkVec`·`NodeVec` 셋 다). 그래서 잔해를 **전제로 하는** 테스트는 잔해가 실제로 생기는
엔진에서만 돈다. 수리 코드는 그대로 둔다: 0.18.x 가 이미 남긴 잔해를 가진 파일이 있을 수
있고, 그 파일을 0.20.4 가 연다.

판정은 엔진 판마다 한 번이다(결정론적이다). 자식 프로세스가 잔해를 만들고, 부모가 열어서
색인을 세워 본다 — 잔해 표에는 쓰지 않는다(0.18.x 에서는 세그폴트다).
근거: [[vinc.ops.ladybug-upgrade-review-2026-09-24]] §2.6
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_CACHE: dict[str, bool] = {}


def _crash(child: str, *args: str) -> str:
    d = os.path.join(tempfile.mkdtemp(), "store")
    r = subprocess.run([sys.executable, "-c", child, str(ROOT), d, *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"잔해 판정용 자식이 실패했다: {r.stderr[-1500:]}")
    return d


def _open(d: str):
    from store.backend import open_store
    from store.config import load_config
    s = open_store(load_config({"data_dir": d}))
    s.provision(dim=4)
    return s


def leaves_remnant(kind: str, child: str, *args: str) -> bool:
    """``kind`` 은 ``fts``·``vector``·``node`` 중 하나, ``child`` 는 그 파일의 크래시 자식."""
    if kind in _CACHE:
        return _CACHE[kind]
    from store.embedded_backend import IndexRemnant
    s = _open(_crash(child, *args))
    try:
        if kind == "node":
            found = "not loaded yet" in (s.create_node_vector_index() or "")
        else:
            try:
                s.ensure_search_indexes(dim=4 if kind == "vector" else None)
                found = False
            except IndexRemnant:
                found = True
    finally:
        s.close()
    _CACHE[kind] = found
    return found


def skip_reason() -> str:
    try:
        import ladybug
        ver = getattr(ladybug, "__version__", "?")
    except ImportError:
        ver = "?"
    return (f"ladybug {ver} 는 드롭 뒤 체크포인트 없이 죽어도 잔해를 남기지 않는다 — "
            f"잔해 경로는 0.18.x 가 남긴 파일에만 해당한다(tests/_remnant_probe.py)")


def needs(kind: str, child: str, *args: str):
    """잔해가 실제로 생기는 엔진에서만 도는 테스트에 단다."""
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *a, **kw):
            if not leaves_remnant(kind, child, *args):
                self.skipTest(skip_reason())
            return fn(self, *a, **kw)
        return wrapper
    return deco


def without(kind: str, child: str, *args: str):
    """반대쪽 — 잔해가 **안** 생기는 엔진에서만 도는 테스트(그 사실 자체를 잠근다)."""
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *a, **kw):
            if leaves_remnant(kind, child, *args):
                self.skipTest("이 엔진은 잔해를 남긴다 — 잔해 테스트들이 그 경로를 본다")
            return fn(self, *a, **kw)
        return wrapper
    return deco
