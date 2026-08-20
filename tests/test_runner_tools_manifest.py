# -*- coding: utf-8 -*-
"""러너 요구사항 목록의 드리프트 방지.

`scripts/runner-tools.txt` 가 "러너에 무엇이 있어야 하는가"의 정본이고, 워크플로의
preflight 스텝은 "이 잡이 무엇을 부르는가"를 적는다. 둘은 다른 질문이지만 **후자가
전자의 부분집합**이어야 한다 — 목록에 없는 것을 잡이 요구하면, 새 러너를 준비한
사람은 그 사실을 빌드가 죽고 나서야 안다."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
LIST = ROOT / "scripts" / "runner-tools.txt"
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def _manifest() -> set[str]:
    out = set()
    for line in LIST.read_text(encoding="utf-8").split("\n"):
        line = line.split("#")[0].strip()
        if line:
            out.add(line.split()[0])
    return out


def _declared_in_workflows() -> dict[str, set[str]]:
    """preflight 스텝의 `tools:` 값들 — 파일별로."""
    found: dict[str, set[str]] = {}
    pat = re.compile(r"runner-preflight\s*\n\s*with:\s*\n\s*tools:\s*(.+)")
    for wf in WORKFLOWS:
        for m in pat.finditer(wf.read_text(encoding="utf-8")):
            found.setdefault(wf.name, set()).update(m.group(1).split())
    return found


def test_manifest_is_not_empty():
    assert _manifest(), "목록이 비었다 — 그러면 이 검사가 아무것도 안 한다"


def test_every_job_requirement_is_in_the_manifest():
    manifest = _manifest()
    for name, tools in _declared_in_workflows().items():
        extra = tools - manifest
        assert not extra, (
            f"{name} 의 preflight 가 요구하는 {sorted(extra)} 가 "
            f"scripts/runner-tools.txt 에 없다 — 새 러너를 준비한 사람은 "
            f"빌드가 죽고 나서야 알게 된다")


def test_preflight_is_actually_wired_somewhere():
    """스텝이 아예 사라지면 위 검사가 조용히 참이 된다."""
    assert _declared_in_workflows(), "preflight 스텝을 쓰는 워크플로가 하나도 없다"


def test_setup_script_reads_the_same_list():
    """설치와 검사가 다른 목록을 보면 못 박은 것이 아니다."""
    sh = (ROOT / "scripts" / "setup_runner.sh").read_text(encoding="utf-8")
    assert "runner-tools.txt" in sh
