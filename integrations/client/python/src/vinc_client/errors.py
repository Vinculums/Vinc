"""Errors raised by the Vinc client.

Every failure the v1 API reports arrives as ``{"error": {"code", "message",
"request_id", "details"}}``; the class tells the caller what to do next, the
``code`` says exactly what happened.
"""
from __future__ import annotations

from typing import Any


class VincError(Exception):
    """Base class. ``code`` is the v1 error code (for example ``quota_exceeded``)."""

    def __init__(self, code: str, message: str, *, status: int | None = None,
                 request_id: str = "", details: dict[str, Any] | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.status = status
        self.request_id = request_id
        self.details = details or {}
        self.retry_after = retry_after


class VincAuthError(VincError):
    """The key is missing, malformed, refused, or not allowed to do this.

    Configuration is wrong; retrying will not help.
    """


class VincNotFound(VincError):
    """The node, document or space does not exist (or is not reachable with this key)."""


class VincInvalidArgument(VincError):
    """The request was malformed, or the graph refused it (for example a topic limit)."""


class VincRoleChanged(VincInvalidArgument):
    """A pinned role changed since its hash was taken, so no contract was returned.

    ``details`` holds the role's current ``id``, ``rev`` and ``hash``. Read the role
    without a pin, review what changed, and pin the new hash to run on it.
    """


class VincQuotaExceeded(VincError):
    """The daily call limit was reached. ``retry_after`` is in seconds when known."""


class VincUnavailable(VincError):
    """The API could not be reached or answered with a server error.

    For a write, ``details.get("write_may_have_applied")`` says whether the
    write may already have landed; do not retry blindly when it is true.
    """


class VincPartialWrite(VincError):
    """A write was accepted only in part. ``result`` holds the server's answer.

    Parts of it may already be stored: read ``result["stored"]`` and
    ``result["warnings"]`` before writing again.
    """

    def __init__(self, message: str, result: dict[str, Any]) -> None:
        super().__init__("partial_write", message, details={"warnings": result.get("warnings")})
        self.result = result


_BY_STATUS: dict[int, type[VincError]] = {
    400: VincInvalidArgument, 401: VincAuthError, 403: VincAuthError,
    404: VincNotFound, 405: VincInvalidArgument, 409: VincInvalidArgument,
    413: VincInvalidArgument, 429: VincQuotaExceeded,
}


def error_class(status: int, code: str | None = None) -> type[VincError]:
    """The class for an HTTP status (and code); every 5xx is ``VincUnavailable``."""
    if status >= 500:
        return VincUnavailable
    if status == 409 and code == "role_changed":
        return VincRoleChanged
    return _BY_STATUS.get(status, VincError)
