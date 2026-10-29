"""Capability ids for common tool servers, and default maps from their tool names.

A role's ``capabilities`` (``allow``, ``deny``, ``approve``) name what an agent may do, in
ids such as ``github.pr.merge``. A capability is enforced only when a tool is bound to an
id. :data:`CAPABILITIES` is the vocabulary for the common servers, and
:func:`default_capability` binds those servers' tool names to it, so a role that sets
capabilities is enforced even when the developer passes no ``capability_map``.

Vinc's own tools are not in the default maps. They follow the role's ``disallowed_tools``
and ``budget``; binding them here would let an ``allow`` list for another server hide the
graph from the role.
"""
from __future__ import annotations

from typing import Mapping

#: 어휘가 바뀌면 올린다. 허브의 쓰기 검사(api/capabilities.py)도 같은 판이어야 한다(parity 시험).
VOCABULARY_VERSION = "1"

#: 능력 id 와 한 줄 뜻. 와일드카드(``github.*``)는 역할 쪽 문법이고 여기에는 없다.
CAPABILITIES: Mapping[str, str] = {
    "vinc.read": "read the Vinc graph: search, brief, nodes, documents",
    "vinc.write": "write to the Vinc graph: ingest, documents, records",
    "vinc.delete": "delete or merge in the Vinc graph",
    "github.repo.read": "read files, commits, branches, tags, releases and repository metadata",
    "github.issue.read": "read and search issues",
    "github.pr.read": "read and search pull requests",
    "github.actions.read": "read workflow runs, jobs and their logs",
    "github.issue.write": "open, edit or comment on issues",
    "github.pr.write": "open, edit, review or comment on pull requests",
    "github.pr.merge": "merge a pull request or turn on auto-merge",
    "github.repo.write": "push commits, create or delete files and branches, create repositories",
    "github.actions.run": "trigger or rerun a workflow",
    "linear.read": "read issues, projects, documents and comments",
    "linear.issue.write": "create or update issues",
    "linear.comment.write": "create, edit or delete comments",
    "linear.project.write": "create or update projects, documents and milestones",
    "slack.read": "read channels, threads and users",
    "slack.message.send": "post a message, a reply or a reaction",
    "fs.read": "read files and list directories",
    "fs.write": "write, edit, move files or create directories",
    "shell.exec": "run a shell command",
}

#: 계열. 허브는 이 계열 안의 모르는 id 를 오타로 보고 경고한다. 다른 계열은 사용자 것이다.
FAMILIES = tuple(sorted({cap.split(".", 1)[0] for cap in CAPABILITIES}))

_GITHUB_REPO_READ = ("get_file_contents", "search_code", "search_repositories", "search_users",
                     "list_branches", "list_commits", "get_commit", "list_tags", "get_tag",
                     "list_releases", "get_latest_release", "get_release_by_tag", "get_me",
                     "get_label", "list_repository_collaborators")

#: 서버 이름(호스트가 ``mcp__<서버>__<도구>`` 로 붙이는 것, 소문자)마다 도구 이름 → 능력 id.
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

#: 호스트에 내장된 도구(Claude Code)와 흔한 셸 도구. 서버 접두 없이 온다.
BUILTIN_MAP: Mapping[str, str] = {
    "Read": "fs.read", "Glob": "fs.read", "Grep": "fs.read",
    "Write": "fs.write", "Edit": "fs.write", "NotebookEdit": "fs.write",
    "Bash": "shell.exec", "shell": "shell.exec", "run_shell_command": "shell.exec",
    "execute_command": "shell.exec",
}


def _unambiguous() -> dict[str, str]:
    # 접두 없이 온 이름은 한 서버에만 있을 때만 묶는다(list_issues 는 GitHub 과 Linear 둘 다에 있다).
    seen: dict[str, set[str]] = {}
    for table in SERVER_MAPS.values():
        for name, cap in table.items():
            seen.setdefault(name, set()).add(cap)
    out = {name: next(iter(caps)) for name, caps in seen.items() if len(caps) == 1}
    out.update(BUILTIN_MAP)
    return out


BARE_MAP: Mapping[str, str] = _unambiguous()


def default_capability(tool_name: str | None) -> str | None:
    """The capability id the default maps give ``tool_name``, or ``None``.

    ``mcp__github__merge_pull_request`` (a host's server prefix) is looked up in that server's
    map; a bare name only when exactly one server uses it, or when it is a built-in tool.
    """
    if not isinstance(tool_name, str) or not tool_name:
        return None
    if tool_name.startswith("mcp__"):
        server, sep, name = tool_name[len("mcp__"):].partition("__")
        if sep:
            return SERVER_MAPS.get(server.lower(), {}).get(name)
    return BARE_MAP.get(tool_name)


def is_known(capability_id: str) -> bool:
    """Whether an id is in the vocabulary, or a ``family.*`` pattern that matches some of it."""
    if capability_id.endswith(".*"):
        stem = capability_id[:-2] + "."
        return any(cap.startswith(stem) for cap in CAPABILITIES)
    return capability_id in CAPABILITIES
