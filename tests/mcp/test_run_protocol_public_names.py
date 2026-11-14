# -*- coding: utf-8 -*-
"""`run_protocol` 의 읽기·저장·제목·보기 도우미는 공개 이름이 정본이고 밑줄 이름은 같은 객체의 별칭이다.
`run_tasks` 와 `run_notify` 는 공개 이름으로만 부른다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vinc_mcp import run_protocol  # noqa: E402

PAIRS = [("_load", "load"), ("_view", "view"), ("_saved", "saved"), ("_title", "title")]


@pytest.mark.parametrize("private,public", PAIRS, ids=[p for p, _ in PAIRS])
def test_밑줄_이름은_공개_이름과_같은_객체다(private, public):
    assert getattr(run_protocol, private) is getattr(run_protocol, public)


@pytest.mark.parametrize("rel", ["vinc_mcp/run_tasks.py", "vinc_mcp/run_notify.py"])
def test_다른_모듈은_공개_이름으로_부른다(rel):
    pattern = r"run_protocol\._(load|view|saved|title)\b|import[^\n]*\b_(load|view|saved|title)\b"
    hits = []
    for i, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        if re.search(pattern, code):
            hits.append(f"{rel}:{i}: {line.strip()}")
    assert hits == []
