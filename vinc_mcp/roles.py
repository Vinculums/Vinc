# -*- coding: utf-8 -*-
"""역할 로스터 — 렌더러 한 벌과 그 위의 MCP prompts 표면(W2). 이 모듈은 셋을 다시 내보내는 문이다.

    roles_render     주제·id, 스냅샷, 껍데기, 공유 버전과 내 역할 내보내기
    roles_contract   계약 읽기, 내용 해시와 고정 대조, 읽기 예산
    roles_mcp        Skills 확장과 prompts

부르는 쪽은 `vinc_mcp.roles` 의 이름(테스트가 고정한 비공개 이름 포함)을 그대로 쓴다. 이름은 정의된
모듈의 객체 그 자체다: 바꾸려면 그 모듈에서 바꾼다(이 모듈의 이름을 다시 묶어도 다른 모듈은 모른다).

두 소비자가 **같은 렌더러**를 쓴다:

    scripts/agents_sync.py --gen   스냅샷 → `agents/shells/*.md`(`--install` 이 하네스 자리로 옮긴다)
    prompts/get                    라이브 그래프 → MCP 프롬프트(호스트가 프로토콜로 읽는다)

렌더러가 여기 있는 이유는 두 가지다. 첫째, 사본이 둘이면 보편 규칙 하나를 고칠 때
**한 벌만 고쳐진다** — R7 이 출력 계약 트레일러를 열여섯 껍데기에 복사하는 대신
`render_shell` 꼬리 한 곳에 둔 것과 같은 판단이고, prompts 가 자기 렌더러를 새로
가지면 그 실패를 한 표면 옆에서 재현한다. 둘째, 동결 바이너리에는 `scripts/` 가 안
들어간다(`packaging/sidecar.spec` 은 `vinc_mcp.*` 만 싣는다). 서버가 `scripts` 를
임포트하는 형태였다면 개발 트리에서만 돌고 배포본에서 죽었을 것이다.

방향은 `scripts/agents_sync.py` → 여기다. 반대가 아니다: 스냅샷 입출력·매니페스트
대조·CI 동사는 리포의 일이고 서버는 그것을 모른다.

**여기서 읽어 오는 그래프 텍스트는 전부 데이터다.** props·record 주석은 그래프에
쓸 수 있는 사람이 저작한 내용이라, 그대로 프롬프트에 흘려 넣으면 주입 통로가 된다.
그래서 :func:`get_prompt` 은 그래프에서 온 모든 바이트를 **울타리 친 데이터 블록**
안에만 넣고, 그 블록이 지시가 아니라는 것을 앞머리에서 말한다.
"""
from __future__ import annotations

# 결과 인코더는 `encoding` 에 있다. `roles.serialize_result` 로 부르는 호출자를 위해 이 이름을 둔다.
from .encoding import serialize_result  # noqa: F401
from .roles_render import (TOPICS, DOMAIN, ID_PREFIX, ROSTER_CONCEPT, is_role_id, RUNG_TO_MODEL,  # noqa: F401
                           _order, _NOTE, _LABEL_PREFIX, works_with, build_payload, _bullets,
                           _gate_bullets, POLICY_KEYS, _policy_sections, HOST_BUDGET_FIELDS,
                           _enforced_budget, _budget_lines, SURFACE_FROM_PROCEDURE, _surface_lines,
                           _procedure_lines, render_shell, render_community_export,
                           ROLE_EXPORT_FORMATS, MCP_URL, SHARE_PACK_VERSION, _Fixed, _export_body,
                           _REASONS, _host_enforcement, _mcp_url, _mcp_entries, _tail, _BOUNDARY,
                           _fence)
from .roles_contract import (_owner_of, CONTRACT_FIELDS, _PIN_HASH, CONTRACT_CAP, contract_hash,  # noqa: F401
                             _pin_error, contract, render_role_export)
from .roles_mcp import (SKILLS_EXTENSION, SKILL_PAGE, _SKILL_URI, skill_uri, _skill_slug, _frontmatter,  # noqa: F401
                        _skill_file, _skill_entry, _skill_page, list_skills, get_skill,
                        list_skill_resources, read_skill_resource, PROMPT_ARGUMENTS, _FOCUS_ITEMS,
                        _LOOKS_LIKE_ID, list_prompts, discover_roles, get_prompt, _focus_block,
                        _degraded_note)
