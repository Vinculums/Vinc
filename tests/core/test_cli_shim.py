"""`vinc shim ls|watch`: 심 레코드를 읽고 은퇴 초안을 쓴다. 모델 호출도 라이브 쓰기도 없다."""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from api import cli, cli_output, fragment, remote_client, shim_registry as reg
from api.server import VincApp
from store.config import load_config

ROOT = Path(__file__).resolve().parents[2]
ROLE = "concept:agent-reviewer"
INCIDENT = "record:review-claimed-green-tests-without-a-run"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["--format", "json", *argv])
    text = out.getvalue() if code == 0 else err.getvalue()
    lines = text.splitlines(True)
    start = next(i for i, line in enumerate(lines) if line.startswith("{"))
    return code, json.loads("".join(lines[start:]))


def test_cli_copy_is_generated_from_the_package_module():
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_shim_registry
    assert sync_shim_registry.TARGET.read_bytes().replace(b"\r\n", b"\n") == \
        sync_shim_registry.expected(), (
        "api/shim_registry.py 가 vinc_client/shims.py 에서 생성된 결과와 다르다. 원본만 고치고 "
        "python -X utf8 scripts/sync_shim_registry.py 로 다시 생성할 것")


def shim(slug, **change):
    args = {"target": ROLE, "patches": "claims tests passed", "mechanism": "prompt_line",
            "installed_because": INCIDENT, "text": "Say which tests you ran.",
            "valid_from": "2026-09-01", "domain": "vinc/agent/harness"}
    return reg.build_shim_fragment(slug, **{**args, **change})


def run_episode(run_id, *, family="claude", fired=None, date="2026-10-20"):
    props = {"run_id": run_id, "role": ROLE, "outcome": "success", "model_family": family}
    if fired:
        props["shims_fired"] = fired
    eid = "episode:run-" + run_id
    return ({"id": eid, "type": "episode", "title": f"Run {run_id}", "date": date,
             "tags": ["agent-run"], "props": props},
            {"from": eid, "to": ROLE, "type": "about", "evidence": "the run's role"})


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    data = str(tmp_path / "db")
    monkeypatch.setenv("MEMORY_DATA_DIR", data)
    monkeypatch.setenv("VINC_TARGET", "local")
    monkeypatch.setattr("api.cli_tools.attached_bridge", lambda args: None)
    nodes = [{"id": ROLE, "type": "concept", "title": "Reviewer", "domain": "vinc/agent/roles",
              "props": {"mission": "Reviews one diff.", "tier": "verify", "rung": "fast",
                        "never": ["merge"], "person_only": []}},
             {"id": INCIDENT, "type": "record", "title": "A review claimed green tests",
              "domain": "vinc/agent/harness"}]
    edges = []
    for part in (shim("quiet"), shim("busy"), shim("retired"), shim("gpt-only", applies_to=["gpt"])):
        nodes += part["nodes"]
        edges += part["edges"]
    nodes[-2]["valid_until"] = "2026-10-01"          # retired
    for episode, about in (run_episode("c1", fired={"record:shim-busy": 2}), run_episode("c2"),
                           run_episode("old", date="2026-08-01")):
        nodes.append(episode)
        edges.append(about)
    app = VincApp.open(load_config({"data_dir": data}))
    try:
        result = fragment.merge_fragment({"owner": "tenant:local", "nodes": nodes,
                                          "edges": edges}, store=app.store)
        assert result["executed"] and not result.get("warnings"), result
    finally:
        app.close()
    return data


def set_state(data, decision_id, state):
    app = VincApp.open(load_config({"data_dir": data}))
    try:
        result = fragment.merge_fragment({"owner": "tenant:local", "nodes": [
            {"id": decision_id, "type": "decision", "props": {"state": state}}]}, store=app.store)
        assert result["executed"], result
    finally:
        app.close()


