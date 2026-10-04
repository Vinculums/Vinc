"""PR #472 리뷰에서 재현된 CLI 결함의 회귀 테스트.

각 테스트 이름이 고장 하나를 말한다. 전부 임시 홈과 임시 데이터 경로에서 돈다.
"""
from __future__ import annotations

import contextlib
import http.server
import io
import json
import threading
from argparse import Namespace
from pathlib import Path

import pytest

from api import cli, cli_output, cli_tools, export, remote_client
from api.cli_transport import data_file
from store.config import resolve_data_dir


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    (root / ".vinc").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(root))
    monkeypatch.setenv("HOME", str(root))
    for name in ("VINC_TARGET", "VINC_SPACE", "VINC_API_KEY", "VINC_REMOTE_MCP_TOKEN",
                 "MEMORY_DATA_DIR", "VINC_FORMAT"):
        monkeypatch.delenv(name, raising=False)
    return root


def _mode(home, mode):
    (home / ".vinc" / "config.json").write_text(json.dumps({"mode": mode}), encoding="utf-8")


# ── 대상 판정 ────────────────────────────────────────────────────────────────

def test_account_mode_in_config_does_not_block_machine_verbs(home, tmp_path):
    """앱이 로그인하면 config.json 에 mode:account 를 쓰고, 사이드카를 플래그 없이 띄운다."""
    _mode(home, "account")
    code, out, err = _run(["store-probe", "--data", str(tmp_path / "db")])
    assert code == 0, err
    code, out, _ = _run(["mcp", "manual"])
    assert code == 0 and json.loads(out)


def test_explicit_account_target_still_refuses_machine_verbs(home, tmp_path):
    code, _, err = _run(["--target", "account", "store-probe", "--data", str(tmp_path / "db")])
    assert code == 2 and "local only" in err


def test_space_environment_is_ignored_on_the_local_target(home, monkeypatch, tmp_path):
    monkeypatch.setenv("VINC_SPACE", "0198c0de-1111-7222-8333-444455556666")
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "db"))
    code, out, err = _run(["--target", "local", "domains"])
    assert code == 0, err
    code, _, err = _run(["--target", "local", "--space",
                         "0198c0de-1111-7222-8333-444455556666", "domains"])
    assert code == 2 and "--space requires --target account" in err


def test_unknown_mode_in_config_reads_as_local(home, monkeypatch, tmp_path):
    _mode(home, "cloudy")
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "db"))
    code, _, err = _run(["domains"])
    assert code == 0, err


# ── merge 단축 표기 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("argv", [
    ["search", "--query", "merge", "--top-k", "3"],
    ["doc", "put", "--id", "d2", "--title", "merge"],
    ["path", "merge", "x"],
])
def test_merge_as_an_argument_value_is_not_rewritten(argv):
    assert cli._expand_merge(list(argv)) == argv


def test_merge_in_verb_position_after_root_options_is_rewritten():
    assert cli._expand_merge(["--format", "json", "merge", "concept:a", "concept:b"]) == \
        ["--format", "json", "merge", "nodes", "concept:a", "concept:b"]
    assert cli._expand_merge(["merge", "candidates"]) == ["merge", "candidates"]
    assert cli._verb_index(["--target", "local", "--json", "search"]) == 3


def test_search_for_the_word_merge_runs(home, monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "db"))
    code, out, err = _run(["search", "--query", "merge", "--top-k", "3"])
    assert code in (0, 1) and "unrecognized arguments" not in err


# ── 붙기 판정과 인프로세스 경로가 같은 파일을 본다 ────────────────────────────

@pytest.mark.parametrize("cmd", ["search", "recall", "brief", "decide", "domains", "doc"])
def test_tool_verbs_attach_to_the_file_they_would_open(home, cmd):
    args = Namespace(cmd=cmd, data=None)
    assert data_file(args) == resolve_data_dir(create=False)


def test_explicit_data_wins_for_attach_and_in_process(home, tmp_path):
    args = Namespace(cmd="search", data=str(tmp_path / "other"))
    assert data_file(args) == str(tmp_path / "other")


# ── 출력 계약 ────────────────────────────────────────────────────────────────

def test_confirmation_is_never_asked_on_stdout_off_a_terminal(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO("yes\n"))
    with contextlib.redirect_stdout(out):
        ok = cli_tools._confirm(Namespace(yes=False), "concept:x")
    assert ok is False and out.getvalue() == ""


