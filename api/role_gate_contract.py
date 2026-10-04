"""역할의 기계 조건 형식 검사. 실행이나 허용 판단은 하네스가 담당한다."""
from __future__ import annotations

import re

_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
_BARE = {"person_only", "side_effect_evidence", "handoff_manifest", "korean_comment_allowed"}


def condition_valid(value: object) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        return False
    kind = value["type"]
    if kind in _BARE:
        return set(value) == {"type"}
    if kind == "command_success":
        command, repos = value.get("command"), value.get("repos")
        return (set(value) == {"type", "command", "repos"}
                and isinstance(command, str) and bool(command.strip()) and len(command) <= 4096
                and isinstance(repos, list) and bool(repos)
                and all(isinstance(r, str) and r in {"Vinc", "Vinc-homepage"} for r in repos)
                and len(set(repos)) == len(repos))
    if kind in {"forbidden_actions", "rendered_copy"}:
        key = "actions" if kind == "forbidden_actions" else "rules"
        allowed = ({"merge", "commit", "push", "edit", "sign_off", "deploy", "english_value",
                    "graph_delete", "graph_merge", "migration"} if kind == "forbidden_actions" else
                   {"variable_particle", "em_dash", "second_person", "object_honorific", "english_value"})
        items = value.get(key)
        return (set(value) == {"type", key} and isinstance(items, list) and bool(items)
                and all(isinstance(item, str) and item in allowed for item in items)
                and len(set(items)) == len(items))
    return False


def check(props: dict) -> list[str]:
    """기존 문자열 gate는 읽는다. 새 조건은 모호한 혼합 없이 구조로 보낸다."""
    warnings = []
    gates = props.get("gates")
    if isinstance(gates, list) and any(isinstance(g, dict) for g in gates):
        if (len(gates) > 64 or not all(isinstance(g, dict) and set(g) == {"id", "check", "on_fail"}
                and isinstance(g.get("id"), str) and bool(_ID.fullmatch(g["id"]))
                and isinstance(g.get("on_fail"), str) and g["on_fail"] in {"block", "interrupt"}
                and condition_valid(g.get("check")) for g in gates)
                or len({g["id"] for g in gates}) != len(gates)):
            warnings.append("gates requires distinct id/check/on_fail objects with known typed conditions")
        else:
            required = {"person_only": "interrupt", "side_effect_evidence": "block", "handoff_manifest": "block"}
            present = {g["check"]["type"] for g in gates}
            if not set(required).issubset(present) or any(
                    g["check"]["type"] in required and g["on_fail"] != required[g["check"]["type"]] for g in gates):
                warnings.append("structured gates must preserve required person_only, side_effect_evidence and handoff_manifest checks")
    elif isinstance(gates, list) and any(not isinstance(g, str) or not g.strip() for g in gates):
        warnings.append("gates must be legacy command strings or typed conditions")
    if "never_checks" in props and (not isinstance(props["never_checks"], list)
            or not all(condition_valid(c) for c in props["never_checks"])):
        warnings.append("never_checks requires known typed conditions; preserve the never prose separately")
    return warnings
