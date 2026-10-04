"""Tests for the role system, Google login and role-based authorization.

The invariants these pin down are the ones that keep the authorization model
honest:

  - a new account is always USER, no matter what Google or the frontend says;
  - an existing account keeps its role across logins;
  - the role is read from the DATABASE on every request, so a promotion applies
    to tokens that were already issued, and a forged `role` claim grants
    nothing.

Adapted from the original in-memory-store version: the store is now PostgreSQL
in production and SQLite in these tests, so `reset_store()` is gone and the
schema is created fresh per test by the `app` fixture in conftest.py.
"""

from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import jwt as pyjwt
import pytest

import api.auth_routes as auth_routes
import config
from extensions import db
from models import Business, get_or_create_google_user, set_user_role
from roles import UserRole


def fake_google_login(
    client,
    monkeypatch,
    sub,
    email="user@example.com",
    name="Test User",
    picture="https://photos.example.com/pic.jpg",
    extra_body=None,
):
    """Log in via /auth/google with a stubbed Google token verification."""
    monkeypatch.setattr(
        auth_routes,
        "verify_google_token",
        lambda token: {"sub": sub, "email": email, "name": name, "picture": picture},
    )
    body = {"token": "fake-google-token"}
    if extra_body:
        body.update(extra_body)
    response = client.post("/auth/google", json=body)
    assert response.status_code == 200
    return response.get_json()


def fake_google_callback(
    client,
    monkeypatch,
    sub,
    email="user@example.com",
    name="Test User",
    picture=None,
):
    """Drive the browser OAuth flow: /auth/google/login then callback."""
    monkeypatch.setattr(
        auth_routes,
        "exchange_code_for_identity",
        lambda code: {"sub": sub, "email": email, "name": name, "picture": picture},
    )
    client.get("/auth/google/login")
    with client.session_transaction() as sess:
        state = sess["google_oauth_state"]
    return client.get(f"/auth/google/callback?code=fake-code&state={state}")


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def seller_with_business(app, make_user):
    """A SELLER whose business is active.

    Seller endpoints require a usable shop, not just the role: an inactive
    business must not be able to accrue points liability or take orders. So the
    role alone is not enough to get past require_business().
    """

    def _make(email="comercio@paseo.test", name="Comercio Test", active=True):
        user = make_user(email=email, name=name, role=UserRole.SELLER)
        business = Business(
            user_id=user.id,
            name=f"Negocio de {name}",
            category="Comida",
            points_per_bs=Decimal("1.00"),
            active=active,
        )
        db.session.add(business)
        db.session.commit()
        return user, business

    return _make


# --- Registration rules ------------------------------------------------------


