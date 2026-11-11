# -*- coding: utf-8 -*-
"""`vinc storage status|connect|move` — 사이드카 없이 이 프로세스에서 같은 핸들러를 부른다.

잠그는 것: 아무것도 고르지 않았으면 위치가 없다고 말한다, 닿지 않는 주소는 실패 코드로 끝난다,
연결은 확인을 거쳐 위치만 적고 비밀번호는 파일·출력에 없다, 이동은 다 왔을 때만 위치를 바꾼다.
홈은 임시 폴더다(`HOME`). Neo4j 쪽은 `VINC_CONFORMANCE_NEO4J_URI` 가 있을 때만 돈다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
URI = os.environ.get("VINC_CONFORMANCE_NEO4J_URI")
PASSWORD = os.environ.get("VINC_CONFORMANCE_NEO4J_PASSWORD", "")


def _cli(home: Path, *args, secret: str | None = None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("MEMORY_DATA_DIR", "VINC_DATABASE_SECRET", "PYTHONHOME", "PYTHONPATH")}
    env["HOME"] = env["USERPROFILE"] = str(home)
    if secret is not None:
        env["VINC_DATABASE_SECRET"] = secret
    p = subprocess.run([sys.executable, "-m", "api.cli", "storage", *args], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    # 출력 계약: 성공은 stdout, 오류 봉투는 stderr 다(`api/cli_output.py`).
    try:
        out = json.loads(p.stdout or p.stderr)
    except ValueError:
        out = {"raw": p.stdout, "stderr": p.stderr[-2000:]}
    return p.returncode, out


def test_고르기_전과_닿지_않는_주소():
    home = Path(tempfile.mkdtemp())
    code, out = _cli(home, "status")
    assert code == 0 and out["location"] is None and out["sidecar"] is None
    code, out = _cli(home, "connect", "bolt://127.0.0.1:1", "--check-only", secret="x")
    assert code == 1 and out["ok"] is False
    code, out = _cli(home, "connect", "bolt://127.0.0.1:1", secret="x")
    assert code == 1 and not (home / ".vinc" / "config.json").exists()


@pytest.mark.skipif(not URI, reason="VINC_CONFORMANCE_NEO4J_URI 없음")
def test_연결하고_폴더로_옮긴다():
    home = Path(tempfile.mkdtemp())
    code, out = _cli(home, "connect", URI, secret=PASSWORD)
    assert code == 1 and out["error"] == "risky_database"            # bolt:// 는 평문이다
    code, out = _cli(home, "connect", URI, "--accept-risk", secret=PASSWORD)
    assert code == 0, out
    assert out["location"]["kind"] == "database" and "keyring" in out
    text = (home / ".vinc" / "config.json").read_text(encoding="utf-8")
    assert PASSWORD not in text and PASSWORD not in json.dumps(out)
    code, out = _cli(home, "move", "--to", "folder", "--path", str(home / "back"), secret=PASSWORD)
    assert code == 0 and out["switched"] is True and out["copy"]["missing"] == 0, out
    code, out = _cli(home, "status")
    assert out["location"] == {"kind": "folder", "path": str(home / "back")}
