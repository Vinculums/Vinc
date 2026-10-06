"""가드 획득과 실행 분리: 잠금 수명, 강등, owner 범위를 그대로 유지한다."""
import threading
from contextlib import contextmanager

import pytest

from store import embedded_backend as eb, request_timing as timing


def test_wait_excludes_held_body_and_preserves_suppression(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(timing.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(timing.time, "thread_time", lambda: 0.0)
    exited = []
    @contextmanager
    def guard():
        clock[0] += .02
        try:
            yield "acquired"
        except ValueError:
            exited.append("suppressed")
        finally:
            clock[0] += 10
    with timing.capture() as collected:
        with timing.wait("ddl_shared_wait", guard()) as value:
            assert value == "acquired"
            clock[0] += 20
            raise ValueError("private material")
    assert exited == ["suppressed"]
    assert collected.values["ddl_shared_wait"] == pytest.approx(20)
    assert not timing.active()


@pytest.mark.parametrize("diagnostic", [False, True])
def test_wait_passes_acquisition_error_without_entering_body(diagnostic):
    @contextmanager
    def failed():
        raise eb.DdlGuardTimeout("private acquisition error")
        yield
    with timing.capture() as collected:
        manager = timing.wait("ddl_shared_wait", failed()) if diagnostic else failed()
        with pytest.raises(eb.DdlGuardTimeout):
            with manager:
                pytest.fail("acquisition failed")
    assert ("ddl_shared_wait" in collected.values) == diagnostic
    assert "private" not in str(collected.values)


def test_ddl_read_wait_is_separate_from_execution_and_hold(monkeypatch):
    guard = eb._DdlGuard()
    monkeypatch.setattr(eb, "DDL_GUARD", guard)
    clock = [0.0]
    monkeypatch.setattr(timing.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(timing.time, "thread_time", lambda: 0.0)
    real_shared = guard.shared
    @contextmanager
    def delayed_shared():
        clock[0] += .05
        with real_shared():
            yield
    monkeypatch.setattr(guard, "shared", delayed_shared)
    class Result:
        def get_column_names(self): return ["value"]
        def has_next(self): return False
    class Raw:
        def execute(self, *_args):
            assert guard._readers == 1
            clock[0] += .1
            return Result()
    with timing.capture() as collected:
        assert eb._exec_to_dicts(eb._Conn(Raw()), "RETURN 1", None) == []
    assert collected.values["ddl_shared_wait"] == pytest.approx(50)
    assert collected.values["db_execute"] == pytest.approx(100)
    assert guard._readers == 0


def test_real_guard_timeout_is_measured_and_released(monkeypatch):
    guard = eb._DdlGuard()
    held, release = threading.Event(), threading.Event()
    def writer():
        with guard.exclusive():
            held.set()
            release.wait(5)
    thread = threading.Thread(target=writer)
    thread.start()
    assert held.wait(5)
    try:
        with timing.capture() as collected:
            with pytest.raises(eb.DdlGuardTimeout):
                with timing.wait("ddl_shared_wait", guard.shared(timeout=.02)):
                    pytest.fail("reader entered during DDL")
        assert collected.values["ddl_shared_wait"] >= 10
        assert guard._readers == 0
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and guard._owner is None


@pytest.mark.parametrize("fallback", [False, True])
def test_node_index_and_owner_fallback_keep_original_scope(monkeypatch, fallback):
    backend = object.__new__(eb.EmbeddedBackend)
    backend._conn = object()
    backend._load_ext = lambda *a, **kw: None
    calls = []
    def execute(conn, query, params):
        calls.append((query, dict(params)))
        if "QUERY_VECTOR_INDEX" in query:
            return [] if fallback else [{"id": "concept:mine", "distance": .1}]
        return [{"id": "concept:mine", "distance": .1}]
    monkeypatch.setattr(eb, "_exec_to_dicts", execute)
    monkeypatch.setattr(eb, "keep_vector_index", lambda: False)
    with timing.capture() as collected:
        rows = backend.node_vector_search([1.0], 1, owner="user:mine")
    assert rows == [{"id": "concept:mine", "distance": .1}]
    assert len(calls) == (2 if fallback else 1)
    assert all(p["owner"] == "user:mine" for q, p in calls)
    assert "WHERE node.owner_id = $owner" in calls[0][0]
    assert calls[0][1]["k"] == backend._OVERSAMPLE
    assert "node_vector_index" in collected.values
    assert ("node_vector_fallback" in collected.values) == fallback
    assert not any(p.endswith("_error") for p in collected.values)


@pytest.mark.parametrize("stage", ["load", "query"])
def test_node_semantic_failure_remains_empty_but_has_a_fixed_metric(monkeypatch, stage):
    backend = object.__new__(eb.EmbeddedBackend)
    backend._conn = object()
    def fail(*a, **kw): raise RuntimeError("private owner and vector")
    backend._load_ext = fail if stage == "load" else lambda *a, **kw: None
    monkeypatch.setattr(eb, "_exec_to_dicts", fail)
    monkeypatch.setattr(eb, "keep_vector_index", lambda: False)
    with timing.capture() as collected:
        assert backend.node_vector_search([1.0], 1, owner="user:mine") == []
        header = timing.header("sidecar")
    assert f"node_vector_{stage}_error;dur=" in header
    assert "private" not in header and "user:mine" not in header


def test_real_node_index_fallback_preserves_owner_and_distance(tmp_path, monkeypatch):
    from api import node_mirror
    from store.backend import open_store
    from store.config import load_config
    monkeypatch.setenv("VINC_KEEP_VECTOR_INDEX", "off")
    store = open_store(load_config({"data_dir": str(tmp_path / "db"), "buffer_pool_mb": 64}))
    assert store is not None, eb.EmbeddedBackend.last_open_error
    try:
        store.provision(dim=4)
        # 전역 상위 5개가 모두 다른 owner라 fallback을 실제 엔진에서 밟는다.
        rows = [{"id": f"concept:other{i}", "kind": "concept", "owner": "user:other",
                 "title": "Other", "text": "Other", "emb": [1.0, .01 * (i + 1), 0, 0]}
                for i in range(24)]
        rows.append({"id": "concept:mine", "kind": "concept", "owner": "user:mine",
                     "title": "Mine", "text": "Mine", "emb": [0, 1.0, 0, 0]})
        assert node_mirror.replace(store, rows, "fixture")["index_ok"]
        reader = store.reader_view()
        try:
            with timing.capture() as collected:
                hits = reader.node_vector_search([1.0, 0, 0, 0], 1, owner="user:mine")
            assert [h["id"] for h in hits] == ["concept:mine"]
            assert hits[0]["distance"] == pytest.approx(1)
            for phase in ("node_vector_index", "node_vector_fallback", "db_execute",
                          "db_fetch", "ddl_shared_wait"):
                assert phase in collected.values
            assert not any(p.endswith("_error") for p in collected.values)
            assert reader.node_vector_search([1.0, 0, 0, 0], 1, owner="user:absent") == []
        finally:
            reader.close()
    finally:
        store.close()
