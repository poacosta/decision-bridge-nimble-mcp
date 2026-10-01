"""Stable application error codes shared by the CLI and the MCP tools."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    OLLAMA_UNAVAILABLE = "OLLAMA_UNAVAILABLE"
    OLLAMA_INCOMPATIBLE = "OLLAMA_INCOMPATIBLE"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MODEL_NOT_LOCAL = "MODEL_NOT_LOCAL"
    DECISION_UNSUPPORTED = "DECISION_UNSUPPORTED"
    CONTEXT_LIMIT = "CONTEXT_LIMIT"
    TIMEOUT = "TIMEOUT"
    BUSY = "BUSY"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    INVALID_UPSTREAM_RESPONSE = "INVALID_UPSTREAM_RESPONSE"


_USAGE_ERRORS = frozenset(
    {ErrorCode.INVALID_INPUT, ErrorCode.PAYLOAD_TOO_LARGE, ErrorCode.INVALID_CONFIGURATION}
)


def classify_retryable(code: ErrorCode, http_status: int | None = None) -> bool:
    """Decide whether a caller could reasonably try the same request again later.

    Conservative default: only failures that are plainly transient are retryable. Note that the
    bridge itself never retries inference; this flag is advice for the caller.
    """
    if code in (ErrorCode.TIMEOUT, ErrorCode.BUSY, ErrorCode.OLLAMA_UNAVAILABLE):
        return True
    if code is ErrorCode.UPSTREAM_ERROR:
        return http_status in (502, 503, 504)
    return False


class BridgeError(Exception):
    """An error with a stable code, a short actionable message, and a CLI exit code."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool | None = None,
        request_id: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = classify_retryable(code, http_status) if retryable is None else retryable
        self.request_id = request_id

    @property
    def exit_code(self) -> int:
        return 2 if self.code in _USAGE_ERRORS else 1

    def to_payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "retryable": self.retryable,
                "request_id": self.request_id,
            }
        }
