"""A task suite: one JSON file of tasks with their graders, read from where the person keeps it.

The file is never copied, uploaded or put in the graph. A card names it by id and sha256.

    {"kind": "vinc.agent-tasks.v1", "split": "smoke|search|test", "author": "...",
     "tasks": [{"id": "...", "family": "...", "prompt": "...", "checks": [{...}], "role": "..."}]}

This reader checks the shape it needs to run and grade; the repository's own loader
(``rag_eval/agent_tasks/tasks.py``) is stricter about how a suite is authored.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .grade import Task

KIND = "vinc.agent-tasks.v1"
#: These tasks need more than the one role being measured, so a single-role run leaves them out.
NEEDS_OTHER_ROLES = ("handoff",)
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


class SuiteError(ValueError):
    """The suite file cannot be read as tasks."""


@dataclass(frozen=True)
class Suite:
    path: str
    sha256: str
    split: str
    tasks: tuple[Task, ...] = field(default_factory=tuple)

    @property
    def stem(self) -> str:
        return Path(self.path).stem


def load_suite(path: str | Path) -> Suite:
    p = Path(path)
    try:
        raw = p.read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except OSError as exc:
        raise SuiteError(f"cannot read the suite file: {exc}") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SuiteError(f"{p.name} is not JSON: {exc}") from None
    if not isinstance(doc, dict) or doc.get("kind") != KIND:
        raise SuiteError(f"{p.name}: kind must be {KIND!r}")
    rows = doc.get("tasks")
    if not isinstance(rows, list) or not rows:
        raise SuiteError(f"{p.name}: tasks is empty")
    tasks, seen = [], set()
    for i, row in enumerate(rows):
        where = f"tasks[{i}]"
        if not isinstance(row, dict):
            raise SuiteError(f"{where} is not an object")
        tid = str(row.get("id") or "")
        if not _ID.fullmatch(tid) or tid in seen:
            raise SuiteError(f"{where}: id must be unique lowercase words joined by '-': {tid!r}")
        seen.add(tid)
        prompt, checks = str(row.get("prompt") or "").strip(), row.get("checks")
        if not prompt or not isinstance(checks, list) or not checks:
            raise SuiteError(f"{where} ({tid}): prompt and checks are required")
        for spec in checks:
            if not isinstance(spec, dict) or not spec.get("type"):
                raise SuiteError(f"{where} ({tid}): each check needs a type")
            if spec["type"] in ("regex", "not_regex"):
                try:
                    re.compile(str(spec.get("pattern") or ""))
                except re.error as exc:
                    raise SuiteError(f"{where} ({tid}): pattern is not a regular expression: {exc}") from None
        tasks.append(Task(id=tid, family=str(row.get("family") or ""), prompt=prompt,
                          checks=tuple(checks), role=row.get("role")))
    return Suite(path=str(p), sha256=hashlib.sha256(raw).hexdigest(), split=str(doc.get("split") or ""),
                 tasks=tuple(tasks))
