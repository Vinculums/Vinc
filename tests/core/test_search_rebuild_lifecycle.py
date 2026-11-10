"""읽기 요청을 마친 뒤 한 번만 재생성하고, 유지보수 중에는 유한하게 거절한다."""
import threading
import time
from types import SimpleNamespace

import pytest

from api.jobs import JobQueue
from api.server import VincApp
from store import embedded_backend as B


class Connection:
    def close(self):
        pass


@pytest.fixture
def app(monkeypatch):
    guard = B._DdlGuard()
    monkeypatch.setattr(B, "DDL_GUARD", guard)
    entered, release = threading.Event(), threading.Event()
    builds = []

    def build(**kwargs):
        with guard.exclusive():
            builds.append(kwargs)
            entered.set()
            assert release.wait(B.REQUEST_WAIT_S + 10)
            return {"vector": True}

    backend = SimpleNamespace(ensure_search_indexes=build)
    backend.reader_view = lambda writer=None: B._ReaderView(backend, Connection(), writer=writer)
    instance = VincApp.__new__(VincApp)
    instance.store = backend
    instance._conn_lock = threading.Lock()
    instance._reader_takes_writer = None
    instance._index_lock = threading.Lock()
    instance._index_job = None
    instance.jobs = JobQueue()
    yield instance, guard, entered, release, builds
    release.set()
    instance.jobs.shutdown(drain=True)


def test_initiating_request_finishes_before_exclusive_rebuild(app):
    instance, guard, entered, release, builds = app

    def search(reader):
        reader.schedule_search_indexes(dim=1024)
        assert not entered.is_set()
        # 후속 읽기와 결과 조립이 끝날 때까지 배타가 요청을 앞지르지 못한다.
        with guard.shared():
            time.sleep(.03)
        assert not entered.is_set()
        return {"executed": True, "arms": "fts", "vector_rebuilding": True}

    started = time.monotonic()
    code, result = instance._read(search)
    assert code == 200 and result["vector_rebuilding"]
    assert time.monotonic() - started < .5
    assert entered.wait(1)
    assert len(builds) == 1
    assert guard._readers == 0


def test_reader_during_rebuild_gets_503_and_recovers(app):
    instance, guard, entered, release, builds = app
    instance._read(lambda reader: reader.schedule_search_indexes(dim=1024) or {})
    assert entered.wait(1)
    ran = []
    started = time.monotonic()
    code, result = instance._read(lambda reader: ran.append(True) or {})
    assert code == 503 and result["error"] == "store_busy"
    assert result["retry_after_s"] == 1 and not result["executed"]
    # 읽기 대기 상한을 다 쓰고 거절한다 — 그 너머로 매달리지 않는다.
    assert not ran and time.monotonic() - started < B.REQUEST_WAIT_S + .5
    release.set()
    assert instance._index_job._done.wait(2)
    assert instance._read(lambda reader: {"executed": True}) == (200, {"executed": True})


def test_sync_writer_is_not_deadlocked_by_request_guard(app):
    instance, guard, entered, release, builds = app
    release.set()
    assert instance._read(lambda reader: reader.ensure_search_indexes(dim=1024))[0] == 200
    assert len(builds) == 1 and guard._readers == 0


def test_rebuild_submission_coalesces_until_job_finishes(app):
    instance, guard, entered, release, builds = app
    first = instance._at_write_point("search.index", lambda: instance.store.ensure_search_indexes(), 0)
    assert entered.wait(1)
    second = instance._at_write_point("search.index", lambda: instance.store.ensure_search_indexes(), 0)
    assert first["job_id"] == second["job_id"]
    release.set()
    assert instance._index_job._done.wait(2)
    third = instance._at_write_point("search.index", lambda: instance.store.ensure_search_indexes(), 0)
    assert third["job_id"] != first["job_id"]
    assert instance._index_job._done.wait(2) and len(builds) == 2


def test_shared_requests_still_overlap(app):
    instance, guard, entered, release, builds = app
    barrier = threading.Barrier(2)
    results = []

    def read(reader):
        barrier.wait(2)
        return {"executed": True}

    threads = [threading.Thread(target=lambda: results.append(instance._read(read))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
    assert len(results) == 2 and all(code == 200 for code, _ in results)
    assert guard._readers == 0
