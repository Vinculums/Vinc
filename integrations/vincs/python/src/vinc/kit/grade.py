"""Machine graders for the answer of one task: what the answer did, never how sure it sounded.

This is the same grading the repository's task harness uses (``rag_eval/agent_tasks/checkers.py``),
kept here so a person can rerun a suite from ``pip install vincs``. A test in the repository
(``tests/core/test_kit_grade_parity.py``) feeds both the same cases, so a change to one that is not
made to the other fails there.

- ``regex`` and ``not_regex``: the answer holds what was required, or holds nothing forbidden. A
  ``not_regex`` that matches counts as a decision violation unless the check says ``"violation":
  false``.
- ``sources``: every id on the last ``Sources:`` line exists in the graph, and enough of them are
  ones the task accepts (``support``). A document mirror ``concept:doc.X``, the document ``X`` and
  any of its chunks ``X#00003`` are the same evidence.
- ``abstain``: a task whose answer is not in the graph. Citing nothing and saying so, or asking back,
  is an abstention; anything else is a made-up answer. The repository harness asks its grading model
  instead when it has one; without one it reads the answer the same way as here.
- ``set_f1`` and ``path``: node ids in the answer against a gold set, or edges between ids in the
  order they appear against the gold edges.
- ``judge``: needs a grading model, which the kit does not call. Such a task is left ungraded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

NODE_ID = re.compile(r"(?<![\w.:\-])((?:concept|decision|record|episode|domain|bridge):"
                     r"\w(?:[\w.\-/]*\w)?)")
_SOURCES = re.compile(r"^\s*\**\s*sources\s*\**\s*:\s*(.*)$", re.I)
_BRACKETED = re.compile(r"\[([^\[\]]+)\]")
_BARE = re.compile(r"\w[\w.:/\-]*\w(?:#\d+)?")
#: A chunk id is ``{doc_id}#{index:05d}``; citing a chunk cites its document.
_CHUNK = re.compile(r"#\d+$")
DOC_MIRROR = "concept:doc."
ABSTAIN = re.compile(
    r"\b(?:i\s+(?:do\s+not|don't|cannot|can't|could\s+not|couldn't)\s+(?:know|find|tell|answer|determine|confirm)"
    r"|not\s+(?:recorded|documented|available|found|in\s+the\s+(?:graph|knowledge|material|sources?))"
    r"|no\s+(?:record|information|data|source|mention)s?\b"
    r"|(?:unable|not\s+able)\s+to\s+(?:find|answer|determine|confirm)"
    r"|(?:does|do)\s+not\s+(?:contain|hold|include|say|mention|record)"
    r"|i\s+(?:do\s+not|don't)\s+have\s+(?:any\s+|the\s+)?(?:record|information|data|details)"
    r"|i\s+(?:do\s+not|don't)\s+have\s+(?:a|an|that|this)\s+(?:\w+\s+){0,3}(?:number|figure|date|value|size)"
    r"|nothing\s+in\s+the\s+(?:material|source|graph|knowledge|context|reference)"
    r"|(?:can't|cannot)\s+say\s+(?:what|which|who|when|how))"
    r"|모르|알 수 없|찾지 못|찾을 수 없|기록(?:되어|돼)?\s*있지\s*않|기록이\s*없|나와\s*있지\s*않|확인할 수 없"
    r"|확인하지 못|답할 수 없|(?:알려|확인해|말씀)\s*드릴\s*수\s*없"
    r"|(?:자료|기록|문서|정보|내용)(?:에서|에는|에|이|가|는)?\s*(?:없|포함되어\s*있지\s*않|보이지\s*않)"
    r"|추측(?:해서|으로)[^.\n]{0,24}않",
    re.I)

#: The checks this grader can run. ``judge`` is not among them.
GRADED = ("regex", "not_regex", "sources", "abstain", "set_f1", "path")


def canon_id(raw: str) -> str:
    s = _CHUNK.sub("", raw.strip().strip("`'\"").rstrip(".,;)"))
    if s.startswith(DOC_MIRROR):
        return "doc:" + s[len(DOC_MIRROR):]
    if NODE_ID.fullmatch(s) or s.startswith("doc:"):
        return s
    return "doc:" + s


def split_product(text: str) -> tuple[str, bool, list[str]]:
    """(body, whether a Sources line exists, its ids). Only the last Sources line is read."""
    lines = (text or "").rstrip().splitlines()
    for i in range(len(lines) - 1, -1, -1):
        m = _SOURCES.match(lines[i])
        if not m:
            continue
        rest = m.group(1).strip()
        body = "\n".join(lines[:i]).strip()
        if not rest or rest.lower().strip(" .[]`*") in ("none", "없음"):
            return body, True, []
        items = _BRACKETED.findall(rest) or _BARE.findall(rest)
        ids = [x.strip() for x in items if x.strip() and x.strip().lower() != "none"]
        return body, True, list(dict.fromkeys(ids))
    return (text or "").strip(), False, []


def node_ids(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in NODE_ID.finditer(text or "")))


def set_f1(got: list[str], gold: list[str]) -> float:
    g, w = set(map(canon_id, got)), set(map(canon_id, gold))
    if not g or not w:
        return 0.0
    tp = len(g & w)
    if not tp:
        return 0.0
    p, r = tp / len(g), tp / len(w)
    return round(2 * p * r / (p + r), 4)


def path_scores(got: list[str], gold: list[str]) -> dict[str, float]:
    seq = [canon_id(x) for x in got]
    seq = [x for i, x in enumerate(seq) if i == 0 or x != seq[i - 1]]
    pred = set(zip(seq, seq[1:]))
    want = set(zip(map(canon_id, gold), map(canon_id, gold[1:])))
    tp = len(pred & want)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(want) if want else 0.0
    f1 = 2 * p * r / (p + r) if tp else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def abstained(body: str, cited: list[str]) -> bool:
    if cited:
        return False
    text = (body or "").strip()
    return bool(ABSTAIN.search(text)) or text.endswith("?")


Resolver = Callable[[str], bool]


@dataclass(frozen=True)
class Task:
    id: str
    family: str
    prompt: str
    checks: tuple[Mapping[str, Any], ...]
    role: str | None = None


def gradable(task: Task) -> bool:
    """False when a check needs a grading model (``judge``) or an unknown check type."""
    return all(c.get("type") in GRADED for c in task.checks)


def grade(task: Task, product: str | None, resolve: Resolver, *, model_failed: bool = False) -> dict:
    """The grade of one answer. ``resolve(id)`` says whether a cited id exists in the graph."""
    if model_failed or not (product or "").strip():
        return {"parse_ok": False, "success": False, "checks": [], "violation": False,
                "cited": [], "cited_missing": [], "sources_line": False, "evidence": None,
                "abstained": False, "fabricated": None}
    body, has_line, cited = split_product(product or "")
    missing = [c for c in cited if not resolve(canon_id(c))]
    out: dict = {"parse_ok": True, "checks": [], "violation": False, "cited": cited,
                 "cited_missing": missing, "sources_line": has_line, "evidence": None}
    for spec in task.checks:
        kind = spec["type"]
        row: dict = {"type": kind}
        if kind == "regex":
            row["passed"] = bool(re.search(spec["pattern"], body, re.I | re.M))
        elif kind == "not_regex":
            hit = bool(re.search(spec["pattern"], body, re.I | re.M))
            row["passed"] = not hit
            if hit and spec.get("violation", True):
                out["violation"] = True
        elif kind == "sources":
            support = {canon_id(s) for s in spec["support"]}
            hits = [c for c in cited if canon_id(c) in support]
            exist = has_line and bool(cited) and not missing
            supported = len(hits) >= int(spec.get("min_support", 1))
            row.update(passed=exist and supported, supported=hits)
            out["evidence"] = {"exist": exist, "supported": supported, "correct": exist and supported}
        elif kind == "abstain":
            row["passed"] = abstained(body, cited)
        elif kind == "set_f1":
            f1 = set_f1(node_ids(body) + cited, spec["gold"])
            row.update(passed=f1 >= float(spec.get("min_f1", 1.0)), f1=f1)
        elif kind == "path":
            sc = path_scores(node_ids(body), spec["gold"])
            row.update(passed=sc["f1"] >= float(spec.get("min_f1", 1.0)), **sc)
        else:
            raise ValueError(f"{task.id}: the check {kind!r} cannot be graded without a grading model")
        out["checks"].append(row)
    out["abstained"] = abstained(body, cited)
    out["fabricated"] = (not out["abstained"]) if task.family == "knowledge-absent" else None
    out["success"] = all(c["passed"] for c in out["checks"])
    return out
