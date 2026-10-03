"""Authentication routes: the Google sign-in flows, logout and the profile.

Kept at the original URLs (/login, /auth/google/login, /auth/google/callback,
/api/me) so anything already pointed at them keeps working.
"""

import secrets

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from auth import (
    clear_session_cookie,
    create_token,
    get_current_user,
    set_session_cookie,
)
from extensions import db, limiter
from google_auth import (
    build_google_auth_url,
    exchange_code_for_identity,
    verify_google_token,
)
from models import (
    GoogleAccountConflict,
    User,
    get_or_create_google_user,
    update_user_location,
)
from roles import UserRole
from services.errors import ValidationError
from services.points import get_balance, get_level_progress

auth_bp = Blueprint("auth", __name__)

# User-facing messages for the login page, keyed by the "error" query param.
# Each message names the problem and how to recover.
LOGIN_ERRORS = {
    "cancelled": "You cancelled the Google sign-in. Nothing was shared - you can try again whenever you're ready.",
    "google": "We couldn't verify your Google account. Please try again.",
    "invalid_state": "Your sign-in session expired. Please try again.",
    "unavailable": "Google sign-in is temporarily unavailable. Please try again later.",
}


def landing_url_for(user: User) -> str:
    """Where to send a user after they sign in, based on their role."""
    role = user.role_enum
    if role == UserRole.ADMIN:
        return url_for("auth.admin_dashboard")
    if role == UserRole.SELLER:
        return url_for("auth.seller_dashboard")
    return url_for("auth.home")


@auth_bp.route("/", endpoint="home")
def home():
    """Storefront home / service descriptor."""
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify(
            {
                "service": "Paseo Aranjuez API",
                "roles": {"customer": "/api/me", "seller": "/api/business/orders", "admin": "/api/admin/stats"},
            }
        )
    return render_template("client/home.html", user=get_current_user())


@auth_bp.route("/seller/dashboard", endpoint="seller_dashboard")
def seller_dashboard():
    user = get_current_user()
    return render_template("seller/dashboard.html", user=user)


@auth_bp.route("/admin/dashboard", endpoint="admin_dashboard")
def admin_dashboard():
    user = get_current_user()
    return render_template("admin/dashboard.html", user=user)


@auth_bp.route("/login")
def login():
    """Render the login page. Already-authenticated users go straight on."""
    user = get_current_user()
    if user is not None:
        return redirect(landing_url_for(user))
    return render_template(
        "auth/login.html",
        error_message=LOGIN_ERRORS.get(request.args.get("error", "")),
    )


@auth_bp.route("/auth/google/login")
def google_oauth_login():
    """Start the Google OAuth 2.0 redirect flow.

    Generates a random CSRF state stored in the signed Flask session, and
    redirects the browser to Google's consent screen.
    """
    state = secrets.token_urlsafe(32)
    session["google_oauth_state"] = state
    try:
        return redirect(build_google_auth_url(state))
    except ValueError:
        session.pop("google_oauth_state", None)
        return redirect(url_for("auth.login", error="unavailable"))


@auth_bp.route("/auth/google/callback")
def google_oauth_callback():
    """Handle Google's redirect back after the consent screen.

    - User cancelled (error=access_denied) -> back to /login with a message.
    - State mismatch (CSRF or expired)       -> back to /login with a message.
    - Success -> find or create the user, issue the session JWT in an HttpOnly
      cookie and redirect to the role-based landing page.
    """
    error = request.args.get("error")
    if error:
        # access_denied is Google telling us the user cancelled the consent screen.
        code = "cancelled" if error == "access_denied" else "google"
        return redirect(url_for("auth.login", error=code))

    state = request.args.get("state", "")
    expected_state = session.pop("google_oauth_state", None)
    # compare_digest is constant-time; session.pop makes the state single-use,
    # so a leaked callback URL cannot be replayed.
    if not expected_state or not secrets.compare_digest(expected_state, state):
        return redirect(url_for("auth.login", error="invalid_state"))

    code = request.args.get("code")
    if not code:
        return redirect(url_for("auth.login", error="google"))

    try:
        identity = exchange_code_for_identity(code)  # sub/email/name/picture
    except ValueError:
        return redirect(url_for("auth.login", error="google"))

    try:
        user = get_or_create_google_user(
            google_sub=identity["sub"],
            email=identity["email"],
            name=identity["name"],
            picture=identity.get("picture"),
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Could not persist the user after Google sign-in")
        return redirect(url_for("auth.login", error="google"))

    response = redirect(landing_url_for(user))
    set_session_cookie(response, create_token(user))
    return response


# Tight on purpose: every call hits Google's tokeninfo endpoint and creates or
# looks up a user, so this is both the credential-replay surface and a way to
# burn the API quota. 10/min is well above a human who mistypes once.
#
# Order matters: @route must sit ABOVE @limiter.limit. Blueprint.route registers
# the function it is given, so if it were the inner decorator it would register
# the bare function and throw away the limiter's wrapper.
@auth_bp.route("/auth/google", methods=["POST"])
@limiter.limit("10 per minute")
def google_login():
    """Register or log in with a Google account, from a JavaScript client.

    The request body may contain ONLY the Google ID token. Any extra field
    (such as a "role" sent by the frontend) is ignored: the role is decided
    exclusively by the backend.

    - New user    -> created with the default USER role.
    - Existing user -> keeps their current role (never reset, never upgraded).
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")

    token = data.get("token")
    if not token:
        raise ValidationError("Missing Google ID token")

    try:
        identity = verify_google_token(token)  # sub/email/name/picture
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "invalid_google_token"}), 401

    try:
        user = get_or_create_google_user(
            google_sub=identity["sub"],
            email=identity["email"],
            name=identity["name"],
            picture=identity.get("picture"),
        )
    except GoogleAccountConflict as exc:
        # The email is verified by Google but already tied to another Google
        # account. This is an account-link question, not a server fault, and it
        # needs a human to resolve it.
        db.session.rollback()
        return jsonify({"error": str(exc), "code": "google_account_conflict"}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Could not persist the user after Google sign-in")
        return jsonify({"error": "Could not complete sign-in"}), 500

    return jsonify(
        {
            "token": create_token(user),
            "user": user.to_dict(),
        }
    )


@auth_bp.route("/logout", methods=["GET", "POST"], endpoint="logout")
@auth_bp.route("/auth/logout", methods=["GET", "POST"])
def logout():
    """Clear session cookie and redirect to home if GET, or return JSON if POST."""
    if request.method == "GET":
        response = redirect(url_for("auth.home"))
    else:
        response = jsonify({"ok": True})
    clear_session_cookie(response)
    return response


@auth_bp.route("/me")
@auth_bp.route("/api/me")
def me():
    """The caller's own profile."""
    user = get_current_user()
    if user is None:
        return jsonify({"error": "Authentication required", "code": "unauthorized"}), 401

    payload = user.to_dict()
    balance = get_balance(user)
    payload["points"] = balance
    payload["level_progress"] = get_level_progress(balance)
    if user.business is not None:
        payload["business"] = user.business.to_dict()
    return jsonify(payload)


@auth_bp.route("/me/location", methods=["POST", "PUT"])
@auth_bp.route("/api/me/location", methods=["POST", "PUT"])
def update_location():
    """Save the authenticated user's location (sent by the device's GPS)."""
    user = get_current_user()
    if user is None:
        return jsonify({"error": "Authentication required", "code": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    try:
        latitude = float(data["latitude"])
        longitude = float(data["longitude"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "latitude and longitude (numbers) are required"}), 400

    try:
        user = update_user_location(user.id, latitude, longitude)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"user": user.to_dict()})
