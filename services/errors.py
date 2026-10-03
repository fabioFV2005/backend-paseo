"""Typed errors the service layer raises.

Routes translate these into HTTP responses in one place (see api/errors.py), so
a service can say what went wrong without knowing anything about HTTP, and the
same rule failure produces the same status code everywhere.

Each error carries the HTTP status it maps to, so adding a new failure mode
does not mean touching the error handler.
"""


class ServiceError(Exception):
    """Base for every expected, business-level failure.

    These are NOT bugs: they are conditions the API is designed to report to the
    caller. Anything that is an actual defect should raise its own exception and
    become a 500.
    """

    status_code = 400
    error_code = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        details: dict | None = None,
        headers: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        # Headers are for the cases where the caller can fix the failure by
        # waiting (Retry-After) rather than by changing the request. Kept
        # optional so the common case stays a one-argument raise.
        self.headers = headers or {}

    def to_dict(self) -> dict:
        payload = {"error": self.message, "code": self.error_code}
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(ServiceError):
    """The caller sent something malformed or self-contradictory."""

    status_code = 400
    error_code = "validation_error"


class NotFoundError(ServiceError):
    """The requested entity does not exist, or is not visible to this caller."""

    status_code = 404
    error_code = "not_found"


class ForbiddenError(ServiceError):
    """Authenticated, but not allowed to do this.

    Deliberately distinct from NotFoundError: for resources the caller may not
    see at all we return 404 to avoid confirming they exist, but for a
    permission failure on a known resource 403 is the honest answer.
    """

    status_code = 403
    error_code = "forbidden"


class UnauthorizedError(ServiceError):
    """No valid credentials."""

    status_code = 401
    error_code = "unauthorized"


class ConflictError(ServiceError):
    """The request is valid but conflicts with current state.

    Used for the race-y cases: not enough stock, not enough points, redeeming
    an already-used coupon. 409 rather than 400 because retrying after changing
    state may well succeed.
    """

    status_code = 409
    error_code = "conflict"


class BusinessRuleError(ServiceError):
    """The action is well-formed but violates a rule of the domain.

    Example: trying to move an order backwards, or applying a coupon to a
    purchase below its minimum.
    """

    status_code = 422
    error_code = "business_rule_violation"
