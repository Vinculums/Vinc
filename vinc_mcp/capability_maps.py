"""능력 id 판정과 흔한 서버의 도구 이름 → 능력 id 지도의 허브 사본, 그리고 호스트가 막을 도구 목록.

지도의 정본은 `vinc_client/capabilities.py` 다. 허브와 클라이언트는 서로 import 하지 않으므로 같은 표를
여기 두고 integrations/parity 가 둘이 같음을 고정한다. 역할 내보내기(`roles.render_role_export`)와
껍데기 생성(`roles.render_shell`)이 이 표로 역할의 막힌 능력을 호스트가 직접 집행하는 칸으로 옮긴다.

Vinc 자신의 도구는 지도에 없다. 클라이언트와 같은 이유다: 다른 서버용 ``allow`` 목록이 그래프 읽기를
가리지 않게 한다. Vinc 도구는 역할의 ``disallowed_tools`` 를 따른다.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Mapping

_GITHUB_REPO_READ = ("get_file_contents", "search_code", "search_repositories", "search_users",
                     "list_branches", "list_commits", "get_commit", "list_tags", "get_tag",
                     "list_releases", "get_latest_release", "get_release_by_tag", "get_me",
                     "get_label", "list_repository_collaborators")

#: 서버 이름(소문자)마다 도구 이름 → 능력 id. 클라이언트의 `SERVER_MAPS` 와 같아야 한다.
SERVER_MAPS: Mapping[str, Mapping[str, str]] = {
    "github": {
        **{name: "github.repo.read" for name in _GITHUB_REPO_READ},
        **{name: "github.issue.read" for name in ("list_issues", "search_issues", "issue_read",
                                                   "list_issue_types")},
        **{name: "github.pr.read" for name in ("list_pull_requests", "search_pull_requests",
                                                "pull_request_read")},
        **{name: "github.actions.read" for name in ("actions_list", "actions_get",
                                                     "get_job_logs")},
        **{name: "github.issue.write" for name in ("issue_write", "add_issue_comment",
                                                    "sub_issue_write", "update_issue_comment")},
        **{name: "github.pr.write" for name in (
            "create_pull_request", "update_pull_request", "pull_request_review_write",
            "add_comment_to_pending_review", "add_reply_to_pull_request_comment",
            "request_copilot_review", "update_pull_request_branch", "resolve_review_thread",
            "unresolve_review_thread")},
        **{name: "github.pr.merge" for name in ("merge_pull_request", "enable_pr_auto_merge")},
        **{name: "github.repo.write" for name in ("create_or_update_file", "push_files",
                                                   "create_branch", "delete_file",
                                                   "fork_repository", "create_repository")},
        "actions_run_trigger": "github.actions.run",
    },
    "linear": {
        **{name: "linear.read" for name in (
            "list_issues", "get_issue", "list_comments", "list_projects", "get_project",
            "list_teams", "get_team", "list_users", "get_user", "list_documents", "get_document",
            "list_issue_labels", "list_issue_statuses", "list_cycles", "list_milestones",
            "search_documentation")},
        "save_issue": "linear.issue.write",
        **{name: "linear.comment.write" for name in ("save_comment", "delete_comment")},
        **{name: "linear.project.write" for name in ("save_project", "save_document",
                                                      "save_milestone")},
    },
    "slack": {
        **{name: "slack.read" for name in ("slack_list_channels", "slack_get_channel_history",
                                            "slack_get_thread_replies", "slack_get_users",
                                            "slack_get_user_profile")},
        **{name: "slack.message.send" for name in ("slack_post_message", "slack_reply_to_thread",
                                                    "slack_add_reaction")},
    },
    "filesystem": {
        **{name: "fs.read" for name in ("read_file", "read_text_file", "read_media_file",
                                         "read_multiple_files", "list_directory",
                                         "list_directory_with_sizes", "directory_tree",
                                         "search_files", "get_file_info",
                                         "list_allowed_directories")},
        **{name: "fs.write" for name in ("write_file", "edit_file", "create_directory",
                                          "move_file")},
    },
}

#: 클라이언트의 `BUILTIN_MAP` 과 같아야 한다. 앞의 여섯과 Bash 는 Claude Code 의 내장 도구다.
BUILTIN_MAP: Mapping[str, str] = {
    "Read": "fs.read", "Glob": "fs.read", "Grep": "fs.read",
    "Write": "fs.write", "Edit": "fs.write", "NotebookEdit": "fs.write",
    "Bash": "shell.exec", "shell": "shell.exec", "run_shell_command": "shell.exec",
    "execute_command": "shell.exec",
}

#: Claude Code 의 내장 도구. 껍데기의 disallowedTools 에는 이것과 mcp__<서버>__<도구> 만 쓴다.
CLAUDE_CODE_BUILTINS = ("Read", "Glob", "Grep", "Write", "Edit", "NotebookEdit", "Bash")

#: OpenClaw 내장 도구(tools.deny 에 쓰는 이름). 근거: docs.openclaw.ai/gateway/config-tools/tool-policy
OPENCLAW_BUILTINS: Mapping[str, tuple[str, ...]] = {
    "fs.read": ("read",),
    "fs.write": ("write", "edit", "apply_patch"),
    "shell.exec": ("exec", "process", "code_execution"),
}


#: 역할 쓰기 검사의 능력 무늬(api/role_policy_contract.CAPABILITY)와 같다. 이 꼴이 아닌 글은 내보내기에 옮기지 않는다.
CAPABILITY_PATTERN = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)*(?:\.\*)?")


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


def _closed(capabilities, capability_id: str) -> str | None:
    """호스트가 막아야 하는 이유: ``deny``·``approve``(사람이 할 일)·``outside_allow``, 아니면 None.

    `vinc_client.tool_policy.RoleToolPolicy` 와 같다: allow 목록이 있으면 그 밖의 아는 도구는 닫힌다."""
    verdict = capability_verdict(capabilities, capability_id)
    if verdict in ("deny", "approve"):
        return verdict
    caps = capabilities if isinstance(capabilities, dict) else {}
    if caps.get("allow") and verdict != "allow":
        return "outside_allow"
    return None


def blocked_tools(capabilities) -> dict:
    """역할의 능력 → 기본 지도가 아는 도구 중 호스트가 막을 것.

    ``{"capabilities": {id: 이유}, "servers": {서버: [도구]}, "builtins": [Claude Code 도구],
    "openclaw_builtins": [OpenClaw 도구]}``. 모두 정렬한다. 막을 것이 없으면 빈 목록들이다."""
    reasons: dict[str, str] = {}
    servers: dict[str, list[str]] = {}
    for server in sorted(SERVER_MAPS):
        names = []
        for name in sorted(SERVER_MAPS[server]):
            cap = SERVER_MAPS[server][name]
            why = _closed(capabilities, cap)
            if why:
                reasons[cap] = why
                names.append(name)
        if names:
            servers[server] = names
    builtins = []
    for name in CLAUDE_CODE_BUILTINS:
        why = _closed(capabilities, BUILTIN_MAP[name])
        if why:
            reasons[BUILTIN_MAP[name]] = why
            builtins.append(name)
    openclaw = sorted(name for cap, names in OPENCLAW_BUILTINS.items()
                      if _closed(capabilities, cap) for name in names)
    return {"capabilities": dict(sorted(reasons.items())), "servers": servers,
            "builtins": builtins, "openclaw_builtins": openclaw}


def claude_code_disallowed(props: dict) -> list[str]:
    """껍데기 프런트매터의 disallowedTools: 역할의 ``disallowed_tools`` 를 그 순서대로 두고, 능력이 막는
    도구를 그 뒤에 더한다. 능력이 없는 역할은 ``disallowed_tools`` 그대로다(기존 껍데기의 바이트가 같다)."""
    own = [str(t) for t in props.get("disallowed_tools") or []]
    if not isinstance(props.get("capabilities"), dict):
        return own
    got = blocked_tools(props["capabilities"])
    extra = got["builtins"] + [f"mcp__{server}__{name}" for server, names in got["servers"].items()
                               for name in names]
    return own + [t for t in extra if t not in own]


def unenforced(capabilities) -> list[str]:
    """deny·approve 에 있는데 기본 지도의 어느 도구와도 맞지 않는 id(호스트 설정으로는 닫히지 않는다).

    능력 무늬가 아닌 글은 뺀다. 이 목록은 내보내기의 데이터 울타리 밖에 실린다."""
    caps = capabilities if isinstance(capabilities, dict) else {}
    known = {cap for table in SERVER_MAPS.values() for cap in table.values()} | set(BUILTIN_MAP.values())
    listed = [p for key in ("deny", "approve") if isinstance(caps.get(key), list) for p in caps[key]]
    return sorted({p for p in listed if isinstance(p, str) and CAPABILITY_PATTERN.fullmatch(p)
                   and not any(capability_verdict({"deny": [p]}, cap) for cap in known)})
