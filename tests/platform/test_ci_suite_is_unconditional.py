# -*- coding: utf-8 -*-
"""머지를 막는 스위트가 **조건 없이 전량으로** 도는가.

왜 검사인가: 이 함정은 적어 두는 것으로 안 멈췄다. `pytest tests` 를 도는 잡에
경로 조건이 붙어 있던 동안, 그 조건이 거짓인 PR 은 소유자 격리·쓰기 테넌시 같은
케이스를 **한 건도 안 돌린 채 초록으로 머지됐다**(런 34044028449 = #237). 조건을
걷은 뒤에도 누군가 "이 잡은 파이썬이 안 바뀌면 중복"이라고 다시 붙일 수 있고,
그때 CI 는 아무 말도 하지 않는다 — 잡이 `skipped` 면 필수 검사는 성공으로 센다.

무엇을 재는가: `pytest ... tests` 를 실제로 부르는 자리가 **적어도 하나 있고**, 그
자리마다 스위치가 하나도 없는가. 스위치는 넷이다.

1. 잡의 `if:` — 거짓이면 조용히 skip 된다
2. 잡의 `needs:` — 선행 잡이 빨갛거나 큐에 남아도 함께 꺼진다
3. **스텝의 `if:`** — 잡은 초록인데 스위트만 안 돈다. 1이 막히면 다음 사람이 조건을
   놓을 자리가 정확히 여기다
4. **수집을 좁히는 인자** — `--ignore` 하나로 이 패키지의 주제인 보안 파일을
   이름으로 빼도 잡은 무조건이고 초록이다

3·4 는 첫 판이 못 잡았다(2026-09-07 리뷰가 셋 다 실증). 1·2 만 보면 "잡이 돈다"는
말했지만 "무엇을 돌렸나"는 못 말한다.

⚠ 호출을 세는 것이지 문자열을 세는 것이 아니다. 첫 판에서는 `run` 안에 "pytest" 와
"tests" 가 들어 있으면 셌는데, 그러면 **셸 주석 한 줄이 잡을 후보로 만든다**(실제로
`changes` 잡이 그렇게 잡혔다). 그래서 주석 줄을 버리고 명령 자리에 선 `pytest` 만 본다.

이 파일이 `unittest.TestCase` 인 것은 우연이 아니다. 모듈 수준 `def test_*` 로
쓰면 이 검사 자신이 `verify.py`(unittest 로더)에 안 보이고, 그러면 자기가 막으려는
구멍 안에 들어앉는다. 근거: [[record:writing-a-trap-down-does-not-stop-it-a-check-does]]
"""
from __future__ import annotations

import re
import shlex
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CI = _ROOT / ".github" / "workflows" / "ci.yml"

#: 명령 자리의 `pytest` 또는 `... -m pytest`. `VINC_X=1 python -m pytest` 처럼 env
#: 접두가 붙는 형태도 명령 자리다(옛 판은 이것을 "호출 없음"으로 읽어 거짓 빨강을
#: 냈다). 인자는 아래에서 따로 본다 — 이 패턴은 "어디서부터 인자인가"만 정한다.
_INVOKES = re.compile(
    r"(?:^|[|&;]\s*)(?:\w+=\S*\s+)*(?:\S*python\S*\s+-m\s+)?pytest\b"
)

#: 전량을 뜻하는 인자. `tests/test_foo.py` 는 전량이 아니므로 여기 안 든다 —
#: 그런 줄은 애초에 "전량을 도는 자리"로 안 세고, 자리가 0이면 그 자체가 위반이다.
_WHOLE_TREE = {"tests", "tests/", "./tests", "./tests/"}

#: **수집을 좁히는** 인자들. 이것들이 붙으면 잡은 무조건이고 초록인데 어떤 파일은
#: 안 돈다 — 이 검사가 막으려는 바로 그 상태다.
#:
#: 일부러 **허용**하는 것들과 그 이유:
#:   `-q`·`-p`·`--durations`·`-n`·env 접두 → 무엇이 도는지 안 바꾼다(형식·플러그인)
#:   `-x`·`--maxfail` → 도중에 멈추지만 멈추는 조건이 **실패**다. 빨강을 숨기지 못하니
#:                      숨김 스위치가 아니다(느려서 쓰는 사람이 있고, 막을 이유가 없다)
#: 즉 이 목록의 기준은 "덜 돈다"가 아니라 "빨강을 숨긴 채 덜 돈다"다.
_NARROWING_LONG = {
    "--ignore", "--ignore-glob",          # 경로로 뺀다
    "--deselect",                          # 이름으로 뺀다
    "--lf", "--last-failed",               # 지난 실패만
    "--ff", "--failed-first",              # 순서만 같지만 --lf 계열과 짝이라 함께 막는다
    "--co", "--collect-only",              # 아예 안 돈다
}
#: 짧은 플래그는 값이 붙어 올 수 있다(`-k expr` · `-kexpr`). 첫 글자만 본다.
_NARROWING_SHORT = "km"


