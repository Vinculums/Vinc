# -*- coding: utf-8 -*-
"""retrieval·ColBERT·임베딩 워커 플래그 파서 표: 값마다 파서가 내놓는 답이 `store.envflag` 로 옮기기 전과 같다.

`tests/fixtures/retrieval_flag_parse_table.json` 은 옮기기 전 파서가 값마다 내놓은 답을 그대로 받아 적은
것이다(없음·빈 값·0/1·on/off·true/false·yes/no·대소문자 섞기·앞뒤 공백·오타·숫자 모양 문자).
`embed_quantize_int8` 열만 일부러 바뀌었다: 옛 판정은 `on` 이 아니면 전부 fp32 였고, 지금은 켜고 끄는
낱말이 아닌 값을 오류로 거절한다. 바뀐 칸은 아래 `QUANTIZE_CHANGED` 가 못 박는다.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from api import encoder_client
from vinc_rag import colbert, retrieval
from vinc_rag.embed import worker

TABLE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "retrieval_flag_parse_table.json")
                   .read_text(encoding="utf-8"))
ENV = TABLE["env"]
VALUES = TABLE["values"]

#: 표에서 빠지면 안 되는 값: 없음, 빈 값, 켜고 끄는 낱말 여덟 개, 대소문자 섞기, 앞뒤 공백, 오타
REQUIRED = [None, "", " ", "0", "1", "on", "off", "true", "false", "yes", "no",
            "On", "OFF", "True", "FALSE", "Yes", "NO", " off ", "\tyes\t", "maybe", "offf", "2"]

#: 이 열은 일부러 답이 바뀐다(아래 테스트). 나머지 열은 한 칸도 바뀌면 안 된다.
CHANGED_COLUMN = "embed_quantize_int8"
UNCHANGED = sorted(set(ENV) - {CHANGED_COLUMN})
CASES = [(fn, i) for fn in UNCHANGED for i in range(len(VALUES))]

TRUE_WORDS = {"1", "true", "yes", "on"}
FALSE_WORDS = {"0", "false", "no", "off"}
#: 옛 판정(`on` 만 int8)과 달라지는 값. 앞쪽은 이제 int8 로 읽히는 켜는 낱말, 뒤쪽은 오류가 되는 값.
QUANTIZE_NOW_INT8_BUT_WAS_FP32 = ["1", "true", "yes", "On", "ON", "True", "TRUE", "Yes", "YES",
                                  " on", "on ", "\tyes\t"]


def _call(fn: str) -> bool:
    if fn == "_node_semantic_arm_on":
        return retrieval._node_semantic_arm_on()
    if fn == "_node_sem_rank_on":
        return retrieval._node_sem_rank_on()
    if fn == "colbert_rescore_on":
        return colbert.rescore_on()
    if fn == "retrieval_colbert_rescore_on":
        return retrieval._colbert_rescore_on()
    if fn == "encoder_client_colbert_rescore_on":
        return encoder_client._colbert_rescore_on()
    if fn == "embed_quantize_int8":
        return worker.artifact_name("org/Model-X").endswith("-int8-rr.onnx")
    raise AssertionError(fn)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in set(ENV.values()):
        monkeypatch.delenv(var, raising=False)
    # numpy 가 없는 환경에서도 ColBERT 스위치의 환경변수 판정만 본다
    monkeypatch.setattr(colbert, "np", types.SimpleNamespace())


def test_table_shape_and_required_values():
    assert set(TABLE["answers"]) == set(ENV)
    for fn, col in TABLE["answers"].items():
        assert len(col) == len(VALUES), fn
    for v in REQUIRED:
        assert v in VALUES, repr(v)
    assert set(ENV.values()) == {
        "VINC_NODE_SEMANTIC_ARM", "VINC_NODE_SEM_RANK", "VINC_NODE_COLBERT", "VINC_EMBED_QUANTIZE"}


@pytest.mark.parametrize("fn,i", CASES, ids=[f"{fn}[{i}]" for fn, i in CASES])
def test_parser_answers_match_the_recorded_table(monkeypatch, fn, i):
    value = VALUES[i]
    if value is not None:
        monkeypatch.setenv(ENV[fn], value)
    want = TABLE["answers"][fn][i]
    got = _call(fn)
    assert got == want and type(got) is type(want), (fn, ENV[fn], value, got, want)


@pytest.mark.parametrize("fn", ["_node_semantic_arm_on", "_node_sem_rank_on"])
def test_default_on_flags_are_false_only_for_the_four_false_words(monkeypatch, fn):
    for value, want in zip(VALUES, TABLE["answers"][fn]):
        if value is None:
            monkeypatch.delenv(ENV[fn], raising=False)
        else:
            monkeypatch.setenv(ENV[fn], value)
        spelled_off = value is not None and value.strip().lower() in FALSE_WORDS
        assert want is (not spelled_off), (fn, value)


def test_colbert_switch_needs_numpy_even_when_the_flag_is_on(monkeypatch):
    monkeypatch.setenv("VINC_NODE_COLBERT", "on")
    monkeypatch.setattr(colbert, "np", None)
    assert colbert.rescore_on() is False


@pytest.mark.parametrize("i", range(len(VALUES)), ids=[repr(v) for v in VALUES])
def test_quantize_reads_the_flag_words_and_refuses_the_rest(monkeypatch, i):
    value = VALUES[i]
    old_int8 = TABLE["answers"][CHANGED_COLUMN][i]
    if value is not None:
        monkeypatch.setenv("VINC_EMBED_QUANTIZE", value)
    word = None if value is None else value.strip().lower()
    if value is None or word in TRUE_WORDS:
        assert worker.quantize_on() is True
        assert _call(CHANGED_COLUMN) is True
        assert old_int8 is (value is None or value == "on"), repr(value)
    elif word in FALSE_WORDS:
        assert worker.quantize_on() is False
        assert _call(CHANGED_COLUMN) is False
        assert old_int8 is False
    else:
        with pytest.raises(ValueError) as err:
            worker.quantize_on()
        assert "VINC_EMBED_QUANTIZE" in str(err.value)
        with pytest.raises(ValueError):
            worker.artifact_name("org/Model-X")
        assert old_int8 is False


def test_quantize_values_that_read_differently_from_the_old_rule_are_these():
    """옛 판정은 정확히 `on` 일 때만 int8 이었다. 지금 int8 로 읽히는데 옛 판정이 fp32 였던 값."""
    now_int8_was_fp32 = [v for v in VALUES
                         if v is not None and v.strip().lower() in TRUE_WORDS and v != "on"]
    assert now_int8_was_fp32 == QUANTIZE_NOW_INT8_BUT_WAS_FP32


def test_quantize_unset_is_int8_and_off_is_fp32(monkeypatch):
    assert worker.artifact_name("BAAI/bge-m3") == "bge-m3-int8-rr.onnx"
    monkeypatch.setenv("VINC_EMBED_QUANTIZE", "off")
    assert worker.artifact_name("BAAI/bge-m3") == "bge-m3-fp32.onnx"
