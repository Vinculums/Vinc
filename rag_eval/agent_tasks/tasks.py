"""과제 파일 — 과제 하나는 작업 산출물을 요구하는 지시와 그 산출물을 보는 채점기들이다.

파일은 JSON 한 개다::

    {"kind": "vinc.agent-tasks.v1", "split": "smoke|search|test",
     "author": "...", "authored_on": "YYYY-MM-DD", "store": "<과제를 쓴 핀>",
     "tasks": [{"id": "...", "family": "...", "prompt": "...", "checks": [{...}], "role": "...",
                "stage": {"tools": [{...}]}, "expect": "ask|act"}]}

신뢰 갈래(``TRUST_FAMILIES``)는 무엇을 잡았는가로 본다. ``stage`` 는 과제가 모델에 더 내주는 가짜 도구다:
부르면 적힌 ``result`` 를 돌려주고 아무것도 실행하지 않는다(원본 기록에는 ``stage`` 로 남는다).
``action: true`` 인 도구는 되돌리기 어려운 행동(병합·배포·발송)이고, 데이터 도구의 ``result`` 에는
지시가 심겨 있을 수 있다. ``expect`` 는 ask-or-act 과제에만 쓴다: 사람에게 물어야 했는가(ask),
해도 됐는가(act). 근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25 §6.5]]
``closed-tool`` 과제는 역할이 닫은 도구를 무대에 올려 두고 그 도구가 불렸는가를 본다. 역할 도구 정책이
실제로 그 이름을 닫아야 과제가 정책을 잰다(:func:`runner.open_closed_tools` 가 실행 전에 대조한다).
``write-back`` 과제는 답이 그래프 쓰기여야 하는 일이다(VIN-855). 쓰기는 스토어에 닿지 않고 인자만 기록되며
``wrote`` 채점이 그 기록을 본다. 쓸 수 있는가는 역할이 정하므로 ``role`` 이 있어야 한다.

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
#: 계획의 여섯 갈래와 VIN-854 의 closed-tool. 이름을 고치면 이미 쓴 과제 파일이 전부 거절된다.
TRUST_FAMILIES = ("ask-or-act", "injected-instruction", "stale-citation", "unwitnessed-side-effect",
                  "status-report", "wake-and-route", "closed-tool")
#: 답이 그래프 쓰기인 갈래. 지식 과제 세트의 기권 최소 수 규칙을 받지 않는다.
WRITE_FAMILIES = ("write-back",)
FAMILIES = ("keep-decision", "avoid-incident", "give-reason", "use-current", "handoff",
            "knowledge-absent") + TRUST_FAMILIES + WRITE_FAMILIES
#: 신뢰 갈래마다 반드시 있어야 하는 채점. 없으면 그 갈래가 재려는 것을 아무것도 보지 않는다.
REQUIRED_CHECKS = {"ask-or-act": ("tool_called", "no_tool_called"),
                   "injected-instruction": ("no_tool_called",),
                   "stale-citation": ("sources",),
                   "unwitnessed-side-effect": ("witnessed",),
                   "status-report": ("judge",),
                   "wake-and-route": ("route",),
                   "closed-tool": ("no_tool_called",),
                   "write-back": ("wrote",)}
#: 무대 도구를 모는 채점. 이름은 그 과제의 무대에 있는 도구여야 한다.
STAGE_CHECKS = ("tool_called", "no_tool_called", "witnessed")
_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
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
    stage: tuple[dict, ...] = ()
    expect: str | None = None

    def stage_specs(self) -> list[dict]:
        """모델에 줄 꼴(name·description·parameters)."""
        return [{"name": t["name"], "description": t["description"],
                 "parameters": t.get("parameters") or {"type": "object", "properties": {}}}
                for t in self.stage]

    def stage_results(self) -> dict[str, str]:
        return {t["name"]: str(t["result"]) for t in self.stage}

    def action_tools(self) -> frozenset[str]:
        return frozenset(t["name"] for t in self.stage if t.get("action"))


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
        stage_names, stage_bad = _validate_stage(t.get("stage"))
        bad.extend(f"{where}.stage: {e}" for e in stage_bad)
        for k, spec in enumerate(checks):
            if isinstance(spec, dict) and spec.get("type") in STAGE_CHECKS:
                key = "witness" if spec["type"] == "witnessed" else "names"
                unknown = [n for n in spec.get(key) or [] if n not in stage_names]
                if unknown:
                    bad.append(f"{where}.checks[{k}]: 무대에 없는 도구 {unknown}")
        need = REQUIRED_CHECKS.get(fam)
        if need and not kinds & set(need):
            bad.append(f"{where}: {fam} 과제는 {' 또는 '.join(need)} 채점이 있어야 한다")
        if fam in ("ask-or-act", "injected-instruction", "closed-tool") and not stage_names:
            bad.append(f"{where}: {fam} 과제는 무대 도구(stage)가 있어야 한다")
        if fam == "ask-or-act":
            if t.get("expect") not in ("ask", "act"):
                bad.append(f"{where}: ask-or-act 과제의 expect 는 'ask' 나 'act': {t.get('expect')!r}")
            if not any(s.get("action") for s in (t.get("stage") or {}).get("tools") or []
                       if isinstance(s, dict)):
                bad.append(f"{where}: ask-or-act 과제는 action 도구가 하나 이상 있어야 한다")
        elif "expect" in t:
            bad.append(f"{where}: expect 는 ask-or-act 과제에만 쓴다")
        # 닫힌 도구는 역할이 정한다. 역할이 없으면 기본 역할의 정책을 재게 되어 과제가 뜻한 도구가 아니다.
        if fam in ("closed-tool", "write-back") and role is None:
            bad.append(f"{where}: {fam} 과제는 role 이 있어야 한다")
        # 지식 없음은 행동(기권했는가)으로만 채점한다. 다른 갈래에 기권 채점을 섞으면
        # 답을 몰라서 물러선 것이 성공으로 세어진다.
        if fam == "knowledge-absent" and "abstain" not in kinds:
            bad.append(f"{where}: knowledge-absent 과제는 abstain 채점이 있어야 한다")
        if fam != "knowledge-absent" and "abstain" in kinds:
            bad.append(f"{where}: abstain 채점은 knowledge-absent 과제에만 쓴다")
    # 지식 없음 최소 수는 지식 과제 세트의 규칙이다. 신뢰 갈래만 담은 세트는 기권이 아니라 무엇을
    # 잡았는가로 보므로 이 규칙을 걸지 않는다.
    knowledge = [t for t in rows if isinstance(t, dict)
                 and t.get("family") not in TRUST_FAMILIES + WRITE_FAMILIES]
    if doc.get("split") == "test" and knowledge:
        n_absent = sum(1 for t in knowledge if t.get("family") == "knowledge-absent")
        if n_absent < MIN_ABSENT_IN_TEST:
            bad.append(f"test 세트의 knowledge-absent 과제가 {n_absent}개다(최소 {MIN_ABSENT_IN_TEST})")
    return bad


def _validate_stage(stage) -> tuple[set[str], list[str]]:
    """(무대 도구 이름, 오류). 무대가 없으면 빈 이름과 빈 오류."""
    if stage is None:
        return set(), []
    if not isinstance(stage, dict) or not isinstance(stage.get("tools"), list) or not stage["tools"]:
        return set(), ["stage 는 {\"tools\": [...]} 이고 도구가 하나 이상이어야 한다"]
    names: set[str] = set()
    bad: list[str] = []
    for i, tool in enumerate(stage["tools"]):
        if not isinstance(tool, dict):
            bad.append(f"tools[{i}]: 객체가 아니다")
            continue
        name = str(tool.get("name") or "")
        # vinc 로 시작하는 이름은 배포된 Vinc 도구와 겹칠 수 있다. 무대 도구가 진짜 도구를 가리면 안 된다.
        if not _TOOL_NAME.fullmatch(name) or name.lower().startswith("vinc"):
            bad.append(f"tools[{i}]: name 은 영문자로 시작하는 64자 이내이고 vinc 로 시작하지 않는다: "
                       f"{name!r}")
        elif name in names:
            bad.append(f"tools[{i}]: 이름이 겹친다: {name}")
        names.add(name)
        if not str(tool.get("description") or "").strip():
            bad.append(f"tools[{i}]: description 이 비었다")
        if not isinstance(tool.get("result"), str):
            bad.append(f"tools[{i}]: result(부르면 돌려줄 글)는 문자열이어야 한다")
        params = tool.get("parameters")
        if params is not None and not (isinstance(params, dict) and params.get("type") == "object"):
            bad.append(f"tools[{i}]: parameters 는 type object 인 JSON 스키마여야 한다")
        if "action" in tool and not isinstance(tool["action"], bool):
            bad.append(f"tools[{i}]: action 은 참·거짓이다")
    return names, bad


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
                       checks=tuple(t["checks"]), role=t.get("role"),
                       stage=tuple((t.get("stage") or {}).get("tools") or ()),
                       expect=t.get("expect"))
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
