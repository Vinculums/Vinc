# -*- coding: utf-8 -*-
"""bake-probe 가 배포할 수 없는가 — 주석이 아니라 검사로.

`bake-probe.yml` 의 안전은 **없는 스텝**이다: 배포 동사가 파일에 아예 없어서 뒤집힐
플래그가 없다. 그런데 "없다"는 성질은 시간이 지나면 조용히 사라진다 — 누가 편의를 위해
`az containerapp update` 한 줄을 더하는 순간, 이 워크플로는 라이브 허브에 실험 모델을
밀어 넣는 물건이 된다. 그 결과가 단순 실패가 아니라는 것이 이 검사가 존재하는 이유다:

    api/server.py 가 첫 워커 폴에서 `switch_embed_model` 을 부르고 그것이 **옛 벡터를
    전부 지운 뒤**, 1024 벡터가 `FLOAT[768]` 열에 안 들어가 실패한다. 스토어를 이미
    비운 다음에.

그래서 성질을 파일에 적어 두는 대신 여기서 고정한다
([[record:writing-a-trap-down-does-not-stop-it-a-check-does]]).

⚠ **주석은 검사 대상이 아니다.** 이 파일 자신이 경고문에 `az containerapp` 을 쓰고
있어서, 원문 grep 으로 재면 자기 경고에 걸린다. YAML 을 파싱해 **실행되는 것**
(`run` 본문과 `uses`)만 본다.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
PROBE = _ROOT / ".github" / "workflows" / "bake-probe.yml"

#: 이 워크플로에 있어서는 안 되는 것들. 배포하거나, 태그를 남기거나, 리비전을 만진다.
FORBIDDEN = [
    ("az containerapp", "컨테이너앱을 만지면 그것이 곧 배포다"),
    ("--image", "태그를 남기면 배포가 가리킬 이름이 생긴다 — --no-push 의 요점이 사라진다"),
    ("docker push", "레지스트리에 남기는 순간 같은 문제다"),
    ("acr import", "다른 이름으로 밀어 넣는 우회로"),
]


def _steps() -> list[dict]:
    doc = yaml.safe_load(PROBE.read_text(encoding="utf-8"))
    jobs = doc.get("jobs") or {}
    assert jobs, "bake-probe.yml 에 잡이 없다"
    out = []
    for job in jobs.values():
        out.extend(job.get("steps") or [])
    assert out, "검사할 스텝이 하나도 없다 — 파싱이 틀렸을 가능성이 높다"
    return out


def _executable_text() -> str:
    """실행되는 것만 모은다 — `run` 본문과 `uses`. 주석·name·설명은 뺀다."""
    parts = []
    for s in _steps():
        if isinstance(s.get("run"), str):
            parts.append(s["run"])
        if isinstance(s.get("uses"), str):
            parts.append(s["uses"])
    return "\n".join(parts)


@pytest.mark.parametrize("needle,why", FORBIDDEN, ids=[f[0] for f in FORBIDDEN])
def test_실행되는_줄에_배포_동사가_없다(needle, why):
    body = _executable_text()
    assert needle not in body, (
        f"bake-probe.yml 의 실행 경로에 {needle!r} 이 있다 — {why}.\n"
        f"이 워크플로의 안전은 '없는 스텝' 이다. 배포가 필요하면 hub-deploy 를 쓸 것"
    )


def test_굽기가_실제로_있다():
    """위 검사만 있으면 스텝을 전부 지워도 통과한다 — 아무것도 안 하는 워크플로가
    가장 안전하기 때문이다. 굽는 일 자체가 남아 있는지 함께 고정한다."""
    body = _executable_text()
    assert "az acr build" in body, "굽는 스텝이 사라졌다"
    assert "--no-push" in body, "--no-push 가 빠지면 태그가 남는다"


def test_판정이_baked_줄의_부재를_실패로_다룬다():
    """빌드가 초록인데 굽기 레이어가 안 돈 이력이 있다(2026-08-01, 이미지가 400MB 대신
    5MB 늘었다). 그때 이 워크플로는 '검증했다'고 보고하면 안 된다."""
    body = _executable_text()
    assert "baked:" in body, "굽기 출력 줄을 찾는 코드가 없다"
    assert "아무것도 검증하지 않았다" in body, (
        "baked 줄이 없을 때 실패시키는 분기가 없다 — 없으면 빈 로그가 초록이 된다"
    )


def test_입력을_run_본문에_직접_끼우지_않는다():
    """`${{ inputs.x }}` 를 셸 본문에 직접 넣으면 입력이 셸로 샌다. env 로 받는다."""
    for s in _steps():
        run = s.get("run")
        if isinstance(run, str):
            assert "${{ inputs." not in run, (
                f"스텝 {s.get('name')!r} 의 run 본문이 입력을 직접 보간한다 — env 로 받을 것"
            )