def test_ndjson_failure_is_one_line(monkeypatch):
    cli_output.configure("ndjson", "doc get")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_output.emit({"kind": "doc.get", "executed": False,
                                "error": {"code": "not_found", "message": "unknown doc_id"}})
    assert code == 1 and out.getvalue() == ""
    assert err.getvalue().count("\n") == 1
    json.loads(err.getvalue())


def test_ndjson_domains_prints_one_row_per_line():
    cli_output.configure("ndjson", "domains")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli_output.emit({"kind": "domains", "executed": True,
                         "rows": [{"domain": "a"}, {"domain": "b"}]})
    assert [json.loads(line) for line in out.getvalue().splitlines()] == \
        [{"domain": "a"}, {"domain": "b"}]


def test_legacy_recall_flags_still_answer_locally(home, monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "db"))
    code, out, err = _run(["recall", "--query", "billing", "--limit", "2"])
    assert code == 0, err
    assert json.loads(out).get("kind") == "recall"


def test_legacy_recall_flags_are_refused_on_the_account_target(home, monkeypatch):
    monkeypatch.setenv("VINC_API_KEY", "vinc_sk_fake")
    code, _, err = _run(["--target", "account", "recall", "--query", "billing"])
    assert code == 2 and "local target only" in err


# ── 원격 클라이언트 ─────────────────────────────────────────────────────────

class _Recorder(http.server.BaseHTTPRequestHandler):
    seen: list = []
    redirect_to: str | None = None
    status = 200
    body = b'{"executed": true, "rows": []}'
    headers_out: dict = {}

    def _answer(self):
        type(self).seen.append((self.command, self.path, self.headers.get("Authorization")))
        if self.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.redirect_to)
            self.end_headers()
            return
        self.send_response(self.status)
        for k, v in self.headers_out.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(self.body)

    do_GET = do_POST = do_PUT = do_DELETE = _answer

    def log_message(self, *_a):
        pass


@contextlib.contextmanager
def _server(**attrs):
    handler = type("H", (_Recorder,), {"seen": [], **attrs})
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield handler, f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_redirect_is_not_followed_with_the_key():
    with _server() as (sink, sink_url):
        with _server(redirect_to=sink_url + "/stolen") as (_, url):
            with pytest.raises(remote_client.RemoteFailure) as err:
                remote_client.call("vinc_search", {"query": "x"}, key="vinc_sk_fake",
                                   base_url=url)
        assert err.value.exit_code == 5
        assert sink.seen == []


def test_plain_http_is_refused_off_loopback():
    with pytest.raises(remote_client.RemoteFailure) as err:
        remote_client.call("vinc_domains", {}, key="vinc_sk_fake",
                           base_url="http://mcp.example.com")
    assert err.value.exit_code == 2


def test_legacy_token_is_used_only_when_it_is_a_member_key(monkeypatch):
    monkeypatch.delenv("VINC_API_KEY", raising=False)
    monkeypatch.setenv("VINC_REMOTE_MCP_TOKEN", "static-mcp-token")
    assert remote_client.api_key() is None
    monkeypatch.setenv("VINC_REMOTE_MCP_TOKEN", "vinc_ro_abc")
    assert remote_client.api_key() == "vinc_ro_abc"
    monkeypatch.setenv("VINC_API_KEY", "vinc_sk_first")
    assert remote_client.api_key() == "vinc_sk_first"


def test_dot_segments_are_encoded():
    with _server() as (seen, url):
        remote_client.call("vinc_neighbors", {"node": ".."}, key="vinc_sk_fake", base_url=url)
    assert seen.seen[0][1].startswith("/v1/nodes/%2E%2E/neighbors")


def test_retry_after_survives_a_non_json_429():
    with _server(status=429, body=b"slow down", headers_out={"Retry-After": "30"}) as (_, url):
        with pytest.raises(remote_client.RemoteFailure) as err:
            remote_client.call("vinc_domains", {}, key="vinc_sk_fake", base_url=url)
    assert err.value.exit_code == 6
    assert err.value.result["error"]["details"]["retry_after_s"] == 30
    assert err.value.result["error"]["code"] == "quota_exceeded"


# ── export ──────────────────────────────────────────────────────────────────

def _bundle(nodes):
    return ({"executed": True, "nodes": nodes, "edges": []}, {"docs": []},
            {"executed": True, "docs": []})


