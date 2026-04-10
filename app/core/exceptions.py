"""
Application exception hierarchy.
All domain errors inherit from AppError so handlers can catch them in one place.
HTTP status codes are declared here to keep them out of business logic.
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all application errors."""
    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict:
        payload: dict = {"error": self.error_code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


class NotFoundError(AppError):
    """Raised when a requested entity does not exist."""
    status_code = 404
    error_code = "not_found"


class InvalidCallStateError(AppError):
    """Raised when an operation is invalid for the current call status."""
    status_code = 422
    error_code = "invalid_call_state"


class PhoneNormalizationError(AppError):
    """Raised when a phone number cannot be parsed or normalized."""
    status_code = 422
    error_code = "phone_normalization_error"


class EngineError(AppError):
    """Raised when the call engine (Vapi, Mango, etc.) returns an error."""
    status_code = 502
    error_code = "engine_error"


class TransferError(AppError):
    """Raised when the transfer engine fails during a warm transfer attempt."""
    status_code = 502
    error_code = "transfer_error"


class NoManagerAvailableError(AppError):
    """Raised when no active, available manager can be found for a transfer."""
    status_code = 503
    error_code = "no_manager_available"


class BlockedPhoneError(AppError):
    """Raised when the target phone number is on the deny list."""
    status_code = 422
    error_code = "blocked_phone"


class QuietHoursError(AppError):
    """Raised when a call is attempted outside the allowed calling window."""
    status_code = 422
    error_code = "quiet_hours"


class RateLimitError(AppError):
    """Raised when the caller exceeds the allowed request rate."""
    status_code = 429
    error_code = "rate_limit_exceeded"


class InvalidTransitionError(AppError):
    """Raised when a status transition is not allowed by the state machine."""
    status_code = 422
    error_code = "invalid_transition"


class TransferTimeoutError(TransferError):
    """Raised when a transfer phase (dial, briefing, bridge) exceeds its timeout."""
    status_code = 502
    error_code = "transfer_timeout"


class CallerDroppedError(TransferError):
    """Raised when the customer hangs up while a transfer is in progress."""
    status_code = 409
    error_code = "caller_dropped"
