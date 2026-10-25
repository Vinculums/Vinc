"""원본 기록 · 도구 표면 고장 분류 · 실행 기록 조각.

원본 기록(trace)은 실행 하나에서 모델이 받은 입력 전부, 모델 출력, 도구 호출과 그 결과를
JSONL 로 담는다. 요약을 그 자리에 넣지 않는다. 파일 이름은 내용의 sha256 이고, 실행 기록
조각(Episode)은 그 해시만 ``trace_ref`` 로 가리킨다. 시각·소요 시간은 원본 기록에 싣지 않는다:
같은 입력이면 같은 바이트가 되어야 두 실행을 바이트로 대조할 수 있다(소요 시간은 실행 행에 둔다).

실행 기록 조각은 ``vinc_client.build_run_fragment`` 로만 만든다. 그 함수가 실행 id 에서
episode id 를 정하므로 같은 실행의 재시도는 한 노드로 합쳐진다.
근거: [[decision:agent-run-record-keeps-the-raw-trace-as-a-hashed-file-2026-10-25]]
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

#: 도구 표면 고장의 종류. 추론 실패와 따로 센다.
SURFACE_KINDS = ("not_found", "argument_error", "truncation", "read_only", "tool_error",
                 "unavailable")
_RUN_ID_BAD = re.compile(r"[^A-Za-z0-9._:-]+")


class Trace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def add(self, event: dict) -> None:
        self.events.append(event)

    def to_bytes(self) -> bytes:
        return b"".join(json.dumps(e, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
                        for e in self.events)

    def sha256(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def write(self, directory: pathlib.Path) -> pathlib.Path:
        directory.mkdir(parents=True, exist_ok=True)
        data = self.to_bytes()
        path = directory / f"{hashlib.sha256(data).hexdigest()}.jsonl"
        if not path.exists():
            path.write_bytes(data)
        return path


def envelope_text(envelope) -> str:
    """MCP 도구 결과 봉투의 텍스트. 문자열이면 그대로."""
    if isinstance(envelope, str):
        return envelope
    parts = [c.get("text") or "" for c in (envelope or {}).get("content") or []
             if isinstance(c, dict)]
    return "\n".join(parts)


def classify(envelope) -> str | None:
    """도구 결과 하나가 도구 표면 고장인가, 그렇다면 어떤 종류인가. 정상 결과면 None.

    봉투(dict)면 ``isError`` 와 ``_meta`` 표식을 먼저 보고, 문자열(어댑터가 돌려준 문장)이면
    어댑터의 고정 문장으로 본다.
    """
    meta = (envelope or {}).get("_meta") or {} if isinstance(envelope, dict) else {}
    if "vinc_invalid_argument" in meta:
        return "argument_error"
    if "vinc_too_large" in meta:
        return "truncation"
    text = envelope_text(envelope)
    low = text.lower()
    is_error = bool(isinstance(envelope, dict) and envelope.get("isError"))
    if isinstance(envelope, dict) and envelope.get("_transport_error"):
        return "unavailable"
    if "is not available right now" in low:
        return "unavailable"
    if "found nothing for that" in low:
        return "not_found"
    if is_error:
        if "read-only" in low:
            return "read_only"
        if "invalid argument" in low or "invalid_argument" in low or "argument" in low:
            return "argument_error"
        if "not found" in low or "not_found" in low or "no such" in low:
            return "not_found"
        return "tool_error"
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(payload, dict):
        if payload.get("shrunk"):
            return "truncation"
        if payload.get("found") is False:
            return "not_found"
        err = payload.get("error")
        if isinstance(err, dict) and err.get("code") in ("not_found", "unreadable"):
            return "not_found"
    return None


def failure_class(grade: dict, events: list[dict], model_error: str) -> str:
    """실패 한 건을 어디에 셀까: ok · model_error · tool_surface · reasoning."""
    if grade.get("success"):
        return "ok"
    if model_error or not grade.get("parse_ok"):
        return "model_error"
    if any(e.get("t") == "tool" and e.get("kind") in SURFACE_KINDS for e in events):
        return "tool_surface"
    return "reasoning"


def run_id(*parts: str) -> str:
    """실행 id. 허용 글자 밖은 하이픈으로, 128자 안으로."""
    raw = ".".join(_RUN_ID_BAD.sub("-", p).strip("-") for p in parts if p)
    if len(raw) > 128:
        raw = raw[:119] + "." + hashlib.sha256(raw.encode()).hexdigest()[:8]
    return raw


def run_fragment(*, rid: str, role: str, outcome: str, trace_path: pathlib.Path, task_ref: str,
                 host: str, model_family: str, steps: int, tool_calls: int, tokens_in: int,
                 tokens_out: int, cost_usd: float | None, gate_results: dict, cited_ids: list[str],
                 date: str, domain: str, about: tuple[str, ...] = ()) -> dict:
    """실행 하나의 Episode 조각(노드와 ``about`` 엣지). vinc-client 의 도우미로만 만든다."""
    from vinc_client import build_run_fragment, trace_ref   # noqa: PLC0415 — 쓰는 실행에서만 든다
    # 근거 id 중 노드 id 만 싣는다. 단순 RAG 가 대는 문서 id 는 그래프 id 가 아니다.
    cited = [c for c in cited_ids if ":" in c and not c.startswith("doc:")]
    return build_run_fragment(
        rid, role=role, outcome=outcome, task_ref=task_ref, host=host,
        model_family=model_family, steps=steps, tool_calls=tool_calls, tokens_in=tokens_in,
        tokens_out=tokens_out, cost_usd=cost_usd, gate_results=gate_results,
        cited_ids=cited, trace=trace_ref(trace_path), about=about, date=date, domain=domain,
        tags=["agent-run", "agent-task-eval"])
