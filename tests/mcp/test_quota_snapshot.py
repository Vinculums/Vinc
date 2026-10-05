"""VIN-138 내부 읽기 준비: 조회가 소비·리셋·잠금을 저장하지 않는지 검증한다."""
import pytest
from vinc_mcp.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "oauth.db")
    yield s
    s.close()


def test_missing_owner_does_not_insert_or_read_other_owner(store):
    store.quota_check("user:other", limit=3, window_s=100, lock_s=50, now=100)
    before = store.usage_rows()
    assert store.quota_snapshot("user:new", limit=3, window_s=100, now=101) == {
        "count": 0, "remaining": 3, "window_start": None, "locked_until": None}
    assert store.usage_rows() == before


def test_read_at_ceiling_does_not_consume_or_create_lock(store):
    store.quota_check("user:a", limit=1, window_s=100, lock_s=50, now=100)
    before = store.usage_rows()
    for _ in range(3):
        assert store.quota_snapshot("user:a", limit=1, window_s=100, now=101) == {
            "count": 1, "remaining": 0, "window_start": 100, "locked_until": None}
    assert store.usage_rows() == before


@pytest.mark.parametrize("now", [200, 201])
def test_expired_window_reads_zero_without_resetting_store(store, now):
    store.quota_check("user:a", limit=3, window_s=100, lock_s=50, now=100)
    before = store.usage_rows()
    assert store.quota_snapshot("user:a", limit=3, window_s=100, now=now)["count"] == 0
    assert store.usage_rows() == before


def test_lock_precedes_expired_window_and_higher_limit(store):
    store.quota_check("user:a", limit=1, window_s=10, lock_s=50, now=100)
    store.quota_check("user:a", limit=1, window_s=10, lock_s=50, now=101)
    before = store.usage_rows()
    assert store.quota_snapshot("user:a", limit=100, window_s=10, now=120) == {
        "count": 1, "remaining": 0, "window_start": 100, "locked_until": 151}
    assert store.usage_rows() == before
    assert store.quota_snapshot("user:a", limit=100, window_s=10, now=151) == {
        "count": 0, "remaining": 100, "window_start": None, "locked_until": None}
    assert store.usage_rows() == before
