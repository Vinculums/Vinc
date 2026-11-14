# -*- coding: utf-8 -*-
"""brief 플래그 파서 표: 환경변수 값마다 파서가 내놓는 답이 `store.envflag` 로 옮기기 전과 같다.

`tests/fixtures/brief_flag_parse_table.json` 은 옮기기 전 `api/brief.py` 의 파서(`_brief_*_on`·`_brief_rule`·
`_brief_expose_n`)가 값마다 내놓은 답을 그대로 받아 적은 것이다(없음·빈 값·0/1·on/off·true/false·yes/no·
대소문자 섞기·앞뒤 공백·오타·숫자 모양 문자). 파서를 고치는 변경이 일부러 어느 값의 답을 바꾸려면 이 표를 고친다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import brief as brief_mod
from api import query as q

TABLE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "brief_flag_parse_table.json")
                   .read_text(encoding="utf-8"))
ENV = TABLE["env"]
VALUES = TABLE["values"]

# 표에서 빠지면 안 되는 값: 없음, 빈 값, 켜고 끄는 낱말 여덟 개, 대소문자 섞기, 앞뒤 공백, 오타
REQUIRED = [None, "", " ", "0", "1", "on", "off", "true", "false", "yes", "no",
            "On", "OFF", "True", "FALSE", "Yes", "NO", " off ", "\tyes\t", "maybe", "offf", "2"]

CASES = [(fn, i) for fn in ENV for i in range(len(VALUES))]

#: 이 두 이름은 파서가 아니라 규칙 표(`_BRIEF_RULES`)의 칸이다. 같은 환경변수에서 기록된 답이 나와야 한다.
TABLE_FIELDS = {
    "_brief_rule_v3": lambda: brief_mod._BRIEF_RULES[brief_mod._brief_rule()].needs_rescore,
    "_brief_rule_colbert": lambda: brief_mod._BRIEF_RULES[brief_mod._brief_rule()].uses_colbert,
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in set(ENV.values()):
        monkeypatch.delenv(var, raising=False)


def test_table_shape_and_required_values():
    assert set(TABLE["answers"]) == set(ENV)
    for fn, col in TABLE["answers"].items():
        assert len(col) == len(VALUES), fn
    for v in REQUIRED:
        assert v in VALUES, repr(v)
    # 여덟 플래그 모두 표에 있다
    assert set(ENV.values()) == {"VINC_BRIEF_" + n for n in (
        "STRICT", "SEM_PROMOTE", "SECTION", "QUOTES", "ANSWERABILITY", "LIST_RESOLVED", "RULE", "EXPOSE")}


@pytest.mark.parametrize("fn,i", CASES, ids=[f"{fn}[{i}]" for fn, i in CASES])
def test_parser_answers_match_the_recorded_table(monkeypatch, fn, i):
    value = VALUES[i]
    if value is not None:
        monkeypatch.setenv(ENV[fn], value)
    want = TABLE["answers"][fn][i]
    got = TABLE_FIELDS[fn]() if fn in TABLE_FIELDS else getattr(brief_mod, fn)()
    assert got == want and type(got) is type(want), (fn, ENV[fn], value, got, want)


@pytest.mark.parametrize("fn", sorted(set(ENV) - set(TABLE_FIELDS)))
def test_query_reexport_is_the_same_function(fn):
    assert getattr(q, fn) is getattr(brief_mod, fn)


def test_rule_returns_only_the_two_names(monkeypatch):
    for value in VALUES:
        if value is None:
            monkeypatch.delenv("VINC_BRIEF_RULE", raising=False)
        else:
            monkeypatch.setenv("VINC_BRIEF_RULE", value)
        assert brief_mod._brief_rule() in ("v3", "production"), value
