"""JWT authentication and role-based authorization.

- create_token(): issues a JWT containing the user's id and role.
- get_current_user(): reads the JWT from the Authorization header and
  returns the authenticated User (or None).
- require_role(...): decorator that allows only the given roles.
- require_admin / require_seller: ready-made decorators.

The user's id and role are read from the database on every request, so a
role change takes effect immediately even for tokens issued earlier.
"""

import datetime
import os
from functools import wraps
from typing import Optional

import jwt
from flask import g, jsonify, request

from roles import UserRole
from models.users import User, find_user_by_id

# Secret used to sign JWTs. Set JWT_SECRET_KEY in the environment in production.
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-secret-change-me-in-production!")
JWT_ALGORITHM = "HS256"
TOKEN_LIFETIME = datetime.timedelta(hours=24)

# HttpOnly cookie that carries the JWT for browser (redirect) sessions.
# API clients keep using the Authorization header; the cookie is only a
# fallback so server-rendered pages work without JavaScript.
SESSION_COOKIE_NAME = "paseo_session"
# Set SESSION_COOKIE_SECURE=1 in production (HTTPS). Off by default so local
# http://localhost development works.
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "").lower() in {
    "1",
    "true",
    "yes",
}


def create_token(user: User) -> str:
    """Create a JWT for the given user. The token carries the user's id and role."""
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "iat": now,
        "exp": now + TOKEN_LIFETIME,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def get_current_user() -> Optional[User]:
    """Return the authenticated user based on the request's JWT.

    The token is read from the Authorization header first
    (Authorization: Bearer <token>), falling back to the session cookie
    set by the browser login flow.
    Returns None if no token is present, the token is invalid/expired,
    or the user no longer exists.
    """
    token = _extract_token()
    if not token:
        return None

    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None

    # The database is the source of truth for the role, so a manually
    # assigned SELLER/ADMIN role applies immediately to existing tokens.
    return find_user_by_id(user_id)


def _extract_token() -> Optional[str]:
    """Read the JWT from the Authorization header, or from the session cookie."""
    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return request.cookies.get(SESSION_COOKIE_NAME)


def set_session_cookie(response, token: str) -> None:
    """Attach the JWT to a response as an HttpOnly session cookie.

    HttpOnly keeps the token away from JavaScript; SameSite=Lax lets the
    cookie survive the top-level redirect back from Google while blocking
    cross-site subrequests.
    """
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=int(TOKEN_LIFETIME.total_seconds()),
        httponly=True,
        samesite="Lax",
        secure=SESSION_COOKIE_SECURE,
        path="/",
    )


def require_role(*allowed_roles: UserRole):
    """Decorator: allow access only to users whose role is one of allowed_roles.

    Example:
        @require_role(UserRole.SELLER, UserRole.ADMIN)
        def seller_or_admin_area(): ...
    """
    if not allowed_roles:
        raise ValueError("require_role needs at least one allowed role")

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if user is None:
                return jsonify({"error": "Authentication required"}), 401
            if user.role not in allowed_roles:
                return jsonify({"error": "Insufficient permissions"}), 403
            g.current_user = user
            return view(*args, **kwargs)

        return wrapper

    return decorator


# Ready-made decorators for the common cases. Usage: @require_admin
require_admin = require_role(UserRole.ADMIN)
require_seller = require_role(UserRole.SELLER)
