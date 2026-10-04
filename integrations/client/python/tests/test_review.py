import asyncio
import json
from pathlib import Path

import pytest

from vinc_client import ReviewOutput, UnparsedReview, parse_review
from vinc_client.review import ReviewParseError, areview_with_retry, review_with_retry


VALID = {"verdict": "RETURN", "findings": [
    {"what": "Particle follows the variable", "cite": ["record:copy-rule"]},
    {"what": "Wrong toast register", "cite": ["record:register-rule"]},
]}
P1 = json.loads((Path(__file__).parents[3] / "fixtures/reviews/p1-invalid.json").read_text(
    encoding="utf-8"))["raw"]


def test_saved_p1_stops_after_exactly_one_retry_and_cannot_be_empty_success():
    seen = []
    def request(feedback):
        seen.append(feedback)
        return P1
    with pytest.raises(UnparsedReview) as caught:
        review_with_retry(request)
    assert caught.value.status == "UNPARSED" and caught.value.attempts == 2
    assert len(seen) == 2 and seen[0] is None
    assert "invalid JSON" in seen[1] and "only parse retry" in seen[1]
    assert not hasattr(caught.value, "findings")
    assert P1 not in str(caught.value) and P1 not in seen[1]


def test_saved_p1_can_recover_two_findings_when_the_retry_is_valid():
    outputs = iter([P1, VALID])
    result = review_with_retry(lambda _: next(outputs))
    assert result.verdict == "RETURN" and len(result.findings) == 2


def test_first_valid_output_makes_one_call_and_preserves_requests_and_citations():
    seen = []
    value = {**VALID, "person_only_requests": [{"action": "merge", "reason": "person only"}]}
    def request(feedback):
        seen.append(feedback)
        return value
    result = review_with_retry(request)
    assert seen == [None]
    assert result.findings[0].cite == ["record:copy-rule"]
    assert result.person_only_requests[0].action == "merge"


@pytest.mark.parametrize("value", [
    {}, None, "```json\n{}\n```", {"verdict": "RETURN", "findings": []},
    {"verdict": "ACCEPT", "findings": VALID["findings"]},
    {"verdict": "ACCEPT", "findings": [], "secret-extra-field": "secret"},
    {"verdict": "RETURN", "findings": [{"what": " ", "cite": ["record:a"]}]},
    {"verdict": "RETURN", "findings": [{"what": "bad", "cite": []}]},
    {"verdict": "RETURN", "findings": [{"what": "bad", "cite": [42]}]},
    {"verdict": "ACCEPT", "findings": [], "person_only_requests": [{"action": "merge"}]},
])
def test_invalid_review_is_never_a_success_and_error_contains_no_input(value):
    with pytest.raises(ReviewParseError) as caught:
        parse_review(value)
    assert "secret" not in str(caught.value)


def test_constructed_or_mutated_model_is_revalidated():
    forged = ReviewOutput.model_construct(verdict="ACCEPT", findings=VALID["findings"])
    with pytest.raises(ReviewParseError):
        parse_review(forged)


def test_client_errors_are_not_parse_retried():
    calls = []
    def request(_):
        calls.append(1)
        raise RuntimeError("service unavailable")
    with pytest.raises(RuntimeError, match="service unavailable"):
        review_with_retry(request)
    assert len(calls) == 1


def test_async_retry_recovers_and_persistent_failure_stops():
    async def go():
        seen = []
        async def request(feedback):
            seen.append(feedback)
            return P1 if len(seen) == 1 else VALID
        assert len((await areview_with_retry(request)).findings) == 2
        async def broken(_):
            return P1
        with pytest.raises(UnparsedReview):
            await areview_with_retry(broken)
    asyncio.run(go())
