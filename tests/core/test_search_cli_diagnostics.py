"""사람용 및 NDJSON 출력에서도 검색 손실이 조용히 사라지지 않는다."""
import json

from api import cli_output


def test_human_search_includes_node_only_matches(capsys):
    cli_output.configure("human", "search")
    assert cli_output.emit({"rows": [], "node_hits": [
        {"id": "episode:synthetic", "kind": "episode", "title": "Release fixture"}]}) == 0
    assert "episode:synthetic" in capsys.readouterr().out


def test_ndjson_preserves_rows_and_reports_lost_node_semantics_on_stderr(capsys):
    cli_output.configure("ndjson", "search")
    assert cli_output.emit({"rows": [{"id": "synthetic#00000"}],
        "node_semantic": {"state": "degraded", "error": "node_vector_query_failed"}}) == 0
    out = capsys.readouterr()
    assert json.loads(out.out) == {"id": "synthetic#00000"}
    assert "node_vector_query_failed" in out.err


def test_human_empty_degraded_search_is_explicit(capsys):
    cli_output.configure("human", "search")
    assert cli_output.emit({"rows": [], "node_hits": [], "node_error": "synthetic failed"}) == 0
    assert "node search degraded" in capsys.readouterr().err
