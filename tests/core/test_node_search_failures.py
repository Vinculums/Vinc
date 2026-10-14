"""노드 의미 검색 실패를 빈 정상 결과와 구분한다."""
import pytest

from store import embedded_backend as eb, request_timing as timing


@pytest.mark.parametrize("stage,code", [
    ("load", "node_vector_extension_unavailable"),
    ("query", "node_vector_query_failed"),
])
def test_native_failure_is_explicit_and_does_not_disclose_driver_detail(monkeypatch, stage, code):
    backend = object.__new__(eb.EmbeddedBackend)
    backend._conn = object()

    def fail(*args, **kwargs):
        raise RuntimeError("private owner and filesystem path")

    backend._load_ext = fail if stage == "load" else lambda *args, **kwargs: None
    monkeypatch.setattr(eb, "_exec_to_dicts", fail)
    with timing.capture() as measured:
        with pytest.raises(RuntimeError, match=f"^{code}$"):
            backend.node_vector_search([1.0], 1, owner="user:synthetic")
    assert f"node_vector_{stage}_error" in measured.values


def test_mirror_validation_compares_the_same_text_used_by_rebuild():
    from store.node_mirror_state import mirror_text, SPEC
    from api import node_mirror
    assert SPEC == {kind: (label, pk, extra) for label, pk, kind, extra in node_mirror.SPEC}
    for title, extra in [(" A  title ", "한글 " * 150), ("Title", ""), ("", "Body")]:
        assert mirror_text(title, extra) == node_mirror.mirror_text(title, extra)


def test_validation_without_owner_does_not_guess_a_cross_owner_source():
    from store.node_mirror_state import validate
    hits = [{"id": "concept:shared", "kind": "concept", "text": "Synthetic"}]
    def forbidden(*args):
        pytest.fail("ownerless validation must not guess ownership")
    kept, state = validate(forbidden, hits, owner=None)
    assert kept == hits and state["freshness"] == "not_checked"


def test_validation_preserves_indexed_prefix_policy_and_exact_kind(monkeypatch):
    from store.node_mirror_state import validate, mirror_text
    monkeypatch.setenv("VINC_PK_NAMESPACED", "0")
    source = {"id": "episode:synthetic", "title": "Episode", "extra": "a" * 300 + "new tail"}
    hits = [{"id": source["id"], "kind": "episode", "title": "Episode",
             "text": mirror_text("Episode", "a" * 300 + "old tail")},
            {"id": source["id"], "kind": "record", "title": "Episode", "text": "Episode"}]
    def read(query, params):
        assert "owner_id=$owner" in query and params["owner"] == "user:synthetic"
        return [source] if "n:Episode" in query else []
    kept, state = validate(read, hits, owner="user:synthetic")
    assert [h["kind"] for h in kept] == ["episode"]
    assert state["stale_candidates"] == 0 and state["missing_candidates"] == 1