def test_local_ls_lists_the_current_shims_of_a_role(local_store):
    code, payload = run("shim", "ls", "--role", "agent-reviewer", "--today", "2026-10-25")
    assert code == 0, payload
    assert [row["id"] for row in payload["shims"]] == [
        "record:shim-busy", "record:shim-gpt-only", "record:shim-quiet"]
    quiet = payload["shims"][-1]
    assert quiet == {"id": "record:shim-quiet", "mechanism": "prompt_line", "applies_to": [],
                     "installed_because": INCIDENT, "valid_from": "2026-09-01",
                     "attached_to": [ROLE], "patches": "claims tests passed"}
    code, payload = run("shim", "ls", "--role", ROLE, "--model-family", "claude",
                        "--today", "2026-10-25")
    assert [row["id"] for row in payload["shims"]] == ["record:shim-busy", "record:shim-quiet"]
    code, payload = run("shim", "ls", "--role", "agent-nobody")
    assert code == 1 and payload["error"]["code"] == "not_found"


def test_local_watch_drafts_and_writes_only_with_write(local_store):
    argv = ["shim", "watch", "--window-days", "30", "--min-runs", "2", "--today", "2026-10-25"]
    code, payload = run(*argv)
    assert code == 0, payload
    assert payload["runs"] == 2 and payload["families_in_use"] == ["claude"]
    assert [d["nodes"][0]["id"] for d in payload["drafts"]] == ["decision:retire-shim-quiet"]
    assert "written" not in payload
    code, read = run("node", "get", "decision:retire-shim-quiet")
    assert code == 1                                     # 아직 아무것도 쓰지 않았다
    code, payload = run(*argv, "--write")
    assert code == 0 and payload["written"] == ["decision:retire-shim-quiet"], payload
    code, read = run("node", "get", "decision:retire-shim-quiet")
    assert code == 0
    props = read["node"]["props"]
    assert props["state"] == "draft" and props["shim"] == "record:shim-quiet"
    assert props["counts"] == {"claude": {"runs": 2, "firings": 0}}
    code, edges = run("edges", "decision:retire-shim-quiet", "--dir", "out", "--type", "ABOUT")
    assert [e["to"] for e in edges["edges"]] == [ROLE]
    # 사람이 판정한 결정은 다시 돌려도 덮지 않는다.
    set_state(local_store, "decision:retire-shim-quiet", "ratified")
    code, payload = run(*argv, "--write")
    assert code == 0 and payload["written"] == []
    assert payload["kept"] == [{"id": "decision:retire-shim-quiet", "state": "ratified"}]
    code, read = run("node", "get", "decision:retire-shim-quiet")
    assert read["node"]["props"] == {"state": "ratified"}
    # 계열을 직접 대면 그 계열마다 실행 수를 채워야 한다.
    code, payload = run(*argv, "--families", "claude,gpt")
    assert code == 0 and payload["drafts"] == []


class _Response:
    def __init__(self, data):
        self.data = data
        self.status = 200
        self.headers = Message()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.data).encode("utf-8")


def _account(monkeypatch, key):
    seen = []
    shim_record = reg.build_shim_fragment(
        "quiet", target=ROLE, patches="claims tests passed", mechanism="nudge",
        installed_because=INCIDENT, text="The user is waiting.", valid_from="2026-09-01")
    stored = {**shim_record["nodes"][0], "valid_until": None}
    episode, _ = run_episode("c1")

    def receive(request, *, timeout):
        parts = urlsplit(request.full_url)
        path, query = unquote(parts.path), parse_qs(parts.query)
        body = json.loads(request.data) if request.data else None
        seen.append((request.get_method(), path, query, body))
        if path == "/v1/episodes":
            return _Response({"rows": [{"id": episode["id"], "date": "2026-10-20"}],
                              "has_more": False, "next_cursor": None})
        if path == "/v1/edges/query":
            return _Response({"edges": [{"from": stored["id"], "to": ROLE,
                                         "type": "ATTACHES_TO", "subtype": "relates_to"}],
                              "missing_nodes": [reg.HARNESS_CONCEPT]})
        if path == "/v1/nodes/" + episode["id"]:
            return _Response({"found": True, "node": episode})
        if path == "/v1/nodes/" + stored["id"]:
            return _Response({"found": True, "node": stored})
        if path == "/v1/fragments":
            return _Response({"merged": {"nodes": {"created": 1}}, "warnings": []})
        raise HTTPError(request.full_url, 404, "Not Found", Message(), io.BytesIO(json.dumps(
            {"error": {"code": "not_found", "message": "No node.", "details": {}}}).encode()))

    monkeypatch.setattr(remote_client, "urlopen", receive)
    monkeypatch.setenv("VINC_API_KEY", key)
    monkeypatch.setenv("VINC_TARGET", "account")
    monkeypatch.setattr("api.cli_tools._bridge", lambda args: pytest.fail("local store"))
    return seen


