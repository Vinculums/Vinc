# -*- coding: utf-8 -*-
"""허브 기동 순서의 writer 인계 약속 — 엔트리포인트 본문으로 확인한다.

1. MCP 가 **먼저** 붙잡기 모드로 포트를 연다. 그래야 플랫폼이 트래픽을 넘기고 구 리비전을
   내린다 — 포트가 닫혀 있으면 구 리비전도 안 내려가 인계가 영영 안 일어난다.
2. 인계 판정이 **복원(벡터 차원 이행) 앞**에 있다. 복원이 먼저면 구 리비전의 마지막
   스냅샷 전에 낡은 사본을 연다.
3. 낡은 리비전이면 스토어를 열지 않고 끝난다(사이드카를 띄우지 않는다).
"""
from __future__ import annotations

import pathlib
import re

_ENTRY = pathlib.Path(__file__).resolve().parents[2] / "packaging" / "docker-entrypoint.sh"


def _text() -> str:
    return _ENTRY.read_text(encoding="utf-8")


def _first(pattern: str, text: str) -> int:
    m = re.search(pattern, text, re.M)
    assert m, f"엔트리포인트에 없다: {pattern}"
    return m.start()


def test_mcp_holds_first_then_the_handoff_wait_then_the_restore():
    t = _text()
    mcp = _first(r"^VINC_MCP_HOLD=1 python -m api\.cli mcp serve-http", t)
    wait = _first(r"^python -m store\.handoff wait", t)
    restore = _first(r"^python -m api\.cli migrate-dim", t)
    sidecar = _first(r"^start_sidecar$", t)
    assert mcp < wait < restore < sidecar


def test_mcp_is_started_once():
    assert len(re.findall(r"mcp serve-http \$MCP_ARGS", _text())) == 1, "MCP 를 두 번 띄운다"


def test_a_stale_revision_exits_before_opening_the_store():
    t = _text()
    stale = re.search(r'if \[ "\$hrc" = "3" \]; then(.*?)\nfi', t, re.S)
    assert stale, "낡은 리비전 분기가 없다"
    body = stale.group(1)
    assert "exit 0" in body
    assert "migrate-dim" not in body and "start_sidecar" not in body


def test_the_sidecar_stop_wait_is_configurable():
    t = _text()
    assert '"${VINC_SIDECAR_STOP_WAIT_S:-40}"' in t
