# -*- coding: utf-8 -*-
"""공개 허브 이미지(VIN-963) — 미는 조건, 예시 compose, 셀프호스트 기본 토큰.

잠그는 것:
  ① 워크플로는 태그 push 나 `publish: true` 디스패치일 때만 ghcr 로 민다. PR 은 짓고 검사만 한다.
     호스티드 레지스트리(vincacr)·Azure 에는 닿지 않는다. 입력값은 env 로만 셸에 들어간다.
  ② 예시 compose 의 허브는 셀프호스트 플래그를 켜고, 포트는 이 기계에서만 연다.
  ③ 엔트리포인트는 셀프호스트일 때 사이드카·MCP 토큰이 없으면 볼륨에 만들고(0600), 다음 기동은
     같은 값을 쓴다. 플래그가 없으면 그 블록을 지나지 않는다.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github/workflows/hub-image-public.yml"
ENTRY = ROOT / "packaging/docker-entrypoint.sh"


def _wf():
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def test_미는_스텝은_PUBLISH_일_때만_돈다():
    wf = _wf()
    job = wf["jobs"]["image"]
    assert job["env"]["PUBLISH"] == ("${{ github.event_name == 'push' || "
                                     "(github.event_name == 'workflow_dispatch' && inputs.publish) }}")
    on = wf[True] if True in wf else wf["on"]
    assert on["push"] == {"tags": ["v*"]}
    assert on["workflow_dispatch"]["inputs"]["publish"]["default"] is False
    pushes = [s for s in job["steps"] if "docker push" in (s.get("run") or "")]
    assert len(pushes) == 1 and pushes[0]["if"] == "env.PUBLISH == 'true'"


def test_호스티드_레지스트리와_Azure_에_닿지_않는다():
    text = WF.read_text(encoding="utf-8")
    for word in ("vincacr", "az acr", "az containerapp", "azure/login"):
        assert word not in text, word
    assert "--platform linux/amd64" in text


def test_입력값은_식으로_셸에_들어가지_않는다():
    for step in _wf()["jobs"]["image"]["steps"]:
        assert "${{ inputs." not in (step.get("run") or ""), step.get("name")


def test_예시_compose_는_셀프호스트이고_이_기계에서만_연다():
    doc = yaml.safe_load((ROOT / "packaging/selfhost/compose.yaml").read_text(encoding="utf-8"))
    hub = doc["services"]["hub"]
    assert hub["environment"]["VINC_SELF_HOSTED"] == "on"
    assert hub["ports"] == ["127.0.0.1:8790:8790"]
    assert hub["volumes"] == ["vinc-data:/data"]
    assert doc["services"]["caddy"]["profiles"] == ["tls"]
    assert doc["services"]["neo4j"]["profiles"] == ["neo4j"]


def _selfhost_block() -> str:
    text = ENTRY.read_text(encoding="utf-8")
    start = text.index("# ── 셀프호스트 기본값")
    end = text.index(': "${VINC_SIDECAR_TOKEN:?')
    return text[start:end]


def _run(env: dict, data: Path) -> dict:
    bin_dir = data / "bin"
    bin_dir.mkdir(exist_ok=True)
    py = bin_dir / "python"
    if not py.exists():
        py.symlink_to(sys.executable)
    script = (f'DATA_DIR="{data}/db"\n' + _selfhost_block()
              + 'echo "SIDECAR=${VINC_SIDECAR_TOKEN:-}"\necho "MCP=${VINC_MCP_TOKEN:-}"\n'
              'echo "STATE=${VINC_SELFHOST_STATE:-}"\n')
    full = {"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}", **env}
    out = subprocess.run(["sh", "-c", script], env=full, capture_output=True, text=True, check=True)
    return dict(line.split("=", 1) for line in out.stdout.strip().splitlines())


def test_셀프호스트는_토큰을_볼륨에_만들고_다시_쓴다():
    data = Path(tempfile.mkdtemp())
    first = _run({"VINC_SELF_HOSTED": "on"}, data)
    assert re.fullmatch(r"[A-Za-z0-9_-]{40,}", first["SIDECAR"])
    assert first["MCP"] and first["MCP"] != first["SIDECAR"]
    assert first["STATE"] == f"{data}/selfhost"
    for name in ("sidecar-token", "mcp-token"):
        mode = stat.S_IMODE((data / "selfhost" / name).stat().st_mode)
        assert mode == 0o600, (name, oct(mode))
    again = _run({"VINC_SELF_HOSTED": "on"}, data)
    assert again["SIDECAR"] == first["SIDECAR"] and again["MCP"] == first["MCP"]


def test_준_토큰은_그대로이고_플래그가_없으면_아무것도_안_한다():
    data = Path(tempfile.mkdtemp())
    given = _run({"VINC_SELF_HOSTED": "on", "VINC_SIDECAR_TOKEN": "s", "VINC_MCP_TOKEN": "m"}, data)
    assert given["SIDECAR"] == "s" and given["MCP"] == "m"
    assert not (data / "selfhost" / "mcp-token").exists()
    off = _run({}, Path(tempfile.mkdtemp()))
    assert off == {"SIDECAR": "", "MCP": "", "STATE": ""}


def test_허브_이미지는_데스크톱과_같은_판의_Neo4j_드라이버를_싣는다():
    """없으면 VINC_DATABASE_URI 로 띄운 허브가 driver_missing 으로 스토어를 못 연다."""
    root = Path(__file__).resolve().parents[2]
    pin = re.search(r'"neo4j==([0-9.]+)"', (root / "pyproject.toml").read_text(encoding="utf-8")).group(1)
    docker = (root / "Dockerfile.hub").read_text(encoding="utf-8")
    assert f"neo4j=={pin}" in docker
    for wf in ("release.yml", "ci.yml"):
        assert f"neo4j=={pin}" in (root / ".github/workflows" / wf).read_text(encoding="utf-8"), wf
