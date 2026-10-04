"""One parse retry for a stateless, non-streaming review agent."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_framework import Message
from pydantic import ValidationError
from vinc_client.review import (REVIEW_INSTRUCTIONS, ReviewOutput, ReviewParseError,
                                areview_with_retry)


async def run_review(
    agent: Any, messages: Any, *, native_structured: bool = True,
    options: Mapping[str, Any] | None = None,
) -> ReviewOutput:
    """Use response_format when supported; unsupported clients opt out explicitly.

    No shared session, streaming or executable tools. Configured middleware and
    context providers must themselves be read-only. Other client errors propagate.
    """
    original = [Message("user", [messages])] if isinstance(messages, str) else list(messages)
    request_options = dict(options or {})
    request_options.pop("response_format", None)
    request_options["tool_choice"] = "none"
    if native_structured:
        request_options["response_format"] = ReviewOutput

    async def request(feedback: str | None) -> Any:
        prompt = [*original, Message("user", [feedback or REVIEW_INSTRUCTIONS])]
        response = await agent.run(prompt, tools=[], options=dict(request_options))
        if native_structured:
            try:
                value = response.value
            except ValidationError:
                raise ReviewParseError("invalid structured review") from None
            if value is not None:
                return value
        return response.text

    return await areview_with_retry(request)
