# -*- coding: utf-8 -*-
"""켠 모드 전량 스위트를 네 조각으로 나눈 모양이 그대로인가 (VIN-1007).

왜 검사인가: 조각으로 나누면 "전량이 돌았다"는 말이 잡 하나가 아니라 **여럿의 합**이
된다. 그 합이 깨지는 길은 조용하다:

1. 매트릭스 그룹이 `--splits` 와 어긋난다 — `--splits 4` 에 그룹 1·2·3 만 있으면 넷째
   조각은 아무도 안 돌고, 나머지 셋은 초록이다.
2. 게이트가 어떤 잡을 안 기다린다 — 필수 체크 `core (VINC_PK_NAMESPACED=on)` 는 게이트가
   보고하므로, 빠진 잡이 빨개도 체크는 초록이다.
3. 게이트에 조건이 붙는다 — skip 된 필수 체크는 성공으로 센다.
4. 시간 파일이 사라진다 — pytest-split 은 파일이 없으면 모두 같은 시간으로 나눠 조각이
   한쪽으로 쏠린다(빠지지는 않지만 체크가 다시 느려진다).
5. 한 잡이던 때의 스위트 밖 스텝(Neo4j 적합성 · PID 1 · 연동 패키지)이 옮기다 빠지거나
   매트릭스 안으로 들어가 네 번 돈다.

걸러내는 인자(`-k`·`-m`·`--deselect`)와 잡의 `if`·`needs` 는
`test_ci_suite_is_unconditional.py` 가 본다. 여기는 나눈 모양만 본다.

`unittest.TestCase` 인 이유는 그 파일과 같다 — `verify.py`(unittest 로더)에도 보이게.
"""
from __future__ import annotations

import json
import re
import shlex
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CI = _ROOT / ".github" / "workflows" / "ci.yml"

#: 룰셋이 요구하는 체크 이름. 바꾸면 룰셋을 같이 고쳐야 한다.
_GATE_NAME = "core (VINC_PK_NAMESPACED=on)"

#: 한 잡이던 때 스위트 밖에서 돌던 스텝들. 각각 정확히 한 번, 게이트 뒤에서 돌아야 한다.
_ONCE = {
    "Neo4j 적합성": re.compile(r"test_store_conformance\.py.*-k neo4j", re.S),
    "인코더 PID 1": re.compile(r"VINC_TEST_ENCODER_PID1=on"),
    "연동 패키지": re.compile(r"for pkg in integrations/\*/python"),
}


def _load() -> dict:
    import yaml
    return yaml.safe_load(_CI.read_text(encoding="utf-8"))


def _runs(job: dict) -> list[str]:
    return [s.get("run") or "" for s in job.get("steps") or []]


def _split_args(run: str) -> dict[str, str] | None:
    """`pytest ... --splits N ...` 호출의 pytest-split 인자들. 그런 호출이 아니면 None."""
    if "--splits" not in run or "pytest" not in run:
        return None
    toks = shlex.split(" ".join(line for line in run.splitlines()
                                if not line.strip().startswith("#")))
    out = {}
    for i, tok in enumerate(toks):
        if tok in ("--splits", "--group", "--durations-path", "--splitting-algorithm"):
            out[tok] = toks[i + 1]
            if tok == "--group" and toks[i + 1] == "${{":
                out[tok] = " ".join(toks[i + 1:i + 4])
        elif "=" in tok and tok.split("=", 1)[0] in ("--splits", "--durations-path"):
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def _needs(job: dict) -> list[str]:
    n = job.get("needs") or []
    return [n] if isinstance(n, str) else list(n)