def test_account_watch_reads_through_v1_and_writes_with_a_full_key(monkeypatch):
    seen = _account(monkeypatch, "vinc_sk_test")
    argv = ["shim", "watch", "--window-days", "30", "--min-runs", "1", "--today", "2026-10-25",
            "--space", "team-1"]
    code, payload = run(*argv)
    assert code == 0, payload
    assert [d["nodes"][0]["id"] for d in payload["drafts"]] == ["decision:retire-shim-quiet"]
    assert all(method != "POST" or path != "/v1/fragments" for method, path, _, _ in seen)
    recall = next(q for m, p, q, _ in seen if p == "/v1/episodes")
    assert recall == {"tags": ["agent-run"], "limit": ["200"], "space": ["team-1"]}
    seen.clear()
    code, payload = run(*argv, "--write")
    assert code == 0 and payload["written"] == ["decision:retire-shim-quiet"], payload
    written = [b for m, p, _, b in seen if p == "/v1/fragments"]
    assert len(written) == 1 and written[0]["nodes"][0]["props"]["state"] == "draft"
    assert written[0]["edges"][0]["type"] == "about"


def test_a_read_only_key_cannot_write_and_sends_nothing(monkeypatch):
    seen = _account(monkeypatch, "vinc_ro_test")
    code, payload = run("shim", "watch", "--window-days", "7", "--min-runs", "1", "--write")
    assert code == 4 and payload["error"]["code"] == "read_only_key"
    assert seen == []
    code, payload = run("shim", "ls", "--role", "agent-reviewer", "--today", "2026-10-25")
    assert code == 0 and [row["id"] for row in payload["shims"]] == ["record:shim-quiet"]


@pytest.mark.parametrize("argv,needle", [
    (["shim", "ls", "--today", "tomorrow"], "--today"),
    (["shim", "watch", "--window-days", "0", "--min-runs", "1"], "--window-days"),
])
def test_usage_errors_exit_two(monkeypatch, argv, needle):
    monkeypatch.setenv("VINC_TARGET", "local")
    monkeypatch.setattr("api.cli_tools._bridge", lambda args: pytest.fail("no store call"))
    code, payload = run(*argv)
    assert code == 2 and needle in payload["error"]["message"]


def test_human_views(capsys):
    cli_output.configure("human", "shim ls")
    cli_output.emit({"kind": "shim.ls", "executed": True, "today": "2026-10-25", "count": 1,
                     "shims": [{"id": "record:shim-quiet", "mechanism": "nudge",
                                "applies_to": [], "installed_because": INCIDENT,
                                "valid_from": "2026-09-01", "attached_to": [ROLE]}]})
    assert capsys.readouterr().out == (
        "shims: 1; today: 2026-10-25\nrecord:shim-quiet\tnudge\tall families\t"
        f"because {INCIDENT}\tsince 2026-09-01\t{ROLE}\n")
    cli_output.configure("human", "shim watch")
    cli_output.emit({"kind": "shim.watch", "executed": True, "runs": 3,
                     "window_start": "2026-09-25", "families_in_use": ["claude"],
                     "shims": ["record:shim-quiet"], "drafts": [{"nodes": [
                         {"id": "decision:retire-shim-quiet", "title": "Draft: retire it"}]}],
                     "written": [], "kept": [{"id": "decision:retire-shim-quiet",
                                              "state": "ratified"}], "warnings": []})
    assert capsys.readouterr().out == (
        "runs: 3 since 2026-09-25; families in use: claude; shims: 1; drafts: 1\n"
        "decision:retire-shim-quiet\tDraft: retire it\n"
        "written: 0; kept as decided: decision:retire-shim-quiet\n")
