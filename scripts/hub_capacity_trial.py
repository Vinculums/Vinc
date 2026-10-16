"""Bounded live capacity trial; use only under the hub-deploy workflow lock.

기존 이미지를 유지해 1 CPU → 2 CPU → 1 CPU 순서로 시험한다. 측정 키는
이 잡에 전달하지 않는다. 외부 측정기가 준비 구간에 읽고, 잡은 항상 원복한다.
근거: [[vinc.plan.hub-capacity-trial-2026-10-06]]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from gate_app_config import BASELINE, project_app

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vinc_mcp import hubhttp  # noqa: E402

APP = ["-n", "vinc-hub", "-g", "vincsRG"]
BASE = "https://mcp.vincs.io"


def az(*args):
    result = subprocess.run(["az", *args, "-o", "json"], capture_output=True,
                            text=True, timeout=240)
    if result.returncode:
        # stderr 에 ARM 템플릿·env 값이 들어갈 수 있어 로그에 복제하지 않는다.
        raise RuntimeError(f"Azure {args[0]} {args[1]} failed ({result.returncode})")
    return json.loads(result.stdout or "null")


def show():
    return az("containerapp", "show", *APP)


def revisions():
    return az("containerapp", "revision", "list", *APP)


def public_state():
    out = {}
    images = {}
    for name in ("vinc-hub", "vinc-encode", "vinc-account"):
        data = az("containerapp", "show", "-n", name, "-g", "vincsRG")
        auth = az("containerapp", "auth", "show", "-n", name, "-g", "vincsRG")
        out[name] = project_app(data, auth)
        images[name] = [c["image"] for c in data["properties"]["template"]["containers"]]
    return out, images


def expected_profile(baseline, cpu):
    expected = copy.deepcopy(baseline)
    # ACA Consumption 은 2 CPU·4 GiB 조합에서 임시 디스크도 8 GiB 로 바뀐다.
    expected["vinc-hub"]["resources"] = {
        "cpu": cpu, "memory": "2Gi" if cpu == 1 else "4Gi",
        "ephemeralStorage": "4Gi" if cpu == 1 else "8Gi"}
    return expected


def verify_profile(state, cpu):
    actual, images = public_state()
    if images != state["images"]:
        raise RuntimeError("Image changed during capacity trial")
    if actual != expected_profile(state["baseline"], cpu):
        raise RuntimeError("Unexpected application structure drift")
    p = show()["properties"]
    if p["configuration"].get("activeRevisionsMode") != "Single":
        raise RuntimeError("Hub must use Single revision mode")
    return p["latestRevisionName"]


def health():
    token = os.environ.get("VINC_MCP_TOKEN", "")
    if not token:
        raise RuntimeError("Authenticated health probe credential unavailable")
    data = hubhttp.post_json(BASE + "/mcp", token=token, payload={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, timeout=30)
    if not isinstance(data.get("result", {}).get("tools"), list):
        raise RuntimeError("MCP tools/list failed")
    with urllib.request.urlopen(BASE + "/.well-known/oauth-protected-resource", timeout=30) as response:
        if response.status != 200:
            raise RuntimeError("OAuth discovery failed")
    try:
        urllib.request.urlopen(urllib.request.Request(BASE + "/mcp", b"{}",
            {"Content-Type": "application/json"}), timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code != 401 or 'resource_metadata="https://' not in exc.headers.get("WWW-Authenticate", ""):
            raise RuntimeError("OAuth authentication challenge failed") from None
    else:
        raise RuntimeError("Unauthenticated MCP request unexpectedly accepted")


def stop_writer():
    for rev in revisions():
        if rev["properties"].get("active"):
            az("containerapp", "revision", "deactivate", *APP, "--revision", rev["name"])
    deadline = time.monotonic() + 210
    while time.monotonic() < deadline:
        values = [r["properties"] for r in revisions()]
        # active=false 는 종료 완료가 아니다. 실제 레플리카 0 을 기다린다.
        if not any(v.get("active") for v in values) and not any(v.get("replicas") for v in values):
            time.sleep(10)
            return
        time.sleep(10)
    raise RuntimeError("Writer replicas did not reach zero; update prohibited")


def wait_ready():
    deadline = time.monotonic() + 420
    while time.monotonic() < deadline:
        p = show()["properties"]
        latest = p["latestRevisionName"]
        revs = revisions()
        active = [r for r in revs if r["properties"].get("active")]
        if (len(active) == 1 and active[0]["name"] == latest
                and active[0]["properties"].get("runningState") in ("Running", "RunningAtMaxScale")
                and sum(r["properties"].get("replicas") or 0 for r in revs) == 1
                and p.get("latestReadyRevisionName") == latest):
            try:
                health()
                return latest
            except (RuntimeError, urllib.error.URLError, TimeoutError, ValueError):
                pass
        time.sleep(10)
    raise RuntimeError("Latest revision or authenticated health probe not ready")


def switch(state, cpu):
    # 이미지는 불변 조건이다. 같은 이미지에서 자원만 바꾸는 새 latest 를 만든다.
    image = state["images"]["vinc-hub"][0]
    current = show()["properties"]["template"]["containers"]
    if len(current) != 1 or current[0]["image"] != image:
        raise RuntimeError("Refusing to resize an unexpected image")
    stop_writer()
    az("containerapp", "update", *APP, "--image", image,
       "--cpu", str(cpu), "--memory", "2Gi" if cpu == 1 else "4Gi")
    latest = show()["properties"]["latestRevisionName"]
    # 변경이 no-op 이면 멈춘 latest 를 켠다. 구 latest 의 activate 만으로 원복하지 않는다.
    target = next(r for r in revisions() if r["name"] == latest)
    if not target["properties"].get("active"):
        az("containerapp", "revision", "activate", *APP, "--revision", latest)
    latest = wait_ready()
    verify_profile(state, cpu)
    print(json.dumps({"event": "ready", "cpu": cpu, "revision": latest}), flush=True)
    return latest


def restore(state):
    # 정상 종료 뒤 always 스텝에서도 안전하게 재실행할 수 있다.
    current = show()["properties"]
    resources = current["template"]["containers"][0]["resources"]
    if resources.get("cpu") == 1 and resources.get("memory") == "2Gi":
        latest = current["latestRevisionName"]
        target = next(r for r in revisions() if r["name"] == latest)
        if not target["properties"].get("active"):
            az("containerapp", "revision", "activate", *APP, "--revision", latest)
        wait_ready()
        verify_profile(state, 1)
    else:
        switch(state, 1)
    print(json.dumps({"event": "restored", "cpu": 1}), flush=True)


def run(args):
    baseline = json.loads(BASELINE.read_text())
    actual, images = public_state()
    initial = show()["properties"]
    if (actual != baseline or images["vinc-hub"] != [args.expected_image]
            or initial["latestRevisionName"] != args.expected_revision
            or images["vinc-hub"] != images["vinc-encode"]
            or initial["template"].get("revisionSuffix")
            or baseline["vinc-hub"]["resources"].get("cpu") != 1):
        raise RuntimeError("Preflight baseline, revision or image mismatch; no mutation")
    state = {"baseline": baseline, "images": images}
    verify_profile(state, 1)
    wait_ready()
    # 값 없는 구조·이미지만 저장한다. 종료 전에 남겨 별도 스텝에서도 원복할 수 있다.
    Path(args.state).write_text(json.dumps(state))
    try:
        switch(state, 2)
        deadline = time.monotonic() + args.hold_seconds
        while time.monotonic() < deadline:
            time.sleep(min(30, max(0, deadline - time.monotonic())))
            verify_profile(state, 2)
            health()
        print(json.dumps({"event": "measurement_window_closed"}), flush=True)
    finally:
        restore(state)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=("run", "restore"))
    ap.add_argument("--state", required=True)
    ap.add_argument("--expected-image", default=os.environ.get("EXPECTED_IMAGE", ""))
    ap.add_argument("--expected-revision", default=os.environ.get("EXPECTED_REVISION", ""))
    ap.add_argument("--hold-seconds", type=int, default=900)
    args = ap.parse_args()
    if not 60 <= args.hold_seconds <= 900:
        ap.error("Measurement window must be between 60 and 900 seconds")
    try:
        if args.mode == "restore":
            if Path(args.state).exists():
                restore(json.loads(Path(args.state).read_text()))
        else:
            run(args)
        return 0
    except Exception as exc:
        # HTTP/ARM 오류 전문은 자격 증명·응답 본문을 담을 수 있다.
        event = {"event": "failed", "error": type(exc).__name__}
        if isinstance(exc, RuntimeError):
            event["reason"] = str(exc)
        print(json.dumps(event), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
