"""Error handling.

One place converts a service failure into an HTTP response, so the same rule
always produces the same status code and the same body shape:

    {"error": "<human readable>", "code": "<machine readable>", "details": {...}}

`details` is optional and carries whatever the caller needs to fix the request
(how many points are missing, how much stock is left). `code` is for the
frontend to branch on; `error` is for a human to read.
"""

from flask import jsonify
from werkzeug.exceptions import HTTPException

from services.errors import ServiceError


def register_error_handlers(app) -> None:
    @app.errorhandler(ServiceError)
    def handle_service_error(exc: ServiceError):
        db_session_rollback()
        response = jsonify(exc.to_dict())
        # Carried by the error rather than built here, so a service can say "wait
        # this long" without the handler needing to know which error it is.
        for name, value in (exc.headers or {}).items():
            response.headers[name] = str(value)
        return response, exc.status_code

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        # Keep Werkzeug's own JSON responses in the same shape as ours, so a
        # client never has to parse two different error formats.
        return (
            jsonify(
                {
                    "error": exc.description,
                    "code": (exc.name or "http_error").lower().replace(" ", "_"),
                }
            ),
            exc.code or 500,
        )

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        # Anything reaching here is a bug, not a business rule. Roll back so a
        # failed request cannot leave a half-written transaction, log it, and
        # return an opaque message: the exception text can leak table and column
        # names, and it is not actionable for the caller.
        db_session_rollback()
        app.logger.exception("Unhandled error: %s", exc)
        return jsonify({"error": "Internal server error", "code": "internal_error"}), 500


def db_session_rollback() -> None:
    """Discard the failed unit of work.

    Imported lazily so this module does not depend on the app being configured.
    """
    try:
        from extensions import db

        db.session.rollback()
    except Exception:  # pragma: no cover - rollback must never mask the error
        pass
