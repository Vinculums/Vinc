"""OpenAI 호환 실행기(``azure:<배포>``)가 허브의 도구 목록을 그대로 보내고 속도 제한을 견디는지 본다.

실제 서버 대신 ``transport`` 자리에 가짜를 꽂는다. 키는 환경변수에서만 읽으므로 가짜 값을 넣는다.
"""
from __future__ import annotations

import io
import urllib.error

import pytest

from rag_eval.agent_tasks import models


@pytest.fixture(autouse=True)
def _azure_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(models.OpenAIChatModel, "_sleep", staticmethod(lambda s: None))


def _http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("https://example.invalid", code, "x", headers, io.BytesIO(b""))


def _ok(text="done"):
    return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            "model": "gpt-test"}


def test_a_top_level_combinator_does_not_reach_the_wire():
    seen = {}

    def transport(url, headers, body):
        seen["tools"] = body["tools"]
        return _ok()

    tool = {"name": "vinc_edges", "description": "edges",
            "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "ids": {"type": "array", "items": {"type": "string"}}},
                           "anyOf": [{"required": ["id"]}, {"required": ["ids"]}], "required": ["x"]}}
    plain = {"name": "vinc_whoami", "parameters": {"type": "object", "properties": {}}}
    model = models.OpenAIChatModel("azure:lab-mini", transport=transport)
    turn = model.step([{"role": "user", "content": "hi"}], [tool, plain])

    assert turn.error is None or turn.error == ""
    wired = {t["function"]["name"]: t["function"]["parameters"] for t in seen["tools"]}
    assert "anyOf" not in wired["vinc_edges"]
    assert wired["vinc_edges"]["type"] == "object"
    assert set(wired["vinc_edges"]["properties"]) == {"id", "ids"}
    assert wired["vinc_edges"]["required"] == ["x"]
    assert wired["vinc_whoami"] == {"type": "object", "properties": {}}


def test_a_schema_without_parameters_becomes_an_empty_object():
    assert models._wire_parameters(None) == {"type": "object", "properties": {}}
    assert models._wire_parameters({"oneOf": [{}], "type": "object"}) == {"type": "object", "properties": {}}


def test_a_rate_limit_is_retried_and_the_later_answer_is_used(monkeypatch):
    waits = []
    monkeypatch.setattr(models.OpenAIChatModel, "_sleep", staticmethod(waits.append))
    calls = {"n": 0}

    def transport(url, headers, body):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(429, retry_after="7")
        return _ok("finally")

    turn = models.OpenAIChatModel("azure:lab-mini", transport=transport).step(
        [{"role": "user", "content": "hi"}], [])
    assert turn.text == "finally" and not turn.error
    assert calls["n"] == 3 and waits == [7.0, 7.0]


def test_the_wait_is_capped_and_defaults_to_a_growing_backoff(monkeypatch):
    waits = []
    monkeypatch.setattr(models.OpenAIChatModel, "_sleep", staticmethod(waits.append))
    seq = [_http_error(429, retry_after="9999"), _http_error(503), _ok()]

    def transport(url, headers, body):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    turn = models.OpenAIChatModel("azure:lab-mini", transport=transport).step([{"role": "user", "content": "hi"}], [])
    assert not turn.error
    assert waits == [models._RETRY_CAP_SECONDS, 4.0]


def test_giving_up_reports_only_the_status_number():
    def transport(url, headers, body):
        raise _http_error(429)

    turn = models.OpenAIChatModel("azure:lab-mini", transport=transport).step([{"role": "user", "content": "hi"}], [])
    assert turn.error == "http 429"


def test_a_client_error_is_not_retried():
    calls = {"n": 0}

    def transport(url, headers, body):
        calls["n"] += 1
        raise _http_error(400)

    turn = models.OpenAIChatModel("azure:lab-mini", transport=transport).step([{"role": "user", "content": "hi"}], [])
    assert turn.error == "http 400" and calls["n"] == 1
