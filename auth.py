"""JWT authentication and role-based authorization.

- create_token(): issues a JWT carrying the user's id.
- load_current_user(): runs once per request, resolves the token into
  ``g.current_user``.
- require_role(...): decorator allowing only the given roles.

Two design decisions that are load-bearing:

1. **The role claim in the JWT is ignored.** ``create_token`` writes it for
   debugging convenience, but ``load_current_user`` never reads it: it re-reads
   the role from the database on every request. A role change therefore takes
   effect immediately, even for tokens issued 24 hours earlier, and a token
   whose ``role`` claim was edited cannot escalate anything. ``test_role_claim_
   is_ignored_when_forging_a_token`` pins this behaviour.

2. **The token only carries an id, nothing else.** No email, no name, no points.
   Anything sensitive is fetched fresh from the database, so a stale token can
   never present stale personal data.
"""

import datetime
from functools import wraps
from typing import Optional

import jwt
from flask import current_app, g, jsonify, request

import config
from models import User, find_user_by_id
from roles import UserRole


def create_token(user: User) -> str:
    """Issue a signed JWT for the given user.

    ``sub`` is the string form of the UUID primary key. ``role`` is included
    purely so a decoded token is readable in a console; nothing trusts it.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": str(user.id),
        "role": user.role_enum.value,
        "iat": now,
        "exp": now + datetime.timedelta(hours=config.TOKEN_LIFETIME_HOURS),
    }
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)


def extract_token() -> Optional[str]:
    """Read the JWT from the Authorization header, falling back to the cookie.

    The header wins so an API client is never silently overridden by a stale
    browser cookie left over from a previous login in the same browser.
    """
    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token.strip()
    return request.cookies.get(config.SESSION_COOKIE_NAME)


def resolve_token_user(token: str) -> Optional[User]:
    """Validate a raw JWT and return the user it identifies, or None."""
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            config.JWT_SECRET_KEY,
            algorithms=[config.JWT_ALGORITHM],
        )
    except jwt.PyJWTError:
        # Covers bad signature, expired, malformed, wrong algorithm. Any of
        # those means "not authenticated"; the reason is never sent to the
        # client because it only helps an attacker.
        return None

    subject = payload.get("sub")
    if not isinstance(subject, str):
        return None
    return find_user_by_id(subject)


def load_current_user() -> None:
    """Populate ``g.current_user`` once per request.

    Registered as a ``before_request`` hook by the app factory, so every route
    can read ``g.current_user`` without caring whether it is protected. It is
    None for anonymous requests.
    """
    g.current_user = resolve_token_user(extract_token())


def get_current_user() -> Optional[User]:
    """The authenticated user for this request, or None.

    Works outside a request context too (returns None), so it is safe to call
    from anywhere without guarding.
    """
    return getattr(g, "current_user", None)


def set_session_cookie(response, token: str) -> None:
    """Attach the JWT to a response as an HttpOnly cookie.

    HttpOnly keeps the token away from JavaScript, so an XSS bug cannot read
    it. SameSite=Lax lets the cookie survive the top-level redirect back from
    Google while blocking cross-site subrequests.
    """
    response.set_cookie(
        config.SESSION_COOKIE_NAME,
        token,
        max_age=config.TOKEN_LIFETIME_HOURS * 3600,
        httponly=True,
        samesite="Lax",
        secure=config.SESSION_COOKIE_SECURE,
        path="/",
    )


def clear_session_cookie(response) -> None:
    """Remove the session cookie. Must mirror set_session_cookie's arguments or
    the browser keeps the original cookie."""
    response.delete_cookie(
        config.SESSION_COOKIE_NAME,
        path="/",
        secure=config.SESSION_COOKIE_SECURE,
        samesite="Lax",
        httponly=True,
    )


def require_role(*allowed_roles: UserRole):
    """Decorator: allow access only to users whose role is in ``allowed_roles``.

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
            if user.role_enum not in allowed_roles:
                return jsonify({"error": "Insufficient permissions"}), 403
            g.current_user = user
            return view(*args, **kwargs)

        return wrapper

    return decorator


# Ready-made decorators for the common cases.
require_admin = require_role(UserRole.ADMIN)
require_seller = require_role(UserRole.SELLER)
# An endpoint that lets a business act on its own catalog, but also lets the
# Paseo admin snoop/act on any of them.
require_seller_or_admin = require_role(UserRole.SELLER, UserRole.ADMIN)