class CorePknsShards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = _load()["jobs"]
        cls.shards = {k: j for k, j in cls.jobs.items()
                      if any(_split_args(r) for r in _runs(j))}
        cls.gates = {k: j for k, j in cls.jobs.items() if j.get("name") == _GATE_NAME}

    def test_조각_잡은_하나이고_그룹이_1부터_splits_까지다(self):
        self.assertEqual(len(self.shards), 1, f"--splits 를 쓰는 잡: {sorted(self.shards)}")
        (key, job), = self.shards.items()
        args = next(a for a in map(_split_args, _runs(job)) if a)
        splits = int(args["--splits"])
        self.assertGreaterEqual(splits, 2)
        self.assertEqual(args.get("--group"), "${{ matrix.group }}",
                         "그룹은 매트릭스에서 받아야 한다")
        groups = job["strategy"]["matrix"]["group"]
        self.assertEqual(groups, list(range(1, splits + 1)),
                         "매트릭스 그룹이 --splits 와 어긋나면 어떤 조각은 아무도 안 돈다")
        self.assertIs(job["strategy"].get("fail-fast"), False,
                      "한 조각이 빨개져도 나머지 조각의 실패 목록이 보여야 한다")
        self.assertFalse(str(job.get("name", key)).startswith("core ("),
                         "조각 이름이 `core (` 로 시작하면 hub_deploy_pick 이 허브 게이트로 센다")

    def test_게이트는_하나이고_켠_모드_잡을_전부_기다린다(self):
        self.assertEqual(len(self.gates), 1, f"{_GATE_NAME!r} 잡: {sorted(self.gates)}")
        (gkey, gate), = self.gates.items()
        self.assertEqual(str(gate.get("if", "")).replace("${{", "").replace("}}", "").strip(),
                         "always()",
                         "게이트는 선행 잡이 빨갛거나 취소돼도 돌아서 빨갛게 끝나야 한다 — "
                         "skip 된 필수 체크는 성공으로 센다")
        self.assertFalse(any("pytest" in r for r in _runs(gate)), "게이트는 스스로 돌지 않는다")
        flagged = {k for k, j in self.jobs.items()
                   if k != gkey and str((j.get("env") or {}).get("VINC_PK_NAMESPACED")) == "on"}
        self.assertTrue(set(self.shards) <= flagged)
        self.assertEqual(set(_needs(gate)), flagged,
                         "게이트는 켠 모드 잡을 전부, 그리고 그것만 기다려야 한다")
        self.assertTrue(any("needs" in json.dumps(s) and "success" in (s.get("run") or "")
                            for s in gate["steps"]),
                        "게이트 스텝이 선행 잡의 결과를 읽고 success 와 대조하지 않는다")

    def test_시간_파일이_있고_비어_있지_않다(self):
        (_key, job), = self.shards.items()
        args = next(a for a in map(_split_args, _runs(job)) if a)
        path = _ROOT / args.get("--durations-path", ".test_durations")
        self.assertTrue(path.is_file(), f"{path.name} 가 없다 — 조각이 한쪽으로 쏠린다")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertGreater(len(data), 1000, "시간 파일이 전량 런에서 나온 것 같지 않다")
        self.assertTrue(all(k.startswith("tests/") for k in data),
                        "테스트 id 가 리포 루트 기준이 아니다 — CI 의 수집 id 와 안 맞는다")
        self.assertTrue(all(isinstance(v, (int, float)) and v >= 0 for v in data.values()))

    def test_스위트_밖_스텝은_게이트_뒤에서_한_번씩_돈다(self):
        (_gkey, gate), = self.gates.items()
        needs = set(_needs(gate))
        for label, pat in _ONCE.items():
            homes = [(k, j) for k, j in self.jobs.items()
                     for r in _runs(j) if pat.search(r)]
            self.assertEqual(len(homes), 1, f"{label} 스텝이 {len(homes)}곳에 있다")
            (key, job), = homes
            self.assertIn(key, needs, f"{label} 가 게이트 뒤에 없다")
            self.assertNotIn("strategy", job, f"{label} 가 매트릭스 안에 있어 여러 번 돈다")


if __name__ == "__main__":
    unittest.main()
