"""역할 능력 id 의 어휘: 흔한 서버(GitHub·Linear·Slack·파일·셸)와 Vinc 자신.

허브는 역할 쓰기에서 이 어휘의 계열(`github.` 등) 안에 있는데 어휘에 없는 id 를 오타로 보고
경고한다. 다른 계열(`acme.deploy`)은 사용자 것이라 묻지 않는다. 쓰기는 막지 않는다.
어휘의 정본은 `vinc_client/capabilities.py` 이고 integrations/parity 가 둘이 같음을 고정한다
(api 와 클라이언트는 서로 import 하지 않는다).
"""
from __future__ import annotations

#: 클라이언트의 `VOCABULARY_VERSION` 과 같아야 한다.
VOCABULARY_VERSION = "1"

CAPABILITIES = frozenset({
    "vinc.read", "vinc.write", "vinc.delete",
    "github.repo.read", "github.issue.read", "github.pr.read", "github.actions.read",
    "github.issue.write", "github.pr.write", "github.pr.merge", "github.repo.write",
    "github.actions.run",
    "linear.read", "linear.issue.write", "linear.comment.write", "linear.project.write",
    "slack.read", "slack.message.send",
    "fs.read", "fs.write",
    "shell.exec",
})

FAMILIES = tuple(sorted({cap.split(".", 1)[0] for cap in CAPABILITIES}))


def is_known(capability_id: str) -> bool:
    """어휘에 있는 id, 또는 그 일부와 맞는 ``계열.*`` 무늬인가."""
    if capability_id.endswith(".*"):
        stem = capability_id[:-2] + "."
        return any(cap.startswith(stem) for cap in CAPABILITIES)
    return capability_id in CAPABILITIES


def unknown_in_families(ids) -> list[str]:
    """알려진 계열 안인데 어휘에 없는 id(오타일 가능성이 높다), 정렬해서."""
    return sorted({c for c in ids if isinstance(c, str) and c.split(".", 1)[0] in FAMILIES
                   and not is_known(c)})
