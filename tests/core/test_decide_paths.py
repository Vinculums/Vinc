# -*- coding: utf-8 -*-
"""VIN-784 결정 문 — 바뀐 경로를 다스리는 현재 기록·결정을 결정론으로 찾는다.

여기서 고정하는 것:
  ① 심어 둔 위반은 다 나오고 대조군은 하나도 안 나온다(정밀도·재현율 1.0, 맞춤 종류까지).
  ② 은퇴한 주장, 시점 결과, 남의 주장, 다른 리포의 같은 경로, 이름만 접두가 같은 경로는 안 나온다.
  ③ 문 셋(MCP·REST·CLI)이 같은 도구 하나를 부른다. CLI 는 200개씩 나눠 보내고 합친다.
  ④ 실행 기록은 쓰기 키일 때만 쓴다. 읽기 전용 키면 쓰지 않고 그렇다고 말한다.
근거: [[vinc.plan.agent-harness-work-order-2026-10-25 §2]]"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace

import pytest

from api import cli, cli_gate, fragment as frag
from api import query as q
from vinc_mcp import rest_routes, tools

_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass

needs_driver = pytest.mark.skipif(_DRIVER is None, reason="임베디드 드라이버(ladybug) 미설치")


@needs_driver
def test_심어_둔_위반은_다_나오고_대조군은_안_나온다():
    from scripts.eval_decision_gate import run
    out = run()
    assert out["executed"], out
    assert (out["precision"], out["recall"], out["label_accuracy"]) == (1.0, 1.0, 1.0), out
    assert out["control_hits"] == [] and out["planted"] >= 15 and out["controls"] >= 15
    # 은퇴 하나(기록)와 시점 결과 하나가 빠졌다고 센다. 조용히 사라지지 않는다.
    assert out["retired_excluded"] == 1 and out["results_excluded"] == 1
    assert out["unqualified_paths"] == ["api/sync.py"]


@pytest.fixture
def store(monkeypatch):
    if _DRIVER is None:
        pytest.skip("임베디드 드라이버(ladybug) 미설치")
    # tmp_path 는 테스트 이름(한글)을 폴더에 싣는데, 윈도 엔진은 그런 경로를 못 연다.
    tmp = tempfile.mkdtemp(prefix="vinc-gate-")
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_DATA_DIR", os.path.join(tmp, "store"))
    from store.backend import open_store
    from store.config import load_config
    s = open_store(load_config())
    s.provision(dim=4)
    yield s
    s.close()
    shutil.rmtree(tmp, ignore_errors=True)


def test_경로_목록이_아니면_실행하지_않는다():
    for bad in (None, [], "vinc:api/x.py", [""], ["  "], [3]):
        out = q.decide_paths(bad, owner="user:a", store=object())
        assert out["executed"] is False and out["error"].startswith("invalid paths"), bad
    out = q.decide_paths([f"vinc:f{i}.py" for i in range(q.DECIDE_PATHS_MAX + 1)],
                         owner="user:a", store=object())
    assert out["executed"] is False and "at most" in out["error"]


def test_도구의_상한은_질의의_상한과_같다():
    assert tools.DECIDE_PATHS_MAX == q.DECIDE_PATHS_MAX
    schema = tools._BY_NAME["vinc_decide_paths"]["inputSchema"]["properties"]["paths"]
    assert schema["maxItems"] == q.DECIDE_PATHS_MAX


def test_결정의_경로는_읽을_때_접힌다(store):
    out = frag.merge_fragment({"owner": "user:a", "nodes": [
        {"id": "decision:d", "type": "decision", "title": "D",
         "props": {"subject_path": ["Vinc:api\\sync.py", "vinc:docs/"]}}]}, store=store)
    # 결정의 subject_path 는 이제 읽힌다: "claim review will not read it" 경고를 내지 않는다.
    assert not any("claim review will not read" in w for w in out.get("warnings") or [])
    got = q.decide_paths(["vinc:api/sync.py"], owner="user:a", store=store)
    assert [i["id"] for i in got["paths"][0]["governed_by"]] == ["decision:d"]
    assert got["paths"][0]["governed_by"][0]["match"] == "exact"


def test_리포_없는_결정의_경로는_경고한다():
    warnings: list[str] = []
    frag._claim_props({"subject_path": "api/sync.py"}, "Decision", "decision:d", warnings)
    assert any("names no repository" in w for w in warnings), warnings
    warnings.clear()
    frag._claim_props({"subject_path": "vinc:api/sync.py", "measured_at": "2026-10-01"},
                      "Decision", "decision:d", warnings)
    assert len(warnings) == 1 and "measured_at" in warnings[0]


def test_은퇴한_결정은_직접_닻이어도_안_나온다(store):
    frag.merge_fragment({"owner": "user:a", "nodes": [
        {"id": "decision:old", "type": "decision", "title": "old",
         "props": {"subject_path": "vinc:a.py", "valid_until": "2026-01-01"}},
        {"id": "decision:new", "type": "decision", "title": "new",
         "props": {"subject_path": "vinc:a.py", "valid_from": "2026-01-01"}}]}, store=store)
    got = q.decide_paths(["vinc:a.py"], owner="user:a", store=store)
    assert [i["id"] for i in got["paths"][0]["governed_by"]] == ["decision:new"]
    assert got["retired_excluded"] == 1 and got["verdict"] == "flag"


def test_목록은_상한에서_잘리고_수는_참값이다(store):
    frag.merge_fragment({"owner": "user:a", "nodes": [
        {"id": f"record:r{i}", "type": "record", "title": f"r{i}",
         "props": {"subject_path": "vinc:api/", "measured_at": f"2026-09-{i + 10:02d}"}}
        for i in range(5)]}, store=store)
    got = q.decide_paths(["vinc:api/x.py"], owner="user:a", store=store, limit=2)
    p = got["paths"][0]
    assert p["count"] == 5 and p["truncated"] is True
    # 최근 측정이 먼저다.
    assert [i["id"] for i in p["governed_by"]] == ["record:r4", "record:r3"]


def test_도구는_경로를_JSON_하나로_보낸다():
    seen = {}

    class Bridge:
        owner = "user:local"

        def request(self, method, path, params=None, body=None, **_kw):
            seen.update(method=method, path=path, params=params)
            return {"kind": "decide_paths", "executed": True, "paths": []}

    tools.call("vinc_decide_paths", {"paths": ["vinc:a.py", "vinc:b/"], "member_limit": 3},
               Bridge())
    assert (seen["method"], seen["path"]) == ("GET", "/decide-paths")
    assert json.loads(seen["params"]["paths"]) == ["vinc:a.py", "vinc:b/"]
    assert seen["params"]["member_limit"] == 3
    for bad in ([], ["x"] * (tools.DECIDE_PATHS_MAX + 1), [""], "vinc:a.py"):
        with pytest.raises(tools.InvalidArgument):
            tools.validate_args("vinc_decide_paths", {"paths": bad})


def test_REST_경로는_본문으로_받는_조회다():
    route = next(r for r in rest_routes.READ_ROUTES if r.tool == "vinc_decide_paths")
    assert (route.method, route.path, route.body) == (
        "POST", "/v1/decide/paths", ("paths", "member_limit"))
    assert tools.tool_tier("vinc_decide_paths") == "viewer"
    # 같은 접두의 GET /v1/decide 와 갈린다.
    assert rest_routes.find_route("GET", "/v1/decide")[0].tool == "vinc_decide"
    assert rest_routes.find_route("POST", "/v1/decide/paths")[0].tool == "vinc_decide_paths"


def test_사이드카_경로가_결정_문을_부른다(store, monkeypatch):
    from api.server import VincApp
    frag.merge_fragment({"owner": "user:a", "nodes": [
        {"id": "record:r", "type": "record", "title": "r",
         "props": {"subject_path": "vinc:api/sync.py", "measured_at": "2026-10-01"}}]},
        store=store)
    app = SimpleNamespace(_read=lambda fn: (200, fn(store)))
    code, out = VincApp.read_decide_paths(
        app, {"owner": "user:a", "paths": json.dumps(["vinc:api/sync.py"])})
    assert code == 200 and out["paths"][0]["governed_by"][0]["id"] == "record:r"
    code, out = VincApp.read_decide_paths(app, {"owner": "user:a", "paths": "[not json"})
    assert code == 400 and out["executed"] is False


# ── CLI ─────────────────────────────────────────────────────────────────────

def test_리포를_붙이고_이미_붙은_것은_둔다():
    assert cli_gate.qualify("api/sync.py", "vinc") == "vinc:api/sync.py"
    assert cli_gate.qualify("vinc-homepage:site/x.js", "vinc") == "vinc-homepage:site/x.js"


def test_diff_reads_changes_since_the_merge_base_with_both_rename_sides(tmp_path):
    # 공통 조상 이후의 파일이고, 이름 바꿈은 옛 경로와 새 경로가 둘 다 나온다.
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    (tmp_path / "a.py").write_text("a\n" * 20, encoding="utf-8")
    (tmp_path / "keep.py").write_text("k\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "topic")
    git("mv", "a.py", "b.py")
    (tmp_path / "new.py").write_text("n\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "topic")
    git("checkout", "-q", "main")
    (tmp_path / "keep.py").write_text("changed on main\n", encoding="utf-8")
    git("commit", "-q", "-am", "main moves on")
    git("checkout", "-q", "topic")
    assert sorted(cli_gate.diff_paths("main", cwd=str(tmp_path))) == ["a.py", "b.py", "new.py"]
    repo = cli_gate.default_repo(cwd=str(tmp_path))
    assert repo and repo == repo.lower() and "/" not in repo


def _result(paths, governed):
    return {"kind": "decide_paths", "executed": True, "verdict": "flag" if governed else "pass",
            "paths": [{"path": p, "governed_by": governed.get(p, []),
                       "count": len(governed.get(p, [])), "truncated": False} for p in paths],
            "path_count": len(paths), "flagged_paths": sum(1 for p in paths if governed.get(p)),
            "governing_count": 0, "retired_excluded": 2, "results_excluded": 1,
            "unqualified_paths": []}


def _gate_main(monkeypatch, argv, *, key):
    calls = []

    def remote(name, data, *, space="personal", **_kw):
        calls.append((name, data, space))
        if name == "vinc_ingest":
            return {"executed": True, "merged": 1, "warnings": []}
        governed = {p: [{"id": "record:r", "kind": "record", "title": "r", "match": "exact",
                         "subject_path": p, "valid_from": "", "valid_until": "",
                         "validity": "current"}] for p in data["paths"] if p.endswith("0.py")}
        return _result(data["paths"], governed)

    monkeypatch.setenv("VINC_API_KEY", key)
    monkeypatch.setattr("api.remote_client.call", remote)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["--target", "account", "--format", "json", "gate", "check", *argv])
    return code, json.loads(out.getvalue() or err.getvalue()), calls


def test_CLI_는_200개씩_나눠_보내고_합친다(monkeypatch):
    paths = [f"api/f{i}.py" for i in range(450)]
    code, out, calls = _gate_main(monkeypatch, ["--paths", *paths, "--repo", "vinc"],
                                  key="vinc_ro_x")
    assert code == 0, out
    assert [len(c[1]["paths"]) for c in calls] == [200, 200, 50]
    assert all(p.startswith("vinc:") for c in calls for p in c[1]["paths"])
    assert out["path_count"] == 450 and out["flagged_paths"] == 45 and out["verdict"] == "flag"
    assert out["governing_count"] == 1 and out["retired_excluded"] == 2


def test_읽기_전용_키로는_실행_기록을_쓰지_않는다(monkeypatch):
    code, out, calls = _gate_main(monkeypatch, ["--paths", "vinc:api/f0.py", "--run-id", "ci-1"],
                                  key="vinc_ro_x")
    assert code == 0 and [c[0] for c in calls] == ["vinc_decide_paths"]
    assert out["run_record"]["recorded"] is False
    assert "read-only" in out["run_record"]["reason"]


def test_쓰기_키면_판정_하나가_실행_기록_하나다(monkeypatch, tmp_path):
    def build(run_id, **kw):
        return {"nodes": [{"id": "episode:run-" + run_id, "type": "episode",
                           "props": {"run_id": run_id, **kw.get("gate_results", {})},
                           "kw": kw}],
                "edges": [{"from": "episode:run-" + run_id, "to": kw["role"], "type": "about"}]}
    monkeypatch.setattr(cli_gate, "_build_run_fragment", lambda: build)
    trace = tmp_path / "gate.json"
    code, out, calls = _gate_main(
        monkeypatch, ["--paths", "vinc:api/f0.py", "--run-id", "ci-1", "--trace", str(trace)],
        key="vinc_sk_x")
    assert code == 0 and [c[0] for c in calls] == ["vinc_decide_paths", "vinc_ingest"]
    assert out["run_record"] == {"recorded": True, "episode_id": "episode:run-ci-1", "warnings": []}
    node = calls[1][1]["nodes"][0]
    assert node["kw"]["role"] == cli_gate.ROLE and node["kw"]["outcome"] == "success"
    assert node["kw"]["gate_results"] == {"decision_gate": "flag"}
    assert node["kw"]["cited_ids"] == ["record:r"] and node["kw"]["domain"] == "vinc/agent/harness"
    # 원본 기록은 파일이고, 실행 기록에는 해시만 간다.
    assert node["kw"]["trace"]["bytes"] == trace.stat().st_size
    assert json.loads(trace.read_text(encoding="utf-8"))["verdict"] == "flag"


def test_요약은_읽을_목록이고_판정을_흉내내지_않는다(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    code, out, _calls = _gate_main(
        monkeypatch, ["--paths", "vinc:api/f0.py", "vinc:api/f1.py", "--summary", str(summary)],
        key="vinc_ro_x")
    text = summary.read_text(encoding="utf-8")
    assert code == 0 and text.startswith("### Decision gate")
    assert "1 of 2 touched paths" in text and "`record:r`" in text
    assert "does not decide" in text


def test_diff_와_paths_는_하나만():
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["--target", "account", "--format", "json", "gate", "check"])
    assert code == 2 and "exactly one of" in err.getvalue()


def test_합칠_것이_없으면_통과다():
    out = cli_gate.merge([])
    assert out["executed"] and out["verdict"] == "pass" and out["path_count"] == 0
    failed = {"kind": "decide_paths", "executed": False, "error": "x"}
    assert cli_gate.merge([_result(["vinc:a"], {}), failed]) is failed
