# -*- coding: utf-8 -*-
"""손으로 띄우는 verify-hosted 의 켠 모드 잡이 ci.yml 의 켠 모드 조각과 같은 것을 재는가.

verify-hosted 의 초록은 "ci 의 그 잡이 이 ref 에서 통과했을 것"을 대신한다. 의존성 설치가
갈리면 그 초록은 다른 것을 잰 것이다: neo4j 드라이버와 서버가 없으면 전량 스위트 안의 Neo4j 쪽
테스트가 조용히 skip 되고, 제한 시간이 없으면 멈춘 스위트가 러너 기본 여섯 시간을 쓴다.
비교 상대는 ci.yml 에서 `--splits` 로 전량을 나눠 도는 잡이다(test_ci_core_pkns_shards 가 그 잡이
하나임을 지킨다). 조각으로 나누는 것과 매트릭스는 비교하지 않는다: verify-hosted 는 전량을 한 번에 돈다.

`unittest.TestCase` 인 이유는 test_ci_core_pkns_shards 와 같다 — `verify.py`(unittest 로더)에도 보이게.
"""
from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / ".github" / "workflows"


def _load(name: str) -> dict:
    import yaml
    return yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))


def _install_steps(job: dict) -> list[tuple[str, str]]:
    """의존성을 까는 스텝의 (이름, 명령). 이름과 명령을 글자 그대로 견준다."""
    return [(s.get("name", ""), (s.get("run") or "").strip()) for s in job.get("steps") or []
            if "pip install" in (s.get("run") or "")]


class VerifyHostedCorePknsMatchesCi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ci = _load("ci.yml")["jobs"]
        shards = [j for j in ci.values()
                  if any("--splits" in (s.get("run") or "") for s in j.get("steps") or [])]
        assert len(shards) == 1, "ci.yml 에서 --splits 로 도는 잡이 하나가 아니다"
        cls.ci = shards[0]
        cls.workflow = _load("verify-hosted.yml")
        cls.hosted = cls.workflow["jobs"]["core-pkns"]

    def test_the_install_step_is_the_same_as_ci(self):
        self.assertEqual(_install_steps(self.hosted), _install_steps(self.ci))

    def test_the_neo4j_service_and_flags_are_the_same_as_ci(self):
        # 드라이버만 깔고 서버가 없으면 Neo4j 쪽 테스트는 URI 가 없어 skip 된다.
        self.assertEqual(self.hosted.get("services"), self.ci.get("services"))
        self.assertEqual(self.hosted.get("env"), self.ci.get("env"))

    def test_the_timeout_is_the_same_as_ci(self):
        self.assertIsNotNone(self.ci.get("timeout-minutes"))
        self.assertEqual(self.hosted.get("timeout-minutes"), self.ci.get("timeout-minutes"))

    def test_every_job_has_a_timeout(self):
        # 제한 시간이 없는 잡은 멈추면 러너 기본 여섯 시간 동안 GitHub 분을 쓴다.
        missing = [name for name, job in self.workflow["jobs"].items()
                   if not isinstance(job.get("timeout-minutes"), int)]
        self.assertEqual(missing, [])

    def test_the_core_timeout_is_the_same_as_ci_core(self):
        ci_core = _load("ci.yml")["jobs"]["core"]
        self.assertIsNotNone(ci_core.get("timeout-minutes"))
        self.assertEqual(self.workflow["jobs"]["core"].get("timeout-minutes"),
                         ci_core.get("timeout-minutes"))

    def test_it_still_runs_only_by_hand(self):
        # yaml 1.1 은 `on:` 열쇠를 True 로 읽는다.
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(list(triggers), ["workflow_dispatch"])
        self.assertEqual(self.hosted.get("if"), "inputs.core_pkns")


if __name__ == "__main__":
    unittest.main()
