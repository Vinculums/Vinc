"""Request timing collection is opt-in, isolated, and cannot carry arbitrary material."""
import concurrent.futures

import pytest

from store import request_timing as timing


def test_no_collector_changes_no_result_and_emits_no_header():
    @timing.timed("query")
    def read():
        return {"rows": ["same evidence"]}
    assert read() == {"rows": ["same evidence"]}
    assert timing.header("rest") == ""
    with timing.measure("embed"):
        pass
    assert not timing.active()


def test_nested_capture_and_failure_restore_previous_collector():
    with timing.capture() as outer:
        with pytest.raises(RuntimeError):
            with timing.capture() as inner:
                with timing.measure("keyword"):
                    raise RuntimeError("request body must not enter header")
        assert "keyword" in inner.values
        assert not outer.values
        with timing.measure("query"):
            pass
        result = timing.header("rest")
        assert "query;dur=" in result and "query_cpu;dur=" in result
        assert "keyword" not in result and "request body" not in result
    assert not timing.active()


@pytest.mark.parametrize("unsafe", ["embed;dur=NaN", "embed;dur=inf", "embed;dur=-1",
    "embed;dur=3600001", "query;dur=not_a_number", "private-key;dur=100",
    "embed;dur=1\r\nAuthorization: private", "private query material"])
def test_merge_rejects_nonfinite_unbounded_or_unrecognised_metrics(unsafe):
    with timing.capture() as collector:
        timing.merge_sidecar(unsafe)
        assert not collector.values


def test_merge_uses_only_fixed_names_and_accumulates_retries():
    with timing.capture() as collector:
        timing.merge_sidecar("embed;dur=14.5, query_cpu;dur=2, sidecar;dur=20")
        timing.merge_sidecar("embed;dur=4")
        assert collector.values == {"sidecar_embed": 18.5, "sidecar_query_cpu": 2.0,
                                    "sidecar": 20.0}


def test_every_sidecar_phase_survives_the_expanded_bounded_header():
    allowed = {"sidecar", *timing.PHASES, *(p + "_cpu" for p in timing.PHASES)}
    value = ", ".join(f"{name};dur=1.25" for name in sorted(allowed))
    assert len(value) < 8192 and len(allowed) > 32
    with timing.capture() as collector:
        timing.merge_sidecar(value)
        assert len(collector.values) == len(allowed)
        assert collector.values["sidecar_node_vector_fallback"] == 1.25
        assert collector.values["sidecar_vector_index_cpu"] == 1.25


def test_engine_observations_are_finite_bounded_ms_and_opt_in():
    timing.observe("db_compile", 1)
    assert not timing.active()
    with timing.capture() as collector:
        for value in (None, "private", float("nan"), float("inf"), -1, 3600001):
            timing.observe("db_compile", value)
        assert not collector.values
        timing.observe("db_compile", 1.5)
        timing.observe("db_compile", 2.5)
        assert collector.values == {"db_compile": 4}


def test_concurrent_threads_and_reused_thread_do_not_share_timings():
    def collect(phase):
        assert not timing.active()
        with timing.capture() as collector:
            with timing.measure(phase):
                pass
        assert not timing.active()
        return collector.values
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(collect, ["embed", "keyword", "node_vector", "reader"]))
    assert [set(v) for v in results] == [
        {p, p + "_cpu"} for p in ["embed", "keyword", "node_vector", "reader"]]
