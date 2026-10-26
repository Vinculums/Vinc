"""``vinc agent run``: run one task as a role, on this machine, with your own model and key."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import evalrun
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


def _eval_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vinc eval", description=evalrun.__doc__)
    sub = parser.add_subparsers(dest="evalcmd", required=True)
    run = sub.add_parser("run", help="run a task suite as a role and write an evaluation card")
    run.add_argument("role", help="agent slug or concept:agent- id")
    run.add_argument("--suite", required=True, metavar="FILE",
                     help="the task suite JSON file; it is read in place and never uploaded")
    run.add_argument("--model", required=True, metavar="PROVIDER:NAME", help="your model")
    run.add_argument("--adapter", default="langgraph", choices=kit_run.ADAPTERS,
                     help="the framework to run on (default langgraph)")
    run.add_argument("--runs", type=int, default=3, help="runs per task (default 3, for pass^3)")
    run.add_argument("--suite-id", default=None, dest="suite_id",
                     help="the name the card gives the suite (default: the file name)")
    run.add_argument("--store-fingerprint", default=evalrun.UNPINNED, dest="store_fingerprint",
                     help="what graph the answers came from. Cards are comparable only when this "
                          "and the suite hash match; the default %r is not comparable" % evalrun.UNPINNED)
    run.add_argument("--limit", type=int, default=None, help="run only the first N tasks")
    run.add_argument("--space", default=None, help="a team id; omit for your personal graph")
    run.add_argument("--trace-dir", default=kit_run.DEFAULT_TRACE_DIR, dest="trace_dir")
    run.add_argument("--no-trace", action="store_true", dest="no_trace", help="write no trace files")
    run.add_argument("--record-runs", action="store_true", dest="record_runs",
                     help="also write a run record for every run (off by default)")
    run.add_argument("--no-card", action="store_true", dest="no_card",
                     help="print the result and write nothing to Vinc")
    run.add_argument("--yes", action="store_true",
                     help="allow more than %d model runs in one go" % evalrun.MAX_RUNS)
    run.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser


def _pct(rate: dict | None) -> str:
    if not rate or not rate["n"]:
        return "unknown"
    return f"{rate['k']}/{rate['n']} ({100 * rate['k'] / rate['n']:.1f}%)"


def eval_main(argv: Sequence[str] | None = None) -> int:
    """``vinc eval run``."""
    args = _eval_parser().parse_args(argv)
    try:
        done = evalrun.run_suite(
            args.role, args.suite, model=args.model, adapter=args.adapter, runs=args.runs,
            space=args.space, suite_id=args.suite_id, store_fingerprint=args.store_fingerprint,
            limit=args.limit, write_card=not args.no_card, record_runs=args.record_runs,
            trace_dir=None if args.no_trace else args.trace_dir, confirm=args.yes)
    except (kit_run.KitError, evalrun.SuiteError) as exc:
        print(f"vinc eval: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"role": done.role_id, "suite": done.suite, "result": done.cell,
                          "skipped": done.skipped, "store_fingerprint": done.store_fingerprint,
                          "card": done.card, "card_error": done.card_error,
                          "rows": done.rows}, ensure_ascii=False, indent=2, default=str))
    else:
        cell = done.cell
        print(f"role: {done.role_id}")
        print(f"suite: {done.suite['id']} ({done.suite['tasks']} tasks, sha256 {done.suite['hash'][:12]})")
        print(f"pass^1: {_pct(cell['pass_1'])}   pass^{cell['runs_per_task']}: {_pct(cell['pass_k'])}")
        print(f"violation: {_pct(cell['violation'])}   fabrication: {_pct(cell['fabrication'])}")
        print(f"ungraded runs: {cell['ungraded']}   skipped tasks: {cell['skipped']}")
        if done.store_fingerprint == evalrun.UNPINNED:
            print("note: --store-fingerprint was not given, so this card is not comparable with others")
        if done.card is not None:
            print("card: written; read it with: vinc eval cards " + done.role_id)
        elif done.card_error:
            print(f"card: not written ({done.card_error})")
    return 1 if done.card_error else 0


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
