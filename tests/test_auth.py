"""Tests for the role system, Google login and role-based authorization."""

import jwt as pyjwt
import pytest

import app as app_module
import auth
from roles import UserRole
from models.users import get_or_create_google_user, reset_store, set_user_role


@pytest.fixture(autouse=True)
def clean_store():
    reset_store()
    yield
    reset_store()


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


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
        app_module,
        "verify_google_token",
        lambda token: {"sub": sub, "email": email, "name": name, "picture": picture},
    )
    body = {"token": "fake-google-token"}
    if extra_body:
        body.update(extra_body)
    response = client.post("/auth/google", json=body)
    assert response.status_code == 200
    return response.get_json()


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


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
        app_module,
        "verify_google_token",
        lambda token: {"sub": "google-3", "email": "e@x.com", "name": "N", "picture": None, "role": "ADMIN"},
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

    monkeypatch.setattr(app_module, "verify_google_token", raise_error)
    response = client.post("/auth/google", json={"token": "bad"})
    assert response.status_code == 401


# --- JWT content --------------------------------------------------------------


def test_jwt_contains_user_id_and_role(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-7")
    payload = pyjwt.decode(data["token"], auth.JWT_SECRET_KEY, algorithms=[auth.JWT_ALGORITHM])
    assert payload["sub"] == str(data["user"]["id"])
    assert payload["role"] == "USER"
    assert "exp" in payload


# --- Authorization ------------------------------------------------------------


def test_me_returns_profile_for_authenticated_user(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-8", email="me@x.com")
    response = client.get("/me", headers=auth_header(data["token"]))
    assert response.status_code == 200
    assert response.get_json()["role"] == "USER"
    assert response.get_json()["email"] == "me@x.com"


def test_me_rejects_missing_token(client):
    assert client.get("/me").status_code == 401


def test_me_rejects_garbage_token(client):
    assert client.get("/me", headers=auth_header("not.a.token")).status_code == 401


def test_seller_route_allows_seller(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-9")
    set_user_role(data["user"]["id"], UserRole.SELLER)
    response = client.get("/seller/dashboard", headers=auth_header(data["token"]))
    assert response.status_code == 200


def test_seller_route_forbids_user(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-10")
    response = client.get("/seller/dashboard", headers=auth_header(data["token"]))
    assert response.status_code == 403


def test_admin_route_allows_admin(client, monkeypatch):
    data = fake_google_login(client, monkeypatch, sub="google-11")
    set_user_role(data["user"]["id"], UserRole.ADMIN)
    response = client.get("/admin/dashboard", headers=auth_header(data["token"]))
    assert response.status_code == 200


def test_admin_route_forbids_user_and_seller(client, monkeypatch):
    user = fake_google_login(client, monkeypatch, sub="google-12")
    seller = fake_google_login(client, monkeypatch, sub="google-13")
    set_user_role(seller["user"]["id"], UserRole.SELLER)
    assert client.get("/admin/dashboard", headers=auth_header(user["token"])).status_code == 403
    assert client.get("/admin/dashboard", headers=auth_header(seller["token"])).status_code == 403


def test_protected_routes_reject_unauthenticated(client):
    assert client.get("/seller/dashboard").status_code == 401
    assert client.get("/admin/dashboard").status_code == 401


def test_role_change_applies_to_existing_token(client, monkeypatch):
    # Role is read from the database on each request, so a promotion to
    # SELLER works immediately even with a token issued before the change.
    data = fake_google_login(client, monkeypatch, sub="google-14")
    assert client.get("/seller/dashboard", headers=auth_header(data["token"])).status_code == 403
    set_user_role(data["user"]["id"], UserRole.SELLER)
    assert client.get("/seller/dashboard", headers=auth_header(data["token"])).status_code == 200


def test_forged_role_claim_does_not_grant_access(client, monkeypatch):
    # An attacker re-signing a token is impossible without the secret; and
    # even decoding/encoding tricks fail because the DB role is authoritative.
    data = fake_google_login(client, monkeypatch, sub="google-15")
    forged = pyjwt.encode(
        {"sub": str(data["user"]["id"]), "role": "ADMIN"}, "wrong-secret", algorithm="HS256"
    )
    assert client.get("/admin/dashboard", headers=auth_header(forged)).status_code == 401


# --- Store invariants ----------------------------------------------------------


def test_set_user_role_rejects_invalid_role():
    user = get_or_create_google_user("google-16", "e@x.com", "N")
    with pytest.raises(ValueError):
        set_user_role(user.id, "SUPERUSER")


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
    # Google omits "picture" for accounts without a profile photo.
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