def test_export_node_types_cannot_leave_the_output_directory():
    files = export.build_files("user:a", "md", *_bundle([
        {"id": "x:alice", "type": "../../escaped", "domain": ""}]))
    names = [n for n in files if n.startswith("nodes/")]
    assert names and all(".." not in Path(n).parts for n in names)


def test_export_types_differing_only_in_case_share_a_folder_without_overwrite():
    files = export.build_files("user:a", "md", *_bundle([
        {"id": "concept:X", "type": "concept", "domain": ""},
        {"id": "Concept:x", "type": "Concept", "domain": ""}]))
    names = [n for n in files if n.startswith("nodes/")]
    assert len(names) == 2
    assert len({n.casefold() for n in names}) == 2


def test_export_reserved_device_names_are_renamed():
    files = export.build_files("user:a", "md", *_bundle([
        {"id": "concept:nul.txt", "type": "concept", "domain": ""}]))
    stem = [n for n in files if n.startswith("nodes/")][0].rsplit("/", 1)[1]
    assert stem.split(".", 1)[0].upper() != "NUL"


def test_export_excel_cells_cannot_start_a_formula():
    files = export.build_files("user:a", "csv", *_bundle([
        {"id": "concept:x", "type": "concept", "title": "=HYPERLINK(\"http://x\")",
         "domain": ""}]), excel=True)
    assert "'=HYPERLINK" in files["nodes.csv"].decode("utf-8-sig")
    plain = export.build_files("user:a", "csv", *_bundle([
        {"id": "concept:x", "type": "concept", "title": "=keep", "domain": ""}]))
    assert "'=keep" not in plain["nodes.csv"].decode("utf-8")


def test_export_domain_filter_ignores_a_trailing_slash():
    files = export.build_files("user:a", "csv", *_bundle([
        {"id": "concept:x", "type": "concept", "domain": "vinc/a"}]), domain="vinc/")
    assert "concept:x" in files["nodes.csv"].decode("utf-8")


# ── 옛 동사 9개 == 도구 결과(D7) ─────────────────────────────────────────────
# 지워진 EquivalenceTests(CLI == 사이드카 라우트)의 후속. D7 로 옛 동사가 도구 모양으로
# 옮겨졌으므로, 이제 고정할 등가는 "옛 동사 == 같은 인자로 부른 `vinc call <tool>`" 이다.

_EQUIV = [
    (["search", "--query", "alpha", "--top-k", "3"], "vinc_search", {"query": "alpha", "limit": 3}),
    (["recall", "--limit", "5"], "vinc_recall", {"limit": 5}),
    (["brief", "--query", "alpha"], "vinc_brief", {"query": "alpha"}),
    (["decide", "--topic", "alpha"], "vinc_decide", {"topic": "alpha"}),
    (["domains"], "vinc_domains", {}),
    (["doc", "get", "--id", "doc.alpha"], "vinc_doc_get", {"doc_id": "doc.alpha"}),
    (["doc", "ls"], "vinc_doc_list", {}),
]


@pytest.fixture
def seeded(home, monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "db"))
    monkeypatch.setattr("sys.stdin", io.StringIO("Alpha document body.\n\nMore alpha text."))
    code, out, err = _run(["doc", "put", "--id", "doc.alpha", "--title", "Alpha"])
    assert code == 0, err
    return tmp_path


@pytest.mark.parametrize("argv,tool,arguments", _EQUIV, ids=[e[1] for e in _EQUIV])
def test_old_verbs_print_exactly_the_tool_result(seeded, argv, tool, arguments):
    code_a, verb_out, err_a = _run(["--format", "json", *argv])
    code_b, call_out, err_b = _run(["--format", "json", "call", tool,
                                    "--args", json.dumps(arguments)])
    assert (code_a, verb_out) == (code_b, call_out), (err_a, err_b)
    assert json.loads(verb_out if code_a == 0 else err_a)


def test_doc_put_and_rm_match_their_tools(seeded, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("Beta body."))
    code, out, err = _run(["--format", "json", "doc", "put", "--id", "doc.beta"])
    assert code == 0 and json.loads(out)["doc_id"] == "doc.beta", err
    code, out, err = _run(["--format", "json", "doc", "rm", "--id", "doc.beta"])
    assert code == 2 and "confirmation required" in err     # 터미널이 아니면 --yes 가 필요하다
    code, out, err = _run(["--format", "json", "doc", "rm", "--id", "doc.beta", "--yes"])
    assert code == 0, err
