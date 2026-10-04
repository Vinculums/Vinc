"""Structured review helpers for LangGraph nodes, without a tool executor."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from vinc_client.review import (REVIEW_INSTRUCTIONS, ReviewOutput, ReviewParseError,
                                areview_with_retry, review_with_retry)


def _request(model: Any, messages: Any, native: bool) -> tuple[Any, list]:
    original = [HumanMessage(messages)] if isinstance(messages, str) else list(messages)
    runner = model.with_structured_output(ReviewOutput, include_raw=True) if native else model
    return runner, original


def _value(result: Any, native: bool) -> Any:
    if native:
        if not isinstance(result, dict) or result.get("parsing_error") is not None:
            raise ReviewParseError("invalid structured review")
        return result.get("parsed")
    if isinstance(result, BaseMessage):
        return result.text
    return result


def run_review(model: Any, messages: Any, *, native_structured: bool = True,
               config: Any = None, options: Mapping[str, Any] | None = None) -> ReviewOutput:
    """Run a standalone review model; persistent parse failure raises UNPARSED.

    Unsupported providers opt out with native_structured=False. Credentials,
    service errors and unsupported-schema errors do not trigger model retries.
    Generation options pass unchanged to the chat model. Tools and response_format
    belong to this helper; set provider-specific reasoning options explicitly.
    """
    runner, original = _request(model, messages, native_structured)
    request_options = _generation_options(options)

    def request(feedback: str | None) -> Any:
        result = runner.invoke([*original, HumanMessage(feedback or REVIEW_INSTRUCTIONS)],
                               config=config, **dict(request_options))
        return _value(result, native_structured)

    return review_with_retry(request)


async def arun_review(model: Any, messages: Any, *, native_structured: bool = True,
                      config: Any = None, options: Mapping[str, Any] | None = None) -> ReviewOutput:
    runner, original = _request(model, messages, native_structured)
    request_options = _generation_options(options)

    async def request(feedback: str | None) -> Any:
        result = await runner.ainvoke([*original, HumanMessage(feedback or REVIEW_INSTRUCTIONS)],
                                     config=config, **dict(request_options))
        return _value(result, native_structured)

    return await areview_with_retry(request)


def _generation_options(options: Mapping[str, Any] | None) -> dict[str, Any]:
    # 스키마용 도구와 실행 도구 선택은 호출자 옵션으로 덮어쓰지 않는다.
    return {key: value for key, value in dict(options or {}).items()
            if key not in {"tools", "tool_choice", "response_format"}}
