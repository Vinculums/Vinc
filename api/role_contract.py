# -*- coding: utf-8 -*-
"""Agent 역할 노드의 작성 계약 — 쓰기 경로가 역할을 받으면 여기서 어긋난 곳을 **말한다**.

누가 쓰든(MCP 의 `vinc_ingest` · 동기화 적용 · 팩 가져오기) 역할 노드는 모두
`fragment._merge_node` 를 지나므로 검사는 거기 한 자리에서 부른다. 거부하지 않고
경고만 한다: 같은 경로가 동기화 전체를 나르므로, 필드 하나로 쓰기를 세우면 동기화가
통째로 멈춘다(`_warn_unkept_fields` 와 같은 태도).

**보낸 것만 본다.** 보내지 않은 필드는 보존되므로, 도메인만 다시 보낸 호출에
"props 가 없다"고 말하면 거짓 경고다. 신규 생성일 때만 "빠졌다"를 말한다.

상수 넷(DOMAIN · ID_PREFIX · ROSTER_CONCEPT · RUNGS)은 `vinc_mcp/roles.py` 에도 있다.
`api` 는 `vinc_mcp` 를 import 하지 않으므로(호스티드 MCP 이미지와 허브의 짐이 다르다)
한 벌로 합치지 않고 테스트가 둘이 같음을 고정한다(tests/test_role_contract.py).

근거: [[vinc.canon.agent-roles §6]] · [[decision:agent-role-contract-is-warned-at-write-not-refused-2026-09-25]]
"""
from __future__ import annotations

import re

DOMAIN = "vinc/agent"
ID_PREFIX = "concept:agent-"
#: 로스터를 정의하는 개념. 접두가 역할과 같지만 역할이 아니다(roles.ROSTER_CONCEPT).
ROSTER_CONCEPT = "concept:agent-orchestration"

TIERS = ("orchestrate", "execute", "verify", "deliver", "knowledge")
#: rung → 모델 대응은 `vinc_mcp/roles.RUNG_TO_MODEL` 이 갖는다. 여기는 이름만.
RUNGS = ("strongest", "strong", "fast", "cheapest")
REQUIRED = ("mission", "tier", "rung", "never", "person_only")
#: 렌더러(`roles._bullets`)가 항목 단위로 걷는 키. 문자열이 오면 **글자 단위로** 걷어
#: 한 줄짜리 금지가 글자마다 불릿 하나로 나온다.
LIST_KEYS = ("never", "person_only", "reads", "gates", "repos", "paths", "tools",
             "disallowed_tools", "writes_graph")
TITLE_PREFIX = "agent role:"

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def is_role_id(nid: str) -> bool:
    return nid.startswith(ID_PREFIX) and nid != ROSTER_CONCEPT


def check(node: dict, *, created: bool) -> list[str]:
    """역할 노드 한 건 → 경고 문장 목록(없으면 빈 목록). ``created`` = 이번 쓰기가 새로 만든다."""
    nid = str(node.get("id") or "").strip()
    if not is_role_id(nid):
        return []
    out: list[str] = []
    why = "see vinc.canon.agent-roles"

    slug = nid[len(ID_PREFIX):]
    if not _SLUG.match(slug):
        out.append(f"role id {nid}: use lowercase letters, digits and hyphens after "
                   f"'{ID_PREFIX}'; the slug is the prompt name a host shows ({why})")

    dom = node.get("domain")
    if dom is None:
        if created:
            out.append(f"role {nid} was created with no domain; prompts/list and the "
                       f"Agent rail read {DOMAIN} and one level below it, so this role is "
                       f"invisible to both ({why})")
    else:
        d = str(dom).strip().strip("/")
        if d != DOMAIN and not d.startswith(DOMAIN + "/"):
            out.append(f"role {nid} is filed under '{d}'; roles live under {DOMAIN}, "
                       f"which is where prompts/list and the Agent rail look ({why})")
        elif d.count("/") > DOMAIN.count("/") + 1:
            out.append(f"role {nid} is filed under '{d}', more than one level below "
                       f"{DOMAIN}; prompts/list reads only one level down, so it will "
                       f"not be listed ({why})")

    title = node.get("title")
    if isinstance(title, str) and title.strip().lower().startswith(TITLE_PREFIX):
        out.append(f"role {nid}: title '{title.strip()}' carries the 'Agent role:' prefix; "
                   f"use the job name alone; a shared prefix defeats duplicate detection "
                   f"({why})")

    props = node.get("props")
    if props is None:
        if created:
            out.append(f"role {nid} was created with no props; a role's contract lives "
                       f"in props: {', '.join(REQUIRED)} ({why})")
        return out
    if not isinstance(props, dict):
        return out          # 형식 오류는 fragment 의 기존 경로가 말한다
    missing = [k for k in REQUIRED if k not in props]
    if missing:
        out.append(f"role {nid}: props is missing {', '.join(missing)}; props replace "
                   f"whole, so read the stored props, merge, and resend them all ({why})")
    tier = props.get("tier")
    if tier is not None and tier not in TIERS:
        out.append(f"role {nid}: tier '{tier}' is not one of {', '.join(TIERS)} ({why})")
    rung = props.get("rung")
    if rung is not None and rung not in RUNGS:
        out.append(f"role {nid}: rung '{rung}' is not one of {', '.join(RUNGS)}; a model "
                   f"name here renders an empty model line; the ladder maps rungs to "
                   f"models ({why})")
    bad = [k for k in LIST_KEYS if k in props and not isinstance(props[k], list)]
    if bad:
        out.append(f"role {nid}: {', '.join(bad)} must be a list; a single string is "
                   f"rendered one character per bullet ({why})")
    mission = props.get("mission")
    one = node.get("one_liner")
    if (isinstance(one, str) and isinstance(mission, str)
            and " ".join(one.split()) != " ".join(mission.split())):
        out.append(f"role {nid}: one_liner and props.mission differ; hosts show the "
                   f"first when listing roles and the second when running one ({why})")
    return out
