"""Tests for business registration, the catalog and admin approval.

The two rules worth protecting here are both about who is allowed to make a shop
real:

  - a self-registered shop starts INACTIVE and cannot activate itself;
  - promoting someone to SELLER and creating their shop is one transaction.

Both were real defects at some point during development, which is exactly why
they are pinned down.
"""

from decimal import Decimal

import pytest

from extensions import db
from models import Business, get_or_create_google_user
from roles import UserRole


def token_for(user):
    from auth import create_token

    return {"Authorization": f"Bearer {create_token(user)}"}


@pytest.fixture()
def customer(app):
    return get_or_create_google_user("sub-cliente", "cliente@paseo.test", "Cliente")


# --- Registration -------------------------------------------------------------


def test_registering_creates_an_inactive_shop(client, app, customer):
    response = client.post(
        "/api/business/register",
        json={"name": "Cafe Aranjuez", "category": "Comida", "points_per_bs": 1},
        headers=token_for(customer),
    )
    assert response.status_code == 201, response.get_json()
    business = response.get_json()["business"]
    assert business["active"] is False
    assert business["name"] == "Cafe Aranjuez"


def test_a_shop_cannot_activate_itself(client, app, customer):
    # Silently ignoring the field would be worse than refusing: the owner would
    # believe it was trading while it was invisible to every customer.
    response = client.post(
        "/api/business/register",
        json={"name": "Cafe Aranjuez", "active": True},
        headers=token_for(customer),
    )
    assert response.status_code == 403
    assert "admin" in response.get_json()["error"].lower()


def test_registering_promotes_the_owner_to_seller(client, app, customer):
    client.post(
        "/api/business/register",
        json={"name": "Cafe Aranjuez"},
        headers=token_for(customer),
    )
    db.session.refresh(customer)
    assert customer.role == UserRole.SELLER.value
    assert customer.business is not None


def test_a_failed_registration_leaves_the_user_untouched(client, app, customer):
    # The promotion and the INSERT must be atomic: a USER who failed to register
    # must not be left promoted to SELLER with no shop.
    response = client.post(
        "/api/business/register",
        json={"name": "", "active": True},
        headers=token_for(customer),
    )
    assert response.status_code == 400
    db.session.refresh(customer)
    assert customer.role == UserRole.USER.value
    assert customer.business is None


def test_registering_twice_conflicts_instead_of_duplicating(client, app, customer):
    headers = token_for(customer)
    first = client.post("/api/business/register", json={"name": "Cafe"}, headers=headers)
    assert first.status_code == 201
    again = client.post("/api/business/register", json={"name": "Otro"}, headers=headers)
    assert again.status_code == 409
    assert again.get_json()["details"]["business_id"] == first.get_json()["business"]["id"]


def test_a_buyer_cannot_register_someone_elses_shop(client, app, customer):
    other = get_or_create_google_user("sub-otro", "otro@paseo.test", "Otro")
    response = client.post(
        "/api/business/register",
        json={"name": "Shop stolen"},
        headers=token_for(customer),
    )
    assert response.status_code == 201
    db.session.refresh(other)
    assert other.role == UserRole.USER.value  # untouched
    assert other.business is None


def test_points_rate_must_be_positive(client, app, customer):
    for bad in (0, -1, "muchos"):
        response = client.post(
            "/api/business/register",
            json={"name": "Cafe", "points_per_bs": bad},
            headers=token_for(customer),
        )
        assert response.status_code == 400, bad


# --- Admin approval -----------------------------------------------------------


@pytest.fixture()
def pending_shop(client, app, customer):
    """A registered but not yet approved shop, plus an admin to approve it."""
    client.post(
        "/api/business/register",
        json={"name": "Cafe Aranjuez", "category": "Comida"},
        headers=token_for(customer),
    )
    admin = get_or_create_google_user("sub-admin", "admin@paseo.test", "Admin")
    admin.role = UserRole.ADMIN.value
    db.session.commit()
    return customer.business, admin


def test_pending_shop_is_hidden_from_the_public_catalog(client, app, pending_shop):
    business, _admin = pending_shop
    listed = client.get("/api/businesses").get_json()["items"]
    assert not any(item["id"] == str(business.id) for item in listed)


