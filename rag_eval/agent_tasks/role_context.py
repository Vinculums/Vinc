"""비교군 R — 과제를 역할 하나로 돌리고, 그 역할의 맥락을 후보 정책으로 컴파일한다(VIN-790).

맥락은 ``vinc_client.compiler.compile_context`` 가 만든다. 정책 ``p0`` 은 오늘의 ``prefetch()`` 와
바이트가 같은 기준점이고, 그 밖의 정책은 ``POLICY`` 를 내보내는 파이썬 파일 하나다. 모델은 맥락
블록과 함께 읽기 도구 셋(brief · search · doc_get)을 받는다. 문서 읽기 도구가 있으므로 필수 문서를
줄인 계획도 허용된다(I4, O1). 계획이 불변식을 깨거나 예산을 넘으면 모델을 부르지 않고 그 과제를
실패로 센다: 맥락을 줄이려고 계약을 깨는 후보가 이득을 보지 못하게 한다.
근거: [[vinc.plan.agent-harness-work-order-2026-10-25]] 5d·5e. 설계는 컴파일러 인터페이스 명세(vinc.spec.context-compiler-interface) §7 이다.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
from dataclasses import dataclass, field

ROLE_ARM = "R"
#: 맥락 모드 어댑터가 주는 읽기 도구(VIN-827 의 문서 읽기 포함). 같은 이름의 MCP 도구로 실행한다.
READ_TOOLS = ("vinc_brief", "vinc_search", "vinc_doc_get")
DEFAULT_ROLE = "concept:agent-maker"
#: 글자 수 한도. 이 핀에서 P0 의 가장 큰 역할(161,022자)이 들어가는 값이다.
DEFAULT_LIMIT = 400_000


def load_policy(spec: str):
    """(정책, 원본 바이트). ``p0`` 은 컴파일러의 전문 정책, 그 밖은 ``POLICY`` 를 내보내는 파일."""
    from vinc_client.compiler import FULL_TEXT, ContextPolicy   # noqa: PLC0415
    if spec == "p0":
        return FULL_TEXT, None
    path = pathlib.Path(spec)
    source = path.read_bytes()
    name = "rag_eval_policy_" + hashlib.sha256(source).hexdigest()[:12]
    module_spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    policy = getattr(module, "POLICY", None)
    if not isinstance(policy, ContextPolicy) or not str(getattr(policy, "id", "")).strip():
        raise ValueError(f"{path}: POLICY 가 id 와 plan() 을 가진 ContextPolicy 가 아니다")
    return policy, source


@dataclass
class Compiled:
    """과제 하나의 컴파일 결과. ``error`` 가 있으면 블록이 없고 모델을 부르지 않는다."""
    block: str = ""
    sha256: str = ""
    manifest: dict = field(default_factory=dict)
    reads: list = field(default_factory=list)
    error: str = ""
    invariant: str = ""

    def event(self) -> dict:
        return {"t": "context", "sha256": self.sha256, "chars": len(self.block),
                "manifest": self.manifest, "reads": self.reads, "error": self.error,
                "invariant": self.invariant}


class RoleContext:
    """역할 하나와 정책 하나. 읽기 응답 본문은 ``reads_dir`` 에 해시 이름으로 한 번만 남긴다."""

    def __init__(self, vinc, role: str, policy, limit: int, reads_dir: pathlib.Path) -> None:
        self.vinc, self.role, self.policy, self.limit = vinc, role, policy, limit
        self.reads_dir = reads_dir
        self._cache: dict[str, Compiled] = {}

    def settings(self) -> dict:
        return {"role": self.role, "policy": self.policy.id, "limit_chars": self.limit,
                "read_tools": list(READ_TOOLS)}

    def compile(self, task: str) -> Compiled:
        if task not in self._cache:
            self._cache[task] = self._compile(task)
        return self._cache[task]

    def _compile(self, task: str) -> Compiled:
        from vinc_client import VincClient, VincError   # noqa: PLC0415
        from vinc_client.compiler import ContextRequest, compile_context   # noqa: PLC0415
        request = ContextRequest(role=self.role, task=task, max_chars=self.limit, read_tool=True)
        with VincClient(self.vinc.key, **self.vinc.client_args()) as client:
            try:
                out = compile_context(client, request, self.policy)
            except VincError as exc:
                details = getattr(exc, "details", None) or {}
                return Compiled(error=exc.code or type(exc).__name__,
                                invariant=str(details.get("invariant") or ""))
            except Exception as exc:   # noqa: BLE001 — 후보 코드의 고장은 그 과제의 실패다
                return Compiled(error=f"policy_error: {type(exc).__name__}: {str(exc)[:200]}")
        return Compiled(block=out.block, sha256=out.sha256, manifest=out.manifest,
                        reads=[self._keep(e) for e in out.trace])

    def _keep(self, entry: dict) -> dict:
        row = {"method": entry.get("method"), "args": entry.get("args")}
        if "error" in entry:
            row["error"] = (entry["error"] or {}).get("code")
            return row
        # 키 순서를 그대로 둔다. 렌더러가 그 순서로 블록을 쓰므로, 정렬하면 이 본문으로 블록을 다시 지을 수 없다.
        body = json.dumps(entry.get("response"), ensure_ascii=False)
        sha = entry.get("sha256") or hashlib.sha256(body.encode("utf-8")).hexdigest()
        self.reads_dir.mkdir(parents=True, exist_ok=True)
        path = self.reads_dir / f"{sha}.json"
        if not path.exists():
            path.write_text(body, encoding="utf-8")
        row.update(sha256=sha, chars=len(body))
        return row


def failure_class(compiled: Compiled) -> str:
    """컴파일이 막혔을 때의 실패 분류. 불변식 위반과 예산 초과를 가른다."""
    if compiled.error == "context_plan_rejected":
        return "policy_violation"
    if compiled.error == "prefetch_budget_exceeded":
        return "budget_exceeded"
    return "context_error"
