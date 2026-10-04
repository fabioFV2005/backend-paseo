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
    Business,
    GoogleAccountConflict,
    Order,
    Product,
    Transaction,
    User,
    find_user_by_email,
    get_or_create_google_user,
    update_user_location,
)
from roles import UserRole
from services.business_service import get_business_for_owner, get_business_sales_analytics
from services.errors import ValidationError
from services.catalog import list_businesses, list_categories, list_products
from services.orders_service import list_business_orders
from services.points import get_balance, get_level_progress, list_transactions
from services.rewards_service import list_customer_coupons, list_rewards

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

    user = get_current_user()
    if user:
        if user.role == UserRole.SELLER.value:
            return redirect(url_for("auth.seller_dashboard"))
        if user.role == UserRole.ADMIN.value and not request.args.get("preview"):
            return redirect(url_for("auth.admin_dashboard"))

    q = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "").strip()
    business_id = (request.args.get("business_id") or "").strip() or None
    floor = (request.args.get("floor") or "").strip() or None

    all_businesses = list_businesses(limit=50)
    floors = sorted({b.floor for b in all_businesses if b.floor})
    if floor:
        businesses = [b for b in all_businesses if b.floor == floor]
    else:
        businesses = all_businesses

    selected_business = next((b for b in all_businesses if str(b.id) == business_id), None) if business_id else None

    products = list_products(
        category=category or None,
        floor=floor if not business_id else None,
        search=q or None,
        business_id=business_id,
        limit=50,
    )
    categories = list_categories()
    user_balance = get_balance(user) if user and user.role == UserRole.USER.value else 0
    level_progress = get_level_progress(user_balance) if user and user.role == UserRole.USER.value else {}
    transactions = list_transactions(user, limit=10) if user and user.role == UserRole.USER.value else []
    rewards = list_rewards(limit=6)
    user_coupons = list_customer_coupons(user, limit=10) if user and user.role == UserRole.USER.value else []

    return render_template(
        "client/home.html",
        user=user,
        user_balance=user_balance,
        level_progress=level_progress,
        transactions=transactions,
        products=products,
        categories=categories,
        businesses=businesses,
        all_businesses=all_businesses,
        floors=floors,
        selected_business=selected_business,
        rewards=rewards,
        user_coupons=user_coupons,
        current_category=category,
        current_floor=floor,
        current_query=q,
        current_business_id=business_id,
    )


@auth_bp.route("/seller/dashboard", endpoint="seller_dashboard")
def seller_dashboard():
    user = get_current_user()
    if not user:
        return redirect(url_for("auth.login", next=request.path))
    if user.role not in (UserRole.SELLER.value, UserRole.ADMIN.value):
        return redirect(url_for("auth.home"))

    business = get_business_for_owner(user)
    if not business:
        business = Business.query.first()

    products = list_products(business_id=business.id, include_inactive=True, limit=100) if business else []
    orders = list_business_orders(business.id, limit=50) if business else []
    categories = list_categories()

    analytics = get_business_sales_analytics(business, period="month") if business else {}

    total_revenue = analytics.get("total_sales_bs", 0.0)
    active_products_count = sum(1 for p in products if p.active)
    pending_orders_count = analytics.get("pending_orders_count", 0)

    return render_template(
        "seller/dashboard.html",
        user=user,
        business=business,
        products=products,
        orders=orders,
        categories=categories,
        analytics=analytics,
        total_revenue=total_revenue,
        active_products_count=active_products_count,
        pending_orders_count=pending_orders_count,
    )


@auth_bp.route("/admin/dashboard", endpoint="admin_dashboard")
def admin_dashboard():
    user = get_current_user()
    if not user:
        return redirect(url_for("auth.login", next=request.path))
    if user.role != UserRole.ADMIN.value:
        return redirect(url_for("auth.home"))

    businesses = Business.query.order_by(Business.created_at.asc()).all()
    users = User.query.order_by(User.points_balance.desc(), User.created_at.asc()).all()
    orders = Order.query.order_by(Order.created_at.desc()).limit(20).all()

    stats = {
        "total_users": User.query.count(),
        "active_businesses": sum(1 for b in businesses if b.active),
        "total_products": Product.query.count(),
        "total_orders": Order.query.count(),
        "points_circulating": sum(u.points_balance for u in users),
    }

    return render_template(
        "admin/dashboard.html",
        user=user,
        businesses=businesses,
        users=users,
        orders=orders,
        stats=stats,
    )


@auth_bp.route("/login", methods=["GET", "POST"], endpoint="login")
def login():
    """Render the login page or handle email/demo sign-in."""
    user = get_current_user()
    next_url = request.args.get("next") or (landing_url_for(user) if user else url_for("auth.home"))
    if user is not None and request.method == "GET":
        return redirect(next_url)

    error_message = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not email or "@" not in email:
            error_message = "Por favor ingresa un correo electrónico válido."
        else:
            user = find_user_by_email(email)
            if user is None:
                name = email.split("@")[0].replace(".", " ").replace("-", " ").title()
                user = get_or_create_google_user(
                    google_sub=f"local-{email}",
                    email=email,
                    name=name,
                )
            token = create_token(user)
            target = request.form.get("next") or request.args.get("next") or landing_url_for(user)
            response = redirect(target)
            set_session_cookie(response, token)
            return response

    error_code = request.args.get("error", "")
    if error_code and not error_message:
        error_message = LOGIN_ERRORS.get(error_code)

    demo_users = [
        {"email": "cliente@paseo.test", "name": "Cliente Demo", "role": "Cliente", "badge": "badge-neutral", "desc": "1,200 pts · Nivel Oro", "icon_text": "CL"},
        {"email": "cafe@paseo.test", "name": "Dueño Cafe", "role": "Vendedor", "badge": "badge-info", "desc": "Café Aranjuez · Cafetería & Pastelería", "icon_text": "CF"},
        {"email": "tech@paseo.test", "name": "Dueño Tech", "role": "Vendedor", "badge": "badge-info", "desc": "Tech Aranjuez · Tecnología & Audio", "icon_text": "TC"},
        {"email": "admin@paseo.test", "name": "Admin Paseo", "role": "Administrador", "badge": "badge-dark", "desc": "Gestión general del centro comercial", "icon_text": "AD"},
    ]

    return render_template(
        "auth/login.html",
        error_message=error_message,
        demo_users=demo_users,
        next_url=request.args.get("next", ""),
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
