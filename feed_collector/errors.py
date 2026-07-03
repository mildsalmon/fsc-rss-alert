from __future__ import annotations

from enum import Enum

import requests


class PollError(RuntimeError):
    pass


class FetchFailureReason(str, Enum):
    STRUCTURE_CHANGED = "STRUCTURE_CHANGED"
    EMPTY_RESULT = "EMPTY_RESULT"
    BLOCKED = "BLOCKED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    NOT_FOUND = "NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    UNKNOWN = "UNKNOWN"


def infer_from_error(exc: BaseException) -> FetchFailureReason:
    messages = " ".join(str(error) for error in _error_chain(exc)).lower()

    if isinstance(exc, requests.Timeout) or "timed out" in messages or "timeout" in messages:
        return FetchFailureReason.TIMEOUT
    if "404" in messages or "not found" in messages:
        return FetchFailureReason.NOT_FOUND
    if "login" in messages or "unauthorized" in messages or "401" in messages:
        return FetchFailureReason.LOGIN_REQUIRED
    if (
        "403" in messages
        or "blocked" in messages
        or "captcha" in messages
        or "cloudflare" in messages
        or "rate limited" in messages
        or "cookie gate" in messages
    ):
        return FetchFailureReason.BLOCKED
    if "produced no items" in messages:
        return FetchFailureReason.EMPTY_RESULT
    if (
        "parse failed" in messages
        or "returned no rows" in messages
        or "list_path" in messages
        or "missing required field" in messages
        or "response missing" in messages
        or "not valid json" in messages
        or "newest-first" in messages
        or "invalid" in messages
    ):
        return FetchFailureReason.STRUCTURE_CHANGED
    if isinstance(exc, requests.RequestException) or "connection" in messages or "network" in messages:
        return FetchFailureReason.NETWORK
    return FetchFailureReason.UNKNOWN


def _error_chain(exc: BaseException) -> list[BaseException]:
    errors = [exc]
    current = exc
    while current.__cause__ is not None:
        current = current.__cause__
        errors.append(current)
    return errors


__all__ = ["FetchFailureReason", "PollError", "infer_from_error"]
