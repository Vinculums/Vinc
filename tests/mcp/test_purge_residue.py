# -*- coding: utf-8 -*-
"""계정 삭제 뒤에 남던 잔여(2026-09-26 갭 분석 §6, 판정 D11) — 순수 층.

  · purge_owner 가 라벨 루프 밖의 셋도 지우는가: _SyncBase(키 접두, 네임스페이싱이 켜진
    스토어에서만)·_SyncMeta(키 꼬리)·어휘 파일의 소유자 주제.
  · delete_node 가 NodeVec 미러 행과 _SyncBase 행을 함께 지우는가 — 미러 행이 남으면
    지운 노드가 다음 기동까지 의미 팔의 검색 후보로 계속 나온다.
  · /internal/purge-owner 가 그래프 뒤에 이 서버의 OAuth 상태도 지우는가, 그리고 다른
    소유자의 행은 그대로인가.

실엔진 왕복은 test_embedded_backend.EmbeddedE2E(리눅스 CI)가 `_SyncMeta` 까지 본다.
"""
from __future__ import annotations

import base64
import hashlib
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import query as q                     # noqa: E402
from store import pkns                         # noqa: E402
from store.theme_store import ThemeStore, owner_theme_id   # noqa: E402
from vinc_mcp import edverify as ev            # noqa: E402
from vinc_mcp.bridge import Bridge             # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp    # noqa: E402
from vinc_mcp.store import Store               # noqa: E402

ACCT_SEED = bytes((i * 9 + 4) & 0xFF for i in range(32))


class _CapStore:
    """run 을 가로채 (cypher, params) 를 기록. `count(n)` 질의만 지정 건수를 돌려준다."""

    def __init__(self, counts: dict[str, int]):
        self.counts = counts
        self.calls: list[tuple[str, dict | None]] = []

    def run(self, cypher: str, params: dict | None = None):
        self.calls.append((cypher, params))
        if "count(n)" in cypher:
            label = cypher.split("(n:", 1)[1].split(")", 1)[0]
            return [{"c": self.counts.get(label, 0)}]
        return []


# ── purge_owner: 라벨 루프 밖의 잔여 ─────────────────────────────────────────
def test_purge_owner_removes_sync_meta_rows_by_key_tail():
    store = _CapStore({})
    out = q.purge_owner("user:alice", store=store)
    assert out["executed"]
    tails = [(cy, p) for cy, p in store.calls if "_SyncMeta" in cy]
    assert tails, "소유자별 _SyncMeta 행을 세는 질의가 나가야 한다"
    assert all("ENDS WITH $s" in cy and p == {"s": ":user:alice"} for cy, p in tails)
    for k in ("_SyncBase", "_SyncMeta", "theme"):
        assert k in out["deleted"], k


def test_purge_owner_sync_base_by_key_prefix_only_when_namespaced(monkeypatch):
    monkeypatch.delenv("VINC_PK_NAMESPACED", raising=False)
    store = _CapStore({})
    q.purge_owner("user:alice", store=store)
    assert not any("_SyncBase" in cy for cy, _ in store.calls), \
        "네임스페이싱이 꺼진 스토어(소유자 하나)에서는 _SyncBase 를 가를 수 없으니 건드리지 않는다"

    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    store = _CapStore({})
    q.purge_owner("user:alice", store=store)
    hits = [(cy, p) for cy, p in store.calls if "_SyncBase" in cy]
    assert hits, "네임스페이싱이 켜지면 키 접두로 _SyncBase 를 센다"
    assert all("STARTS WITH $p" in cy and p == {"p": "user:alice" + pkns.SEP} for cy, p in hits)
    # owner 는 문자열에 끼워 넣지 않는다
    assert all("user:alice" not in cy for cy, _ in store.calls)


# ── delete_node: 미러 행과 기준 행이 함께 간다 ───────────────────────────────
def test_delete_node_drops_mirror_and_base_rows():
    store = _CapStore({"Concept": 1})
    out = q.delete_node("concept:x", owner="user:alice", store=store)
    assert out["found"] and out["deleted"]["node"] == 1, out
    assert any("NodeVec" in cy and "DELETE v" in cy and p == {"vid": "user:alice#concept:x"}
               for cy, p in store.calls), "NodeVec 미러 행 삭제가 없다"
    assert any("_SyncBase" in cy and "DELETE b" in cy
               and p == {"id": pkns.key("user:alice", "concept:x")}
               for cy, p in store.calls), "_SyncBase 행 삭제가 없다"


# ── 어휘 파일: 소유자 주제 삭제 ──────────────────────────────────────────────
def test_theme_store_delete_theme_in_memory():
    ts = ThemeStore(None)
    tid = owner_theme_id("user:alice")
    ts.create_theme(tid, "alice")
    assert ts.delete_theme(tid) is True
    assert ts.list_themes() == []
    assert ts.delete_theme(tid) is False          # 없으면 False, 예외 없음
    assert ts.delete_theme("") is False


# ── /internal/purge-owner: 그래프 뒤에 OAuth 상태 ───────────────────────────
class _PurgeCapBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, body))
        return {"executed": True, "deleted": {"Concept": 3}, "total": 3}


_BH_EMPTY = base64.urlsafe_b64encode(hashlib.sha256(b"").digest()).rstrip(b"=").decode()


def _purge_assertion(sub="u-1", seed=ACCT_SEED, **over):
    now = int(time.time())
    body = {"typ": "purge", "iss": "account", "aud": "mcp",
            "sub": sub, "iat": now, "exp": now + 120,
            "bh": _BH_EMPTY, "jti": uuid.uuid4().hex}
    body.update(over)
    return ev.sign_assertion(seed, body)


def test_purge_request_purges_oauth_rows_of_that_owner_only(tmp_path):
    s = Store(str(tmp_path / "oauth.db"))
    now = time.time()
    for o in ("user:u-1", "user:u-2"):
        s.add_token(f"tok-{o}", "c1", o, "free", now + 600)
        s.add_refresh(f"ref-{o}", "c1", o, "free")
        s.set_owner_plan(o, "free")
        s.quota_check(o, limit=10, window_s=60, lock_s=60)
    app = MCPHTTPApp(_PurgeCapBridge(), static_token="t",
                     account_public=ev.secret_to_public(ACCT_SEED), store=s)
    try:
        out = app.purge_owner_request(_purge_assertion(sub="u-1"))
        assert out["executed"]
        assert out["oauth"] == {"tokens": 1, "refresh": 1, "codes": 0, "usage": 1, "owner_plan": 1}

        def left(table: str, owner: str) -> int:
            return s._db.execute(f"SELECT count(*) FROM {table} WHERE owner=?",
                                 (owner,)).fetchone()[0]

        for t in ("tokens", "refresh", "usage", "owner_plan"):
            assert left(t, "user:u-1") == 0, f"{t}: u-1 의 행이 남음"
            assert left(t, "user:u-2") == 1, f"{t}: u-2 의 행이 함께 지워짐 — 격리 위반"
    finally:
        s.close()


def test_purge_request_skips_oauth_when_no_store():
    app = MCPHTTPApp(_PurgeCapBridge(), static_token="t",
                     account_public=ev.secret_to_public(ACCT_SEED))
    out = app.purge_owner_request(_purge_assertion(sub="u-1"))
    assert out["executed"] and "oauth" not in out
