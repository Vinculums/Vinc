# -*- coding: utf-8 -*-
"""실행 프로토콜: 역할 하나를 Vinc 가 단계마다 붙잡고 돌린다. 모델은 호스트에 있다.

`vinc_run_start` 가 실행을 열고, 호스트의 모델이 한 단계를 하면 `vinc_run_submit` 으로 낸다.
Vinc 는 그 산출물에 역할의 게이트(`OutputGate`)를 돌리고, 사람만 하는 행동이나 먼저 묻는 행동
앞에서 실행을 대기 상태로 세운다. 답(approve·decline)이 오면 이어 가거나 막힌 채 끝낸다.
끝나면 실행 기록이 남는다. 상태는 따로 두지 않는다 - 실행 기록 Episode(`episode:run-*`)의 props
가 곧 상태다: `outcome` 은 끝날 때까지 `waiting`, `state` 가 working·waiting·done 을 가른다.

한계: 호스트가 낸 문장은 증거가 아니다. 무언가를 했다는 주장은 `role_check` 와 같이 게이트에서
막힌다(증거는 호스트가 관측한 도구 호출에서만 온다). 산출물 원문은 그래프에 넣지 않고 해시와
길이만 남긴다.
근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

import datetime
import fnmatch
import hashlib
import re
import uuid

from . import role_output_gate
from .roles import contract

ROLE_PREFIX = "concept:agent-"
RUN_PREFIX = "episode:run-"
#: 이 흐름의 실행 기록 토픽(D8). 도메인은 `vinc/` 아래 둔다.
DOMAIN = "vinc/agent/harness"
STATES = ("working", "waiting", "done")
#: 열린 실행의 outcome. 끝나기 전에는 아무 결론도 나지 않았다.
OPEN_OUTCOME = "waiting"
ANSWERS = ("approve", "decline")
TASK_MAX = 4000
SUBJECT_MAX = 500
OUTPUT_MAX = 131072
CITED_MAX = 200
#: 호스트가 보고하는 사용량. 합산해 역할의 budget 과 견준다.
USAGE_KEYS = ("tool_calls", "tokens_in", "tokens_out", "cost_usd")
#: 호스트가 관측한 능력 id 의 문법(역할 정책과 같다, 와일드카드는 없다). 한 번에·실행 전체에 담는 상한.
_CAPABILITY_ID = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)*")
CAPABILITIES_PER_STEP = 64
CAPABILITIES_MAX = 200
#: 능력 감사의 표지. denied 는 역할이 막은 것, outside_allow 는 허용 목록 밖, unapproved 는 승인 없이 쓴 것.
CAPABILITY_FLAGS = ("denied", "outside_allow", "unapproved")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def episode_id(run_id: str) -> str:
    """`vinc_client.run.run_episode_id` 와 같은 id(parity 시험이 고정한다). 같은 run_id 는 같은 노드다."""
    slug = re.sub(r"[^a-z0-9]+", "-", run_id.lower()).strip("-")[:48].strip("-") or "run"
    return f"{RUN_PREFIX}{slug}-{hashlib.sha256(run_id.encode('utf-8')).hexdigest()[:8]}"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _stamp(moment: datetime.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _failure(error: str, note: str, **extra) -> dict:
    return {"kind": "run", "executed": False, "error": error, "note": note, **extra}


def _resolve(run) -> str | None:
    """episode id 로 온 것은 그대로, 호스트의 run id 는 episode id 로 바꾼다. 못 읽으면 None."""
    text = str(run or "").strip()
    if text.startswith(RUN_PREFIX):
        return text
    return episode_id(text) if _RUN_ID.fullmatch(text) else None


def _load(bridge, owner: str, eid: str) -> dict | None:
    got = bridge.request("GET", "/runs", params={"owner": owner, "run": eid, "limit": 1})
    if not got.get("executed"):
        return {"_error": got}
    for item in got.get("runs") or []:
        if item.get("id") == eid:
            return dict(item.get("props") or {})
    return None


def _save(bridge, owner: str, eid: str, props: dict, *, title: str) -> dict:
    node = {"id": eid, "type": "episode", "title": title, "date": _stamp(_now())[:10],
            "domain": DOMAIN, "tags": ["agent-run"], "props": props}
    role = props["role"]
    return bridge.request("POST", "/ingest/fragment", body={
        "owner": owner, "nodes": [node, {"id": role, "type": "concept"}],
        "edges": [{"from": eid, "to": role, "type": "about"}]})


def _title(props: dict) -> str:
    slug = props["role"][len("concept:"):]
    tail = props["outcome"] if props["state"] == "done" else props["state"]
    return f"Run {props['run_id']} of {slug}: {tail}"


def _saved(bridge, owner: str, eid: str, props: dict) -> dict | None:
    """저장하고, 실패하면 그 결과를 돌려준다. 성공은 None."""
    out = _save(bridge, owner, eid, props, title=_title(props))
    if not out.get("executed") or out.get("error"):
        return _failure("run_not_saved", "The run state could not be saved, so nothing advanced. "
                        "Call again with the same arguments.", detail=out.get("error") or out.get("note"))
    return None


def _budget_left(policy: dict, props: dict) -> dict:
    budget = policy.get("budget") or {}
    used = props.get("used") or {}
    left = {}
    if "model_calls" in budget:
        left["model_calls"] = max(0, int(budget["model_calls"]) - int(props.get("step", 0)))
    if "tool_calls" in budget:
        left["tool_calls"] = max(0, int(budget["tool_calls"]) - int(used.get("tool_calls", 0)))
    return left


def _view(props: dict, eid: str, policy: dict | None = None, **extra) -> dict:
    out = {"kind": "run", "executed": True, "run": eid, "run_id": props["run_id"],
           "role": props["role"], "state": props["state"], "outcome": props["outcome"],
           "step": props["step"], **extra}
    if props.get("capability_flags"):
        out["capability_flags"] = list(props["capability_flags"])
    if props["state"] == "waiting":
        pending = props.get("pending") or {}
        out["question"] = pending.get("question", "")
        out["pending_actions"] = list(pending.get("actions") or [])
        #: 몇 번째 질문인가. MCP Tasks 의 작업 id 가 이 번호를 쓴다(`run_tasks.py`).
        out["ask"] = int(props.get("asks") or 0)
        out["asked_at"] = pending.get("asked_at", "")
        out["next"] = ("A person decides first. Show the question, then call vinc_run_submit with "
                       "answer approve or decline. No output is accepted while it waits.")
    elif props["state"] == "working":
        out["next"] = ("Do this step, then call vinc_run_submit with output. Name an action in "
                       "person_only_requests before taking it when the role lists it as person "
                       "only or asks first.")
        if policy is not None:
            out["task"] = props.get("task", "")
            out["policy"] = {k: policy[k] for k in ("reads", "gates", "output_kind", "act_policy",
                                                    "capabilities", "person_only", "never")
                             if k in policy}
            out["budget_left"] = _budget_left(policy, props)
    else:
        out["ended_at"] = props.get("ended_at", "")
        out["end_reason"] = props.get("end_reason", "")
    return out


def _fresh_role(bridge, owner: str, role: str) -> dict:
    return contract(role, bridge, forced_owner=owner, compact=True, capped=False)


def start(args: dict, bridge, owner: str) -> dict:
    role, task = str(args.get("role") or "").strip(), str(args.get("task") or "").strip()
    run_id = str(args.get("run_id") or "").strip() or "run-" + uuid.uuid4().hex[:12]
    if not role:
        return _failure("role_required", "Invalid: name the role to run.")
    if not task or len(task) > TASK_MAX:
        return _failure("task_required", f"Invalid: pass the task as a non-empty string of at most "
                                         f"{TASK_MAX} characters.")
    if not _RUN_ID.fullmatch(run_id):
        return _failure("invalid_run_id", "Invalid: run_id is 1 to 128 letters, digits, '.', '_', "
                                          "':' or '-'.")
    subject = str(args.get("subject") or "").strip()[:SUBJECT_MAX]
    eid = episode_id(run_id)
    got = _fresh_role(bridge, owner, role)
    if not got.get("executed") or not got.get("found"):
        return {**got, "kind": "run"}
    existing = _load(bridge, owner, eid)
    if existing and "_error" in existing:
        return {**existing["_error"], "kind": "run"}
    if existing:
        if existing.get("role") != got["id"]:
            return _failure("run_id_in_use", "Invalid: this run_id belongs to another role.")
        return _view(existing, eid, got["props"], resumed=True)
    props = {"run_id": run_id, "role": got["id"], "outcome": OPEN_OUTCOME, "state": "working",
             "step": 0, "task": task, "started_at": _stamp(_now()), "role_rev": got.get("rev"),
             "role_hash": got.get("hash"), "used": {}, "approved": []}
    if subject:
        props["subject"] = subject
    failed = _saved(bridge, owner, eid, props)
    if failed:
        return failed
    return _view(props, eid, got["props"], resumed=False)


def _end(props: dict, outcome: str, reason: str) -> None:
    props.update(outcome=outcome, state="done", ended_at=_stamp(_now()), end_reason=reason)
    props.pop("pending", None)


def _over_budget(policy: dict, props: dict) -> str:
    budget = policy.get("budget") or {}
    used = props.get("used") or {}
    if "model_calls" in budget and props["step"] >= int(budget["model_calls"]):
        return "budget_model_calls"
    if "tool_calls" in budget and used.get("tool_calls", 0) > budget["tool_calls"]:
        return "budget_tool_calls"
    if "tokens" in budget and used.get("tokens_in", 0) + used.get("tokens_out", 0) > budget["tokens"]:
        return "budget_tokens"
    if "cost_usd" in budget and used.get("cost_usd", 0) > budget["cost_usd"]:
        return "budget_cost_usd"
    if "wall_seconds" in budget:
        started = datetime.datetime.strptime(props["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
        if (_now() - started).total_seconds() > budget["wall_seconds"]:
            return "budget_wall_seconds"
    return ""


def _add_usage(props: dict, usage) -> None:
    if not isinstance(usage, dict):
        return
    used = props.setdefault("used", {})
    for key in USAGE_KEYS:
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            used[key] = used.get(key, 0) + value


def _add_cited(props: dict, cited) -> None:
    if not isinstance(cited, list):
        return
    merged = list(props.get("cited_ids") or [])
    for item in cited:
        if isinstance(item, str) and ":" in item.strip() and item.strip() not in merged:
            merged.append(item.strip())
    if merged:
        props["cited_ids"] = merged[:CITED_MAX]


def capability_verdict(capabilities, capability_id: str) -> str | None:
    """역할의 ``capabilities`` 에서 id 하나의 판정: ``deny``·``approve``·``allow`` 순으로 앞선 것, 없으면 None.

    `vinc_client.role_policy.RolePolicy.capability` 와 같은 규칙이다(integrations/parity 가 고정한다).
    """
    caps = capabilities if isinstance(capabilities, dict) else {}
    for verdict in ("deny", "approve", "allow"):
        for pattern in caps.get(verdict) or []:
            if not isinstance(pattern, str):
                continue
            if capability_id == pattern or (pattern.endswith(".*") and fnmatch.fnmatchcase(
                    capability_id, pattern[:-2] + ".*")):
                return verdict
    return None


def _used_capabilities(args: dict):
    """(이번 단계에 호스트가 쓴 능력 id 들, 오류 문장). 없으면 빈 목록."""
    raw = args.get("capabilities_used")
    if raw is None:
        return [], None
    if (not isinstance(raw, list) or len(raw) > CAPABILITIES_PER_STEP
            or any(not isinstance(c, str) or not _CAPABILITY_ID.fullmatch(c) for c in raw)):
        return [], (f"Invalid: capabilities_used is a list of at most {CAPABILITIES_PER_STEP} "
                    "capability ids such as github.pr.merge (no wildcards).")
    return list(dict.fromkeys(raw)), None


def _audit(policy: dict, used: list[str], approved: list[str]) -> list[dict]:
    """쓴 능력마다 역할과 견준 표지. 문제없는 id 는 빠진다."""
    caps = policy.get("capabilities") if isinstance(policy.get("capabilities"), dict) else {}
    allow_list = bool(caps.get("allow"))
    flags = []
    for cap in used:
        verdict = capability_verdict(caps, cap)
        if verdict == "deny":
            flags.append({"id": cap, "flag": "denied"})
        elif verdict == "approve" and cap not in approved:
            flags.append({"id": cap, "flag": "unapproved"})
        elif verdict is None and allow_list:
            flags.append({"id": cap, "flag": "outside_allow"})
    return flags


def _record_capabilities(props: dict, used: list[str], flags: list[dict], step: int) -> None:
    if used:
        merged = list(dict.fromkeys(list(props.get("capabilities_used") or []) + used))
        props["capabilities_used"] = merged[:CAPABILITIES_MAX]
    if flags:
        kept = list(props.get("capability_flags") or [])
        seen = {(f.get("id"), f.get("flag")) for f in kept}
        kept += [{**f, "step": step} for f in flags if (f["id"], f["flag"]) not in seen]
        props["capability_flags"] = kept[:CAPABILITIES_MAX]


def _question(actions: list[str], policy: dict, checks: list[str]) -> str:
    reasons = {p.get("action"): p.get("reason") for p in policy.get("person_only") or []
               if isinstance(p, dict)}
    parts = [f"{a}" + (f" ({reasons[a]})" if reasons.get(a) else "") for a in actions]
    if parts:
        return "A person decides before the role goes on: " + "; ".join(parts) + "."
    return "A gate asks a person to decide: " + ", ".join(checks) + "."


def _requests(args: dict, policy: dict, approved: list[str]):
    """호스트가 하려는 행동을 셋으로 가른다: 사람만(게이트로), 먼저 묻기, 금지. 이미 승인된 것은 뺀다."""
    raw = args.get("person_only_requests")
    if raw is None:
        return [], [], [], None
    if not isinstance(raw, list):
        return [], [], [], "Invalid: person_only_requests is a list of {action, reason}."
    person = {p.get("action") for p in policy.get("person_only") or [] if isinstance(p, dict)}
    act = policy.get("act_policy") or {}
    gate, asks, never = [], [], []
    for item in raw:
        if (not isinstance(item, dict) or set(item) != {"action", "reason"}
                or not isinstance(item["action"], str) or not str(item["reason"] or "").strip()):
            return [], [], [], "Invalid: each person_only_request is exactly {action, reason}."
        action = item["action"]
        if action in approved:
            continue
        if act.get(action) == "never":
            never.append(action)
        elif action in person:
            gate.append(item)
        elif act.get(action) == "ask_first":
            asks.append(action)
        elif "." in action and capability_verdict(policy.get("capabilities"), action) == "approve":
            # 승인으로 묶인 능력 id 도 미리 묻는다. 승인되면 capabilities_used 에 그 id 가 와도 표지가 없다.
            asks.append(action)
        else:
            return [], [], [], (f"Invalid: {action} is neither person only nor ask first for this "
                                "role, so it needs no request. Take it, or drop the request.")
    return gate, asks, never, None


def submit(args: dict, bridge, owner: str) -> dict:
    eid = _resolve(args.get("run"))
    if eid is None:
        return _failure("run_required", "Invalid: pass the run id vinc_run_start returned.")
    modes = [k for k in ("output", "answer", "abandon") if args.get(k) not in (None, "", False)]
    if len(modes) != 1:
        return _failure("one_input_required", "Invalid: pass exactly one of output, answer or "
                                              "abandon.")
    props = _load(bridge, owner, eid)
    if props is None:
        return {"kind": "run", "executed": False, "found": False, "error": "run_not_found",
                "note": "Unknown run: start one with vinc_run_start."}
    if "_error" in props:
        return {**props["_error"], "kind": "run"}
    if props.get("state") == "done":
        return _view(props, eid)
    got = _fresh_role(bridge, owner, props["role"])
    if not got.get("executed") or not got.get("found"):
        return {**got, "kind": "run"}
    policy = got["props"]
    mode = modes[0]
    used, bad = _used_capabilities(args)
    if bad:
        return _failure("invalid_capabilities_used", bad)
    _add_usage(props, args.get("usage"))
    _add_cited(props, args.get("cited_ids"))
    flags = _audit(policy, used, list(props.get("approved") or []))
    # 표지는 이 호출이 낸 단계에 단다(output 이면 _output 이 올릴 다음 단계 번호).
    _record_capabilities(props, used, flags, int(props.get("step", 0)) + (mode == "output"))
    if mode == "abandon":
        _end(props, "abandoned", "abandoned by the host")
        return _finish(bridge, owner, eid, props)
    if props["state"] == "waiting":
        if mode != "answer":
            return _failure("invalid_run_state", "Invalid: the run waits for an answer, not output.",
                            run_state=_view(props, eid))
        return _answer(args, bridge, owner, eid, props, policy)
    if mode == "answer":
        return _failure("invalid_run_state", "Invalid: the run is not waiting for an answer.")
    over = _over_budget(policy, props)
    if over:
        _end(props, "blocked", over)
        return _finish(bridge, owner, eid, props)
    return _output(args, bridge, owner, eid, props, policy, flags)


def _finish(bridge, owner, eid, props, **extra) -> dict:
    failed = _saved(bridge, owner, eid, props)
    return failed or _view(props, eid, **extra)


def _answer(args, bridge, owner, eid, props, policy) -> dict:
    answer = args.get("answer")
    if answer not in ANSWERS:
        return _failure("invalid_answer", "Invalid: answer is approve or decline.")
    pending = props.pop("pending", {}) or {}
    props["answered_at"] = _stamp(_now())
    #: `run.answered` 이벤트(api/events.py)가 읽는다. 다음 답이 오면 덮여 새 이벤트가 된다.
    props["answer"] = answer
    props["answered_actions"] = list(pending.get("actions") or [])
    if answer == "decline":
        _end(props, "blocked", "declined by a person")
        return _finish(bridge, owner, eid, props)
    props["approved"] = list(dict.fromkeys(list(props.get("approved") or [])
                                           + list(pending.get("actions") or [])))
    props["state"] = "working"
    return _resume(bridge, owner, eid, props, policy)


def _resume(bridge, owner, eid, props, policy) -> dict:
    failed = _saved(bridge, owner, eid, props)
    return failed or _view(props, eid, policy, approved=props["approved"])


def _output(args, bridge, owner, eid, props, policy, flags=()) -> dict:
    output = args.get("output")
    if not isinstance(output, str) or not output.strip() or len(output) > OUTPUT_MAX:
        return _failure("output_required", f"Invalid: pass the output as one non-empty string of at "
                                           f"most {OUTPUT_MAX} characters.")
    gate_requests, asks, never, bad = _requests(args, policy, list(props.get("approved") or []))
    if bad:
        return _failure("invalid_person_only_requests", bad)
    props["step"] = int(props.get("step", 0)) + 1
    props["output_ref"] = {"sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
                           "chars": len(output)}
    if never or flags:
        # 금지된 행동과, 역할이 허락하지 않은 능력을 이미 쓴 단계는 막는다. 실행은 열린 채 다음 시도를 받는다.
        props["gate_results"] = {"verdict": "block", "failed_checks": (
            [f"action_never:{a}" for a in never]
            + [f"capability_{f['flag']}:{f['id']}" for f in flags])}
        failed = _saved(bridge, owner, eid, props)
        return failed or _view(props, eid, policy, status="blocked",
                               failed_checks=props["gate_results"]["failed_checks"])
    result = role_output_gate.OutputGate(policy).evaluate(output, person_only_requests=gate_requests)
    props["gate_results"] = {"verdict": result.verdict, "failed_checks": list(result.failed_checks)}
    if result.verdict == "block":
        failed = _saved(bridge, owner, eid, props)
        return failed or _view(props, eid, policy, status="blocked",
                               failed_checks=list(result.failed_checks),
                               evidence_sentences=list(result.evidence_sentences))
    actions = list(dict.fromkeys(list(result.person_only_actions) + asks))
    if result.verdict == "interrupt" or actions:
        props["state"] = "waiting"
        asked_at = _stamp(_now())
        props["asks"] = int(props.get("asks") or 0) + 1
        props["last_asked_at"] = asked_at
        props["pending"] = {"actions": actions, "checks": list(result.failed_checks),
                            "question": _question(actions, policy, list(result.failed_checks)),
                            "asked_at": asked_at}
        failed = _saved(bridge, owner, eid, props)
        return failed or _view(props, eid, policy)
    _end(props, "success", "gates passed")
    return _finish(bridge, owner, eid, props, status="passed")
