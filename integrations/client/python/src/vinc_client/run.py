"""에이전트 실행 하나를 Episode 하나로 기록한다(실행 기록).

원본 실행 기록(프롬프트·도구 호출·도구 결과·출력)은 그래프에 넣지 않는다. 파일로 두고
``trace_ref`` 에 sha256·바이트 수·파일 이름만 남긴다. 그 자리에 요약을 넣지 않는다 — 하네스
탐색은 요약이 아니라 원본을 읽어야 한다. 로컬 경로도 싣지 않는다(``uri`` 는 공개해도 되는
위치를 호출자가 알 때만).

같은 ``run_id`` 는 늘 같은 episode id 가 된다. 그래서 한 실행의 재시도는 같은 노드로 합쳐지고,
다른 실행은 다른 노드다. ``props`` 는 쓸 때마다 통째로 바뀌므로 다시 쓸 때는 그 실행의 값을
전부 보낸다.
근거: [[decision:agent-run-record-keeps-the-raw-trace-as-a-hashed-file-2026-10-25]]
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Iterable, Mapping

from .episode import build_episode_fragment, check_about

ROLE_PREFIX = "concept:agent-"
OUTCOMES = ("success", "failure", "blocked", "waiting", "abandoned", "error")
#: 실행 id 로 받는 글자. 서버가 붙이는 id 와 호스트의 run id 를 둘 다 받을 만큼 넓다.
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COUNTS = ("steps", "tool_calls", "tokens_in", "tokens_out")


def run_episode_id(run_id: str) -> str:
    """``episode:run-<slug>-<8 hex>``. 슬러그는 읽기용이고 접미는 원래 id 의 해시라서
    대소문자나 구두점만 다른 두 실행이 한 노드로 합쳐지지 않는다."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be 1 to 128 letters, digits, '.', '_', ':' or '-'")
    slug = re.sub(r"[^a-z0-9]+", "-", run_id.lower()).strip("-")[:48].strip("-") or "run"
    return f"episode:run-{slug}-{hashlib.sha256(run_id.encode('utf-8')).hexdigest()[:8]}"


def trace_ref(path: str | os.PathLike[str], *, uri: str | None = None) -> dict[str, Any]:
    """원본 기록 파일을 읽어 그 주소를 만든다: ``{sha256, bytes, name[, uri]}``."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
    ref: dict[str, Any] = {"sha256": digest.hexdigest(), "bytes": size,
                           "name": os.path.basename(os.fspath(path))}
    if uri:
        ref["uri"] = str(uri)
    return ref


def _check_trace(trace: Mapping[str, Any]) -> dict[str, Any]:
    ref = dict(trace)
    if not isinstance(ref.get("sha256"), str) or not _SHA256.fullmatch(ref["sha256"]):
        raise ValueError("trace needs a lowercase sha256 hex digest; build it with trace_ref()")
    if not isinstance(ref.get("bytes"), int) or isinstance(ref["bytes"], bool) or ref["bytes"] < 0:
        raise ValueError("trace needs a non-negative byte count")
    return ref


def build_run_fragment(run_id: str, *, role: str, outcome: str, title: str | None = None,
                       summary: str = "", task_ref: str | None = None, host: str | None = None,
                       model_family: str | None = None, steps: int | None = None,
                       tool_calls: int | None = None, tokens_in: int | None = None,
                       tokens_out: int | None = None, cost_usd: float | None = None,
                       gate_results: Mapping[str, str] | None = None,
                       cited_ids: Iterable[str] = (),
                       shims_fired: Mapping[str, int] | None = None,
                       trace: Mapping[str, Any] | None = None, about: Iterable[str] = (),
                       date: str | None = None, domain: str | None = None,
                       tags: Iterable[str] | None = None) -> dict[str, Any]:
    """Nodes and edges for ``POST /v1/fragments``: one run Episode ``about`` its role.

    ``cost_usd`` is whatever the host reports (a subscription run reports a
    list-price equivalent). Values left as ``None`` are not stored.
    """
    if not isinstance(role, str) or not role.startswith(ROLE_PREFIX):
        raise ValueError(f"role must be a role id starting with {ROLE_PREFIX!r}")
    if outcome not in OUTCOMES:
        raise ValueError("outcome must be one of: " + ", ".join(OUTCOMES))
    props: dict[str, Any] = {"run_id": run_id, "role": role, "outcome": outcome}
    for key, value in (("task_ref", task_ref), ("host", host), ("model_family", model_family)):
        if value is not None:
            props[key] = str(value)
    counts = {"steps": steps, "tool_calls": tool_calls, "tokens_in": tokens_in,
              "tokens_out": tokens_out}
    for key in _COUNTS:
        value = counts[key]
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        props[key] = value
    if cost_usd is not None:
        if isinstance(cost_usd, bool) or not isinstance(cost_usd, (int, float)) or cost_usd < 0:
            raise ValueError("cost_usd must be a non-negative number")
        props["cost_usd"] = float(cost_usd)
    if gate_results is not None:
        props["gate_results"] = {str(k): str(v) for k, v in gate_results.items()}
    cited = [str(c).strip() for c in cited_ids if str(c).strip()]
    bad = [c for c in cited if ":" not in c]
    if bad:
        raise ValueError("cited_ids must be graph ids with a type prefix: " + ", ".join(bad))
    if cited:
        props["cited_ids"] = list(dict.fromkeys(cited))
    if shims_fired is not None:
        fired: dict[str, int] = {}
        for key, value in shims_fired.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("shims_fired counts must be non-negative integers")
            fired[str(key)] = value
        props["shims_fired"] = fired
    if trace is not None:
        props["trace_ref"] = _check_trace(trace)
    targets = check_about([role, *about])
    fragment = build_episode_fragment(
        title or f"Run {run_id} of {role[len('concept:'):]}: {outcome}",
        summary=summary, about=targets, date=date, domain=domain,
        tags=["agent-run"] if tags is None else tags, episode_id=run_episode_id(run_id))
    fragment["nodes"][0]["props"] = props
    return fragment