#: 전량 잡에 허용하는 단 하나의 잡 조건(소유자 판정 2026-11-13). 그 밖의 `if:` 는 그대로 위반이다.
PR_ONLY_SKIP = "github.event_name != 'pull_request'"


def _args_after_pytest(line: str) -> list[str] | None:
    """명령 자리의 `pytest` 뒤 인자들. 그런 호출이 아니면 None."""
    m = _INVOKES.search(line)
    if not m:
        return None
    rest = line[m.end():]
    try:
        return shlex.split(rest)
    except ValueError:          # 따옴표가 안 맞는 줄 — 나누기라도 한다
        return rest.split()


def _invocations(doc: dict):
    """`pytest` 로 `tests` **전량**을 부르는 자리들. (잡 이름, 잡, 스텝, 인자들)."""
    for name, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            for raw in (step.get("run") or "").split("\n"):
                line = raw.strip()
                if line.startswith("#"):
                    continue
                args = _args_after_pytest(line)
                if args and _WHOLE_TREE.intersection(args):
                    yield name, job, step, args


def _narrowing(args: list[str]) -> list[str]:
    """수집을 좁히는 인자들의 이름."""
    found = []
    for tok in args:
        head = tok.split("=", 1)[0]
        if head in _NARROWING_LONG:
            found.append(head)
        elif len(tok) > 1 and tok[0] == "-" and tok[1] != "-" and tok[1] in _NARROWING_SHORT:
            found.append(tok[:2])
    return found


def offences(doc: dict) -> list[str]:
    """전량 스위트를 끌 수 있는 자리들. 비어 있어야 한다."""
    runs = list(_invocations(doc))
    if not runs:
        return ["ci.yml 에 `pytest` 로 `tests` 전량을 도는 자리가 하나도 없다"]

    bad = []
    triggers = doc.get("on", doc.get(True)) or {}
    for name, job in sorted({n: j for n, j, _s, _a in runs}.items()):
        if job.get("if") == PR_ONLY_SKIP and "merge_group" in triggers:
            # 허용하는 조건은 이것 하나다: PR 런에서만 빠지고 머지 큐와 main push 에서는 전량이 돈다.
            # 머지 큐가 없는 워크플로라면 PR 이 전량 없이 들어갈 수 있으므로 허용하지 않는다.
            pass
        elif "if" in job:
            bad.append(f"{name} 에 `if:` 가 있다 ({job['if']!r}) — 거짓이면 조용히 skip 된다")
        if "needs" in job:
            bad.append(f"{name} 에 `needs: {job['needs']!r}` 가 있다 — 선행 잡이 빨갛거나 "
                       "큐에 남으면 함께 꺼진다")
    for name, _job, step, args in sorted(runs, key=lambda r: r[0]):
        if "if" in step:
            bad.append(f"{name} 의 pytest 스텝에 `if:` 가 있다 ({step['if']!r}) — 잡은 "
                       "초록인데 스위트만 안 돈다")
        narrowed = _narrowing(args)
        if narrowed:
            bad.append(f"{name} 의 pytest 가 {narrowed} 로 좁혀져 있다 — 전량이 아니다")
    return bad


class FullSuiteHasNoOffSwitch(unittest.TestCase):
    def test_only_the_pull_request_skip_is_allowed(self):
        job = {"if": PR_ONLY_SKIP, "steps": [{"run": "python -m pytest -q tests"}]}
        queue = {"on": {"pull_request": None, "merge_group": None}, "jobs": {"s": job}}
        self.assertEqual(offences(queue), [])
        no_queue = {"on": {"pull_request": None}, "jobs": {"s": job}}
        self.assertTrue(offences(no_queue), "머지 큐가 없으면 PR 런 건너뛰기도 위반이다")
        other = {"on": {"merge_group": None}, "jobs": {"s": {**job, "if": "needs.changes.outputs.python == 'true'"}}}
        self.assertTrue(offences(other), "다른 조건은 여전히 위반이다")

    def test_the_pytest_job_is_unconditional(self):
        import yaml
        doc = yaml.safe_load(_CI.read_text(encoding="utf-8"))
        self.assertEqual(offences(doc), [], "전량 스위트를 끌 수 있는 자리가 생겼다")


if __name__ == "__main__":
    unittest.main()
