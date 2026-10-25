"""OAuth 상태 저장소는 커넥션 하나를 여러 스레드가 나눠 쓴다. 읽기도 락 안에서 해야 한다."""
from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

from vinc_mcp.store import Store

SOURCE = Path(__file__).resolve().parents[2] / "vinc_mcp" / "store.py"
#: 락 밖에서 커넥션을 만져도 되는 자리: 생성자(커넥션을 아직 아무도 못 본다), 생성자 안에서만
#: 부르는 열 보강, 이미 락을 쥔 호출자만 부르는 함수(이름이 _locked 로 끝나거나 스냅샷 복사).
ALLOWED = {"__init__", "_ensure_column", "_save_snapshot", "_restore_snapshot"}


def _uses_db(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr == "_db" for n in ast.walk(node))


def _inside_lock(func: ast.FunctionDef) -> bool:
    """함수 안의 모든 self._db 사용이 `with self._lock:` 블록 안에 있는가."""
    locked: set[int] = set()
    for n in ast.walk(func):
        if isinstance(n, ast.With) and any(
                isinstance(i.context_expr, ast.Attribute) and i.context_expr.attr == "_lock"
                for i in n.items):
            locked.update(id(m) for m in ast.walk(n))
    return all(id(n) in locked for n in ast.walk(func)
               if isinstance(n, ast.Attribute) and n.attr == "_db")


def test_every_method_touches_the_connection_under_the_lock():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    store = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Store")
    loose = [f.name for f in store.body if isinstance(f, ast.FunctionDef)
             and f.name not in ALLOWED and not f.name.endswith("_locked")
             and _uses_db(f) and not _inside_lock(f)]
    assert loose == []


def test_getters_survive_concurrent_reads(tmp_path):
    store = Store(str(tmp_path / "oauth.db"), snapshot="")
    store.add_client("client-1", ["https://example.test/cb"], "Example")
    store.add_token("tok-1", "client-1", "user:u1", "free", exp=time.time() + 3600)
    store.set_owner_plan("user:u1", "free")
    errors: list[BaseException] = []

    def hammer():
        try:
            for _ in range(200):
                assert store.get_client("client-1")["name"] == "Example"
                assert store.get_token("tok-1")["owner"] == "user:u1"
                assert store.get_owner_plan_age("user:u1")[0] == "free"
        except BaseException as exc:  # noqa: BLE001 — 스레드 안의 실패를 밖으로 옮긴다
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store.close()
    assert errors == []


def test_an_expired_token_is_removed_under_the_same_lock(tmp_path):
    store = Store(str(tmp_path / "oauth.db"), snapshot="")
    store.add_token("old", "client-1", "user:u1", "free", exp=time.time() - 1)
    assert store.get_token("old") is None
    assert store.get_token("old") is None
    store.close()
