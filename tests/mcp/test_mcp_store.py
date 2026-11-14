# -*- coding: utf-8 -*-
"""vinc_mcp.hub_state — SQLite OAuth·쿼터 영속 스토어 단위."""
import os
import shutil
import time

import pytest

from vinc_mcp.hub_state import Store


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


def test_refresh_rotates_once_and_answers_again_only_inside_the_window(store):
    """회전은 한 번만 새 쌍을 만든다. 창 안의 재요청은 **같은 쌍**, 창 밖은 None(VIN-796)."""
    store.add_refresh("r1", "c1", "user:u1", "pro")
    assert store.get_refresh("r1")["owner"] == "user:u1"
    first = store.rotate_refresh("r1", access="a2", refresh="r2", exp=5000, now=1000)
    assert first["replayed"] is False and first["owner"] == "user:u1" and first["plan"] == "pro"
    again = store.rotate_refresh("r1", access="a3", refresh="r3", exp=5000, now=1030)
    assert again["replayed"] is True
    assert (again["access_token"], again["refresh_token"]) == ("a2", "r2")
    assert store.get_refresh("r3") is None                       # 새 쌍을 만들지 않았다
    assert store.rotate_refresh("r1", access="a4", refresh="r4", exp=9e9, now=1061) is None
    assert store.rotate_refresh("nope", access="a5", refresh="r5", exp=9e9, now=1000) is None


def test_connect_request_consume(store):
    store.add_connect_request("req1", "c1", "https://a/cb", "chal", "st", exp=9e9)
    rec = store.consume_connect_request("req1")
    assert rec and rec["client_id"] == "c1" and rec["redirect_uri"] == "https://a/cb"
    assert store.consume_connect_request("req1") is None   # 단일 소비


def test_connect_request_expiry(store):
    store.add_connect_request("req2", "c1", "https://a/cb", "chal", None, exp=1000)
    assert store.consume_connect_request("req2", now=2000) is None


def test_owner_plan(store):
    assert store.get_owner_plan_age("user:u1") == (None, None)
    store.set_owner_plan("user:u1", "pro")
    assert store.get_owner_plan_age("user:u1")[0] == "pro"


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


def test_quota_aligned_day_resets_at_utc_midnight_without_a_lock(store):
    """Curious 의 하루 창: 00:00 UTC 경계에서 시작하고, 한도 뒤에는 잠그지 않고 그날
    끝까지만 거부한다. 23:00 에 처음 쓴 사람도 한 시간 뒤 자정에 다시 열린다."""
    day = 86400 * 20_000                       # 어느 날의 00:00 UTC
    o, kw = "user:u1", dict(limit=2, window_s=86400, lock_s=0, aligned=True)
    for _ in range(2):
        assert store.quota_check(o, now=day + 23 * 3600, **kw)["allowed"]
    r = store.quota_check(o, now=day + 23 * 3600, **kw)
    assert not r["allowed"] and r["reason"] == "exceeded"
    assert r["retry_after_s"] == 3600 and r["locked_until"] is None
    assert r["resets_at"] == day + 86400
    assert store.usage_rows()[0]["locked_until"] == 0     # 잠금을 기록하지 않는다
    r = store.quota_check(o, now=day + 86400 + 1, **kw)   # 다음 날 00:00:01
    assert r["allowed"] and r["count"] == 1


def test_purge_expired(store):
    store.add_code("c", "c1", "u", "ch", None, None, exp=100)
    # `now=50` 은 픽스처가 아니라 계약이다: add_token 이 발급 **전에** 만료 행을
    # 쓸어 가므로(파일 크기 억제), 시계를 안 고정하면 실제 벽시계로 위 코드가 먼저
    # 지워져 아래 집계가 3 이 아닌 2 가 된다.
    store.add_token("t", "c1", None, None, exp=100, now=50)
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
    """저널 모드 기본은 WAL 이 아니다.

    작업본이 로컬이라 WAL 자체는 가능하지만, WAL 은 `-wal`/`-shm` 사이드카 파일을
    만들어 **DB 파일 하나만 복사하는 스냅샷으로는 일관 복원이 안 된다**. 롤백 저널은
    커밋 후 단일 파일이라 복사가 곧 스냅샷이다."""
    monkeypatch.delenv("VINC_MCP_SQLITE_JOURNAL", raising=False)
    s = Store(os.path.join(tmp_path, "oauth.db"))
    assert s.journal_mode != "wal"
    s.close()


