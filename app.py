import os
import secrets

from dotenv import load_dotenv

# Load the .env file BEFORE importing modules that read environment variables.
load_dotenv()

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for

from auth import (
    JWT_SECRET_KEY,
    create_token,
    get_current_user,
    require_admin,
    require_seller,
    set_session_cookie,
)
from google_auth import build_google_auth_url, exchange_code_for_identity, verify_google_token
from models.users import User, get_or_create_google_user, update_user_location
from roles import UserRole

app = Flask(__name__)
# Signs the Flask session cookie that carries the OAuth "state" CSRF token
# between the redirect to Google and the callback.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or JWT_SECRET_KEY

# User-facing messages for the login page, keyed by the "error" query param.
# Each message names the problem and how to recover.
LOGIN_ERRORS = {
    "cancelled": "You cancelled the Google sign-in. Nothing was shared — you can try again whenever you're ready.",
    "google": "We couldn't verify your Google account. Please try again.",
    "invalid_state": "Your sign-in session expired. Please try again.",
    "unavailable": "Google sign-in is temporarily unavailable. Please try again later.",
}


def landing_url_for(user: User) -> str:
    """Role-based landing page after a successful login."""
    if user.role is UserRole.ADMIN:
        return url_for("admin_dashboard")
    if user.role is UserRole.SELLER:
        return url_for("seller_dashboard")
    return url_for("hello_world")


def user_payload(user: User) -> dict:
    """Public JSON representation of a user (used by /auth/google and /me)."""
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role.value,
        "picture": user.picture,
        "latitude": user.latitude,
        "longitude": user.longitude,
    }


@app.route("/")
def hello_world():
    return "Hello World!"


@app.route("/login")
def login():
    """Render the login page. Already-authenticated users go straight on."""
    user = get_current_user()
    if user is not None:
        return redirect(landing_url_for(user))
    return render_template("auth/login.html", error_message=LOGIN_ERRORS.get(request.args.get("error", "")))


@app.route("/auth/google/login")
def google_oauth_login():
    """Start the Google OAuth 2.0 redirect flow.

    Generates a random CSRF state (stored in the signed Flask session) and
    redirects the browser to Google's consent screen.
    """
    state = secrets.token_urlsafe(32)
    session["google_oauth_state"] = state
    try:
        return redirect(build_google_auth_url(state))
    except ValueError:
        session.pop("google_oauth_state", None)
        return redirect(url_for("login", error="unavailable"))


@app.route("/auth/google/callback")
def google_oauth_callback():
    """Handle Google's redirect back after the consent screen.

    - User cancelled (error=access_denied) -> back to /login with a message.
    - State mismatch (CSRF/expired)        -> back to /login with a message.
    - Success -> find or create the user, issue the session JWT in an
      HttpOnly cookie and redirect to the role-based landing page.
    """
    error = request.args.get("error")
    if error:
        # access_denied is Google telling us the user cancelled the consent screen.
        code = "cancelled" if error == "access_denied" else "google"
        return redirect(url_for("login", error=code))

    state = request.args.get("state", "")
    expected_state = session.pop("google_oauth_state", None)
    if not expected_state or not secrets.compare_digest(expected_state, state):
        return redirect(url_for("login", error="invalid_state"))

    code = request.args.get("code")
    if not code:
        return redirect(url_for("login", error="google"))

    try:
        identity = exchange_code_for_identity(code)  # returns only sub/email/name/picture
    except ValueError:
        return redirect(url_for("login", error="google"))

    user = get_or_create_google_user(
        google_sub=identity["sub"],
        email=identity["email"],
        name=identity["name"],
        picture=identity["picture"],
    )

    response = redirect(landing_url_for(user))
    set_session_cookie(response, create_token(user))
    return response


@app.route("/auth/google", methods=["POST"])
def google_login():
    """Register or log in with a Google account.

    The request body may contain ONLY the Google ID token. Any extra field
    (such as a "role" sent by the frontend) is ignored: the role is decided
    exclusively by the backend.

    - New user  -> created with the default USER role.
    - Existing user -> keeps their current role (never reset, never upgraded).
    """
    data = request.get_json(silent=True) or {}
    token = data.get("token")
    if not token:
        return jsonify({"error": "Missing Google ID token"}), 400

    try:
        identity = verify_google_token(token)  # returns only sub/email/name/picture
    except ValueError:
        return jsonify({"error": "Invalid Google token"}), 401

    user = get_or_create_google_user(
        google_sub=identity["sub"],
        email=identity["email"],
        name=identity["name"],
        picture=identity["picture"],
    )

    return jsonify({"token": create_token(user), "user": user_payload(user)})


@app.route("/me")
def me():
    """Any authenticated user can see their own profile."""
    user = get_current_user()
    if user is None:
        return jsonify({"error": "Authentication required"}), 401
    return jsonify(user_payload(user))


@app.route("/me/location", methods=["POST", "PUT"])
def update_my_location():
    """Save the authenticated user's location (sent by the device's GPS).

    Body JSON: {"latitude": 4.611, "longitude": -74.081}
    """
    user = get_current_user()
    if user is None:
        return jsonify({"error": "Authentication required"}), 401

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

    return jsonify({"user": user_payload(user)})


@app.route("/seller/dashboard")
@require_seller
def seller_dashboard():
    return jsonify({"message": f"Welcome to the seller dashboard, {g.current_user.name}"})


@app.route("/admin/dashboard")
@require_admin
def admin_dashboard():
    return jsonify({"message": f"Welcome to the admin dashboard, {g.current_user.name}"})


if __name__ == "__main__":
    app.run()