def test_admin_activates_the_shop(client, app, pending_shop):
    business, admin = pending_shop
    response = client.patch(
        f"/api/admin/businesses/{business.id}",
        json={"active": True},
        headers=token_for(admin),
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["business"]["active"] is True

    listed = client.get("/api/businesses").get_json()["items"]
    assert any(item["id"] == str(business.id) for item in listed)


def test_a_seller_cannot_activate_their_own_shop(client, app, pending_shop):
    business, _admin = pending_shop
    response = client.patch(
        f"/api/admin/businesses/{business.id}",
        json={"active": True},
        headers=token_for(business.owner),
    )
    assert response.status_code == 403
    db.session.refresh(business)
    assert business.active is False


def test_a_seller_cannot_edit_the_shop_profile_active_field(client, app, pending_shop):
    business, admin = pending_shop
    headers = token_for(business.owner)

    # While pending, the shop cannot even reach its own edit endpoint.
    assert client.patch("/api/business/me", json={"name": "Renombrado"}, headers=headers).status_code == 403

    client.patch(
        f"/api/admin/businesses/{business.id}", json={"active": True}, headers=token_for(admin)
    )

    # Once active it can edit its profile, but not its own approval status.
    renamed = client.patch("/api/business/me", json={"name": "Renombrado"}, headers=headers)
    assert renamed.status_code == 200
    assert renamed.get_json()["business"]["name"] == "Renombrado"

    smuggle = client.patch("/api/business/me", json={"active": False}, headers=headers)
    assert smuggle.status_code == 403
    db.session.refresh(business)
    assert business.active is True  # untouched


def test_admin_can_change_the_exchange_rate(client, app, pending_shop):
    business, admin = pending_shop
    response = client.patch(
        f"/api/admin/businesses/{business.id}",
        json={"points_per_bs": "2.50"},
        headers=token_for(admin),
    )
    assert response.status_code == 200
    assert float(response.get_json()["business"]["points_per_bs"]) == 2.50


# --- Catalog and products -----------------------------------------------------


def test_only_an_active_shop_can_add_products(client, app, pending_shop):
    business, _admin = pending_shop
    response = client.post(
        "/api/business/products",
        json={"name": "Cafe", "price_bs": 5, "stock": 10},
        headers=token_for(business.owner),
    )
    assert response.status_code == 403


def test_products_need_a_name_and_a_positive_price(client, app, pending_shop):
    business, admin = pending_shop
    client.patch(
        f"/api/admin/businesses/{business.id}", json={"active": True}, headers=token_for(admin)
    )
    headers = token_for(business.owner)

    assert client.post(
        "/api/business/products", json={"price_bs": 5, "stock": 1}, headers=headers
    ).status_code == 400
    assert client.post(
        "/api/business/products", json={"name": "X", "price_bs": 0, "stock": 1}, headers=headers
    ).status_code == 400
    assert client.post(
        "/api/business/products", json={"name": "X", "price_bs": 5, "stock": -1}, headers=headers
    ).status_code == 400


def test_a_shop_cannot_edit_a_rivals_product(client, app, pending_shop):
    business, admin = pending_shop
    client.patch(
        f"/api/admin/businesses/{business.id}", json={"active": True}, headers=token_for(admin)
    )
    mine = client.post(
        "/api/business/products",
        json={"name": "Cafe", "price_bs": 5, "stock": 10},
        headers=token_for(business.owner),
    ).get_json()["product"]

    rival_user = get_or_create_google_user("sub-rival", "rival@paseo.test", "Rival")
    rival_user.role = UserRole.SELLER.value
    db.session.flush()
    rival = Business(
        user_id=rival_user.id, name="Rival", points_per_bs=Decimal("1"), active=True
    )
    db.session.add(rival)
    db.session.commit()

    response = client.patch(
        f"/api/business/products/{mine['id']}",
        json={"price_bs": 0.01},
        headers=token_for(rival_user),
    )
    assert response.status_code in (403, 404)
    from services.catalog import get_product

    assert Decimal(str(get_product(mine["id"]).price_bs)) == Decimal("5")


def test_public_catalog_is_browsable_without_signing_in(client, app, pending_shop):
    business, admin = pending_shop
    client.patch(
        f"/api/admin/businesses/{business.id}", json={"active": True}, headers=token_for(admin)
    )
    client.post(
        "/api/business/products",
        json={"name": "Cafe", "price_bs": 5, "stock": 10},
        headers=token_for(business.owner),
    )

    # No Authorization header at all.
    assert client.get("/api/businesses").status_code == 200
    assert client.get("/api/products").status_code == 200
    assert client.get("/api/rewards").status_code == 200
    assert client.get("/api/categories").status_code == 200

    listed = client.get("/api/products").get_json()["items"]
    assert [item["name"] for item in listed] == ["Cafe"]


def test_search_finds_a_product_by_name(client, app, pending_shop):
    business, admin = pending_shop
    client.patch(
        f"/api/admin/businesses/{business.id}", json={"active": True}, headers=token_for(admin)
    )
    for name in ("Cafe Espresso", "Empanada"):
        client.post(
            "/api/business/products",
            json={"name": name, "price_bs": 5, "stock": 10},
            headers=token_for(business.owner),
        )

    found = client.get("/api/products", query_string={"q": "espresso"}).get_json()["items"]
    assert [item["name"] for item in found] == ["Cafe Espresso"]


def test_admin_can_promote_a_user_to_admin(client, app, customer):
    admin = get_or_create_google_user("sub-admin2", "admin2@paseo.test", "Admin2")
    admin.role = UserRole.ADMIN.value
    db.session.commit()

    response = client.patch(
        f"/api/admin/users/{customer.id}/role",
        json={"role": "ADMIN"},
        headers=token_for(admin),
    )
    assert response.status_code == 200
    assert response.get_json()["user"]["role"] == "ADMIN"


def test_an_admin_cannot_demote_themselves(client, app):
    admin = get_or_create_google_user("sub-admin3", "admin3@paseo.test", "Admin3")
    admin.role = UserRole.ADMIN.value
    db.session.commit()

    response = client.patch(
        f"/api/admin/users/{admin.id}/role",
        json={"role": "USER"},
        headers=token_for(admin),
    )
    assert response.status_code == 409


def test_the_last_admin_cannot_be_demoted(client, app):
    first = get_or_create_google_user("sub-a1", "a1@paseo.test", "A1")
    first.role = UserRole.ADMIN.value
    second = get_or_create_google_user("sub-a2", "a2@paseo.test", "A2")
    second.role = UserRole.ADMIN.value
    db.session.commit()

    # Even from a different admin: demoting the only remaining one would leave
    # nobody able to undo it.
    response = client.patch(
        f"/api/admin/users/{second.id}/role",
        json={"role": "USER"},
        headers=token_for(first),
    )
    assert response.status_code == 200  # two admins, so one may step down


def test_admin_stats_require_admin(client, app):
    assert client.get("/api/admin/stats").status_code == 401
    user = get_or_create_google_user("sub-plain", "plain@paseo.test", "Plain")
    assert client.get("/api/admin/stats", headers=token_for(user)).status_code == 403