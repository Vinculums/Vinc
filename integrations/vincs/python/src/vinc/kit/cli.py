"""``vinc agent run``: run one task as a role, on this machine, with your own model and key."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import run as kit_run

#: 종료 코드: 끝났다 0 · 게이트가 막았다 3 · 사람을 기다린다 4 · 시작하지 못했다 2.
EXIT = {"done": 0, "blocked": 3, "waiting": 4}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vinc agent", description=__doc__)
    sub = parser.add_subparsers(dest="agentcmd", required=True)
    run = sub.add_parser("run", help="run one task as a role and write the run record")
    run.add_argument("role", help="agent slug or concept:agent- id, for example dev-core")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--task", help="what to do")
    source.add_argument("--task-file", metavar="FILE", help="read the task from a file; - reads stdin")
    run.add_argument("--model", required=True, metavar="PROVIDER:NAME",
                     help="your model, for example anthropic:claude-sonnet-5-5 (its key comes from "
                          "your environment)")
    run.add_argument("--adapter", default="langgraph", choices=kit_run.ADAPTERS,
                     help="the framework to run on (default langgraph)")
    run.add_argument("--run-id", default=None, dest="run_id",
                     help="your own id; the record of a rerun with the same id merges into one")
    run.add_argument("--space", default=None, help="a team id; omit for your personal graph")
    run.add_argument("--trace-dir", default=kit_run.DEFAULT_TRACE_DIR, dest="trace_dir",
                     help="where the raw trace file goes (default ./%s)" % kit_run.DEFAULT_TRACE_DIR)
    run.add_argument("--no-trace", action="store_true", dest="no_trace", help="write no trace file")
    run.add_argument("--no-record", action="store_true", dest="no_record",
                     help="do not write the run record")
    run.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser


def _task(args: argparse.Namespace) -> str:
    if args.task is not None:
        return args.task
    if args.task_file == "-":
        return sys.stdin.read()
    with open(args.task_file, encoding="utf-8") as handle:
        return handle.read()


def _summary(done: kit_run.KitRun) -> dict:
    result = done.run
    return {"run_id": result.run_id, "role": result.role_id, "status": result.status,
            "outcome": result.outcome, "adapter": done.adapter, "model_family": done.model_family,
            "output": result.output, "cited_ids": list(result.cited_ids),
            "failed_checks": list(result.gate.failed_checks) if result.gate else [],
            "person_only_actions": list(result.gate.person_only_actions) if result.gate else [],
            "steps": done.steps, "tool_calls": done.tool_calls,
            "trace": str(done.trace) if done.trace else None,
            "recorded": result.record is not None, "record_error": result.record_error}


def _print(summary: dict) -> None:
    print(f"run: {summary['run_id']}")
    print(f"role: {summary['role']}")
    print(f"status: {summary['status']} ({summary['adapter']}, {summary['model_family']})")
    if summary["trace"]:
        print(f"trace: {summary['trace']}")
    if summary["recorded"]:
        print(f"record: written; read it with: vinc run show {summary['run_id']}")
    else:
        print("record: not written" + (f" ({summary['record_error']})" if summary["record_error"] else ""))
    if summary["status"] == "blocked":
        print("blocked by: " + (", ".join(summary["failed_checks"]) or "a gate"))
    if summary["status"] == "waiting":
        print("a person decides first: " + (", ".join(summary["person_only_actions"]) or "a gate asked"))
    if summary["output"]:
        print("---")
        print(summary["output"])


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        done = kit_run.run_role(
            args.role, _task(args), model=args.model, adapter=args.adapter, space=args.space,
            run_id=args.run_id, record=not args.no_record,
            trace_dir=None if args.no_trace else args.trace_dir)
    except kit_run.KitError as exc:
        print(f"vinc agent: {exc}", file=sys.stderr)
        return 2
    summary = _summary(done)
    print(json.dumps(summary, ensure_ascii=False, indent=2)) if args.json else _print(summary)
    return EXIT.get(summary["status"], 1)
