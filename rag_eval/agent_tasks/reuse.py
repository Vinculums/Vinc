"""끊긴 회차의 원본 기록을 다시 쓴다 — 같은 실행을 모델에 다시 보내지 않는다.

회차는 끝에서야 결과를 쓴다. 그래서 중간에 끊기면(컨테이너 회수, 시간 한도) 그때까지 쓴 원본 기록만
남는다. ``--reuse-traces`` 는 그 기록 중 지금 계획한 실행과 **첫 사건들이 똑같은** 것을 찾아, 모델과
도구를 부르지 않고 그 기록에서 실행의 출력을 되살린다. 채점·게이트·실패 분류는 평소대로 다시 돈다.

- 맞추는 열쇠는 모델 호출 앞의 사건(``start`` 와, 있으면 ``context``) 전부다. 과제·비교군·호스트·
  모델·실행 번호·프롬프트와, 역할 맥락이면 그 블록의 해시와 목록까지 같아야 한다.
- 모델 오류나 호스트 오류로 끝난 기록, ``final`` 이 없는 기록은 다시 쓰지 않는다. 그 실행은 다시 돈다.
- 되살린 행은 ``reused`` 가 참이고 ``latency_s`` 가 None 이다(걸린 시간은 기록에 없다).
"""
from __future__ import annotations

import json
import pathlib

from rag_eval.agent_tasks import hosts as hosts_mod

_RUN_EVENTS = ("model", "tool")


def _key(prefix: list[dict]) -> str:
    return json.dumps(prefix, sort_keys=True, ensure_ascii=False)


def _split(events: list[dict]) -> tuple[list[dict], list[dict], dict | None]:
    """(모델 앞의 사건, 실행 사건, final 사건)."""
    i = next((n for n, e in enumerate(events) if e["t"] in _RUN_EVENTS + ("final",)), len(events))
    j = next((n for n, e in enumerate(events) if e["t"] == "final"), None)
    if j is None:
        return events[:i], events[i:], None
    return events[:i], events[i:j], events[j]


def output(run: list[dict], final: dict) -> hosts_mod.RunOutput:
    """실행 사건에서 :class:`hosts.RunOutput` 을 되살린다(:class:`hosts.Stepper` 가 세는 그대로)."""
    model = [e for e in run if e["t"] == "model"]
    costs = [e["list_cost_usd"] for e in model if e.get("list_cost_usd") is not None]
    versions: list[str] = []
    for e in model:
        if e.get("model_version") and e["model_version"] not in versions:
            versions.append(e["model_version"])
    return hosts_mod.RunOutput(
        final_text=final["text"], steps=len(model),
        tool_calls=sum(1 for e in run if e["t"] == "tool"),
        tokens_in=sum(e["tokens_in"] for e in model), tokens_out=sum(e["tokens_out"] for e in model),
        model_error=final.get("model_error") or "", model_versions=versions,
        list_cost_usd=round(sum(costs), 8) if costs else None)


class TraceReuse:
    """원본 기록 디렉터리들의 색인. :meth:`take` 는 맞는 기록을 한 번만 내준다."""

    def __init__(self, dirs: list[pathlib.Path]) -> None:
        self.dirs = [pathlib.Path(d) for d in dirs]
        self._by_key: dict[str, tuple[list[dict], dict]] = {}
        self.skipped = 0
        for d in self.dirs:
            if not d.is_dir():
                raise FileNotFoundError(f"원본 기록 디렉터리가 없다: {d}")
            for path in sorted(d.glob("*.jsonl")):
                try:
                    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                except (UnicodeDecodeError, ValueError):
                    self.skipped += 1
                    continue
                prefix, run, final = _split(events)
                if (not prefix or prefix[0].get("t") != "start" or final is None
                        or final.get("model_error") or final.get("host_error")):
                    self.skipped += 1
                    continue
                self._by_key.setdefault(_key(prefix), (run, final))
        self.used = 0

    def __len__(self) -> int:
        return len(self._by_key)

    def take(self, prefix: list[dict]) -> tuple[list[dict], hosts_mod.RunOutput] | None:
        """``prefix`` (지금 실행의 모델 앞 사건)와 같은 기록의 (실행 사건, 출력). 없으면 None."""
        hit = self._by_key.pop(_key(prefix), None)
        if hit is None:
            return None
        self.used += 1
        run, final = hit
        return run, output(run, final)

    def describe(self) -> dict:
        return {"dirs": [str(d) for d in self.dirs], "available": len(self) + self.used,
                "skipped": self.skipped, "used": self.used}
