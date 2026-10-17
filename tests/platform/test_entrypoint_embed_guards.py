# -*- coding: utf-8 -*-
"""임베딩 워커를 띄우는 자리의 두 약속 — 엔트리포인트 본문으로 확인한다.

1. 메모리가 모자라면 커널이 **워커를 먼저** 고른다. 모델을 컨테이너 안에 들면 워커와
   사이드카가 한 cgroup 을 나누고, 사이드카가 골라지면 단일 writer 를 잃는다.
2. 오래 건강하게 돌다 죽은 워커는 재기동 상한을 처음부터 다시 센다. 상한은 뜨자마자
   죽는 루프를 끊으려는 것이지, 하루 몇 번의 죽음으로 벡터 검색을 영영 끄려는 것이 아니다.
"""
from __future__ import annotations

import pathlib
import re

_ENTRY = pathlib.Path(__file__).resolve().parents[2] / "packaging" / "docker-entrypoint.sh"


def _supervisor() -> str:
    text = _ENTRY.read_text(encoding="utf-8")
    start = text.index('EMBED_PIDFILE="${VINC_EMBED_PIDFILE')
    end = text.index("EMBED_SUP_PID=$!", start)
    return text[start:end]


def test_워커가_먼저_죽도록_oom_점수를_올리고_exec_로_넘긴다():
    body = _supervisor()
    assert "echo 1000 > /proc/self/oom_score_adj" in body, "워커의 OOM 점수를 올리지 않는다"
    assert "exec python -m vinc_rag.embed.worker" in body, (
        "exec 가 아니면 pidfile 의 pid 가 파이썬이 아니게 되어 TERM 이 워커에 안 닿는다")
    assert re.search(r"nice -n \"\$\{VINC_EMBED_NICE:-\d+\}\"", body), "워커 우선순위를 낮추지 않는다"


def test_오래_돈_워커는_재기동_상한을_다시_센다():
    body = _supervisor()
    reset = body.find("attempt=0\n      fi")
    bump = body.find("attempt=$((attempt + 1))")
    assert "VINC_EMBED_STABLE_S" in body, "건강했던 시간을 보지 않는다"
    assert 0 <= reset < bump, "초기화가 증가보다 앞에 있어야 이번 죽음부터 새로 센다"
