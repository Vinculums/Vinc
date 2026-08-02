# -*- coding: utf-8 -*-
"""vinc_mcp.store — SQLite OAuth·쿼터 영속 스토어 단위."""
import os

import pytest

from vinc_mcp.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(os.path.join(tmp_path, "oauth.db"))
    yield s
    s.close()


def test_clients_roundtrip(store):
    assert store.get_client("nope") is None
    store.add_client("c1", ["https://a/cb", "https://b/cb"], "Claude")
    got = store.get_client("c1")
    assert got["name"] == "Claude"
    assert got["redirect_uris"] == ["https://a/cb", "https://b/cb"]


def test_codes_single_consume_carries_owner_plan(store):
    store.add_code("code1", "c1", "https://a/cb", "chal", "user:u1", "pro", exp=9e9)
    rec = store.pop_code("code1")
    assert rec["client_id"] == "c1" and rec["owner"] == "user:u1" and rec["plan"] == "pro"
    assert store.pop_code("code1") is None  # 단일 소비


def test_tokens_expiry(store):
    store.add_token("t1", "c1", "user:u1", "free", exp=1000)
    assert store.get_token("t1", now=500)["owner"] == "user:u1"
    assert store.get_token("t1", now=2000) is None       # 만료 → None
    assert store.get_token("t1", now=500) is None         # 만료 시 삭제됨


def test_refresh_single_consume(store):
    store.add_refresh("r1", "c1", "user:u1", "pro")
    assert store.pop_refresh("r1")["owner"] == "user:u1"
    assert store.pop_refresh("r1") is None


def test_connect_request_consume(store):
    store.add_connect_request("req1", "c1", "https://a/cb", "chal", "st", exp=9e9)
    assert store.get_connect_request("req1")["client_id"] == "c1"
    rec = store.consume_connect_request("req1")
    assert rec and rec["redirect_uri"] == "https://a/cb"
    assert store.consume_connect_request("req1") is None   # 단일 소비
    assert store.get_connect_request("req1") is None       # 소비 후 조회 불가


def test_connect_request_expiry(store):
    store.add_connect_request("req2", "c1", "https://a/cb", "chal", None, exp=1000)
    assert store.get_connect_request("req2", now=2000) is None
    assert store.consume_connect_request("req2", now=2000) is None


def test_owner_plan(store):
    assert store.get_owner_plan("user:u1") is None
    store.set_owner_plan("user:u1", "pro")
    assert store.get_owner_plan("user:u1") == "pro"


def test_quota_allows_then_locks(store):
    o, kw = "user:u1", dict(limit=3, window_s=86400, lock_s=43200)
    # 1..3 허용
    for i in (1, 2, 3):
        r = store.quota_check(o, now=100, **kw)
        assert r["allowed"] and r["count"] == i
    # 4번째 → 초과·잠금
    r = store.quota_check(o, now=100, **kw)
    assert not r["allowed"] and r["reason"] == "exceeded"
    assert r["retry_after_s"] == 43200
    # 잠금 중 재시도 → locked, retry_after 감소
    r = store.quota_check(o, now=100 + 600, **kw)
    assert not r["allowed"] and r["reason"] == "locked"
    assert r["retry_after_s"] == 43200 - 600


def test_quota_resets_after_lock_expires(store):
    o, kw = "user:u1", dict(limit=2, window_s=86400, lock_s=43200)
    for _ in range(2):
        assert store.quota_check(o, now=0, **kw)["allowed"]
    assert not store.quota_check(o, now=0, **kw)["allowed"]     # 잠금(locked_until=43200)
    # 잠금 해제 후 → 카운터 리셋, 다시 허용
    r = store.quota_check(o, now=43200, **kw)
    assert r["allowed"] and r["count"] == 1


def test_quota_resets_after_window(store):
    o, kw = "user:u1", dict(limit=2, window_s=1000, lock_s=500)
    assert store.quota_check(o, now=0, **kw)["allowed"]
    # 창(1000s) 경과 → 리셋
    r = store.quota_check(o, now=1001, **kw)
    assert r["allowed"] and r["count"] == 1


def test_purge_expired(store):
    store.add_code("c", "c1", "u", "ch", None, None, exp=100)
    store.add_token("t", "c1", None, None, exp=100)
    store.add_connect_request("r", "c1", "u", "ch", None, exp=100)
    n = store.purge_expired(now=200)
    assert n >= 3
    assert store.pop_code("c") is None and store.get_token("t", now=50) is None


def test_persistence_across_reopen(tmp_path):
    path = os.path.join(tmp_path, "oauth.db")
    s1 = Store(path)
    s1.add_token("t1", "c1", "user:u1", "pro", exp=9e9)
    s1.close()
    s2 = Store(path)                        # 재기동 시뮬레이션
    assert s2.get_token("t1")["owner"] == "user:u1"   # 재인가 불필요 — 영속 확인
    s2.close()


def test_client_registration_survives_restart(tmp_path):
    """2026-08-03 사고 회귀 — 등록이 재기동을 넘겨야 한다.

    허브가 상태 DB 를 이미지 레이어(`~/.vinc/mcp`)에 두는 바람에 배포마다 이 표가
    비었고, 커넥터가 캐시해 둔 client_id 를 제시하면 `/authorize` 가
    `400 {"error":"unknown client_id"}` 를 냈다. 파일이 남아 있는 한 등록도
    남는다는 것 — 즉 사고의 원인이 스토어가 아니라 **경로**였다는 것 — 을 못 박는다.
    """
    path = os.path.join(tmp_path, "oauth.db")
    s1 = Store(path)
    s1.add_client("cid-1", ["https://claude.ai/api/mcp/auth_callback"], "Claude")
    s1.close()
    s2 = Store(path)
    got = s2.get_client("cid-1")
    assert got is not None, "재기동 후 client 가 사라졌다 — unknown client_id 재발"
    assert got["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]
    s2.close()


def test_journal_mode_defaults_off_wal(tmp_path, monkeypatch):
    """저널 모드 기본은 WAL 이 아니다 — 상태 DB 가 Azure Files(SMB) 위에 산다.

    SQLite 의 WAL 은 `-shm` 공유 메모리를 요구해 네트워크 파일시스템에서 동작하지
    않는다. 여기서 WAL 로 되돌아가면 영속화 수정이 오히려 기동을 깨뜨린다."""
    monkeypatch.delenv("VINC_MCP_SQLITE_JOURNAL", raising=False)
    s = Store(os.path.join(tmp_path, "oauth.db"))
    assert s.journal_mode != "wal"
    s.close()


def test_journal_mode_env_override_and_bad_value(tmp_path, monkeypatch):
    """되돌릴 길(env)은 열어 두되, 오타는 기본값으로 강등한다 — 상태 DB 는 기동 경로라
    문법 오류로 번지면 서비스가 통째로 안 뜬다."""
    monkeypatch.setenv("VINC_MCP_SQLITE_JOURNAL", "wal")
    s1 = Store(os.path.join(tmp_path, "a.db"))
    assert s1.journal_mode == "wal"          # 로컬 디스크에서는 실제로 올라간다
    s1.close()

    monkeypatch.setenv("VINC_MCP_SQLITE_JOURNAL", "not-a-mode")
    s2 = Store(os.path.join(tmp_path, "b.db"))
    assert s2.journal_mode == "delete"
    s2.close()
