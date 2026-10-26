"""``run_suite``: run a task suite as one role with the person's own model, grade it with the
machine graders, and write the result as an evaluation card into the person's own graph.

Each task runs ``runs`` times (three by default) through :func:`vinc.kit.run_role`, so every run has
the role's gates, budget guard and raw trace. Per run record is off by default: a suite is dozens of
runs and the card is the result. Nothing here calls a model of Vinc's, and the suite file stays where
it is.

What the numbers mean:

- ``pass_1``: successful runs out of graded runs. ``pass_k``: tasks where every one of the ``runs``
  runs succeeded, out of tasks where every run was graded.
- A run that raised (model or network error) is *ungraded* and left out of the rates; a run the role's
  gates blocked or held for a person is graded and failed.
- ``violation`` is among decision tasks, ``fabrication`` among tasks whose answer is not in the graph.
- Tasks that need a grading model (``judge``) or several roles (``handoff``) are skipped and counted.
- ``cost_per_success_usd`` is left empty: the kit does not know a model's price.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from vinc.sdk import VincClient
from vinc.sdk.errors import VincNotFound
from vinc.sdk.eval_card import record_eval_card

from . import grade as graders
from .run import DEFAULT_TRACE_DIR, KitError, model_family, run_role
from .suite import NEEDS_OTHER_ROLES, Suite, SuiteError, load_suite

#: The families whose "violation" counts, as in the repository's harness.
KEEP_FAMILIES = ("keep-decision", "use-current")
UNPINNED = "unpinned"
MAX_RUNS = 60


@dataclass
class EvalRun:
    """What a suite run produced: the card's result cell, per-run rows and the write's answer."""

    role_id: str
    suite: dict[str, Any]
    cell: dict[str, Any]
    rows: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    card: dict[str, Any] | None = None
    card_error: str | None = None
    store_fingerprint: str = UNPINNED


def _rate(k: int, n: int) -> dict[str, int]:
    return {"k": k, "n": n}


def make_resolver(client: VincClient) -> Callable[[str], bool]:
    """Whether a cited id exists in this graph. A missing one is False; a hub failure raises."""
    seen: dict[str, bool] = {}

    def resolve(cid: str) -> bool:
        if cid not in seen:
            try:
                if cid.startswith("doc:"):
                    client.document(cid[len("doc:"):])
                    seen[cid] = True
                else:
                    seen[cid] = client.node(cid).get("found") is not False
            except VincNotFound:
                seen[cid] = False
        return seen[cid]

    return resolve


def summarize(rows: Sequence[dict[str, Any]], tasks: int, runs: int) -> dict[str, Any]:
    """The card cell's rates from per-run rows (``outcome``, ``task``, ``family``, ``grade``)."""
    graded = [r for r in rows if r["outcome"] != "error"]
    wins = sum(1 for r in graded if r["outcome"] == "success")
    by_task: dict[str, list[dict]] = {}
    for r in rows:
        by_task.setdefault(r["task"], []).append(r)
    full = [v for v in by_task.values() if len(v) == runs and all(x["outcome"] != "error" for x in v)]
    answered = [r for r in graded if r["grade"] is not None]
    keep = [r for r in answered if r["family"] in KEEP_FAMILIES]
    absent = [r for r in answered if r["family"] == "knowledge-absent"]
    return {"tasks": tasks, "runs_per_task": runs,
            "pass_1": _rate(wins, len(graded)),
            "pass_k": _rate(sum(1 for v in full if all(x["outcome"] == "success" for x in v)), len(full)),
            "violation": _rate(sum(1 for r in keep if r["grade"]["violation"]), len(keep)) if keep else None,
            "fabrication": (_rate(sum(1 for r in absent if r["grade"]["fabricated"]), len(absent))
                            if absent else None),
            "cost_per_success_usd": None,
            "ungraded": len(rows) - len(graded)}


