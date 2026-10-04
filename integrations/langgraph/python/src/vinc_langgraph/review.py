"""Structured review helpers for LangGraph nodes, without a tool executor."""
from __future__ import annotations

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
               config: Any = None) -> ReviewOutput:
    """Run a standalone review model; persistent parse failure raises UNPARSED.

    Unsupported providers opt out with native_structured=False. Credentials,
    service errors and unsupported-schema errors do not trigger model retries.
    """
    runner, original = _request(model, messages, native_structured)

    def request(feedback: str | None) -> Any:
        result = runner.invoke([*original, HumanMessage(feedback or REVIEW_INSTRUCTIONS)],
                               config=config)
        return _value(result, native_structured)

    return review_with_retry(request)


async def arun_review(model: Any, messages: Any, *, native_structured: bool = True,
                      config: Any = None) -> ReviewOutput:
    runner, original = _request(model, messages, native_structured)

    async def request(feedback: str | None) -> Any:
        result = await runner.ainvoke([*original, HumanMessage(feedback or REVIEW_INSTRUCTIONS)],
                                     config=config)
        return _value(result, native_structured)

    return await areview_with_retry(request)
