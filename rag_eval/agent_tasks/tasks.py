"""과제 파일 — 과제 하나는 작업 산출물을 요구하는 지시와 그 산출물을 보는 채점기들이다.

파일은 JSON 한 개다::

    {"kind": "vinc.agent-tasks.v1", "split": "smoke|search|test",
     "author": "...", "authored_on": "YYYY-MM-DD", "store": "<과제를 쓴 핀>",
     "tasks": [{"id": "...", "family": "...", "prompt": "...", "checks": [{...}], "role": "..."}]}

분할(split)을 지키는 규칙:
- 파일 하나는 분할 하나다. 실행은 ``--split`` 으로 같은 분할을 명시해야 돈다.
- ``test`` 는 보류 시험 세트다. 실행에 ``--heldout`` 이 따로 필요하고 결과는 후보 디렉터리
  (제안자가 읽는 자리) 밖에 쓴다(:mod:`runner`).
- 두 세트가 같은 과제를 나누면 :func:`overlap` 이 그것을 찾는다.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
from dataclasses import dataclass, field

from rag_eval.agent_tasks import checkers

KIND = "vinc.agent-tasks.v1"
#: 계획의 여섯 갈래. 이름을 고치면 이미 쓴 과제 파일이 전부 거절된다.
FAMILIES = ("keep-decision", "avoid-incident", "give-reason", "use-current", "handoff",
            "knowledge-absent")
SPLITS = ("smoke", "search", "test")
#: 보류 시험 세트가 가져야 하는 지식 없음 과제의 최소 수.
MIN_ABSENT_IN_TEST = 8
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Task:
    id: str
    family: str
    prompt: str
    checks: tuple[dict, ...]
    role: str | None = None


@dataclass(frozen=True)
class TaskSet:
    path: str
    sha256: str
    split: str
    author: str
    authored_on: str
    store: str
    note: str
    tasks: tuple[Task, ...] = field(default_factory=tuple)


class TaskFileError(ValueError):
    pass


def validate(doc: dict) -> list[str]:
    """파일 내용의 오류 목록. 빈 목록이면 쓸 수 있다."""
    bad: list[str] = []
    if not isinstance(doc, dict):
        return ["파일이 JSON 객체가 아니다"]
    if doc.get("kind") != KIND:
        bad.append(f"kind 는 {KIND!r} 여야 한다: {doc.get('kind')!r}")
    if doc.get("split") not in SPLITS:
        bad.append(f"split 은 {SPLITS} 중 하나: {doc.get('split')!r}")
    if not str(doc.get("author") or "").strip():
        bad.append("author 가 비었다")
    if not _DAY.fullmatch(str(doc.get("authored_on") or "")):
        bad.append("authored_on 은 YYYY-MM-DD 여야 한다")
    if not str(doc.get("store") or "").strip():
        bad.append("store(과제를 쓴 핀)가 비었다")
    rows = doc.get("tasks")
    if not isinstance(rows, list) or not rows:
        return bad + ["tasks 가 비었다"]
    seen: set[str] = set()
    for i, t in enumerate(rows):
        where = f"tasks[{i}]"
        if not isinstance(t, dict):
            bad.append(f"{where}: 객체가 아니다")
            continue
        tid = str(t.get("id") or "")
        if not _ID.fullmatch(tid):
            bad.append(f"{where}: id 는 소문자·숫자·하이픈 64자 이내: {tid!r}")
        elif tid in seen:
            bad.append(f"{where}: id 가 겹친다: {tid}")
        seen.add(tid)
        fam = t.get("family")
        if fam not in FAMILIES:
            bad.append(f"{where}: family 는 {FAMILIES} 중 하나: {fam!r}")
        if not str(t.get("prompt") or "").strip():
            bad.append(f"{where}: prompt 가 비었다")
        role = t.get("role")
        if role is not None and not str(role).startswith("concept:agent-"):
            bad.append(f"{where}: role 은 concept:agent- 로 시작해야 한다: {role!r}")
        checks = t.get("checks")
        if not isinstance(checks, list) or not checks:
            bad.append(f"{where}: checks 가 비었다")
            continue
        for k, spec in enumerate(checks):
            bad.extend(f"{where}.checks[{k}]: {e}" for e in checkers.validate_spec(spec))
        kinds = {c.get("type") for c in checks if isinstance(c, dict)}
        # 지식 없음은 행동(기권했는가)으로만 채점한다. 다른 갈래에 기권 채점을 섞으면
        # 답을 몰라서 물러선 것이 성공으로 세어진다.
        if fam == "knowledge-absent" and "abstain" not in kinds:
            bad.append(f"{where}: knowledge-absent 과제는 abstain 채점이 있어야 한다")
        if fam != "knowledge-absent" and "abstain" in kinds:
            bad.append(f"{where}: abstain 채점은 knowledge-absent 과제에만 쓴다")
    if doc.get("split") == "test":
        n_absent = sum(1 for t in rows if isinstance(t, dict) and t.get("family") == "knowledge-absent")
        if n_absent < MIN_ABSENT_IN_TEST:
            bad.append(f"test 세트의 knowledge-absent 과제가 {n_absent}개다(최소 {MIN_ABSENT_IN_TEST})")
    return bad


def load(path: str | pathlib.Path) -> TaskSet:
    p = pathlib.Path(path)
    raw = p.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskFileError(f"{p}: JSON 이 아니다: {exc}") from exc
    bad = validate(doc)
    if bad:
        raise TaskFileError(f"{p}:\n  " + "\n  ".join(bad))
    tasks = tuple(Task(id=t["id"], family=t["family"], prompt=t["prompt"].strip(),
                       checks=tuple(t["checks"]), role=t.get("role"))
                  for t in doc["tasks"])
    return TaskSet(path=str(p), sha256=hashlib.sha256(raw).hexdigest(), split=doc["split"],
                   author=doc["author"], authored_on=doc["authored_on"], store=doc["store"],
                   note=str(doc.get("note") or ""), tasks=tasks)


def require_split(ts: TaskSet, split: str, *, heldout: bool) -> None:
    """실행이 밝힌 분할과 파일의 분할이 같고, 보류 세트면 따로 허락을 받았는가."""
    if ts.split != split:
        raise TaskFileError(f"과제 파일의 split 은 {ts.split!r} 인데 실행은 {split!r} 로 불렸다")
    if split == "test" and not heldout:
        raise TaskFileError("test 세트는 보류 시험 세트다 — --heldout 없이 돌리지 않는다")
    if split != "test" and heldout:
        raise TaskFileError("--heldout 은 test 세트에만 쓴다")


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def overlap(a: TaskSet, b: TaskSet) -> list[str]:
    """두 세트가 함께 가진 과제(같은 id 또는 정규화한 지시문이 같은 것)."""
    ids = {t.id for t in a.tasks} & {t.id for t in b.tasks}
    prompts = {_norm(t.prompt) for t in a.tasks} & {_norm(t.prompt) for t in b.tasks}
    return sorted(ids) + [f"prompt: {p[:80]}" for p in sorted(prompts)]
