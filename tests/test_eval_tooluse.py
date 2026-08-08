# -*- coding: utf-8 -*-
"""E4 — MCP 도구 사용 평가셋이 **낡지 않았는지** 지킨다.

E1 이 고친 함정이 정확히 이것이다: 평가셋의 정답이 코퍼스와 어긋나면 그 평가는
"측정하지 않는 측정"이 된다. 그리고 그때 깨지는 것은 평가가 아니라 **모델 점수**라,
증상이 원인을 안 가리킨다(모델이 틀린 것처럼 보인다).

그래서 정답을 픽스처에서 매번 재도출해 대조한다. 픽스처를 바꾸거나 엔진 동작이
달라지면 여기서 먼저 큰 소리로 깨진다.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.eval_tooluse import EVALSET, verify      # noqa: E402


def _load() -> dict:
    return json.loads(EVALSET.read_text(encoding="utf-8"))


def test_every_expected_answer_still_holds_on_the_fixture():
    problems = verify(_load())
    assert not problems, "평가셋이 픽스처와 어긋났다:\n  " + "\n  ".join(problems)


def test_evalset_shape_is_complete():
    """문항마다 채점에 필요한 것이 다 있는가 — 빠지면 그 문항은 채점 불가다."""
    d = _load()
    assert len(d["questions"]) == 10, "E4 는 10문항으로 고정한다"
    ids = [q["id"] for q in d["questions"]]
    assert len(set(ids)) == len(ids), f"문항 id 중복: {ids}"
    for q in d["questions"]:
        for key in ("ask", "expect", "requires", "forbidden", "probes", "verify"):
            assert key in q, f"{q['id']}: {key} 없음"
        assert q["expect"]["kind"] in ("set", "list", "exact", "contains"), q["id"]
        assert q["requires"], f"{q['id']}: 요구 도구가 비었다 — process 채점이 무의미해진다"


def test_required_tools_exist():
    """평가셋이 없는 도구를 요구하면 그 문항은 영원히 0점이 된다."""
    from vinc_mcp.rpc import _tool_specs
    known = {t["name"] for t in _tool_specs()}
    for q in _load()["questions"]:
        unknown = [t for t in q["requires"] + q["forbidden"] if t not in known]
        assert not unknown, f"{q['id']}: 없는 도구를 지목한다 {unknown}"


def test_questions_are_read_only():
    """전 문항 읽기 전용 — 평가가 그래프를 바꾸면 다음 실행의 정답이 달라진다."""
    from vinc_mcp.rpc import _tool_specs
    ann = {t["name"]: (t.get("annotations") or {}) for t in _tool_specs()}
    for q in _load()["questions"]:
        writes = [t for t in q["requires"] if ann.get(t, {}).get("readOnlyHint") is not True]
        assert not writes, f"{q['id']}: 쓰기 도구를 요구한다 {writes}"
