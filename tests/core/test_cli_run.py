"""`vinc run ls|start|submit|show`: 플래그가 도구 인자로 옮겨지는 길.

근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

import pytest

from api import cli_tools
from types import SimpleNamespace


def args(runcmd, **kw):
    base = {"cmd": "run", "runcmd": runcmd, "owner": None}
    return SimpleNamespace(**{**base, **kw})


def test_ls_maps_cited_role_and_limit():
    name, data, guard = cli_tools._tool_request(
        args("ls", cited="record:x", role="agent-x", limit=5))
    assert (name, guard) == ("vinc_runs", None)
    assert data == {"cited": "record:x", "role": "agent-x", "limit": 5}
    assert cli_tools._tool_request(args("ls", cited=None, role=None, limit=None))[1] == {}


def test_show_asks_for_one_run():
    assert cli_tools._tool_request(args("show", run="r-1"))[:2] == ("vinc_runs", {"run": "r-1"})


def test_start_keeps_only_the_flags_that_were_given():
    name, data, _ = cli_tools._tool_request(
        args("start", role="agent-dev-core", task="Review it.", subject=None, run_id="r-1"))
    assert name == "vinc_run_start"
    assert data == {"role": "agent-dev-core", "task": "Review it.", "run_id": "r-1"}


def test_submit_reads_the_output_file_and_parses_the_json_flags(tmp_path):
    out = tmp_path / "out.md"
    out.write_text("The change reads correctly.", encoding="utf-8")
    name, data, _ = cli_tools._tool_request(args(
        "submit", run="r-1", output=str(out), answer=None, abandon=False, cited=["record:x"],
        person_only_requests='[{"action":"merge","reason":"green"}]', usage='{"tool_calls":2}'))
    assert name == "vinc_run_submit"
    assert data == {"run": "r-1", "output": "The change reads correctly.", "cited_ids": ["record:x"],
                    "person_only_requests": [{"action": "merge", "reason": "green"}],
                    "usage": {"tool_calls": 2}}
    name, data, _ = cli_tools._tool_request(args(
        "submit", run="r-1", output=None, answer="approve", abandon=False, cited=None,
        person_only_requests=None, usage=None))
    assert data == {"run": "r-1", "answer": "approve"}
    name, data, _ = cli_tools._tool_request(args(
        "submit", run="r-1", output=None, answer=None, abandon=True, cited=None,
        person_only_requests=None, usage=None))
    assert data == {"run": "r-1", "abandon": True}


def test_a_bad_json_flag_is_a_usage_error():
    with pytest.raises(ValueError, match="--usage"):
        cli_tools._tool_request(args(
            "submit", run="r-1", output=None, answer="approve", abandon=False, cited=None,
            person_only_requests=None, usage="{nope"))
