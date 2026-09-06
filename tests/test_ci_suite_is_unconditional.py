# -*- coding: utf-8 -*-
"""머지를 막는 스위트가 **조건 없이** 도는가.

왜 검사인가: 이 함정은 적어 두는 것으로 안 멈췄다. `pytest tests` 를 도는 잡에
경로 조건이 붙어 있던 동안, 그 조건이 거짓인 PR 은 소유자 격리·쓰기 테넌시 같은
케이스를 **한 건도 안 돌린 채 초록으로 머지됐다**(런 34044028449 = #237). 조건을
걷은 뒤에도 누군가 "이 잡은 파이썬이 안 바뀌면 중복"이라고 다시 붙일 수 있고,
그때 CI 는 아무 말도 하지 않는다 — 잡이 `skipped` 면 필수 검사는 성공으로 센다.

무엇을 재는가: `pytest ... tests` 를 실제로 부르는 잡이 **적어도 하나 있고**, 그런
잡 전부가 `if:` 도 `needs:` 도 갖지 않는가. 둘을 함께 보는 이유는 스위치가 둘이기
때문이다 — 조건이 거짓이어도 꺼지고, 선행 잡이 빨갛거나 큐에 남아도 꺼진다.

⚠ 호출을 세는 것이지 문자열을 세는 것이 아니다. 첫 판에서는 `run` 안에 "pytest" 와
"tests" 가 들어 있으면 셌는데, 그러면 **셸 주석 한 줄이 잡을 후보로 만든다**(실제로
`changes` 잡이 그렇게 잡혔다). 그래서 주석 줄을 버리고 명령 자리에 선 `pytest` 만 본다.

이 파일이 `unittest.TestCase` 인 것은 우연이 아니다. 모듈 수준 `def test_*` 로
쓰면 이 검사 자신이 `verify.py`(unittest 로더)에 안 보이고, 그러면 자기가 막으려는
구멍 안에 들어앉는다. 근거: [[record:writing-a-trap-down-does-not-stop-it-a-check-does]]
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CI = _ROOT / ".github" / "workflows" / "ci.yml"

#: 명령 자리의 `pytest` 또는 `... -m pytest` 이고 인자에 `tests` 가 있는 줄.
_INVOKES = re.compile(r"(?:^|[|&;]\s*)(?:\S*python\S*\s+-m\s+)?pytest\b.*\btests\b")


def pytest_jobs(doc: dict) -> dict[str, dict]:
    """`pytest` 로 `tests` 전량을 부르는 잡들. 잡 이름 → 잡 정의."""
    out = {}
    for name, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            for raw in (step.get("run") or "").split("\n"):
                line = raw.strip()
                if line.startswith("#"):
                    continue
                if _INVOKES.search(line):
                    out[name] = job
                    break
            if name in out:
                break
    return out


def offences(doc: dict) -> list[str]:
    """전량 스위트를 끌 수 있는 자리들. 비어 있어야 한다."""
    jobs = pytest_jobs(doc)
    if not jobs:
        return ["ci.yml 에 `pytest tests` 를 도는 잡이 하나도 없다"]
    bad = []
    for name, job in sorted(jobs.items()):
        if "if" in job:
            bad.append(f"{name} 에 `if:` 가 있다 ({job['if']!r}) — 거짓이면 조용히 skip 된다")
        if "needs" in job:
            bad.append(f"{name} 에 `needs: {job['needs']!r}` 가 있다 — 선행 잡이 빨갛거나 "
                       "큐에 남으면 함께 꺼진다")
    return bad


class FullSuiteHasNoOffSwitch(unittest.TestCase):
    def test_the_pytest_job_is_unconditional(self):
        import yaml
        doc = yaml.safe_load(_CI.read_text(encoding="utf-8"))
        self.assertEqual(offences(doc), [], "전량 스위트를 끌 수 있는 자리가 생겼다")


if __name__ == "__main__":
    unittest.main()