def run_suite(role: str, suite: Suite | str | Path, *, model: Any, adapter: str = "langgraph",
              runs: int = 3, client: VincClient | None = None, api_key: str | None = None,
              base_url: str | None = None, space: str | None = None, suite_id: str | None = None,
              store_fingerprint: str = UNPINNED, limit: int | None = None, write_card: bool = True,
              record_runs: bool = False, trace_dir: str | Path | None = DEFAULT_TRACE_DIR,
              confirm: bool = False, progress: Callable[[str], None] | None = None,
              **build_kwargs: Any) -> EvalRun:
    """Run every gradable task of ``suite`` ``runs`` times as ``role`` and write one card.

    More than :data:`MAX_RUNS` model runs need ``confirm=True``: a suite is billed to the person's own
    model account, so a long one is never a surprise. ``limit`` runs only the first tasks.
    """
    if not isinstance(runs, int) or isinstance(runs, bool) or runs < 1:
        raise KitError("runs must be a positive integer")
    suite = suite if isinstance(suite, Suite) else load_suite(suite)
    chosen, skipped = [], []
    for task in suite.tasks:
        (skipped if task.family in NEEDS_OTHER_ROLES or not graders.gradable(task) else chosen).append(task)
    if limit is not None:
        chosen = chosen[:limit]
    if not chosen:
        raise KitError("no task of this suite can be graded here (every one needs a grading model "
                       "or several roles)")
    if len(chosen) * runs > MAX_RUNS and not confirm:
        raise KitError(f"this is {len(chosen) * runs} model runs ({len(chosen)} tasks x {runs}); "
                       f"pass --yes to run more than {MAX_RUNS}, or --limit to run fewer tasks")
    sync = client or VincClient(api_key, base_url=base_url, space=space)
    resolve = make_resolver(sync)
    sid = suite_id or suite.stem
    say = progress or (lambda line: print(line, file=sys.stderr, flush=True))
    rows: list[dict[str, Any]] = []
    role_id, family, total = "", model_family(model), len(chosen) * runs
    for t_index, task in enumerate(chosen):
        for i in range(runs):
            rid = f"eval-{sid}-{task.id}-{i + 1}"[:128]
            row: dict[str, Any] = {"task": task.id, "family": task.family, "run": i + 1,
                                   "outcome": "error", "grade": None}
            try:
                done = run_role(role, task.prompt, model=model, adapter=adapter, client=sync,
                                space=space, run_id=rid, record=record_runs, trace_dir=trace_dir,
                                **build_kwargs)
            except KitError:
                raise
            except Exception as exc:  # 모델·네트워크 오류: 그 실행은 채점하지 않고 센다.
                row["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            else:
                role_id = done.run.role_id
                if done.run.status == "done":
                    row["grade"] = graders.grade(task, done.run.output, resolve)
                    row["outcome"] = "success" if row["grade"]["success"] else "failure"
                else:
                    row["outcome"] = done.run.status
                    row["blocked_by"] = list(done.run.gate.failed_checks) if done.run.gate else []
            rows.append(row)
            say(f"[{t_index * runs + i + 1}/{total}] {task.id} run {i + 1}: {row['outcome']}")
    cell = {"adapter": adapter, "model_family": family,
            **summarize(rows, len(chosen), runs), "skipped": len(skipped)}
    result = EvalRun(role_id=role_id, suite={"id": sid, "hash": suite.sha256, "tasks": len(chosen)},
                     cell=cell, rows=rows, skipped=[t.id for t in skipped],
                     store_fingerprint=store_fingerprint)
    if write_card and role_id:
        try:
            from vinc import __version__ as kit_version
            result.card = record_eval_card(
                sync, role_id, suite=result.suite, results=[cell],
                store_fingerprint=store_fingerprint, harness_version=f"vincs-kit-{kit_version}",
                space=space)
        except Exception as exc:  # 카드를 못 써도 잰 값은 돌려준다.
            result.card_error = f"{type(exc).__name__}: {str(exc)[:200]}"
    return result
