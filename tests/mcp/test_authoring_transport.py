"""MCP, REST and CLI preserve authoring diagnostics without changing success."""
import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

from api import cli
from vinc_mcp import tools
from tests.mcp.test_rest_v1 import Server

RESULT = {"executed": True, "merged": {"nodes": {"created": 1, "matched": 0},
          "edges": {"created": 0}}, "warnings": [],
          "advisories": [{"code": "claim_replacement_review", "node_id": "record:new",
                         "previous_id": "record:old", "automatic": False}],
          "advisory_review": {"state": "complete", "claims_reviewed": 1},
          "retrieval": {"node_vectors": "not_checked", "node_refresh": "release_boundary"}}


def test_mcp_ingest_preserves_advisories_and_owner():
    class Bridge:
        owner = "user:author"
        def request(self, method, path, body=None, **kwargs):
            assert path == "/ingest/fragment" and body["owner"] == self.owner
            assert body["review_claims"] is True
            return RESULT
    assert tools._t_ingest({"nodes": [], "review_claims": True}, Bridge()) == RESULT


def test_rest_result_and_cli_json_are_the_same_diagnostic_receipt():
    server = Server(role="editor")
    try:
        server.bridge.override = RESULT
        status, _, result = server.request("POST", "/v1/fragments", body={"nodes": [], "review_claims": True})
        assert status == 200 and result == RESULT
        assert server.bridge.calls[-1][3]["review_claims"] is True
        out = io.StringIO()
        with patch.dict("os.environ", {"VINC_API_KEY": "vinc_sk_good",
                                      "VINC_API_URL": server.base}), redirect_stdout(out), patch.object(
                server.server.RequestHandlerClass, "log_message", lambda *args: None):
            code = cli.main(["--target", "account", "call", "vinc_ingest",
                             "--args", '{"nodes": [], "review_claims": true}'])
        assert code == 0 and json.loads(out.getvalue()) == RESULT
        assert server.bridge.calls[-1][3]["review_claims"] is True
    finally:
        server.close()


def test_cli_document_validity_flags_forward_and_omission_preserves():
    seen = []
    def receive(name, data, **kwargs):
        seen.append((name, data))
        return {"executed": True, "doc_id": "probe"}
    with patch("api.cli_account.call", receive), patch("sys.stdin", io.StringIO("Canonical text")), redirect_stdout(io.StringIO()):
        assert cli.main(["--target", "account", "doc", "put", "--id", "probe",
                         "--topic", "vinc/product/roadmap", "--valid-from", "2026-10-13",
                         "--valid-until", "", "--superseded-by", "new"]) == 0
    assert seen[0][1]["valid_from"] == "2026-10-13"
    assert seen[0][1]["valid_until"] == ""
    assert seen[0][1]["superseded_by"] == "new"
    assert seen[0][1]["topic"] == "vinc/product/roadmap"
    with patch("api.cli_account.call", receive), patch("sys.stdin", io.StringIO("Canonical text")), redirect_stdout(io.StringIO()):
        assert cli.main(["--target", "account", "doc", "put", "--id", "probe"]) == 0
    assert all(key not in seen[1][1] for key in ("valid_from", "valid_until", "superseded_by", "topic"))


def test_cli_ingest_comparison_flag_is_explicit(tmp_path):
    path = tmp_path / "fragment.json"
    path.write_text('{"nodes": []}', encoding="utf-8")
    seen = []
    def receive(name, data, **kwargs):
        seen.append(data)
        return RESULT
    with patch("api.cli_account.call", receive), redirect_stdout(io.StringIO()):
        assert cli.main(["--target", "account", "ingest", "--file", str(path), "--review-claims"]) == 0
        assert cli.main(["--target", "account", "ingest", "--file", str(path)]) == 0
    assert seen[0]["review_claims"] is True and "review_claims" not in seen[1]
