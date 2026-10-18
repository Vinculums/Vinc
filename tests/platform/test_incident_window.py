# -*- coding: utf-8 -*-
"""허브를 내리거나 바꾸는 워크플로는 장애 감시의 배포 창을 열고 닫는다.

1. 창은 허브를 건드리기 **전에** 열린다. 늦게 열면 그 사이의 실패로 장애 메일이 나간다.
2. 닫기는 앞이 실패해도 돈다(always). 안 닫으면 배포가 망가뜨린 허브를 30분 동안 못 본다.
3. 스크립트는 토큰이 없거나 호출이 실패해도 0 으로 끝난다. 배포 창이 배포를 막으면 안 된다.
근거: [[vinc.plan.incident-mail-automation-2026-10-18]] §2
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"
SCRIPT = ROOT / "scripts" / "incident_window.sh"


def _text(name: str) -> str:
    return (WF / name).read_text(encoding="utf-8")


def _step(text: str, name: str) -> str:
    start = text.index(f"- name: {name}")
    end = text.find("\n      - ", start + 10)
    return text[start:end if end != -1 else len(text)]


def test_hub_deploy_opens_before_the_switch_and_closes_always():
    t = _text("hub-deploy.yml")
    opened = t.index("- name: Incident window open")
    deploy = t.index("- name: Deploy to Container Apps")
    closed = t.index("- name: Incident window close")
    handoff = t.index("- name: Verify writer handoff")
    assert opened < deploy < closed < handoff
    assert "if: always()" in _step(t, "Incident window close")
    assert "secrets.INCIDENT_MAINTENANCE_TOKEN" in _step(t, "Incident window open")


@pytest.mark.parametrize("name", ["hub-store-rebuild.yml", "hub-data-repair.yml"])
def test_store_apply_opens_before_the_hub_stops_and_closes_always(name):
    t = _text(name)
    opened = t.index("- name: Incident window open")
    stop = t.index("- name: Stop the hub")
    closed = t.index("- name: Incident window close")
    assert opened < stop < closed
    assert "if: inputs.mode == 'apply'" in _step(t, "Incident window open")
    assert "always() && inputs.mode == 'apply'" in _step(t, "Incident window close")


def _run(*args: str, **env: str) -> subprocess.CompletedProcess:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash 가 없다")
    e = {k: v for k, v in os.environ.items() if k != "INCIDENT_MAINTENANCE_TOKEN"}
    e.update(env)
    return subprocess.run([bash, str(SCRIPT), *args], capture_output=True, text=True, env=e, timeout=60)


def test_no_token_is_a_notice_not_a_failure():
    r = _run("open", "hub", "x")
    assert r.returncode == 0
    assert "INCIDENT_MAINTENANCE_TOKEN is not set" in r.stdout


def test_an_unreachable_server_does_not_fail_the_run():
    r = _run("open", "hub", "x", INCIDENT_MAINTENANCE_TOKEN="t" * 40,
             INCIDENT_MAINTENANCE_URL="http://127.0.0.1:9/api/admin/incident/maintenance")
    assert r.returncode == 0
    assert "failed" in r.stdout


def test_unknown_service_or_action_is_not_sent():
    assert "unknown service" in _run("open", "db", "x", INCIDENT_MAINTENANCE_TOKEN="t").stdout
    assert "unknown action" in _run("pause", "hub", "x", INCIDENT_MAINTENANCE_TOKEN="t").stdout
