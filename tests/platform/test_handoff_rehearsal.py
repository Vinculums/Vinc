# -*- coding: utf-8 -*-
"""writer 인계 리허설의 약속 — 스크립트와 워크플로를 함께 본다.

1. 임시 앱은 운영과 같은 기동 경로를 갖고(로컬 작업본·/data 스냅샷·단일 리비전),
   첫 리비전만 빈 스토어로 시작할 수 있다.
2. 판정: 끝내 실패한 쓰기가 있거나, 성공으로 답한 쓰기가 전환 뒤 없으면 실패다.
3. 프로브는 503·끊김을 같은 operation_id 로 다시 보내고, 늦게 성공한 것은 성공으로 센다.
4. 워크플로는 운영 앱을 읽기만 하고 운영 공유를 열지 않으며, 만든 것을 늘 지운다.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import handoff_rehearsal as hr                                    # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "hub-handoff-rehearsal.yml"


def _spec() -> dict:
    return hr.app_spec(name="vinc-hub-rh-1", env_id="/sub/env", image="vincacr.azurecr.io/vinc-hub:x",
                       storage="rh1", acr_password="p", mcp_token="m", sidecar_token="s")


def test_app_spec_boots_like_the_hub_with_an_empty_first_store():
    spec = _spec()
    props = spec["properties"]
    assert props["configuration"]["activeRevisionsMode"] == "Single"
    tpl = props["template"]
    env = {e["name"]: e.get("value", e.get("secretRef")) for e in tpl["containers"][0]["env"]}
    assert env["VINC_STORE_LOCAL"] == "on"
    assert env["VINC_DATA_DIR"].startswith("/data/")
    assert env["VINC_STORE_ALLOW_EMPTY"] == "1"
    assert env["VINC_MCP_TOKEN"] == "mcp-token"          # 값이 아니라 시크릿 참조
    assert tpl["terminationGracePeriodSeconds"] >= 60
    assert tpl["volumes"] == [{"name": "data", "storageType": "AzureFile", "storageName": "rh1"}]
    assert not any(e["name"] in ("VINC_EMBED_REMOTE", "VINC_MCP_ISSUER", "VINC_ACCOUNT_INTERNAL_URL")
                   for e in tpl["containers"][0]["env"]), "운영 의존을 끌어오면 안 된다"


def _row(n, ok=True, attempts=1, first=200):
    return {"n": n, "id": f"record:p-{n}", "ok": ok, "attempts": attempts,
            "first_status": first, "status": 200 if ok else 503, "latency_s": float(attempts)}


def test_summary_passes_only_when_nothing_failed_and_nothing_vanished():
    rows = [_row(1), _row(2, attempts=3, first=503), _row(3)]
    found = {r["id"]: True for r in rows}
    v = hr.summarize(rows, found)
    assert v["pass"] and v["retried"] == 1 and v["max_latency_s"] == 3.0

    found["record:p-2"] = False
    v = hr.summarize(rows, found)
    assert not v["pass"] and v["missing_ids"] == ["record:p-2"]

    v = hr.summarize(rows + [_row(4, ok=False)], {r["id"]: True for r in rows})
    assert not v["pass"] and v["failed"] == 1

    assert not hr.summarize([], {})["pass"], "아무것도 안 쓴 런은 통과가 아니다"


def test_probe_retries_with_the_same_operation_and_counts_a_late_success(tmp_path):
    stop = tmp_path / "stop"
    out = tmp_path / "probe.jsonl"
    calls: list[tuple[str, str]] = []
    replies = iter([(False, 503, "http 503"), (False, 0, "reset"), (True, 200, "ok"),
                    (True, 200, "ok")])

    def fake_write(url, token, node_id, title, op, rid):
        calls.append((node_id, op))
        if len(calls) == 4:
            stop.write_text("")
        return next(replies)

    t = {"now": 0.0}
    with mock.patch.object(hr, "_write_once", fake_write):
        hr.probe("u", "t", "p", str(stop), str(out), interval_s=0.0,
                 clock=lambda: t["now"], sleep=lambda s: t.__setitem__("now", t["now"] + s))
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["ok"] for r in rows] == [True, True]
    assert rows[0]["attempts"] == 3 and rows[0]["first_status"] == 503
    assert len({op for nid, op in calls if nid == rows[0]["id"]}) == 1, "재시도는 같은 operation_id"


def test_probe_gives_up_after_the_write_deadline(tmp_path):
    stop = tmp_path / "stop"
    out = tmp_path / "probe.jsonl"
    t = {"now": 0.0}

    def fake_write(*a, **k):
        if t["now"] > hr.WRITE_DEADLINE_S:
            stop.write_text("")
        return False, 503, "http 503"

    with mock.patch.object(hr, "_write_once", fake_write):
        hr.probe("u", "t", "p", str(stop), str(out), interval_s=1.0,
                 clock=lambda: t["now"], sleep=lambda s: t.__setitem__("now", t["now"] + s))
    first = json.loads(out.read_text().splitlines()[0])
    assert first["ok"] is False and first["why"] == "http 503"


def test_workflow_reads_production_and_never_writes_it():
    lines = [ln for ln in WORKFLOW.read_text(encoding="utf-8").splitlines()
             if not ln.lstrip().startswith("#")]
    assert not any("hub-data" in ln for ln in lines), "운영 공유를 열면 안 된다"
    for line in lines:
        if re.search(r"-n vinc-hub\b(?!-)|--name vinc-hub\b(?!-)", line):
            assert "containerapp show" in line, f"운영 앱에 쓰기 명령: {line.strip()}"


def test_workflow_always_cleans_up_what_it_created():
    text = WORKFLOW.read_text(encoding="utf-8")
    cleanup = text.split("- name: Delete the rehearsal app, mount and share", 1)
    assert len(cleanup) == 2
    body = cleanup[1]
    assert "if: always()" in body.split("run:", 1)[0]
    for cmd in ("containerapp delete", "env storage remove", "share-rm delete"):
        assert cmd in body, cmd


def test_workflow_does_not_stop_the_old_revision_first():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "revision deactivate" not in text
