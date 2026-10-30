"""비교군 F — 하네스 전체, 그리고 그 부품을 하나씩 뺀 비교군(VIN-788 절제).

F 는 어댑터가 실제로 하는 일을 한 자리에 모은다.

| 부품 | F 에서 | 빼는 비교군 |
|---|---|---|
| 역할 맥락 | 과제의 역할을 기본 정책(TASK_EVIDENCE)으로 컴파일한 블록 | ``F-context`` |
| 도구 전체 | 배포된 Vinc MCP 도구 전부(A2 와 같다) | ``F-tools``: 읽기 도구 셋만 |
| 도구 정책 | 능력 id·``disallowed_tools`` 로 도구를 숨기고 승인 도구 호출을 거절 | ``F-toolpolicy`` |
| 출력 게이트 | 마지막 답을 역할의 출력 게이트에 넣는다. block 이면 내보내지 않고, interrupt 면 기다리는 결정을 붙여 내보낸다 | ``F-outgate`` |
| 근거 계약 | 시스템 지시와 블록의 ``Sources:`` 계약 | ``F-sources`` |

도구 정책과 출력 게이트는 역할 계약을 리포의 로스터 스냅샷(`agents/roles.json`)에서 읽는다. 핀
스토어의 역할은 핀을 만든 날의 계약이라 그 뒤에 더한 능력 id 가 없기 때문이다. 로스터에 없는 역할은
핀의 계약을 쓴다. 역할 맥락(필수 문서와 과제 근거)은 핀에서 컴파일한다.

게이트가 답을 막으면(block) :data:`WITHHELD` 문장이 산출물이 된다. 사람의 결정을 기다리는 답(interrupt)은
``OutputGate.deliver`` 와 같이 그 결정을 붙여 나간다. 그래서 채점은 사용자에게 실제로 나간 글을 본다. 도구 호출은 게이트 앞에서 이미 일어났으므로 무대 도구 채점은 그대로 원본 기록을 본다.
근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25 §6.4]]
"""
from __future__ import annotations

import json
import pathlib

from rag_eval.agent_tasks import checkers
from rag_eval.agent_tasks import role_context as role_mod

FULL_ARM = "F"
ABLATIONS = ("context", "tools", "toolpolicy", "outgate", "sources")
FULL_ARMS = (FULL_ARM,) + tuple(f"{FULL_ARM}-{a}" for a in ABLATIONS)
ROSTER = pathlib.Path(__file__).resolve().parents[2] / "agents" / "roles.json"
#: 게이트가 답을 막았을 때(block) 사용자에게 나가는 글. 채점은 이 글을 본다.
WITHHELD = ("[The role's output gate withheld this answer: {verdict} ({checks}). "
            "A person has to review it before it goes out.]")


def removed(arm: str) -> str | None:
    """F 비교군이 뺀 부품. ``F`` 는 None, F 계열이 아니면 :class:`ValueError`."""
    if arm == FULL_ARM:
        return None
    part = arm.split("-", 1)[1] if arm.startswith(FULL_ARM + "-") else ""
    if part not in ABLATIONS:
        raise ValueError(f"모르는 F 비교군 {arm!r}")
    return part


def strip_sources(text: str) -> str:
    """블록에서 근거 계약을 뺀다: ``Sources:`` 지시 문장과 끝의 인용 계약."""
    from vinc_client import SOURCES_LINE   # noqa: PLC0415
    cut = text.rfind("Citation contract for this run:")
    if cut >= 0:
        text = text[:cut].rstrip()
    return text.replace(SOURCES_LINE, "").rstrip()


def system_without_sources(system: str) -> str:
    """시스템 지시에서 마지막 ``Sources`` 줄 요구를 뺀다(:data:`arms.BASE_SYSTEM` 의 끝 문장)."""
    cut = system.find(" End your answer with one final line \"Sources:")
    return system[:cut] if cut >= 0 else system


