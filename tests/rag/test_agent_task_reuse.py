"""끊긴 회차의 원본 기록 다시 쓰기 — 다시 쓸 기록 고르기와 출력 되살리기(vinc-client 없이).

실행 행을 다시 만드는 쪽은 `integrations/parity/test_task_harness_reuse.py`.
"""
from __future__ import annotations

import json

import pytest

from rag_eval.agent_tasks import metrics, reuse


def test_traces_that_ended_in_an_error_or_without_a_final_are_run_again(tmp_path):
    start = {"t": "start", "task": "t1", "arm": "A0", "run": 1}
    model = {"t": "model", "tokens_in": 1, "tokens_out": 1, "model_version": "m", "list_cost_usd": None}
    lines = {"cut.jsonl": [start, model],
             "err.jsonl": [{**start, "run": 2}, model,
                           {"t": "final", "text": "", "model_error": "boom", "host_error": ""}],
             "host.jsonl": [{**start, "run": 3},
                            {"t": "final", "text": "", "model_error": "", "host_error": "X"}],
             "ok.jsonl": [{**start, "run": 4}, model,
                          {"t": "final", "text": "fine", "model_error": "", "host_error": ""}]}
    for name, events in lines.items():
        (tmp_path / name).write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (tmp_path / "bad.jsonl").write_text("{not json\n", encoding="utf-8")
    index = reuse.TraceReuse([tmp_path])
    assert len(index) == 1 and index.skipped == 4
    _run, out = index.take([{**start, "run": 4}])
    assert out.final_text == "fine" and out.steps == 1 and out.list_cost_usd is None
    with pytest.raises(FileNotFoundError):
        reuse.TraceReuse([tmp_path / "missing"])


def test_the_mean_latency_skips_reused_rows():
    rows = [{"latency_s": 2.0}, {"latency_s": None}, {"latency_s": 4.0}]
    assert metrics._mean([r["latency_s"] for r in rows if r.get("latency_s") is not None]) == 3.0