# ── 영속 스냅샷 (2026-08-03 재설계) ──────────────────────────────────────────
# SQLite 를 SMB 에 올렸다가 `database is locked` 로 허브가 내려간 뒤의 모양이다:
# 작업본은 로컬, 영속 볼륨에는 파일 복사만. 아래가 그 계약을 고정한다.

def test_snapshot_restores_registration_after_wipe(tmp_path):
    """**사고 ① 회귀** — 작업본이 지워져도(배포=이미지 레이어 교체) 스냅샷에서
    등록이 돌아온다. 이게 되면 커넥터가 400 unknown client_id 를 안 만난다."""
    work = os.path.join(tmp_path, "work", "oauth.db")
    snap = os.path.join(tmp_path, "persist", "oauth.db")
    s1 = Store(work, snapshot=snap)
    s1.add_client("cid-1", ["https://claude.ai/api/mcp/auth_callback"], "Claude")
    s1.add_token("t1", "cid-1", "user:u1", "pro", exp=9e9)
    assert s1.snapshot_state == "ok", s1.snapshot_error
    s1.close()

    shutil.rmtree(os.path.dirname(work))          # 리비전 교체 = 작업본 소멸
    assert not os.path.exists(work)

    s2 = Store(work, snapshot=snap)               # 새 리비전 기동
    assert s2.restore_state == "restored"
    got = s2.get_client("cid-1")
    assert got is not None, "스냅샷 복원 실패 — unknown client_id 재발"
    assert got["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]
    assert s2.get_token("t1")["owner"] == "user:u1"
    s2.close()


def test_snapshot_does_not_clobber_existing_working_copy(tmp_path):
    """작업본이 이미 있으면 스냅샷으로 덮지 않는다 — 오래된 스냅샷이 최신 상태를
    되돌리면 방금 발급한 토큰이 사라진다(재시작 없는 재기동 경로)."""
    work = os.path.join(tmp_path, "oauth.db")
    snap = os.path.join(tmp_path, "snap.db")
    s1 = Store(work, snapshot=snap)
    s1.add_client("old", ["https://a/cb"], "")
    s1.close()
    s2 = Store(work, snapshot=snap)               # 작업본 살아 있음
    assert s2.restore_state == "working-copy-kept"
    s2.add_client("new", ["https://b/cb"], "")
    assert s2.get_client("old") and s2.get_client("new")
    s2.close()


def test_snapshot_failure_is_soft(tmp_path):
    """**사고 ② 회귀** — 스냅샷 경로가 못 쓰는 곳이어도 스토어는 산다.

    영속은 증강이고 인증은 성립 조건이다. 여기서 예외가 올라가면 그날처럼 MCP 가
    통째로 죽는다."""
    work = os.path.join(tmp_path, "oauth.db")
    # 파일을 디렉터리 자리에 놓아 스냅샷 쓰기를 확실히 실패시킨다.
    blocker = os.path.join(tmp_path, "blocked")
    with open(blocker, "w", encoding="utf-8") as f:
        f.write("not a directory")
    s = Store(work, snapshot=os.path.join(blocker, "oauth.db"))
    s.add_client("c", ["https://a/cb"], "")       # 예외가 새어 나오면 안 된다
    assert s.get_client("c") is not None          # 기능은 정상
    assert s.snapshot_state == "save-failed"      # 실패는 조용히 숨기지 않는다
    assert s.snapshot_error
    assert s.health()["snapshot_state"] == "save-failed"
    s.close()


def test_snapshot_off_when_unset(tmp_path):
    """스냅샷 미설정(데스크탑 기본)이면 아무것도 하지 않는다."""
    s = Store(os.path.join(tmp_path, "oauth.db"), snapshot=None)
    s.add_client("c", ["https://a/cb"], "")
    assert s.snapshot_state == "off"
    assert s.health()["snapshot"] == ""
    s.close()


def test_quota_writes_are_not_snapshotted(tmp_path):
    """쿼터 카운터는 연성 상태다 — 툴 호출마다 SMB 로 복사하면 비용만 든다.
    창 만료로 어차피 리셋되므로 잃어도 무방하다."""
    work = os.path.join(tmp_path, "oauth.db")
    snap = os.path.join(tmp_path, "snap.db")
    s = Store(work, snapshot=snap)
    before = os.path.getmtime(snap)
    time.sleep(0.01)
    for _ in range(3):
        s.quota_check("user:u", limit=100, window_s=60, lock_s=60)
    assert os.path.getmtime(snap) == before, "쿼터 쓰기가 스냅샷을 유발했다"
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


# ── clients 상한 · 기회주의 만료 청소 ────────────────────────────────────────
# 영속이 필요한 쓰기마다 DB 를 통째로 복사하는데, purge_expired 의 호출부가
# 프로덕션에 0건이고 clients 에 상한이 없어서 파일이 단조 증가했다 — 무인증
# /register 하나가 복사 하나이므로 비용이 초선형으로 자란다.

def test_add_client_purges_expired_rows(store):
    """무인증 등록 경로가 만료 행을 쓸어 간다 — 파일이 커지지 않게."""
    store.add_code("old", "c1", "https://a/cb", "chal", None, None, exp=100)
    store.add_token("stale", "c1", "user:u1", "free", exp=100, now=50)
    store.add_client("c2", ["https://b/cb"], "later", now=9000)
    assert store.pop_code("old") is None, "만료 코드가 남았다"
    assert store.get_token("stale", now=9000) is None, "만료 토큰이 남았다"


def test_add_client_keeps_live_rows(store):
    """청소가 살아 있는 것까지 지우면 커넥터가 끊긴다."""
    store.add_code("live", "c1", "https://a/cb", "chal", "user:u1", "pro", exp=9e9)
    store.add_token("good", "c1", "user:u1", "free", exp=9e9)
    store.add_client("c2", ["https://b/cb"], "later")
    assert store.get_token("good")["owner"] == "user:u1"
    assert store.pop_code("live")["plan"] == "pro"


def test_clients_table_is_capped(store, monkeypatch):
    from vinc_mcp import hub_state as store_mod
    monkeypatch.setattr(store_mod, "MAX_CLIENTS", 3)
    for i in range(10):
        store.add_client(f"c{i}", ["https://a/cb"], f"n{i}", now=1000 + i)
    n = store._db.execute("SELECT count(*) FROM clients").fetchone()[0]
    assert n == 3, f"상한이 안 걸렸다: {n}"
    # 가장 최근 것들이 남는다(방금 등록한 것이 잘려 나가면 등록이 무의미해진다).
    assert store.get_client("c9") is not None
    assert store.get_client("c0") is None


def test_cap_never_evicts_a_live_connector(store, monkeypatch):
    """상한이 **인증된 커넥터를 끊으면** 치료가 병보다 나쁘다. 토큰을 든
    client_id 는 오래됐어도 후보에서 빠진다."""
    from vinc_mcp import hub_state as store_mod
    monkeypatch.setattr(store_mod, "MAX_CLIENTS", 2)
    store.add_client("old-but-live", ["https://a/cb"], "keep", now=1)
    store.add_token("tok", "old-but-live", "user:u1", "free", exp=9e9)
    for i in range(6):
        store.add_client(f"junk{i}", ["https://a/cb"], "junk", now=100 + i)
    assert store.get_client("old-but-live") is not None, "살아 있는 커넥터가 잘렸다"


def test_add_token_purges_before_insert(store):
    """INSERT 뒤에 지우면 이미 만료된 exp 로 발급한 토큰이 곧바로 사라져,
    호출자에게는 쓰기가 조용히 실패한 것처럼 보인다."""
    store.add_token("t1", "c1", "user:u1", "free", exp=1000, now=9000)
    row = store._db.execute("SELECT owner FROM tokens WHERE token='t1'").fetchone()
    assert row is not None and row["owner"] == "user:u1"


def test_purge_expired_still_works_standalone(store):
    """공개 메서드는 그대로다 — 락을 스스로 쥐고 스냅샷까지 한다(교착 없이)."""
    store.add_code("c", "c1", "https://a/cb", "chal", None, None, exp=100)
    assert store.purge_expired(now=200) >= 1