class FullHarness:
    """역할마다 맥락 컴파일러·도구 정책·출력 게이트를 한 번 만들어 둔다."""

    def __init__(self, vinc, default_role: str, limit: int, reads_dir: pathlib.Path,
                 roster: pathlib.Path = ROSTER) -> None:
        from vinc_client import output_gate   # noqa: PLC0415
        from vinc_client.compiler import default_policy   # noqa: PLC0415
        if not hasattr(output_gate, "with_pending"):
            # 그 전의 게이트는 결정을 기다리는 답을 감춘다. 다른 규칙으로 잰 F 를 같은 이름으로 내지 않는다.
            raise RuntimeError("vinc-client 의 출력 게이트에 with_pending 이 없다: interrupt 전달 규칙이 "
                               "다른 판이다. 리포의 integrations/client/python 을 깔 것")
        if output_gate.PENDING_HEADER != checkers.GATE_NOTE:
            # 채점은 이 머리글로 "게이트가 사람에게 넘겼다"를 가른다. 문구가 다르면 그 구분이 조용히 사라진다.
            raise RuntimeError("vinc-client 의 PENDING_HEADER 가 checkers.GATE_NOTE 와 다르다: 둘을 같이 고칠 것")
        self.vinc, self.default_role, self.limit, self.reads_dir = vinc, default_role, limit, reads_dir
        self.policy = default_policy(read_tool=True)
        doc = json.loads(roster.read_text(encoding="utf-8"))
        self.roster = {r["id"]: r for r in doc["roles"]}
        self._contexts: dict[str, role_mod.RoleContext] = {}
        self._contracts: dict[str, dict] = {}

    def settings(self) -> dict:
        return {"policy": self.policy.id, "limit_chars": self.limit, "default_role": self.default_role,
                "contracts_for_tool_policy_and_gate": "agents/roles.json, else the pinned role",
                "withheld_text": WITHHELD}

    def role_of(self, task) -> str:
        return task.role or self.default_role

    def compile(self, role: str, prompt: str) -> role_mod.Compiled:
        if role not in self._contexts:
            self._contexts[role] = role_mod.RoleContext(self.vinc, role, self.policy, self.limit,
                                                        self.reads_dir)
        return self._contexts[role].compile(prompt)

    def contract(self, role: str) -> dict:
        """도구 정책과 게이트가 읽는 계약: 로스터 스냅샷, 없으면 핀의 역할."""
        if role not in self._contracts:
            if role in self.roster:
                self._contracts[role] = self.roster[role]
            else:
                from vinc_client import VincClient   # noqa: PLC0415
                with VincClient(self.vinc.key, **self.vinc.client_args()) as client:
                    self._contracts[role] = client.role(role)
        return self._contracts[role]

    def tool_policy(self, role: str):
        from vinc_client import RoleToolPolicy   # noqa: PLC0415
        return RoleToolPolicy.from_contract(self.contract(role))

    def gate(self, role: str):
        from vinc_client import OutputGate   # noqa: PLC0415
        return OutputGate(self.contract(role).get("props") or {})


def gate_answer(gate, text: str) -> tuple[str, dict]:
    """(사용자에게 나갈 글, 원본 기록 사건). 빈 답은 게이트에 넣지 않는다(채점에서 빠진다).

    ``OutputGate.deliver`` 의 output 단계와 같다: pass 는 그대로, interrupt 는 기다리는 결정을 붙여,
    block 은 :data:`WITHHELD` 로.
    """
    if gate is None or not (text or "").strip():
        return text, {"t": "gate", "verdict": None}
    got = gate.evaluate(text)
    event = {"t": "gate", "verdict": got.verdict, "failed_checks": list(got.failed_checks),
             "person_only_actions": list(got.person_only_actions)}
    if got.verdict == "pass":
        return text, event
    if got.verdict == "interrupt":
        from vinc_client.output_gate import with_pending   # noqa: PLC0415
        return with_pending(text, got, gate.person_only), event
    return WITHHELD.format(verdict=got.verdict, checks=", ".join(got.failed_checks) or "none"), event
