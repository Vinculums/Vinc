"""A parsed review is a proposal, never approval or permission to execute it."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Text = Annotated[str, Field(strict=True, min_length=1, max_length=16384, pattern=r"\S")]
Citation = Annotated[str, Field(strict=True, min_length=1, max_length=2048, pattern=r"\S")]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    what: Text
    cite: Annotated[list[Citation], Field(min_length=1, max_length=256)]
    quote: Text | None = None
    rule: Text | None = None


class PersonOnlyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Text
    reason: Text


class ReviewOutput(BaseModel):
    """Shape validation only: citation existence and approval need separate gates."""
    model_config = ConfigDict(extra="forbid", strict=True)
    verdict: Literal["ACCEPT", "RETURN"]
    findings: Annotated[list[Finding], Field(max_length=100)]
    person_only_requests: Annotated[list[PersonOnlyRequest], Field(max_length=100)] = Field(
        default_factory=list)

    @model_validator(mode="after")
    def consistent_verdict(self) -> ReviewOutput:
        if (self.verdict == "RETURN") != bool(self.findings):
            raise ValueError("RETURN requires findings; ACCEPT requires none")
        return self


class ReviewParseError(ValueError):
    """Contains fixed diagnostics, never a model response or validation input."""


class UnparsedReview(ValueError):
    status = "UNPARSED"
    attempts = 2

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"UNPARSED after two outputs; escalate to a person: {reason}")


REVIEW_INSTRUCTIONS = (
    'Return only a JSON object: verdict is "ACCEPT" with findings=[] or "RETURN" '
    'with at least one finding. Each finding has what (nonempty text), cite '
    '(one or more evidence IDs), and optional quote/rule. person_only_requests '
    'is a list of {action, reason}; requests are proposals, not executed actions. '
    'Do not invent citations or treat formatting success as approval.'
)


def parse_review(value: Any) -> ReviewOutput:
    """Reject malformed JSON, extra fields and contradictory empty-success results."""
    try:
        if isinstance(value, ReviewOutput):
            # Revalidate even model_construct instances and subsequently mutated objects.
            value = value.model_dump(mode="python", warnings=False)
        if isinstance(value, str):
            if len(value) > 65536:
                raise ReviewParseError("review exceeds 65536 characters")
            return ReviewOutput.model_validate_json(value)
        return ReviewOutput.model_validate(value)
    except ValidationError as error:
        codes = {item["type"] for item in error.errors(
            include_input=False, include_context=False, include_url=False)}
        if "json_invalid" in codes:
            reason = "invalid JSON"
        else:
            # Neither exception text nor field names are safe to echo to another model.
            reason = "invalid review schema or inconsistent verdict"
        raise ReviewParseError(reason) from None


def retry_feedback(error: ReviewParseError) -> str:
    return (f"The preceding output failed parsing: {error}. This is the only parse retry. "
            "Return the complete review again; preserve all findings. " + REVIEW_INSTRUCTIONS)


def review_with_retry(request: Callable[[str | None], Any]) -> ReviewOutput:
    """At most two callback invocations; service and execution errors propagate."""
    feedback = None
    for attempt in range(2):
        try:
            return parse_review(request(feedback))
        except ReviewParseError as error:
            if attempt:
                raise UnparsedReview(str(error)) from None
            feedback = retry_feedback(error)
    raise AssertionError("unreachable")


async def areview_with_retry(
    request: Callable[[str | None], Awaitable[Any]],
) -> ReviewOutput:
    feedback = None
    for attempt in range(2):
        try:
            return parse_review(await request(feedback))
        except ReviewParseError as error:
            if attempt:
                raise UnparsedReview(str(error)) from None
            feedback = retry_feedback(error)
    raise AssertionError("unreachable")
