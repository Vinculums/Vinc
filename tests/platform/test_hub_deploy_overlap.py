# -*- coding: utf-8 -*-
"""허브 배포는 구 리비전을 먼저 내리지 않는다 — 순서는 기동의 writer 인계가 지킨다.

1. 배포 잡에 `revision deactivate` 가 없다. 있으면 새 리비전이 넘김 표식을 기다릴 상대가
   이미 멈춰 있어서, 인계 대신 배포 중 단절이 돌아온다.
2. 이미지 갱신이 종료 유예 60초를 함께 싣는다(구 리비전이 최종 스냅샷을 올릴 시간).
3. 헬스 뒤에 영속 공유의 표식으로 인계를 대조한다: writer 는 새 리비전, 넘긴 쪽은 구 리비전.
"""
from __future__ import annotations

import pathlib
import re

WORKFLOW = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "hub-deploy.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_the_deploy_does_not_stop_the_old_revision_first():
    assert "revision deactivate" not in _text()


def test_the_update_carries_the_termination_grace():
    m = re.search(r"^ +if ! (az containerapp update .+); then$", _text(), re.M)
    assert m, "이미지 갱신 줄이 없다"
    assert "--termination-grace-period 60" in m.group(1)


def test_the_handoff_is_checked_last_against_the_share_markers():
    """인계 확인은 배포 잡의 맨 끝이다. 앞에 두면 그 빨강이 뒤의 검증(OAuth 표면, 구조 기준선,
    인용 생존)을 건너뛰게 한다 — 인계가 늦었을 뿐 서비스는 떠 있는데 그것을 확인하는 스텝이
    안 돈다."""
    t = _text()
    health = t.index("- name: Verify health (MCP tools/list)")
    handoff = t.index("- name: Verify writer handoff (markers on the persistent share)")
    report = t.index("\n  report:\n")
    assert health < handoff < report
    assert t.find("\n      - name:", handoff + 10, report) == -1, "인계 확인 뒤에 다른 스텝이 있다"
    body = t[handoff:report]
    assert "if: ${{ !cancelled() && env.HANDOFF_EXPECTED == '1' }}" in body
    assert '"$w" = "$LATEST_REV"' in body and '"$r" = "$PREV_REV"' in body
    assert "hub-data" in body
