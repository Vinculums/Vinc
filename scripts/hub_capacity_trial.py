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
import time
import urllib.error
import urllib.request
from pathlib import Path

from gate_app_config import APPS, BASELINE, HUB_APP, project_app

try:
    from scripts._azutil import AzError, az as _az
    from scripts._root import ROOT  # noqa: F401  리포 루트를 sys.path 에 올린다
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _azutil import AzError, az as _az
    from _root import ROOT  # noqa: F401
from vinc_mcp import hubhttp  # noqa: E402

# 바꾸는 앱은 드리프트 게이트와 같은 허브 앱이다(대상 설정을 hub-deploy 의 표로 푼 이름).
APP = ["-n", HUB_APP, "-g", "vincsRG"]
BASE = "https://mcp.vincs.io"
# 이 둘이 없으면 허브의 엔트리포인트가 모델을 컨테이너 안에 들고 루프백으로 인코딩한다.
# 로컬 인코더 시험은 2 CPU 구간에서 이 둘만 빼고, 원복 때 같은 정의로 되돌린다.
LOCAL_ENCODER_ENV = ("VINC_EMBED_REMOTE", "VINC_EMBED_ENCODE_TOKEN")


def az(*args):
    try:
        return _az(args, timeout=240)
    except AzError as e:
        if e.returncode is None:
            raise
        # stderr 에 ARM 템플릿·env 값이 들어갈 수 있어 로그에 복제하지 않는다.
        raise RuntimeError(f"Azure {args[0]} {args[1]} failed ({e.returncode})") from None


def show():
    return az("containerapp", "show", *APP)


def revisions():
    return az("containerapp", "revision", "list", *APP)


def public_state():
    out = {}
    images = {}
    # 드리프트 게이트와 같은 앱 목록을 본다. 따로 적어 두면 기준선에 앱이 늘 때마다(레이아웃)
    # 사전 확인이 늘 어긋나 시험이 아무것도 못 바꾸고 끝난다.
    for name in APPS:
        data = az("containerapp", "show", "-n", name, "-g", "vincsRG")
        auth = az("containerapp", "auth", "show", "-n", name, "-g", "vincsRG")
        out[name] = project_app(data, auth)
        images[name] = [c["image"] for c in data["properties"]["template"]["containers"]]
    return out, images


def expected_profile(baseline, cpu, local=False):
    expected = copy.deepcopy(baseline)
    # ACA Consumption 은 2 CPU·4 GiB 조합에서 임시 디스크도 8 GiB 로 바뀐다.
    expected[HUB_APP]["resources"] = {
        "cpu": cpu, "memory": "2Gi" if cpu == 1 else "4Gi",
        "ephemeralStorage": "4Gi" if cpu == 1 else "8Gi"}
    if local and cpu != 1:
        expected[HUB_APP]["env_names"] = [
            n for n in expected[HUB_APP]["env_names"] if n not in LOCAL_ENCODER_ENV]
    return expected


def encoder_env(app):
    """원복에 쓸 원격 인코더 env 정의. 주소는 비밀이 아니고, 토큰은 시크릿 **이름**만 담는다."""
    env = {e.get("name"): e for e in app["properties"]["template"]["containers"][0].get("env") or []}
    out = []
    for name in LOCAL_ENCODER_ENV:
        e = env.get(name)
        if not e:
            raise RuntimeError(f"{name} is not set; nothing to switch to a local encoder")
        if e.get("secretRef"):
            out.append(f"{name}=secretref:{e['secretRef']}")
        elif e.get("value"):
            out.append(f"{name}={e['value']}")
        else:
            raise RuntimeError(f"{name} has neither a value nor a secret reference")
    return out


def verify_profile(state, cpu):
    actual, images = public_state()
    if images != state["images"]:
        raise RuntimeError("Image changed during capacity trial")
    if actual != expected_profile(state["baseline"], cpu, state.get("local", False)):
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
    image = state["images"][HUB_APP][0]
    current = show()["properties"]["template"]["containers"]
    if len(current) != 1 or current[0]["image"] != image:
        raise RuntimeError("Refusing to resize an unexpected image")
    # 구 리비전을 먼저 내리지 않는다. 새 리비전이 요청을 붙잡은 채 구 리비전의 넘김을 보고
    # 복원하므로 단일 writer 순서는 기동이 지킨다(hub-deploy 와 같다). 먼저 내리면 플랫폼이
    # 그 리비전을 한 번 다시 띄워 인계가 꼬이고 전환마다 1분쯤 끊긴다.
    extra = []
    if state.get("local"):
        extra = (["--remove-env-vars", *LOCAL_ENCODER_ENV] if cpu != 1
                 else ["--set-env-vars", *state["encoder_env"]])
    az("containerapp", "update", *APP, "--image", image, *extra,
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
    names = {e.get("name") for e in current["template"]["containers"][0].get("env") or []}
    remote_back = not state.get("local") or all(n in names for n in LOCAL_ENCODER_ENV)
    if resources.get("cpu") == 1 and resources.get("memory") == "2Gi" and remote_back:
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
    if (actual != baseline or images[HUB_APP] != [args.expected_image]
            or initial["latestRevisionName"] != args.expected_revision
            or initial["template"].get("revisionSuffix")
            or baseline[HUB_APP]["resources"].get("cpu") != 1):
        raise RuntimeError("Preflight baseline, revision or image mismatch; no mutation")
    state = {"baseline": baseline, "images": images}
    if getattr(args, "local_encoder", False):
        state.update(local=True, encoder_env=encoder_env({"properties": initial}))
    verify_profile(state, 1)
    wait_ready()
    # 비밀 값 없는 구조·이미지(로컬 인코더면 인코더 주소와 시크릿 이름)만 저장한다. 종료 전에
    # 남겨 별도 스텝에서도 원복할 수 있다.
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
    ap.add_argument("--local-encoder", action="store_true",
                    help="2 CPU 구간에서 원격 인코더 env 를 빼 모델을 허브 컨테이너 안에 든다")
    args = ap.parse_args()
    # 로컬 인코더는 하루 몇 번 나던 인코더 종료가 같은 컨테이너에서 어떻게 되는지까지 봐야
    # 해서 창을 길게 허용한다. 자원만 바꾸는 시험은 그대로 15분이다.
    longest = 7200 if args.local_encoder else 900
    if not 60 <= args.hold_seconds <= longest:
        ap.error(f"Measurement window must be between 60 and {longest} seconds")
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