def test_new_google_user_gets_user_role(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-1")
    assert data["user"]["role"] == "USER"


def test_frontend_cannot_choose_role(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-2", extra_body={"role": "ADMIN"})
    assert data["user"]["role"] == "USER"


def test_google_never_determines_role(client, monkeypatch):
    # Even if Google's identity data somehow contained a role, it must be ignored.
    monkeypatch.setattr(
        auth_routes,
        "verify_google_token",
        lambda token: {"sub": "google-3", "email": "e@x.com", "name": "N", "role": "ADMIN"},
    )
    response = client.post("/auth/google", json={"token": "fake"})
    assert response.status_code == 200
    assert response.get_json()["user"]["role"] == "USER"


def test_second_login_returns_same_user_and_keeps_user_role(client, monkeypatch):
    first = fake_google_login(client, monkeypatch, sub="google-4")
    second = fake_google_login(client, monkeypatch, sub="google-4")
    assert second["user"]["id"] == first["user"]["id"]
    assert second["user"]["role"] == "USER"  # still USER, never auto-upgraded


def test_existing_admin_is_never_reset_to_user(client, monkeypatch):
    first = fake_google_login(client, monkeypatch, sub="google-5")
    set_user_role(first["user"]["id"], UserRole.ADMIN)  # manual admin process
    second = fake_google_login(client, monkeypatch, sub="google-5")
    assert second["user"]["role"] == "ADMIN"


def test_existing_seller_keeps_seller_role(client, monkeypatch):
    first = fake_google_login(client, monkeypatch, sub="google-6")
    set_user_role(first["user"]["id"], UserRole.SELLER)  # authorized flow
    second = fake_google_login(client, monkeypatch, sub="google-6")
    assert second["user"]["role"] == "SELLER"


def test_login_requires_google_token(client):
    response = client.post("/auth/google", json={})
    assert response.status_code == 400


def test_invalid_google_token_is_rejected(client, monkeypatch):
    def raise_error(token):
        raise ValueError("Invalid Google token")

    monkeypatch.setattr(auth_routes, "verify_google_token", raise_error)
    response = client.post("/auth/google", json={"token": "bad"})
    assert response.status_code == 401


def test_user_survives_a_restart_because_it_is_in_the_database(client, monkeypatch, app):
    # The original in-memory store lost every user when the process restarted.
    # Logging in again must now find the same row, not create a new one.
    first = fake_google_login(client, monkeypatch, sub="google-restart")
    second = fake_google_login(client, monkeypatch, sub="google-restart")
    assert second["user"]["id"] == first["user"]["id"]
    from models import find_user_by_google_sub

    assert find_user_by_google_sub("google-restart") is not None


# --- JWT content --------------------------------------------------------------


def test_jwt_contains_user_id_and_role(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-7")
    payload = pyjwt.decode(
        data["token"], config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM]
    )
    assert payload["sub"] == str(data["user"]["id"])
    assert payload["role"] == "USER"
    assert "exp" in payload


# --- Authorization ------------------------------------------------------------


def test_me_returns_profile_for_authenticated_user(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-8", email="me@x.com")
    response = client.get("/api/me", headers=auth_header(data["token"]))
    assert response.status_code == 200
    body = response.get_json()
    assert body["role"] == "USER"
    assert body["email"] == "me@x.com"
    assert body["points"] == 0


def test_me_rejects_missing_token(client):
    assert client.get("/api/me").status_code == 401


def test_me_rejects_garbage_token(client):
    assert client.get("/api/me", headers=auth_header("not.a.token")).status_code == 401


def test_seller_route_allows_seller(client, monkeypatch, seller_with_business):
    user, business = seller_with_business()
    from auth import create_token

    response = client.get("/api/business/orders", headers=auth_header(create_token(user)))
    assert response.status_code == 200


def test_seller_route_forbids_user(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-10")
    response = client.get("/api/business/orders", headers=auth_header(data["token"]))
    assert response.status_code == 403


def test_inactive_business_cannot_use_seller_routes(client, seller_with_business):
    # Self-registered shops start inactive. Until an admin approves them they
    # must not be able to credit points or take orders.
    user, _business = seller_with_business(active=False)
    from auth import create_token

    response = client.get("/api/business/orders", headers=auth_header(create_token(user)))
    assert response.status_code == 403


def test_admin_route_allows_admin(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-11")
    set_user_role(data["user"]["id"], UserRole.ADMIN)
    response = client.get("/api/admin/stats", headers=auth_header(data["token"]))
    assert response.status_code == 200


def test_admin_route_forbids_user_and_seller(client, monkeypatch):
    user = fake_google_login(client, monkeypatch, sub="google-12", email="u@x.com")
    seller = fake_google_login(client, monkeypatch, sub="google-13", email="s@x.com")
    set_user_role(seller["user"]["id"], UserRole.SELLER)
    assert client.get("/api/admin/stats", headers=auth_header(user["token"])).status_code == 403
    assert client.get("/api/admin/stats", headers=auth_header(seller["token"])).status_code == 403


def test_same_email_under_a_different_google_account_is_a_conflict(client, monkeypatch):
    # Google verified the email, so this is nearly impossible for a real user --
    # but "nearly" is not a reason to hand an account over silently. The second
    # Google identity must not be able to walk into the first one's account.
    first = fake_google_login(client, monkeypatch, sub="sub-a", email="dup@x.com")

    monkeypatch.setattr(
        auth_routes,
        "verify_google_token",
        lambda token: {"sub": "sub-b", "email": "dup@x.com", "name": "Impostor"},
    )
    second = client.post("/auth/google", json={"token": "fake"})
    assert second.status_code == 409
    assert second.get_json()["code"] == "google_account_conflict"
    # The original account is untouched.
    assert first["user"]["email"] == "dup@x.com"


def test_protected_routes_reject_unauthenticated(client):
    assert client.get("/api/business/orders").status_code == 401
    assert client.get("/api/admin/stats").status_code == 401


def test_role_change_applies_to_existing_token(client, monkeypatch, app):
    # Role is read from the database on each request, so a promotion applies to
    # tokens that were already issued.
    data = fake_google_login(client, monkeypatch, sub="google-14")
    user_id = UUID(data["user"]["id"])
    assert client.get("/api/business/orders", headers=auth_header(data["token"])).status_code == 403

    set_user_role(user_id, UserRole.SELLER)
    # Still 403 after promotion: the role is not enough, the shop must exist and
    # be active too. This is the second half of the authorization rule.
    assert client.get("/api/business/orders", headers=auth_header(data["token"])).status_code == 403

    db.session.add(
        Business(
            user_id=user_id,
            name="Shop",
            points_per_bs=Decimal("1.00"),
            active=True,
        )
    )
    db.session.commit()
    assert client.get("/api/business/orders", headers=auth_header(data["token"])).status_code == 200


def test_forged_role_claim_does_not_grant_access(client, monkeypatch):
    # An attacker re-signing a token is impossible without the secret; and
    # even decoding/encoding tricks fail because the DB role is authoritative.
    data = fake_google_login(client, monkeypatch, sub="google-15")
    # 32+ bytes so the test does not trip PyJWT's key-length warning: the point
    # is that the signature is wrong, not that the key is implausibly short.
    forged = pyjwt.encode(
        {"sub": str(data["user"]["id"]), "role": "ADMIN"},
        "a-completely-different-signing-secret-32b",
        algorithm="HS256",
    )
    assert client.get("/api/admin/stats", headers=auth_header(forged)).status_code == 401


def test_repeated_google_logins_are_rate_limited():
    # The shared `app` fixture cannot be reused here. Flask-Limiter resolves its
    # `enabled` flag inside init_app and then returns early, skipping the wiring
    # of every @limiter.limit decorator when it is off -- so flipping the config
    # afterwards would leave the decorators unwired and this test would pass for
    # the wrong reason. The flag has to be on before the app is built.
    import os

    from app import create_app
    from extensions import db as _db, limiter

    was_enabled = limiter.enabled
    limited_app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite+pysqlite:///:memory:",
            "RATELIMIT_ENABLED": True,
        }
    )
    try:
        with limited_app.app_context():
            _db.create_all()
        client = limited_app.test_client()

        statuses = []
        for _ in range(14):
            response = client.post("/auth/google", json={"token": "whatever"})
            statuses.append(response.status_code)
            if response.status_code == 429:
                break

        # 401 is the normal answer: an unverifiable Google token. The point is
        # that the burst stops being answered and starts being rejected.
        assert 429 in statuses, statuses
        assert statuses[0] == 401

        limited = client.post("/auth/google", json={"token": "whatever"})
        assert limited.status_code == 429
        # The client needs to know when it can retry, not just that it failed.
        assert limited.headers.get("Retry-After")
    finally:
        limiter.reset()
        limiter.enabled = was_enabled
        with limited_app.app_context():
            _db.session.remove()
            _db.drop_all()


# --- Store invariants ----------------------------------------------------------


def test_set_user_role_rejects_invalid_role(app):
    user = get_or_create_google_user("google-16", "e@x.com", "N")
    with pytest.raises(ValueError):
        set_user_role(user.id, "SUPERUSER")


def test_email_is_stored_lowercase(app):
    # The DB has UNIQUE on email: 'Ana@x.com' and 'ana@x.com' must not become
    # two accounts for one person.
    user = get_or_create_google_user("google-17", "  Ana@Paseo.COM ", "Ana")
    assert user.email == "ana@paseo.com"


def test_points_balance_is_derived_from_the_ledger(app, make_user):
    # Written through the service on purpose. Inserting a Transaction directly
    # would move the balance but leave `User.level` stale, because level is a
    # denormalised column refreshed by the same transaction as the movement.
    # That is the reason every write goes through services/points.py.
    from services.points import adjust_points, get_balance

    user = make_user(email="saldo@paseo.test")
    adjust_points(user, 1000, note="Carga inicial de prueba")
    db.session.refresh(user)

    assert user.points_balance == 1000
    assert get_balance(user) == 1000
    assert user.level == "GOLD"  # 1000 >= 700 (SILVER is 300, GOLD is 700)

    # A second movement stacks on the first; balances are never overwritten.
    adjust_points(user, 600, note="Segunda carga")
    db.session.refresh(user)
    assert user.points_balance == 1600
    assert user.level == "PLATINUM"  # 1600 >= 1500


# --- Google profile picture -----------------------------------------------------


def test_profile_picture_is_saved_and_returned(client, monkeypatch):
    data = fake_google_login(
        client, monkeypatch, sub="google-pic-1", picture="https://photos.example.com/me.jpg"
    )
    assert data["user"]["picture"] == "https://photos.example.com/me.jpg"


def test_profile_picture_is_refreshed_on_next_login(client, monkeypatch):
    fake_google_login(client, monkeypatch, sub="google-pic-2", picture="https://photos.example.com/old.jpg")
    data = fake_google_login(client, monkeypatch, sub="google-pic-2", picture="https://photos.example.com/new.jpg")
    assert data["user"]["picture"] == "https://photos.example.com/new.jpg"


def test_profile_picture_may_be_absent(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-pic-3", picture=None)
    assert data["user"]["picture"] is None


# --- Location --------------------------------------------------------------------


def test_new_user_has_no_location(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-loc-1")
    assert data["user"]["latitude"] is None
    assert data["user"]["longitude"] is None


def test_update_location(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-loc-2")
    response = client.put(
        "/me/location",
        json={"latitude": 4.611, "longitude": -74.081},
        headers=auth_header(data["token"]),
    )
    assert response.status_code == 200
    assert response.get_json()["user"]["latitude"] == 4.611
    assert response.get_json()["user"]["longitude"] == -74.081


def test_update_location_rejects_out_of_range(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-loc-3")
    response = client.put(
        "/me/location",
        json={"latitude": 123, "longitude": 0},
        headers=auth_header(data["token"]),
    )
    assert response.status_code == 400


def test_update_location_requires_coordinates(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-loc-4")
    response = client.put("/me/location", json={}, headers=auth_header(data["token"]))
    assert response.status_code == 400


def test_update_location_requires_auth(client):
    response = client.put("/me/location", json={"latitude": 0, "longitude": 0})
    assert response.status_code == 401


def test_me_includes_picture_and_location(client, monkeypatch):
    data = fake_google_login(
        client, monkeypatch, sub="google-me-1", picture="https://photos.example.com/p.jpg"
    )
    client.put(
        "/me/location",
        json={"latitude": 1.5, "longitude": 2.5},
        headers=auth_header(data["token"]),
    )
    profile = client.get("/me", headers=auth_header(data["token"])).get_json()
    assert profile["picture"] == "https://photos.example.com/p.jpg"
    assert profile["latitude"] == 1.5
    assert profile["longitude"] == 2.5


# --- Browser redirect flow (/auth/google/login + /auth/google/callback) ----------


def test_google_callback_creates_user_and_sets_session_cookie(client, monkeypatch):
    response = fake_google_callback(client, monkeypatch, sub="google-cb-1")
    assert response.status_code == 302
    location = response.headers["Location"]
    assert "/admin/dashboard" not in location and "/seller/dashboard" not in location
    assert "paseo_session" in response.headers.get("Set-Cookie", "")


def test_google_callback_existing_admin_goes_to_admin_dashboard(client, monkeypatch):
    user = get_or_create_google_user("google-cb-2", "admin@x.com", "Admin")
    set_user_role(user.id, UserRole.ADMIN)
    response = fake_google_callback(
        client, monkeypatch, sub="google-cb-2", email="admin@x.com", name="Admin"
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/dashboard")
    assert "paseo_session" in response.headers.get("Set-Cookie", "")


def test_google_callback_existing_seller_goes_to_seller_dashboard(client, monkeypatch):
    user = get_or_create_google_user("google-cb-3", "seller@x.com", "Seller")
    set_user_role(user.id, UserRole.SELLER)
    response = fake_google_callback(
        client, monkeypatch, sub="google-cb-3", email="seller@x.com", name="Seller"
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/seller/dashboard")


def test_google_callback_rejects_bad_state(client, monkeypatch):
    monkeypatch.setattr(
        auth_routes,
        "exchange_code_for_identity",
        lambda code: {"sub": "x", "email": "x@x.com", "name": "X", "picture": None},
    )
    client.get("/auth/google/login")
    response = client.get("/auth/google/callback?code=fake-code&state=wrong-state")
    assert response.status_code == 302
    assert "error=invalid_state" in response.headers["Location"]


def test_google_callback_exchange_failure_redirects_with_google_error(client, monkeypatch):
    def fail_exchange(code):
        raise ValueError("Google token exchange failed")

    monkeypatch.setattr(auth_routes, "exchange_code_for_identity", fail_exchange)
    client.get("/auth/google/login")
    with client.session_transaction() as sess:
        state = sess["google_oauth_state"]
    response = client.get(f"/auth/google/callback?code=fake-code&state={state}")
    assert response.status_code == 302
    assert "error=google" in response.headers["Location"]


# The callback tests above all start with client.get("/auth/google/login") and
# throw the response away, reading the state out of the session instead. That
# hides a broken login route: the state is written before the URL is built, so
# the callback half of the tests passes even when the redirect half raises.
#
# That is not hypothetical. google_auth.build_google_auth_url() called
# urlencode() without importing it, so /auth/google/login returned 500 for
# every real user while all eleven of these tests stayed green. These two
# assert on the redirect itself, which is the part a browser actually hits.

def test_google_login_redirects_to_google(client):
    response = client.get("/auth/google/login")
    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")


def test_google_login_url_carries_every_parameter_google_requires(client, monkeypatch):
    monkeypatch.setattr(
        config, "GOOGLE_CLIENT_ID", "123.apps.googleusercontent.com", raising=False
    )
    monkeypatch.setattr(
        config,
        "GOOGLE_REDIRECT_URI",
        "http://localhost:5000/auth/google/callback",
        raising=False,
    )
    location = client.get("/auth/google/login").headers["Location"]

    # Google rejects the request outright if any of these is missing or wrong,
    # and it does so on a screen the user sees, not in a log.
    query = parse_qs(urlparse(location).query)
    assert query["client_id"] == ["123.apps.googleusercontent.com"]
    assert query["redirect_uri"] == ["http://localhost:5000/auth/google/callback"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email profile"]
    # prompt=select_account is what lets a user switch accounts without
    # signing out of Google first.
    assert query["prompt"] == ["select_account"]

    # The state is the CSRF token; the callback compares it against the session.
    assert len(query["state"][0]) >= 32

