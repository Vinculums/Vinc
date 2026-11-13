"""역할의 실행 정책 칸의 형식 검사: output_kind · act_policy · budget · capabilities · tool_servers.

모두 선택이다. 쓰기는 거절하지 않고 경고만 한다(role_contract 와 같은 태도). 집행은 호스트와
어댑터가 한다 — 여기는 값의 모양만 본다.

사본이 두 벌 있다.
- 어휘는 `vinc_client/role_policy.py` 에도 있고 integrations/parity 테스트가 둘이 같음을 대조한다.
  클라이언트는 따로 배포되는 패키지(`vinc-client`, 의존은 httpx · pydantic 뿐)라 `api` 를
  import 할 수 없다.
- `KEYS` 는 `vinc_mcp/roles.POLICY_KEYS` 에도 있고 tests/core/test_role_policy_contract.py 가
  대조한다. 이 사본은 층 규칙이 강요하는 것이 아니다: 소유 게이트(`scripts/gate_ownership.py` 의
  LAYER_BANS)가 막는 방향은 `vinc_mcp` → `api` 이고 `api` → `vinc_mcp` 는 열려 있다. 한쪽에서
  가져다 쓸지, 사본과 대조 테스트를 유지할지는 열린 결정이다.

근거: [[vinc.canon.agent-roles §3]]
"""
from __future__ import annotations

import re

from . import capabilities

#: 칸 이름. `vinc_mcp/roles.POLICY_KEYS` 와 같아야 한다(tests/core/test_role_policy_contract.py).
KEYS = ("output_kind", "act_policy", "budget", "capabilities", "tool_servers")
#: 이 역할이 내놓는 것. verdict 는 통과·막음 같은 결론 하나, write_proposal 은 쓰기 제안(미리보기)이다.
OUTPUT_KINDS = ("prose", "code", "verdict", "write_proposal", "none")
#: 행동 하나에 대한 태도. ask_first 는 사람이 먼저 정하고, never 는 하지 않는다.
ACT_MODES = ("act", "act_and_report", "ask_first", "never")
#: 실행 하나의 상한. 앞의 셋은 정수, 뒤의 둘은 수다.
BUDGET_INTS = ("tool_calls", "model_calls", "tokens")
BUDGET_NUMBERS = ("cost_usd", "wall_seconds")
CAPABILITY_LISTS = ("allow", "deny", "approve")
MAX_ENTRIES = 256

#: person_only 의 action 과 같은 문법(소문자 snake_case).
ACTION = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
#: 점으로 이은 소문자 이름(`vinc.read`, `github.pr.merge`). 끝 칸 하나만 `*` 일 수 있다.
CAPABILITY = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)*(?:\.\*)?")


def _person_actions(props: dict) -> set[str]:
    person = props.get("person_only")
    if not isinstance(person, list):
        return set()
    return {item["action"] for item in person
            if isinstance(item, dict) and isinstance(item.get("action"), str)}


def _number(value: object, *, integer: bool) -> bool:
    if isinstance(value, bool):
        return False
    if integer:
        return isinstance(value, int) and value >= 0
    return isinstance(value, (int, float)) and value == value and value >= 0 and value != float("inf")


def check(props: dict) -> list[str]:
    """보낸 칸만 본다. 없는 칸은 경고하지 않는다."""
    out: list[str] = []
    if "output_kind" in props and props["output_kind"] not in OUTPUT_KINDS:
        out.append(f"output_kind must be one of {', '.join(OUTPUT_KINDS)}")

    if "act_policy" in props:
        policy = props["act_policy"]
        if (not isinstance(policy, dict) or len(policy) > MAX_ENTRIES
                or any(not isinstance(k, str) or not ACTION.fullmatch(k) or v not in ACT_MODES
                       for k, v in policy.items())):
            out.append("act_policy must map lowercase snake_case action ids to one of "
                       + ", ".join(ACT_MODES))
        else:
            # 사람만 하는 행동을 정책이 풀어 줄 수 없다. 묻거나 하지 않거나 둘 중 하나다.
            loose = sorted(a for a in _person_actions(props)
                           if policy.get(a) in ("act", "act_and_report"))
            if loose:
                out.append("act_policy cannot let the role act alone on a person_only action: "
                           + ", ".join(loose) + "; use ask_first or never")

    if "budget" in props:
        budget = props["budget"]
        known = BUDGET_INTS + BUDGET_NUMBERS
        if (not isinstance(budget, dict) or not budget
                or any(k not in known for k in budget)
                or any(not _number(budget[k], integer=k in BUDGET_INTS) for k in budget)):
            out.append("budget must hold one or more of " + ", ".join(known)
                       + " as numbers of 0 or more (whole numbers for "
                       + ", ".join(BUDGET_INTS) + ")")

    if "capabilities" in props:
        caps = props["capabilities"]
        if (not isinstance(caps, dict) or not caps
                or any(k not in CAPABILITY_LISTS for k in caps)
                or any(not isinstance(v, list) or len(v) > MAX_ENTRIES
                       or any(not isinstance(c, str) or not CAPABILITY.fullmatch(c) for c in v)
                       for v in caps.values())):
            out.append("capabilities must hold allow, deny or approve lists of dotted lowercase "
                       "capability ids such as vinc.read or github.pr.merge (a final .* matches "
                       "a family)")
        else:
            seen: dict[str, str] = {}
            twice = set()
            for key in CAPABILITY_LISTS:
                for cap in caps.get(key) or []:
                    if cap in seen and seen[cap] != key:
                        twice.add(cap)
                    seen.setdefault(cap, key)
            if twice:
                out.append("capabilities lists the same id in more than one list: "
                           + ", ".join(sorted(twice)))
            unknown = capabilities.unknown_in_families(seen)
            if unknown:
                out.append("capabilities names ids that are not in the capability vocabulary "
                           f"(version {capabilities.VOCABULARY_VERSION}): " + ", ".join(unknown)
                           + "; check the spelling, or use your own family for your own tools")

    if "tool_servers" in props and not capabilities.tool_servers_ok(props["tool_servers"]):
        out.append("tool_servers must map the host's server names (letters, digits, dot, dash, "
                   "underscore) to one of " + ", ".join(capabilities.TOOL_SERVERS)
                   + f", at most {capabilities.TOOL_SERVERS_MAX} of them, such as "
                   '{"gh": "github"}')
    return out
